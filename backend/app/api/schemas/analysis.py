"""Statement, impact, finalization and export payloads."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import Field

from app.api.schemas.common import ApiModel, Money, Ratio
from app.domain.enums import (
    ExportFormat,
    Ifrs18Category,
    ReconciliationStatus,
    SubtotalKey,
)
from app.domain.impact import ReportedPlacement, StepKind, Unit
from app.domain.validation import Severity

# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


class CheckResponse(ApiModel):
    """One reconciliation check, with the numbers behind it.

    "Could not reconcile" without the magnitude is not actionable, so expected,
    actual and delta travel with every check (spec §19).
    """

    check: str
    passed: bool
    severity: Severity
    detail: str
    expected: Money | None = None
    actual: Money | None = None
    delta: Money | None = None
    tolerance: Money


class ReconciliationResponse(ApiModel):
    status: ReconciliationStatus
    passed: bool
    checks: list[CheckResponse] = Field(default_factory=list)
    #: Failures that prevent the result being presented normally (spec §19).
    blocking_failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# The statement
# ---------------------------------------------------------------------------


class StatementLineResponse(ApiModel):
    line_id: uuid.UUID | None = None
    classification_id: uuid.UUID | None = None
    label_ko: str
    amount: Money
    normalized_account_code: str | None = None
    rule_id: str | None = None
    #: True where a person, not the engine, decided this line's category.
    user_override: bool = False


class SubtotalResponse(ApiModel):
    key: SubtotalKey
    label_en: str
    label_ko: str
    amount: Money
    #: False where IFRS 18 forbids presenting this subtotal (¶73). A flag
    #: rather than an omission, so the reason can be shown.
    presented: bool = True
    suppressed_reason: str | None = None


class SectionResponse(ApiModel):
    category: Ifrs18Category
    label_en: str
    label_ko: str
    lines: list[StatementLineResponse] = Field(default_factory=list)
    total: Money


class StatementResponse(ApiModel):
    reconciliation: ReconciliationResponse
    currency: str
    scale: int
    sections: list[SectionResponse] = Field(default_factory=list)
    subtotals: list[SubtotalResponse] = Field(default_factory=list)
    #: Returned by the API, never hardcoded in a client, so it cannot be lost
    #: in a UI refactor and is identical in every export (spec §24).
    disclaimer: str
    limitations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Impact
# ---------------------------------------------------------------------------


class KpiResponse(ApiModel):
    key: str
    unit: Unit
    #: Null where IFRS 18 introduced the measure, so there is no "before".
    before: Money | None = None
    after: Money
    change: Money | None = None
    change_pct: Ratio | None = None


class WaterfallStepResponse(ApiModel):
    kind: StepKind
    key: str
    label_ko: str
    label_en: str
    value: Money
    to_category: Ifrs18Category | None = None
    reported_placement: ReportedPlacement | None = None
    line_ids: list[str] = Field(default_factory=list)
    classification_ids: list[uuid.UUID] = Field(default_factory=list)


class ReclassificationResponse(ApiModel):
    classification_id: uuid.UUID | None = None
    line_id: uuid.UUID | None = None
    original_account: str
    amount: Money
    reported_placement: ReportedPlacement
    ifrs18_category: Ifrs18Category
    impact_on_operating_profit: Money
    rule_id: str | None = None
    rationale_summary: str | None = None


class ImpactResponse(ApiModel):
    reconciliation: ReconciliationResponse
    currency: str
    scale: int
    #: False when the source printed no operating subtotal: there is no
    #: "before" to compare against, so "no change" would be a fabrication.
    comparable: bool = True
    headline: KpiResponse | None = None
    kpis: list[KpiResponse] = Field(default_factory=list)
    waterfall: list[WaterfallStepResponse] = Field(default_factory=list)
    #: Proved server-side: every step from the start lands exactly on the end.
    waterfall_balances: bool = True
    top_reclassifications: list[ReclassificationResponse] = Field(default_factory=list)
    computed_at: dt.datetime | None = None
    disclaimer: str
    limitations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Finalization and export
# ---------------------------------------------------------------------------


class FinalizeResponse(ApiModel):
    status: str
    reconciliation: ReconciliationResponse
    impact_analysis_id: uuid.UUID
    finalized_at: dt.datetime | None = None


class ExportResponse(ApiModel):
    id: uuid.UUID
    format: ExportFormat
    status: str
    is_watermarked_unreconciled: bool
    created_at: dt.datetime
