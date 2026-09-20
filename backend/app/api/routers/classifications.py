"""Classification and human review (API spec §2).

The endpoint shapes here follow from spec §1: the engine proposes, a person
decides. ``POST /classify`` never produces a finished answer — it produces
proposals, a review queue and the questions the rules are waiting on — and
``PATCH`` on a classification is the only way a final category is settled by a
human.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ai.factory import build_advisors
from app.api.deps import (
    CurrentUser,
    SessionDep,
    SettingsDep,
    ensure_not_finalized,
    parse_uuid,
)
from app.api.errors import NotFoundError, UnprocessableStateError
from app.api.schemas.classification import (
    ClassificationDetailResponse,
    ClassificationResponse,
    ClassificationsResponse,
    ClassificationSummary,
    ClassifyRequest,
    ClassifyResponse,
    EvidenceResponse,
    QuestionResponse,
    ReviewRequest,
    ReviewResponse,
    RuleResponse,
)
from app.data.rule_catalog import get_engine, rule_set_version
from app.domain.enums import ClassificationMethod, Ifrs18Category, ReviewAction
from app.models import Ifrs18Classification, Project, ReviewQuestion
from app.repositories.classifications import ClassificationRepository
from app.repositories.projects import ProjectRepository
from app.repositories.review import QuestionRepository, ReviewRepository
from app.repositories.statements import LineRepository
from app.services.classification import (
    ClassificationError,
    classify_project,
    recompute_impact,
)
from app.services.review import ReviewDecision, ReviewError, review_classification

router = APIRouter(tags=["classification"])


async def _project(session: SessionDep, project_id: str, user: CurrentUser) -> Project:
    project = await ProjectRepository(session).by_id(parse_uuid(project_id, "Project"), user.id)
    if project is None:
        raise NotFoundError("Project")
    return project


def _rule_response(rule_id: str | None) -> RuleResponse | None:
    rule = get_engine().rule(rule_id) if rule_id else None
    if rule is None:
        return None
    return RuleResponse(
        rule_id=rule.rule_id,
        priority=rule.priority,
        description=rule.description,
        source_type=rule.source.type,
        source_reference=rule.source.reference,
        verification_status=rule.source.verification_status,
        source_url=rule.source.url,
        source_note=rule.source.note,
    )


def classification_response(row: Ifrs18Classification) -> ClassificationResponse:
    rule = get_engine().rule(row.rule_id) if row.rule_id else None
    return ClassificationResponse(
        id=row.id,
        line_id=row.line_id,
        original_account=row.original_account,
        normalized_account_code=row.normalized_account_code,
        amount=row.amount,
        current_category=row.current_category,
        proposed_ifrs18_category=row.proposed_ifrs18_category,
        proposed_ifrs18_subcategory=row.proposed_ifrs18_subcategory,
        final_ifrs18_category=row.final_ifrs18_category,
        final_ifrs18_subcategory=row.final_ifrs18_subcategory,
        classification_method=row.classification_method,
        rule_id=row.rule_id,
        rule_source_reference=rule.source.reference if rule else None,
        rule_verification_status=rule.source.verification_status if rule else None,
        ai_model=row.ai_model,
        ai_confidence=row.ai_confidence,
        ai_reasoning=row.ai_reasoning,
        confidence_band=row.confidence_band,
        requires_human_review=row.requires_human_review,
        blocked_on_question_id=row.blocked_on_question_id,
        user_override=row.user_override,
        override_reason=row.override_reason,
        reviewed_at=row.reviewed_at,
        reviewer_user_id=row.reviewer_user_id,
        impact_on_operating_profit=row.impact_on_operating_profit,
        evidence=[EvidenceResponse.model_validate(item) for item in row.evidence],
    )


def summarize(
    rows: list[Ifrs18Classification], questions: list[ReviewQuestion]
) -> ClassificationSummary:
    """The counts the review screen is driven by.

    ``unreviewed`` is the one that matters: a classification flagged for review
    and not yet looked at is what stands between a project and finalization.
    """
    needing = [row for row in rows if row.requires_human_review]
    return ClassificationSummary(
        total=len(rows),
        by_method=dict(Counter(str(row.classification_method) for row in rows)),
        by_category=dict(
            Counter(
                str(row.final_ifrs18_category or Ifrs18Category.UNCLASSIFIED.value) for row in rows
            )
        ),
        requires_review=len(needing),
        unreviewed=sum(1 for row in needing if row.reviewed_at is None),
        open_questions=sum(
            1 for question in questions if question.blocks_finalization and not question.is_resolved
        ),
    )


def question_response(
    question: ReviewQuestion, *, blocked: list[Ifrs18Classification] | None = None
) -> QuestionResponse:
    """One question, with what turns on it.

    ``affected_amount`` is the absolute size of the lines waiting on this
    answer: the user should know the stakes before deciding, not after.
    """
    from app.data.question_catalog import get_question

    spec = get_question(question.question_key)
    waiting = blocked or []
    return QuestionResponse(
        id=question.id,
        scope=question.scope,
        line_id=question.line_id,
        question_key=question.question_key,
        question_text_ko=question.question_text_ko,
        question_text_en=question.question_text_en,
        help_ko=spec.help_ko if spec else None,
        help_en=spec.help_en if spec else None,
        raised_by_rule_id=question.raised_by_rule_id,
        options=list(spec.answers) if spec else [],
        allows_undue_cost_or_effort=spec.allows_undue_cost_or_effort if spec else False,
        answer=question.answer,
        resolved_category=question.resolved_category,
        undue_cost_or_effort=question.undue_cost_or_effort,
        note=question.note,
        answered_at=question.answered_at,
        is_resolved=question.is_resolved,
        blocks_finalization=question.blocks_finalization,
        affected_line_count=len(waiting),
        affected_amount=sum((abs(row.amount) for row in waiting), start=Decimal(0))
        if waiting
        else None,
    )


async def questions_with_stakes(session: AsyncSession, project: Project) -> list[QuestionResponse]:
    questions = await QuestionRepository(session).for_project(project.id)
    rows = await ClassificationRepository(session).for_project(project.id)
    blocked: dict[object, list[Ifrs18Classification]] = {}
    for row in rows:
        if row.blocked_on_question_id is not None:
            blocked.setdefault(row.blocked_on_question_id, []).append(row)
    return [
        question_response(question, blocked=blocked.get(question.id, [])) for question in questions
    ]


# ---------------------------------------------------------------------------
# Running the engine
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/classify", response_model=ClassifyResponse)
async def classify(
    project_id: str,
    payload: ClassifyRequest,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
) -> ClassifyResponse:
    """Classify every extracted line.

    Synchronous, like extraction: the rule set is a few hundred comparisons per
    line, and a job handle would cost the caller a round trip to learn what the
    response already contains.
    """
    project = await _project(session, project_id, user)
    ensure_not_finalized(project)
    advisors = build_advisors(settings)
    try:
        run = await classify_project(
            session,
            project=project,
            use_ai_assistant=payload.use_ai_assistant,
            preserve_user_overrides=payload.preserve_user_overrides,
            actor_id=user.id,
            advisors=advisors,
        )
    except ClassificationError as exc:
        raise UnprocessableStateError(
            title="Nothing to classify", code=exc.code, detail=exc.reason
        ) from exc

    return ClassifyResponse(
        summary=summarize(list(run.classifications), list(run.questions)),
        questions=await questions_with_stakes(session, project),
        rule_set_version=rule_set_version(),
        preserved_overrides=run.preserved_overrides,
        discarded=run.discarded,
        ai_assistant_available=advisors.available,
    )


@router.get("/projects/{project_id}/classifications", response_model=ClassificationsResponse)
async def list_classifications(
    project_id: str,
    session: SessionDep,
    user: CurrentUser,
    requires_review: Annotated[bool | None, Query()] = None,
    category: Annotated[Ifrs18Category | None, Query()] = None,
    method: Annotated[ClassificationMethod | None, Query()] = None,
) -> ClassificationsResponse:
    """Every classification, largest absolute effect on operating profit first."""
    project = await _project(session, project_id, user)
    rows = await ClassificationRepository(session).for_project(
        project.id,
        requires_review=requires_review,
        category=category.value if category else None,
        method=method.value if method else None,
    )
    # Summarized over the whole project rather than the filtered page: a
    # summary that changed with the filter would tell a reviewer they have
    # fewer items left than they do.
    everything = (
        rows
        if requires_review is None and category is None and method is None
        else await ClassificationRepository(session).for_project(project.id)
    )
    return ClassificationsResponse(
        items=[classification_response(row) for row in rows],
        summary=summarize(everything, await QuestionRepository(session).for_project(project.id)),
    )


@router.get(
    "/projects/{project_id}/classifications/{classification_id}",
    response_model=ClassificationDetailResponse,
)
async def get_classification(
    project_id: str,
    classification_id: str,
    session: SessionDep,
    user: CurrentUser,
) -> ClassificationDetailResponse:
    """The drill-down of spec §7: the rule, the reasoning, the source cell."""
    project = await _project(session, project_id, user)
    row = await _classification(session, classification_id, project)

    line = await LineRepository(session).by_id(row.line_id, project.id)
    question = (
        await QuestionRepository(session).by_id(row.blocked_on_question_id, project.id)
        if row.blocked_on_question_id
        else None
    )
    reviews = await ReviewRepository(session).for_classification(row.id)

    return ClassificationDetailResponse(
        **classification_response(row).model_dump(),
        raw_label=line.raw_label if line else row.original_account,
        raw_value=line.raw_value if line else "",
        source_locator=dict(line.source_locator or {}) if line else {},
        rule=_rule_response(row.rule_id),
        question=question_response(question) if question else None,
        reviews=[ReviewResponse.model_validate(item) for item in reviews],
    )


@router.patch(
    "/projects/{project_id}/classifications/{classification_id}",
    response_model=ClassificationResponse,
)
async def review(
    project_id: str,
    classification_id: str,
    payload: ReviewRequest,
    session: SessionDep,
    user: CurrentUser,
) -> ClassificationResponse:
    """Accept, override or defer one classification (spec §1 layer 3)."""
    project = await _project(session, project_id, user)
    ensure_not_finalized(project)
    row = await _classification(session, classification_id, project)

    try:
        await review_classification(
            session,
            project=project,
            classification=row,
            decision=ReviewDecision(
                action=ReviewAction(payload.action),
                final_category=payload.final_ifrs18_category,
                final_subcategory=payload.final_ifrs18_subcategory,
                reason=payload.override_reason,
            ),
            actor=user,
        )
    except ReviewError as exc:
        raise UnprocessableStateError(
            title="Review rejected", code=exc.code, detail=exc.reason
        ) from exc

    await recompute_impact(session, project=project, classification=row)
    return classification_response(row)


async def _classification(
    session: AsyncSession, classification_id: str, project: Project
) -> Ifrs18Classification:
    row = await ClassificationRepository(session).by_id(
        parse_uuid(classification_id, "Classification"), project.id
    )
    if row is None:
        raise NotFoundError("Classification")
    return row
