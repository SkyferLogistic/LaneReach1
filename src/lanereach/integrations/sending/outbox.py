"""The sent log: search the outbox, count by status and show an email's whole conversation."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.core.errors import NotFoundError
from lanereach.core.repository import TenantRepository
from lanereach.domain.messages.models import Message
from lanereach.integrations.sending.models import OutboxMessage, OutboxStatus

MAX_THREAD_MESSAGES = 100
_SEARCH_MAX = 100


class OutboxRepository(TenantRepository[OutboxMessage]):
    """Queries over the outbox."""

    model = OutboxMessage


@dataclass(frozen=True, slots=True)
class OutboxFilter:
    """What the sent log is narrowed to (every field optional)."""

    search: str | None = None
    status: OutboxStatus | None = None
    kind: str | None = None
    sender_id: uuid.UUID | None = None
    office_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    enrollment_id: uuid.UUID | None = None
    sent_from: date | None = None
    sent_to: date | None = None


def filter_outbox[StatementT: Select[*tuple[Any, ...]]](
    statement: StatementT, query: OutboxFilter
) -> StatementT:
    """Apply a filter to a select over the outbox (rows or aggregates).

    Args:
        statement: The base statement.
        query: The filter.

    Returns:
        The narrowed statement.
    """
    if query.search:
        # Escape LIKE wildcards: the search is literal text, not a pattern.
        needle = query.search.strip()[:_SEARCH_MAX]
        needle = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{needle}%"
        statement = statement.where(
            or_(
                OutboxMessage.subject.ilike(pattern, escape="\\"),
                OutboxMessage.to_email.ilike(pattern, escape="\\"),
                OutboxMessage.to_name.ilike(pattern, escape="\\"),
                OutboxMessage.from_email.ilike(pattern, escape="\\"),
            )
        )
    if query.status is not None:
        statement = statement.where(OutboxMessage.status == query.status)
    if query.kind:
        statement = statement.where(OutboxMessage.kind == query.kind)
    for column, value in (
        (OutboxMessage.sender_id, query.sender_id),
        (OutboxMessage.office_id, query.office_id),
        (OutboxMessage.contact_id, query.contact_id),
        (OutboxMessage.enrollment_id, query.enrollment_id),
    ):
        if value is not None:
            statement = statement.where(column == value)
    # Dates are whole UTC days, both ends included.
    if query.sent_from is not None:
        start = datetime.combine(query.sent_from, time.min, tzinfo=UTC)
        statement = statement.where(OutboxMessage.created_at >= start)
    if query.sent_to is not None:
        end = datetime.combine(query.sent_to + timedelta(days=1), time.min, tzinfo=UTC)
        statement = statement.where(OutboxMessage.created_at < end)
    return statement


async def list_outbox(
    session: AsyncSession, query: OutboxFilter, *, limit: int, cursor: str | None
) -> tuple[Sequence[OutboxMessage], str | None]:
    """A newest-first page of the sent log.

    Args:
        session: Tenant session.
        query: Filters.
        limit: Page size.
        cursor: Cursor of the previous page.

    Returns:
        The rows and the next cursor.
    """
    statement = filter_outbox(select(OutboxMessage), query)
    return await OutboxRepository(session).page(statement, limit=limit, cursor=cursor)


async def count_outbox_by_status(session: AsyncSession, query: OutboxFilter) -> dict[str, int]:
    """How many emails match the filter, per status (the status filter itself is ignored).

    Args:
        session: Tenant session.
        query: Filters.

    Returns:
        `all` plus one count per status.
    """
    unfiltered = replace(query, status=None)
    statement = filter_outbox(
        select(OutboxMessage.status, func.count()).group_by(OutboxMessage.status),
        unfiltered,
    )
    counts = {status.value: 0 for status in OutboxStatus}
    for status, count in (await session.execute(statement)).all():
        counts[str(status)] = int(count)
    counts["all"] = sum(counts.values())
    return counts


async def require_outbox_message(session: AsyncSession, message_id: uuid.UUID) -> OutboxMessage:
    """One outbox email.

    Raises:
        NotFoundError: If it does not exist in the tenant.
    """
    row = await OutboxRepository(session).get(message_id)
    if row is None:
        raise NotFoundError("Outbox message not found.")
    return row


async def load_outbox_thread(session: AsyncSession, message_id: uuid.UUID) -> list[Message]:
    """Every message of the email's conversation, oldest first (outbound and replies).

    Args:
        session: Tenant session.
        message_id: An outbox email.

    Returns:
        The conversation's messages (at most 100).

    Raises:
        NotFoundError: If the email does not exist in the tenant.
    """
    row = await require_outbox_message(session, message_id)
    return list(
        (
            await session.scalars(
                select(Message)
                .where(Message.thread_id == row.thread_id)
                .order_by(Message.occurred_at, Message.created_at)
                .limit(MAX_THREAD_MESSAGES)
            )
        ).all()
    )
