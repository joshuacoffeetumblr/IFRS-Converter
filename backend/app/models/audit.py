"""Append-only audit trail (ERD §2, spec §8).

The application database role is granted INSERT and SELECT only on this table;
no UPDATE, no DELETE. That grant is applied by the migration.

This table is the one intentional, access-controlled place where monetary
detail may be recorded. Application logs redact it (spec §32, see
``app.core.logging``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, EnumText, enum_check
from app.domain.enums import ActorType, AuditAction


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        enum_check("actor_type", ActorType),
        enum_check("action", AuditAction),
        Index("ix_audit_project_time", "project_id", "occurred_at"),
        Index("ix_audit_entity", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    #: Null for system actions.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    actor_type: Mapped[EnumText]
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL")
    )
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[uuid.UUID | None]
    action: Mapped[EnumText]
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: Correlates with structured application logs.
    request_id: Mapped[str | None] = mapped_column(String(100))
