"""Round-trip tests: the schema stores what the ERD says it stores."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import (
    ActivitySource,
    ActivityType,
    ClassificationMethod,
    DecompositionStatus,
    EvidenceProducer,
    EvidenceType,
    Ifrs18Category,
    Ifrs18Subcategory,
    SubtotalKind,
)
from app.models import (
    BusinessActivity,
    ClassificationEvidence,
    FinancialStatementLine,
    Ifrs18Classification,
)
from tests.db import make_line, make_project, make_statement


async def test_money_survives_full_precision(db_session: AsyncSession) -> None:
    """NUMERIC(38,6) must round-trip exactly. Binary floats would not.

    This is the reason architecture §5 bans float: reconciliation asserts exact
    equality, so a single lost digit anywhere breaks the validation gate.
    """
    project = await make_project(db_session)
    statement = await make_statement(db_session, project)
    # 32 integer digits + 6 decimal places = the full 38 digits of precision.
    extreme = Decimal("12345678901234567890123456789012.123456")

    line = await make_line(db_session, statement, ordinal=1, raw_label="매출액", amount=extreme)
    db_session.expunge_all()

    fetched = await db_session.get(FinancialStatementLine, line.id)
    assert fetched is not None
    assert fetched.amount == extreme
    assert str(fetched.amount) == str(extreme)


async def test_negative_and_zero_amounts_round_trip(db_session: AsyncSession) -> None:
    """Test vector T6: zero and negative amounts are ordinary values."""
    project = await make_project(db_session)
    statement = await make_statement(db_session, project)

    expense = await make_line(
        db_session, statement, ordinal=1, raw_label="매출원가", amount=Decimal("-70000")
    )
    zero = await make_line(
        db_session, statement, ordinal=2, raw_label="기타수익", amount=Decimal("0")
    )
    db_session.expunge_all()

    fetched_expense = await db_session.get(FinancialStatementLine, expense.id)
    fetched_zero = await db_session.get(FinancialStatementLine, zero.id)

    assert fetched_expense is not None
    assert fetched_zero is not None
    assert fetched_expense.amount == Decimal("-70000")
    assert fetched_zero.amount == Decimal("0")


async def test_full_project_graph_round_trips(db_session: AsyncSession) -> None:
    """The spec §8 audit fields survive a write and read."""
    project = await make_project(db_session)
    statement = await make_statement(db_session, project)
    line = await make_line(
        db_session, statement, ordinal=1, raw_label="이자수익", amount=Decimal("3000")
    )

    classification = Ifrs18Classification(
        project_id=project.id,
        line_id=line.id,
        original_account="이자수익",
        normalized_account_code="INTEREST_INCOME",
        amount=Decimal("3000"),
        current_category="영업외수익",
        proposed_ifrs18_category=Ifrs18Category.INVESTING,
        proposed_ifrs18_subcategory=Ifrs18Subcategory.INVESTING_INCOME,
        final_ifrs18_category=Ifrs18Category.INVESTING,
        final_ifrs18_subcategory=Ifrs18Subcategory.INVESTING_INCOME,
        classification_method=ClassificationMethod.RULE,
        rule_id="IFRS18-INVESTING-002",
        rule_set_version="2026.09.1",
        impact_on_operating_profit=Decimal("-3000"),
    )
    db_session.add(classification)
    await db_session.flush()

    db_session.add(
        ClassificationEvidence(
            classification_id=classification.id,
            evidence_type=EvidenceType.RULE_SOURCE,
            reference="IFRS 18 paragraphs 49-50",
            produced_by=EvidenceProducer.RULE,
        )
    )
    await db_session.flush()
    db_session.expunge_all()

    fetched = (
        await db_session.execute(
            select(Ifrs18Classification).where(Ifrs18Classification.line_id == line.id)
        )
    ).scalar_one()

    assert fetched.original_account == "이자수익"
    assert fetched.final_ifrs18_category == Ifrs18Category.INVESTING
    assert fetched.impact_on_operating_profit == Decimal("-3000")
    assert fetched.rule_id == "IFRS18-INVESTING-002"

    evidence = (
        (
            await db_session.execute(
                select(ClassificationEvidence).where(
                    ClassificationEvidence.classification_id == fetched.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert [e.reference for e in evidence] == ["IFRS 18 paragraphs 49-50"]


async def test_is_main_business_activity_is_three_valued(db_session: AsyncSession) -> None:
    """NULL (unknown) must stay distinguishable from False (user said no).

    Only NULL blocks a NEEDS_FACT rule. Conflating the two would silently
    classify items the user was never asked about.
    """
    project = await make_project(db_session)

    unknown = BusinessActivity(
        company_id=project.company_id,
        project_id=project.id,
        activity_type=ActivityType.INVESTING_IN_ASSETS,
        is_main_business_activity=None,
        source=ActivitySource.DOCUMENT,
    )
    denied = BusinessActivity(
        company_id=project.company_id,
        project_id=project.id,
        activity_type=ActivityType.PROVIDING_FINANCING_TO_CUSTOMERS,
        is_main_business_activity=False,
        source=ActivitySource.USER,
        confirmed_by_user=True,
        confirmed_at=dt.datetime.now(dt.UTC),
    )
    db_session.add_all([unknown, denied])
    await db_session.flush()
    db_session.expunge_all()

    rows = (await db_session.execute(select(BusinessActivity))).scalars().all()
    by_type = {r.activity_type: r.is_main_business_activity for r in rows}

    assert by_type[ActivityType.INVESTING_IN_ASSETS] is None
    assert by_type[ActivityType.PROVIDING_FINANCING_TO_CUSTOMERS] is False


async def test_is_summable_excludes_subtotals_and_decomposed_parents(
    db_session: AsyncSession,
) -> None:
    """Test vectors T5 and T13: neither may contribute to a category total."""
    project = await make_project(db_session)
    statement = await make_statement(db_session, project)

    detail = await make_line(
        db_session, statement, ordinal=1, raw_label="이자수익", amount=Decimal("40")
    )
    subtotal = await make_line(
        db_session,
        statement,
        ordinal=2,
        raw_label="매출총이익",
        amount=Decimal("30"),
        is_subtotal=True,
        subtotal_kind=SubtotalKind.GROSS_PROFIT,
    )
    aggregate = await make_line(
        db_session,
        statement,
        ordinal=3,
        raw_label="영업외수익",
        amount=Decimal("100"),
        decomposition_status=DecompositionStatus.DECOMPOSED,
    )

    assert detail.is_summable
    assert not subtotal.is_summable
    assert not aggregate.is_summable


async def test_decomposition_children_link_to_parent(db_session: AsyncSession) -> None:
    """Test vector T13: components sum back to the caption they came from."""
    project = await make_project(db_session)
    statement = await make_statement(db_session, project)

    parent = await make_line(
        db_session,
        statement,
        ordinal=1,
        raw_label="영업외수익",
        amount=Decimal("100"),
        decomposition_status=DecompositionStatus.DECOMPOSED,
    )
    children = [
        await make_line(
            db_session,
            statement,
            ordinal=2,
            raw_label="이자수익",
            amount=Decimal("40"),
            parent_line_id=parent.id,
        ),
        await make_line(
            db_session,
            statement,
            ordinal=3,
            raw_label="매출채권 외환차익",
            amount=Decimal("35"),
            parent_line_id=parent.id,
            note_references=["주석 12"],
        ),
        await make_line(
            db_session,
            statement,
            ordinal=4,
            raw_label="유형자산처분이익",
            amount=Decimal("25"),
            parent_line_id=parent.id,
        ),
    ]

    assert sum(c.amount for c in children) == parent.amount
    assert all(c.is_summable for c in children)
    assert not parent.is_summable
