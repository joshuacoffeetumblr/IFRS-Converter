"""Declarative base and shared column types.

Architecture §5: every monetary column is ``NUMERIC(38, 6)`` and maps to
``decimal.Decimal``. No binary float is permitted anywhere near an amount.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from sqlalchemy import CheckConstraint, DateTime, MetaData, Numeric, String, func, text
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


def enum_check(
    column: str,
    enum_cls: type[StrEnum],
    *,
    nullable: bool = False,
) -> CheckConstraint:
    """Build a ``CHECK`` constraint restricting ``column`` to ``enum_cls`` members.

    ERD §4.1: enum-like columns are ``text`` plus a ``CHECK`` rather than a
    PostgreSQL ``ENUM`` type, because spec §9 requires the classification
    taxonomy to stay extensible and altering a PG enum is awkward to migrate and
    to roll back. Generating the constraint from the Python enum keeps
    ``app.domain.enums`` the single source of truth: adding a member and running
    ``alembic revision --autogenerate`` produces the migration, and CI's
    ``alembic check`` fails if someone forgets.

    Members are sorted so the emitted SQL is stable and a reordered enum does not
    produce a spurious diff.

    **Alembic autogenerate does not diff CheckConstraints.** Adding an enum
    member therefore does *not* produce a migration automatically, and
    ``alembic check`` stays silent. The enum class is recorded on the
    constraint's ``info`` so ``tests/test_enum_constraints.py`` can compare every
    constraint against the live database and fail when a migration is missing.
    That test is the safeguard; autogenerate is not.
    """
    allowed = ", ".join(f"'{member.value}'" for member in sorted(enum_cls, key=lambda m: m.value))
    predicate = f"{column} IN ({allowed})"
    if nullable:
        predicate = f"{column} IS NULL OR {predicate}"
    constraint = CheckConstraint(predicate, name=f"{column}_valid")
    constraint.info["enum_cls"] = enum_cls
    constraint.info["enum_column"] = column
    constraint.info["enum_nullable"] = nullable
    return constraint


#: Enum-backed columns are stored as text (ERD §4.1). ``String`` rather than
#: ``Text`` so the length is documented, with the CHECK doing the real work.
EnumText = Annotated[str, mapped_column(String(64))]
