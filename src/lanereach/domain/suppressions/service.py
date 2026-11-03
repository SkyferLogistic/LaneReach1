"""Suppression business logic: suppress, lift, and unsubscribe from a signed link."""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.enrollments.models import OPEN_ENROLLMENT_STATUSES, Enrollment
from lanereach.automation.enrollments.service import stop_enrollment
from lanereach.core.clock import Clock
from lanereach.core.errors import NotFoundError
from lanereach.core.repository import TenantRepository
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.contacts.models import Contact, ContactStage
from lanereach.domain.suppressions.models import Suppression, SuppressionReason

logger = structlog.get_logger(__name__)

# Permanent suppressions for these reasons also mark the contact Do Not Contact.
_DO_NOT_CONTACT_REASONS = frozenset(
    {SuppressionReason.DO_NOT_CONTACT, SuppressionReason.UNSUBSCRIBED, SuppressionReason.COMPLAINT}
)


class SuppressionRepository(TenantRepository[Suppression]):
    """Queries over suppressions."""

    model = Suppression


class SuppressionService:
    """Suppression operations.

    Args:
        session: Tenant session.
        clock: Time source.
    """

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock
        self.repository = SuppressionRepository(session)

    async def suppress(
        self,
        *,
        contact_id: uuid.UUID | None,
        email: str | None,
        reason: SuppressionReason,
        note: str | None,
        expires_at: datetime | None,
        source: str,
    ) -> Suppression:
        """Add a suppression; a permanent Do-Not-Contact also sets the contact's stage.

        Args:
            contact_id: Contact to suppress (or give `email`).
            email: Address to suppress (matched to a contact when one exists).
            reason: Why.
            note: Free text.
            expires_at: End of a temporary suppression; None is permanent.
            source: Who or what suppressed (`user:<subject>`, `bounce`...).

        Returns:
            The suppression.

        Raises:
            NotFoundError: If `contact_id` does not exist.
        """
        contact = None
        if contact_id is not None:
            contact = await self._session.get(Contact, contact_id)
            if contact is None:
                raise NotFoundError("Contact not found.")
        address = email.lower() if email else contact.email.lower() if contact else ""
        if contact is None:
            contact = await self._session.scalar(
                select(Contact).where(func.lower(Contact.email) == address)
            )
        now = self._clock.now()
        suppression = Suppression(
            tenant_id=require_session_tenant_id(self._session),
            contact_id=contact.id if contact else None,
            email=address,
            reason=reason,
            source=source[:100],
            note=note,
            expires_at=expires_at,
            created_at=now,
        )
        self._session.add(suppression)
        if contact is not None and expires_at is None and reason in _DO_NOT_CONTACT_REASONS:
            contact.stage = ContactStage.DO_NOT_CONTACT
            contact.updated_at = now
        await self._session.flush()
        return suppression

    async def lift(self, suppression_id: uuid.UUID) -> None:
        """Delete a suppression (admin only: it re-enables email).

        Raises:
            NotFoundError: If missing.
        """
        suppression = await self.repository.get(suppression_id)
        if suppression is None:
            raise NotFoundError("Suppression not found.")
        await self.repository.delete(suppression)


async def unsubscribe_contact(session: AsyncSession, clock: Clock, contact_id: uuid.UUID) -> bool:
    """Unsubscribe a contact from a signed link: suppress, Do Not Contact, stop outreach.

    Idempotent: an already unsubscribed contact is left as it is.

    Args:
        session: Session of the tenant named in the signed token.
        clock: Time source.
        contact_id: Contact named in the token.

    Returns:
        True if the contact was unsubscribed now, False if already unsubscribed or unknown.
    """
    contact = await session.get(Contact, contact_id)
    if contact is None or contact.stage is ContactStage.DO_NOT_CONTACT:
        return False
    now = clock.now()
    session.add(
        Suppression(
            tenant_id=contact.tenant_id,
            contact_id=contact.id,
            email=contact.email.lower(),
            reason=SuppressionReason.UNSUBSCRIBED,
            source="unsubscribe_link",
            created_at=now,
        )
    )
    contact.stage = ContactStage.DO_NOT_CONTACT
    contact.updated_at = now
    enrollments = await session.scalars(
        select(Enrollment).where(
            Enrollment.contact_id == contact.id, Enrollment.status.in_(OPEN_ENROLLMENT_STATUSES)
        )
    )
    for enrollment in enrollments.all():
        stop_enrollment(enrollment, "contact unsubscribed", clock)
    logger.info("contact_unsubscribed", contact_id=str(contact.id))
    return True
