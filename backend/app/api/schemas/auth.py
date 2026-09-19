"""Authentication payloads."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import EmailStr, Field

from app.api.schemas.common import ApiModel
from app.domain.enums import UserRole


class RegisterRequest(ApiModel):
    email: EmailStr
    #: Length is checked against configuration in the service, not fixed here,
    #: so the policy lives in one place (spec §29).
    password: str = Field(min_length=1, max_length=256)
    display_name: str | None = Field(default=None, max_length=200)


class LoginRequest(ApiModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(ApiModel):
    access_token: str
    # S105 is a false positive: this is the OAuth 2.0 token type, not a secret.
    token_type: str = "bearer"  # noqa: S105
    expires_at: dt.datetime


class UserResponse(ApiModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    role: UserRole
