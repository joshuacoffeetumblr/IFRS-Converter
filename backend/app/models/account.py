"""The canonical account dictionary (ERD §2, spec §4 step 3)."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, EnumText, TimestampMixin, UUIDPrimaryKeyMixin, enum_check
from app.domain.enums import AccountNature, StatementSection, SynonymMatchType


class NormalizedAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A canonical account such as ``INTEREST_INCOME``. Seeded, not user-specific."""

    __tablename__ = "normalized_accounts"
    __table_args__ = (
        enum_check("statement_section", StatementSection),
        enum_check("default_nature", AccountNature, nullable=True),
    )

    code: Mapped[str] = mapped_column(String(100), unique=True)
    label_ko: Mapped[str] = mapped_column(String(300))
    label_en: Mapped[str] = mapped_column(String(300))
    statement_section: Mapped[EnumText]
    #: Sanity check on sign during extraction. A contradiction raises an
    #: extraction warning; it never changes a classification (test vector T6).
    default_nature: Mapped[EnumText | None]
    parent_code: Mapped[str | None] = mapped_column(
        ForeignKey("normalized_accounts.code", ondelete="SET NULL")
    )
    #: Aggregate buckets such as 기타수익 that always need human attention,
    #: because their contents cannot be inferred from the caption alone.
    ambiguous_by_default: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)

    synonyms: Mapped[list[AccountSynonym]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class AccountSynonym(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Surface forms mapping to a canonical account.

    Kept in its own table so the dictionary grows without schema change:
    매출액 / 매출 / Revenue / 수익(매출액) all resolve to ``REVENUE``.
    """

    __tablename__ = "account_synonyms"
    __table_args__ = (
        enum_check("match_type", SynonymMatchType),
        UniqueConstraint("synonym", "locale"),
        CheckConstraint("locale IN ('ko', 'en')", name="locale_valid"),
    )

    normalized_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("normalized_accounts.id", ondelete="CASCADE")
    )
    synonym: Mapped[str] = mapped_column(String(300), index=True)
    locale: Mapped[str] = mapped_column(String(2))
    #: ``NORMALIZED`` compares after stripping whitespace, folding full-width to
    #: half-width, removing parenthetical suffixes and case-folding.
    match_type: Mapped[EnumText] = mapped_column(default=SynonymMatchType.NORMALIZED.value)

    account: Mapped[NormalizedAccount] = relationship(back_populates="synonyms")
