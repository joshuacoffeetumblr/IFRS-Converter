"""The rule set, the account dictionary and the active thresholds.

Explainability (spec §23) is not only a per-line "why?" panel. A reviewer has
to be able to audit the engine itself: read every rule with its citation and
its verification status, see which captions the dictionary recognises, and know
what thresholds and tolerances are in force. All three are served from the same
data the engine runs on, never from a second copy.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import SettingsDep
from app.api.schemas.catalog import (
    AccountResponse,
    AccountsResponse,
    ClassificationConfigResponse,
    RuleDetailResponse,
    RulesResponse,
    StandardResponse,
)
from app.data.catalog import catalog_version, load_catalog
from app.data.rule_catalog import load_rules, rule_set_version

router = APIRouter(tags=["catalog"])


@router.get("/rules", response_model=RulesResponse)
async def list_rules() -> RulesResponse:
    """Every active rule, in the order the engine applies them.

    Ordered by priority because first match wins: reading them in any other
    order would not tell you what the engine actually does.
    """
    _, standard, rules = load_rules()
    return RulesResponse(
        version=rule_set_version(),
        standard=StandardResponse(
            name=standard.name,
            issued=standard.issued,
            mandatory_from=standard.mandatory_from,
            early_application_permitted=standard.early_application_permitted,
        ),
        items=[
            RuleDetailResponse(
                rule_id=rule.rule_id,
                priority=rule.priority,
                description=rule.description,
                condition=rule.condition,
                source_type=rule.source.type,
                source_reference=rule.source.reference,
                verification_status=rule.source.verification_status,
                source_url=rule.source.url,
                source_note=rule.source.note,
                category=rule.outcome.category if rule.outcome else None,
                subcategory=rule.outcome.subcategory if rule.outcome else None,
                requires_activity_fact=rule.requires_activity_fact,
                requires_line_fact=rule.requires_line_fact,
                requires_human_review=rule.requires_human_review,
                is_residual=rule.is_residual,
            )
            for rule in sorted(rules, key=lambda item: item.priority)
        ],
    )


@router.get("/accounts", response_model=AccountsResponse)
async def list_accounts(
    section: Annotated[str | None, Query(max_length=20)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> AccountsResponse:
    """The canonical accounts a caption can normalize to.

    Served from the catalog file rather than the table so it always matches the
    dictionary the engine matches against, seeded or not.
    """
    _, definitions = load_catalog()
    items = [
        AccountResponse(
            code=item.code,
            label_ko=item.label_ko,
            label_en=item.label_en,
            statement_section=item.section,
            default_nature=item.nature,
            parent_code=item.parent_code,
            ambiguous_by_default=item.ambiguous_by_default,
            synonyms_ko=list(item.synonyms_ko),
            synonyms_en=list(item.synonyms_en),
        )
        for item in definitions
    ]
    if section:
        items = [item for item in items if item.statement_section == section]
    if q:
        needle = q.strip().lower()
        items = [
            item
            for item in items
            if needle in item.code.lower()
            or needle in item.label_ko.lower()
            or needle in item.label_en.lower()
            or any(needle in synonym.lower() for synonym in item.synonyms_ko)
        ]
    return AccountsResponse(version=catalog_version(), items=items, total=len(items))


@router.get("/config/classification", response_model=ClassificationConfigResponse)
async def classification_config(settings: SettingsDep) -> ClassificationConfigResponse:
    """The thresholds and tolerances in force (spec §11, §19).

    A project pins these at classification time, so a finished analysis can
    always explain itself under the values that actually applied to it; this
    endpoint reports what a *new* run would use.
    """
    return ClassificationConfigResponse(
        high_confidence_min=settings.classification.high_confidence_min,
        medium_confidence_min=settings.classification.medium_confidence_min,
        profit_before_tax_tolerance=settings.reconciliation.profit_before_tax_tolerance,
        subtotal_tolerance=settings.reconciliation.subtotal_tolerance,
        rule_set_version=rule_set_version(),
        catalog_version=catalog_version(),
    )
