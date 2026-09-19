"""Registration and sign-in."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.api.errors import ConflictError, UnauthorizedError
from app.api.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.services.auth import (
    AuthenticationError,
    RegistrationError,
    authenticate,
    register,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_account(
    payload: RegisterRequest, session: SessionDep, settings: SettingsDep
) -> UserResponse:
    try:
        user = await register(
            session,
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
            settings=settings,
        )
    except RegistrationError as exc:
        raise ConflictError(
            title="Registration failed", code="registration-failed", detail=str(exc)
        ) from exc
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, session: SessionDep, settings: SettingsDep) -> TokenResponse:
    try:
        token, expires_at, _user = await authenticate(
            session, email=payload.email, password=payload.password, settings=settings
        )
    except AuthenticationError as exc:
        raise UnauthorizedError(str(exc)) from exc
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)
