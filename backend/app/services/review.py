"""Human decisions: answering a question, and reviewing a classification.

This is the third layer of spec §1, and the only one allowed to settle
anything. Two invariants run through everything here:

**A human decision is always attributable.** Every answer and every override
records who made it and when, in the row itself and again in the audit trail
(spec §8). The database refuses an override that does not name its reviewer.

**Only a person confirms a main business activity** (spec §10). An AI
suggestion may create the row; it can never mark it confirmed, and until a
person does, the fact reads as *unknown* to the rule engine — which is not the
same as "no".
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.question_catalog import QuestionSpec, get_question
from app.domain.enums import (
    SUBCATEGORY_TO_CATEGORY,
    ActivitySource,
    ActivityType,
    ActorType,
    AuditAction,
    ClassificationMethod,
    Ifrs18Category,
    Ifrs18Subcategory,
    QuestionAnswer,
    QuestionScope,
    ReviewAction,
)
from app.models import (
    BusinessActivity,
    Ifrs18Classification,
    Project,
    ReviewQuestion,
    User,
    UserReview,
)
from app.repositories.review import ActivityRepository, ReviewRepository
from app.services import audit


class ReviewError(Exception):
    """The decision as stated cannot be recorded."""

    def __init__(self, reason: str, *, code: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


# ---------------------------------------------------------------------------
# Answering a question
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Answer:
    """What a user said, before it is checked against the question."""

    answer: QuestionAnswer
    resolved_category: Ifrs18Category | None = None
    resolved_subcategory: Ifrs18Subcategory | None = None
    undue_cost_or_effort: bool = False
    note: str | None = None


def _validate(question: ReviewQuestion, spec: QuestionSpec, answer: Answer) -> None:
    """Check the answer is one this question actually accepts.

    Rejecting here rather than storing a meaningless answer matters: a stored
    answer unblocks a rule, and a rule that proceeds on an answer nobody could
    interpret is worse than one that keeps asking.
    """
    if answer.answer not in spec.answers:
        raise ReviewError(
            f"{question.question_key} cannot be answered {answer.answer.value}.",
            code="unanswerable",
        )

    # Converted rather than compared: a stored enum column reads back as a
    # plain string, and `is` against the enum member would quietly be False.
    if QuestionScope(question.scope) is QuestionScope.COMPANY:
        if answer.resolved_category is not None or answer.undue_cost_or_effort:
            raise ReviewError(
                "This question settles whether an activity is a main business "
                "activity, not which category a line belongs to.",
                code="wrong-answer-shape",
            )
        return

    if answer.answer is QuestionAnswer.NOT_SURE:
        return
    if answer.undue_cost_or_effort:
        if not spec.allows_undue_cost_or_effort:
            raise ReviewError(
                "This question has no undue cost or effort relief.",
                code="wrong-answer-shape",
            )
        return
    if answer.resolved_category is None:
        raise ReviewError(
            "Say which category the line belongs to, or that identifying it "
            "would involve undue cost or effort.",
            code="category-required",
        )
    if (
        answer.resolved_subcategory is not None
        and SUBCATEGORY_TO_CATEGORY[answer.resolved_subcategory] is not answer.resolved_category
    ):
        raise ReviewError(
            f"{answer.resolved_subcategory.value} does not belong to "
            f"{answer.resolved_category.value}.",
            code="subcategory-mismatch",
        )


async def answer_question(
    session: AsyncSession,
    *,
    project: Project,
    question: ReviewQuestion,
    answer: Answer,
    actor: User,
) -> ReviewQuestion:
    """Record an answer, and the entity fact it establishes.

    A ``NOT_SURE`` answer is stored and leaves the question blocking. That is
    deliberate: the user has been asked and could not say, which is worth
    recording, but it is not a fact the rules may act on (spec §5).
    """
    spec = get_question(question.question_key)
    if spec is None:  # pragma: no cover - the catalog is checked at load time
        raise ReviewError(
            f"{question.question_key} is not a question this version asks.",
            code="unknown-question",
        )
    _validate(question, spec, answer)

    before = {"answer": question.answer, "resolved_category": question.resolved_category}

    question.answer = answer.answer.value
    question.resolved_category = (
        answer.resolved_category.value if answer.resolved_category else None
    )
    question.undue_cost_or_effort = answer.undue_cost_or_effort
    question.note = answer.note
    question.answered_by = actor.id
    question.answered_at = _now()

    if QuestionScope(question.scope) is QuestionScope.COMPANY and spec.activity_type is not None:
        activity = await _confirm_activity(
            session,
            project=project,
            activity_type=spec.activity_type,
            answer=answer,
            actor=actor,
        )
        question.resulting_activity_id = activity.id if activity is not None else None

    await session.flush()
    await audit.record(
        session,
        action=AuditAction.QUESTION_ANSWERED,
        entity_type="review_questions",
        entity_id=question.id,
        project_id=project.id,
        actor_user_id=actor.id,
        actor_type=ActorType.USER,
        before=before,
        after={
            "answer": question.answer,
            "resolved_category": question.resolved_category,
            "undue_cost_or_effort": question.undue_cost_or_effort,
            "note": question.note,
        },
    )
    return question


async def _confirm_activity(
    session: AsyncSession,
    *,
    project: Project,
    activity_type: ActivityType,
    answer: Answer,
    actor: User,
) -> BusinessActivity | None:
    """Turn a YES/NO into the confirmed fact the rule engine reads.

    ``NOT_SURE`` deliberately writes nothing: an unknown fact is the absence of
    a confirmation, not a confirmation of absence.
    """
    if answer.answer is QuestionAnswer.NOT_SURE:
        return None

    return await set_activity(
        session,
        project=project,
        activity_type=activity_type,
        is_main_business_activity=answer.answer is QuestionAnswer.YES,
        description=answer.note,
        actor=actor,
    )


# ---------------------------------------------------------------------------
# Declaring a business activity directly
# ---------------------------------------------------------------------------


async def set_activity(
    session: AsyncSession,
    *,
    project: Project,
    activity_type: ActivityType,
    is_main_business_activity: bool | None,
    description: str | None,
    actor: User,
) -> BusinessActivity:
    """Confirm — or explicitly un-confirm — a business activity for this project.

    ``None`` is a real answer here and is stored as unknown, which the engine
    treats as blocking. It is how a user withdraws a confirmation they now
    doubt, rather than being forced to assert a "no" they do not mean.
    """
    activities = ActivityRepository(session)
    activity = await activities.find(project.company_id, project.id, activity_type.value)
    before = (
        {
            "is_main_business_activity": activity.is_main_business_activity,
            "confirmed_by_user": activity.confirmed_by_user,
        }
        if activity is not None
        else None
    )

    if activity is None:
        activity = await activities.add(
            BusinessActivity(
                company_id=project.company_id,
                project_id=project.id,
                activity_type=activity_type.value,
                source=ActivitySource.USER.value,
            )
        )

    activity.is_main_business_activity = is_main_business_activity
    activity.description = description
    # A person is setting this, so the row becomes theirs whatever created it.
    activity.source = ActivitySource.USER.value
    activity.confirmed_by_user = is_main_business_activity is not None
    activity.confirmed_by = actor.id if is_main_business_activity is not None else None
    activity.confirmed_at = _now() if is_main_business_activity is not None else None
    await session.flush()

    await audit.record(
        session,
        action=AuditAction.UPDATED if before else AuditAction.CREATED,
        entity_type="business_activities",
        entity_id=activity.id,
        project_id=project.id,
        actor_user_id=actor.id,
        actor_type=ActorType.USER,
        before=before,
        after={
            "activity_type": activity.activity_type,
            "is_main_business_activity": activity.is_main_business_activity,
            "confirmed_by_user": activity.confirmed_by_user,
        },
    )
    return activity


# ---------------------------------------------------------------------------
# Reviewing a classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    action: ReviewAction
    final_category: Ifrs18Category | None = None
    final_subcategory: Ifrs18Subcategory | None = None
    reason: str | None = None


async def review_classification(
    session: AsyncSession,
    *,
    project: Project,
    classification: Ifrs18Classification,
    decision: ReviewDecision,
    actor: User,
) -> Ifrs18Classification:
    """Accept, override or defer one classification.

    ``ACCEPTED`` is recorded as well as ``OVERRIDDEN``, because "a person looked
    at this and agreed" is itself audit-relevant (spec §8). ``DEFERRED`` is
    recorded too and deliberately does **not** clear the review: it says the
    reviewer came back later, not that the item is settled.
    """
    previous = classification.final_ifrs18_category
    if decision.action is ReviewAction.OVERRIDDEN:
        _validate_override(decision)

    if decision.action is ReviewAction.OVERRIDDEN:
        assert decision.final_category is not None  # checked above
        classification.final_ifrs18_category = decision.final_category.value
        classification.final_ifrs18_subcategory = (
            decision.final_subcategory.value if decision.final_subcategory else None
        )
        classification.classification_method = ClassificationMethod.USER.value
        classification.user_override = True
        classification.override_reason = decision.reason
        classification.rule_id = None
    elif decision.action is ReviewAction.ACCEPTED and classification.final_ifrs18_category is None:
        raise ReviewError(
            "There is no proposal to accept — this line is unresolved.",
            code="nothing-to-accept",
        )

    if decision.action is not ReviewAction.DEFERRED:
        # What clears the review queue is that a person decided, not that the
        # flag was lowered: `requires_human_review` stays as the record that
        # review was needed.
        classification.reviewed_at = _now()
        classification.reviewer_user_id = actor.id
        await _release_line_question(session, classification=classification, actor=actor)

    await session.flush()

    await ReviewRepository(session).add(
        UserReview(
            classification_id=classification.id,
            reviewer_user_id=actor.id,
            action=decision.action.value,
            previous_category=previous,
            new_category=classification.final_ifrs18_category,
            reason=decision.reason,
            reviewed_at=_now(),
        )
    )
    await audit.record(
        session,
        action=(
            AuditAction.OVERRIDDEN
            if decision.action is ReviewAction.OVERRIDDEN
            else AuditAction.ACCEPTED
        ),
        entity_type="ifrs18_classifications",
        entity_id=classification.id,
        project_id=project.id,
        actor_user_id=actor.id,
        actor_type=ActorType.USER,
        before={"final_ifrs18_category": previous},
        after={
            "action": decision.action.value,
            "final_ifrs18_category": classification.final_ifrs18_category,
            "final_ifrs18_subcategory": classification.final_ifrs18_subcategory,
            "reason": decision.reason,
        },
    )
    return classification


def _validate_override(decision: ReviewDecision) -> None:
    if decision.final_category is None:
        raise ReviewError(
            "An override has to say which category the item belongs to.",
            code="category-required",
        )
    if not (decision.reason or "").strip():
        raise ReviewError(
            "An override has to say why (spec §8).",
            code="reason-required",
        )
    if (
        decision.final_subcategory is not None
        and SUBCATEGORY_TO_CATEGORY[decision.final_subcategory] is not decision.final_category
    ):
        raise ReviewError(
            f"{decision.final_subcategory.value} does not belong to "
            f"{decision.final_category.value}.",
            code="subcategory-mismatch",
        )


async def _release_line_question(
    session: AsyncSession, *, classification: Ifrs18Classification, actor: User
) -> None:
    """Stop a per-line question blocking once its line has been decided by hand.

    Only a ``LINE`` question, and only the one this classification is waiting
    on. A main business activity is a fact about the entity: other lines still
    depend on it, so deciding one of them settles nothing.

    The question is left unanswered rather than given an answer nobody gave.
    What changes is that it no longer blocks.
    """
    question_id = classification.blocked_on_question_id
    classification.blocked_on_question_id = None
    if question_id is None:
        return

    question = await session.get(ReviewQuestion, question_id)
    if question is None or question.answer is not None:
        return
    if QuestionScope(question.scope) is not QuestionScope.LINE:
        return

    question.blocks_finalization = False
    question.note = (
        "Superseded by a manual classification of this line; the question was not answered."
    )
    await session.flush()
    await audit.record(
        session,
        action=AuditAction.UPDATED,
        entity_type="review_questions",
        entity_id=question.id,
        project_id=question.project_id,
        actor_user_id=actor.id,
        actor_type=ActorType.USER,
        after={"blocks_finalization": False, "reason": "line classified manually"},
    )
