"""Auth endpoints: dev tokens, current principal and tenant API keys."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse

from lanereach.auth.dependencies import (
    CurrentPrincipal,
    IdempotencyGuardDependency,
    TenantSession,
    get_bearer_authenticator,
    require_scopes,
)
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.auth.schemas import (
    ApiKeyCreatedResponse,
    ApiKeyCreateRequest,
    ApiKeyResponse,
    DevTokenRequest,
    DevTokenResponse,
    PrincipalResponse,
)
from lanereach.auth.service import ApiKeyService, BearerAuthenticator
from lanereach.core.dependencies import ClockDependency, SettingsDependency
from lanereach.core.errors import ProblemDocument
from lanereach.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from lanereach.core.schemas import ResponseModel
from lanereach.tenants.models import Tenant

_PROBLEM: dict[str, Any] = {"model": ProblemDocument}
_AUTH_ERRORS: dict[int | str, dict[str, Any]] = {401: _PROBLEM, 403: _PROBLEM}

router = APIRouter(prefix="/auth", tags=["Auth"])
dev_router = APIRouter(prefix="/auth", tags=["Auth"])

AdminPrincipal = Annotated[Principal, Depends(require_scopes(Scope.ADMIN_MANAGE))]


@dev_router.post(
    "/dev-token",
    response_model=DevTokenResponse,
    summary="Issue a dev token (dev mode only)",
    description="Mints an HS256 token for any tenant and role set. Only mounted when "
    "`AUTH_MODE=dev`; the app refuses to start with dev auth in production.",
    responses={404: _PROBLEM},
)
async def issue_dev_token(
    payload: DevTokenRequest,
    authenticator: Annotated[BearerAuthenticator, Depends(get_bearer_authenticator)],
) -> DevTokenResponse:
    """Issue a dev token."""
    issued = await authenticator.issue_dev_token_for_tenant(
        tenant_id=payload.tenant_id,
        subject=payload.subject,
        roles=frozenset(payload.roles),
        scopes=frozenset(payload.scopes) if payload.scopes is not None else None,
        lifetime=timedelta(seconds=payload.expires_in_seconds),
    )
    return DevTokenResponse(access_token=issued.access_token, expires_at=issued.expires_at)


class DevTenantResponse(ResponseModel):
    """A tenant offered on the dev login screen."""

    id: uuid.UUID
    name: str
    slug: str


@dev_router.get(
    "/dev-tenants",
    response_model=list[DevTenantResponse],
    summary="List tenants for the dev login screen (dev mode only)",
    description="Unauthenticated, like `dev-token`, and only mounted when `AUTH_MODE=dev`.",
)
async def list_dev_tenants(
    authenticator: Annotated[BearerAuthenticator, Depends(get_bearer_authenticator)],
) -> list[DevTenantResponse]:
    """List active tenants."""
    return [
        DevTenantResponse(id=tenant_id, name=name, slug=slug)
        for tenant_id, name, slug in await authenticator.list_active_tenants()
    ]


class AuthConfigResponse(ResponseModel):
    """How the console signs people in."""

    mode: str
    authority: str | None
    client_id: str | None
    scopes: list[str]
    provider_name: str


@router.get(
    "/config",
    response_model=AuthConfigResponse,
    summary="How the console signs in (public)",
    description="`dev`: the development sign-in. `oidc`: redirect to the company's identity "
    "provider (Authorization Code with PKCE) using `authority`, `client_id` and `scopes`. "
    "Contains no secret.",
)
async def read_auth_config(settings: SettingsDependency) -> AuthConfigResponse:
    """Public sign-in settings for the console."""
    oidc = settings.auth_mode.value == "oidc"
    return AuthConfigResponse(
        mode=settings.auth_mode.value,
        authority=settings.oidc_authority.rstrip("/") or None if oidc else None,
        client_id=settings.oidc_client_id or None if oidc else None,
        scopes=settings.oidc_scopes.split() if oidc else [],
        provider_name=settings.oidc_provider_name,
    )


@router.get(
    "/me",
    response_model=PrincipalResponse,
    summary="Describe the current caller",
    responses={401: _PROBLEM},
)
async def read_current_principal(
    principal: CurrentPrincipal, session: TenantSession
) -> PrincipalResponse:
    """Return the resolved principal and the name of its tenant."""
    tenant = await session.get(Tenant, principal.tenant_id)
    return PrincipalResponse(
        subject=principal.subject,
        tenant_id=principal.tenant_id,
        tenant_name=tenant.name if tenant else None,
        roles=sorted(principal.roles),
        scopes=sorted(principal.scopes),
        auth_method=principal.auth_method,
    )


@router.post(
    "/api-keys",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tenant API key",
    description="Requires `admin:manage`. The key can only carry scopes the caller holds. "
    "The secret is returned once and stored only as a hash.",
    responses={**_AUTH_ERRORS, 422: _PROBLEM},
)
async def create_api_key(
    payload: ApiKeyCreateRequest,
    principal: AdminPrincipal,
    session: TenantSession,
    idempotency: IdempotencyGuardDependency,
    settings: SettingsDependency,
    clock: ClockDependency,
) -> ApiKeyCreatedResponse | JSONResponse:
    """Create an API key."""
    if (replay := await idempotency.claim()) is not None:
        return replay
    created = await ApiKeyService(session, settings, clock).create_api_key(
        principal,
        name=payload.name,
        scopes=frozenset(payload.scopes),
        expires_at=payload.expires_at,
    )
    response = ApiKeyCreatedResponse(
        **ApiKeyResponse.model_validate(created.api_key).model_dump(), secret=created.plaintext
    )
    await idempotency.complete(
        status.HTTP_201_CREATED, response, replay_body=response.model_copy(update={"secret": None})
    )
    return response


@router.get(
    "/api-keys",
    response_model=Page[ApiKeyResponse],
    summary="List tenant API keys",
    responses=_AUTH_ERRORS,
)
async def list_api_keys(
    _principal: AdminPrincipal,
    session: TenantSession,
    settings: SettingsDependency,
    clock: ClockDependency,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[ApiKeyResponse]:
    """List API keys newest first."""
    keys, next_cursor = await ApiKeyService(session, settings, clock).list_api_keys(
        limit=limit, cursor=cursor
    )
    return Page(items=[ApiKeyResponse.model_validate(key) for key in keys], next_cursor=next_cursor)


@router.delete(
    "/api-keys/{api_key_id}",
    response_model=ApiKeyResponse,
    summary="Revoke a tenant API key",
    description="Revoked keys stop authenticating immediately. Keys of other tenants return 404.",
    responses={**_AUTH_ERRORS, 404: _PROBLEM},
)
async def revoke_api_key(
    api_key_id: uuid.UUID,
    _principal: AdminPrincipal,
    session: TenantSession,
    settings: SettingsDependency,
    clock: ClockDependency,
) -> ApiKeyResponse:
    """Revoke an API key."""
    revoked = await ApiKeyService(session, settings, clock).revoke_api_key(api_key_id)
    return ApiKeyResponse.model_validate(revoked)
