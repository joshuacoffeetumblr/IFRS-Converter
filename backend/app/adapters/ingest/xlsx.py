"""Reading an income statement out of a workbook, with cell-level provenance.

An adapter, not domain logic: it turns a file into
:class:`~app.domain.extraction.ExtractedStatement` and nothing more. All
validation of the result happens in the domain layer, so the same checks apply
however the data arrived.

Spec §18 requires the originating cell to be recoverable from the audit trail,
so every line records its sheet, row and column.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell, MergedCell
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.domain.enums import SignNormalization, SubtotalKind
from app.domain.extraction import ExtractedLine, ExtractedStatement, SourceLocator
from app.domain.money import (
    AmountParseError,
    ParsedAmount,
    decimal_from_spreadsheet_value,
    looks_like_amount,
    parse_amount,
)

# ---------------------------------------------------------------------------
# Recognising the statement
# ---------------------------------------------------------------------------

#: Captions that identify a row as a subtotal printed by the source, mapped to
#: what it represents. Matched against the label with whitespace removed.
#: Order matters: the longest, most specific captions are tested first so
#: 법인세차감전순이익 is not mistaken for 당기순이익.
SUBTOTAL_CAPTIONS: tuple[tuple[str, SubtotalKind], ...] = (
    ("매출총이익", SubtotalKind.GROSS_PROFIT),
    ("매출총손실", SubtotalKind.GROSS_PROFIT),
    ("법인세차감전순이익", SubtotalKind.PROFIT_BEFORE_TAX),
    ("법인세비용차감전순이익", SubtotalKind.PROFIT_BEFORE_TAX),
    ("법인세차감전계속영업이익", SubtotalKind.PROFIT_BEFORE_TAX),
    ("영업이익", SubtotalKind.REPORTED_OPERATING_PROFIT),
    ("영업손실", SubtotalKind.REPORTED_OPERATING_PROFIT),
    ("당기순이익", SubtotalKind.PROFIT_FOR_THE_PERIOD),
    ("당기순손실", SubtotalKind.PROFIT_FOR_THE_PERIOD),
    ("분기순이익", SubtotalKind.PROFIT_FOR_THE_PERIOD),
    ("반기순이익", SubtotalKind.PROFIT_FOR_THE_PERIOD),
)

#: Sheet names that suggest an income statement.
_SHEET_HINTS = ("손익계산서", "포괄손익", "손익", "income statement", "profit or loss")

#: Captions that reliably appear in an income statement, used to locate it when
#: the sheet name is unhelpful.
_STATEMENT_ANCHORS = ("매출액", "수익(매출액)", "영업수익", "revenue")

#: ``(단위: 백만원)`` and friends.
_SCALE_UNITS: tuple[tuple[str, int], ...] = (
    ("십억원", 9),
    ("백만원", 6),
    ("천원", 3),
    ("억원", 8),
    ("원", 0),
)
_UNIT_PATTERN = re.compile(r"단위\s*[:：]?\s*([^)\]]*)")

#: A cell that merely repeats the unit or a heading, never a line item.
_NON_ITEM_PATTERN = re.compile(r"^(과목|계정과목|항목|구\s*분|account|description)$", re.I)


class StatementNotFoundError(ValueError):
    """No sheet in the workbook looks like an income statement."""


@dataclass(frozen=True, slots=True)
class ReadOptions:
    """Caller-supplied hints. Every field is optional; all are auto-detected."""

    sheet: str | None = None
    header_row: int | None = None
    label_column: int | None = None
    amount_column: int | None = None
    note_column: int | None = None
    #: Which period column to read when several are present. 0 is the first
    #: (normally the current period), 1 the comparative, and so on.
    period_index: int = 0


def _normalise(label: object) -> str:
    return re.sub(r"\s+", "", str(label or ""))


def classify_subtotal(label: str) -> SubtotalKind | None:
    """Return the subtotal a caption denotes, or ``None`` for a detail line."""
    compact = _normalise(label)
    if not compact:
        return None
    for caption, kind in SUBTOTAL_CAPTIONS:
        if compact == caption or compact.startswith(caption):
            return kind
    return None


def detect_scale(sheet: Worksheet, *, search_rows: int = 12) -> int:
    """Read ``(단위: 백만원)`` into a power of ten. Defaults to 0 (원)."""
    for row in sheet.iter_rows(min_row=1, max_row=search_rows):
        for cell in row:
            if not isinstance(cell.value, str):
                continue
            match = _UNIT_PATTERN.search(cell.value)
            if not match:
                continue
            unit_text = match.group(1)
            for unit, power in _SCALE_UNITS:
                if unit in unit_text:
                    return power
    return 0


def _find_sheet(workbook: object, options: ReadOptions) -> Worksheet:
    sheets: list[Worksheet] = list(workbook.worksheets)  # type: ignore[attr-defined]

    if options.sheet is not None:
        for sheet in sheets:
            if sheet.title == options.sheet:
                return sheet
        raise StatementNotFoundError(f"sheet {options.sheet!r} is not in the workbook")

    for sheet in sheets:
        title = sheet.title.lower()
        if any(hint.lower() in title for hint in _SHEET_HINTS):
            return sheet

    # Fall back to content: a sheet containing a revenue caption.
    for sheet in sheets:
        for row in sheet.iter_rows(min_row=1, max_row=40):
            for cell in row:
                if _normalise(cell.value).lower() in _STATEMENT_ANCHORS:
                    return sheet

    raise StatementNotFoundError("no sheet resembles an income statement")


def _find_layout(sheet: Worksheet, options: ReadOptions) -> tuple[int, int, list[int]]:
    """Locate (label column, first data row, ordered amount columns).

    Amount columns are found by looking at the first row that pairs a text label
    with at least one figure, rather than by trusting a header, because header
    captions vary far more than the data shape does.
    """
    for row in sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, 60)):
        label_column: int | None = None
        amount_columns: list[int] = []
        for cell in row:
            if cell.value is None:
                continue
            if label_column is None and isinstance(cell.value, str):
                if _NON_ITEM_PATTERN.match(_normalise(cell.value)):
                    break  # a header row, not a data row
                if not looks_like_amount(cell.value):
                    label_column = cell.column
                    continue
            if label_column is not None and looks_like_amount(cell.value):
                amount_columns.append(cell.column)
        if label_column is not None and amount_columns:
            data_row = row[0].row
            if data_row is None:  # pragma: no cover - openpyxl always sets this
                continue
            return label_column, data_row, amount_columns

    raise StatementNotFoundError("could not locate a label column paired with figures")


def _parse_cell(value: object) -> ParsedAmount:
    """Read one amount cell.

    Numeric cells arrive already typed, and a spreadsheet stores numbers as
    doubles, so they go through ``decimal_from_spreadsheet_value`` rather than
    ``parse_amount`` — the conversion out of binary floating point is explicit
    and happens in exactly one place. Text cells carry the printed conventions
    (parentheses, triangle marks) and go to ``parse_amount``.
    """
    if isinstance(value, str):
        return parse_amount(value)
    return ParsedAmount(
        decimal_from_spreadsheet_value(value),
        SignNormalization.AS_IS,
    )


def _indent(cell: Cell | MergedCell) -> int:
    alignment = cell.alignment
    indent = int(alignment.indent) if alignment and alignment.indent else 0
    if indent:
        return indent
    text = str(cell.value or "")
    leading = len(text) - len(text.lstrip())
    return leading // 2


def read_income_statement(
    path: Path | str,
    *,
    options: ReadOptions | None = None,
) -> ExtractedStatement:
    """Extract an income statement from a workbook.

    Figures are read exactly as printed and converted to signed profit-or-loss
    effects where the cell says so. Signs the document never printed are **not**
    guessed here — that is
    :func:`~app.domain.extraction.infer_signs_from_subtotals`, which accepts a
    derived sign only when the statement's own subtotals then reconcile.
    """
    options = options or ReadOptions()
    path = Path(path)

    # `data_only` returns cached formula results rather than formula text: a
    # statement built with formulas must still yield its figures.
    workbook = load_workbook(path, data_only=True, read_only=False)
    try:
        sheet = _find_sheet(workbook, options)
        label_column, first_row, amount_columns = _find_layout(sheet, options)

        if options.label_column is not None:
            label_column = options.label_column
        if options.header_row is not None:
            first_row = options.header_row + 1
        if options.amount_column is not None:
            amount_columns = [options.amount_column]

        if options.period_index >= len(amount_columns):
            raise StatementNotFoundError(
                f"period_index {options.period_index} requested but the statement "
                f"has {len(amount_columns)} amount column(s)"
            )
        amount_column = amount_columns[options.period_index]

        lines: list[ExtractedLine] = []
        ordinal = 0
        for excel_row in range(first_row, sheet.max_row + 1):
            label_cell = sheet.cell(row=excel_row, column=label_column)
            raw_label = str(label_cell.value or "").strip()
            if not raw_label or _NON_ITEM_PATTERN.match(_normalise(raw_label)):
                continue

            amount_cell = sheet.cell(row=excel_row, column=amount_column)
            if amount_cell.value is None:
                continue
            try:
                parsed = _parse_cell(amount_cell.value)
            except AmountParseError:
                # A stray text cell in the amount column is not a line item.
                continue

            note = None
            if options.note_column is not None:
                note = sheet.cell(row=excel_row, column=options.note_column).value
            else:
                for column in range(label_column + 1, amount_column):
                    candidate = sheet.cell(row=excel_row, column=column).value
                    if isinstance(candidate, str) and candidate.strip():
                        note = candidate
                        break

            subtotal_kind = classify_subtotal(raw_label)
            column_letter = get_column_letter(amount_column)
            lines.append(
                ExtractedLine(
                    ordinal=ordinal,
                    raw_label=raw_label,
                    raw_value=str(amount_cell.value),
                    amount=parsed.value,
                    sign_normalization=parsed.sign_normalization,
                    depth=_indent(label_cell),
                    is_subtotal=subtotal_kind is not None,
                    subtotal_kind=subtotal_kind,
                    is_nil=parsed.is_nil,
                    note_references=(str(note).strip(),) if note else (),
                    locator=SourceLocator(
                        source_file=path.name,
                        sheet=sheet.title,
                        row=excel_row,
                        column=column_letter,
                        cell=f"{column_letter}{excel_row}",
                    ),
                )
            )
            ordinal += 1

        period_label = sheet.cell(row=first_row - 1, column=amount_column).value

        return ExtractedStatement(
            source_file=path.name,
            sheet=sheet.title,
            lines=tuple(lines),
            scale=detect_scale(sheet),
            period_label=str(period_label).strip() if period_label else None,
        )
    finally:
        workbook.close()


def total_of_details(statement: ExtractedStatement) -> Decimal:
    """Convenience for callers that only need the bottom line."""
    return sum((line.amount for line in statement.detail_lines), start=Decimal(0))
