"""Recording bookings: contact (created when new), open deal, meeting row, event and notice."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.engine.queue import TaskQueue
from lanereach.automation.events.models import EventType
from lanereach.automation.events.service import publish_event
from lanereach.core.clock import Clock
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.contacts.models import Contact
from lanereach.domain.contacts.schemas import ContactCreateRequest
from lanereach.domain.contacts.service import ContactService
from lanereach.domain.crm.models import Deal, DealStatus
from lanereach.domain.offices.models import Office
from lanereach.integrations.meetings.models import Meeting, MeetingStatus
from lanereach.integrations.meetings.providers import BookingNotice
from lanereach.integrations.notifications.notifier import NotificationMessage, RecordingNotifier
from lanereach.integrations.outbound.models import IntegrationConnection


@dataclass(frozen=True, slots=True)
class BookingOutcome:
    """What a webhook changed."""

    meeting: Meeting
    contact_created: bool


def _split_name(name: str | None) -> tuple[str | None, str | None]:
    if not name:
        return None, None
    first, _, last = name.strip().partition(" ")
    return first[:100] or None, last[:100] or None


async def record_booking(
    session: AsyncSession,
    clock: Clock,
    connection: IntegrationConnection,
    notice: BookingNotice,
    *,
    task_queue: TaskQueue | None,
) -> BookingOutcome:
    """Store a booking change and tell the office.

    A new invitee becomes a contact of the connection's office. The meeting is linked to the
    contact's most recent open deal. A booking or reschedule publishes `meeting.booked` (the
    `trigger_meeting_booked` workflows start), a cancellation `meeting.canceled`.

    Args:
        session: Tenant session of the connection's tenant.
        clock: Time source.
        connection: The Calendly or Cal.com connection the webhook came through.
        notice: The verified booking.
        task_queue: Where event processing is enqueued.

    Returns:
        The meeting and whether the contact was created.
    """
    now = clock.now()
    office_id_text = connection.config.get("office_id")
    office_id = uuid.UUID(office_id_text) if office_id_text else None
    contact = await session.scalar(select(Contact).where(Contact.email == notice.invitee_email))
    created = False
    if contact is None:
        first, last = _split_name(notice.invitee_name)
        contact = await ContactService(session, clock, task_queue).create_contact(
            ContactCreateRequest(
                email=notice.invitee_email,
                first_name=first,
                last_name=last,
                office_id=office_id,
                tags=["meeting-booked"],
            )
        )
        created = True
    deal_id = await session.scalar(
        select(Deal.id)
        .where(Deal.contact_id == contact.id, Deal.status == DealStatus.OPEN)
        .order_by(Deal.updated_at.desc())
        .limit(1)
    )
    provider = connection.kind.value
    meeting = await session.scalar(
        select(Meeting).where(
            Meeting.provider == provider, Meeting.external_id == notice.external_id
        )
    )
    if meeting is None:
        meeting = Meeting(
            tenant_id=require_session_tenant_id(session),
            contact_id=contact.id,
            deal_id=deal_id,
            connection_id=connection.id,
            provider=provider,
            external_id=notice.external_id,
            created_at=now,
            title=notice.title,
            starts_at=notice.starts_at,
            status=notice.status,
            invitee_email=notice.invitee_email,
            updated_at=now,
        )
        session.add(meeting)
    meeting.title = notice.title
    meeting.starts_at = notice.starts_at
    meeting.ends_at = notice.ends_at
    meeting.status = notice.status
    meeting.join_url = notice.join_url
    meeting.invitee_name = notice.invitee_name
    meeting.organizer_email = notice.organizer_email
    meeting.deal_id = meeting.deal_id or deal_id
    meeting.updated_at = now
    await session.flush()
    connection.last_used_at = now
    connection.last_error = None
    canceled = notice.status is MeetingStatus.CANCELED
    await publish_event(
        session,
        clock,
        event_type=EventType.MEETING_CANCELED if canceled else EventType.MEETING_BOOKED,
        contact_id=contact.id,
        payload={
            "meeting_id": str(meeting.id),
            "deal_id": str(meeting.deal_id) if meeting.deal_id else None,
            "provider": provider,
            "title": meeting.title,
            "starts_at": meeting.starts_at.isoformat(),
            "status": meeting.status.value,
        },
        idempotency_key=f"meeting:{meeting.id}:{meeting.status.value}:{meeting.starts_at.isoformat()}",
        task_queue=task_queue,
    )
    office = await session.get(Office, contact.office_id) if contact.office_id else None
    if office is not None and office.owner_email:
        verb = (
            "canceled"
            if canceled
            else "rescheduled"
            if notice.status is MeetingStatus.RESCHEDULED
            else "booked"
        )
        await RecordingNotifier(session, clock).notify(
            NotificationMessage(
                channel="office_owner",
                recipient=office.owner_email,
                subject=f"{contact.display_name} {verb} a meeting: {meeting.title}",
                body=f"{meeting.starts_at:%a %d %b %Y %H:%M} UTC"
                + (f" · {meeting.join_url}" if meeting.join_url and not canceled else ""),
                payload={"meeting_id": str(meeting.id), "provider": provider},
                contact_id=contact.id,
            )
        )
    return BookingOutcome(meeting, created)
