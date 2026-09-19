"""Reconstructing the IFRS 18 statement. Pure — constructed lines, no files."""

from __future__ import annotations

from decimal import Decimal

from app.domain.enums import (
    ActivityType,
    Ifrs18Category,
    SubtotalKey,
    SubtotalKind,
)
from app.domain.rules import EntityFacts
from app.domain.statement import operating_profit_before, reconstruct
from tests.statement_builders import classified, line, statement

NON_FINANCIAL = EntityFacts(
    {
        ActivityType.INVESTING_IN_ASSETS: False,
        ActivityType.PROVIDING_FINANCING_TO_CUSTOMERS: False,
    }
)


# ---------------------------------------------------------------------------
# T1 — the baseline from docs/04 §12
# ---------------------------------------------------------------------------


def test_t1_baseline_subtotals() -> None:
    """Revenue 100, operating expense -70, interest income 10, finance cost -5."""
    revenue = line(0, "매출액", "100")
    expense = line(1, "영업비용", "-70")
    interest = line(2, "이자수익", "10")
    finance = line(3, "이자비용", "-5")

    result = reconstruct(
        statement(revenue, expense, interest, finance),
        (
            classified(revenue, Ifrs18Category.OPERATING),
            classified(expense, Ifrs18Category.OPERATING),
            classified(interest, Ifrs18Category.INVESTING),
            classified(finance, Ifrs18Category.FINANCING),
        ),
        facts=NON_FINANCIAL,
    )

    assert result.amount(SubtotalKey.OPERATING_PROFIT) == Decimal("30")
    assert result.amount(SubtotalKey.PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES) == Decimal("40")
    assert result.amount(SubtotalKey.PROFIT_BEFORE_TAX) == Decimal("35")


# ---------------------------------------------------------------------------
# T2 — reclassification moves operating profit, never profit before tax
# ---------------------------------------------------------------------------


def test_t2_reclassification_changes_operating_profit_not_profit_before_tax() -> None:
    """The invariant the whole product rests on.

    docs/04 §12 records §30's "30 → 20" as approximate; the four figures give
    40 → 30 (open question Q6). What holds under every reading, and is the more
    important assertion, is that profit before tax does not move at all.
    """
    revenue = line(0, "매출액", "100")
    expense = line(1, "영업비용", "-70")
    interest = line(2, "이자수익", "10")
    finance = line(3, "이자비용", "-5")
    source = statement(revenue, expense, interest, finance)

    # As previously reported: interest income sat inside operating.
    before = reconstruct(
        source,
        (
            classified(revenue, Ifrs18Category.OPERATING),
            classified(expense, Ifrs18Category.OPERATING),
            classified(interest, Ifrs18Category.OPERATING),
            classified(finance, Ifrs18Category.FINANCING),
        ),
        facts=NON_FINANCIAL,
    )
    # Under IFRS 18: interest income is investing.
    after = reconstruct(
        source,
        (
            classified(revenue, Ifrs18Category.OPERATING),
            classified(expense, Ifrs18Category.OPERATING),
            classified(interest, Ifrs18Category.INVESTING),
            classified(finance, Ifrs18Category.FINANCING),
        ),
        facts=NON_FINANCIAL,
    )

    assert before.amount(SubtotalKey.OPERATING_PROFIT) == Decimal("40")
    assert after.amount(SubtotalKey.OPERATING_PROFIT) == Decimal("30")

    delta_pbt = after.amount(SubtotalKey.PROFIT_BEFORE_TAX) - before.amount(
        SubtotalKey.PROFIT_BEFORE_TAX
    )
    assert delta_pbt == Decimal(0)
    assert after.total_of_all_lines == before.total_of_all_lines


# ---------------------------------------------------------------------------
# Subtotal arithmetic
# ---------------------------------------------------------------------------


def test_income_tax_is_added_not_subtracted() -> None:
    """The sign convention means every subtotal is a plain sum."""
    revenue = line(0, "매출액", "100")
    tax = line(1, "법인세비용", "-20")

    result = reconstruct(
        statement(revenue, tax),
        (
            classified(revenue, Ifrs18Category.OPERATING),
            classified(tax, Ifrs18Category.INCOME_TAX),
        ),
        facts=NON_FINANCIAL,
    )

    assert result.amount(SubtotalKey.PROFIT_BEFORE_TAX) == Decimal("100")
    assert result.amount(SubtotalKey.PROFIT_FROM_CONTINUING_OPERATIONS) == Decimal("80")


def test_discontinued_operations_sit_after_tax() -> None:
    revenue = line(0, "매출액", "100")
    tax = line(1, "법인세비용", "-20")
    discontinued = line(2, "중단영업손익", "-5")

    result = reconstruct(
        statement(revenue, tax, discontinued),
        (
            classified(revenue, Ifrs18Category.OPERATING),
            classified(tax, Ifrs18Category.INCOME_TAX),
            classified(discontinued, Ifrs18Category.DISCONTINUED_OPERATION),
        ),
        facts=NON_FINANCIAL,
    )

    assert result.amount(SubtotalKey.PROFIT_FROM_CONTINUING_OPERATIONS) == Decimal("80")
    assert result.amount(SubtotalKey.PROFIT_FOR_THE_PERIOD) == Decimal("75")


def test_both_required_subtotals_are_presented_even_when_equal() -> None:
    """docs/07 F9: an entity with no investing items still presents both."""
    revenue = line(0, "매출액", "100")

    result = reconstruct(
        statement(revenue),
        (classified(revenue, Ifrs18Category.OPERATING),),
        facts=NON_FINANCIAL,
    )

    operating = result.subtotal(SubtotalKey.OPERATING_PROFIT)
    pbfit = result.subtotal(SubtotalKey.PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES)
    assert operating is not None and pbfit is not None
    assert operating.amount == pbfit.amount == Decimal("100")
    assert operating.presented and pbfit.presented


# ---------------------------------------------------------------------------
# Double counting
# ---------------------------------------------------------------------------


def test_t5_subtotals_do_not_participate() -> None:
    revenue = line(0, "매출액", "100")
    expense = line(1, "매출원가", "-70")
    gross = line(2, "매출총이익", "30", subtotal=SubtotalKind.GROSS_PROFIT)

    result = reconstruct(
        statement(revenue, expense, gross),
        (
            classified(revenue, Ifrs18Category.OPERATING),
            classified(expense, Ifrs18Category.OPERATING),
            classified(gross, Ifrs18Category.OPERATING),
        ),
        facts=NON_FINANCIAL,
    )

    assert result.amount(SubtotalKey.OPERATING_PROFIT) == Decimal("30")


def test_t13_decomposed_parent_does_not_participate() -> None:
    """The caption is a container; its children carry the amount."""
    parent = line(0, "영업외수익", "100", decomposed=True)
    first = line(1, "이자수익", "60")
    second = line(2, "유형자산처분이익", "40")

    result = reconstruct(
        statement(parent, first, second),
        (
            classified(parent, Ifrs18Category.OPERATING),
            classified(first, Ifrs18Category.INVESTING),
            classified(second, Ifrs18Category.OPERATING),
        ),
        facts=NON_FINANCIAL,
    )

    assert result.amount(SubtotalKey.OPERATING_PROFIT) == Decimal("40")
    assert result.total(Ifrs18Category.INVESTING) == Decimal("60")
    assert result.total_of_all_lines == Decimal("100")


# ---------------------------------------------------------------------------
# Unclassified items
# ---------------------------------------------------------------------------


def test_unclassified_items_stay_out_of_operating_profit() -> None:
    """An unclassified item must not be silently absorbed into a subtotal."""
    revenue = line(0, "매출액", "100")
    unknown = line(1, "기타수익", "30")

    result = reconstruct(
        statement(revenue, unknown),
        (
            classified(revenue, Ifrs18Category.OPERATING),
            classified(unknown, Ifrs18Category.UNCLASSIFIED, resolved=False),
        ),
        facts=NON_FINANCIAL,
    )

    assert result.amount(SubtotalKey.OPERATING_PROFIT) == Decimal("100")
    assert result.amount(SubtotalKey.PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES) == Decimal("100")


def test_unclassified_items_still_reach_profit_before_tax() -> None:
    """They are pre-tax; keeping them out would break a check that is not the
    real problem, hiding the one that is."""
    revenue = line(0, "매출액", "100")
    unknown = line(1, "기타수익", "30")

    result = reconstruct(
        statement(revenue, unknown),
        (
            classified(revenue, Ifrs18Category.OPERATING),
            classified(unknown, Ifrs18Category.UNCLASSIFIED, resolved=False),
        ),
        facts=NON_FINANCIAL,
    )

    assert result.amount(SubtotalKey.PROFIT_BEFORE_TAX) == Decimal("130")
    assert result.total_of_all_lines == Decimal("130")


# ---------------------------------------------------------------------------
# IFRS 18 paragraph 73 (open question Q3)
# ---------------------------------------------------------------------------


def test_pbfit_is_suppressed_for_a_financing_to_customers_entity() -> None:
    """A prohibition, not an exemption (docs/07 F9)."""
    revenue = line(0, "이자수익", "100")
    facts = EntityFacts({ActivityType.PROVIDING_FINANCING_TO_CUSTOMERS: True})

    result = reconstruct(
        statement(revenue),
        (classified(revenue, Ifrs18Category.OPERATING),),
        facts=facts,
    )

    pbfit = result.subtotal(SubtotalKey.PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES)
    assert pbfit is not None
    assert not pbfit.presented
    assert pbfit.suppressed_reason is not None
    assert "paragraph 73" in pbfit.suppressed_reason


def test_pbfit_is_presented_when_the_activity_is_unknown_or_denied() -> None:
    revenue = line(0, "매출액", "100")

    for facts in (EntityFacts.unknown(), NON_FINANCIAL):
        result = reconstruct(
            statement(revenue),
            (classified(revenue, Ifrs18Category.OPERATING),),
            facts=facts,
        )
        pbfit = result.subtotal(SubtotalKey.PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES)
        assert pbfit is not None
        assert pbfit.presented


# ---------------------------------------------------------------------------
# The reported "before" figure (open question Q2)
# ---------------------------------------------------------------------------


def test_reported_operating_profit_is_taken_from_the_source() -> None:
    revenue = line(0, "매출액", "100")
    expense = line(1, "매출원가", "-70")
    reported = line(2, "영업이익", "30", subtotal=SubtotalKind.REPORTED_OPERATING_PROFIT)

    result = reconstruct(
        statement(revenue, expense, reported),
        (
            classified(revenue, Ifrs18Category.OPERATING),
            classified(expense, Ifrs18Category.OPERATING),
            classified(reported, Ifrs18Category.OPERATING),
        ),
        facts=NON_FINANCIAL,
    )

    assert result.reported_operating_profit == Decimal("30")


def test_operating_profit_before_is_decided_by_position() -> None:
    """Everything above the entity's own subtotal was inside it (ERD §5)."""
    revenue = line(0, "매출액", "100")
    reported = line(1, "영업이익", "100", subtotal=SubtotalKind.REPORTED_OPERATING_PROFIT)
    interest = line(2, "이자수익", "10")
    source = statement(revenue, reported, interest)

    assert operating_profit_before(source, revenue) == Decimal("100")
    assert operating_profit_before(source, interest) == Decimal("0")
    # The subtotal itself never contributes.
    assert operating_profit_before(source, reported) == Decimal("0")


def test_reported_operating_profit_is_none_when_the_source_printed_none() -> None:
    revenue = line(0, "매출액", "100")

    result = reconstruct(
        statement(revenue),
        (classified(revenue, Ifrs18Category.OPERATING),),
        facts=NON_FINANCIAL,
    )

    assert result.reported_operating_profit is None
