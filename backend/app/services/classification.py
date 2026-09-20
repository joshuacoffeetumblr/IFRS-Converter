"""Running the classification engine over a stored statement (spec §1, §5, §8).

Three things this layer is responsible for, and nothing else decides them:

**The engine never writes a final answer on its own.** A rule-derived decision
is stored as the *proposal*, and where the rule says so it carries
``requires_human_review``. An AI decision always does, by construction. A human
decision, when there is one, is written by `app.services.review`.

**A question is raised by a rule, not by a model.** Where a rule returns
``NEEDS_FACT`` the engine has said exactly which fact is missing; this module
turns that into a ``review_questions`` row, keyed so a re-run finds the
question the user already answered rather than asking again.

**Re-running never silently discards a human decision.** An overridden
classification keeps its final category; only the proposal beside it is
refreshed, so a reviewer can see that the engine now proposes something else.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.catalog import get_dictionary
from app.data.question_catalog import QuestionSpec, activity_question_key, get_questions
from app.data.rule_catalog import rule_set_version
from app.db.base import MONEY_SCALE
from app.domain.classification import ClassificationDecision
from app.domain.enums import (
    ActivityType,
    ActorType,
    AuditAction,
    ClassificationMethod,
    ConfidenceBand,
    EvidenceProducer,
    EvidenceType,
    Ifrs18Category,
    NormalizationMethod,
    ProjectStatus,
    QuestionScope,
)
from app.domain.extraction import ExtractedLine, ExtractedStatement
from app.domain.impact import line_impact_on_operating_profit
from app.domain.normalization import NormalizationReport, normalize_statement
from app.domain.rules import EntityFacts, LineFact
from app.domain.statement import ClassifiedLine
from app.models import (
    ClassificationEvidence,
    FinancialStatementLine,
    Ifrs18Classification,
    Project,
    ReviewQuestion,
)
from app.repositories.accounts import AccountRepository
from app.repositories.classifications import ClassificationRepository
from app.repositories.review import ActivityRepository, QuestionRepository
from app.repositories.statements import LineRepository, StatementRepository
from app.services import audit
from app.services.extraction import to_extracted_statement
from app.services.pipeline import classify_normalized

#: A mapping a person made by hand is a decision, not a guess.
MANUAL_MATCH_SCORE = Decimal("1.0000")

#: The scale monetary columns are stored at, as an exponent to quantize to.
STORED_SCALE = Decimal(1).scaleb(-MONEY_SCALE)


class ClassificationError(Exception):
    """The project is not in a state where classification means anything."""

    def __init__(self, reason: str, *, code: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


@dataclass(frozen=True, slots=True)
class ClassificationRun:
    classifications: tuple[Ifrs18Classification, ...]
    questions: tuple[ReviewQuestion, ...]
    #: Human decisions this run left untouched.
    preserved_overrides: int
    #: Rows dropped because their line is no longer classifiable.
    discarded: int

    @property
    def open_questions(self) -> tuple[ReviewQuestion, ...]:
        return tuple(question for question in self.questions if not question.is_resolved)

    @property
    def requires_review(self) -> int:
        return sum(1 for item in self.classifications if item.requires_human_review)


def line_key(line: ExtractedLine) -> str:
    """How a line is identified within one classification run.

    The ordinal, not the caption: two lines in one statement can print the same
    caption, and a per-line fact (B65, B72) answered for one of them must not
    leak onto the other.
    """
    return str(line.ordinal)


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


async def entity_facts(
    session: AsyncSession,
    project: Project,
    lines: list[FinancialStatementLine] | None = None,
) -> EntityFacts:
    """What the entity has confirmed, as the rule engine sees it.

    Two rules are applied here rather than in the engine:

    * **Only a human confirms a main business activity** (spec §10). A row the
      AI suggested is visible in the UI but reads as *unknown* to the engine,
      so it cannot unblock anything.
    * **A project-scoped confirmation wins over a company-wide one**, being the
      more specific statement about this reporting period.
    """
    activities = await ActivityRepository(session).for_project(project.company_id, project.id)

    main: dict[ActivityType, bool | None] = {}
    for activity in sorted(activities, key=lambda item: item.project_id is not None):
        main[ActivityType(activity.activity_type)] = (
            activity.is_main_business_activity if activity.confirmed_by_user else None
        )

    return EntityFacts(main, await _line_facts(session, project, lines))


async def _line_facts(
    session: AsyncSession, project: Project, lines: list[FinancialStatementLine] | None
) -> dict[tuple[str, str], LineFact]:
    """Answered per-line questions, keyed the way the engine looks them up."""
    if lines is None:
        lines = await LineRepository(session).for_project(project.id)
    ordinal_of = {line.id: line.ordinal for line in lines}

    facts: dict[tuple[str, str], LineFact] = {}
    for question in await QuestionRepository(session).for_project(project.id):
        if question.scope != QuestionScope.LINE or not question.is_resolved:
            continue
        if question.line_id is None or question.line_id not in ordinal_of:
            continue
        facts[(str(ordinal_of[question.line_id]), question.question_key)] = LineFact(
            question_key=question.question_key,
            category=(
                Ifrs18Category(question.resolved_category) if question.resolved_category else None
            ),
            undue_cost_or_effort=question.undue_cost_or_effort,
        )
    return facts


# ---------------------------------------------------------------------------
# Normalization, including what a person corrected by hand
# ---------------------------------------------------------------------------


def _with_manual_mappings(
    report: NormalizationReport, manual_codes: dict[int, str]
) -> NormalizationReport:
    """Substitute the account mappings a person set by hand.

    Without this a manual correction would be visible on the line and change
    nothing about the classification, which is the kind of silent no-op this
    product cannot afford.
    """
    if not manual_codes:
        return report

    dictionary = get_dictionary()
    substituted = tuple(
        (
            replace(
                normalized,
                code=manual_codes[normalized.line.ordinal],
                method=NormalizationMethod.MANUAL,
                score=MANUAL_MATCH_SCORE,
                requires_human_review=False,
                needs_decomposition=dictionary.is_ambiguous(manual_codes[normalized.line.ordinal]),
                reasoning="mapped by a reviewer",
            )
            if normalized.line.ordinal in manual_codes and not normalized.line.is_subtotal
            else normalized
        )
        for normalized in report.lines
    )
    return NormalizationReport(lines=substituted)


async def _store_normalization(
    session: AsyncSession,
    *,
    report: NormalizationReport,
    line_by_ordinal: dict[int, FinancialStatementLine],
) -> None:
    """Write each caption's resolved account back onto its line.

    A mapping a person made is never overwritten: it is a decision, and the
    dictionary does not get to revisit it.
    """
    codes = {item.code for item in report.lines if item.code}
    accounts = await AccountRepository(session).by_codes(codes)

    for normalized in report.lines:
        line = line_by_ordinal.get(normalized.line.ordinal)
        if line is None or line.is_subtotal:
            continue
        if line.normalization_method == NormalizationMethod.MANUAL:
            continue
        account = accounts.get(normalized.code) if normalized.code else None
        line.normalized_account = account
        line.normalization_method = normalized.method.value if normalized.method else None
        line.normalization_score = normalized.score if normalized.method else None


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


async def classify_project(
    session: AsyncSession,
    *,
    project: Project,
    use_ai_assistant: bool = False,
    preserve_user_overrides: bool = True,
    actor_id: uuid.UUID | None = None,
) -> ClassificationRun:
    """Classify every extracted line, and raise the questions the rules need."""
    statement = await StatementRepository(session).primary_for_project(project.id)
    if statement is None:
        raise ClassificationError(
            "Extract a statement before classifying it.", code="nothing-extracted"
        )

    lines = await LineRepository(session).for_statement(statement.id)
    if not lines:
        raise ClassificationError("The extracted statement has no lines.", code="nothing-extracted")

    source = to_extracted_statement(statement, lines)
    line_by_ordinal = {line.ordinal: line for line in lines}
    manual_codes = {
        line.ordinal: line.normalized_account.code
        for line in lines
        if line.normalization_method == NormalizationMethod.MANUAL
        and line.normalized_account is not None
    }

    report = _with_manual_mappings(normalize_statement(source, get_dictionary()), manual_codes)
    facts = await entity_facts(session, project, lines)
    classified = classify_normalized(
        report, facts, use_advisor=use_ai_assistant, line_id_of=line_key
    )
    await _store_normalization(session, report=report, line_by_ordinal=line_by_ordinal)

    existing = await ClassificationRepository(session).by_line(project.id)
    version = rule_set_version()

    stored: list[Ifrs18Classification] = []
    raised: set[uuid.UUID] = set()
    preserved = 0

    for item in classified:
        line = line_by_ordinal[item.line.ordinal]
        question = await _question_for(
            session, project=project, line=line, decision=item.decision, actor_id=actor_id
        )
        if question is not None:
            raised.add(question.id)

        row = existing.get(line.id)
        keep_final = row is not None and row.user_override and preserve_user_overrides
        preserved += 1 if keep_final else 0

        stored.append(
            await _upsert(
                session,
                project=project,
                line=line,
                item=item,
                source=source,
                existing=row,
                question=question,
                version=version,
                keep_final=keep_final,
            )
        )

    classified_lines = {line_by_ordinal[item.line.ordinal].id for item in classified}
    discarded = await ClassificationRepository(session).delete_for_lines(
        project.id, [line_id for line_id in existing if line_id not in classified_lines]
    )
    await _discard_stale_questions(session, project=project, still_raised=raised)

    project.rule_set_version = version
    project.status = (
        ProjectStatus.IN_REVIEW
        if any(row.requires_human_review for row in stored) or raised
        else ProjectStatus.CLASSIFIED
    )
    await session.flush()

    run = ClassificationRun(
        classifications=tuple(stored),
        questions=tuple(await QuestionRepository(session).for_project(project.id)),
        preserved_overrides=preserved,
        discarded=discarded,
    )

    await audit.record(
        session,
        action=AuditAction.CLASSIFIED,
        entity_type="projects",
        entity_id=project.id,
        project_id=project.id,
        actor_user_id=actor_id,
        actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
        after={
            "classified": len(stored),
            "requires_review": run.requires_review,
            "open_questions": len(run.open_questions),
            "preserved_overrides": preserved,
            "discarded": discarded,
            "rule_set_version": version,
            # Recorded because it is the one input to a run that cannot be
            # reconstructed from the rows afterwards.
            "ai_assistant_requested": use_ai_assistant,
        },
    )
    return run


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def _spec_for(decision: ClassificationDecision) -> QuestionSpec | None:
    if decision.blocked_on_activity is not None:
        key = activity_question_key(decision.blocked_on_activity)
    elif decision.blocked_on_line_fact is not None:
        key = decision.blocked_on_line_fact
    else:
        return None
    # The catalog is checked against the rule set when it loads, so a key that
    # no question defines cannot reach here.
    return get_questions()[key]


async def _question_for(
    session: AsyncSession,
    *,
    project: Project,
    line: FinancialStatementLine,
    decision: ClassificationDecision,
    actor_id: uuid.UUID | None,
) -> ReviewQuestion | None:
    """The question this decision waits on, raised if it does not exist yet."""
    spec = _spec_for(decision)
    if spec is None:
        return None

    scoped_line = line.id if spec.scope is QuestionScope.LINE else None
    questions = QuestionRepository(session)
    found = await questions.find(project.id, spec.question_key, scoped_line)
    if found is not None:
        return found

    question = await questions.add(
        ReviewQuestion(
            project_id=project.id,
            scope=spec.scope,
            line_id=scoped_line,
            question_key=spec.question_key,
            question_text_ko=spec.question_text_ko,
            question_text_en=spec.question_text_en,
            raised_by_rule_id=decision.rule_id,
        )
    )
    await audit.record(
        session,
        action=AuditAction.CREATED,
        entity_type="review_questions",
        entity_id=question.id,
        project_id=project.id,
        actor_user_id=actor_id,
        # Raised by a rule, not by a person: the actor is the system even when
        # a person started the run.
        actor_type=ActorType.SYSTEM,
        after={"question_key": spec.question_key, "raised_by_rule_id": decision.rule_id},
    )
    return question


async def _discard_stale_questions(
    session: AsyncSession, *, project: Project, still_raised: set[uuid.UUID]
) -> None:
    """Remove unanswered questions no rule is waiting on any more.

    An answered question is always kept: it records a human decision and, for a
    main business activity, the row that decision created. An *unanswered* one
    that nothing depends on would block finalization forever over a line that
    no longer exists.
    """
    questions = QuestionRepository(session)
    for question in await questions.for_project(project.id):
        if question.id in still_raised or question.answer is not None:
            continue
        await questions.delete(question)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _evidence_rows(item: ClassifiedLine) -> list[ClassificationEvidence]:
    return [
        ClassificationEvidence(
            evidence_type=EvidenceType(evidence.type),
            reference=evidence.reference,
            excerpt=evidence.note,
            produced_by=EvidenceProducer(evidence.produced_by),
        )
        for evidence in item.decision.evidence
    ]


def movement_of(
    source: ExtractedStatement,
    line: ExtractedLine,
    *,
    category: str | None,
    normalized_account_code: str | None = None,
) -> Decimal | None:
    """How this line moved operating profit, under the category that stands.

    Always the *final* category, never the proposal: once a human has
    overridden a decision, the impact table and the waterfall have to describe
    what the user decided. ERD §5 wants one definition of this number, so both
    the classification run and a later review come through here.
    """
    if category is None:
        return None
    decision = ClassificationDecision(
        line_id=line_key(line),
        category=Ifrs18Category(category),
        subcategory=None,
        # The movement depends on the category and the line, not on who
        # decided, so the rest of the decision is filler.
        method=ClassificationMethod.USER,
        rule_id=None,
        confidence=Decimal(0),
        confidence_band=ConfidenceBand.LOW,
        requires_human_review=False,
    )
    movement = line_impact_on_operating_profit(
        source,
        ClassifiedLine(
            line=line, decision=decision, normalized_account_code=normalized_account_code
        ),
    )
    # Quantized to the column's scale so the API reports the same string
    # whether the row was just computed or read back from the database.
    return movement.quantize(STORED_SCALE)


async def recompute_impact(
    session: AsyncSession, *, project: Project, classification: Ifrs18Classification
) -> None:
    """Refresh one stored movement after a human changed the category."""
    statement = await StatementRepository(session).primary_for_project(project.id)
    if statement is None:  # pragma: no cover - a classification implies a statement
        return
    lines = await LineRepository(session).for_statement(statement.id)
    source = to_extracted_statement(statement, lines)

    ordinal = next((line.ordinal for line in lines if line.id == classification.line_id), None)
    line = next((item for item in source.lines if item.ordinal == ordinal), None)
    if line is None:  # pragma: no cover - the line was removed under us
        return

    classification.impact_on_operating_profit = movement_of(
        source,
        line,
        category=classification.final_ifrs18_category,
        normalized_account_code=classification.normalized_account_code,
    )
    await session.flush()


async def _upsert(
    session: AsyncSession,
    *,
    project: Project,
    line: FinancialStatementLine,
    item: ClassifiedLine,
    source: ExtractedStatement,
    existing: Ifrs18Classification | None,
    question: ReviewQuestion | None,
    version: str,
    keep_final: bool,
) -> Ifrs18Classification:
    decision = item.decision
    proposed_category = decision.category.value
    proposed_subcategory = decision.subcategory.value if decision.subcategory else None

    row = existing
    if row is None:
        row = Ifrs18Classification(project_id=project.id, line_id=line.id)
        session.add(row)

    # Snapshots, denormalized on purpose (ERD §2): an audit record has to stay
    # readable even if the account dictionary is edited later.
    row.original_account = line.raw_label
    row.normalized_account_code = item.normalized_account_code
    row.amount = line.amount
    row.current_category = line.current_category

    row.proposed_ifrs18_category = proposed_category
    row.proposed_ifrs18_subcategory = proposed_subcategory
    row.rule_set_version = version

    if not keep_final:
        # Discarding a human decision discards all of it. Leaving
        # `user_override` set beside a rule-derived method is a state the
        # database refuses outright — rightly, since it would read as a person
        # having chosen what the engine chose.
        row.user_override = False
        row.override_reason = None
        row.reviewed_at = None
        row.reviewer_user_id = None
        row.final_ifrs18_category = proposed_category
        row.final_ifrs18_subcategory = proposed_subcategory
        row.classification_method = decision.method.value
        row.rule_id = decision.rule_id
        row.ai_confidence = decision.confidence
        row.ai_reasoning = decision.reasoning
        row.confidence_band = decision.confidence_band.value
        # Spec §1: an AI proposal always reaches a person, whatever the model
        # said about its own certainty. The engine already forces this; it is
        # restated here because the database constraint depends on it.
        row.requires_human_review = (
            decision.requires_human_review or decision.method is ClassificationMethod.AI
        )
        row.blocked_on_question_id = question.id if question is not None else None
        row.evidence.clear()
        for evidence in _evidence_rows(item):
            row.evidence.append(evidence)

    row.impact_on_operating_profit = movement_of(
        source,
        item.line,
        category=row.final_ifrs18_category,
        normalized_account_code=item.normalized_account_code,
    )
    await session.flush()
    return row
