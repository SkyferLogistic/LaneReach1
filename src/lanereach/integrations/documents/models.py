"""Per-tenant document library connection (SharePoint, or a local folder in development)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    StrEnumType,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
)


class LibraryProvider(StrEnum):
    """Where the tenant's scripts live."""

    LOCAL = "local"
    SHAREPOINT = "sharepoint"


class SyncStatus(StrEnum):
    """Outcome of the last sync."""

    NEVER = "never"
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


class DocumentLibraryConnection(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """Which library holds a tenant's scripts, and how the last sync went.

    The Azure app's client secret is never stored here: it comes from the environment
    (`SHAREPOINT_CLIENT_SECRETS`, keyed by tenant slug).
    """

    __tablename__ = "document_library_connections"
    __table_args__ = (UniqueConstraint("tenant_id"),)

    provider: Mapped[LibraryProvider] = mapped_column(
        StrEnumType(LibraryProvider, 20), default=LibraryProvider.LOCAL
    )
    site_url: Mapped[str | None] = mapped_column(String(500))
    library_name: Mapped[str] = mapped_column(String(200), default="Documents")
    root_folder: Mapped[str] = mapped_column(String(300), default="LaneReach")
    azure_tenant_id: Mapped[str | None] = mapped_column(String(100))
    client_id: Mapped[str | None] = mapped_column(String(100))
    last_sync_at: Mapped[datetime | None]
    last_sync_status: Mapped[SyncStatus] = mapped_column(
        StrEnumType(SyncStatus, 20), default=SyncStatus.NEVER
    )
    last_sync_error: Mapped[str | None] = mapped_column(Text)
    last_sync_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime]
