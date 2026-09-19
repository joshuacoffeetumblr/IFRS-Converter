"""Declarative base and shared column types.

Architecture §5: every monetary column is ``NUMERIC(38, 6)`` and maps to
``decimal.Decimal``. No binary float is permitted anywhere near an amount.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated

from sqlalchemy import DateTime, MetaData, Numeric, func, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: Explicit naming convention so Alembic autogenerate produces stable,
#: reversible migration names for constraints and indexes.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

MONEY_PRECISION = 38
MONEY_SCALE = 6

Money = Annotated[Decimal, mapped_column(Numeric(MONEY_PRECISION, MONEY_SCALE))]
Ratio = Annotated[Decimal, mapped_column(Numeric(5, 4))]


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {  # noqa: RUF012
        Decimal: Numeric(MONEY_PRECISION, MONEY_SCALE),
        uuid.UUID: postgresql.UUID(as_uuid=True),
        dt.datetime: DateTime(timezone=True),
    }


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        server_default=func.now(),
        onupdate=func.now(),
    )
