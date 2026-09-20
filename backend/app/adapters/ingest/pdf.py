"""Reading an income statement out of a text-based PDF (spec §3, Phase 3c).

Like every other adapter, this one's only job is to produce a
:class:`~app.adapters.ingest.grid.Grid`. Locating the statement, classifying
subtotals and reconciling against the document's own subtotals are shared and
live in ``grid.py``.

What makes a PDF different is that it has no cells. A page is a bag of
positioned words, and the columns a reader sees are an artefact of where the
ink landed. So the words are grouped into rows by their vertical position and
into columns by horizontal gaps — and where that grouping is ambiguous, the
extraction reconciliation catches it, because a misread column does not
reproduce the statement's own subtotals.

**Scanned pages are refused, not guessed at.** A page whose text layer is empty
has been photographed, and OCR is explicitly out of MVP scope (§33) precisely
because its failures are silent: a misread digit looks exactly like a correct
one. Saying "this is a scan" is the only honest outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pdfplumber

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

#: Words whose baselines differ by less than this are on the same visual row.
#: Korean filings are typeset at 8 to 11pt, so 3pt is comfortably inside one
#: line and comfortably outside the gap between two.
ROW_TOLERANCE = 3.0

#: A horizontal gap at least this wide separates one column from the next.
#: Narrower than the indentation used for sub-items, wider than inter-word
#: spacing at the sizes these documents use.
COLUMN_GAP = 12.0

#: Leading whitespace of this many points counts as one indentation level.
#: Sub-items in Korean statements are indented by roughly one character.
INDENT_WIDTH = 10.0

#: Rows above the table that may carry the title, period and presentation unit.
PREAMBLE_ROWS = 12


@dataclass(frozen=True, slots=True)
class _Word:
    text: str
    x0: float
    x1: float
    top: float


def _words(page: Any) -> list[_Word]:
    extracted = page.extract_words(
        keep_blank_chars=False,
        use_text_flow=False,
        # Korean filings routinely mix fonts within a row (bold subtotals,
        # regular details), so splitting on font changes would fragment a
        # caption into pieces.
        extra_attrs=[],
    )
    return [
        _Word(
            text=str(word["text"]),
            x0=float(word["x0"]),
            x1=float(word["x1"]),
            top=float(word["top"]),
        )
        for word in extracted
        if str(word["text"]).strip()
    ]


def _rows(words: list[_Word]) -> list[list[_Word]]:
    """Group words into visual rows by their vertical position."""
    rows: list[list[_Word]] = []
    for word in sorted(words, key=lambda item: (item.top, item.x0)):
        if rows and abs(rows[-1][0].top - word.top) <= ROW_TOLERANCE:
            rows[-1].append(word)
        else:
            rows.append([word])
    return [sorted(row, key=lambda item: item.x0) for row in rows]


def _cells(row: list[_Word]) -> list[tuple[str, float, float]]:
    """Join words separated by ordinary spacing; split on a column gap.

    Returns each cell's text with the x-range it occupies, which is what the
    column assignment and the indentation of a caption are read from.
    """
    cells: list[tuple[list[str], float, float]] = []

    for word in row:
        if cells and word.x0 - cells[-1][2] <= COLUMN_GAP:
            parts, x0, _ = cells[-1]
            parts.append(word.text)
            cells[-1] = (parts, x0, word.x1)
        else:
            cells.append(([word.text], word.x0, word.x1))

    return [(" ".join(parts), x0, x1) for parts, x0, x1 in cells]


def _column_bands(rows: list[list[tuple[str, float, float]]]) -> list[tuple[float, float]]:
    """Work out where the page's columns are, from every row at once.

    A PDF has no columns — only ink at positions — so a cell's column has to be
    decided by **where it sits on the page**, not by how many cells happen to
    precede it in its row. Deciding it per row is the mistake that silently
    drops subtotals: a subtotal line usually has no note, so its figure would
    land in the note's column and the reader would look for figures elsewhere.

    Cells whose horizontal extents overlap are in the same column, so the bands
    are the merged intervals of every cell on the page. Right-aligned figures
    overlap each other by construction, which is what makes this work for the
    figure column.
    """
    intervals = sorted((x0, x1) for row in rows for _, x0, x1 in row)
    bands: list[tuple[float, float]] = []
    for start, end in intervals:
        if bands and start <= bands[-1][1]:
            bands[-1] = (bands[-1][0], max(bands[-1][1], end))
        else:
            bands.append((start, end))
    return bands


def _band_of(bands: list[tuple[float, float]], x0: float, x1: float) -> int:
    """The 1-based column a cell belongs to: the band it overlaps most."""
    best_index = 1
    best_overlap = -1.0
    for index, (start, end) in enumerate(bands, start=1):
        overlap = min(x1, end) - max(x0, start)
        if overlap > best_overlap:
            best_index, best_overlap = index, overlap
    return best_index


def page_to_grid(page: Any, source_file: str, *, page_number: int, left_margin: float) -> Grid:
    """Turn one page's words into a grid of rows and columns."""
    rows = [_cells(row) for row in _rows(_words(page))]
    bands = _column_bands(rows)

    grid_rows: list[tuple[GridCell, ...]] = []
    for row_index, row in enumerate(rows, start=1):
        cells = tuple(
            GridCell(
                value=text,
                row=row_index,
                column=_band_of(bands, x0, x1),
                # A PDF has no column letters; the page carries the provenance.
                column_label=None,
                indent=(
                    int(max(0.0, x0 - left_margin) // INDENT_WIDTH)
                    if _band_of(bands, x0, x1) == 1
                    else 0
                ),
            )
            for text, x0, x1 in row
        )
        if cells:
            grid_rows.append(cells)

    preamble = tuple(" ".join(cell.text for cell in row) for row in grid_rows[:PREAMBLE_ROWS])
    return Grid(
        source_file=source_file,
        rows=tuple(grid_rows),
        sheet=f"p.{page_number}",
        preamble=preamble,
        page=page_number,
    )


def _left_margin(page: Any) -> float:
    """Where the leftmost column starts, so indentation is measured from it."""
    words = _words(page)
    return min((word.x0 for word in words), default=0.0)


def _looks_like_the_statement(grid: Grid) -> bool:
    """Whether this page carries an income statement.

    Judged on the same hints every adapter uses: the title, or the captions
    that reliably appear in one. A PDF filing holds the balance sheet, the cash
    flow statement and the notes in the same file, so picking the wrong page is
    the most likely way to read the wrong numbers.
    """
    text = " ".join(cell.text for row in grid.rows for cell in row)
    normalised = normalise(text)
    if any(normalise(hint) in normalised for hint in SHEET_HINTS):
        return True
    return any(normalise(anchor) in normalised for anchor in STATEMENT_ANCHORS)


def read_income_statement(
    path: Path | str,
    *,
    options: ExtractOptions | None = None,
) -> ExtractedStatement:
    """Extract an income statement from a text-based PDF.

    ``options.sheet`` selects a page explicitly, as ``"p.4"`` or ``"4"``; left
    unset, the pages are searched for one that looks like an income statement
    and the first that also yields lines is used.
    """
    options = options or ExtractOptions()
    path = Path(path)

    with pdfplumber.open(path) as document:
        if not document.pages:
            raise StatementNotFoundError("the PDF has no pages")

        wanted = _requested_page(options.sheet)
        empty_pages = 0
        candidates: list[tuple[int, Grid]] = []

        for number, page in enumerate(document.pages, start=1):
            if wanted is not None and number != wanted:
                continue
            grid = page_to_grid(page, path.name, page_number=number, left_margin=_left_margin(page))
            if not grid.rows:
                empty_pages += 1
                continue
            if wanted is not None or _looks_like_the_statement(grid):
                candidates.append((number, grid))

        if not candidates:
            if empty_pages:
                raise StatementNotFoundError(
                    "This PDF has no text layer — it is a scan. Scanned documents "
                    "are out of scope: OCR misreads a digit silently, which is the "
                    "one failure this tool must not have. Upload the XLSX or CSV "
                    "the filing was produced from."
                )
            raise StatementNotFoundError("No page in this PDF looks like an income statement.")

        # The first candidate page that actually yields lines. A page can carry
        # the title and continue onto the next, so "looks like it" is not by
        # itself enough.
        last_error: StatementNotFoundError | None = None
        for _, grid in candidates:
            try:
                return extract_statement(grid, options)
            except StatementNotFoundError as exc:
                last_error = exc

        raise last_error or StatementNotFoundError(
            "No page in this PDF could be read as an income statement."
        )


def _requested_page(sheet: str | None) -> int | None:
    if not sheet:
        return None
    text = sheet.lower().removeprefix("p.").removeprefix("page").strip()
    return int(text) if text.isdigit() else None
