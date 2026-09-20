"""Reading an income statement out of a PDF (Phase 3c).

The risk a PDF carries that a spreadsheet does not is that it has no cells: a
page is ink at positions, and every column a reader sees is inferred. So these
tests are about the inference — that a figure lands in the right column even
when a row has no note, that the statement is found among other statements,
and that a scan is refused rather than guessed at.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.ingest.grid import ExtractOptions, StatementNotFoundError
from app.adapters.ingest.pdf import read_income_statement
from app.domain.enums import SubtotalKind
from app.domain.extraction import reconcile_extraction
from tests.fixtures.korean_income_statement import STATEMENT_ROWS, SignStyle
from tests.fixtures.korean_pdf_statement import build_notes_pdf, build_pdf


@pytest.fixture
def statement_pdf(tmp_path: Path) -> Path:
    return build_pdf(tmp_path / "fs.pdf")


# ---------------------------------------------------------------------------
# Reading the table
# ---------------------------------------------------------------------------


def test_every_line_is_read_including_the_subtotals(statement_pdf: Path) -> None:
    """The failure this guards against is specific and silent.

    A subtotal line has no note, so a reader that assigns columns by counting
    cells left to right would put its figure in the note's column and drop the
    line. The statement would then appear to have no subtotals at all — and
    with nothing to reconcile against, a misread would pass unnoticed.
    """
    statement = read_income_statement(statement_pdf)

    assert len(statement.lines) == len(STATEMENT_ROWS)
    assert [line.raw_label for line in statement.lines] == [row.label for row in STATEMENT_ROWS]


def test_the_figures_match_the_document(statement_pdf: Path) -> None:
    statement = read_income_statement(statement_pdf)

    by_label = {line.raw_label: line.amount for line in statement.lines}
    assert by_label["매출액"] == Decimal("1000000")
    assert by_label["매출원가"] == Decimal("-700000")
    assert by_label["당기순이익"] == Decimal("95160")


def test_the_document_reconciles_against_its_own_subtotals(statement_pdf: Path) -> None:
    """Spec §17 — and the only real defence against a misread column."""
    report = reconcile_extraction(read_income_statement(statement_pdf))

    assert report.passed
    assert report.blockers == ()
    assert {check.check for check in report.checks} >= {
        "GROSS_PROFIT",
        "REPORTED_OPERATING_PROFIT",
        "PROFIT_BEFORE_TAX",
    }


def test_subtotals_are_recognised_by_their_captions(statement_pdf: Path) -> None:
    statement = read_income_statement(statement_pdf)

    kinds = {line.raw_label: line.subtotal_kind for line in statement.lines if line.is_subtotal}
    assert kinds["영업이익"] == SubtotalKind.REPORTED_OPERATING_PROFIT
    assert kinds["법인세차감전순이익"] == SubtotalKind.PROFIT_BEFORE_TAX


def test_the_presentation_unit_is_read_from_the_page(statement_pdf: Path) -> None:
    """A statement headed (단위: 백만원) is stated in millions, and reading it
    as 원 would be wrong by six orders of magnitude in every figure."""
    assert read_income_statement(statement_pdf).scale == 6


def test_note_references_survive(statement_pdf: Path) -> None:
    statement = read_income_statement(statement_pdf)

    revenue = next(line for line in statement.lines if line.raw_label == "매출액")
    assert revenue.note_references == ("주석 21",)


def test_indentation_becomes_depth(statement_pdf: Path) -> None:
    """Sub-items of an aggregate are indented, and that is how they are found."""
    statement = read_income_statement(statement_pdf)

    by_label = {line.raw_label: line.depth for line in statement.lines}
    assert by_label["매출액"] == 0
    assert by_label["기타수익"] > 0


# ---------------------------------------------------------------------------
# Provenance (spec §18)
# ---------------------------------------------------------------------------


def test_every_figure_points_at_the_page_it_came_from(statement_pdf: Path) -> None:
    statement = read_income_statement(statement_pdf)

    assert all(line.locator.page == 2 for line in statement.lines)
    assert all(line.locator.source_file == "fs.pdf" for line in statement.lines)
    assert statement.sheet == "p.2"


def test_the_statement_is_found_past_a_cover_page(tmp_path: Path) -> None:
    with_cover = read_income_statement(build_pdf(tmp_path / "cover.pdf", cover_page=True))
    without = read_income_statement(build_pdf(tmp_path / "plain.pdf", cover_page=False))

    assert with_cover.sheet == "p.2"
    assert without.sheet == "p.1"
    assert [line.amount for line in with_cover.lines] == [line.amount for line in without.lines]


def test_the_statement_is_found_among_other_statements(tmp_path: Path) -> None:
    """A filing carries the balance sheet in the same file, and picking the
    wrong page is the most likely way to read the wrong numbers."""
    statement = read_income_statement(build_notes_pdf(tmp_path / "filing.pdf"))

    assert statement.sheet == "p.2"
    by_label = {line.raw_label: line.amount for line in statement.lines}
    assert "매출액" in by_label
    assert "자산총계" not in by_label


def test_a_page_can_be_named_explicitly(tmp_path: Path) -> None:
    statement = read_income_statement(
        build_pdf(tmp_path / "fs.pdf"), options=ExtractOptions(sheet="p.2")
    )

    assert statement.sheet == "p.2"


# ---------------------------------------------------------------------------
# What the reader refuses
# ---------------------------------------------------------------------------


def test_a_scan_is_refused_rather_than_guessed_at(tmp_path: Path) -> None:
    """OCR is out of scope (§33) because its failures are silent: a misread
    digit looks exactly like a correct one."""
    scanned = build_pdf(tmp_path / "scan.pdf", scanned=True)

    with pytest.raises(StatementNotFoundError, match="scan"):
        read_income_statement(scanned)


def test_a_pdf_without_an_income_statement_is_refused(tmp_path: Path) -> None:
    from reportlab.pdfgen import canvas

    from tests.fixtures.korean_pdf_statement import FONT, _register

    _register()
    path = tmp_path / "unrelated.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.setFont(FONT, 12)
    pdf.drawString(60, 700, "감사보고서")
    pdf.drawString(60, 680, "우리는 위 재무제표를 감사하였습니다.")
    pdf.save()

    with pytest.raises(StatementNotFoundError):
        read_income_statement(path)


# ---------------------------------------------------------------------------
# The same statement, read three ways
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("style", [SignStyle.PARENTHESES, SignStyle.TRIANGLE])
def test_both_printed_sign_conventions_read_to_the_same_figures(
    tmp_path: Path, style: SignStyle
) -> None:
    """(70,000) and △70,000 are the same number printed two ways."""
    statement = read_income_statement(build_pdf(tmp_path / f"{style}.pdf", style=style))

    by_label = {line.raw_label: line.amount for line in statement.lines}
    assert by_label["매출원가"] == Decimal("-700000")
    assert reconcile_extraction(statement).passed


def test_a_pdf_and_a_workbook_of_the_same_statement_agree(tmp_path: Path) -> None:
    """Three formats, one pipeline: the adapter is the only thing that differs."""
    from app.adapters.ingest.xlsx import read_income_statement as read_xlsx
    from tests.fixtures.korean_income_statement import build_workbook

    from_pdf = read_income_statement(build_pdf(tmp_path / "fs.pdf"))
    from_xlsx = read_xlsx(build_workbook(tmp_path / "fs.xlsx"))

    assert [(line.raw_label, line.amount) for line in from_pdf.lines] == [
        (line.raw_label, line.amount) for line in from_xlsx.lines
    ]
    assert from_pdf.scale == from_xlsx.scale
