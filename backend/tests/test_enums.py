"""Invariants of the IFRS 18 taxonomy. Pure — no database."""

from __future__ import annotations

import pytest

from app.domain.enums import (
    SUBCATEGORY_TO_CATEGORY,
    ActivityType,
    Ifrs18Category,
    Ifrs18Subcategory,
)


def test_five_ifrs18_categories_plus_one_technical_state() -> None:
    """IFRS 18 defines five categories for profit or loss. UNCLASSIFIED is ours."""
    real = [c for c in Ifrs18Category if c.is_ifrs18_category]

    assert len(real) == 5
    assert set(real) == {
        Ifrs18Category.OPERATING,
        Ifrs18Category.INVESTING,
        Ifrs18Category.FINANCING,
        Ifrs18Category.INCOME_TAX,
        Ifrs18Category.DISCONTINUED_OPERATION,
    }
    assert not Ifrs18Category.UNCLASSIFIED.is_ifrs18_category


def test_every_subcategory_maps_to_a_category() -> None:
    """A subcategory added later must not be left orphaned."""
    unmapped = set(Ifrs18Subcategory) - set(SUBCATEGORY_TO_CATEGORY)

    assert not unmapped, f"subcategories with no parent category: {sorted(unmapped)}"


@pytest.mark.parametrize(
    ("subcategory", "expected"),
    [
        (Ifrs18Subcategory.OPERATING_REVENUE, Ifrs18Category.OPERATING),
        (Ifrs18Subcategory.INVESTING_INCOME, Ifrs18Category.INVESTING),
        (Ifrs18Subcategory.FINANCING_EXPENSE, Ifrs18Category.FINANCING),
        (Ifrs18Subcategory.INCOME_TAX_EXPENSE, Ifrs18Category.INCOME_TAX),
        (Ifrs18Subcategory.DISCONTINUED_RESULT, Ifrs18Category.DISCONTINUED_OPERATION),
    ],
)
def test_subcategory_parentage(subcategory: Ifrs18Subcategory, expected: Ifrs18Category) -> None:
    assert SUBCATEGORY_TO_CATEGORY[subcategory] is expected


def test_no_subcategory_maps_to_unclassified() -> None:
    """UNCLASSIFIED is a state, not a presentational bucket."""
    assert Ifrs18Category.UNCLASSIFIED not in SUBCATEGORY_TO_CATEGORY.values()


def test_exactly_two_specified_main_business_activities() -> None:
    """IFRS 18 specifies investing in assets and providing financing to customers."""
    specified = {a for a in ActivityType if a.is_specified_main_business_activity}

    assert specified == {
        ActivityType.INVESTING_IN_ASSETS,
        ActivityType.PROVIDING_FINANCING_TO_CUSTOMERS,
    }


def test_descriptive_activities_are_not_specified() -> None:
    """These describe an industry; they must never drive classification alone."""
    for activity in (
        ActivityType.FINANCIAL_SERVICES,
        ActivityType.INVESTMENT,
        ActivityType.LENDING,
        ActivityType.REAL_ESTATE,
        ActivityType.OTHER,
    ):
        assert not activity.is_specified_main_business_activity
