"""Opens and clicks of tracked emails, one row per pixel load or link click."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    StrEnumType,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
    tenant_foreign_key,
)


class EmailEventKind(StrEnum):
    """What the recipient (or a scanner) did."""

    OPENED = "opened"
    CLICKED = "clicked"


class EmailEvent(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """One open or click.

    Suspected bots (link scanners, prefetchers, opens seconds after sending) are recorded with
    `suspected_bot` so the numbers can be audited, but they never count as engagement.
    """

    __tablename__ = "email_events"
    __table_args__ = (
        Index("ix_email_events_tenant_id_message_id", "tenant_id", "message_id"),
        Index("ix_email_events_tenant_id_occurred_at", "tenant_id", "occurred_at"),
        tenant_foreign_key("message_id", "messages"),
    )

    message_id: Mapped[uuid.UUID]
    kind: Mapped[EmailEventKind] = mapped_column(StrEnumType(EmailEventKind, 10))
    url: Mapped[str | None] = mapped_column(String(1500))
    occurred_at: Mapped[datetime]
    suspected_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    client: Mapped[str] = mapped_column(String(40))
