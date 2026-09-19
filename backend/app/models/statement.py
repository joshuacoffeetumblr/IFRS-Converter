"""Financial statements and their extracted lines (ERD §2).

These tables carry three correctness-critical invariants:

1. **Sign convention** (architecture §5). ``amount`` is always the signed effect
   on profit or loss: income positive, expense negative. Every category subtotal
   is then a plain sum with no per-account sign logic anywhere.
2. **Subtotal exclusion.** Rows that are subtotals in the source are retained as
   reconciliation targets and excluded from all summation.
3. **Decomposition** (open question Q5). A parent caption that has been broken
   into components is likewise excluded, so components are counted once.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, EnumText, Ratio, TimestampMixin, UUIDPrimaryKeyMixin, enum_check
from app.domain.enums import (
    DecompositionStatus,
    NormalizationMethod,
    SignNormalization,
    StatementType,
    SubtotalKind,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from app.models.account import NormalizedAccount


class FinancialStatement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One statement, for one period, found in one uploaded file.

    The MVP restructures ``INCOME_STATEMENT`` only. Other types are accepted so
    that extraction validation (spec §17) can check balance-sheet identities.
    """

    __tablename__ = "financial_statements"
    __table_args__ = (enum_check("statement_type", StatementType),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    uploaded_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("uploaded_files.id", ondelete="SET NULL")
    )
    statement_type: Mapped[EnumText]
    period_start: Mapped[dt.date | None]
    period_end: Mapped[dt.date | None]
    #: A prior-period comparative column, classified independently of the
    #: current period (test vector T12).
    is_comparative: Mapped[bool] = mapped_column(default=False)
    currency: Mapped[str] = mapped_column(String(3), default="KRW")
    scale: Mapped[int] = mapped_column(default=0)
    source_locator: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    lines: Mapped[list[FinancialStatementLine]] = relationship(
        back_populates="statement",
        cascade="all, delete-orphan",
        order_by="FinancialStatementLine.ordinal",
    )


class FinancialStatementLine(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An extracted fact, with provenance back to the exact source cell.

    Treated as immutable once written: a correction creates a new extraction run
    rather than mutating history.
    """

    __tablename__ = "financial_statement_lines"
    __table_args__ = (
        enum_check("sign_normalization", SignNormalization),
        enum_check("subtotal_kind", SubtotalKind, nullable=True),
        enum_check("decomposition_status", DecompositionStatus),
        enum_check("normalization_method", NormalizationMethod, nullable=True),
        UniqueConstraint("statement_id", "ordinal"),
        Index("ix_fsl_statement_summable", "statement_id", "is_subtotal", "decomposition_status"),
        # A subtotal is a reconciliation target, never a classifiable fact.
        CheckConstraint(
            "NOT is_subtotal OR normalized_account_id IS NULL",
            name="subtotal_has_no_account",
        ),
        CheckConstraint(
            "is_subtotal OR subtotal_kind IS NULL",
            name="subtotal_kind_requires_subtotal",
        ),
        # A subtotal cannot also be an aggregate awaiting decomposition.
        CheckConstraint(
            "NOT is_subtotal OR decomposition_status = 'NOT_REQUIRED'",
            name="subtotal_not_decomposable",
        ),
        # A decomposed parent is a container, not a component.
        CheckConstraint(
            "decomposition_status <> 'DECOMPOSED' OR parent_line_id IS NULL",
            name="decomposed_parent_is_not_a_child",
        ),
        CheckConstraint("ordinal >= 0", name="ordinal_non_negative"),
        CheckConstraint("depth >= 0", name="depth_non_negative"),
    )

    statement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("financial_statements.id", ondelete="CASCADE")
    )
    #: Presentation order in the source document.
    ordinal: Mapped[int]
    #: Indentation level in the source document.
    depth: Mapped[int] = mapped_column(default=0)

    #: Exactly as printed, e.g. ``이자수익``.
    raw_label: Mapped[str] = mapped_column(String(500))
    #: Exactly as printed, e.g. ``(2,000)``. Kept so the transformation into
    #: ``amount`` stays auditable.
    raw_value: Mapped[str] = mapped_column(String(100))
    #: **Signed effect on profit or loss** (architecture §5).
    amount: Mapped[Decimal]
    sign_normalization: Mapped[EnumText] = mapped_column(default=SignNormalization.AS_IS.value)

    #: Excluded from all summation; retained as a reconciliation target.
    is_subtotal: Mapped[bool] = mapped_column(default=False)
    subtotal_kind: Mapped[EnumText | None]

    #: Set on a component produced by decomposing an aggregate caption (Q5).
    parent_line_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("financial_statement_lines.id", ondelete="CASCADE")
    )
    decomposition_status: Mapped[EnumText] = mapped_column(
        default=DecompositionStatus.NOT_REQUIRED.value
    )

    normalized_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("normalized_accounts.id", ondelete="SET NULL")
    )
    normalization_method: Mapped[EnumText | None]
    normalization_score: Mapped[Ratio | None]
    #: Eagerly loaded. A response reports the account by its code, and a lazy
    #: load on attribute access inside an async request raises rather than
    #: quietly emitting a query.
    normalized_account: Mapped[NormalizedAccount | None] = relationship(lazy="selectin")

    #: The item's presentation bucket as reported, e.g. ``영업외수익``. Used to
    #: compute the operating-profit bridge, not to decide IFRS 18 category.
    current_category: Mapped[str | None] = mapped_column(String(100))
    #: Provenance (spec §18): sheet/row/column for XLSX, page/bbox for PDF.
    source_locator: Mapped[dict[str, Any]] = mapped_column(JSONB)
    note_references: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    statement: Mapped[FinancialStatement] = relationship(back_populates="lines")
    children: Mapped[list[FinancialStatementLine]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
        remote_side=None,
    )
    parent: Mapped[FinancialStatementLine | None] = relationship(
        back_populates="children",
        remote_side="FinancialStatementLine.id",
    )

    @property
    def is_summable(self) -> bool:
        """Whether this line contributes to category totals.

        Subtotals are reconciliation targets, and a decomposed parent is a
        container whose components carry the amounts. Including either would
        double count.
        """
        return not self.is_subtotal and (
            self.decomposition_status != DecompositionStatus.DECOMPOSED
        )
