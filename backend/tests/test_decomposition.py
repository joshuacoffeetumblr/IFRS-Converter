"""Breaking an aggregate caption into its components (open question Q5)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.decomposition import (
    Component,
    DecompositionError,
    check_decomposition,
    decompose,
    status_after,
)
from app.domain.enums import DecompositionStatus, SignNormalization, SubtotalKind
from app.domain.extraction import ExtractedLine, SourceLocator

LOCATOR = SourceLocator(source_file="fs.xlsx", sheet="손익계산서", row=11, column="C", cell="C11")


def aggregate(amount: str = "100", label: str = "영업외수익") -> ExtractedLine:
    return ExtractedLine(
        ordinal=5,
        raw_label=label,
        raw_value=amount,
        amount=Decimal(amount),
        sign_normalization=SignNormalization.AS_IS,
        locator=LOCATOR,
        depth=1,
        note_references=("주석 24",),
    )


COMPONENTS = (
    Component("이자수익", Decimal("40"), "주석 25"),
    Component("매출채권 외환차익", Decimal("35"), "주석 24"),
    Component("유형자산처분이익", Decimal("25"), "주석 11"),
)


def test_balanced_components_are_accepted() -> None:
    check = check_decomposition(aggregate(), COMPONENTS)

    assert check.balances
    assert check.delta == 0
    assert check.components_total == Decimal("100")
    assert check.component_count == 3


def test_unbalanced_components_are_reported_with_the_difference() -> None:
    check = check_decomposition(aggregate(), COMPONENTS[:2])

    assert not check.balances
    assert check.delta == Decimal("-25")


def test_decompose_marks_the_parent_and_creates_children() -> None:
    parent = aggregate()

    decomposed, children = decompose(parent, COMPONENTS, next_ordinal=100)

    assert len(children) == 3
    assert sum(child.amount for child in children) == parent.amount
    assert [child.raw_label for child in children] == [
        "이자수익",
        "매출채권 외환차익",
        "유형자산처분이익",
    ]
    assert decomposed.amount == parent.amount


def test_children_are_nested_below_the_parent() -> None:
    _, children = decompose(aggregate(), COMPONENTS, next_ordinal=100)

    assert all(child.depth == 2 for child in children)
    assert [child.ordinal for child in children] == [100, 101, 102]


def test_children_carry_their_own_note_reference() -> None:
    """Provenance must reach the note the figure was read from (spec §18)."""
    _, children = decompose(aggregate(), COMPONENTS, next_ordinal=100)

    assert children[0].note_references == ("주석 25",)
    assert children[1].note_references == ("주석 24",)


def test_children_inherit_the_captions_source_location() -> None:
    _, children = decompose(aggregate(), COMPONENTS, next_ordinal=100)

    for child in children:
        assert child.locator.cell == "C11"
        assert child.locator.source_file == "fs.xlsx"


def test_children_are_summable_and_countable_once() -> None:
    """Test vector T13: the caption's amount must not be counted twice."""
    _, children = decompose(aggregate(), COMPONENTS, next_ordinal=100)

    assert all(child.is_summable for child in children)
    assert sum(child.amount for child in children) == Decimal("100")


def test_unbalanced_decomposition_is_refused() -> None:
    """Letting this through would silently stop the statement adding up."""
    with pytest.raises(DecompositionError, match="difference"):
        decompose(aggregate(), COMPONENTS[:2], next_ordinal=100)


def test_decomposition_balance_has_no_tolerance() -> None:
    """Unlike a printed subtotal, these figures are being entered now.

    A difference is a data-entry error, not a presentation rounding artefact,
    so there is nothing to absorb.
    """
    nearly = (*COMPONENTS[:2], Component("유형자산처분이익", Decimal("25.000001")))

    with pytest.raises(DecompositionError):
        decompose(aggregate(), nearly, next_ordinal=100)


def test_empty_decomposition_is_refused() -> None:
    with pytest.raises(DecompositionError, match="at least one"):
        decompose(aggregate(), (), next_ordinal=100)


def test_a_subtotal_cannot_be_decomposed() -> None:
    subtotal = ExtractedLine(
        ordinal=2,
        raw_label="영업이익",
        raw_value="120",
        amount=Decimal("120"),
        sign_normalization=SignNormalization.AS_IS,
        locator=LOCATOR,
        is_subtotal=True,
        subtotal_kind=SubtotalKind.REPORTED_OPERATING_PROFIT,
    )

    with pytest.raises(DecompositionError, match="subtotal"):
        decompose(subtotal, COMPONENTS, next_ordinal=100)


def test_negative_aggregate_decomposes() -> None:
    """영업외비용 is negative under the sign convention; so are its parts."""
    expense = aggregate(amount="-50", label="영업외비용")
    parts = (
        Component("이자비용", Decimal("-30")),
        Component("외환차손", Decimal("-20")),
    )

    _, children = decompose(expense, parts, next_ordinal=10)

    assert sum(child.amount for child in children) == Decimal("-50")


def test_mixed_sign_components_are_allowed_when_they_balance() -> None:
    """A net caption may contain gains and losses."""
    net = aggregate(amount="10", label="기타수익")
    parts = (
        Component("외환차익", Decimal("30")),
        Component("외환차손", Decimal("-20")),
    )

    _, children = decompose(net, parts, next_ordinal=10)

    assert sum(child.amount for child in children) == Decimal("10")


@pytest.mark.parametrize(
    ("accepted", "decomposed", "expected"),
    [
        (False, True, DecompositionStatus.DECOMPOSED),
        (True, False, DecompositionStatus.ACCEPTED_AGGREGATE),
        (False, False, DecompositionStatus.REQUIRED),
    ],
)
def test_status_after_user_action(
    accepted: bool, decomposed: bool, expected: DecompositionStatus
) -> None:
    assert status_after(accepted, decomposed) is expected
