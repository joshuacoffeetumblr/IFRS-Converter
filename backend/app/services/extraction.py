"""Turning an uploaded file into stored statement lines (spec §17, §18).

Two things this layer must get right:

**Provenance survives.** Every stored line records the sheet, row and cell it
came from, so a figure on screen can always be traced back to the document
(spec §18). That is the whole basis of the audit trail.

**Extraction is verified before anything downstream runs.** The extracted
detail lines are summed and compared against the subtotals the document itself
printed. If we cannot reproduce a statement's own arithmetic, we have misread
it, and saying so is the only honest outcome (spec §17).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ingest import csv as csv_ingest
from app.adapters.ingest import pdf as pdf_ingest
from app.adapters.ingest import xlsx as xlsx_ingest
from app.adapters.ingest.grid import ExtractOptions, StatementNotFoundError
from app.adapters.storage.files import CSV_MIME, PDF_MIME, XLS_MIME, XLSX_MIME
from app.domain.enums import (
    ActorType,
    AuditAction,
    DecompositionStatus,
    ParseStatus,
    ProjectStatus,
    SignNormalization,
    StatementType,
    SubtotalKind,
)
from app.domain.extraction import (
    ExtractedStatement,
    ReconciliationReport,
    infer_signs_from_subtotals,
    reconcile_extraction,
)
from app.models import FinancialStatement, FinancialStatementLine, Project, UploadedFile
from app.repositories.statements import LineRepository, StatementRepository
from app.services import audit


class ExtractionError(Exception):
    """The file could not be read as an income statement."""

    def __init__(self, reason: str, *, code: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    statement: FinancialStatement
    lines: list[FinancialStatementLine]
    report: ReconciliationReport
    signs_inferred: bool

    @property
    def reconciled(self) -> bool:
        return self.report.passed


def read_statement(path: Path, mime_type: str, options: ExtractOptions) -> ExtractedStatement:
    if mime_type in {XLSX_MIME, XLS_MIME}:
        return xlsx_ingest.read_income_statement(path, options=options)
    if mime_type == CSV_MIME:
        return csv_ingest.read_income_statement(path, options=options)
    if mime_type == PDF_MIME:
        return pdf_ingest.read_income_statement(path, options=options)
    raise ExtractionError(
        f"{mime_type} cannot be extracted.",
        code="unsupported-for-extraction",
    )


async def extract(
    session: AsyncSession,
    *,
    project: Project,
    upload: UploadedFile,
    storage_dir: Path,
    options: ExtractOptions | None = None,
    actor_id: uuid.UUID | None = None,
) -> ExtractionResult:
    """Read the uploaded file and store what it contains."""
    options = options or ExtractOptions()
    path = storage_dir / upload.storage_key
    if not path.exists():  # pragma: no cover - storage inconsistency
        raise ExtractionError("The uploaded file is no longer available.", code="file-missing")

    try:
        statement = read_statement(path, upload.mime_type, options)
    except StatementNotFoundError as exc:
        upload.parse_status = ParseStatus.FAILED
        upload.parse_error = str(exc)
        project.status = ProjectStatus.EXTRACTION_FAILED
        await session.flush()
        raise ExtractionError(str(exc), code="statement-not-found") from exc

    # A statement that prints every figure unsigned contradicts its own
    # arithmetic when read literally. One hypothesis is tested, and accepted
    # only if the subtotals then reconcile.
    statement, signs_inferred = infer_signs_from_subtotals(statement)
    report = reconcile_extraction(statement)

    statements = StatementRepository(session)
    await statements.replace_project_statements(project.id)

    stored = await statements.add(
        FinancialStatement(
            project_id=project.id,
            uploaded_file_id=upload.id,
            statement_type=StatementType.INCOME_STATEMENT,
            period_start=project.period_start,
            period_end=project.period_end,
            is_comparative=options.period_index > 0,
            currency=project.presentation_currency,
            # `is None`, not `or`: a statement in 원 has scale 0, and
            # truthiness would replace it with the project's default.
            scale=(project.presentation_scale if statement.scale is None else statement.scale),
            source_locator={
                "source_file": statement.source_file,
                "sheet": statement.sheet,
                "period_label": statement.period_label,
            },
        )
    )

    rows = [
        FinancialStatementLine(
            statement_id=stored.id,
            ordinal=line.ordinal,
            depth=line.depth,
            raw_label=line.raw_label,
            raw_value=line.raw_value,
            amount=line.amount,
            sign_normalization=SignNormalization(line.sign_normalization),
            is_subtotal=line.is_subtotal,
            subtotal_kind=SubtotalKind(line.subtotal_kind) if line.subtotal_kind else None,
            source_locator=line.locator.as_dict(),
            note_references=list(line.note_references) or None,
        )
        for line in statement.lines
    ]
    await LineRepository(session).add_all(rows)

    upload.parse_status = ParseStatus.PARSED
    upload.parse_error = None
    project.status = ProjectStatus.EXTRACTED
    await session.flush()

    await audit.record(
        session,
        action=AuditAction.EXTRACTED,
        entity_type="financial_statements",
        entity_id=stored.id,
        project_id=project.id,
        actor_user_id=actor_id,
        actor_type=ActorType.USER if actor_id else ActorType.SYSTEM,
        after={
            "lines": len(rows),
            "reconciled": report.passed,
            "signs_inferred": signs_inferred,
        },
    )

    return ExtractionResult(
        statement=stored, lines=rows, report=report, signs_inferred=signs_inferred
    )


def to_extracted_statement(
    statement: FinancialStatement, lines: list[FinancialStatementLine]
) -> ExtractedStatement:
    """Rebuild the domain value object from stored rows.

    Downstream stages are pure functions over ``ExtractedStatement``, so this
    is the single crossing back from storage into the domain. Keeping it in one
    place means the classification engine never learns what a database row is.
    """
    from app.domain.extraction import ExtractedLine, SourceLocator

    def _optional_int(value: object) -> int | None:
        return int(value) if isinstance(value, int | str) else None

    def locator(raw: dict[str, object] | None) -> SourceLocator:
        data = raw or {}
        return SourceLocator(
            source_file=str(data.get("source_file", "")),
            sheet=str(data["sheet"]) if data.get("sheet") else None,
            row=_optional_int(data.get("row")),
            column=str(data["column"]) if data.get("column") else None,
            cell=str(data["cell"]) if data.get("cell") else None,
            page=_optional_int(data.get("page")),
        )

    return ExtractedStatement(
        source_file=str((statement.source_locator or {}).get("source_file", "")),
        sheet=str((statement.source_locator or {}).get("sheet") or "") or None,
        currency=statement.currency,
        scale=statement.scale,
        lines=tuple(
            ExtractedLine(
                ordinal=row.ordinal,
                raw_label=row.raw_label,
                raw_value=row.raw_value,
                amount=row.amount,
                sign_normalization=SignNormalization(row.sign_normalization),
                locator=locator(row.source_locator),
                depth=row.depth,
                is_subtotal=row.is_subtotal,
                subtotal_kind=SubtotalKind(row.subtotal_kind) if row.subtotal_kind else None,
                note_references=tuple(row.note_references or ()),
                decomposition_status=DecompositionStatus(row.decomposition_status),
            )
            for row in sorted(lines, key=lambda item: item.ordinal)
        ),
    )
