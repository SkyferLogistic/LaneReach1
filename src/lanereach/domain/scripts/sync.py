"""Running the scripts-library sync for one tenant (console button) or all tenants (beat)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import httpx
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.core.clock import Clock
from lanereach.core.config import Settings
from lanereach.core.container import ServiceContainer
from lanereach.core.errors import BusinessRuleViolationError
from lanereach.core.tenancy import open_system_session, open_tenant_session
from lanereach.domain.scripts.service import ScriptLibraryService, SyncSummary
from lanereach.integrations.documents.models import SyncStatus
from lanereach.integrations.documents.service import get_library_connection, open_document_store
from lanereach.integrations.documents.store import DocumentStore, DocumentStoreError
from lanereach.tenants.models import Tenant

logger = structlog.get_logger(__name__)


async def open_script_library(
    session: AsyncSession,
    *,
    clock: Clock,
    settings: Settings,
    http: httpx.AsyncClient,
    tenant: Tenant,
) -> tuple[ScriptLibraryService, DocumentStore]:
    """The library service for the session's tenant.

    Raises:
        BusinessRuleViolationError: When SharePoint is selected but not configured.
    """
    connection = await get_library_connection(session, clock)
    store = open_document_store(
        connection, tenant=tenant, settings=settings, http=http, clock=clock
    )
    return ScriptLibraryService(session, clock, settings, store, tenant), store


async def sync_tenant_library(
    session: AsyncSession,
    *,
    clock: Clock,
    settings: Settings,
    http: httpx.AsyncClient,
    tenant: Tenant,
) -> SyncSummary:
    """Sync one tenant's library and record the outcome on its connection.

    Args:
        session: Tenant session.
        clock: Time source.
        settings: Configuration.
        http: HTTP client (SharePoint).
        tenant: The tenant.

    Returns:
        What the sync did (a failure is recorded, not raised).
    """
    connection = await get_library_connection(session, clock)
    summary = SyncSummary()
    try:
        service, _ = await open_script_library(
            session, clock=clock, settings=settings, http=http, tenant=tenant
        )
        summary = await service.sync_library()
        status = SyncStatus.PARTIAL if summary.errors else SyncStatus.OK
        connection.last_sync_error = None
    except (DocumentStoreError, BusinessRuleViolationError) as exc:
        status = SyncStatus.FAILED
        summary.errors.append(str(exc))
        connection.last_sync_error = str(exc)
        logger.warning("script_library_sync_failed", error=str(exc))
    now = clock.now()
    connection.last_sync_at = now
    connection.last_sync_status = status
    connection.last_sync_summary = summary.to_json()
    connection.updated_at = now
    return summary


@dataclass
class LibrarySyncRun:
    """Result of syncing every tenant."""

    tenants: int = 0
    imported: int = 0
    errors: list[str] = field(default_factory=list)


async def sync_all_script_libraries(container: ServiceContainer) -> LibrarySyncRun:
    """Sync every tenant's library (Celery beat). One failing tenant does not stop the others.

    Args:
        container: Application container.

    Returns:
        Totals.
    """
    run = LibrarySyncRun()
    async with open_system_session(container.session_factory) as system:
        tenant_ids: list[uuid.UUID] = list(
            (await system.scalars(text("SELECT tenant_id FROM maintenance_list_tenants()"))).all()
        )
    for tenant_id in tenant_ids:
        try:
            async with open_tenant_session(container.session_factory, tenant_id) as session:
                tenant = await session.get(Tenant, tenant_id)
                if tenant is None:
                    continue
                summary = await sync_tenant_library(
                    session,
                    clock=container.clock,
                    settings=container.settings,
                    http=container.http_client,
                    tenant=tenant,
                )
                run.tenants += 1
                run.imported += summary.imported
        except Exception as exc:
            logger.exception("script_library_sync_crashed", tenant_id=str(tenant_id))
            run.errors.append(f"{tenant_id}: {type(exc).__name__}")
    return run
