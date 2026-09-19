"""CSV ingest, with the encoding problem that Korean exports actually present."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.ingest.csv import (
    EncodingDetectionError,
    decode_bytes,
    read_income_statement,
    sniff_delimiter,
)
from app.adapters.ingest.grid import ExtractOptions, StatementNotFoundError
from app.domain.enums import SignNormalization
from app.domain.extraction import infer_signs_from_subtotals, reconcile_extraction
from tests.fixtures.korean_income_statement import (
    STATEMENT_ROWS,
    SignStyle,
    build_csv,
    expected_amounts,
)

# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp949", "euc-kr"])
def test_korean_text_survives_every_supported_encoding(tmp_path: Path, encoding: str) -> None:
    path = build_csv(tmp_path / "fs.csv", encoding=encoding)

    statement = read_income_statement(path)

    labels = {line.raw_label for line in statement.lines}
    assert "매출액" in labels
    assert "법인세차감전순이익" in labels


def test_utf8_is_tried_before_cp949(tmp_path: Path) -> None:
    """Order matters and is not incidental.

    CP949 bytes fail a strict UTF-8 decode, so UTF-8 first is safe. The reverse
    is not: CP949 accepts most byte sequences without raising and would turn a
    UTF-8 file into mojibake silently.
    """
    path = build_csv(tmp_path / "fs.csv", encoding="utf-8")

    assert decode_bytes(path.read_bytes()).encoding.startswith("utf-8")


def test_cp949_file_is_detected_as_cp949(tmp_path: Path) -> None:
    path = build_csv(tmp_path / "fs.csv", encoding="cp949")

    decoded = decode_bytes(path.read_bytes())

    assert decoded.encoding in {"cp949", "euc-kr"}
    assert "매출액" in decoded.text


def test_excel_byte_order_mark_is_stripped(tmp_path: Path) -> None:
    """A leading BOM would otherwise become part of the first caption."""
    path = build_csv(tmp_path / "fs.csv", encoding="utf-8-sig")

    statement = read_income_statement(path)

    assert all(not line.raw_label.startswith("﻿") for line in statement.lines)


def test_explicit_encoding_overrides_detection(tmp_path: Path) -> None:
    path = build_csv(tmp_path / "fs.csv", encoding="cp949")

    statement = read_income_statement(path, encoding="cp949")

    assert "매출액" in {line.raw_label for line in statement.lines}


def test_wrong_explicit_encoding_is_reported(tmp_path: Path) -> None:
    path = build_csv(tmp_path / "fs.csv", encoding="cp949")

    with pytest.raises(EncodingDetectionError, match="not valid utf-8"):
        read_income_statement(path, encoding="utf-8")


def test_undecodable_bytes_raise(tmp_path: Path) -> None:
    path = tmp_path / "broken.csv"
    path.write_bytes(b"\xff\xfe\x00\x00\xff\xff\xfe\xfe")

    with pytest.raises(EncodingDetectionError):
        decode_bytes(path.read_bytes())


# ---------------------------------------------------------------------------
# Delimiters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_common_delimiters_are_detected(tmp_path: Path, delimiter: str) -> None:
    path = build_csv(tmp_path / "fs.csv", delimiter=delimiter)

    statement = read_income_statement(path)

    assert {line.raw_label: line.amount for line in statement.lines} == expected_amounts()


def test_sniff_prefers_a_consistent_delimiter() -> None:
    text = "제목\n과목;주석;금액\n매출액;주석 21;1,000\n매출원가;주석 22;(700)\n"

    assert sniff_delimiter(text) == ";"


def test_quoted_thousands_separators_do_not_split_fields(tmp_path: Path) -> None:
    """`"1,000,000"` is one field, not three."""
    path = build_csv(tmp_path / "fs.csv", delimiter=",")

    statement = read_income_statement(path)
    revenue = next(line for line in statement.lines if line.raw_label == "매출액")

    assert revenue.amount == Decimal("1000000")


# ---------------------------------------------------------------------------
# Extraction, shared with XLSX
# ---------------------------------------------------------------------------


def test_every_row_is_extracted(tmp_path: Path) -> None:
    path = build_csv(tmp_path / "fs.csv")

    statement = read_income_statement(path)

    assert {line.raw_label: line.amount for line in statement.lines} == expected_amounts()


def test_subtotals_are_flagged(tmp_path: Path) -> None:
    path = build_csv(tmp_path / "fs.csv")

    statement = read_income_statement(path)

    flagged = {line.raw_label for line in statement.lines if line.is_subtotal}
    assert flagged == {row.label for row in STATEMENT_ROWS if row.is_subtotal}


def test_presentation_scale_is_read_from_the_preamble(tmp_path: Path) -> None:
    path = build_csv(tmp_path / "fs.csv")

    assert read_income_statement(path).scale == 6


def test_provenance_records_row_and_column(tmp_path: Path) -> None:
    """CSV has no cells, but an auditor should still see one convention."""
    path = build_csv(tmp_path / "fs.csv")

    statement = read_income_statement(path)
    revenue = next(line for line in statement.lines if line.raw_label == "매출액")

    assert revenue.locator.source_file == "fs.csv"
    assert revenue.locator.row == 6
    assert revenue.locator.column == "C"
    assert revenue.locator.cell == "C6"


def test_leading_whitespace_becomes_depth(tmp_path: Path) -> None:
    path = build_csv(tmp_path / "fs.csv")

    statement = read_income_statement(path)
    by_label = {line.raw_label: line.depth for line in statement.lines}

    assert by_label["매출액"] == 0
    assert by_label["기타수익"] == 1


def test_note_references_are_captured(tmp_path: Path) -> None:
    path = build_csv(tmp_path / "fs.csv")

    statement = read_income_statement(path)
    equity = next(line for line in statement.lines if line.raw_label == "지분법이익")

    assert equity.note_references == ("주석 13",)


def test_comparative_column_is_read_separately(tmp_path: Path) -> None:
    path = build_csv(tmp_path / "fs.csv")

    current = read_income_statement(path)
    prior = read_income_statement(path, options=ExtractOptions(period_index=1))

    assert next(ln for ln in current.lines if ln.raw_label == "매출액").amount == Decimal("1000000")
    assert next(ln for ln in prior.lines if ln.raw_label == "매출액").amount == Decimal("900000")


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("style", [SignStyle.PARENTHESES, SignStyle.TRIANGLE])
def test_signed_statements_reconcile_as_read(tmp_path: Path, style: SignStyle) -> None:
    path = build_csv(tmp_path / "fs.csv", style=style)

    report = reconcile_extraction(read_income_statement(path))

    assert report.passed, [(c.check, str(c.delta)) for c in report.failures]


def test_unsigned_statement_reconciles_only_after_inference(tmp_path: Path) -> None:
    path = build_csv(tmp_path / "fs.csv", style=SignStyle.UNSIGNED)
    statement = read_income_statement(path)

    assert not reconcile_extraction(statement).passed

    adjusted, inferred = infer_signs_from_subtotals(statement)

    assert inferred
    assert reconcile_extraction(adjusted).passed
    cost = next(ln for ln in adjusted.lines if ln.raw_label == "매출원가")
    assert cost.sign_normalization is SignNormalization.INFERRED_FROM_SUBTOTAL


def test_csv_and_xlsx_produce_identical_figures(tmp_path: Path) -> None:
    """The format is transport. Two adapters must never disagree on a number."""
    from app.adapters.ingest import xlsx
    from tests.fixtures.korean_income_statement import build_workbook

    csv_statement = read_income_statement(build_csv(tmp_path / "fs.csv"))
    xlsx_statement = xlsx.read_income_statement(
        build_workbook(tmp_path / "fs.xlsx", style=SignStyle.PARENTHESES)
    )

    assert [(ln.raw_label, ln.amount) for ln in csv_statement.lines] == [
        (ln.raw_label, ln.amount) for ln in xlsx_statement.lines
    ]
    assert [ln.is_subtotal for ln in csv_statement.lines] == [
        ln.is_subtotal for ln in xlsx_statement.lines
    ]


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_empty_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")

    with pytest.raises(StatementNotFoundError, match="empty"):
        read_income_statement(path)


def test_file_without_figures_raises(tmp_path: Path) -> None:
    path = tmp_path / "notes.csv"
    path.write_text("메모\n이것은 재무제표가 아닙니다\n", encoding="utf-8")

    with pytest.raises(StatementNotFoundError):
        read_income_statement(path)
