"""Companies and their business activities (ERD §2, spec §10)."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, EnumText, TimestampMixin, UUIDPrimaryKeyMixin, enum_check
from app.domain.enums import ActivitySource, ActivityType, IdentifierScheme


class Company(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The reporting entity.

    Separate from ``projects`` because main business activities are a property
    of the entity, reusable across reporting periods.
    """

    __tablename__ = "companies"
    __table_args__ = (enum_check("identifier_scheme", IdentifierScheme, nullable=True),)

    name: Mapped[str] = mapped_column(String(300))
    identifier: Mapped[str | None] = mapped_column(String(100))
    identifier_scheme: Mapped[EnumText | None]
    jurisdiction: Mapped[str] = mapped_column(String(2), default="KR")
    #: Informational only. Spec §10 forbids inferring main business activities
    #: from an industry code; they are always user-confirmed.
    industry_code: Mapped[str | None] = mapped_column(String(50))

    activities: Mapped[list[BusinessActivity]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class BusinessActivity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A declared business activity (spec §10).

    ``is_main_business_activity`` is deliberately **three-valued**:

    * ``NULL``  — unknown. Blocks a ``NEEDS_FACT`` rule and raises a question.
    * ``True``  — the user confirmed it is a main business activity.
    * ``False`` — the user confirmed it is not.

    ``NULL`` and ``False`` must never be conflated: only ``NULL`` blocks.
    """

    __tablename__ = "business_activities"
    __table_args__ = (
        enum_check("activity_type", ActivityType),
        enum_check("source", ActivitySource),
        UniqueConstraint("company_id", "project_id", "activity_type"),
        # Spec §10: only a human may confirm a main business activity. An AI
        # suggestion may create the row, but cannot mark it confirmed.
        CheckConstraint(
            "NOT (confirmed_by_user AND source = 'AI_SUGGESTED')",
            name="ai_cannot_self_confirm",
        ),
        CheckConstraint(
            "NOT confirmed_by_user OR confirmed_at IS NOT NULL",
            name="confirmed_requires_timestamp",
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    #: Confirmation may be scoped to one project rather than the whole entity.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    activity_type: Mapped[EnumText]
    is_main_business_activity: Mapped[bool | None] = mapped_column(Boolean)
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[EnumText]
    confirmed_by_user: Mapped[bool] = mapped_column(default=False)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[dt.datetime | None]

    company: Mapped[Company] = relationship(back_populates="activities")
