"""Event table (outbox pattern) and scheduled-trigger bookkeeping."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    StrEnumType,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
    tenant_foreign_key,
)


class EventType(StrEnum):
    """Event types triggers can listen to (v1)."""

    CONTACT_CREATED = "contact.created"
    CONTACT_TAG_ADDED = "contact.tag_added"
    REPLY_RECEIVED = "reply.received"
    REPLY_CLASSIFIED = "reply.classified"
    MANUAL_ENROLL = "manual.enroll"
    SCHEDULE_TICK = "schedule.tick"
    LEAD_STATUS_CHANGED = "lead.status_changed"
    CUSTOM = "custom.event"
    DEAL_STAGE_CHANGED = "deal.stage_changed"
    EMAIL_OPENED = "email.opened"
    EMAIL_CLICKED = "email.clicked"
    LEAD_CREATED = "lead.created"
    MEETING_BOOKED = "meeting.booked"
    MEETING_CANCELED = "meeting.canceled"
    FORM_SUBMITTED = "form.submitted"
    CONTACT_STAGE_CHANGED = "contact.stage_changed"
    QUOTE_STATUS_CHANGED = "quote.status_changed"
    QUOTE_EXPIRING = "quote.expiring"
    DEAL_IDLE = "deal.idle"
    EMAIL_BOUNCED = "email.bounced"


class EventStatus(StrEnum):
    """Processing state of an event."""

    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


class Event(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """Something that happened and may start workflows.

    Events are written in the same transaction as the change that caused them (outbox
    pattern); a worker then matches them to triggers. `idempotency_key` makes publishing safe
    to retry.
    """

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key"),
        Index(
            "ix_events_pending",
            "tenant_id",
            "occurred_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    type: Mapped[EventType] = mapped_column(StrEnumType(EventType, 50))
    contact_id: Mapped[uuid.UUID | None]
    payload: Mapped[dict[str, Any]]
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime]
    status: Mapped[EventStatus] = mapped_column(
        StrEnumType(EventStatus, 20), default=EventStatus.PENDING
    )
    processed_at: Mapped[datetime | None]
    enrollments_created: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class ScheduledTriggerRun(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """Records that a `trigger_scheduled_date` fired, so it fires once per version."""

    __tablename__ = "scheduled_trigger_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workflow_id", "version"),
        tenant_foreign_key("workflow_id", "workflows"),
    )

    workflow_id: Mapped[uuid.UUID]
    version: Mapped[int] = mapped_column(Integer)
    enrolled_count: Mapped[int] = mapped_column(Integer, default=0)
