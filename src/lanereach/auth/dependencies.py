"""FastAPI dependencies for authentication, authorization and tenant-scoped sessions.

Typical endpoint signature::

    async def create_thing(
        principal: Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_WRITE))],
        session: TenantSession,
        idempotency: IdempotencyGuardDependency,
    ) -> ThingResponse: ...
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from typing import Annotated

import structlog
from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.auth.service import BearerAuthenticator
from lanereach.core.container import ServiceContainer
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import AuthenticationError, PermissionDeniedError
from lanereach.core.idempotency import IDEMPOTENCY_HEADER, IdempotencyGuard, fingerprint_request
from lanereach.core.tenancy import open_tenant_session
from lanereach.domain.audit.service import record_request_audit

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="An OIDC access token, a dev token (dev mode only) or a tenant API key.",
)


def get_bearer_authenticator(container: ContainerDependency) -> BearerAuthenticator:
    """Build the authenticator for this request.

    Args:
        container: The service container.

    Returns:
        A `BearerAuthenticator` wired to the container's resources.
    """
    return BearerAuthenticator(
        container.settings, container.clock, container.session_factory, container.jwks_cache
    )


async def get_current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    authenticator: Annotated[BearerAuthenticator, Depends(get_bearer_authenticator)],
) -> Principal:
    """Authenticate the request and bind the tenant to the logging context.

    Args:
        credentials: Parsed `Authorization: Bearer` header, if any.
        authenticator: The request's authenticator.

    Returns:
        The authenticated principal.

    Raises:
        AuthenticationError: If the header is missing or the credential is invalid.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("A bearer token is required.")
    principal = await authenticator.authenticate_bearer_token(credentials.credentials)
    structlog.contextvars.bind_contextvars(
        tenant_id=str(principal.tenant_id), auth_method=principal.auth_method.value
    )
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


def require_scopes(*required: Scope) -> Callable[[Principal], Awaitable[Principal]]:
    """Build a dependency that requires the caller to hold every scope in `required`.

    Args:
        *required: Scopes the endpoint needs.

    Returns:
        A dependency returning the principal when authorized.
    """

    async def check_scopes(principal: CurrentPrincipal) -> Principal:
        missing = [scope.value for scope in required if not principal.has_scope(scope)]
        if missing:
            raise PermissionDeniedError("Missing required scope(s): " + ", ".join(missing))
        return principal

    return check_scopes


async def _open_request_tenant_session(
    request: Request, principal: CurrentPrincipal, container: ContainerDependency
) -> AsyncIterator[AsyncSession]:
    async with open_tenant_session(container.session_factory, principal.tenant_id) as session:
        yield session
        # Reached only when the endpoint succeeded: audit the change in the same transaction.
        await _audit_request(request, principal, container, session)


async def _audit_request(
    request: Request, principal: Principal, container: ServiceContainer, session: AsyncSession
) -> None:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    route = request.scope.get("route")
    body: object = None
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = json.loads(await request.body() or b"null")
        except ValueError:
            body = None
    await record_request_audit(
        session,
        container.clock,
        actor=principal.subject,
        method=request.method,
        route_path=getattr(route, "path", None),
        route_name=getattr(route, "name", None),
        route_summary=getattr(route, "summary", None),
        path_params=dict(request.path_params),
        json_body=body,
    )


# scope="function" makes FastAPI commit the session *before* the response is sent, so a failed
# commit surfaces as an error instead of a success response for work that was rolled back.
TenantSession = Annotated[AsyncSession, Depends(_open_request_tenant_session, scope="function")]


async def _request_body_for_fingerprint(request: Request) -> bytes:
    """The request body, or for file uploads the parsed form fields and file contents.

    FastAPI parses form bodies before dependencies run, so the raw stream of a multipart
    upload is already consumed; the parsed (cached) form is fingerprinted instead.
    """
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith(("multipart/form-data", "application/x-www-form-urlencoded")):
        return await request.body()
    form = await request.form()
    digest = hashlib.sha256()
    for name, value in sorted(form.multi_items(), key=lambda item: item[0]):
        digest.update(name.encode() + b"=")
        if isinstance(value, str):
            digest.update(value.encode())
        else:
            content = await value.read()
            await value.seek(0)
            digest.update(str(value.filename).encode() + b":" + hashlib.sha256(content).digest())
        digest.update(b"\n")
    return digest.digest()


async def get_idempotency_guard(
    request: Request,
    session: TenantSession,
    container: ContainerDependency,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias=IDEMPOTENCY_HEADER,
            description="Optional client-generated key; retries with the same key and body "
            "return the original response instead of repeating the side effect.",
        ),
    ] = None,
) -> IdempotencyGuard:
    """Build the idempotency guard for a side-effecting POST.

    Args:
        request: The current request (method, path and body are fingerprinted).
        session: The request's tenant session.
        container: The service container.
        idempotency_key: The `Idempotency-Key` header.

    Returns:
        The guard.
    """
    body = await _request_body_for_fingerprint(request)
    return IdempotencyGuard(
        session,
        container.clock,
        key=idempotency_key,
        request_fingerprint=fingerprint_request(request.method, request.url.path, body),
        ttl=timedelta(seconds=container.settings.idempotency_key_ttl_seconds),
    )


IdempotencyGuardDependency = Annotated[IdempotencyGuard, Depends(get_idempotency_guard)]
