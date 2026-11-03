"""Tenant table.

The `tenants` table is the one table without a `tenant_id` column: its primary key *is* the
tenant ID, and its RLS policy compares `id` with the current tenant setting instead.
"""

from __future__ import annotations

from datetime import datetime, time
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, String, Time, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import Base, CreatedAtMixin, StrEnumType, UUIDPrimaryKeyMixin


class TenantStatus(StrEnum):
    """Lifecycle state of a tenant. Only `active` tenants can authenticate."""

    ACTIVE = "active"
    SUSPENDED = "suspended"


class Tenant(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """An organisation using LaneReach (for example a freight forwarder).

    `sending_paused_at` is the tenant-wide kill switch. The sending window (recipient local
    time) applies to every office unless overridden later per office.
    """

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[TenantStatus] = mapped_column(
        StrEnumType(TenantStatus, 20), default=TenantStatus.ACTIVE
    )
    sending_paused_at: Mapped[datetime | None]
    sending_window_start: Mapped[time] = mapped_column(Time, default=time(9, 0))
    sending_window_end: Mapped[time] = mapped_column(Time, default=time(11, 30))
    # Company look used by generated agent signatures (logo, tagline, offices, badges...).
    branding: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Open/click tracking of HTML emails; a workflow's `send_email` node can switch it off.
    track_opens: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    track_clicks: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    # Service lines the tenant sells (menus and filters show only these); empty = all.
    service_lines: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    # Monthly AI budget and AI-or-rules per area (`lanereach.ai.controls.AIControls`).
    ai_controls: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    updated_at: Mapped[datetime]
