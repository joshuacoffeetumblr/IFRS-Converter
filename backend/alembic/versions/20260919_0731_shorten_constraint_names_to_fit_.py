"""shorten constraint names to fit postgres identifiers

SQLAlchemy truncates any identifier longer than 63 characters and appends a
hash to keep it unique. The constraint is still created and still enforced, so
nothing is broken at the database level — but it no longer answers to the name
the application computes for it. A hand-written migration that drops it by name
fails, and a check that looks it up finds nothing.

That matters here specifically because Alembic autogenerate does not diff
CheckConstraints, so every enum change needs a hand-written migration that
references constraints by name.

Two changes:

The foreign-key naming template drops the referred table, which adds length
without adding uniqueness: a column references exactly one table.

Keys are renamed by deriving the new name from the table and column rather than
from a hand-maintained list, so the migration stays correct if a key was added
between writing and running it. CHECK constraints need no rename — the only one
that was too long is created under its short name by the migration before this
one.

Revision ID: 3c4dae46fbeb
Revises: f4a67c7f8074
Create Date: 2026-09-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3c4dae46fbeb"
down_revision: str | None = "f4a67c7f8074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FOREIGN_KEYS = sa.text(
    """
    SELECT c.conrelid::regclass::text AS table_name,
           c.conname                  AS old_name,
           a.attname                  AS column_name
    FROM pg_constraint c
    JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
    WHERE c.contype = 'f'
      AND c.connamespace = 'public'::regnamespace
      AND k.ord = 1
    """
)


def _rename(table: str, old: str, new: str) -> None:
    if old == new:
        return
    op.execute(sa.text(f'ALTER TABLE {table} RENAME CONSTRAINT "{old}" TO "{new}"'))


def _rename_foreign_keys(template: str) -> None:
    connection = op.get_bind()
    for table_name, old_name, column_name in connection.execute(_FOREIGN_KEYS).all():
        _rename(table_name, old_name, template.format(table=table_name, column=column_name))


def upgrade() -> None:
    _rename_foreign_keys("fk_{table}_{column}")


def downgrade() -> None:
    # The old template included the referred table, so it is reconstructed from
    # the catalog rather than guessed.
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT c.conrelid::regclass::text,
                   c.conname,
                   a.attname,
                   c.confrelid::regclass::text
            FROM pg_constraint c
            JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
            WHERE c.contype = 'f'
              AND c.connamespace = 'public'::regnamespace
              AND k.ord = 1
            """
        )
    ).all()
    for table_name, old_name, column_name, referred in rows:
        _rename(table_name, old_name, f"fk_{table_name}_{column_name}_{referred}")
