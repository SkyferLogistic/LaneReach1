"""Message table: every email sent to or received from a contact."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Index, Integer, String, Text, UniqueConstraint, text
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


class MessageDirection(StrEnum):
    """Whether LaneReach sent or received the message."""

    OUTBOUND = "outbound"
    INBOUND = "inbound"


class MessageKind(StrEnum):
    """What kind of message this is.

    `cold` messages (first touch of a sequence) count toward the frequency cap and office
    rotation; `follow_up` messages continue a sequence in the same thread; `reply` messages are
    answers in an existing thread; inbound messages are `reply`, `bounce` or `complaint`.
    """

    COLD = "cold"
    FOLLOW_UP = "follow_up"
    REPLY = "reply"
    BOUNCE = "bounce"
    COMPLAINT = "complaint"


class Message(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """An email in a conversation thread.

    `thread_id` groups a conversation (the Message-ID of the first email). `body` is personal
    data: it is never logged and is purged after the retention period.
    """

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "message_id"),
        tenant_unique_id(),
        Index(
            "ix_messages_tenant_id_contact_id_occurred_at", "tenant_id", "contact_id", "occurred_at"
        ),
        Index("ix_messages_tenant_id_thread_id", "tenant_id", "thread_id"),
        # The dispatcher's safety net: inbound messages the reply pipeline has not processed.
        Index(
            "ix_messages_unprocessed_inbound",
            "tenant_id",
            "created_at",
            postgresql_where=text("direction = 'inbound' AND processed_at IS NULL"),
        ),
        tenant_foreign_key("contact_id", "contacts"),
        # The tracking endpoints find the message by its tracking id.
        Index("ix_messages_tenant_id_tracking_id", "tenant_id", "tracking_id", unique=True),
    )

    contact_id: Mapped[uuid.UUID]
    direction: Mapped[MessageDirection] = mapped_column(StrEnumType(MessageDirection, 10))
    kind: Mapped[MessageKind] = mapped_column(StrEnumType(MessageKind, 20))
    message_id: Mapped[str] = mapped_column(String(300))
    thread_id: Mapped[str] = mapped_column(String(300))
    in_reply_to: Mapped[str | None] = mapped_column(String(300))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text)
    from_email: Mapped[str] = mapped_column(String(320))
    to_email: Mapped[str] = mapped_column(String(320))
    office_id: Mapped[uuid.UUID | None]
    sender_id: Mapped[uuid.UUID | None]
    enrollment_id: Mapped[uuid.UUID | None]
    provider: Mapped[str] = mapped_column(String(50))
    occurred_at: Mapped[datetime]
    processed_at: Mapped[datetime | None]
    processing_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    body_purged_at: Mapped[datetime | None]
    # Open and click tracking of HTML emails (M14); counts exclude suspected bots.
    tracking_id: Mapped[uuid.UUID | None]
    first_opened_at: Mapped[datetime | None]
    first_clicked_at: Mapped[datetime | None]
    open_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    click_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
