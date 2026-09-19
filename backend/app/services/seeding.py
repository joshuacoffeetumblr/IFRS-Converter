"""Loading the account catalog into the database.

Idempotent by design: the catalog is a shipped data file that changes between
releases, so seeding must be safe to re-run and must not orphan a line that
already points at an account. Nothing is deleted — an account that leaves the
catalog is deactivated instead, because ``financial_statement_lines`` may still
reference it and an audit trail must stay readable (ERD §4.3).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.catalog import load_catalog
from app.domain.accounts import AccountDefinition, normalize_label
from app.domain.enums import SynonymMatchType
from app.models import AccountSynonym, NormalizedAccount


@dataclass(frozen=True, slots=True)
class SeedResult:
    version: str
    created: int
    updated: int
    deactivated: int
    synonyms_created: int
    synonyms_removed: int

    @property
    def changed(self) -> bool:
        return bool(
            self.created
            or self.updated
            or self.deactivated
            or self.synonyms_created
            or self.synonyms_removed
        )


def _differs(row: NormalizedAccount, definition: AccountDefinition) -> bool:
    return (
        row.label_ko != definition.label_ko
        or row.label_en != definition.label_en
        or row.statement_section != definition.section
        or row.default_nature != (definition.nature.value if definition.nature else None)
        or row.parent_code != definition.parent_code
        or row.ambiguous_by_default != definition.ambiguous_by_default
        or not row.is_active
    )


async def seed_accounts(session: AsyncSession) -> SeedResult:
    """Bring ``normalized_accounts`` and ``account_synonyms`` in line with the catalog."""
    version, definitions = load_catalog()

    existing = {
        row.code: row for row in (await session.execute(select(NormalizedAccount))).scalars().all()
    }

    created = updated = 0
    # Parents are inserted before children so the self-referencing foreign key
    # on `parent_code` resolves within one flush.
    ordered = sorted(definitions, key=lambda d: (d.parent_code is not None, d.code))

    for definition in ordered:
        row = existing.get(definition.code)
        if row is None:
            session.add(
                NormalizedAccount(
                    code=definition.code,
                    label_ko=definition.label_ko,
                    label_en=definition.label_en,
                    statement_section=definition.section,
                    default_nature=definition.nature,
                    parent_code=definition.parent_code,
                    ambiguous_by_default=definition.ambiguous_by_default,
                    is_active=True,
                )
            )
            created += 1
            await session.flush()
            continue

        if _differs(row, definition):
            row.label_ko = definition.label_ko
            row.label_en = definition.label_en
            row.statement_section = definition.section
            row.default_nature = definition.nature
            row.parent_code = definition.parent_code
            row.ambiguous_by_default = definition.ambiguous_by_default
            row.is_active = True
            updated += 1

    await session.flush()

    # Deactivate, never delete: a statement line may still reference the account
    # and its audit record must remain readable.
    catalog_codes = {definition.code for definition in definitions}
    deactivated = 0
    for code, row in existing.items():
        if code not in catalog_codes and row.is_active:
            row.is_active = False
            deactivated += 1

    synonyms_created, synonyms_removed = await _seed_synonyms(session, definitions)
    await session.flush()

    return SeedResult(
        version=version,
        created=created,
        updated=updated,
        deactivated=deactivated,
        synonyms_created=synonyms_created,
        synonyms_removed=synonyms_removed,
    )


async def _seed_synonyms(
    session: AsyncSession,
    definitions: tuple[AccountDefinition, ...],
) -> tuple[int, int]:
    accounts = {
        row.code: row for row in (await session.execute(select(NormalizedAccount))).scalars().all()
    }
    existing = (await session.execute(select(AccountSynonym))).scalars().all()
    by_key = {(row.synonym, row.locale): row for row in existing}

    wanted: dict[tuple[str, str], str] = {}
    for definition in definitions:
        for form in (definition.label_ko, *definition.synonyms_ko):
            if form.strip():
                wanted[(normalize_label(form), "ko")] = definition.code
        for form in (definition.label_en, *definition.synonyms_en):
            if form.strip():
                wanted[(normalize_label(form), "en")] = definition.code

    created = 0
    for (synonym, locale), code in wanted.items():
        account = accounts.get(code)
        if account is None:
            continue
        row = by_key.get((synonym, locale))
        if row is None:
            session.add(
                AccountSynonym(
                    normalized_account_id=account.id,
                    synonym=synonym,
                    locale=locale,
                    match_type=SynonymMatchType.NORMALIZED,
                )
            )
            created += 1
        elif row.normalized_account_id != account.id:
            row.normalized_account_id = account.id

    removed = 0
    for key, row in by_key.items():
        if key not in wanted:
            await session.delete(row)
            removed += 1

    return created, removed
