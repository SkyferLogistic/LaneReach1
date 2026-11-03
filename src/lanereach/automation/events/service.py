"""Publishing and processing events."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.engine.queue import TaskQueue
from lanereach.automation.events.models import Event, EventStatus, EventType
from lanereach.core.clock import Clock
from lanereach.core.repository import TenantRepository
from lanereach.core.tenancy import require_session_tenant_id, run_after_commit

logger = structlog.get_logger(__name__)


async def publish_event(
    session: AsyncSession,
    clock: Clock,
    *,
    event_type: EventType,
    contact_id: uuid.UUID | None,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
    occurred_at: datetime | None = None,
    task_queue: TaskQueue | None = None,
) -> Event:
    """Store an event in the same transaction as the change that caused it.

    Publishing twice with the same `idempotency_key` returns the first event. When a task queue
    is given, processing is enqueued once the transaction commits.

    Args:
        session: Tenant session.
        clock: Time source.
        event_type: What happened.
        contact_id: The contact it concerns.
        payload: Event data (for example `{"tag": "uae-importers"}`).
        idempotency_key: Deduplication key.
        occurred_at: When it happened (defaults to now).
        task_queue: Where to enqueue processing after commit.

    Returns:
        The stored (or previously stored) event.
    """
    now = clock.now()
    tenant_id = require_session_tenant_id(session)
    event_id = await session.scalar(
        insert(Event)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            type=event_type,
            contact_id=contact_id,
            payload=payload,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at or now,
            status=EventStatus.PENDING,
            enrollments_created=0,
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "idempotency_key"])
        .returning(Event.id)
    )
    if event_id is None:
        existing = await session.scalar(
            select(Event).where(Event.idempotency_key == idempotency_key)
        )
        assert existing is not None  # noqa: S101 - the conflicting row exists
        return existing
    event = await session.get(Event, event_id)
    assert event is not None  # noqa: S101 - just inserted
    if task_queue is not None:

        async def enqueue() -> None:
            await task_queue.enqueue_event(tenant_id, event_id)

        run_after_commit(session, enqueue)
    logger.info("event_published", event_type=event_type.value, event_id=str(event_id))
    return event


class EventRepository(TenantRepository[Event]):
    """Queries over `events`."""

    model = Event

    async def list_events(
        self,
        *,
        event_type: EventType | None,
        status: EventStatus | None,
        contact_id: uuid.UUID | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[Sequence[Event], str | None]:
        """List events newest first.

        Args:
            event_type: Optional type filter.
            status: Optional status filter.
            contact_id: Optional contact filter.
            limit: Page size.
            cursor: Cursor from the previous page.

        Returns:
            The page and the next cursor.
        """
        statement = select(Event)
        if event_type is not None:
            statement = statement.where(Event.type == event_type)
        if status is not None:
            statement = statement.where(Event.status == status)
        if contact_id is not None:
            statement = statement.where(Event.contact_id == contact_id)
        return await self.page(statement, limit=limit, cursor=cursor)

    async def list_pending_ids(self, limit: int) -> list[uuid.UUID]:
        """IDs of events not processed yet, oldest first.

        Args:
            limit: Maximum number.

        Returns:
            Event IDs.
        """
        rows = await self.session.scalars(
            select(Event.id)
            .where(Event.status == EventStatus.PENDING)
            .order_by(Event.occurred_at)
            .limit(limit)
        )
        return list(rows.all())


@dataclass(frozen=True)
class EventProcessingOutcome:
    """What processing an event did."""

    event_id: uuid.UUID
    status: EventStatus
    enrollment_ids: list[uuid.UUID]
