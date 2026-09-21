"""RFC 9457 problem responses.

Every failure leaves the API in the same shape, so a client never has to guess
whether an error body carries ``detail``, ``message`` or ``error``. Accounting
failures in particular carry structured data — which check failed, by how much —
because "could not reconcile" without the numbers is not actionable.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

log = get_logger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"
ERROR_BASE = "https://ifrs18.app/errors"


class ApiProblemError(Exception):
    """A failure that should reach the client as a problem document."""

    def __init__(
        self,
        *,
        status_code: int,
        title: str,
        code: str,
        detail: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(title)
        self.status_code = status_code
        self.title = title
        self.code = code
        self.detail = detail
        self.extra = extra or {}

    def to_response(self, request: Request) -> JSONResponse:
        body: dict[str, Any] = {
            "type": f"{ERROR_BASE}/{self.code}",
            "title": self.title,
            "status": self.status_code,
            "instance": str(request.url.path),
        }
        if self.detail:
            body["detail"] = self.detail
        body.update(self.extra)
        return JSONResponse(
            status_code=self.status_code, content=body, media_type=PROBLEM_CONTENT_TYPE
        )


class NotFoundError(ApiProblemError):
    """404 is also what another user's resource returns.

    Answering 403 would confirm the id exists, letting a caller enumerate other
    tenants' projects one guess at a time. Ownership is enforced at the
    repository layer, and a miss is indistinguishable from a wrong id.
    """

    def __init__(self, resource: str) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            title=f"{resource} not found",
            code="not-found",
        )


class UnauthorizedError(ApiProblemError):
    def __init__(self, detail: str = "Authentication is required.") -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            title="Not authenticated",
            code="unauthorized",
            detail=detail,
        )


class ConflictError(ApiProblemError):
    def __init__(self, title: str, code: str, detail: str | None = None) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT, title=title, code=code, detail=detail
        )


class UnprocessableStateError(ApiProblemError):
    """The request is well-formed but the project is not in a state to accept it."""

    def __init__(self, title: str, code: str, detail: str | None = None, **extra: Any) -> None:
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            title=title,
            code=code,
            detail=detail,
            extra=extra,
        )


#: Raised when the database cannot be *reached* — as distinct from a query
#: that reached it and was rejected. SQLAlchemy raises `OperationalError` for a
#: refused or dropped connection and `InterfaceError` for a driver-level
#: failure; both wrap the driver's own exception.
_UNREACHABLE = (OperationalError, InterfaceError)


def _database_is_unreachable(exc: BaseException) -> bool:
    if isinstance(exc, _UNREACHABLE):
        return True
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True
    return isinstance(exc, ConnectionError | OSError)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiProblemError)
    async def _problem(request: Request, exc: ApiProblemError) -> JSONResponse:
        if exc.status_code >= 500:  # pragma: no cover - defensive
            log.error("api_problem", code=exc.code, status=exc.status_code)
        return exc.to_response(request)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return ApiProblemError(
            status_code=exc.status_code,
            title=str(exc.detail),
            code="http-error",
        ).to_response(request)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        """The last resort, and the one that was missing.

        Without it Starlette answers an unexpected failure with the bare text
        ``Internal Server Error``. That is not a problem document, so a client
        has nothing to show but "something went wrong" — and the single most
        likely cause in a fresh deployment, an API that cannot reach its
        database, looked exactly like a bug in the sign-up form.

        The message names the dependency and never the connection string: a
        DSN carries the database password (spec §32).
        """
        if _database_is_unreachable(exc):
            log.error("database_unreachable", error_type=type(exc).__name__)
            return ApiProblemError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                title="The database is not reachable",
                code="database-unavailable",
                detail=(
                    "The API is running but cannot reach its database, so nothing "
                    "can be read or saved. This is a deployment problem, not a "
                    "problem with what you entered. Check that the database is "
                    "running and that IFRS18_DATABASE_URL points at it; "
                    "GET /api/ready reports the same check on its own."
                ),
            ).to_response(request)

        # Nothing is echoed back: an unexpected exception's message can carry
        # a query, a row, or a credential (spec §32). The type is logged so the
        # failure is findable; the client is told only that it was unexpected.
        log.exception("unhandled_exception", error_type=type(exc).__name__)
        return ApiProblemError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            title="Unexpected server error",
            code="internal-error",
            detail="The request failed for a reason this API did not anticipate.",
        ).to_response(request)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return ApiProblemError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            title="Request validation failed",
            code="validation-failed",
            extra={
                "errors": [
                    {
                        "field": ".".join(str(part) for part in error["loc"][1:]),
                        "message": error["msg"],
                    }
                    for error in exc.errors()
                ]
            },
        ).to_response(request)
