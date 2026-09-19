"""Seeding the account catalog into the database."""

from __future__ import annotations

import datetime as dt

import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.catalog import load_catalog
from app.data.rule_catalog import load_rules
from app.domain.enums import ActivityType, Ifrs18Category, RuleVerificationStatus
from app.models import AccountSynonym, ClassificationRule, NormalizedAccount
from app.services.seeding import seed_accounts, seed_rules


@pytest_asyncio.fixture(autouse=True)
async def empty_catalog(db_session: AsyncSession) -> None:
    """Start from an empty catalog regardless of ambient database state.

    A developer or a deployment may already have run `seed-accounts` against
    this database. These tests assert on counts, so they must not inherit that.
    The deletes live inside the test transaction and are rolled back with it.
    """
    await db_session.execute(delete(AccountSynonym))
    await db_session.execute(delete(NormalizedAccount))
    await db_session.flush()


async def test_first_seed_creates_the_whole_catalog(db_session: AsyncSession) -> None:
    _, definitions = load_catalog()

    result = await seed_accounts(db_session)

    assert result.created == len(definitions)
    assert result.updated == 0
    assert result.synonyms_created > 0

    count = (
        await db_session.execute(select(func.count()).select_from(NormalizedAccount))
    ).scalar_one()
    assert count == len(definitions)


async def test_seeding_is_idempotent(db_session: AsyncSession) -> None:
    """The catalog ships with the code, so seeding runs on every deployment."""
    await seed_accounts(db_session)

    second = await seed_accounts(db_session)

    assert not second.changed
    assert second.created == 0
    assert second.synonyms_created == 0
    assert second.synonyms_removed == 0


async def test_parent_references_are_resolvable_after_seeding(
    db_session: AsyncSession,
) -> None:
    await seed_accounts(db_session)

    rows = (await db_session.execute(select(NormalizedAccount))).scalars().all()
    codes = {row.code for row in rows}

    for row in rows:
        if row.parent_code:
            assert row.parent_code in codes, row.code


async def test_synonyms_are_stored_in_canonical_form(db_session: AsyncSession) -> None:
    """Stored normalized, so a lookup needs no per-row transformation."""
    await seed_accounts(db_session)

    row = (
        await db_session.execute(select(AccountSynonym).where(AccountSynonym.synonym == "판관비"))
    ).scalar_one()

    account = await db_session.get(NormalizedAccount, row.normalized_account_id)
    assert account is not None
    assert account.code == "SELLING_AND_ADMIN_EXPENSES"


async def test_ambiguous_flag_reaches_the_database(db_session: AsyncSession) -> None:
    await seed_accounts(db_session)

    other_income = (
        await db_session.execute(
            select(NormalizedAccount).where(NormalizedAccount.code == "OTHER_INCOME")
        )
    ).scalar_one()
    interest = (
        await db_session.execute(
            select(NormalizedAccount).where(NormalizedAccount.code == "INTEREST_INCOME")
        )
    ).scalar_one()

    assert other_income.ambiguous_by_default
    assert not interest.ambiguous_by_default


async def test_account_removed_from_the_catalog_is_deactivated_not_deleted(
    db_session: AsyncSession,
) -> None:
    """A statement line may still reference it, and audit records must stay readable."""
    await seed_accounts(db_session)
    db_session.add(
        NormalizedAccount(
            code="RETIRED_ACCOUNT",
            label_ko="폐지계정",
            label_en="Retired account",
            statement_section="PL",
            is_active=True,
        )
    )
    await db_session.flush()

    result = await seed_accounts(db_session)

    assert result.deactivated == 1
    retired = (
        await db_session.execute(
            select(NormalizedAccount).where(NormalizedAccount.code == "RETIRED_ACCOUNT")
        )
    ).scalar_one()
    assert retired is not None, "the row must survive"
    assert not retired.is_active


async def test_changed_label_is_updated_in_place(db_session: AsyncSession) -> None:
    await seed_accounts(db_session)
    revenue = (
        await db_session.execute(
            select(NormalizedAccount).where(NormalizedAccount.code == "REVENUE")
        )
    ).scalar_one()
    revenue.label_en = "stale"
    await db_session.flush()

    result = await seed_accounts(db_session)

    assert result.updated == 1
    await db_session.refresh(revenue)
    assert revenue.label_en == "Revenue"


# ---------------------------------------------------------------------------
# Classification rules
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def empty_rules(db_session: AsyncSession) -> None:
    await db_session.execute(delete(ClassificationRule))
    await db_session.flush()


async def test_rules_are_seeded(db_session: AsyncSession, empty_rules: None) -> None:
    _, _, specs = load_rules()

    result = await seed_rules(db_session)

    assert result.created == len(specs)
    count = (
        await db_session.execute(select(func.count()).select_from(ClassificationRule))
    ).scalar_one()
    assert count == len(specs)


async def test_rule_seeding_is_idempotent(db_session: AsyncSession, empty_rules: None) -> None:
    await seed_rules(db_session)

    second = await seed_rules(db_session)

    assert not second.changed


async def test_fact_dependent_rule_stores_both_outcomes(
    db_session: AsyncSession, empty_rules: None
) -> None:
    """One reviewable row, not a pair that must be kept in step by hand."""
    await seed_rules(db_session)

    row = (
        await db_session.execute(
            select(ClassificationRule).where(ClassificationRule.rule_id == "IFRS18-INVESTING-002")
        )
    ).scalar_one()

    assert row.requires_activity_fact == ActivityType.INVESTING_IN_ASSETS
    assert row.outcome_category == Ifrs18Category.INVESTING
    assert row.fact_true_category == Ifrs18Category.OPERATING


async def test_line_fact_rule_records_the_undue_cost_landing(
    db_session: AsyncSession, empty_rules: None
) -> None:
    """B65 and B72 both fall back to operating for undue cost or effort."""
    await seed_rules(db_session)

    row = (
        await db_session.execute(
            select(ClassificationRule).where(ClassificationRule.rule_id == "IFRS18-DERIV-001")
        )
    ).scalar_one()

    assert row.requires_line_fact == "DERIVATIVE_RISK_MANAGED"
    assert row.inherits_category
    assert row.undue_cost_category == Ifrs18Category.OPERATING
    assert "B72" in row.source_reference


async def test_citations_and_verification_status_reach_the_database(
    db_session: AsyncSession, empty_rules: None
) -> None:
    """Spec §25: the rule's basis must be queryable, not just in a file."""
    await seed_rules(db_session)

    rows = (await db_session.execute(select(ClassificationRule))).scalars().all()

    for row in rows:
        assert row.source_reference, row.rule_id
        assert row.verification_status == RuleVerificationStatus.VERIFIED_SECONDARY, row.rule_id


async def test_rule_removed_from_the_set_is_deactivated_not_deleted(
    db_session: AsyncSession, empty_rules: None
) -> None:
    """ifrs18_classifications.rule_id records which rule decided a figure."""
    await seed_rules(db_session)
    db_session.add(
        ClassificationRule(
            rule_id="IFRS18-RETIRED-001",
            version="old",
            priority=5000,
            description="retired",
            condition={"field": {"name": "statement_section", "op": "eq", "value": "PL"}},
            outcome_category=Ifrs18Category.OPERATING,
            source_type="OTHER",
            source_reference="retired",
            effective_date=dt.date(2024, 4, 9),
            is_active=True,
        )
    )
    await db_session.flush()

    result = await seed_rules(db_session)

    assert result.deactivated == 1
    retired = (
        await db_session.execute(
            select(ClassificationRule).where(ClassificationRule.rule_id == "IFRS18-RETIRED-001")
        )
    ).scalar_one()
    assert retired is not None
    assert not retired.is_active
