"""Inbound provider events: store replies, bounces and complaints as inbound messages.

The webhook router verifies the signature and replay protection; this module matches each
event to a contact and thread and stores it. Processing (classification and actions) happens
afterwards in the reply pipeline, enqueued after the transaction commits.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.engine.queue import TaskQueue
from lanereach.core.clock import Clock
from lanereach.core.tenancy import require_session_tenant_id, run_after_commit
from lanereach.domain.contacts.models import Contact
from lanereach.domain.messages.models import Message, MessageDirection, MessageKind
from lanereach.integrations.sending.provider import ProviderEvent, ProviderEventType

logger = structlog.get_logger(__name__)

BOUNCE_TYPES = ("hard", "soft", "blocked")
_MAX_SUBJECT = 300


@dataclass(frozen=True)
class IngestedEvent:
    """What happened to one provider event."""

    event_id: str
    type: str
    message_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    skipped_reason: str | None = None


def _text(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


async def _find_original(session: AsyncSession, message_id: str) -> Message | None:
    if not message_id:
        return None
    return await session.scalar(
        select(Message).where(
            Message.message_id == message_id, Message.direction == MessageDirection.OUTBOUND
        )
    )


async def _find_contact(session: AsyncSession, email: str) -> Contact | None:
    if not email:
        return None
    return await session.scalar(
        select(Contact).where(func.lower(Contact.email) == email.lower()).limit(1)
    )


async def _latest_outbound(session: AsyncSession, contact_id: uuid.UUID) -> Message | None:
    return await session.scalar(
        select(Message)
        .where(Message.contact_id == contact_id, Message.direction == MessageDirection.OUTBOUND)
        .order_by(Message.occurred_at.desc())
        .limit(1)
    )


async def ingest_provider_event(
    session: AsyncSession, clock: Clock, *, provider: str, event: ProviderEvent
) -> IngestedEvent:
    """Store one provider event as an inbound message.

    Matching: the email it answers (`in_reply_to` for replies, `original_message_id` for
    bounces and complaints) identifies contact and thread; otherwise the sender (replies) or
    recipient (bounces, complaints) address identifies the contact and the latest thread with
    them. Events that match no contact are skipped: LaneReach only processes answers to its
    own outreach.

    Args:
        session: Tenant session of the event's tenant.
        clock: Time source.
        provider: Provider name.
        event: The parsed event.

    Returns:
        What was stored.
    """
    data = event.data
    is_reply = event.type is ProviderEventType.REPLY
    answered_id = _text(data, "in_reply_to" if is_reply else "original_message_id")
    original = await _find_original(session, answered_id)
    contact: Contact | None
    if original is not None:
        contact = await session.get(Contact, original.contact_id)
    else:
        contact = await _find_contact(
            session, _text(data, "from_email" if is_reply else "recipient")
        )
        if contact is not None:
            original = await _latest_outbound(session, contact.id)
    if contact is None:
        logger.info("provider_event_unmatched", event_id=event.event_id, type=event.type.value)
        return IngestedEvent(event.event_id, event.type.value, None, None, "no matching contact")

    message_id = _text(data, "message_id") or f"<{provider}.{event.event_id}@events.invalid>"
    duplicate = await session.scalar(select(Message.id).where(Message.message_id == message_id))
    if duplicate is not None:
        return IngestedEvent(
            event.event_id, event.type.value, duplicate, contact.id, "message already stored"
        )

    hint: str | None = None
    if event.type is ProviderEventType.BOUNCE:
        bounce_type = _text(data, "bounce_type").lower()
        hint = "blocked" if bounce_type == "blocked" else f"bounce_{bounce_type or 'hard'}"
        if bounce_type not in ("", *BOUNCE_TYPES):
            hint = "bounce_hard"
    elif event.type is ProviderEventType.COMPLAINT:
        hint = "complaint"

    kind = {
        ProviderEventType.REPLY: MessageKind.REPLY,
        ProviderEventType.BOUNCE: MessageKind.BOUNCE,
        ProviderEventType.COMPLAINT: MessageKind.COMPLAINT,
    }[event.type]
    subject = _text(data, "subject") or (original.subject if original else "")
    message = Message(
        tenant_id=require_session_tenant_id(session),
        contact_id=contact.id,
        direction=MessageDirection.INBOUND,
        kind=kind,
        message_id=message_id,
        thread_id=original.thread_id if original else message_id,
        in_reply_to=original.message_id if original else None,
        subject=subject[:_MAX_SUBJECT],
        body=_text(data, "body") or _text(data, "diagnostic") or None,
        from_email=_text(data, "from_email") or contact.email,
        to_email=_text(data, "to_email") or (original.from_email if original else ""),
        office_id=original.office_id if original else contact.office_id,
        sender_id=original.sender_id if original else None,
        enrollment_id=original.enrollment_id if original else None,
        provider=provider,
        occurred_at=event.occurred_at,
        processing_result={"hint": hint} if hint else None,
        created_at=clock.now(),
    )
    session.add(message)
    await session.flush()
    logger.info(
        "inbound_message_stored",
        message_id=str(message.id),
        kind=kind.value,
        contact_id=str(contact.id),
    )
    return IngestedEvent(event.event_id, event.type.value, message.id, contact.id)


def enqueue_inbound_processing(
    session: AsyncSession, task_queue: TaskQueue, message_ids: list[uuid.UUID]
) -> None:
    """Run the reply pipeline for stored messages once the transaction commits.

    Args:
        session: The session that stored the messages.
        task_queue: Where the work goes.
        message_ids: Stored inbound messages.
    """
    if not message_ids:
        return
    tenant_id = require_session_tenant_id(session)
    pending = list(message_ids)

    async def enqueue() -> None:
        for message_id in pending:
            await task_queue.enqueue_inbound_message(tenant_id, message_id)

    run_after_commit(session, enqueue)
