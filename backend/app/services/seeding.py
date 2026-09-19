"""Loading the account catalog into the database.

Idempotent by design: the catalog is a shipped data file that changes between
releases, so seeding must be safe to re-run and must not orphan a line that
already points at an account. Nothing is deleted — an account that leaves the
catalog is deactivated instead, because ``financial_statement_lines`` may still
reference it and an audit trail must stay readable (ERD §4.3).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.catalog import load_catalog
from app.data.rule_catalog import load_rules
from app.domain.accounts import AccountDefinition, normalize_label
from app.domain.classification import ClassificationRuleSpec
from app.domain.enums import SynonymMatchType
from app.models import AccountSynonym, ClassificationRule, NormalizedAccount


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


# ---------------------------------------------------------------------------
# Classification rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RuleSeedResult:
    version: str
    created: int
    updated: int
    deactivated: int

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated or self.deactivated)


def _rule_differs(row: ClassificationRule, spec: ClassificationRuleSpec, version: str) -> bool:
    outcome = spec.outcome
    fact_true = spec.outcome_when_fact_true
    fact_false = spec.outcome_when_fact_false
    # For a fact-dependent rule the "false" branch is stored in the plain
    # outcome columns, so the row shape is the same either way.
    effective = fact_false or outcome
    return (
        row.version != version
        or row.priority != spec.priority
        or row.description != spec.description
        or row.condition != spec.condition
        or row.outcome_category != (effective.category.value if effective else None)
        or row.fact_true_category != (fact_true.category.value if fact_true else None)
        or row.undue_cost_category
        != (spec.undue_cost_outcome.category.value if spec.undue_cost_outcome else None)
        or row.requires_line_fact != spec.requires_line_fact
        or row.requires_human_review != spec.requires_human_review
        or row.is_residual != spec.is_residual
        or row.inherits_category != spec.inherits_category
        or row.source_reference != spec.source.reference
        or row.verification_status != spec.source.verification_status.value
        or not row.is_active
    )


def _apply_rule(row: ClassificationRule, spec: ClassificationRuleSpec, version: str) -> None:
    effective = spec.outcome_when_fact_false or spec.outcome
    fact_true = spec.outcome_when_fact_true

    row.version = version
    row.priority = spec.priority
    row.description = spec.description
    row.condition = spec.condition
    row.outcome_category = effective.category if effective else None
    row.outcome_subcategory = effective.subcategory if effective else None
    row.fact_true_category = fact_true.category if fact_true else None
    row.fact_true_subcategory = fact_true.subcategory if fact_true else None
    row.undue_cost_category = spec.undue_cost_outcome.category if spec.undue_cost_outcome else None
    row.inherits_category = spec.inherits_category
    row.requires_activity_fact = spec.requires_activity_fact
    row.requires_line_fact = spec.requires_line_fact
    row.requires_human_review = spec.requires_human_review
    row.is_residual = spec.is_residual
    row.source_type = spec.source.type
    row.source_reference = spec.source.reference
    row.source_url = spec.source.url
    row.verification_status = spec.source.verification_status
    row.verification_note = spec.source.note
    row.confidence_ceiling = spec.confidence_ceiling
    row.is_active = True


async def seed_rules(session: AsyncSession) -> RuleSeedResult:
    """Bring ``classification_rules`` in line with the shipped rule set.

    Like the account catalog, nothing is deleted: a rule that leaves the rule
    set is deactivated, because ``ifrs18_classifications.rule_id`` records which
    rule decided a figure and that record must stay explicable (spec §8).
    """
    version, standard, specs = load_rules()

    existing = {
        row.rule_id: row
        for row in (await session.execute(select(ClassificationRule))).scalars().all()
    }

    created = updated = 0
    for spec in specs:
        row = existing.get(spec.rule_id)
        if row is None:
            row = ClassificationRule(
                rule_id=spec.rule_id,
                version=version,
                priority=spec.priority,
                description=spec.description,
                condition=spec.condition,
                source_type=spec.source.type,
                source_reference=spec.source.reference,
                effective_date=dt.date.fromisoformat(standard.issued),
            )
            _apply_rule(row, spec, version)
            session.add(row)
            created += 1
        elif _rule_differs(row, spec, version):
            _apply_rule(row, spec, version)
            updated += 1

    await session.flush()

    shipped = {spec.rule_id for spec in specs}
    deactivated = 0
    for rule_id, row in existing.items():
        if rule_id not in shipped and row.is_active:
            row.is_active = False
            deactivated += 1

    await session.flush()
    return RuleSeedResult(
        version=version, created=created, updated=updated, deactivated=deactivated
    )
