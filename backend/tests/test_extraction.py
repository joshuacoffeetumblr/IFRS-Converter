"""Reconciliation and sign inference. Pure — constructed statements, no files."""

from __future__ import annotations

from decimal import Decimal

from app.domain.enums import SignNormalization, SubtotalKind
from app.domain.extraction import (
    ExtractedLine,
    ExtractedStatement,
    SourceLocator,
    infer_signs_from_subtotals,
    reconcile_extraction,
)

LOCATOR = SourceLocator(source_file="test.xlsx", sheet="손익계산서")


def line(
    ordinal: int,
    label: str,
    amount: str,
    *,
    subtotal: SubtotalKind | None = None,
) -> ExtractedLine:
    return ExtractedLine(
        ordinal=ordinal,
        raw_label=label,
        raw_value=amount,
        amount=Decimal(amount),
        sign_normalization=SignNormalization.AS_IS,
        locator=LOCATOR,
        is_subtotal=subtotal is not None,
        subtotal_kind=subtotal,
    )


def statement(*lines: ExtractedLine) -> ExtractedStatement:
    return ExtractedStatement(source_file="test.xlsx", lines=lines)


def consistent() -> ExtractedStatement:
    """A statement whose own arithmetic holds."""
    return statement(
        line(0, "매출액", "1000"),
        line(1, "매출원가", "-700"),
        line(2, "매출총이익", "300", subtotal=SubtotalKind.GROSS_PROFIT),
        line(3, "판매비와관리비", "-180"),
        line(4, "영업이익", "120", subtotal=SubtotalKind.REPORTED_OPERATING_PROFIT),
        line(5, "금융수익", "30"),
        line(6, "금융비용", "-20"),
        line(7, "법인세차감전순이익", "130", subtotal=SubtotalKind.PROFIT_BEFORE_TAX),
        line(8, "법인세비용", "-30"),
        line(9, "당기순이익", "100", subtotal=SubtotalKind.PROFIT_FOR_THE_PERIOD),
    )


def test_consistent_statement_reconciles() -> None:
    report = reconcile_extraction(consistent())

    assert report.passed
    assert not report.failures
    assert [c.check for c in report.checks] == [
        "GROSS_PROFIT",
        "REPORTED_OPERATING_PROFIT",
        "PROFIT_BEFORE_TAX",
        "PROFIT_FOR_THE_PERIOD",
        "TOTAL_OF_ALL_DETAIL_LINES",
    ]


def test_subtotals_are_excluded_from_their_own_sums() -> None:
    """Test vector T5: including a subtotal would double count everything above."""
    report = reconcile_extraction(consistent())
    gross = next(c for c in report.checks if c.check == "GROSS_PROFIT")

    assert gross.computed == Decimal("300")


def test_misread_line_fails_only_its_own_section() -> None:
    """Resetting the running total to the reported figure localises the defect.

    Without the reset, one bad line makes every later subtotal fail too and the
    user cannot see where the problem is.
    """
    broken = statement(
        line(0, "매출액", "1000"),
        line(1, "매출원가", "-600"),  # misread: should be -700
        line(2, "매출총이익", "300", subtotal=SubtotalKind.GROSS_PROFIT),
        line(3, "판매비와관리비", "-180"),
        line(4, "영업이익", "120", subtotal=SubtotalKind.REPORTED_OPERATING_PROFIT),
    )

    report = reconcile_extraction(broken)
    failed = {c.check for c in report.failures}

    assert "GROSS_PROFIT" in failed
    assert "REPORTED_OPERATING_PROFIT" not in failed, "failure must not cascade"


def test_dropped_trailing_line_is_caught_by_the_total_check() -> None:
    """A line after the last subtotal escapes the sectional checks."""
    report = reconcile_extraction(
        statement(
            line(0, "매출액", "1000"),
            line(1, "매출원가", "-700"),
            line(2, "매출총이익", "300", subtotal=SubtotalKind.GROSS_PROFIT),
            # 법인세비용 was dropped entirely by the parser.
            line(3, "당기순이익", "270", subtotal=SubtotalKind.PROFIT_FOR_THE_PERIOD),
        )
    )

    assert not report.passed
    assert any(c.check == "TOTAL_OF_ALL_DETAIL_LINES" for c in report.failures)


def test_tolerance_absorbs_rounding_but_reports_the_delta() -> None:
    """Test vector T8: a rounded source may differ by a presentation unit."""
    rounded = statement(
        line(0, "매출액", "1000"),
        line(1, "매출원가", "-700"),
        line(2, "매출총이익", "301", subtotal=SubtotalKind.GROSS_PROFIT),
        line(3, "당기순이익", "300", subtotal=SubtotalKind.PROFIT_FOR_THE_PERIOD),
    )

    strict = reconcile_extraction(rounded)
    lenient = reconcile_extraction(rounded, tolerance=Decimal("1"))

    assert not strict.passed
    assert lenient.passed
    # The difference is still reported, never silently absorbed.
    gross = next(c for c in lenient.checks if c.check == "GROSS_PROFIT")
    assert gross.delta == Decimal("-1")


def test_statement_without_subtotals_cannot_be_verified() -> None:
    """Reporting "passed" here would be a lie: nothing was checked."""
    report = reconcile_extraction(statement(line(0, "매출액", "1000")))

    assert not report.passed
    assert report.blockers


def test_empty_statement_is_blocked() -> None:
    report = reconcile_extraction(statement())

    assert not report.passed
    assert report.blockers


# ---------------------------------------------------------------------------
# Sign inference
# ---------------------------------------------------------------------------


def unsigned() -> ExtractedStatement:
    """The same statement with every figure printed positive."""
    return statement(
        line(0, "매출액", "1000"),
        line(1, "매출원가", "700"),
        line(2, "매출총이익", "300", subtotal=SubtotalKind.GROSS_PROFIT),
        line(3, "판매비와관리비", "180"),
        line(4, "영업이익", "120", subtotal=SubtotalKind.REPORTED_OPERATING_PROFIT),
        line(5, "금융수익", "30"),
        line(6, "금융비용", "20"),
        line(7, "법인세차감전순이익", "130", subtotal=SubtotalKind.PROFIT_BEFORE_TAX),
        line(8, "법인세비용", "30"),
        line(9, "당기순이익", "100", subtotal=SubtotalKind.PROFIT_FOR_THE_PERIOD),
    )


def test_unsigned_statement_is_resolved_and_marked() -> None:
    assert not reconcile_extraction(unsigned()).passed

    adjusted, inferred = infer_signs_from_subtotals(unsigned())

    assert inferred
    assert reconcile_extraction(adjusted).passed
    flipped = {
        ln.raw_label
        for ln in adjusted.lines
        if ln.sign_normalization is SignNormalization.INFERRED_FROM_SUBTOTAL
    }
    assert flipped == {"매출원가", "판매비와관리비", "금융비용", "법인세비용"}


def test_inference_is_skipped_when_already_consistent() -> None:
    """A statement that reconciles must never be altered."""
    adjusted, inferred = infer_signs_from_subtotals(consistent())

    assert not inferred
    assert adjusted is consistent() or adjusted.lines == consistent().lines


def test_inference_is_skipped_when_some_figure_was_printed_negative() -> None:
    """Mixed signs mean the document does print signs, so the hypothesis is wrong."""
    mixed = statement(
        line(0, "매출액", "1000"),
        line(1, "매출원가", "700"),
        line(2, "금융비용", "-20"),
        line(3, "매출총이익", "300", subtotal=SubtotalKind.GROSS_PROFIT),
    )

    _, inferred = infer_signs_from_subtotals(mixed)

    assert not inferred


def test_inference_refuses_when_it_does_not_explain_the_statement() -> None:
    """The hypothesis is accepted only if reconciliation then passes.

    This is what stops the function manufacturing a plausible-looking result:
    it tests one hypothesis, it does not search for a combination that fits.
    """
    unexplainable = statement(
        line(0, "매출액", "1000"),
        line(1, "매출원가", "700"),
        line(2, "매출총이익", "999", subtotal=SubtotalKind.GROSS_PROFIT),
        line(3, "당기순이익", "999", subtotal=SubtotalKind.PROFIT_FOR_THE_PERIOD),
    )

    adjusted, inferred = infer_signs_from_subtotals(unexplainable)

    assert not inferred
    # The original is returned, so the reported failure is the real one.
    assert adjusted.lines == unexplainable.lines
    assert not reconcile_extraction(adjusted).passed
