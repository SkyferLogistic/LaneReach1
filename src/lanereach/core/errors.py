"""Domain exceptions and their RFC 7807 `application/problem+json` rendering.

Services raise the exceptions defined here; they never build HTTP responses. The handlers
registered by `register_exception_handlers` translate them into problem documents with the
fields `type`, `title`, `status`, `detail`, `instance` and `errors[]`, plus `request_id`.
"""

from __future__ import annotations

from collections.abc import Sequence
from http import HTTPStatus
from typing import Any, ClassVar

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_CONTENT_TYPE = "application/problem+json"

logger = structlog.get_logger(__name__)


class ProblemFieldError(BaseModel):
    """One entry of a problem document's `errors[]` list."""

    location: list[str | int] = Field(default_factory=list, description="Path to the field.")
    message: str
    code: str | None = None


class ProblemDocument(BaseModel):
    """RFC 7807 problem details body returned for every error response."""

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    request_id: str | None = None
    errors: list[ProblemFieldError] = Field(default_factory=list)


class LaneReachError(Exception):
    """Base class for every expected, client-visible error.

    Subclasses set `status_code`, `problem_type` (a stable slug) and `title`. The `detail`
    passed at raise time must be safe to show to API clients: no secrets, no other tenant's data.

    Args:
        detail: Human-readable explanation specific to this occurrence.
        errors: Optional field-level errors.
        headers: Optional extra response headers (for example `Retry-After`).
    """

    status_code: ClassVar[int] = HTTPStatus.BAD_REQUEST
    problem_type: ClassVar[str] = "bad-request"
    title: ClassVar[str] = "Bad request"

    def __init__(
        self,
        detail: str | None = None,
        *,
        errors: Sequence[ProblemFieldError] = (),
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.errors = list(errors)
        self.headers = headers or {}


class AuthenticationError(LaneReachError):
    """The caller is not authenticated (missing, malformed, expired or unknown credentials)."""

    status_code = HTTPStatus.UNAUTHORIZED
    problem_type = "unauthenticated"
    title = "Authentication required"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail, headers={"WWW-Authenticate": "Bearer"})


class PermissionDeniedError(LaneReachError):
    """The caller is authenticated but lacks a required scope.

    Never raise this for resources owned by another tenant; use `NotFoundError` so resource
    existence is not leaked across tenants.
    """

    status_code = HTTPStatus.FORBIDDEN
    problem_type = "permission-denied"
    title = "Permission denied"


class NotFoundError(LaneReachError):
    """The resource does not exist or belongs to another tenant."""

    status_code = HTTPStatus.NOT_FOUND
    problem_type = "not-found"
    title = "Resource not found"


class ConflictError(LaneReachError):
    """The request conflicts with the current state of a resource."""

    status_code = HTTPStatus.CONFLICT
    problem_type = "conflict"
    title = "Conflict"


class IdempotencyKeyReusedError(LaneReachError):
    """An `Idempotency-Key` was reused with a different request payload."""

    status_code = HTTPStatus.UNPROCESSABLE_ENTITY
    problem_type = "idempotency-key-reused"
    title = "Idempotency key reused with a different request"


class BusinessRuleViolationError(LaneReachError):
    """The request is well-formed but violates a business rule."""

    status_code = HTTPStatus.UNPROCESSABLE_ENTITY
    problem_type = "business-rule-violation"
    title = "Business rule violated"


class RequestTooLargeError(LaneReachError):
    """The request body exceeds `MAX_REQUEST_BODY_BYTES`."""

    status_code = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    # Matches the slug derived from HTTPStatus(413) so both 413 code paths look identical.
    problem_type = "content-too-large"
    title = "Content Too Large"


class RateLimitedError(LaneReachError):
    """The caller exceeded a rate limit."""

    status_code = HTTPStatus.TOO_MANY_REQUESTS
    problem_type = "rate-limited"
    title = "Too many requests"


class ServiceUnavailableError(LaneReachError):
    """A dependency the request needs is unavailable."""

    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    problem_type = "service-unavailable"
    title = "Service unavailable"


class ConfigurationError(Exception):
    """The process configuration is invalid. Raised at startup; never mapped to HTTP."""


def build_problem_response(
    request: Request,
    *,
    status_code: int,
    problem_type: str,
    title: str,
    detail: str | None = None,
    errors: Sequence[ProblemFieldError] = (),
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Render a problem document as a JSON response.

    Args:
        request: The request being answered; provides `instance` and the request ID.
        status_code: HTTP status code.
        problem_type: Stable slug appended to the configured problem type base URI.
        title: Short, human-readable summary of the problem type.
        detail: Occurrence-specific explanation.
        errors: Field-level errors.
        headers: Extra response headers.

    Returns:
        A `JSONResponse` with media type `application/problem+json`.
    """
    base_uri = _problem_type_base_uri(request)
    document = ProblemDocument(
        type=f"{base_uri}{problem_type}",
        title=title,
        status=status_code,
        detail=detail,
        instance=request.url.path,
        request_id=getattr(request.state, "request_id", None),
        errors=list(errors),
    )
    return JSONResponse(
        status_code=status_code,
        content=document.model_dump(mode="json"),
        media_type=PROBLEM_CONTENT_TYPE,
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Install handlers that turn every error into a problem document.

    Args:
        app: The application to configure.
    """

    async def handle_lanereach_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, LaneReachError)  # noqa: S101 - narrowed by registration
        return build_problem_response(
            request,
            status_code=exc.status_code,
            problem_type=exc.problem_type,
            title=exc.title,
            detail=exc.detail,
            errors=exc.errors,
            headers=exc.headers,
        )

    async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError)  # noqa: S101 - narrowed by registration
        return build_problem_response(
            request,
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            problem_type="validation-error",
            title="Request validation failed",
            detail="One or more fields are invalid.",
            errors=[_to_field_error(error) for error in exc.errors()],
        )

    async def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, StarletteHTTPException)  # noqa: S101 - narrowed by registration
        status = HTTPStatus(exc.status_code)
        return build_problem_response(
            request,
            status_code=exc.status_code,
            problem_type=status.phrase.lower().replace(" ", "-"),
            title=status.phrase,
            detail=exc.detail if isinstance(exc.detail, str) else None,
            headers=dict(exc.headers) if exc.headers else None,
        )

    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # Never leak internals: log the exception server-side, return a generic document.
        logger.exception("unhandled_exception", error_type=type(exc).__name__)
        return build_problem_response(
            request,
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            problem_type="internal-error",
            title="Internal server error",
            detail="An unexpected error occurred.",
        )

    app.add_exception_handler(LaneReachError, handle_lanereach_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(Exception, handle_unexpected_error)


def _to_field_error(error: Any) -> ProblemFieldError:
    location = [part for part in error.get("loc", ()) if isinstance(part, str | int)]
    return ProblemFieldError(
        location=location,
        message=str(error.get("msg", "Invalid value")),
        code=error.get("type"),
    )


def _problem_type_base_uri(request: Request) -> str:
    container = getattr(request.app.state, "container", None)
    if container is None:
        return "urn:lanereach:problem:"
    base_uri: str = container.settings.problem_type_base_uri
    return base_uri
