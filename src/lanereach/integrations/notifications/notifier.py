"""Notification interface and the recording implementation.

Every notification is recorded in the `notifications` table (shown in the app). When a Teams
channel has an incoming-webhook URL configured, `TeamsWebhookNotifier` also posts it there.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.core.clock import Clock
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.integrations.notifications.models import Notification

TEAMS_CHANNEL_PREFIX = "teams:"


@dataclass(frozen=True)
class NotificationMessage:
    """Something a person or channel should know about.

    Attributes:
        channel: `office_owner`, `pricing`, `review` or `teams:<channel>`.
        recipient: Email address or channel name.
        subject: One-line summary.
        body: Details (no raw email bodies; summaries only).
        payload: Structured data for the app (IDs, bucket, shipment summary).
    """

    channel: str
    recipient: str
    subject: str
    body: str
    payload: dict[str, Any] = field(default_factory=dict)
    contact_id: uuid.UUID | None = None
    enrollment_id: uuid.UUID | None = None


class Notifier(Protocol):
    """Delivers notifications."""

    async def notify(self, message: NotificationMessage) -> None:
        """Deliver one notification."""
        ...


class RecordingNotifier:
    """Stores notifications in the tenant's `notifications` table.

    Args:
        session: Tenant-scoped session (the row commits with the action that caused it).
        clock: Time source.
        delivered_via: Label stored on the row.
    """

    def __init__(self, session: AsyncSession, clock: Clock, *, delivered_via: str = "app") -> None:
        self._session = session
        self._clock = clock
        self._delivered_via = delivered_via

    async def notify(self, message: NotificationMessage) -> None:
        """Record the notification."""
        self._session.add(
            Notification(
                tenant_id=require_session_tenant_id(self._session),
                channel=message.channel,
                recipient=message.recipient,
                subject=message.subject,
                body=message.body,
                payload=message.payload,
                contact_id=message.contact_id,
                enrollment_id=message.enrollment_id,
                delivered_via=self._delivered_via,
                created_at=self._clock.now(),
            )
        )
        await self._session.flush()
