"""Every enum-backed CHECK constraint must match its Python enum.

This test exists because **Alembic autogenerate does not diff
CheckConstraints**. Adding a member to an enum in ``app.domain.enums`` changes
the constraint SQL that ``enum_check`` emits, but ``alembic revision
--autogenerate`` produces nothing and ``alembic check`` reports no drift. The
database silently keeps rejecting the new value.

That was found the hard way while adding ``SignNormalization.TRIANGLE_NEGATED``
in Phase 3. So the safeguard is here, not in autogenerate: this test reads the
constraint definitions out of PostgreSQL and compares them to the enums.
"""

from __future__ import annotations

import re
from enum import StrEnum

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import app.models  # noqa: F401  registers every table
from app.db.base import Base

#: Matches the quoted literals inside a rendered CHECK constraint.
_LITERAL = re.compile(r"'([^']*)'")


def _enum_constraints() -> list[tuple[str, str, str, type[StrEnum]]]:
    """(table, constraint_name, column, enum_cls) for every generated check."""
    found: list[tuple[str, str, str, type[StrEnum]]] = []
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            enum_cls = constraint.info.get("enum_cls")
            if enum_cls is None:
                continue
            column = constraint.info["enum_column"]
            # The metadata naming convention has already been applied to .name.
            found.append((table.name, str(constraint.name), column, enum_cls))
    return sorted(found)


def test_registry_is_populated() -> None:
    """A guard that finds nothing proves nothing."""
    constraints = _enum_constraints()

    assert len(constraints) >= 30, f"expected many enum constraints, found {len(constraints)}"


@pytest.mark.parametrize(
    ("table", "constraint_name", "column", "enum_cls"),
    _enum_constraints(),
    ids=lambda v: v if isinstance(v, str) else "",
)
async def test_database_check_matches_enum(
    db_session: AsyncSession,
    table: str,
    constraint_name: str,
    column: str,
    enum_cls: type[StrEnum],
) -> None:
    row = (
        await db_session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = :name AND conrelid = CAST(:table AS regclass)"
            ),
            {"name": constraint_name, "table": table},
        )
    ).scalar_one_or_none()

    assert row is not None, (
        f"{table}.{column}: constraint {constraint_name} is missing from the database"
    )

    in_database = set(_LITERAL.findall(row))
    in_python = {member.value for member in enum_cls}

    assert in_database == in_python, (
        f"{table}.{column} is out of sync with {enum_cls.__name__}.\n"
        f"  missing from database: {sorted(in_python - in_database)}\n"
        f"  stale in database:     {sorted(in_database - in_python)}\n"
        "Alembic autogenerate does not diff CheckConstraints — write the "
        "migration by hand."
    )
