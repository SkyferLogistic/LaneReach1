"""Contact management."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.engine.queue import TaskQueue
from lanereach.automation.events.models import EventType
from lanereach.automation.events.service import publish_event
from lanereach.core.clock import Clock
from lanereach.core.errors import NotFoundError
from lanereach.core.repository import TenantRepository, flush_or_conflict
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.companies.models import Company, CompanyBranch
from lanereach.domain.contacts.models import Contact, ContactStage
from lanereach.domain.contacts.schemas import ContactCreateRequest, ContactUpdateRequest
from lanereach.domain.messages.models import Message
from lanereach.domain.offices.models import Office


class ContactRepository(TenantRepository[Contact]):
    """Queries over contacts."""

    model = Contact

    async def search(
        self,
        *,
        query: str | None,
        tag: str | None,
        company_id: uuid.UUID | None,
        stage: ContactStage | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[Sequence[Contact], str | None]:
        """Contacts newest first with optional filters."""
        statement = select(Contact)
        if query:
            pattern = f"%{query.replace('%', '').replace('_', '')}%"
            statement = statement.where(
                Contact.email.ilike(pattern)
                | Contact.first_name.ilike(pattern)
                | Contact.last_name.ilike(pattern)
            )
        if tag:
            statement = statement.where(Contact.tags.contains([tag]))
        if company_id is not None:
            statement = statement.where(Contact.company_id == company_id)
        if stage is not None:
            statement = statement.where(Contact.stage == stage)
        return await self.page(statement, limit=limit, cursor=cursor)

    async def messages_of(self, contact_id: uuid.UUID) -> Sequence[Message]:
        """The contact's messages, oldest first."""
        rows = await self.session.scalars(
            select(Message).where(Message.contact_id == contact_id).order_by(Message.occurred_at)
        )
        return rows.all()


class ContactService:
    """Contact operations. Creating a contact or adding a tag publishes an event.

    Args:
        session: Tenant session.
        clock: Time source.
        task_queue: Where event processing is enqueued after commit.
    """

    def __init__(self, session: AsyncSession, clock: Clock, task_queue: TaskQueue | None) -> None:
        self._session = session
        self._clock = clock
        self._task_queue = task_queue
        self.repository = ContactRepository(session)

    async def create_contact(self, request: ContactCreateRequest) -> Contact:
        """Create a contact and publish `contact.created`.

        Raises:
            NotFoundError: If a referenced company, branch or office does not exist.
            ConflictError: If the email already exists.
        """
        await self._check_references(request.company_id, request.branch_id, request.office_id)
        now = self._clock.now()
        contact = Contact(
            tenant_id=require_session_tenant_id(self._session),
            **request.model_dump(exclude={"email", "owner_email", "tags"}),
            email=str(request.email).lower(),
            owner_email=str(request.owner_email).lower() if request.owner_email else None,
            tags=list(dict.fromkeys(request.tags)),
            created_at=now,
            updated_at=now,
        )
        self._session.add(contact)
        await flush_or_conflict(
            self._session, f"A contact with email {request.email} already exists."
        )
        await publish_event(
            self._session,
            self._clock,
            event_type=EventType.CONTACT_CREATED,
            contact_id=contact.id,
            payload={"email_domain": contact.email_domain},
            idempotency_key=f"contact.created:{contact.id}",
            task_queue=self._task_queue,
        )
        return contact

    async def require_contact(self, contact_id: uuid.UUID) -> Contact:
        """Load a contact or raise 404."""
        contact = await self.repository.get(contact_id)
        if contact is None:
            raise NotFoundError("Contact not found.")
        return contact

    async def update_contact(self, contact_id: uuid.UUID, request: ContactUpdateRequest) -> Contact:
        """Update contact fields.

        Raises:
            NotFoundError: If the contact or a referenced row does not exist.
        """
        contact = await self.require_contact(contact_id)
        changes = request.model_dump(exclude_unset=True)
        await self._check_references(
            changes.get("company_id"), changes.get("branch_id"), changes.get("office_id")
        )
        if changes.get("owner_email"):
            changes["owner_email"] = str(changes["owner_email"]).lower()
        previous = contact.stage
        for field, value in changes.items():
            setattr(contact, field, value)
        contact.updated_at = self._clock.now()
        if contact.stage is not previous:
            await publish_contact_stage_changed(
                self._session, self._clock, contact, previous, self._task_queue
            )
        return contact

    async def add_tag(self, contact_id: uuid.UUID, tag: str) -> Contact:
        """Add a tag; publishes `contact.tag_added` when the tag is new.

        Raises:
            NotFoundError: If the contact does not exist.
        """
        contact = await self.require_contact(contact_id)
        if any(existing.casefold() == tag.casefold() for existing in contact.tags):
            return contact
        now = self._clock.now()
        contact.tags = [*contact.tags, tag]
        contact.updated_at = now
        await publish_event(
            self._session,
            self._clock,
            event_type=EventType.CONTACT_TAG_ADDED,
            contact_id=contact.id,
            payload={"tag": tag, "source": "api"},
            idempotency_key=f"tag:{contact.id}:{tag.casefold()}:{now.isoformat()}",
            task_queue=self._task_queue,
        )
        return contact

    async def remove_tag(self, contact_id: uuid.UUID, tag: str) -> Contact:
        """Remove a tag (case-insensitive).

        Raises:
            NotFoundError: If the contact does not exist.
        """
        contact = await self.require_contact(contact_id)
        contact.tags = [
            existing for existing in contact.tags if existing.casefold() != tag.casefold()
        ]
        contact.updated_at = self._clock.now()
        return contact

    async def _check_references(
        self, company_id: uuid.UUID | None, branch_id: uuid.UUID | None, office_id: uuid.UUID | None
    ) -> None:
        for model, row_id, label in (
            (Company, company_id, "Company"),
            (CompanyBranch, branch_id, "Branch"),
            (Office, office_id, "Office"),
        ):
            if row_id is not None and await self._session.get(model, row_id) is None:
                raise NotFoundError(f"{label} not found.")


async def publish_contact_stage_changed(
    session: AsyncSession,
    clock: Clock,
    contact: Contact,
    previous: ContactStage,
    task_queue: TaskQueue | None,
) -> None:
    """Publish `contact.stage_changed` (the `trigger_contact_stage_changed` workflows start).

    Args:
        session: Tenant session.
        clock: Time source.
        contact: The contact, already on its new stage.
        previous: The stage it left.
        task_queue: Where event processing is enqueued.
    """
    now = clock.now()
    await publish_event(
        session,
        clock,
        event_type=EventType.CONTACT_STAGE_CHANGED,
        contact_id=contact.id,
        payload={"stage": contact.stage.value, "previous": previous.value},
        idempotency_key=f"contact:{contact.id}:{previous.value}->{contact.stage.value}:{now.isoformat()}",
        task_queue=task_queue,
    )
