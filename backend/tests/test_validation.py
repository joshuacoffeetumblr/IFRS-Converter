"""The reconciliation gate (spec §19). Blocking checks must actually block."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from app.domain.enums import ActivityType, Ifrs18Category, SubtotalKind
from app.domain.extraction import ExtractedStatement
from app.domain.rules import EntityFacts
from app.domain.statement import ClassifiedLine, reconstruct
from app.domain.validation import (
    Finding,
    Severity,
    Tolerances,
    ValidationReport,
    validate,
)
from tests.statement_builders import classified, line, statement

NON_FINANCIAL = EntityFacts(
    {
        ActivityType.INVESTING_IN_ASSETS: False,
        ActivityType.PROVIDING_FINANCING_TO_CUSTOMERS: False,
    }
)


def reconciling_case() -> tuple[ExtractedStatement, tuple[ClassifiedLine, ...]]:
    """A statement whose own arithmetic holds and whose lines all classify."""
    revenue = line(0, "매출액", "1000")
    cost = line(1, "매출원가", "-700")
    reported = line(2, "영업이익", "300", subtotal=SubtotalKind.REPORTED_OPERATING_PROFIT)
    interest = line(3, "이자수익", "30")
    finance = line(4, "이자비용", "-20")
    pbt = line(5, "법인세차감전순이익", "310", subtotal=SubtotalKind.PROFIT_BEFORE_TAX)
    tax = line(6, "법인세비용", "-60")
    net = line(7, "당기순이익", "250", subtotal=SubtotalKind.PROFIT_FOR_THE_PERIOD)

    source = statement(revenue, cost, reported, interest, finance, pbt, tax, net)
    decisions = (
        classified(revenue, Ifrs18Category.OPERATING),
        classified(cost, Ifrs18Category.OPERATING),
        classified(reported, Ifrs18Category.OPERATING),
        classified(interest, Ifrs18Category.INVESTING),
        classified(finance, Ifrs18Category.FINANCING),
        classified(pbt, Ifrs18Category.OPERATING),
        classified(tax, Ifrs18Category.INCOME_TAX),
        classified(net, Ifrs18Category.OPERATING),
    )
    return source, decisions


def test_a_consistent_reconstruction_passes_every_check() -> None:
    source, decisions = reconciling_case()

    report = validate(source, decisions, reconstruct(source, decisions, facts=NON_FINANCIAL))

    assert report.passed, [(f.check, str(f.delta)) for f in report.failures]
    assert not report.failures
    assert {f.check for f in report.findings} >= {
        "TOTAL_INVARIANCE",
        "CATEGORY_COMPLETENESS",
        "NO_UNANSWERED_QUESTIONS",
        "OUT_OF_VALIDATED_SCOPE",
        "REPORTED_PROFIT_BEFORE_TAX",
        "OPERATING_BRIDGE",
    }


# ---------------------------------------------------------------------------
# Total invariance — the backbone
# ---------------------------------------------------------------------------


def test_total_invariance_has_no_tolerance() -> None:
    """A difference here is a defect, not rounding, so nothing absorbs it."""
    source, decisions = reconciling_case()
    # Drop a line from the reconstruction, as a buggy engine might.
    reconstructed = reconstruct(source, decisions[:-2], facts=NON_FINANCIAL)

    report = validate(
        source,
        decisions,
        reconstructed,
        tolerances=Tolerances(profit_before_tax=Decimal("999999"), subtotal=Decimal("999999")),
    )

    invariance = next(f for f in report.findings if f.check == "TOTAL_INVARIANCE")
    assert not invariance.passed
    assert invariance.tolerance == Decimal(0)
    assert invariance.severity is Severity.BLOCKING
    assert not report.passed


def test_total_invariance_is_blind_to_how_items_are_classified() -> None:
    """Moving an item between categories cannot change the total."""
    source, decisions = reconciling_case()

    moved = tuple(
        classified(item.line, Ifrs18Category.FINANCING)
        if item.line.raw_label == "이자수익"
        else item
        for item in decisions
    )
    report = validate(source, moved, reconstruct(source, moved, facts=NON_FINANCIAL))

    assert next(f for f in report.findings if f.check == "TOTAL_INVARIANCE").passed


# ---------------------------------------------------------------------------
# Structural blocks
# ---------------------------------------------------------------------------


def test_an_unclassified_line_blocks() -> None:
    """UNCLASSIFIED is a technical state, never a category."""
    source, decisions = reconciling_case()
    with_unknown = tuple(
        classified(item.line, Ifrs18Category.UNCLASSIFIED, resolved=False)
        if item.line.raw_label == "이자수익"
        else item
        for item in decisions
    )

    report = validate(source, with_unknown, reconstruct(source, with_unknown, facts=NON_FINANCIAL))

    assert not report.passed
    failed = {f.check for f in report.blocking_failures}
    assert "CATEGORY_COMPLETENESS" in failed
    assert "이자수익" in next(
        f.detail for f in report.failures if f.check == "CATEGORY_COMPLETENESS"
    )


def test_an_unanswered_question_blocks() -> None:
    """Test vector T4 at the gate: a rule waiting on a fact is not a decision."""
    source, decisions = reconciling_case()
    blocked = tuple(
        classified(item.line, Ifrs18Category.INVESTING, resolved=False)
        if item.line.raw_label == "이자수익"
        else item
        for item in decisions
    )

    report = validate(source, blocked, reconstruct(source, blocked, facts=NON_FINANCIAL))

    assert "NO_UNANSWERED_QUESTIONS" in {f.check for f in report.blocking_failures}


def test_a_prohibited_subtotal_blocks_as_out_of_scope() -> None:
    """Open question Q3: IFRS 18.73 entities are outside the validated scope."""
    source, decisions = reconciling_case()
    facts = EntityFacts({ActivityType.PROVIDING_FINANCING_TO_CUSTOMERS: True})

    report = validate(source, decisions, reconstruct(source, decisions, facts=facts))

    assert not report.passed
    finding = next(f for f in report.blocking_failures if f.check == "OUT_OF_VALIDATED_SCOPE")
    assert "paragraph 73" in finding.detail


# ---------------------------------------------------------------------------
# Agreement with the figures the entity printed
# ---------------------------------------------------------------------------


def test_profit_before_tax_must_match_the_reported_figure() -> None:
    source, decisions = reconciling_case()
    # A misread line: profit before tax no longer reconciles.
    broken_line = replace(decisions[3].line, amount=Decimal("99"))
    broken = (*decisions[:3], classified(broken_line, Ifrs18Category.INVESTING), *decisions[4:])

    report = validate(source, broken, reconstruct(source, broken, facts=NON_FINANCIAL))

    assert not report.passed
    assert "REPORTED_PROFIT_BEFORE_TAX" in {f.check for f in report.blocking_failures}


def test_rounding_tolerance_absorbs_a_presentation_unit_but_reports_it() -> None:
    """Test vector T8. A source stated in 백만원 may itself round."""
    revenue = line(0, "매출액", "1000")
    pbt = line(1, "법인세차감전순이익", "1001", subtotal=SubtotalKind.PROFIT_BEFORE_TAX)
    source = statement(revenue, pbt)
    decisions = (
        classified(revenue, Ifrs18Category.OPERATING),
        classified(pbt, Ifrs18Category.OPERATING),
    )
    reconstructed = reconstruct(source, decisions, facts=NON_FINANCIAL)

    strict = validate(
        source, decisions, reconstructed, tolerances=Tolerances(profit_before_tax=Decimal(0))
    )
    lenient = validate(
        source, decisions, reconstructed, tolerances=Tolerances(profit_before_tax=Decimal(1))
    )

    assert not strict.passed
    assert lenient.passed
    # Absorbed, but never hidden: the difference is still reported.
    finding = next(f for f in lenient.findings if f.check == "REPORTED_PROFIT_BEFORE_TAX")
    assert finding.delta == Decimal("-1")


def test_checks_are_skipped_when_the_source_printed_no_such_subtotal() -> None:
    """Claiming a check passed when nothing was compared would be a lie."""
    revenue = line(0, "매출액", "1000")
    source = statement(revenue)
    decisions = (classified(revenue, Ifrs18Category.OPERATING),)

    report = validate(source, decisions, reconstruct(source, decisions, facts=NON_FINANCIAL))

    assert "REPORTED_PROFIT_BEFORE_TAX" not in {f.check for f in report.findings}
    assert "OPERATING_BRIDGE" not in {f.check for f in report.findings}
    assert report.passed


# ---------------------------------------------------------------------------
# The operating bridge — what makes the waterfall add up
# ---------------------------------------------------------------------------


def test_the_operating_bridge_balances_when_an_item_is_reclassified_out() -> None:
    """Reported operating profit plus every movement equals the new figure."""
    revenue = line(0, "매출액", "1000")
    interest = line(1, "이자수익", "30")
    reported = line(2, "영업이익", "1030", subtotal=SubtotalKind.REPORTED_OPERATING_PROFIT)
    source = statement(revenue, interest, reported)

    decisions = (
        classified(revenue, Ifrs18Category.OPERATING),
        # Interest income was inside the reported subtotal; IFRS 18 moves it out.
        classified(interest, Ifrs18Category.INVESTING),
        classified(reported, Ifrs18Category.OPERATING),
    )
    reconstructed = reconstruct(source, decisions, facts=NON_FINANCIAL)

    report = validate(source, decisions, reconstructed)

    bridge = next(f for f in report.findings if f.check == "OPERATING_BRIDGE")
    assert bridge.passed
    assert bridge.tolerance == Decimal(0)
    assert reconstructed.reported_operating_profit == Decimal("1030")
    assert bridge.expected == Decimal("1000")


def test_the_operating_bridge_balances_when_an_item_is_reclassified_in() -> None:
    """The case that matters for Korean statements: 영업외 items become operating."""
    revenue = line(0, "매출액", "1000")
    reported = line(1, "영업이익", "1000", subtotal=SubtotalKind.REPORTED_OPERATING_PROFIT)
    disposal = line(2, "유형자산처분이익", "40")
    source = statement(revenue, reported, disposal)

    decisions = (
        classified(revenue, Ifrs18Category.OPERATING),
        classified(reported, Ifrs18Category.OPERATING),
        classified(disposal, Ifrs18Category.OPERATING),
    )
    reconstructed = reconstruct(source, decisions, facts=NON_FINANCIAL)

    report = validate(source, decisions, reconstructed)

    assert next(f for f in report.findings if f.check == "OPERATING_BRIDGE").passed
    assert reconstructed.total(Ifrs18Category.OPERATING) == Decimal("1040")


# ---------------------------------------------------------------------------
# Report shape
# ---------------------------------------------------------------------------


def test_warnings_do_not_block() -> None:
    report = ValidationReport(
        findings=(
            Finding(
                check="SOMETHING_MINOR",
                passed=False,
                severity=Severity.WARNING,
                detail="not fatal",
            ),
        )
    )

    assert report.passed
    assert report.warnings
    assert not report.blocking_failures
