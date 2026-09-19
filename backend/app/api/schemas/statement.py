"""Upload, extraction and line payloads."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import Field

from app.api.schemas.common import ApiModel, Money
from app.domain.enums import (
    DecompositionStatus,
    ParseStatus,
    ScanStatus,
    SignNormalization,
    StatementType,
    SubtotalKind,
)


class UploadResponse(ApiModel):
    id: uuid.UUID
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    scan_status: ScanStatus
    parse_status: ParseStatus
    parse_error: str | None = None
    created_at: dt.datetime


class ExtractRequest(ApiModel):
    """Hints for the reader. Every field is optional; all are auto-detected."""

    uploaded_file_id: uuid.UUID | None = None
    sheet: str | None = Field(default=None, max_length=200)
    header_row: int | None = Field(default=None, ge=1, le=10_000)
    label_column: int | None = Field(default=None, ge=1, le=1_000)
    amount_column: int | None = Field(default=None, ge=1, le=1_000)
    note_column: int | None = Field(default=None, ge=1, le=1_000)
    #: Which period column to read. 0 is the current period, 1 the comparative.
    period_index: int = Field(default=0, ge=0, le=20)


class SourceLocatorResponse(ApiModel):
    """Where a figure came from (spec §18)."""

    source_file: str
    sheet: str | None = None
    row: int | None = None
    column: str | None = None
    cell: str | None = None
    page: int | None = None


class LineResponse(ApiModel):
    id: uuid.UUID
    ordinal: int
    depth: int
    raw_label: str
    raw_value: str
    #: The signed effect on profit or loss. A string, not a number — see
    #: `app.api.schemas.common`.
    amount: Money
    sign_normalization: SignNormalization
    is_subtotal: bool
    subtotal_kind: SubtotalKind | None
    decomposition_status: DecompositionStatus
    parent_line_id: uuid.UUID | None
    normalized_account_code: str | None = None
    note_references: list[str] = Field(default_factory=list)
    source_locator: SourceLocatorResponse


class ReconciliationCheckResponse(ApiModel):
    check: str
    reported: Money
    computed: Money
    delta: Money
    tolerance: Money
    passed: bool


class ExtractionReportResponse(ApiModel):
    """Spec §17: extraction is verified against the source's own subtotals."""

    passed: bool
    #: Problems that prevent checking at all, such as a statement with no
    #: printed subtotals — where reporting "passed" would be a lie, since
    #: nothing was compared.
    blockers: list[str] = Field(default_factory=list)
    checks: list[ReconciliationCheckResponse] = Field(default_factory=list)
    #: True when the statement printed every figure unsigned and the signs were
    #: derived, which is only accepted when the subtotals then reconcile.
    signs_inferred: bool = False


class StatementResponse(ApiModel):
    id: uuid.UUID
    statement_type: StatementType
    is_comparative: bool
    currency: str
    scale: int
    period_start: dt.date | None
    period_end: dt.date | None


class ExtractResponse(ApiModel):
    statement: StatementResponse
    line_count: int
    report: ExtractionReportResponse


class LinesResponse(ApiModel):
    items: list[LineResponse]
    report: ExtractionReportResponse | None = None


class UpdateLineRequest(ApiModel):
    """Correcting a misread cell.

    Amounts arrive as strings for the same reason they leave as strings: a JSON
    number would be parsed as a double and could not represent the full stored
    precision.
    """

    raw_label: str | None = Field(default=None, min_length=1, max_length=500)
    amount: Money | None = None
    is_subtotal: bool | None = None
    subtotal_kind: SubtotalKind | None = None
    normalized_account_code: str | None = Field(default=None, max_length=100)
