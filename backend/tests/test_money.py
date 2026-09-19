"""Parsing printed figures. Pure — no database, no files."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.enums import SignNormalization
from app.domain.money import (
    AmountParseError,
    decimal_from_spreadsheet_value,
    looks_like_amount,
    parse_amount,
)


@pytest.mark.parametrize(
    ("raw", "expected", "normalization"),
    [
        ("100,000", Decimal("100000"), SignNormalization.AS_IS),
        ("1000000", Decimal("1000000"), SignNormalization.AS_IS),
        ("-70,000", Decimal("-70000"), SignNormalization.AS_IS),
        ("+5,000", Decimal("5000"), SignNormalization.AS_IS),
        ("1,234.56", Decimal("1234.56"), SignNormalization.AS_IS),
        # Parentheses — the common export convention.
        ("(70,000)", Decimal("-70000"), SignNormalization.PARENTHESES_NEGATED),
        ("( 70,000 )", Decimal("-70000"), SignNormalization.PARENTHESES_NEGATED),
        # Full-width parentheses, folded by NFKC.
        ("（70,000）", Decimal("-70000"), SignNormalization.PARENTHESES_NEGATED),
        # Triangle marks — a Korean convention.
        ("△70,000", Decimal("-70000"), SignNormalization.TRIANGLE_NEGATED),
        ("▲70,000", Decimal("-70000"), SignNormalization.TRIANGLE_NEGATED),
        # Currency marks and exotic whitespace from Excel exports.
        ("₩ 5,000", Decimal("5000"), SignNormalization.AS_IS),
        ("5 000", Decimal("5000"), SignNormalization.AS_IS),
    ],
)
def test_parses_printed_conventions(
    raw: str, expected: Decimal, normalization: SignNormalization
) -> None:
    parsed = parse_amount(raw)

    assert parsed.value == expected
    assert parsed.sign_normalization is normalization


def test_minus_inside_parentheses_is_not_double_negated() -> None:
    """`(-70,000)` is negative once. Treating it as a double negation would
    silently turn a cost into income."""
    assert parse_amount("(-70,000)").value == Decimal("-70000")


@pytest.mark.parametrize("raw", ["", "-", "–", "N/A", "n/a", "."])
def test_nil_markers_are_zero_and_flagged(raw: str) -> None:
    """A blank cell and a printed zero differ: one means the line was omitted."""
    parsed = parse_amount(raw)

    assert parsed.value == Decimal(0)
    assert parsed.is_nil


def test_printed_zero_is_not_nil() -> None:
    assert parse_amount("0").value == Decimal(0)
    assert not parse_amount("0").is_nil


def test_none_is_nil() -> None:
    assert parse_amount(None).is_nil


@pytest.mark.parametrize("raw", ["abc", "매출액", "1,2,3.4.5", "12%", "2025-01-01"])
def test_non_figures_are_rejected(raw: str) -> None:
    with pytest.raises(AmountParseError):
        parse_amount(raw)


def test_booleans_are_not_figures() -> None:
    """bool subclasses int; accepting it would read TRUE as 1."""
    with pytest.raises(AmountParseError):
        parse_amount(True)


def test_decimal_passes_through_exactly() -> None:
    value = Decimal("12345678901234567890123456789012.123456")

    assert parse_amount(value).value == value


def test_looks_like_amount_never_raises() -> None:
    assert looks_like_amount("1,000")
    assert looks_like_amount(1000)
    assert not looks_like_amount("매출액")
    assert not looks_like_amount(None)
    assert not looks_like_amount(True)


# ---------------------------------------------------------------------------
# Values arriving from a spreadsheet library
# ---------------------------------------------------------------------------


def test_parse_amount_refuses_float() -> None:
    """Architecture §5: a float has already lost precision.

    Accepting it here would launder that loss into a Decimal in the middle of
    the pipeline, where nobody would see it. Callers holding a float must go
    through `decimal_from_spreadsheet_value`, which says so in its name.
    """
    with pytest.raises(AmountParseError):
        parse_amount(1234.56)  # type: ignore[arg-type]


def test_spreadsheet_float_becomes_the_displayed_decimal() -> None:
    """A spreadsheet stores doubles; recover the figure it displays."""
    assert decimal_from_spreadsheet_value(1234.56) == Decimal("1234.56")
    assert decimal_from_spreadsheet_value(1000000) == Decimal("1000000")
    assert decimal_from_spreadsheet_value(Decimal("7.5")) == Decimal("7.5")


def test_spreadsheet_float_conversion_avoids_binary_artefacts() -> None:
    """Decimal(float) would give 1234.5599999999999...; Decimal(str(float)) does not."""
    converted = decimal_from_spreadsheet_value(1234.56)

    assert str(converted) == "1234.56"
    # Decimal.from_float is the explicit API for the lossy conversion, and shows
    # what the naive route would have produced.
    assert converted != Decimal.from_float(1234.56)
    assert str(Decimal.from_float(1234.56)).startswith("1234.5599999")


@pytest.mark.parametrize("value", [True, False, "text", None, object()])
def test_non_numeric_spreadsheet_values_are_rejected(value: object) -> None:
    with pytest.raises(AmountParseError):
        decimal_from_spreadsheet_value(value)
