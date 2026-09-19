"""Impact analysis snapshots and exports (ERD §2)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, EnumText, TimestampMixin, UUIDPrimaryKeyMixin, enum_check
from app.domain.enums import ExportFormat, ExportStatus, ReconciliationStatus


class ImpactAnalysis(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A computed snapshot, stored so an export is reproducible.

    Deliberately a table and not a view: an exported PDF must stay
    byte-for-byte reproducible, and a view would silently change when a rule or
    a dictionary entry is edited.

    ``kpis`` and ``waterfall`` are ``jsonb`` rather than relational rows because
    they are a derived, versioned report artefact — always read whole, never
    queried field by field. Normalizing them would add joins and buy nothing.
    """

    __tablename__ = "impact_analyses"
    __table_args__ = (
        enum_check("reconciliation_status", ReconciliationStatus),
        Index("ix_impact_current", "project_id", "is_current"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    computed_at: Mapped[dt.datetime]
    rule_set_version: Mapped[str | None] = mapped_column(String(50))
    config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    reconciliation_status: Mapped[EnumText]
    #: Every check with its expected value, actual value and delta.
    reconciliation_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    kpis: Mapped[dict[str, Any]] = mapped_column(JSONB)
    waterfall: Mapped[dict[str, Any]] = mapped_column(JSONB)
    #: Limitations attached to this run, e.g. an aggregate caption the user
    #: accepted without decomposing (Q5) — surfaced, never buried.
    limitations: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    is_current: Mapped[bool] = mapped_column(default=True)


class Export(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "exports"
    __table_args__ = (
        enum_check("format", ExportFormat),
        enum_check("status", ExportStatus),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    #: Exactly which snapshot was exported.
    impact_analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("impact_analyses.id", ondelete="SET NULL")
    )
    format: Mapped[EnumText]
    storage_key: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[EnumText] = mapped_column(default=ExportStatus.PENDING.value)
    error: Mapped[str | None] = mapped_column(Text)
    #: True when produced from an unreconciled project; such a file is
    #: watermarked "UNRECONCILED — DO NOT RELY ON" (spec §19).
    is_watermarked_unreconciled: Mapped[bool] = mapped_column(default=False)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[dt.datetime | None]
