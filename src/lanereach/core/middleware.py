"""Pure ASGI middleware: request IDs, access logging and request body size limits.

Pure ASGI (rather than `BaseHTTPMiddleware`) keeps streaming intact and keeps `contextvars`
bound in the middleware visible to the endpoint and to exception handlers.
"""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from lanereach.core.errors import RequestTooLargeError, build_problem_response

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

logger = structlog.get_logger("lanereach.access")


class RequestContextMiddleware:
    """Assigns a request ID, binds logging context and writes one access log line.

    An inbound `X-Request-ID` is reused when it is safe (so IDs propagate from gateways);
    otherwise a new UUID is generated. The ID is echoed on every response.

    Args:
        app: The wrapped ASGI application.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one ASGI connection."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = _header(scope, REQUEST_ID_HEADER)
        request_id = inbound if inbound and _VALID_REQUEST_ID.fullmatch(inbound) else None
        request_id = request_id or str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.lower().encode(), request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "request_completed",
                method=scope["method"],
                path=scope["path"],
                status_code=status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            structlog.contextvars.clear_contextvars()


class BodySizeLimitMiddleware:
    """Rejects request bodies larger than `max_bytes` with a 413 problem document.

    Checks `Content-Length` up front and also counts streamed chunks, so chunked uploads
    without a length header are limited too.

    Args:
        app: The wrapped ASGI application.
        max_bytes: Maximum accepted body size.
        upload_max_bytes: Larger limit for file-upload endpoints.
        upload_path_suffixes: Paths (suffixes) that accept file uploads.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int,
        upload_max_bytes: int | None = None,
        upload_path_suffixes: tuple[str, ...] = (),
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.upload_max_bytes = upload_max_bytes or max_bytes
        self.upload_path_suffixes = upload_path_suffixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one ASGI connection."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        limit = (
            self.upload_max_bytes if path.endswith(self.upload_path_suffixes) else self.max_bytes
        )
        declared = _header(scope, "content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await self._reject(scope, receive, send, limit)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # FastAPI wraps arbitrary errors raised while reading the body into a 400,
                    # but re-raises HTTPException, which our handler renders as a 413 problem.
                    raise StarletteHTTPException(
                        status_code=RequestTooLargeError.status_code,
                        detail=self._detail(limit),
                    )
            return message

        await self.app(scope, limited_receive, send)

    def _detail(self, limit: int) -> str:
        return f"Request bodies are limited to {limit} bytes."

    async def _reject(self, scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        error = RequestTooLargeError(self._detail(limit))
        response = build_problem_response(
            Request(scope, receive),
            status_code=error.status_code,
            problem_type=error.problem_type,
            title=error.title,
            detail=error.detail,
        )
        await response(scope, receive, send)


def _header(scope: Scope, name: str) -> str | None:
    wanted = name.lower().encode()
    for key, value in scope.get("headers", []):
        if key.lower() == wanted:
            decoded: str = value.decode("latin-1")
            return decoded
    return None
