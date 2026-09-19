"""Parsing printed figures into signed profit-or-loss effects.

Architecture §5: every amount downstream is the **signed effect on profit or
loss** — income positive, expense negative — so that each category subtotal is
a plain sum with no per-account sign logic anywhere.

Getting here from a printed statement is not trivial. Korean statements use
several conventions for negatives, and many present every figure unsigned and
expect the reader to know which captions are deductions. This module handles
what can be read from the cell itself; deriving a sign the statement never
printed is `app.domain.extraction`'s job, and is only ever accepted when the
statement's own subtotals confirm it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.domain.enums import SignNormalization

#: Triangle marks denote a negative figure in many Korean statements:
#: U+25B3 △, U+25B2 ▲, U+25BD ▽, U+25BC ▼. Written as escapes because they are
#: easy to confuse with one another on sight.
_TRIANGLE_MARKS = "\u25b3\u25b2\u25bd\u25bc"

#: Characters stripped before parsing: thousands separators, currency marks,
#: whitespace of every kind, and the non-breaking spaces Excel exports produce.
_STRIP = str.maketrans(
    dict.fromkeys(
        ","  # thousands separator
        " "  # SPACE
        "\u00a0"  # NO-BREAK SPACE, common in Excel exports
        "\u2009"  # THIN SPACE
        "\u202f"  # NARROW NO-BREAK SPACE
        "\u20a9\u00a5$\u20ac"  # ₩ ¥ $ €
        "\uc6d0"  # 원
    )
)

#: A value that means "nil" rather than "unparseable".
_NIL_TOKENS = frozenset(
    {
        "",
        "-",
        "\u2013",  # EN DASH
        "\u2014",  # EM DASH
        "\u2015",  # HORIZONTAL BAR
        "0",
        "nil",
        "N/A",
        "n/a",
        ".",
    }
)

_NUMERIC = re.compile(r"^[+-]?\d+(\.\d+)?$")


class AmountParseError(ValueError):
    """The cell held something that is not a figure."""


@dataclass(frozen=True, slots=True)
class ParsedAmount:
    """A figure read from a cell, with how its sign was determined."""

    value: Decimal
    sign_normalization: SignNormalization
    #: True when the cell was blank or an explicit nil marker such as ``-``.
    is_nil: bool = False


def parse_amount(raw: str | int | Decimal | None) -> ParsedAmount:
    """Read a printed figure into a signed :class:`~decimal.Decimal`.

    Recognised negative conventions:

    ======================  ===========  ==============================
    Printed                 Value        ``sign_normalization``
    ======================  ===========  ==============================
    ``100,000``             ``100000``   ``AS_IS``
    ``-70,000``             ``-70000``   ``AS_IS``
    ``(70,000)``            ``-70000``   ``PARENTHESES_NEGATED``
    ``△70,000``             ``-70000``   ``TRIANGLE_NEGATED``
    ``(-70,000)``           ``-70000``   ``PARENTHESES_NEGATED``
    ======================  ===========  ==============================

    A blank cell, ``-`` or ``N/A`` yields zero with ``is_nil`` set, so a caller
    can tell "nothing printed" from "printed zero" — they differ when checking
    whether a statement omitted a line or reported it as nil.

    ``float`` is deliberately **not** accepted: binary floating point cannot
    represent decimal money exactly, and the reconciliation gate asserts exact
    equality. A caller holding a float has already lost precision and must say
    so rather than have it silently laundered into a ``Decimal`` here.
    """
    if raw is None:
        return ParsedAmount(Decimal(0), SignNormalization.AS_IS, is_nil=True)

    if isinstance(raw, Decimal):
        return ParsedAmount(raw, SignNormalization.AS_IS)

    if isinstance(raw, bool):  # bool is an int subclass; never a figure
        raise AmountParseError(f"not a figure: {raw!r}")

    if isinstance(raw, float):
        # Refused on purpose. Falling through to str(raw) would quietly convert
        # an already-imprecise double into a Decimal, hiding the loss. A caller
        # holding a spreadsheet float must say so by calling
        # `decimal_from_spreadsheet_value`.
        raise AmountParseError(
            "float is not accepted; use decimal_from_spreadsheet_value "
            f"for a spreadsheet value: {raw!r}"
        )

    if isinstance(raw, int):
        return ParsedAmount(Decimal(raw), SignNormalization.AS_IS)

    text = unicodedata.normalize("NFKC", str(raw)).strip()
    if text in _NIL_TOKENS:
        return ParsedAmount(Decimal(0), SignNormalization.AS_IS, is_nil=text != "0")

    negation = SignNormalization.AS_IS
    negate = False

    # Parentheses, including the full-width forms NFKC has already folded.
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
        negation = SignNormalization.PARENTHESES_NEGATED
        negate = True

    # Triangle marks may sit inside or outside the parentheses.
    if text and text[0] in _TRIANGLE_MARKS:
        text = text[1:].strip()
        if negation is SignNormalization.AS_IS:
            negation = SignNormalization.TRIANGLE_NEGATED
        negate = True

    cleaned = text.translate(_STRIP)
    if cleaned in _NIL_TOKENS:
        return ParsedAmount(Decimal(0), negation, is_nil=True)

    if not _NUMERIC.match(cleaned):
        raise AmountParseError(f"not a figure: {raw!r}")

    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:  # pragma: no cover - guarded by _NUMERIC
        raise AmountParseError(f"not a figure: {raw!r}") from exc

    if negate:
        # `(-70,000)` is negative, not positive: an explicit minus inside
        # parentheses is a redundant marker, not a double negation.
        value = -abs(value)

    return ParsedAmount(value, negation)


def decimal_from_spreadsheet_value(value: object) -> Decimal:
    """Convert a value a spreadsheet library returned into an exact Decimal.

    Spreadsheets store numbers as IEEE-754 doubles, so a cell displaying
    ``1,234.56`` comes back as the float ``1234.56`` — a value that is *already*
    not exactly 1234.56. Nothing can recover the decimal the author typed; the
    best available reconstruction is ``Decimal(str(value))``, which yields the
    shortest decimal that round-trips the double, and is therefore the same
    figure the spreadsheet displays.

    Doing this in one named, documented place keeps the conversion visible.
    ``parse_amount`` deliberately refuses floats so this narrowing cannot happen
    silently somewhere in the middle of the pipeline.

    Raises :class:`AmountParseError` for values that are not numbers at all,
    such as dates.
    """
    if isinstance(value, bool):
        raise AmountParseError(f"not a figure: {value!r}")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    raise AmountParseError(f"not a figure: {value!r}")


def looks_like_amount(raw: object) -> bool:
    """Whether a cell could be read as a figure. Never raises."""
    if isinstance(raw, bool):
        return False
    if isinstance(raw, int | float | Decimal):
        return True
    if not isinstance(raw, str):
        return False
    try:
        parse_amount(raw)
    except AmountParseError:
        return False
    return True
