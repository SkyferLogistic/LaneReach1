"""Notification table: what the recording notifier delivered (shown in the app)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import Base, CreatedAtMixin, TenantScopedMixin, UUIDPrimaryKeyMixin


class Notification(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A notification to a person or channel (office owner, pricing, Teams)."""

    __tablename__ = "notifications"

    channel: Mapped[str] = mapped_column(String(50))
    recipient: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    contact_id: Mapped[uuid.UUID | None]
    enrollment_id: Mapped[uuid.UUID | None]
    delivered_via: Mapped[str] = mapped_column(String(50))
    read_at: Mapped[datetime | None]
