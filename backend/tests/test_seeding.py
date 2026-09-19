"""Seeding the account catalog into the database."""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.catalog import load_catalog
from app.models import AccountSynonym, NormalizedAccount
from app.services.seeding import seed_accounts


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
