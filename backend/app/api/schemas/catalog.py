"""Rule set, account dictionary and configuration payloads."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import Field

from app.api.schemas.common import ApiModel, Money, Ratio
from app.domain.enums import (
    AccountNature,
    ActivityType,
    Ifrs18Category,
    Ifrs18Subcategory,
    RuleVerificationStatus,
    StatementSection,
)


class StandardResponse(ApiModel):
    name: str
    issued: str
    mandatory_from: str
    early_application_permitted: bool


class RuleDetailResponse(ApiModel):
    """One rule, as a reviewer needs to audit it (spec §23, §25)."""

    rule_id: str
    priority: int
    description: str
    #: The machine-readable condition, so a reviewer can see exactly what the
    #: rule matches rather than inferring it from the description.
    condition: dict[str, Any]
    source_type: str
    source_reference: str
    #: How far the citation has been checked. `VERIFIED_SECONDARY` means
    #: against IFRS Foundation and Big 4 publications, not against the issued
    #: text — stated rather than implied.
    verification_status: RuleVerificationStatus
    source_url: str | None = None
    source_note: str | None = None
    category: Ifrs18Category | None = None
    subcategory: Ifrs18Subcategory | None = None
    #: Set where the outcome depends on a confirmed entity fact (spec §10).
    requires_activity_fact: ActivityType | None = None
    #: Set where it depends on a fact about the individual line (B65, B72).
    requires_line_fact: str | None = None
    requires_human_review: bool = False
    #: The catch-all that expresses "operating is the residual category".
    is_residual: bool = False


class RulesResponse(ApiModel):
    version: str
    standard: StandardResponse
    items: list[RuleDetailResponse] = Field(default_factory=list)


class AccountResponse(ApiModel):
    code: str
    label_ko: str
    label_en: str
    statement_section: StatementSection
    default_nature: AccountNature | None = None
    parent_code: str | None = None
    #: Aggregate captions whose contents cannot be inferred from the caption
    #: alone. These always reach human review (Q5).
    ambiguous_by_default: bool = False
    synonyms_ko: list[str] = Field(default_factory=list)
    synonyms_en: list[str] = Field(default_factory=list)


class AccountsResponse(ApiModel):
    version: str
    items: list[AccountResponse] = Field(default_factory=list)
    total: int = 0


class ClassificationConfigResponse(ApiModel):
    high_confidence_min: Ratio
    medium_confidence_min: Ratio
    profit_before_tax_tolerance: Money
    subtotal_tolerance: Money
    #: Always zero. Total invariance is exact and not configurable: if the sum
    #: of all income and expenses changes, the software has a bug, and no
    #: tolerance can make that acceptable (spec §19).
    total_invariance_tolerance: Money = Decimal(0)
    rule_set_version: str
    catalog_version: str
