"""The audit trail, read back (spec §8).

An audit trail nobody can read is not an audit trail. Everything that changed
a project — the upload, the extraction, each classification run, every answer
and every override — is listed here in the order it happened, with who did it.

Figures appear only as the before/after of a change that was made, which is
what an audit record is for; nothing here reproduces the statement itself.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, SessionDep, parse_uuid
from app.api.errors import NotFoundError
from app.api.schemas.audit import AuditEntryResponse, AuditLogResponse
from app.domain.enums import AuditAction
from app.models import AuditLog, Project
from app.repositories.projects import ProjectRepository

router = APIRouter(tags=["audit"])


async def _project(session: SessionDep, project_id: str, user: CurrentUser) -> Project:
    project = await ProjectRepository(session).by_id(parse_uuid(project_id, "Project"), user.id)
    if project is None:
        raise NotFoundError("Project")
    return project


@router.get("/projects/{project_id}/audit-logs", response_model=AuditLogResponse)
async def list_audit_logs(
    project_id: str,
    session: SessionDep,
    user: CurrentUser,
    action: Annotated[AuditAction | None, Query()] = None,
    entity_type: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditLogResponse:
    """Oldest first, because an audit trail is read as a narrative.

    Paginated by offset rather than by cursor: the rows are append-only and a
    reader works forward through them, so the usual objection to an offset —
    that rows shift under it — does not arise here.
    """
    project = await _project(session, project_id, user)

    filters = [AuditLog.project_id == project.id]
    if action:
        filters.append(AuditLog.action == action.value)
    if entity_type:
        filters.append(AuditLog.entity_type == entity_type)

    total = int(
        (
            await session.execute(select(func.count()).select_from(AuditLog).where(*filters))
        ).scalar_one()
    )
    rows = (
        (
            await session.execute(
                select(AuditLog)
                .where(*filters)
                .order_by(AuditLog.occurred_at, AuditLog.id)
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return AuditLogResponse(
        items=[AuditEntryResponse.model_validate(row) for row in rows],
        total=total,
    )
