"""The `SendingProvider` interface. All email leaves LaneReach through an implementation of it.

v1 ships only `DryRunSendingProvider` (writes to the `outbox` table). Real providers (Warmbly,
Pigeon, Microsoft Graph) implement the same protocol later; they must honour `idempotency_key`
so a step re-run after a crash never sends twice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol


@dataclass(frozen=True)
class OutboundMessage:
    """An email ready to send (already rendered and cleared by the guardrails)."""

    idempotency_key: str
    kind: str
    to_email: str
    to_name: str | None
    from_email: str
    from_name: str
    subject: str
    body: str
    html_body: str | None = None
    thread_id: str | None = None
    in_reply_to: str | None = None
    references: tuple[str, ...] = ()
    headers: dict[str, str] = field(default_factory=dict)
    contact_id: uuid.UUID | None = None
    office_id: uuid.UUID | None = None
    sender_id: uuid.UUID | None = None
    enrollment_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ProviderMessageRef:
    """What the provider returned for a sent email."""

    provider: str
    message_id: str
    thread_id: str
    sent_at: datetime


class ProviderEventType(StrEnum):
    """Inbound events a provider reports through its webhook."""

    REPLY = "reply"
    BOUNCE = "bounce"
    COMPLAINT = "complaint"


@dataclass(frozen=True)
class ProviderEvent:
    """A normalised inbound provider event (parsed from a webhook)."""

    type: ProviderEventType
    event_id: str
    occurred_at: datetime
    data: dict[str, Any]


class SendingProvider(Protocol):
    """Sends email and understands its own webhooks."""

    name: str

    async def send_email(self, message: OutboundMessage) -> ProviderMessageRef:
        """Send a new email (starts a thread)."""
        ...

    async def send_reply_in_thread(self, message: OutboundMessage) -> ProviderMessageRef:
        """Send a reply inside an existing thread (`thread_id` and `in_reply_to` set)."""
        ...

    def parse_webhook(self, payload: dict[str, Any]) -> list[ProviderEvent]:
        """Turn a verified webhook payload into provider events."""
        ...
