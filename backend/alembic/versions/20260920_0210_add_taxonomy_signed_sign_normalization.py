"""add TAXONOMY_SIGNED sign normalization

An XBRL filing reports a deduction as a positive figure and leaves the sign to
the taxonomy — the concept's own nature says whether it is income or expense.
That is a *different* provenance from every value already allowed here: the
others record a guess this product made about a printed page, and this one
records that no guess was needed. Collapsing it into `NEGATED` would lose
exactly the distinction an auditor asks about (spec §8).

Written by hand on purpose: **Alembic autogenerate does not diff
CheckConstraints**, so changing `app.domain.enums.SignNormalization` produces
no migration and `alembic check` reports no drift. `tests/test_enum_constraints.py`
compares every generated constraint against the live database, and that test,
not autogenerate, is the safeguard.

Revision ID: 6b1e0a4d9c37
Revises: 3c4dae46fbeb
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "6b1e0a4d9c37"
down_revision: str | None = "3c4dae46fbeb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "financial_statement_lines"
# Short name: Alembic applies the metadata naming convention itself, so
# passing the fully-qualified name would prefix it a second time.
CONSTRAINT = "sign_normalization_valid"
COLUMN = "sign_normalization"

BEFORE = (
    "AS_IS",
    "EXPENSE_COLUMN_NEGATED",
    "INFERRED_FROM_SUBTOTAL",
    "NEGATED",
    "PARENTHESES_NEGATED",
    "TRIANGLE_NEGATED",
)
AFTER = tuple(sorted((*BEFORE, "TAXONOMY_SIGNED")))


def _replace_check(values: tuple[str, ...]) -> None:
    allowed = ", ".join(f"'{value}'" for value in values)
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(CONSTRAINT, TABLE, f"{COLUMN} IN ({allowed})")


def upgrade() -> None:
    _replace_check(AFTER)


def downgrade() -> None:
    # Rows holding a value the narrower set forbids must be migrated before the
    # old constraint can be restored, or the downgrade fails on live data.
    # `NEGATED` is the closest older meaning: the figure was printed positive
    # and stored negative.
    op.execute(
        "UPDATE financial_statement_lines SET sign_normalization = 'NEGATED' "
        "WHERE sign_normalization = 'TAXONOMY_SIGNED'"
    )
    _replace_check(BEFORE)
