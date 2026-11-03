"""Erasing a contact: the "right to be forgotten" and the console's delete button.

Deleting the contact row cascades to its messages, enrollments, leads, deals and meetings. Rows
that only point at the contact without a foreign key, or keep a copy of personal data (outbox
copies, notifications, events and the webhook/Slack deliveries made from them, review drafts,
notes, form submissions), are removed explicitly first. One thing is kept on purpose: a
suppression of the email address, so a later import of the same person never emails them.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.ai.review.models import ReviewItem
from lanereach.automation.events.models import Event
from lanereach.core.clock import Clock
from lanereach.core.errors import NotFoundError
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.contacts.models import Contact
from lanereach.domain.crm.models import Note
from lanereach.domain.forms.models import FormSubmission
from lanereach.domain.suppressions.models import Suppression, SuppressionReason
from lanereach.integrations.notifications.models import Notification
from lanereach.integrations.outbound.models import IntegrationDelivery
from lanereach.integrations.sending.models import OutboxMessage


async def erase_contact(
    session: AsyncSession, clock: Clock, contact_id: uuid.UUID, actor: str
) -> str:
    """Delete a contact and every copy of their personal data; keep their address suppressed.

    Args:
        session: Tenant session.
        clock: Time source.
        contact_id: The contact.
        actor: Who erased them (kept on the suppression, not the person's data).

    Returns:
        The erased address (for the confirmation message).

    Raises:
        NotFoundError: If the contact does not exist in the tenant.
    """
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise NotFoundError("Contact not found.")
    email = contact.email.lower()
    now = clock.now()
    suppressed = await session.scalar(
        select(Suppression.id).where(
            func.lower(Suppression.email) == email, Suppression.expires_at.is_(None)
        )
    )
    if suppressed is None:
        session.add(
            Suppression(
                tenant_id=require_session_tenant_id(session),
                contact_id=None,
                email=email,
                reason=SuppressionReason.DO_NOT_CONTACT,
                source=f"erase:{actor}"[:100],
                note="Contact erased; kept so the address is never emailed again.",
                expires_at=None,
                created_at=now,
            )
        )
    event_ids = select(Event.id).where(Event.contact_id == contact.id)
    for statement in (
        delete(IntegrationDelivery).where(IntegrationDelivery.event_id.in_(event_ids)),
        delete(Event).where(Event.contact_id == contact.id),
        delete(OutboxMessage).where(OutboxMessage.contact_id == contact.id),
        delete(Notification).where(Notification.contact_id == contact.id),
        delete(ReviewItem).where(ReviewItem.contact_id == contact.id),
        delete(Note).where(Note.contact_id == contact.id),
        delete(FormSubmission).where(FormSubmission.contact_id == contact.id),
    ):
        await session.execute(statement.execution_options(synchronize_session=False))
    await session.delete(contact)
    await session.flush()
    return email
