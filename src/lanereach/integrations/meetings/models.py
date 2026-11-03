"""Meetings booked through Calendly or Cal.com, linked to the contact and their open deal."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    StrEnumType,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
    tenant_foreign_key,
)


class MeetingStatus(StrEnum):
    """Where the booking stands."""

    BOOKED = "booked"
    RESCHEDULED = "rescheduled"
    CANCELED = "canceled"


class Meeting(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """One booking; a reschedule or cancellation updates the same row."""

    __tablename__ = "meetings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "external_id"),
        Index("ix_meetings_tenant_id_contact_id", "tenant_id", "contact_id"),
        tenant_foreign_key("contact_id", "contacts"),
        tenant_foreign_key("deal_id", "deals", nullable_column=True),
    )

    contact_id: Mapped[uuid.UUID]
    deal_id: Mapped[uuid.UUID | None]
    connection_id: Mapped[uuid.UUID | None]
    provider: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(String(300))
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime | None]
    status: Mapped[MeetingStatus] = mapped_column(StrEnumType(MeetingStatus, 20))
    join_url: Mapped[str | None] = mapped_column(String(1000))
    invitee_email: Mapped[str] = mapped_column(String(320))
    invitee_name: Mapped[str | None] = mapped_column(String(200))
    organizer_email: Mapped[str | None] = mapped_column(String(320))
    updated_at: Mapped[datetime]
