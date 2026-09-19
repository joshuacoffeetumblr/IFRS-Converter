"""Writing the audit trail (spec §8).

A thin helper on purpose. The value of an audit trail is that it is written on
every path that changes something, so the call has to be cheap enough that
nobody is tempted to skip it.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ActorType, AuditAction
from app.models import AuditLog


async def record(
    session: AsyncSession,
    *,
    action: AuditAction,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    actor_type: ActorType = ActorType.USER,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        project_id=project_id,
        actor_user_id=actor_user_id,
        actor_type=actor_type,
        before=before,
        after=after,
        request_id=request_id,
    )
    session.add(entry)
    await session.flush()
    return entry
