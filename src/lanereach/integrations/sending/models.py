"""Outbox table: every email LaneReach sent (or recorded in dry-run mode), with its status."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    StrEnumType,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
)


class OutboxStatus(StrEnum):
    """What happened to an outbox email."""

    DRY_RUN = "dry_run"
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"


class OutboxMessage(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A copy of every email a sending provider handled: the tenant's sent log.

    In dry-run mode (dev, test, staging) nothing leaves: the row is the email. With a real
    provider the row records what was sent and whether the mailbox accepted it.

    `idempotency_key` is the enrollment step key (or review decision key): re-sending with the
    same key returns the stored message instead of creating a second one.
    """

    __tablename__ = "outbox"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    idempotency_key: Mapped[str] = mapped_column(String(150))
    provider_message_id: Mapped[str] = mapped_column(String(300))
    thread_id: Mapped[str] = mapped_column(String(300))
    in_reply_to: Mapped[str | None] = mapped_column(String(300))
    references: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20))
    to_email: Mapped[str] = mapped_column(String(320))
    to_name: Mapped[str | None] = mapped_column(String(200))
    from_email: Mapped[str] = mapped_column(String(320))
    from_name: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    html_body: Mapped[str | None] = mapped_column(Text)
    headers: Mapped[dict[str, Any]] = mapped_column(JSONB)
    contact_id: Mapped[uuid.UUID | None]
    office_id: Mapped[uuid.UUID | None]
    sender_id: Mapped[uuid.UUID | None]
    enrollment_id: Mapped[uuid.UUID | None]
    status: Mapped[OutboxStatus] = mapped_column(
        StrEnumType(OutboxStatus, 10), default=OutboxStatus.DRY_RUN, server_default="dry_run"
    )
    provider: Mapped[str] = mapped_column(String(30), default="dry_run", server_default="dry_run")
    sent_at: Mapped[datetime | None]
    error: Mapped[str | None] = mapped_column(String(500))
    # Real sending happens after the step commits: queued rows are delivered by a worker.
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None]
