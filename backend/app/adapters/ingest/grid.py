"""A format-neutral grid, and the extraction logic shared by every ingest adapter.

XLSX, CSV and (later) PDF tables all reduce to the same thing: a rectangle of
cells, some holding captions and some holding figures. Locating the statement
within that rectangle, deciding which column is which, reading the presentation
scale and classifying subtotals are identical in every case, so they live here
once rather than in each adapter (spec §29: no duplicated business logic).

An adapter's job is therefore narrow: turn its file format into a :class:`Grid`,
preserving enough positional information for provenance (spec §18).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.accounts import normalize_label
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
# Recognising captions
# ---------------------------------------------------------------------------

#: Captions identifying a row as a subtotal printed by the source. Ordered so
#: the longest, most specific caption is tested first — otherwise
#: 법인세차감전순이익 would be matched as 당기순이익.
#:
#: The third field says whether a caption may match as a **prefix**. Korean
#: subtotals carry their qualifiers as suffixes, so a prefix match is what
#: recognises 영업이익(손실) and 당기순이익 귀속분. English captions do not
#: behave that way: `Profit (loss)` reduces to `profit`, and prefix-matching
#: that would make a subtotal of `Profit from disposal of investments` —
#: a detail line, silently added to the total it was supposed to verify.
SUBTOTAL_CAPTIONS: tuple[tuple[str, SubtotalKind, bool], ...] = (
    ("법인세비용차감전순이익", SubtotalKind.PROFIT_BEFORE_TAX, True),
    ("법인세차감전계속영업이익", SubtotalKind.PROFIT_BEFORE_TAX, True),
    ("법인세차감전순이익", SubtotalKind.PROFIT_BEFORE_TAX, True),
    ("법인세차감전순손실", SubtotalKind.PROFIT_BEFORE_TAX, True),
    ("매출총이익", SubtotalKind.GROSS_PROFIT, True),
    ("매출총손실", SubtotalKind.GROSS_PROFIT, True),
    ("영업이익", SubtotalKind.REPORTED_OPERATING_PROFIT, True),
    ("영업손실", SubtotalKind.REPORTED_OPERATING_PROFIT, True),
    ("당기순이익", SubtotalKind.PROFIT_FOR_THE_PERIOD, True),
    ("당기순손실", SubtotalKind.PROFIT_FOR_THE_PERIOD, True),
    ("반기순이익", SubtotalKind.PROFIT_FOR_THE_PERIOD, True),
    ("분기순이익", SubtotalKind.PROFIT_FOR_THE_PERIOD, True),
    # The IFRS taxonomy's own standard labels, which is what a filing prints
    # in English and what an XBRL-derived statement carries. Without these a
    # statement captioned in English has no subtotals at all — so its own
    # arithmetic can never be checked, and it is refused as unreadable (§17)
    # however well it was extracted.
    ("profit (loss) before tax", SubtotalKind.PROFIT_BEFORE_TAX, False),
    ("profit or loss before tax", SubtotalKind.PROFIT_BEFORE_TAX, False),
    ("profit before tax", SubtotalKind.PROFIT_BEFORE_TAX, False),
    ("loss before tax", SubtotalKind.PROFIT_BEFORE_TAX, False),
    ("profit before income tax", SubtotalKind.PROFIT_BEFORE_TAX, False),
    ("gross profit", SubtotalKind.GROSS_PROFIT, False),
    ("gross loss", SubtotalKind.GROSS_PROFIT, False),
    ("operating profit", SubtotalKind.REPORTED_OPERATING_PROFIT, False),
    ("operating income", SubtotalKind.REPORTED_OPERATING_PROFIT, False),
    ("operating loss", SubtotalKind.REPORTED_OPERATING_PROFIT, False),
    ("operating income (loss)", SubtotalKind.REPORTED_OPERATING_PROFIT, False),
    ("profit (loss)", SubtotalKind.PROFIT_FOR_THE_PERIOD, False),
    ("profit or loss", SubtotalKind.PROFIT_FOR_THE_PERIOD, False),
    ("profit for the period", SubtotalKind.PROFIT_FOR_THE_PERIOD, False),
    ("loss for the period", SubtotalKind.PROFIT_FOR_THE_PERIOD, False),
    ("profit (loss) for the period", SubtotalKind.PROFIT_FOR_THE_PERIOD, False),
    ("net income", SubtotalKind.PROFIT_FOR_THE_PERIOD, False),
    ("profit (loss) from continuing operations", SubtotalKind.PROFIT_FOR_THE_PERIOD, False),
)

#: Sheet or file names suggesting an income statement.
SHEET_HINTS: tuple[str, ...] = (
    "손익계산서",
    "포괄손익",
    "손익",
    "income statement",
    "profit or loss",
)

#: Captions that reliably appear in an income statement, used to find it when
#: the sheet name is unhelpful.
STATEMENT_ANCHORS: tuple[str, ...] = (
    "매출액",
    "수익(매출액)",
    "영업수익",
    "revenue",
)

_SCALE_UNITS: tuple[tuple[str, int], ...] = (
    ("십억원", 9),
    ("백만원", 6),
    ("억원", 8),
    ("천원", 3),
    ("원", 0),
)
_UNIT_PATTERN = re.compile(r"단위\s*[:：]?\s*([^)\]]*)")

#: A cell repeating a column heading, never a line item.
NON_ITEM_PATTERN = re.compile(r"^(과목|계정과목|항목|구\s*분|account|description)$", re.I)
#: Column headers that name a note-reference column rather than a figure.
NOTE_HEADER_PATTERN = re.compile(r"^(주석|주기|참조|비고|註釋|note[s]?|ref(erence)?)$", re.I)

#: A note reference is a small positive integer. Real figures in a Korean
#: income statement are not bounded like this, but the bound alone decides
#: nothing — see :func:`_note_columns`.
MAX_NOTE_REFERENCE = 999


class StatementNotFoundError(ValueError):
    """The source does not contain anything resembling an income statement."""


def normalise(label: object) -> str:
    return re.sub(r"\s+", "", str(label or ""))


def classify_subtotal(label: str) -> SubtotalKind | None:
    """Return the subtotal a caption denotes, or ``None`` for a detail line.

    Uses the same canonical form as account matching, so an enumerated caption
    such as ``Ⅲ. 매출총이익`` or ``Ⅵ. 법인세비용차감전순이익`` is recognised. Doing
    only whitespace removal here was a real defect: enumerated subtotals were
    treated as ordinary lines, which both broke reconciliation (their amounts
    were added to the running total they were meant to verify) and exposed them
    to fuzzy account matching, where 영업이익 matched 영업외이익 — opposite
    concepts one character apart.
    """
    compact = normalize_label(label)
    if not compact:
        return None
    for caption, kind, allow_prefix in SUBTOTAL_CAPTIONS:
        canonical = normalize_label(caption)
        if compact == canonical or (allow_prefix and compact.startswith(canonical)):
            return kind
    return None


# ---------------------------------------------------------------------------
# The grid
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GridCell:
    """One cell, with whatever positional detail the source format offers."""

    value: object
    #: 1-based, matching how a user refers to rows in a spreadsheet or editor.
    row: int
    column: int
    #: Spreadsheet-style column label where the format has one ("C"), else None.
    column_label: str | None = None
    #: Indentation level, where the format records it.
    indent: int = 0

    @property
    def text(self) -> str:
        return str(self.value).strip() if self.value is not None else ""


@dataclass(frozen=True, slots=True)
class Grid:
    """A rectangle of cells produced by an ingest adapter."""

    source_file: str
    rows: tuple[tuple[GridCell, ...], ...]
    sheet: str | None = None
    #: Rows of preamble text scanned for the presentation unit.
    preamble: tuple[str, ...] = field(default=())
    #: Set by formats that have pages rather than sheets, so a figure extracted
    #: from a PDF still points at where it was printed (spec §18).
    page: int | None = None

    def cell(self, row: int, column: int) -> GridCell | None:
        for grid_row in self.rows:
            for grid_cell in grid_row:
                if grid_cell.row == row and grid_cell.column == column:
                    return grid_cell
        return None


@dataclass(frozen=True, slots=True)
class ExtractOptions:
    """Caller hints. Every field is optional; all are detected otherwise."""

    sheet: str | None = None
    header_row: int | None = None
    label_column: int | None = None
    amount_column: int | None = None
    note_column: int | None = None
    #: Which period column to read when several are present. 0 is the first,
    #: normally the current period.
    period_index: int = 0


# ---------------------------------------------------------------------------
# Shared extraction
# ---------------------------------------------------------------------------


def detect_scale(texts: tuple[str, ...]) -> int:
    """Read ``(단위: 백만원)`` into a power of ten. Defaults to 0 (원)."""
    for text in texts:
        match = _UNIT_PATTERN.search(text)
        if not match:
            continue
        unit_text = match.group(1)
        for unit, power in _SCALE_UNITS:
            if unit in unit_text:
                return power
    return 0


def parse_cell(value: object) -> ParsedAmount:
    """Read one amount cell.

    Text carries the printed conventions (parentheses, triangle marks) and goes
    to ``parse_amount``. A value that arrived already typed came from a
    spreadsheet, which stores numbers as doubles, so it goes through the one
    named conversion out of binary floating point.
    """
    if isinstance(value, str):
        return parse_amount(value)
    return ParsedAmount(decimal_from_spreadsheet_value(value), SignNormalization.AS_IS)


@dataclass(frozen=True, slots=True)
class Layout:
    """Where the statement's parts sit in the grid."""

    label_column: int
    first_row: int
    amount_columns: tuple[int, ...]
    #: The note-reference column, when the grid has one. Korean filings print
    #: it as a bare number, so it has to be identified rather than recognised
    #: by looking un-numeric.
    note_column: int | None = None


def _note_columns(
    grid: Grid, *, label_column: int, first_row: int, candidates: list[int]
) -> set[int]:
    """Which candidate columns hold note references rather than figures.

    Korean filings print the note column as a bare number — `29`, not
    `주석 29` — so it reads as a figure and, being to the left of the amounts,
    gets picked as the current period. Everything downstream then agrees: the
    figures are note numbers, and every row without a note (which is every
    subtotal) vanishes for having no amount. The statement's own arithmetic
    catches it, so nothing wrong is ever shown — but it is caught as "this file
    is unreadable" rather than read correctly, so it has to be settled here.

    A header saying so is decisive. Failing that, the deciding signal is
    **sparsity**: a note column is blank on most rows and on every subtotal,
    while an amount column carries a figure on essentially every line. The
    small-integer bound only narrows what sparsity is allowed to disqualify —
    on its own it would throw away a genuine statement presented in 십억원.
    """
    if len(candidates) < 2:
        # Nothing to choose between. A statement with one numeric column is
        # read as that column, note or not, and the reconciliation decides.
        return set()

    populated: dict[int, int] = dict.fromkeys(candidates, 0)
    small_integers: dict[int, bool] = dict.fromkeys(candidates, True)
    rows = 0

    for grid_row in grid.rows:
        if not grid_row or grid_row[0].row < first_row:
            continue
        cells = {cell.column: cell for cell in grid_row}
        label = cells.get(label_column)
        if not label or not label.text or NON_ITEM_PATTERN.match(normalise(label.text)):
            continue
        rows += 1
        for column in candidates:
            cell = cells.get(column)
            if cell is None or cell.value is None or cell.text == "":
                continue
            populated[column] += 1
            try:
                value = parse_cell(cell.value).value
            except AmountParseError:
                small_integers[column] = False
                continue
            if value != value.to_integral_value() or not (0 < value <= MAX_NOTE_REFERENCE):
                small_integers[column] = False

    if rows == 0:
        return set()

    densest = max(populated.values())
    notes: set[int] = set()
    for column in candidates:
        header = _header_above(grid, column=column, first_row=first_row)
        if header and NOTE_HEADER_PATTERN.match(normalise(header)):
            notes.add(column)
            continue
        # Sparser than the fullest column, and never anything but a small
        # positive integer. Both, or it stays a candidate figure.
        if small_integers[column] and populated[column] * 10 < densest * 9:
            notes.add(column)

    # Never disqualify everything: if the survey rules out every column, the
    # survey is wrong, and reading the statement beats refusing it.
    return notes if len(notes) < len(candidates) else set()


def _header_above(grid: Grid, *, column: int, first_row: int) -> str | None:
    """The nearest non-empty text sitting over a column, within a few rows."""
    best: tuple[int, str] | None = None
    for grid_row in grid.rows:
        for cell in grid_row:
            if cell.column != column or cell.row >= first_row:
                continue
            if first_row - cell.row > 3 or not cell.text:
                continue
            if best is None or cell.row > best[0]:
                best = (cell.row, cell.text)
    return best[1] if best else None


def find_layout(grid: Grid) -> Layout:
    """Locate the label column, the first data row, and the amount columns.

    Detected from the first row pairing a caption with at least one figure,
    rather than from a header row: heading captions vary between filings far
    more than the shape of the data does. The columns that row offers are then
    surveyed across the whole grid, because one row cannot tell a note
    reference from a figure — see :func:`_note_columns`.
    """
    for grid_row in grid.rows:
        label_column: int | None = None
        candidates: list[int] = []
        for grid_cell in grid_row:
            if grid_cell.value is None or grid_cell.text == "":
                continue
            if label_column is None and isinstance(grid_cell.value, str):
                if NON_ITEM_PATTERN.match(normalise(grid_cell.value)):
                    break  # a header row, not a data row
                if not looks_like_amount(grid_cell.value):
                    label_column = grid_cell.column
                    continue
            if label_column is not None and looks_like_amount(grid_cell.value):
                candidates.append(grid_cell.column)

        if label_column is None or not candidates:
            continue

        first_row = grid_row[0].row
        notes = _note_columns(
            grid, label_column=label_column, first_row=first_row, candidates=candidates
        )
        amounts = tuple(column for column in candidates if column not in notes)
        if not amounts:
            continue
        return Layout(
            label_column=label_column,
            first_row=first_row,
            amount_columns=amounts,
            # The leftmost, when a filing prints more than one reference column.
            note_column=min(notes) if notes else None,
        )

    raise StatementNotFoundError("could not locate a label column paired with figures")


def extract_statement(grid: Grid, options: ExtractOptions | None = None) -> ExtractedStatement:
    """Turn a grid into an :class:`ExtractedStatement`.

    Figures are read exactly as printed, and converted to signed profit-or-loss
    effects where the cell says so. A sign the document never printed is **not**
    guessed here; that is
    :func:`~app.domain.extraction.infer_signs_from_subtotals`, which accepts a
    derived sign only when the statement's own subtotals then reconcile.
    """
    options = options or ExtractOptions()

    layout = find_layout(grid)
    label_column = layout.label_column
    first_row = layout.first_row
    amount_columns = list(layout.amount_columns)
    note_column = options.note_column if options.note_column is not None else layout.note_column
    if options.label_column is not None:
        label_column = options.label_column
    if options.header_row is not None:
        first_row = options.header_row + 1
    if options.amount_column is not None:
        amount_columns = [options.amount_column]

    if options.period_index >= len(amount_columns):
        raise StatementNotFoundError(
            f"period_index {options.period_index} requested but the statement has "
            f"{len(amount_columns)} amount column(s)"
        )
    amount_column = amount_columns[options.period_index]

    by_row: dict[int, dict[int, GridCell]] = {}
    for grid_row in grid.rows:
        for grid_cell in grid_row:
            by_row.setdefault(grid_cell.row, {})[grid_cell.column] = grid_cell

    lines: list[ExtractedLine] = []
    ordinal = 0
    for row_number in sorted(by_row):
        if row_number < first_row:
            continue
        cells = by_row[row_number]

        label_cell = cells.get(label_column)
        raw_label = label_cell.text if label_cell else ""
        if not raw_label or NON_ITEM_PATTERN.match(normalise(raw_label)):
            continue

        amount_cell = cells.get(amount_column)
        if amount_cell is None or amount_cell.value is None or amount_cell.text == "":
            continue
        try:
            parsed = parse_cell(amount_cell.value)
        except AmountParseError:
            # A stray text cell in the amount column is not a line item.
            continue

        note: str | None = None
        if note_column is not None:
            note_cell = cells.get(note_column)
            note = note_cell.text if note_cell else None
        else:
            # No column was identified as holding references, so the only
            # notes to find are ones printed as text beside the caption.
            for column in range(label_column + 1, amount_column):
                candidate = cells.get(column)
                if candidate and candidate.text and not looks_like_amount(candidate.value):
                    note = candidate.text
                    break

        subtotal_kind = classify_subtotal(raw_label)
        lines.append(
            ExtractedLine(
                ordinal=ordinal,
                raw_label=raw_label,
                raw_value=str(amount_cell.value),
                amount=parsed.value,
                sign_normalization=parsed.sign_normalization,
                depth=label_cell.indent if label_cell else 0,
                is_subtotal=subtotal_kind is not None,
                subtotal_kind=subtotal_kind,
                is_nil=parsed.is_nil,
                note_references=(note,) if note else (),
                locator=SourceLocator(
                    source_file=grid.source_file,
                    sheet=grid.sheet,
                    row=row_number,
                    column=amount_cell.column_label,
                    cell=(
                        f"{amount_cell.column_label}{row_number}"
                        if amount_cell.column_label
                        else None
                    ),
                    page=grid.page,
                ),
            )
        )
        ordinal += 1

    header_cells = by_row.get(first_row - 1, {})
    period_cell = header_cells.get(amount_column)
    period_label = period_cell.text if period_cell and period_cell.text else None

    return ExtractedStatement(
        source_file=grid.source_file,
        sheet=grid.sheet,
        lines=tuple(lines),
        scale=detect_scale(grid.preamble),
        period_label=period_label,
    )


def total_of_details(statement: ExtractedStatement) -> Decimal:
    """Convenience for callers that only need the bottom line."""
    return sum((line.amount for line in statement.detail_lines), start=Decimal(0))
