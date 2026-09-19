"""No generated identifier may exceed PostgreSQL's 63-character limit.

SQLAlchemy truncates a longer name and appends a hash to keep it unique. The
constraint is still created and still enforced, so nothing is broken at the
database level — but it no longer answers to the name the application computes
for it. A hand-written migration that drops it by name fails, and a check that
looks it up finds nothing.

That matters here specifically: Alembic autogenerate does not diff
CheckConstraints, so every enum change needs a hand-written migration that
references constraints by name. Two real cases were found this way —
``outcome_when_fact_true_subcategory`` at 64 characters, and the original
foreign-key template, which pushed several keys past the limit.
"""

from __future__ import annotations

import pytest

import app.models  # noqa: F401  registers every table
from app.db.base import Base

#: PostgreSQL's NAMEDATALEN - 1.
MAX_IDENTIFIER_LENGTH = 63


def _named_objects() -> list[tuple[str, str]]:
    """(kind, name) for everything the schema names explicitly."""
    found: list[tuple[str, str]] = []
    for table in Base.metadata.tables.values():
        found.append(("table", table.name))
        for column in table.columns:
            found.append((f"column {table.name}", column.name))
        for constraint in table.constraints:
            if constraint.name:
                found.append((f"constraint {table.name}", str(constraint.name)))
        for index in table.indexes:
            if index.name:
                found.append((f"index {table.name}", str(index.name)))
    return found


def test_something_is_checked() -> None:
    """A guard that finds nothing proves nothing."""
    assert len(_named_objects()) > 100


@pytest.mark.parametrize(
    ("kind", "name"),
    _named_objects(),
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_identifier_fits_postgresql(kind: str, name: str) -> None:
    assert len(name) <= MAX_IDENTIFIER_LENGTH, (
        f"{kind}: {name!r} is {len(name)} characters. PostgreSQL truncates at "
        f"{MAX_IDENTIFIER_LENGTH} silently, leaving an object that cannot be "
        "found by the name the application computes. Shorten the column."
    )
