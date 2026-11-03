"""SMTP sending provider: queues each email in the outbox; a worker delivers it after commit.

Sending is a two-step transactional outbox:

1. `send_email` (inside the workflow step's transaction, after the guardrails allowed it) stores
   the outbox row as `queued` with its final `Message-ID`, so the thread, the tracking and the
   step all refer to the real message straight away.
2. The delivery worker (`lanereach.deliver_outbox`) sends queued rows through the agent's
   mailbox over SMTP once the transaction committed, and records `sent` or `failed`.

A crash between the two never loses an email (the row stays queued) and never sends one twice
(the row is locked while it is delivered and `idempotency_key` is unique). SMTP I/O never holds
the engine's transaction open.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.core.clock import Clock
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.integrations.sending.models import OutboxMessage, OutboxStatus
from lanereach.integrations.sending.provider import (
    OutboundMessage,
    ProviderEvent,
    ProviderMessageRef,
)

SMTP_PROVIDER = "smtp"


def message_id_for(from_email: str) -> str:
    """A globally unique Message-ID in the sender's own domain (what mail servers expect)."""
    domain = from_email.rsplit("@", 1)[-1].strip().lower() or "lanereach.invalid"
    return f"<{uuid.uuid4().hex}@{domain}>"


class SmtpSendingProvider:
    """Queues emails for delivery through the agents' own mailboxes.

    Args:
        session: Tenant-scoped session (the row commits with the step that sent it).
        clock: Time source.
    """

    name = SMTP_PROVIDER

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def send_email(self, message: OutboundMessage) -> ProviderMessageRef:
        """Queue a new email; its Message-ID starts the thread."""
        return await self._queue(message)

    async def send_reply_in_thread(self, message: OutboundMessage) -> ProviderMessageRef:
        """Queue a reply that keeps the existing thread."""
        if not message.thread_id or not message.in_reply_to:
            raise ValueError("a reply needs thread_id and in_reply_to")
        return await self._queue(message)

    async def _queue(self, message: OutboundMessage) -> ProviderMessageRef:
        existing = await self._session.scalar(
            select(OutboxMessage).where(OutboxMessage.idempotency_key == message.idempotency_key)
        )
        if existing is not None:
            return ProviderMessageRef(
                existing.provider,
                existing.provider_message_id,
                existing.thread_id,
                existing.created_at,
            )
        now = self._clock.now()
        message_id = message_id_for(message.from_email)
        thread_id = message.thread_id or message_id
        headers: dict[str, Any] = {"Message-ID": message_id, **message.headers}
        references = None
        if message.in_reply_to:
            references = " ".join(message.references or (message.in_reply_to,))
            headers["In-Reply-To"] = message.in_reply_to
            headers["References"] = references
        self._session.add(
            OutboxMessage(
                tenant_id=require_session_tenant_id(self._session),
                idempotency_key=message.idempotency_key,
                provider_message_id=message_id,
                thread_id=thread_id,
                in_reply_to=message.in_reply_to,
                references=references,
                kind=message.kind,
                to_email=message.to_email,
                to_name=message.to_name,
                from_email=message.from_email,
                from_name=message.from_name,
                subject=message.subject,
                body=message.body,
                html_body=message.html_body,
                headers=headers,
                contact_id=message.contact_id,
                office_id=message.office_id,
                sender_id=message.sender_id,
                enrollment_id=message.enrollment_id,
                status=OutboxStatus.QUEUED,
                provider=self.name,
                attempts=0,
                next_attempt_at=now,
                created_at=now,
            )
        )
        await self._session.flush()
        return ProviderMessageRef(self.name, message_id, thread_id, now)

    def parse_webhook(self, payload: dict[str, Any]) -> list[ProviderEvent]:
        """SMTP has no webhooks: replies and bounces are read from the mailbox over IMAP."""
        return []
