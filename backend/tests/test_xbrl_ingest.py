"""Reading an XBRL filing (spec §3).

Every other reader guesses; this one is supposed not to. So these tests are
mostly about the guesses *not* happening: that the basis and the period come
from the project rather than from document order, that a sign comes from the
taxonomy rather than from a caption, and that a note breakdown is used only
when the filing's own arithmetic says it is the caption's breakdown.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.ingest.grid import ExtractOptions, StatementNotFoundError
from app.adapters.ingest.xbrl import MAX_ELEMENTS, read_income_statement
from app.domain.enums import SignNormalization, SubtotalKind
from app.domain.extraction import reconcile_extraction
from tests.fixtures.xbrl_filing import (
    CURRENT,
    FACE,
    NOTE_DIMENSIONS,
    PRIOR,
    QUARTER,
    Fact,
    Filing,
    build_xbrl,
)

CONSOLIDATED = "ifrs-full:ConsolidatedMember"
SEPARATE = "ifrs-full:SeparateMember"


def current(**kwargs: object) -> ExtractOptions:
    return ExtractOptions(period_start=CURRENT[0], period_end=CURRENT[1], **kwargs)  # type: ignore[arg-type]


def amounts(path: Path, options: ExtractOptions | None = None) -> dict[str, Decimal]:
    statement = read_income_statement(path, options=options)
    return {line.raw_label: line.amount for line in statement.lines}


# ---------------------------------------------------------------------------
# Choosing what to read
# ---------------------------------------------------------------------------


def test_reads_the_period_and_basis_that_were_asked_for(tmp_path: Path) -> None:
    path = build_xbrl(tmp_path / "filing.xbrl")

    statement = read_income_statement(path, options=current())

    assert statement.period_label == "2026-01-01 ~ 2026-06-30"
    assert statement.sheet is not None
    assert statement.sheet.startswith("ConsolidatedMember")


def test_separate_basis_is_a_different_statement(tmp_path: Path) -> None:
    """The two bases are both in the file and both complete.

    A reader that took whichever came first would return a real, internally
    consistent statement — for the wrong entity. Nothing downstream could tell.
    """
    filing = Filing()
    filing.add(CURRENT, CONSOLIDATED, FACE)
    filing.add(
        CURRENT,
        SEPARATE,
        tuple(Fact(fact.concept, fact.value * 2) for fact in FACE),
    )
    path = build_xbrl(tmp_path / "filing.xbrl", filing)

    consolidated = amounts(path, current(basis="CONSOLIDATED"))
    separate = amounts(path, current(basis="SEPARATE"))

    assert consolidated["수익(매출액)"] == Decimal(1000)
    assert separate["수익(매출액)"] == Decimal(2000)


def test_a_period_the_filing_does_not_report_is_an_error(tmp_path: Path) -> None:
    """Not a fallback. Reading 1H2026 when 1H2025 was asked for is silent
    nonsense; an error naming what *is* available is recoverable."""
    path = build_xbrl(tmp_path / "filing.xbrl")

    with pytest.raises(StatementNotFoundError) as caught:
        read_income_statement(
            path,
            options=ExtractOptions(
                period_start=dt.date(2024, 1, 1), period_end=dt.date(2024, 12, 31)
            ),
        )

    assert "2026-01-01 to 2026-06-30" in str(caught.value)


def test_a_basis_the_filing_does_not_report_is_an_error(tmp_path: Path) -> None:
    filing = Filing()
    filing.add(CURRENT, CONSOLIDATED, FACE)
    path = build_xbrl(tmp_path / "filing.xbrl", filing)

    with pytest.raises(StatementNotFoundError):
        read_income_statement(path, options=current(basis="SEPARATE"))


def test_without_a_period_the_longest_latest_span_wins(tmp_path: Path) -> None:
    """A half-year filing carries the quarter too. The cumulative figure is the
    statement; the quarter is a column beside it."""
    path = build_xbrl(tmp_path / "filing.xbrl")

    statement = read_income_statement(path, options=ExtractOptions(basis="CONSOLIDATED"))

    assert statement.period_label == f"{CURRENT[0]} ~ {CURRENT[1]}"
    assert QUARTER[0].isoformat() not in statement.period_label
    assert PRIOR[0].isoformat() not in statement.period_label


def test_a_segment_figure_is_not_the_company_figure(tmp_path: Path) -> None:
    """The same concept, the same period, the same basis — one extra dimension.

    Only the undimensioned fact is on the face of the statement.
    """
    path = build_xbrl(tmp_path / "filing.xbrl")

    assert amounts(path, current())["수익(매출액)"] == Decimal(1000)


# ---------------------------------------------------------------------------
# Signs, which come from the taxonomy
# ---------------------------------------------------------------------------


def test_expenses_are_negative_though_the_filing_prints_them_positive(tmp_path: Path) -> None:
    path = build_xbrl(tmp_path / "filing.xbrl")

    read = amounts(path, current())

    assert read["매출원가"] == Decimal(-600)
    assert read["법인세비용"] == Decimal(-70)
    assert read["수익(매출액)"] == Decimal(1000)


def test_a_taxonomy_sign_is_recorded_as_such(tmp_path: Path) -> None:
    """Provenance, not just the number (spec §8).

    `NEGATED` would mean "this product decided the caption was an expense".
    `TAXONOMY_SIGNED` means the filing said so, which is a different answer to
    give an auditor who asks where the minus came from.
    """
    path = build_xbrl(tmp_path / "filing.xbrl")

    statement = read_income_statement(path, options=current())
    by_label = {line.raw_label: line for line in statement.lines}

    assert by_label["매출원가"].sign_normalization is SignNormalization.TAXONOMY_SIGNED
    assert by_label["수익(매출액)"].sign_normalization is SignNormalization.AS_IS


def test_the_locator_names_the_concept_and_context(tmp_path: Path) -> None:
    path = build_xbrl(tmp_path / "filing.xbrl")

    statement = read_income_statement(path, options=current())
    revenue = next(line for line in statement.lines if line.raw_label == "수익(매출액)")

    assert revenue.locator.concept == "ifrs-full:Revenue"
    assert revenue.locator.context is not None
    assert "ConsolidatedMember" in revenue.locator.context


def test_subtotals_are_marked_and_the_statement_reconciles(tmp_path: Path) -> None:
    path = build_xbrl(tmp_path / "filing.xbrl")

    statement = read_income_statement(path, options=current())
    kinds = {line.subtotal_kind for line in statement.lines if line.is_subtotal}

    assert SubtotalKind.GROSS_PROFIT in kinds
    assert SubtotalKind.PROFIT_BEFORE_TAX in kinds
    assert reconcile_extraction(statement).passed


def test_scale_is_units_not_guessed(tmp_path: Path) -> None:
    """XBRL states figures in the currency's own units. There is no presentation
    scale to detect, so detecting one would only ever be wrong."""
    path = build_xbrl(tmp_path / "filing.xbrl")

    statement = read_income_statement(path, options=current())

    assert statement.scale == 0
    assert statement.currency == "KRW"


# ---------------------------------------------------------------------------
# Note decomposition
# ---------------------------------------------------------------------------


def test_a_caption_is_replaced_by_the_notes_that_reconcile_to_it(tmp_path: Path) -> None:
    """The point of reading XBRL at all.

    금융수익 tells IFRS 18 nothing: interest, FX and derivative gains are
    classified by three different rules (¶49-50, B65, B72). The filing already
    carries the split, so the product should not be asking a human for it.
    """
    path = build_xbrl(tmp_path / "filing.xbrl")

    read = amounts(path, current())

    assert "금융수익" not in read
    assert read["이자수익"] == Decimal(50)
    assert read["외환차익"] == Decimal(30)
    assert read["파생상품이익"] == Decimal(10)
    assert read["이자비용"] == Decimal(-25)
    assert read["외환차손"] == Decimal(-15)


def test_a_breakdown_that_does_not_add_up_is_not_used(tmp_path: Path) -> None:
    """An approximate split of 금융수익 is worse than none.

    It would look like a complete answer while quietly moving money between
    IFRS 18 categories, and the extraction reconciliation would still pass
    because the caption's own total is what the subtotals are checked against.
    """
    filing = Filing()
    incomplete = tuple(
        Fact(fact.concept, fact.value, fact.dimensions)
        for fact in (
            Fact("dart:InterestIncomeFinanceIncome", Decimal(50), NOTE_DIMENSIONS),
            Fact("ifrs-full:ForeignExchangeGain", Decimal(30), NOTE_DIMENSIONS),
            # The derivative gain of 10 is missing, so 50 + 30 != 90.
        )
    )
    filing.add(CURRENT, CONSOLIDATED, (*FACE, *incomplete))
    path = build_xbrl(tmp_path / "filing.xbrl", filing)

    read = amounts(path, current())

    assert read["금융수익"] == Decimal(90)
    assert "이자수익" not in read
    assert "외환차익" not in read


def test_a_decomposed_statement_still_reconciles(tmp_path: Path) -> None:
    """The components stand where their caption stood.

    Their taxonomy order puts them after 당기순이익; if they were left there,
    every subtotal above would re-sum without them and the statement would be
    reported as misread.
    """
    path = build_xbrl(tmp_path / "filing.xbrl")

    statement = read_income_statement(path, options=current())
    report = reconcile_extraction(statement)
    checks = {check.check: check for check in report.checks}

    assert checks[SubtotalKind.PROFIT_BEFORE_TAX.value].delta == Decimal(0)
    assert report.passed, report.failures


def test_a_component_reported_inconsistently_is_dropped(tmp_path: Path) -> None:
    """Two contexts of the same period and basis disagreeing about one component
    is not something to pick between, so the caption simply stands."""
    filing = Filing()
    conflicting = (
        Fact("dart:InterestIncomeFinanceIncome", Decimal(50), NOTE_DIMENSIONS),
        Fact("ifrs-full:ForeignExchangeGain", Decimal(30), NOTE_DIMENSIONS),
        Fact("dart:GainFromDerivatives", Decimal(10), NOTE_DIMENSIONS),
        Fact(
            "dart:GainFromDerivatives",
            Decimal(11),
            (("ifrs-full:SegmentsAxis", "dart:SemiconductorMember"),),
        ),
    )
    filing.add(CURRENT, CONSOLIDATED, (*FACE, *conflicting))
    path = build_xbrl(tmp_path / "filing.xbrl", filing)

    read = amounts(path, current())

    assert read["금융수익"] == Decimal(90)
    assert "파생상품이익" not in read


def test_notes_from_another_period_are_not_borrowed(tmp_path: Path) -> None:
    """The prior half-year's breakdown must not be used to split this one's
    caption, even though it is the same concept under the same basis."""
    filing = Filing()
    filing.add(CURRENT, CONSOLIDATED, FACE)
    filing.add(
        PRIOR,
        CONSOLIDATED,
        (
            *FACE,
            Fact("dart:InterestIncomeFinanceIncome", Decimal(50), NOTE_DIMENSIONS),
            Fact("ifrs-full:ForeignExchangeGain", Decimal(30), NOTE_DIMENSIONS),
            Fact("dart:GainFromDerivatives", Decimal(10), NOTE_DIMENSIONS),
        ),
    )
    path = build_xbrl(tmp_path / "filing.xbrl", filing)

    read = amounts(path, current())

    assert read["금융수익"] == Decimal(90)
    assert "이자수익" not in read


# ---------------------------------------------------------------------------
# What is not a filing
# ---------------------------------------------------------------------------


def test_a_linkbase_says_so_rather_than_reading_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "filing_pre.xml"
    path.write_text(
        '<?xml version="1.0"?><link:linkbase '
        'xmlns:link="http://www.xbrl.org/2003/linkbase"></link:linkbase>',
        encoding="utf-8",
    )

    with pytest.raises(StatementNotFoundError) as caught:
        read_income_statement(path)

    assert ".xbrl" in str(caught.value)


def test_malformed_xml_is_reported_not_raised_raw(tmp_path: Path) -> None:
    path = tmp_path / "filing.xbrl"
    path.write_text("<xbrli:xbrl><oops>", encoding="utf-8")

    with pytest.raises(StatementNotFoundError):
        read_income_statement(path)


def test_a_filing_with_no_concepts_we_know_is_reported(tmp_path: Path) -> None:
    filing = Filing()
    filing.add(CURRENT, CONSOLIDATED, (Fact("dart:SomethingElseEntirely", Decimal(5)),))
    path = build_xbrl(tmp_path / "filing.xbrl", filing)

    with pytest.raises(StatementNotFoundError):
        read_income_statement(path, options=current())


def test_an_enormous_tree_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """XML is a denial-of-service surface (spec §31). The upload layer caps the
    byte size; this caps what a small file may expand into."""
    monkeypatch.setattr("app.adapters.ingest.xbrl.MAX_ELEMENTS", 3)
    path = build_xbrl(tmp_path / "filing.xbrl")

    with pytest.raises(StatementNotFoundError) as caught:
        read_income_statement(path, options=current())

    assert "larger than this tool will parse" in str(caught.value)


def test_the_element_ceiling_is_set_high_enough_for_a_real_filing() -> None:
    """Samsung's half-year instance is a few tens of thousands of elements."""
    assert MAX_ELEMENTS > 1_000_000


def test_a_prefix_the_filer_chose_does_not_change_the_reading(tmp_path: Path) -> None:
    """A concept is identified by its namespace, not by the prefix bound to it.

    A filing that binds the IFRS taxonomy to `ifrs` rather than `ifrs-full`
    reports exactly the same figures.
    """
    path = build_xbrl(tmp_path / "filing.xbrl")
    renamed = tmp_path / "renamed.xbrl"
    renamed.write_text(
        path.read_text(encoding="utf-8")
        .replace('xmlns:ifrs-full="', 'xmlns:ifrs="')
        .replace("ifrs-full:", "ifrs:"),
        encoding="utf-8",
    )

    assert amounts(renamed, current()) == amounts(path, current())
