"""Dry-run sending provider: records emails in the `outbox` table instead of sending them.

It is the default provider in dev, test and staging, so every flow can be exercised end to end
without a real email leaving the system. It also accepts webhooks in a simple JSON format
(`dry_run`) that tests and the app use to simulate replies, bounces and complaints.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.core.clock import Clock
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.integrations.sending.models import OutboxMessage, OutboxStatus
from lanereach.integrations.sending.provider import (
    OutboundMessage,
    ProviderEvent,
    ProviderEventType,
    ProviderMessageRef,
)

DRY_RUN_PROVIDER = "dry_run"
MESSAGE_ID_DOMAIN = "dryrun.lanereach.invalid"


class DryRunSendingProvider:
    """Writes each email to the outbox and returns a fake Message-ID.

    Args:
        session: Tenant-scoped session (the outbox row commits with the step that sent it).
        clock: Time source.
    """

    name = DRY_RUN_PROVIDER

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def send_email(self, message: OutboundMessage) -> ProviderMessageRef:
        """Record a new email; its Message-ID starts the thread."""
        return await self._record(message)

    async def send_reply_in_thread(self, message: OutboundMessage) -> ProviderMessageRef:
        """Record a reply; it keeps the existing thread."""
        if not message.thread_id or not message.in_reply_to:
            raise ValueError("a reply needs thread_id and in_reply_to")
        return await self._record(message)

    async def _record(self, message: OutboundMessage) -> ProviderMessageRef:
        existing = await self._session.scalar(
            select(OutboxMessage).where(OutboxMessage.idempotency_key == message.idempotency_key)
        )
        if existing is not None:
            return ProviderMessageRef(
                self.name, existing.provider_message_id, existing.thread_id, existing.created_at
            )
        now = self._clock.now()
        message_id = f"<{uuid.uuid4().hex}@{MESSAGE_ID_DOMAIN}>"
        thread_id = message.thread_id or message_id
        headers = {"Message-ID": message_id, **message.headers}
        if message.in_reply_to:
            headers["In-Reply-To"] = message.in_reply_to
            headers["References"] = " ".join(message.references or (message.in_reply_to,))
        row = OutboxMessage(
            tenant_id=require_session_tenant_id(self._session),
            idempotency_key=message.idempotency_key,
            provider_message_id=message_id,
            thread_id=thread_id,
            in_reply_to=message.in_reply_to,
            references=headers.get("References"),
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
            status=OutboxStatus.DRY_RUN,
            provider=self.name,
            created_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return ProviderMessageRef(self.name, message_id, thread_id, now)

    def parse_webhook(self, payload: dict[str, Any]) -> list[ProviderEvent]:
        """Parse the dry-run webhook format.

        Format: `{"events": [{"event_id", "type": "reply|bounce|complaint", "occurred_at",
        ...type-specific fields}]}`. Unknown event types are ignored.

        Args:
            payload: The verified JSON body.

        Returns:
            The events.
        """
        events: list[ProviderEvent] = []
        for raw in payload.get("events", []):
            try:
                event_type = ProviderEventType(raw["type"])
                occurred_at = datetime.fromisoformat(raw["occurred_at"])
            except (KeyError, ValueError, TypeError):
                continue
            events.append(ProviderEvent(event_type, str(raw.get("event_id")), occurred_at, raw))
        return events
