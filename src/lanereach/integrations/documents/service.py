"""Which document library a tenant uses, and opening it."""

from __future__ import annotations

from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.core.clock import Clock
from lanereach.core.config import Settings
from lanereach.core.errors import BusinessRuleViolationError
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.integrations.documents.models import DocumentLibraryConnection, LibraryProvider
from lanereach.integrations.documents.sharepoint import SharePointDocumentStore, SharePointSettings
from lanereach.integrations.documents.store import DocumentStore, LocalDocumentStore
from lanereach.tenants.models import Tenant


async def get_library_connection(session: AsyncSession, clock: Clock) -> DocumentLibraryConnection:
    """The tenant's connection row, created (local folder) the first time it is needed."""
    connection = await session.scalar(select(DocumentLibraryConnection))
    if connection is None:
        now = clock.now()
        connection = DocumentLibraryConnection(
            tenant_id=require_session_tenant_id(session),
            provider=LibraryProvider.LOCAL,
            library_name="Documents",
            root_folder="LaneReach",
            last_sync_summary={},
            created_at=now,
            updated_at=now,
        )
        session.add(connection)
        await session.flush()
    return connection


def sharepoint_secret_configured(settings: Settings, tenant: Tenant) -> bool:
    """Whether `SHAREPOINT_CLIENT_SECRETS` has a secret for this tenant."""
    return tenant.slug in settings.sharepoint_client_secrets


def open_document_store(
    connection: DocumentLibraryConnection,
    *,
    tenant: Tenant,
    settings: Settings,
    http: httpx.AsyncClient,
    clock: Clock,
) -> DocumentStore:
    """Open the tenant's library.

    Args:
        connection: The tenant's connection settings.
        tenant: The tenant (slug selects the client secret).
        settings: Process configuration.
        http: Shared HTTP client.
        clock: Time source.

    Returns:
        The store.

    Raises:
        BusinessRuleViolationError: When SharePoint is selected but not fully configured.
    """
    if connection.provider is LibraryProvider.LOCAL:
        return LocalDocumentStore(Path(settings.local_documents_path) / str(tenant.id))
    secret = settings.sharepoint_client_secrets.get(tenant.slug)
    missing = [
        name
        for name, value in (
            ("site URL", connection.site_url),
            ("Azure tenant ID", connection.azure_tenant_id),
            ("client ID", connection.client_id),
            ("client secret (SHAREPOINT_CLIENT_SECRETS)", secret),
        )
        if not value
    ]
    if missing:
        raise BusinessRuleViolationError(
            "SharePoint is not fully configured: " + ", ".join(missing)
        )
    assert secret is not None  # noqa: S101 - checked above
    return SharePointDocumentStore(
        http,
        SharePointSettings(
            graph_base_url=settings.graph_base_url,
            login_base_url=settings.graph_login_url,
            azure_tenant_id=str(connection.azure_tenant_id),
            client_id=str(connection.client_id),
            client_secret=secret.get_secret_value(),
            site_url=str(connection.site_url),
            library_name=connection.library_name,
            root_folder=connection.root_folder,
        ),
        clock,
    )
