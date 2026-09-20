"""Reading a statement shaped like a filing, not like our fixtures.

Every other fixture in this suite was written by us, and one of its accidents
hid a real defect for the whole project: our notes read `주석 21`, which cannot
be mistaken for a figure. Filings print the note as a bare `21`, and that one
difference made the reader take the note column for the current period — so
every amount became a note number, and every row without a note, which is every
subtotal, vanished for having no amount.

The extraction reconciliation caught it (§17), so nothing wrong would ever have
reached a screen. But "this file is unreadable" is the wrong answer to a file we
should read, and the gap only showed up when the question was asked about a real
company's statement rather than ours.

These tests are that question, kept.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.adapters.ingest import xlsx as xlsx_ingest
from app.adapters.ingest.grid import ExtractOptions, find_layout
from app.services.dry_run import dry_run
from tests.fixtures.korean_income_statement import (
    DART_ROWS,
    Row,
    build_dart_workbook,
)
from tests.fixtures.korean_pdf_statement import build_pdf


@pytest.fixture
def dart(tmp_path: Path) -> Path:
    return build_dart_workbook(tmp_path / "dart.xlsx")


def _read(path: Path, **kwargs: object) -> object:
    return xlsx_ingest.read_income_statement(path, options=ExtractOptions(**kwargs))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The defect itself
# ---------------------------------------------------------------------------


def test_a_numeric_note_column_is_not_read_as_the_amount(dart: Path) -> None:
    statement = xlsx_ingest.read_income_statement(dart, options=ExtractOptions())

    by_label = {line.raw_label: line.amount for line in statement.lines}
    assert by_label["Ⅰ. 수익(매출액)"] == Decimal("300870903000000")
    # The note numbers, which is what every amount used to be.
    assert 29 not in {int(amount) for amount in by_label.values()}


def test_rows_without_a_note_survive(dart: Path) -> None:
    """Which is every subtotal — and a statement with no subtotals cannot have
    its own arithmetic checked, so this is what made the file unreadable."""
    statement = xlsx_ingest.read_income_statement(dart, options=ExtractOptions())

    assert len(statement.lines) == len(DART_ROWS)
    subtotals = [line for line in statement.lines if line.is_subtotal]
    assert len(subtotals) == 4
    assert {line.raw_label for line in subtotals} == {
        "Ⅲ. 매출총이익",
        "Ⅳ. 영업이익",
        "Ⅴ. 법인세비용차감전순이익",
        "Ⅵ. 당기순이익",
    }


def test_bare_numeric_notes_are_captured_as_references(dart: Path) -> None:
    """The same root cause, in the other direction: notes printed as numbers
    were not collected either, so note-based decomposition of an aggregate
    caption (B65, B72) would have had nothing to work from."""
    statement = xlsx_ingest.read_income_statement(dart, options=ExtractOptions())

    notes = {line.raw_label: line.note_references for line in statement.lines}
    assert notes["Ⅰ. 수익(매출액)"] == ("29",)
    assert notes["지분법이익"] == ("12",)
    # A subtotal has no note, and inventing one would be a fabricated citation.
    assert notes["Ⅳ. 영업이익"] == ()


def test_the_statement_reconciles_end_to_end(dart: Path) -> None:
    """The check that decides everything: read correctly, a filing reproduces
    its own subtotals exactly (§17)."""
    run = dry_run(dart)

    assert run.extraction_passed
    assert [check.computed - check.reported for check in run.extraction.checks] == [Decimal(0)] * 5


def test_subtotals_are_found_from_captions_alone(dart: Path) -> None:
    """No bold, no cell indent — a real download carries neither, so the
    caption is the only thing left to recognise a subtotal by."""
    run = dry_run(dart)

    assert run.subtotal_lines == 4
    assert run.detail_lines == 9


def test_roman_numeral_prefixes_do_not_defeat_the_dictionary(dart: Path) -> None:
    """`Ⅲ. 매출총이익` is how filings enumerate captions."""
    run = dry_run(dart)

    assert run.normalization.coverage == Decimal(1)
    assert run.unresolved_captions == ()


# ---------------------------------------------------------------------------
# Scale: won, with no presentation unit
# ---------------------------------------------------------------------------


def test_won_scale_figures_survive_exactly(dart: Path) -> None:
    """A filing reported in 원 prints 15-digit integers. Spreadsheets store
    numbers as doubles, so this is the range where a reader that goes through
    binary floating point starts returning figures nobody wrote."""
    statement = xlsx_ingest.read_income_statement(dart, options=ExtractOptions())

    revenue = next(line for line in statement.lines if "매출액" in line.raw_label)
    assert revenue.amount == Decimal("300870903000000")
    assert len(str(abs(revenue.amount))) == 15
    assert all(line.amount == line.amount.to_integral_value() for line in statement.lines)


def test_the_exactness_ceiling_is_the_spreadsheets_not_ours(tmp_path: Path) -> None:
    """Documenting where this stops working, and whose limit it is.

    A double holds integers exactly to 2^53. Above that the spreadsheet itself
    has already lost the value before any reader sees the file — there is
    nothing to fix here, and it is far above any Korean company's income
    statement line (2^53 원 is roughly 9,000조).
    """
    path = tmp_path / "ceiling.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet["A1"] = 2**53
    sheet["A2"] = 2**53 + 1
    workbook.save(path)

    from openpyxl import load_workbook

    reread = load_workbook(path).active
    assert reread is not None
    assert reread["A1"].value == 2**53
    assert reread["A2"].value == 2**53  # the odd value is gone, in the file
    # Two orders of magnitude of headroom over the largest line in a Korean
    # income statement, so the limit is theoretical.
    assert 2**53 > 300_870_903_000_000 * 20


# ---------------------------------------------------------------------------
# Not over-correcting
# ---------------------------------------------------------------------------


def test_a_statement_with_one_numeric_column_is_still_read(tmp_path: Path) -> None:
    """With nothing to choose between, the single column is the amount —
    note-like or not. Refusing would turn a fix into a new failure."""
    path = build_dart_workbook(tmp_path / "one.xlsx", periods=1, note_header=None)
    statement = xlsx_ingest.read_income_statement(path, options=ExtractOptions())

    assert len(statement.lines) == len(DART_ROWS)
    assert statement.lines[0].amount == Decimal("300870903000000")


def test_small_genuine_figures_are_not_mistaken_for_notes(tmp_path: Path) -> None:
    """A statement presented in 십억원 has figures in the same range as note
    numbers. Magnitude alone must not disqualify a column — a note column is
    identified by being *sparse*, because subtotals never carry one."""
    rows = tuple(
        Row(
            row.label,
            (row.amount / Decimal(10**12)).quantize(Decimal("1")),
            note=row.note,
            depth=row.depth,
            is_subtotal=row.is_subtotal,
            subtotal_kind=row.subtotal_kind,
        )
        for row in DART_ROWS
    )
    path = build_dart_workbook(tmp_path / "billions.xlsx", rows=rows, note_header=None)

    statement = xlsx_ingest.read_income_statement(path, options=ExtractOptions())

    assert len(statement.lines) == len(rows)
    assert statement.lines[0].amount == Decimal("301")
    assert sum(1 for line in statement.lines if line.is_subtotal) == 4


def test_a_note_header_is_believed_even_where_structure_is_ambiguous(
    tmp_path: Path,
) -> None:
    """`주석` over a column settles it outright."""
    from openpyxl import load_workbook

    path = build_dart_workbook(tmp_path / "hdr.xlsx")
    sheet = xlsx_ingest.find_sheet(load_workbook(path, data_only=True), "연결 포괄손익계산서")
    layout = find_layout(xlsx_ingest.worksheet_to_grid(sheet, path.name))

    assert layout.note_column == 2
    assert 2 not in layout.amount_columns


def test_the_comparative_periods_are_still_reachable(dart: Path) -> None:
    """Three period columns, and the reader must not have collapsed them while
    discarding the note column."""
    current = dry_run(dart, options=ExtractOptions(period_index=0))
    prior = dry_run(dart, options=ExtractOptions(period_index=2))

    assert current.source.lines[0].amount == Decimal("300870903000000")
    assert prior.source.lines[0].amount < current.source.lines[0].amount
    assert prior.extraction_passed


def test_all_three_formats_read_a_filing_shaped_statement_identically(
    tmp_path: Path,
) -> None:
    """XLSX, CSV and PDF share one grid, so they shared the defect. They have
    to share the fix — and the format must stay transport only, even at won
    scale with a numeric note column."""
    csv_path = tmp_path / "dart.csv"
    header = "과목,주석,제 56 기,제 55 기"
    body = "\n".join(
        f"{row.label},{row.note or ''},{int(row.amount)},{int(row.amount * 9 // 10)}"
        for row in DART_ROWS
    )
    csv_path.write_text(f"﻿{header}\n{body}\n", encoding="utf-8")

    runs = [
        dry_run(build_dart_workbook(tmp_path / "dart.xlsx")),
        dry_run(csv_path),
        dry_run(build_pdf(tmp_path / "dart.pdf", rows=DART_ROWS)),
    ]

    assert {run.extraction_passed for run in runs} == {True}
    assert {run.subtotal_lines for run in runs} == {4}
    assert {run.detail_lines for run in runs} == {9}
    assert len({tuple(line.amount for line in run.source.lines) for run in runs}) == 1
    assert len({run.normalization.coverage for run in runs}) == 1
