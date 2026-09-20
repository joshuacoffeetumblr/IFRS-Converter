"""Shared response types.

The money rule is the important one. **Monetary values cross the API as JSON
strings, never as numbers.** JSON numbers are IEEE-754 doubles in every
mainstream parser, so a ``NUMERIC(38, 6)`` figure cannot survive the trip — and
silent precision loss in a tool whose central invariant is exact reconciliation
is not a trade-off worth making. The frontend parses these with a decimal
library and only formats them; it never computes (architecture §13).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, PlainSerializer

from app.db.base import MONEY_SCALE, RATIO_SCALE


def _fixed(value: Decimal, scale: int) -> str:
    """Plain notation, always at the stored scale.

    Two things this avoids. Scientific notation, which a decimal parser on the
    other side would have to special-case — so ``format`` rather than ``str``.
    And a figure whose text changes with how it was produced: a total summed
    from nothing is ``Decimal(0)``, and rendering that as "0" beside
    "-70000.000000" invites a client to compare the two as strings and see a
    difference that is not there.

    ``format`` rather than ``quantize``: a full-precision ``numeric(38, 6)``
    value exceeds the default decimal context's 28 digits, and ``quantize``
    raises on it. Formatting is exact at any size.
    """
    return format(value, f".{scale}f")


#: A monetary value, at the scale it is stored at (``numeric(38, 6)``).
Money = Annotated[
    Decimal,
    PlainSerializer(lambda value: _fixed(value, MONEY_SCALE), return_type=str, when_used="json"),
]

#: A ratio or percentage. Same reasoning as Money, at ``numeric(5, 4)``.
Ratio = Annotated[
    Decimal,
    PlainSerializer(lambda value: _fixed(value, RATIO_SCALE), return_type=str, when_used="json"),
]


class ApiModel(BaseModel):
    """Base for every response body."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


T = TypeVar("T")


class Page(ApiModel, Generic[T]):
    items: list[T]
    #: Opaque; clients pass it back rather than constructing one.
    next_cursor: str | None = None
    total: int | None = None
