"""Audit log: who changed what, when (settings, publishing, sending controls, keys...)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import Base, CreatedAtMixin, TenantScopedMixin, UUIDPrimaryKeyMixin


class AuditEvent(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """One recorded action.

    `action` is a dotted verb such as `workflow.published` or `settings.sending_paused`;
    `changes` holds the changed fields (never secrets or email bodies).
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_tenant_id_occurred_at", "tenant_id", "occurred_at"),
        Index("ix_audit_events_tenant_id_entity", "tenant_id", "entity_type", "entity_id"),
    )

    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(100))
    summary: Mapped[str] = mapped_column(String(500))
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(100))
    occurred_at: Mapped[datetime]
