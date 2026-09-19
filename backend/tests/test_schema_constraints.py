"""The database must refuse states the ERD says are impossible.

Each test here corresponds to a claim made in `docs/02-erd.md`. A constraint
that is documented but not enforced is worse than none, so every one is
exercised.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import (
    ActivitySource,
    ActivityType,
    ClassificationMethod,
    DecompositionStatus,
    Ifrs18Category,
    ProjectStatus,
    QuestionScope,
    ReconciliationStatus,
    SubtotalKind,
)
from app.models import (
    AuditLog,
    BusinessActivity,
    Ifrs18Classification,
    ReviewQuestion,
)
from tests.db import make_line, make_project, make_statement


async def _classification(session: AsyncSession, **overrides: object) -> Ifrs18Classification:
    project = await make_project(session)
    statement = await make_statement(session, project)
    line = await make_line(
        session, statement, ordinal=1, raw_label="이자수익", amount=Decimal("3000")
    )
    defaults: dict[str, object] = {
        "project_id": project.id,
        "line_id": line.id,
        "original_account": "이자수익",
        "amount": Decimal("3000"),
        "classification_method": ClassificationMethod.RULE,
        "rule_id": "IFRS18-INVESTING-002",
    }
    defaults.update(overrides)
    return Ifrs18Classification(**defaults)


# ---------------------------------------------------------------------------
# Spec §1 — the AI layer can never finalize a classification
# ---------------------------------------------------------------------------


async def test_ai_classification_must_require_human_review(db_session: AsyncSession) -> None:
    """Spec §1: an AI proposal is never final, whatever its confidence."""
    bad = await _classification(
        db_session,
        classification_method=ClassificationMethod.AI,
        rule_id=None,
        ai_confidence=Decimal("0.99"),
        requires_human_review=False,
    )
    db_session.add(bad)

    with pytest.raises(IntegrityError, match="ai_always_requires_review"):
        await db_session.flush()


async def test_ai_classification_with_review_is_accepted(db_session: AsyncSession) -> None:
    ok = await _classification(
        db_session,
        classification_method=ClassificationMethod.AI,
        rule_id=None,
        ai_confidence=Decimal("0.99"),
        requires_human_review=True,
    )
    db_session.add(ok)
    await db_session.flush()

    assert ok.id is not None


async def test_confidence_outside_zero_to_one_is_rejected(db_session: AsyncSession) -> None:
    bad = await _classification(
        db_session,
        classification_method=ClassificationMethod.AI,
        rule_id=None,
        ai_confidence=Decimal("1.5"),
        requires_human_review=True,
    )
    db_session.add(bad)

    with pytest.raises(IntegrityError, match="ai_confidence_in_range"):
        await db_session.flush()


# ---------------------------------------------------------------------------
# Spec §8 — every human decision names the human
# ---------------------------------------------------------------------------


async def test_override_without_reviewer_is_rejected(db_session: AsyncSession) -> None:
    bad = await _classification(
        db_session,
        classification_method=ClassificationMethod.USER,
        rule_id=None,
        user_override=True,
    )
    db_session.add(bad)

    with pytest.raises(IntegrityError, match="override_records_reviewer"):
        await db_session.flush()


async def test_rule_classification_must_name_its_rule(db_session: AsyncSession) -> None:
    bad = await _classification(
        db_session, classification_method=ClassificationMethod.RULE, rule_id=None
    )
    db_session.add(bad)

    with pytest.raises(IntegrityError, match="rule_method_records_rule_id"):
        await db_session.flush()


async def test_one_live_classification_per_line(db_session: AsyncSession) -> None:
    """History lives in user_reviews and audit_logs, not in duplicate rows."""
    first = await _classification(db_session)
    db_session.add(first)
    await db_session.flush()

    duplicate = Ifrs18Classification(
        project_id=first.project_id,
        line_id=first.line_id,
        original_account="이자수익",
        amount=Decimal("3000"),
        classification_method=ClassificationMethod.RULE,
        rule_id="IFRS18-INVESTING-002",
    )
    db_session.add(duplicate)

    with pytest.raises(IntegrityError):
        await db_session.flush()


# ---------------------------------------------------------------------------
# Statement lines — subtotal and decomposition invariants
# ---------------------------------------------------------------------------


async def test_subtotal_cannot_carry_a_normalized_account(db_session: AsyncSession) -> None:
    """A subtotal is a reconciliation target, never a classifiable fact."""
    project = await make_project(db_session)
    statement = await make_statement(db_session, project)

    with pytest.raises(IntegrityError, match="subtotal_has_no_account"):
        await make_line(
            db_session,
            statement,
            ordinal=1,
            raw_label="영업이익",
            amount=Decimal("30"),
            is_subtotal=True,
            normalized_account_id=uuid.uuid4(),
        )


async def test_subtotal_kind_requires_a_subtotal(db_session: AsyncSession) -> None:
    project = await make_project(db_session)
    statement = await make_statement(db_session, project)

    with pytest.raises(IntegrityError, match="subtotal_kind_requires_subtotal"):
        await make_line(
            db_session,
            statement,
            ordinal=1,
            raw_label="이자수익",
            amount=Decimal("10"),
            is_subtotal=False,
            subtotal_kind=SubtotalKind.GROSS_PROFIT,
        )


async def test_subtotal_cannot_also_be_decomposable(db_session: AsyncSession) -> None:
    project = await make_project(db_session)
    statement = await make_statement(db_session, project)

    with pytest.raises(IntegrityError, match="subtotal_not_decomposable"):
        await make_line(
            db_session,
            statement,
            ordinal=1,
            raw_label="영업이익",
            amount=Decimal("30"),
            is_subtotal=True,
            decomposition_status=DecompositionStatus.REQUIRED,
        )


async def test_decomposed_parent_cannot_itself_be_a_child(db_session: AsyncSession) -> None:
    """Otherwise a chain of containers could hide amounts from every sum."""
    project = await make_project(db_session)
    statement = await make_statement(db_session, project)
    parent = await make_line(
        db_session, statement, ordinal=1, raw_label="영업외수익", amount=Decimal("100")
    )

    with pytest.raises(IntegrityError, match="decomposed_parent_is_not_a_child"):
        await make_line(
            db_session,
            statement,
            ordinal=2,
            raw_label="기타",
            amount=Decimal("50"),
            parent_line_id=parent.id,
            decomposition_status=DecompositionStatus.DECOMPOSED,
        )


# ---------------------------------------------------------------------------
# Review questions — scope (open question Q4)
# ---------------------------------------------------------------------------


async def _question(session: AsyncSession, **overrides: object) -> ReviewQuestion:
    project = await make_project(session)
    defaults: dict[str, object] = {
        "project_id": project.id,
        "scope": QuestionScope.COMPANY,
        "question_key": "SMBA_INVESTING_IN_ASSETS",
        "question_text_ko": "금융자산 투자가 주요 사업활동입니까?",
        "question_text_en": "Is investing in assets a main business activity?",
    }
    defaults.update(overrides)
    return ReviewQuestion(**defaults)


async def test_line_scoped_question_requires_a_line(db_session: AsyncSession) -> None:
    """IFRS 18 B72 needs a per-instrument answer, so the line must be named."""
    bad = await _question(
        db_session, scope=QuestionScope.LINE, question_key="DERIVATIVE_RISK_MANAGED"
    )
    db_session.add(bad)

    with pytest.raises(IntegrityError, match="line_scope_requires_line"):
        await db_session.flush()


async def test_company_scoped_question_must_not_name_a_line(db_session: AsyncSession) -> None:
    project = await make_project(db_session)
    statement = await make_statement(db_session, project)
    line = await make_line(
        db_session, statement, ordinal=1, raw_label="통화선도", amount=Decimal("5")
    )

    bad = ReviewQuestion(
        project_id=project.id,
        scope=QuestionScope.COMPANY,
        line_id=line.id,
        question_key="SMBA_INVESTING_IN_ASSETS",
        question_text_ko="...",
        question_text_en="...",
    )
    db_session.add(bad)

    with pytest.raises(IntegrityError, match="line_scope_requires_line"):
        await db_session.flush()


async def test_two_derivative_lines_get_separate_questions(db_session: AsyncSession) -> None:
    """Test vector T14: the same question key may repeat across lines."""
    project = await make_project(db_session)
    statement = await make_statement(db_session, project)
    hedge_a = await make_line(
        db_session, statement, ordinal=1, raw_label="통화선도(차입금)", amount=Decimal("5")
    )
    hedge_b = await make_line(
        db_session, statement, ordinal=2, raw_label="통화선도(수출)", amount=Decimal("-3")
    )

    for line in (hedge_a, hedge_b):
        db_session.add(
            ReviewQuestion(
                project_id=project.id,
                scope=QuestionScope.LINE,
                line_id=line.id,
                question_key="DERIVATIVE_RISK_MANAGED",
                question_text_ko="이 파생상품은 어떤 위험을 관리합니까?",
                question_text_en="Which risk does this derivative manage?",
                raised_by_rule_id="IFRS18-DERIV-001",
            )
        )

    await db_session.flush()  # must not raise: distinct line_id


async def test_answer_must_record_who_answered(db_session: AsyncSession) -> None:
    bad = await _question(db_session, answer="YES")
    db_session.add(bad)

    with pytest.raises(IntegrityError, match="answer_records_answerer"):
        await db_session.flush()


# ---------------------------------------------------------------------------
# Spec §10 — only a human confirms a main business activity
# ---------------------------------------------------------------------------


async def test_ai_cannot_confirm_a_business_activity(db_session: AsyncSession) -> None:
    project = await make_project(db_session)
    bad = BusinessActivity(
        company_id=project.company_id,
        project_id=project.id,
        activity_type=ActivityType.INVESTING_IN_ASSETS,
        is_main_business_activity=True,
        source=ActivitySource.AI_SUGGESTED,
        confirmed_by_user=True,
        confirmed_at=dt.datetime.now(dt.UTC),
    )
    db_session.add(bad)

    with pytest.raises(IntegrityError, match="ai_cannot_self_confirm"):
        await db_session.flush()


# ---------------------------------------------------------------------------
# Spec §19 — finalization requires reconciliation
# ---------------------------------------------------------------------------


async def test_project_cannot_be_finalized_while_unreconciled(
    db_session: AsyncSession,
) -> None:
    """Test vector T10: a failing project must never look like a normal result."""
    project = await make_project(db_session)
    project.status = ProjectStatus.FINALIZED
    project.reconciliation_status = ReconciliationStatus.FAILED
    project.finalized_at = dt.datetime.now(dt.UTC)

    with pytest.raises(IntegrityError, match="finalized_requires_reconciliation"):
        await db_session.flush()


async def test_period_end_must_not_precede_period_start(db_session: AsyncSession) -> None:
    project = await make_project(db_session)
    project.period_end = dt.date(2024, 12, 31)

    with pytest.raises(IntegrityError, match="period_ordered"):
        await db_session.flush()


# ---------------------------------------------------------------------------
# Enum CHECK constraints are generated from app.domain.enums
# ---------------------------------------------------------------------------


async def test_unknown_category_value_is_rejected(db_session: AsyncSession) -> None:
    """ERD §4.1 stores enums as text + CHECK; the CHECK must actually bite."""
    bad = await _classification(db_session, final_ifrs18_category="NOT_A_CATEGORY")
    db_session.add(bad)

    with pytest.raises(IntegrityError, match="final_ifrs18_category_valid"):
        await db_session.flush()


async def test_every_real_category_is_accepted(db_session: AsyncSession) -> None:
    for category in Ifrs18Category:
        async with db_session.begin_nested():
            item = await _classification(db_session, final_ifrs18_category=category)
            db_session.add(item)
            await db_session.flush()


# ---------------------------------------------------------------------------
# Spec §8 — the audit trail is append-only
# ---------------------------------------------------------------------------


async def test_audit_log_cannot_be_updated(db_session: AsyncSession) -> None:
    project = await make_project(db_session)
    entry = AuditLog(
        actor_type="SYSTEM",
        project_id=project.id,
        entity_type="projects",
        entity_id=project.id,
        action="CREATED",
    )
    db_session.add(entry)
    await db_session.flush()

    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(
            text("UPDATE audit_logs SET action = 'DELETED' WHERE id = :id"),
            {"id": entry.id},
        )


async def test_audit_log_cannot_be_deleted(db_session: AsyncSession) -> None:
    project = await make_project(db_session)
    entry = AuditLog(
        actor_type="SYSTEM",
        project_id=project.id,
        entity_type="projects",
        entity_id=project.id,
        action="CREATED",
    )
    db_session.add(entry)
    await db_session.flush()

    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(text("DELETE FROM audit_logs WHERE id = :id"), {"id": entry.id})
