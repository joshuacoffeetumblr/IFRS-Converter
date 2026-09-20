"""Shared FastAPI dependencies."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import UnauthorizedError
from app.core.config import Settings
from app.core.security import InvalidTokenError, read_token
from app.db.session import get_session
from app.models import Project, User
from app.repositories.projects import UserRepository

#: `auto_error=False` so a missing header reaches our handler and returns a
#: problem document like every other failure, rather than FastAPI's own shape.
bearer_scheme = HTTPBearer(auto_error=False)


def get_request_settings(request: Request) -> Settings:
    """Resolve settings from application state, not from the process-wide cache.

    Reading the cached singleton directly would make ``create_app(settings=...)``
    silently inert, so tests and a running server could disagree about
    configuration — including the accounting thresholds in
    ``Settings.classification``. Resolving through app state keeps the factory
    argument authoritative.
    """
    return request.app.state.settings  # type: ignore[no-any-return]


SettingsDep = Annotated[Settings, Depends(get_request_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    settings: SettingsDep,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> User:
    if credentials is None:
        raise UnauthorizedError()

    try:
        claims = read_token(credentials.credentials, settings)
    except InvalidTokenError as exc:
        raise UnauthorizedError("The token is invalid or has expired.") from exc

    user = await UserRepository(session).by_id(claims.user_id)
    if user is None:
        # The token is well-formed but its subject no longer exists — a deleted
        # account, or a token minted against a different database.
        raise UnauthorizedError("The token is invalid or has expired.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def parse_uuid(value: str, resource: str) -> uuid.UUID:
    """Turn a path parameter into a UUID, or report the resource as missing.

    A malformed id is reported as 404 rather than 422 for the same reason
    another user's id is: the response must not distinguish "no such thing"
    from "not yours" or "not a valid id at all".
    """
    from app.api.errors import NotFoundError

    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise NotFoundError(resource) from exc


def ensure_not_finalized(project: Project) -> None:
    """Refuse a change to a finalized project.

    A finalized analysis is a statement somebody may already have sent
    somewhere. Changing what is under it silently would make the exported file
    and the project disagree, so a correction has to start by reopening.
    """
    from app.api.errors import ConflictError
    from app.domain.enums import ProjectStatus

    if project.status == ProjectStatus.FINALIZED:
        raise ConflictError(
            title="Project is finalized",
            code="project-finalized",
            detail="Reopen the project before changing it.",
        )
