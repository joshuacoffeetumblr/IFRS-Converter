"""Registration and sign-in (spec §31)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import (
    hash_password,
    issue_token,
    verify_password_constant_work,
)
from app.domain.enums import ActorType, AuditAction, UserRole
from app.models import User
from app.repositories.projects import UserRepository
from app.services import audit


class RegistrationError(ValueError):
    """The account could not be created."""


class AuthenticationError(ValueError):
    """The credentials were not accepted."""


async def register(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str | None,
    settings: Settings,
) -> User:
    minimum = settings.auth.min_password_length
    if len(password) < minimum:
        raise RegistrationError(f"Password must be at least {minimum} characters.")

    users = UserRepository(session)
    if await users.by_email(email) is not None:
        raise RegistrationError("An account with that email already exists.")

    user = User(
        email=email,
        display_name=display_name,
        role=UserRole.OWNER,
        password_hash=hash_password(password),
    )
    try:
        await users.add(user)
    except IntegrityError as exc:  # pragma: no cover - race with a concurrent signup
        raise RegistrationError("An account with that email already exists.") from exc

    await audit.record(
        session,
        action=AuditAction.CREATED,
        entity_type="users",
        entity_id=user.id,
        actor_user_id=user.id,
        actor_type=ActorType.USER,
    )
    return user


async def authenticate(
    session: AsyncSession, *, email: str, password: str, settings: Settings
) -> tuple[str, dt.datetime, User]:
    """Verify credentials and issue a token.

    A missing account and a wrong password produce the same error, and the
    same hashing work is done either way, so neither the message nor the
    response time reveals which addresses are registered.
    """
    user = await UserRepository(session).by_email(email)
    accepted = verify_password_constant_work(password, user.password_hash if user else None)
    if user is None or not accepted:
        raise AuthenticationError("Email or password is incorrect.")

    token, expires_at = issue_token(user.id, settings)
    return token, expires_at, user
