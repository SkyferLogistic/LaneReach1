"""Sender (mailbox / agent) table and the daily usage counter used by the capacity guardrail."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Integer, String, Text, UniqueConstraint
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


class ServiceLine(StrEnum):
    """Logistics business an agent, a script or a deal belongs to.

    `any` agents and scripts serve every line. Stored as text, so lines can be added without a
    migration.
    """

    AIR = "air"
    OCEAN = "ocean"
    ROAD = "road"
    RAIL = "rail"
    COURIER = "courier"
    CUSTOMS = "customs"
    WAREHOUSING = "warehousing"
    PROJECT = "project"
    ANY = "any"


class SignatureMode(StrEnum):
    """How an agent's email signature is produced."""

    GENERATED = "generated"
    CUSTOM_HTML = "custom_html"
    IMAGE = "image"


class Sender(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A mailbox an office sends from: one agent (for example Molly, air freight, Canada).

    A flagged sender (after a "blocked" bounce) is skipped until an admin clears the flag. The
    signature is generated from the tenant's branding and the agent's details unless the agent
    has a custom HTML signature or an uploaded signature image.
    """

    __tablename__ = "senders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email"),
        tenant_unique_id(),
        tenant_foreign_key("office_id", "offices"),
        tenant_foreign_key("signature_asset_id", "email_assets", nullable_column=True),
    )

    office_id: Mapped[uuid.UUID]
    display_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320))
    daily_cap: Mapped[int] = mapped_column(Integer, default=50)
    is_active: Mapped[bool] = mapped_column(default=True)
    flagged_at: Mapped[datetime | None]
    flag_reason: Mapped[str | None] = mapped_column(String(200))
    service_line: Mapped[ServiceLine] = mapped_column(
        StrEnumType(ServiceLine, 10), default=ServiceLine.ANY
    )
    job_title: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(50))
    mobile: Mapped[str | None] = mapped_column(String(50))
    signature_mode: Mapped[SignatureMode] = mapped_column(
        StrEnumType(SignatureMode, 20), default=SignatureMode.GENERATED
    )
    signature_html: Mapped[str | None] = mapped_column(Text)
    signature_asset_id: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime]


class MailboxDailyUsage(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """How many emails a sender sent on a day (in the office's timezone)."""

    __tablename__ = "mailbox_daily_usage"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sender_id", "day"),
        tenant_foreign_key("sender_id", "senders"),
    )

    sender_id: Mapped[uuid.UUID]
    day: Mapped[date]
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
