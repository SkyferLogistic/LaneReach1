"""Hand-off of engine work to background workers.

Work is always enqueued *after commit* (see `run_after_commit`), and the periodic dispatcher
re-discovers anything that was never enqueued (for example if Redis was briefly down), so an
enqueue failure delays work but never loses it.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol

from celery import Celery

EVENT_TASK = "lanereach.process_event"
ADVANCE_TASK = "lanereach.advance_enrollment"
INBOUND_TASK = "lanereach.process_inbound_message"


class TaskQueue(Protocol):
    """Where engine work is sent."""

    async def enqueue_event(self, tenant_id: uuid.UUID, event_id: uuid.UUID) -> None:
        """Process an event (match it to triggers)."""
        ...

    async def enqueue_advance(self, tenant_id: uuid.UUID, enrollment_id: uuid.UUID) -> None:
        """Advance an enrollment."""
        ...

    async def enqueue_inbound_message(self, tenant_id: uuid.UUID, message_id: uuid.UUID) -> None:
        """Run the inbound reply pipeline for a stored message."""
        ...


class CeleryTaskQueue:
    """Sends tasks to Celery by name (the worker imports the implementations).

    Args:
        celery_app: Configured Celery application.
    """

    def __init__(self, celery_app: Celery) -> None:
        self._celery = celery_app

    async def enqueue_event(self, tenant_id: uuid.UUID, event_id: uuid.UUID) -> None:
        """Send `process_event`."""
        self._celery.send_task(EVENT_TASK, args=[str(tenant_id), str(event_id)])

    async def enqueue_advance(self, tenant_id: uuid.UUID, enrollment_id: uuid.UUID) -> None:
        """Send `advance_enrollment`."""
        self._celery.send_task(ADVANCE_TASK, args=[str(tenant_id), str(enrollment_id)])

    async def enqueue_inbound_message(self, tenant_id: uuid.UUID, message_id: uuid.UUID) -> None:
        """Send `process_inbound_message`."""
        self._celery.send_task(INBOUND_TASK, args=[str(tenant_id), str(message_id)])


type WorkHandler = Callable[[uuid.UUID, uuid.UUID], Awaitable[object]]


class InlineTaskQueue:
    """Runs work immediately in-process (tests, single-process demos).

    Handlers are bound late (`bind`) because they need the application container, which in
    turn owns this queue.
    """

    def __init__(self) -> None:
        self._event: WorkHandler | None = None
        self._advance: WorkHandler | None = None
        self._inbound: WorkHandler | None = None

    def bind(
        self,
        *,
        process_event: WorkHandler,
        advance_enrollment: WorkHandler,
        process_inbound_message: WorkHandler,
    ) -> None:
        """Attach the handlers that do the work.

        Args:
            process_event: `(tenant_id, event_id)` handler.
            advance_enrollment: `(tenant_id, enrollment_id)` handler.
            process_inbound_message: `(tenant_id, message_id)` handler.
        """
        self._event = process_event
        self._advance = advance_enrollment
        self._inbound = process_inbound_message

    async def enqueue_event(self, tenant_id: uuid.UUID, event_id: uuid.UUID) -> None:
        """Process the event now."""
        if self._event is not None:
            await self._event(tenant_id, event_id)

    async def enqueue_advance(self, tenant_id: uuid.UUID, enrollment_id: uuid.UUID) -> None:
        """Advance the enrollment now."""
        if self._advance is not None:
            await self._advance(tenant_id, enrollment_id)

    async def enqueue_inbound_message(self, tenant_id: uuid.UUID, message_id: uuid.UUID) -> None:
        """Process the inbound message now."""
        if self._inbound is not None:
            await self._inbound(tenant_id, message_id)


class RecordingTaskQueue:
    """Remembers what was enqueued without running it (unit tests)."""

    def __init__(self) -> None:
        self.events: list[tuple[uuid.UUID, uuid.UUID]] = []
        self.advances: list[tuple[uuid.UUID, uuid.UUID]] = []
        self.inbound: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def enqueue_event(self, tenant_id: uuid.UUID, event_id: uuid.UUID) -> None:
        """Record the event."""
        self.events.append((tenant_id, event_id))

    async def enqueue_advance(self, tenant_id: uuid.UUID, enrollment_id: uuid.UUID) -> None:
        """Record the advance."""
        self.advances.append((tenant_id, enrollment_id))

    async def enqueue_inbound_message(self, tenant_id: uuid.UUID, message_id: uuid.UUID) -> None:
        """Record the inbound message."""
        self.inbound.append((tenant_id, message_id))
