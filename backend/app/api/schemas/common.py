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

#: A monetary value. Serialized in plain notation — ``format(v, "f")`` rather
#: than ``str(v)`` — so a large or small figure never reaches the client in
#: scientific notation, which a decimal parser would have to special-case.
Money = Annotated[
    Decimal,
    PlainSerializer(lambda value: format(value, "f"), return_type=str, when_used="json"),
]

#: A ratio or percentage. Same reasoning as Money.
Ratio = Annotated[
    Decimal,
    PlainSerializer(lambda value: format(value, "f"), return_type=str, when_used="json"),
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
