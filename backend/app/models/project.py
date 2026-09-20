"""Projects and uploaded files (ERD §2)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, EnumText, TimestampMixin, UUIDPrimaryKeyMixin, enum_check
from app.domain.enums import (
    ParseStatus,
    ProjectBasis,
    ProjectStatus,
    ReconciliationStatus,
    ScanStatus,
)
from app.models.company import Company


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One entity, one reporting period, one restructuring run."""

    __tablename__ = "projects"
    __table_args__ = (
        enum_check("basis", ProjectBasis),
        enum_check("status", ProjectStatus),
        enum_check("reconciliation_status", ReconciliationStatus),
        CheckConstraint("period_end >= period_start", name="period_ordered"),
        CheckConstraint(
            "status <> 'FINALIZED' OR reconciliation_status = 'PASSED'",
            name="finalized_requires_reconciliation",
        ),
        CheckConstraint(
            "status <> 'FINALIZED' OR finalized_at IS NOT NULL",
            name="finalized_requires_timestamp",
        ),
    )

    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    name: Mapped[str] = mapped_column(String(300))
    fiscal_year: Mapped[int]
    period_start: Mapped[dt.date]
    period_end: Mapped[dt.date]
    basis: Mapped[EnumText]
    presentation_currency: Mapped[str] = mapped_column(String(3), default="KRW")
    #: Power of ten the source is stated in: 6 means 백만원.
    presentation_scale: Mapped[int] = mapped_column(default=0)

    status: Mapped[EnumText] = mapped_column(default=ProjectStatus.DRAFT.value, index=True)
    reconciliation_status: Mapped[EnumText] = mapped_column(
        default=ReconciliationStatus.NOT_RUN.value
    )

    #: Pinned when classification runs, so a finalized project stays reproducible.
    rule_set_version: Mapped[str | None] = mapped_column(String(50))
    #: Thresholds and tolerances in force for this run. A later global change
    #: must not retroactively alter how an existing analysis explains itself.
    config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    finalized_at: Mapped[dt.datetime | None]
    deleted_at: Mapped[dt.datetime | None]

    uploaded_files: Mapped[list[UploadedFile]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    #: Eager, because the project list shows the entity's name on every row.
    #: Lazily loaded it would either raise under async or issue a query per
    #: row; `selectin` fetches every company in one further statement.
    company: Mapped[Company] = relationship(lazy="selectin")


class UploadedFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "uploaded_files"
    __table_args__ = (
        enum_check("scan_status", ScanStatus),
        enum_check("parse_status", ParseStatus),
        CheckConstraint("size_bytes > 0", name="size_positive"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    #: Retained for display only. Never used to build a filesystem path.
    original_filename: Mapped[str] = mapped_column(String(500))
    #: Opaque object-store key.
    storage_key: Mapped[str] = mapped_column(String(500))
    #: Sniffed server-side, never trusted from the client (spec §31).
    mime_type: Mapped[str] = mapped_column(String(200))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    scan_status: Mapped[EnumText] = mapped_column(default=ScanStatus.PENDING.value)
    parse_status: Mapped[EnumText] = mapped_column(default=ParseStatus.PENDING.value)
    parse_error: Mapped[str | None] = mapped_column(Text)
    #: Retention policy (spec §32).
    purge_after: Mapped[dt.datetime | None]
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    project: Mapped[Project] = relationship(back_populates="uploaded_files")
