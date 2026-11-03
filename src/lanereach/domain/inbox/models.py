"""What people did with a conversation in the unified inbox.

Everything else about a conversation (its messages, contact, agent, dates, last reply type) is
read from `messages`, so there is nothing to keep in sync; only human state lives here.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import Base, CreatedAtMixin, TenantScopedMixin, UUIDPrimaryKeyMixin


class ConversationState(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """Read, done, snoozed, assignee and labels of one email thread.

    A conversation counts as read when `read_at` is after its last inbound message, and as done
    when `done_at` is after its last message: a new reply brings it back to the inbox.
    """

    __tablename__ = "conversation_states"
    __table_args__ = (UniqueConstraint("tenant_id", "thread_id"),)

    thread_id: Mapped[str] = mapped_column(String(300))
    read_at: Mapped[datetime | None]
    done_at: Mapped[datetime | None]
    snoozed_until: Mapped[datetime | None]
    assignee_email: Mapped[str | None] = mapped_column(String(320))
    labels: Mapped[list[str]] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime]
