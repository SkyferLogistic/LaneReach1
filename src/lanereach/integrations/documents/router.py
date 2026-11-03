"""Scripts library settings: local folder or SharePoint, and a connection test."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Self

from fastapi import APIRouter, Depends
from pydantic import Field, model_validator

from lanereach.auth.dependencies import CurrentPrincipal, TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import BusinessRuleViolationError
from lanereach.core.openapi import READ, WRITE
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.integrations.documents.models import DocumentLibraryConnection, LibraryProvider
from lanereach.integrations.documents.service import (
    get_library_connection,
    open_document_store,
    sharepoint_secret_configured,
)
from lanereach.integrations.documents.sharepoint import SharePointDocumentStore
from lanereach.integrations.documents.store import DocumentStoreError
from lanereach.tenants.models import Tenant

router = APIRouter(prefix="/settings/library", tags=["Settings"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Admin = Annotated[Principal, Depends(require_scopes(Scope.ADMIN_MANAGE))]


class LibrarySettingsResponse(ResponseModel):
    """The tenant's scripts library."""

    provider: LibraryProvider
    site_url: str | None
    library_name: str
    root_folder: str
    azure_tenant_id: str | None
    client_id: str | None
    client_secret_configured: bool
    last_sync_at: datetime | None
    last_sync_status: str
    last_sync_error: str | None
    last_sync_summary: dict[str, object]


class LibrarySettingsRequest(StrictRequestModel):
    """Where scripts live. The client secret is set in the environment, never here."""

    provider: LibraryProvider
    site_url: str | None = Field(
        default=None,
        max_length=500,
        pattern=r"^https://[a-zA-Z0-9.-]+\.sharepoint\.com(/.*)?$",
        examples=["https://contoso.sharepoint.com/sites/Marketing"],
    )
    library_name: str = Field(default="Documents", min_length=1, max_length=200)
    root_folder: str = Field(default="LaneReach", min_length=1, max_length=300)
    azure_tenant_id: str | None = Field(default=None, max_length=100)
    client_id: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _require_sharepoint_fields(self) -> Self:
        if self.provider is LibraryProvider.SHAREPOINT and not (
            self.site_url and self.azure_tenant_id and self.client_id
        ):
            raise ValueError("SharePoint needs site_url, azure_tenant_id and client_id")
        return self


class LibraryTestResponse(ResponseModel):
    """Result of a connection test."""

    ok: bool
    detail: str
    files: int


async def _tenant(session: TenantSession, principal: CurrentPrincipal) -> Tenant:
    tenant = await session.get(Tenant, principal.tenant_id)
    assert tenant is not None  # noqa: S101 - authenticated tenants exist
    return tenant


TenantRow = Annotated[Tenant, Depends(_tenant)]


def _describe(connection: DocumentLibraryConnection, secret: bool) -> LibrarySettingsResponse:
    return LibrarySettingsResponse(
        provider=connection.provider,
        site_url=connection.site_url,
        library_name=connection.library_name,
        root_folder=connection.root_folder,
        azure_tenant_id=connection.azure_tenant_id,
        client_id=connection.client_id,
        client_secret_configured=secret,
        last_sync_at=connection.last_sync_at,
        last_sync_status=connection.last_sync_status.value,
        last_sync_error=connection.last_sync_error,
        last_sync_summary=dict(connection.last_sync_summary or {}),
    )


@router.get("", response_model=LibrarySettingsResponse, summary="Scripts library", responses=READ)
async def read_library_settings(
    _principal: Reader, session: TenantSession, container: ContainerDependency, tenant: TenantRow
) -> LibrarySettingsResponse:
    """Where scripts are read from and how the last sync went."""
    connection = await get_library_connection(session, container.clock)
    return _describe(connection, sharepoint_secret_configured(container.settings, tenant))


@router.put(
    "",
    response_model=LibrarySettingsResponse,
    summary="Choose the scripts library",
    responses=WRITE,
)
async def update_library_settings(
    payload: LibrarySettingsRequest,
    _principal: Admin,
    session: TenantSession,
    container: ContainerDependency,
    tenant: TenantRow,
) -> LibrarySettingsResponse:
    """Switch between the local folder and SharePoint (admin only)."""
    connection = await get_library_connection(session, container.clock)
    for name, value in payload.model_dump().items():
        setattr(connection, name, value)
    connection.updated_at = container.clock.now()
    return _describe(connection, sharepoint_secret_configured(container.settings, tenant))


@router.post(
    "/test",
    response_model=LibraryTestResponse,
    summary="Test the library connection",
    responses=WRITE,
)
async def check_library_connection(
    _principal: Admin, session: TenantSession, container: ContainerDependency, tenant: TenantRow
) -> LibraryTestResponse:
    """Sign in (SharePoint), open the library and count its files. Changes nothing."""
    connection = await get_library_connection(session, container.clock)
    try:
        store = open_document_store(
            connection,
            tenant=tenant,
            settings=container.settings,
            http=container.http_client,
            clock=container.clock,
        )
        detail = "Local folder is ready."
        if isinstance(store, SharePointDocumentStore):
            detail = f"Connected to {await store.check_connection()}"
        files = len(await store.list_files())
    except (DocumentStoreError, BusinessRuleViolationError) as exc:
        return LibraryTestResponse(ok=False, detail=str(exc), files=0)
    return LibraryTestResponse(ok=True, detail=detail, files=files)
