"""extend sign normalization for korean conventions

Adds `TRIANGLE_NEGATED` (△/▲, a Korean convention for negative figures) and
`INFERRED_FROM_SUBTOTAL` (sign derived by testing one structural hypothesis
against the statement's own printed subtotals) to the
`financial_statement_lines.sign_normalization` CHECK constraint.

Written by hand on purpose: **Alembic autogenerate does not diff
CheckConstraints**, so changing `app.domain.enums.SignNormalization` produces
no migration and `alembic check` reports no drift. The mismatch was caught by
`tests/test_enum_constraints.py`, which compares every generated constraint
against the live database. That test, not autogenerate, is the safeguard.

Revision ID: c23f659641c8
Revises: 1558c0538102
Create Date: 2026-09-19
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c23f659641c8"
down_revision: str | None = "1558c0538102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "financial_statement_lines"
# Short name: Alembic applies the metadata naming convention itself, so
# passing the fully-qualified name would prefix it a second time.
CONSTRAINT = "sign_normalization_valid"
COLUMN = "sign_normalization"

BEFORE = ("AS_IS", "EXPENSE_COLUMN_NEGATED", "NEGATED", "PARENTHESES_NEGATED")
AFTER = (
    "AS_IS",
    "EXPENSE_COLUMN_NEGATED",
    "INFERRED_FROM_SUBTOTAL",
    "NEGATED",
    "PARENTHESES_NEGATED",
    "TRIANGLE_NEGATED",
)


def _replace_check(values: tuple[str, ...]) -> None:
    allowed = ", ".join(f"'{value}'" for value in values)
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(CONSTRAINT, TABLE, f"{COLUMN} IN ({allowed})")


def upgrade() -> None:
    _replace_check(AFTER)


def downgrade() -> None:
    # Rows holding a value the narrower set forbids must be migrated before the
    # old constraint can be restored, or the downgrade fails on live data.
    op.execute(
        "UPDATE financial_statement_lines SET sign_normalization = 'NEGATED' "
        "WHERE sign_normalization IN ('TRIANGLE_NEGATED', 'INFERRED_FROM_SUBTOTAL')"
    )
    _replace_check(BEFORE)
