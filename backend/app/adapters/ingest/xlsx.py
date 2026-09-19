"""Reading an income statement out of a workbook (spec §18: cell provenance).

This adapter's only job is to turn a worksheet into a
:class:`~app.adapters.ingest.grid.Grid`. Locating the statement within that
grid, classifying subtotals, reading the presentation scale and building lines
are shared with every other ingest format and live in ``grid.py``.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell, MergedCell
from openpyxl.utils import get_column_letter
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from app.adapters.ingest.grid import (
    SHEET_HINTS,
    STATEMENT_ANCHORS,
    ExtractOptions,
    Grid,
    GridCell,
    StatementNotFoundError,
    extract_statement,
    normalise,
)
from app.domain.extraction import ExtractedStatement

#: Rows above the data that may carry the title, period and presentation unit.
_PREAMBLE_ROWS = 12

#: Backwards-compatible alias: callers pass the same options to every adapter.
ReadOptions = ExtractOptions


def _indent(cell: Cell | MergedCell) -> int:
    """Indentation level, from the cell's alignment or its leading spaces."""
    alignment = cell.alignment
    indent = int(alignment.indent) if alignment and alignment.indent else 0
    if indent:
        return indent
    text = str(cell.value or "")
    return (len(text) - len(text.lstrip())) // 2


def find_sheet(workbook: Workbook, sheet: str | None = None) -> Worksheet:
    """Locate the worksheet holding the income statement."""
    sheets: list[Worksheet] = list(workbook.worksheets)

    if sheet is not None:
        for candidate in sheets:
            if candidate.title == sheet:
                return candidate
        raise StatementNotFoundError(f"sheet {sheet!r} is not in the workbook")

    for candidate in sheets:
        title = candidate.title.lower()
        if any(hint.lower() in title for hint in SHEET_HINTS):
            return candidate

    # Fall back to content: a sheet containing a revenue caption.
    for candidate in sheets:
        for row in candidate.iter_rows(min_row=1, max_row=40):
            for cell in row:
                if normalise(cell.value).lower() in STATEMENT_ANCHORS:
                    return candidate

    raise StatementNotFoundError("no sheet resembles an income statement")


def worksheet_to_grid(sheet: Worksheet, source_file: str) -> Grid:
    rows: list[tuple[GridCell, ...]] = []
    for row in sheet.iter_rows():
        cells = tuple(
            GridCell(
                value=cell.value,
                row=cell.row,
                column=cell.column,
                column_label=get_column_letter(cell.column),
                indent=_indent(cell),
            )
            for cell in row
            if cell.row is not None and cell.column is not None
        )
        if cells:
            rows.append(cells)

    preamble = tuple(
        str(cell.value)
        for row in sheet.iter_rows(min_row=1, max_row=_PREAMBLE_ROWS)
        for cell in row
        if isinstance(cell.value, str)
    )

    return Grid(source_file=source_file, rows=tuple(rows), sheet=sheet.title, preamble=preamble)


def read_income_statement(
    path: Path | str,
    *,
    options: ExtractOptions | None = None,
) -> ExtractedStatement:
    """Extract an income statement from a workbook."""
    options = options or ExtractOptions()
    path = Path(path)

    # `data_only` returns cached formula results rather than formula text, so a
    # statement built with formulas still yields its figures.
    workbook = load_workbook(path, data_only=True, read_only=False)
    try:
        sheet = find_sheet(workbook, options.sheet)
        return extract_statement(worksheet_to_grid(sheet, path.name), options)
    finally:
        workbook.close()
