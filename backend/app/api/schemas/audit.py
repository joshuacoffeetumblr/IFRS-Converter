"""Audit trail payloads (spec §8)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import Field

from app.api.schemas.common import ApiModel
from app.domain.enums import ActorType, AuditAction


class AuditEntryResponse(ApiModel):
    id: int
    occurred_at: dt.datetime
    action: AuditAction
    entity_type: str
    entity_id: uuid.UUID | None = None
    #: Null for system actions, such as a question a rule raised.
    actor_user_id: uuid.UUID | None = None
    actor_type: ActorType
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    #: Correlates with the structured application logs.
    request_id: str | None = None


class AuditLogResponse(ApiModel):
    items: list[AuditEntryResponse] = Field(default_factory=list)
    total: int = 0
