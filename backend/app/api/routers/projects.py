"""Projects (API spec §2)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, SessionDep, parse_uuid
from app.api.errors import NotFoundError
from app.api.schemas.common import Page
from app.api.schemas.project import (
    CompanyResponse,
    CreateProjectRequest,
    ProjectResponse,
    ProjectSummary,
)
from app.domain.enums import ProjectStatus
from app.models import Company, Project
from app.repositories.projects import ProjectRepository
from app.services.projects import create_project, delete_project, progress_for

router = APIRouter(prefix="/projects", tags=["projects"])


async def _load(session: SessionDep, project_id: str, user: CurrentUser) -> Project:
    """Fetch a project the caller owns, or report it as missing.

    Ownership is applied in the repository query, so a project belonging to
    someone else is indistinguishable from one that does not exist.
    """
    project = await ProjectRepository(session).by_id(parse_uuid(project_id, "Project"), user.id)
    if project is None:
        raise NotFoundError("Project")
    return project


async def _to_response(
    session: SessionDep, project: Project, *, with_progress: bool = True
) -> ProjectResponse:
    company = await session.get(Company, project.company_id)
    assert company is not None  # a project cannot exist without its company
    response = ProjectResponse(
        id=project.id,
        name=project.name,
        company=CompanyResponse.model_validate(company),
        fiscal_year=project.fiscal_year,
        period_start=project.period_start,
        period_end=project.period_end,
        basis=project.basis,
        presentation_currency=project.presentation_currency,
        presentation_scale=project.presentation_scale,
        status=project.status,
        reconciliation_status=project.reconciliation_status,
        rule_set_version=project.rule_set_version,
        created_at=project.created_at,
        updated_at=project.updated_at,
        finalized_at=project.finalized_at,
    )
    if with_progress:
        response.progress = await progress_for(session, project)
    return response


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create(
    payload: CreateProjectRequest, session: SessionDep, user: CurrentUser
) -> ProjectResponse:
    project = await create_project(session, payload=payload, owner=user)
    return await _to_response(session, project)


@router.get("", response_model=Page[ProjectSummary])
async def list_projects(
    session: SessionDep,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    project_status: Annotated[ProjectStatus | None, Query(alias="status")] = None,
) -> Page[ProjectSummary]:
    projects = await ProjectRepository(session).list_for_owner(
        user.id, limit=limit, status=project_status.value if project_status else None
    )
    return Page[ProjectSummary](
        items=[ProjectSummary.model_validate(item) for item in projects],
        total=await ProjectRepository(session).count_for_owner(user.id),
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, session: SessionDep, user: CurrentUser) -> ProjectResponse:
    return await _to_response(session, await _load(session, project_id, user))


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove(project_id: str, session: SessionDep, user: CurrentUser) -> Response:
    project = await _load(session, project_id, user)
    await delete_project(session, project=project, actor_id=user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
