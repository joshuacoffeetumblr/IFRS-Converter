"""Impact analysis: KPIs, the waterfall, and the account-level detail."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.enums import ActivityType, Ifrs18Category, SubtotalKind
from app.domain.extraction import ExtractedStatement
from app.domain.impact import (
    ImpactAnalysis,
    ReportedPlacement,
    StepKind,
    Unit,
    analyse,
    line_impact_on_operating_profit,
)
from app.domain.rules import EntityFacts
from app.domain.statement import ClassifiedLine, reconstruct
from tests.statement_builders import classified, line, statement

NON_FINANCIAL = EntityFacts(
    {
        ActivityType.INVESTING_IN_ASSETS: False,
        ActivityType.PROVIDING_FINANCING_TO_CUSTOMERS: False,
    }
)


def case() -> tuple[ExtractedStatement, tuple[ClassifiedLine, ...]]:
    """Interest income moves out of operating; a disposal gain moves in."""
    revenue = line(0, "매출액", "1000")
    cost = line(1, "매출원가", "-700")
    interest = line(2, "이자수익", "50")
    reported = line(3, "영업이익", "350", subtotal=SubtotalKind.REPORTED_OPERATING_PROFIT)
    disposal = line(4, "유형자산처분이익", "40")
    tax = line(5, "법인세비용", "-90")
    net = line(6, "당기순이익", "300", subtotal=SubtotalKind.PROFIT_FOR_THE_PERIOD)

    source = statement(revenue, cost, interest, reported, disposal, tax, net)
    decisions = (
        classified(revenue, Ifrs18Category.OPERATING, account="REVENUE"),
        classified(cost, Ifrs18Category.OPERATING, account="COST_OF_SALES"),
        classified(interest, Ifrs18Category.INVESTING, account="INTEREST_INCOME"),
        classified(reported, Ifrs18Category.OPERATING),
        classified(disposal, Ifrs18Category.OPERATING, account="GAIN_ON_DISPOSAL_OF_PPE"),
        classified(tax, Ifrs18Category.INCOME_TAX, account="INCOME_TAX_EXPENSE"),
        classified(net, Ifrs18Category.OPERATING),
    )
    return source, decisions


@pytest.fixture
def analysis() -> ImpactAnalysis:
    source, decisions = case()
    return analyse(source, decisions, reconstruct(source, decisions, facts=NON_FINANCIAL))


# ---------------------------------------------------------------------------
# Per-line movement
# ---------------------------------------------------------------------------


def test_an_item_moved_out_of_operating_has_a_negative_impact() -> None:
    source, decisions = case()
    interest = next(d for d in decisions if d.line.raw_label == "이자수익")

    assert line_impact_on_operating_profit(source, interest) == Decimal("-50")


def test_an_item_moved_into_operating_has_a_positive_impact() -> None:
    source, decisions = case()
    disposal = next(d for d in decisions if d.line.raw_label == "유형자산처분이익")

    assert line_impact_on_operating_profit(source, disposal) == Decimal("40")


def test_an_item_that_did_not_move_has_no_impact() -> None:
    source, decisions = case()
    revenue = next(d for d in decisions if d.line.raw_label == "매출액")

    assert line_impact_on_operating_profit(source, revenue) == Decimal(0)


def test_subtotals_contribute_nothing() -> None:
    source, decisions = case()
    reported = next(d for d in decisions if d.line.raw_label == "영업이익")

    assert line_impact_on_operating_profit(source, reported) == Decimal(0)


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------


def test_headline_operating_profit(analysis: ImpactAnalysis) -> None:
    headline = analysis.headline

    assert headline is not None
    assert headline.before == Decimal("350")
    assert headline.after == Decimal("340")  # 350 - 50 + 40
    assert headline.change == Decimal("-10")


def test_profit_before_tax_does_not_move(analysis: ImpactAnalysis) -> None:
    """The user-visible proof that presentation changed and profit did not."""
    for key in ("PROFIT_FOR_THE_PERIOD",):
        kpi = analysis.kpi(key)
        assert kpi is not None
        assert kpi.change == Decimal(0)
        assert kpi.change_pct == Decimal(0)


def test_revenue_is_identified_by_account_not_by_sign(analysis: ImpactAnalysis) -> None:
    """A disposal gain is positive and operating, but it is not revenue.

    Counting it would inflate the margin denominator, and would make the margin
    move whenever an unrelated item was reclassified into operating.
    """
    revenue = analysis.kpi("REVENUE")

    assert revenue is not None
    assert revenue.after == Decimal("1000")
    assert revenue.change == Decimal(0)


def test_margin_is_reported_in_percent_and_basis_points(analysis: ImpactAnalysis) -> None:
    margin = analysis.kpi("OPERATING_PROFIT_MARGIN")

    assert margin is not None
    assert margin.unit is Unit.PERCENT
    assert margin.before == Decimal("35.0000")
    assert margin.after == Decimal("34.0000")
    assert margin.change_bps == Decimal("-100.00")


def test_measures_introduced_by_ifrs18_have_no_before(analysis: ImpactAnalysis) -> None:
    """Reporting zero would imply a change from nothing to something.

    These categories did not exist under the entity's previous presentation, so
    a "before" of zero is a different and false claim from "did not exist".
    """
    for key in (
        "PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES",
        "INVESTING_RESULT",
        "FINANCING_RESULT",
    ):
        kpi = analysis.kpi(key)
        assert kpi is not None, key
        assert kpi.is_new_measure, key
        assert kpi.before is None, key
        assert kpi.change is None, key
        assert kpi.change_pct is None, key


def test_percentage_change_is_undefined_when_the_base_is_zero() -> None:
    """Rendering 0% or infinity would both be wrong."""
    revenue = line(0, "매출액", "0")
    reported = line(1, "영업이익", "0", subtotal=SubtotalKind.REPORTED_OPERATING_PROFIT)
    gain = line(2, "유형자산처분이익", "40")
    source = statement(revenue, reported, gain)
    decisions = (
        classified(revenue, Ifrs18Category.OPERATING, account="REVENUE"),
        classified(reported, Ifrs18Category.OPERATING),
        classified(gain, Ifrs18Category.OPERATING),
    )

    result = analyse(source, decisions, reconstruct(source, decisions, facts=NON_FINANCIAL))

    operating = result.kpi("OPERATING_PROFIT")
    assert operating is not None
    assert operating.change == Decimal("40")
    assert operating.change_pct is None


def test_percentage_change_uses_the_magnitude_of_the_base() -> None:
    """A loss narrowing from -100 to -50 is a 50% improvement, not -50%."""
    expense = line(0, "영업비용", "-100")
    reported = line(1, "영업이익", "-100", subtotal=SubtotalKind.REPORTED_OPERATING_PROFIT)
    gain = line(2, "유형자산처분이익", "50")
    source = statement(expense, reported, gain)
    decisions = (
        classified(expense, Ifrs18Category.OPERATING),
        classified(reported, Ifrs18Category.OPERATING),
        classified(gain, Ifrs18Category.OPERATING),
    )

    result = analyse(source, decisions, reconstruct(source, decisions, facts=NON_FINANCIAL))

    operating = result.kpi("OPERATING_PROFIT")
    assert operating is not None
    assert operating.change == Decimal("50")
    assert operating.change_pct == Decimal("50.0000")


# ---------------------------------------------------------------------------
# The waterfall
# ---------------------------------------------------------------------------


def test_the_waterfall_starts_at_the_reported_figure(analysis: ImpactAnalysis) -> None:
    start = analysis.waterfall[0]

    assert start.kind is StepKind.START
    assert start.value == Decimal("350")


def test_the_waterfall_ends_at_the_ifrs18_figure(analysis: ImpactAnalysis) -> None:
    end = analysis.waterfall[-1]

    assert end.kind is StepKind.END
    assert end.value == Decimal("340")


def test_the_waterfall_balances(analysis: ImpactAnalysis) -> None:
    """Start plus every step lands exactly on the end."""
    assert analysis.waterfall_balances


def test_each_step_names_the_lines_behind_it(analysis: ImpactAnalysis) -> None:
    """Spec §23: clicking a step must lead to the accounts that caused it."""
    deltas = [s for s in analysis.waterfall if s.kind is StepKind.DELTA]

    assert deltas
    for step in deltas:
        assert step.line_ids


def test_steps_are_ordered_by_magnitude(analysis: ImpactAnalysis) -> None:
    deltas = [s for s in analysis.waterfall if s.kind is StepKind.DELTA]
    magnitudes = [abs(s.value) for s in deltas]

    assert magnitudes == sorted(magnitudes, reverse=True)


def test_small_movements_are_grouped_rather_than_dropped() -> None:
    """Dropping them would make the waterfall fail to add up."""
    reported = line(0, "영업이익", "0", subtotal=SubtotalKind.REPORTED_OPERATING_PROFIT)
    extras = [line(i + 1, f"기타항목{i}", str(i + 1)) for i in range(8)]
    source = statement(reported, *extras)
    decisions = (
        classified(reported, Ifrs18Category.OPERATING),
        *(classified(item, Ifrs18Category.OPERATING) for item in extras),
    )

    result = analyse(
        source,
        decisions,
        reconstruct(source, decisions, facts=NON_FINANCIAL),
        max_waterfall_steps=3,
    )

    grouped = next(s for s in result.waterfall if s.key == "OTHER_RECLASSIFICATIONS")
    assert len(grouped.line_ids) == 5
    assert result.waterfall_balances


def test_an_unattributed_difference_is_shown_not_absorbed() -> None:
    """Open question Q2.

    Where the reported subtotal does not equal the lines above it, the
    difference appears as its own step. The gate blocks such a statement, but
    the impact screen is still rendered in a degraded state (spec §19), and a
    bridge that silently failed to add up there would be worse than none.
    """
    revenue = line(0, "매출액", "1000")
    # The entity printed 900, but the lines above it total 1000.
    reported = line(1, "영업이익", "900", subtotal=SubtotalKind.REPORTED_OPERATING_PROFIT)
    source = statement(revenue, reported)
    decisions = (
        classified(revenue, Ifrs18Category.OPERATING, account="REVENUE"),
        classified(reported, Ifrs18Category.OPERATING),
    )

    result = analyse(source, decisions, reconstruct(source, decisions, facts=NON_FINANCIAL))

    unattributed = next(s for s in result.waterfall if s.kind is StepKind.UNATTRIBUTED)
    assert unattributed.value == Decimal("100")
    assert result.waterfall_balances


def test_no_unattributed_step_when_everything_reconciles(analysis: ImpactAnalysis) -> None:
    assert not [s for s in analysis.waterfall if s.kind is StepKind.UNATTRIBUTED]


# ---------------------------------------------------------------------------
# Account-level detail
# ---------------------------------------------------------------------------


def test_reclassifications_record_where_the_item_used_to_sit(analysis: ImpactAnalysis) -> None:
    """Only what the reported statement actually tells us.

    It carries no IFRS 18 categories, so claiming one for the "before" side
    would be a fabrication. All it says is whether an item was inside the
    operating subtotal.
    """
    interest = next(r for r in analysis.reclassifications if r.label == "이자수익")
    disposal = next(r for r in analysis.reclassifications if r.label == "유형자산처분이익")

    assert interest.reported_placement is ReportedPlacement.INSIDE_OPERATING
    assert interest.ifrs18_category is Ifrs18Category.INVESTING
    assert disposal.reported_placement is ReportedPlacement.OUTSIDE_OPERATING
    assert disposal.ifrs18_category is Ifrs18Category.OPERATING


def test_no_comparison_is_made_when_the_source_printed_no_operating_subtotal() -> None:
    """Without a "before" there is no movement to report.

    Regression: every operating line previously showed its full amount as an
    impact, describing an unchanged statement as completely restructured.
    """
    revenue = line(0, "매출액", "1000")
    source = statement(revenue)
    decisions = (classified(revenue, Ifrs18Category.OPERATING, account="REVENUE"),)

    result = analyse(source, decisions, reconstruct(source, decisions, facts=NON_FINANCIAL))

    assert not result.comparable
    assert not result.reclassifications
    assert not result.waterfall
    # Nothing to bridge is not the same as a broken bridge.
    assert result.waterfall_balances

    operating = result.kpi("OPERATING_PROFIT")
    assert operating is not None
    assert operating.before is None


def test_unchanged_lines_are_not_listed_as_reclassifications(analysis: ImpactAnalysis) -> None:
    labels = {r.label for r in analysis.reclassifications}

    assert "매출액" not in labels
    assert labels == {"이자수익", "유형자산처분이익"}


def test_reclassifications_carry_their_rule(analysis: ImpactAnalysis) -> None:
    """Spec §23: every driver must be traceable to why it moved."""
    for item in analysis.reclassifications:
        assert item.rule_id is not None


def test_top_reclassifications_are_ranked_by_magnitude(analysis: ImpactAnalysis) -> None:
    ranked = analysis.top_reclassifications

    assert [r.label for r in ranked] == ["이자수익", "유형자산처분이익"]


def test_reclassifications_sum_to_the_headline_change(analysis: ImpactAnalysis) -> None:
    """The account detail and the headline cannot disagree."""
    headline = analysis.headline
    assert headline is not None

    total = sum(
        (r.impact_on_operating_profit for r in analysis.reclassifications),
        start=Decimal(0),
    )
    assert total == headline.change
