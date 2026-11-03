"""Lead table."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    StrEnumType,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
    tenant_foreign_key,
    tenant_unique_id,
)


class LeadStatus(StrEnum):
    """Where the lead is in the pricing team's process."""

    NEW = "new"
    QUOTING = "quoting"
    QUOTED = "quoted"
    WON = "won"
    LOST = "lost"


class LeadPriority(StrEnum):
    """How urgently pricing should look at the lead."""

    NORMAL = "normal"
    HIGH = "high"


class Lead(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A quote-ready opportunity with the shipment details extracted from a reply."""

    __tablename__ = "leads"
    __table_args__ = (tenant_unique_id(), tenant_foreign_key("contact_id", "contacts"))

    contact_id: Mapped[uuid.UUID]
    company_id: Mapped[uuid.UUID | None]
    office_id: Mapped[uuid.UUID | None]
    message_id: Mapped[uuid.UUID | None]
    enrollment_id: Mapped[uuid.UUID | None]
    status: Mapped[LeadStatus] = mapped_column(StrEnumType(LeadStatus, 20), default=LeadStatus.NEW)
    priority: Mapped[LeadPriority] = mapped_column(
        StrEnumType(LeadPriority, 10), default=LeadPriority.NORMAL
    )
    source: Mapped[str] = mapped_column(String(50))
    summary: Mapped[str | None] = mapped_column(Text)
    shipment: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime]
