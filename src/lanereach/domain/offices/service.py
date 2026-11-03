"""Office and sender management."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.workflows.usage import describe_users, find_workflows_using
from lanereach.core.clock import Clock
from lanereach.core.errors import ConflictError, NotFoundError
from lanereach.core.repository import TenantRepository, flush_or_conflict
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.assets.models import AssetKind
from lanereach.domain.assets.service import AssetService
from lanereach.domain.contacts.models import Contact
from lanereach.domain.messages.models import Message
from lanereach.domain.offices.models import Office
from lanereach.domain.offices.schemas import (
    OfficeCreateRequest,
    OfficeUpdateRequest,
    SenderCreateRequest,
    SenderUpdateRequest,
)
from lanereach.domain.senders.models import Sender, SignatureMode
from lanereach.domain.senders.signature import clean_custom_signature


class OfficeRepository(TenantRepository[Office]):
    """Queries over offices."""

    model = Office

    async def list_all(self) -> Sequence[Office]:
        """Every office, by name."""
        return (await self.session.scalars(select(Office).order_by(Office.name))).all()


class SenderRepository(TenantRepository[Sender]):
    """Queries over senders."""

    model = Sender

    async def list_for(self, office_id: uuid.UUID | None) -> Sequence[Sender]:
        """Senders, optionally of one office, by email."""
        statement = select(Sender).order_by(Sender.email)
        if office_id is not None:
            statement = statement.where(Sender.office_id == office_id)
        return (await self.session.scalars(statement)).all()


class OfficeService:
    """Creates and updates offices and their sender mailboxes.

    Args:
        session: Tenant session.
        clock: Time source.
    """

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock
        self.offices = OfficeRepository(session)
        self.senders = SenderRepository(session)

    async def create_office(self, request: OfficeCreateRequest) -> Office:
        """Create an office.

        Raises:
            ConflictError: If the name is taken.
        """
        now = self._clock.now()
        office = Office(
            tenant_id=require_session_tenant_id(self._session),
            **request.model_dump(),
            created_at=now,
            updated_at=now,
        )
        office.owner_email = office.owner_email.lower()
        self._session.add(office)
        await flush_or_conflict(self._session, f"An office named '{request.name}' already exists.")
        return office

    async def require_office(self, office_id: uuid.UUID) -> Office:
        """Load an office or raise 404."""
        office = await self.offices.get(office_id)
        if office is None:
            raise NotFoundError("Office not found.")
        return office

    async def update_office(self, office_id: uuid.UUID, request: OfficeUpdateRequest) -> Office:
        """Update an office; `sending_paused` flips the office kill switch.

        Raises:
            NotFoundError: If missing.
            ConflictError: If the new name is taken.
        """
        office = await self.require_office(office_id)
        changes = request.model_dump(exclude_unset=True)
        paused = changes.pop("sending_paused", None)
        for field, value in changes.items():
            setattr(office, field, value)
        now = self._clock.now()
        if paused is True and office.sending_paused_at is None:
            office.sending_paused_at = now
        elif paused is False:
            office.sending_paused_at = None
        office.updated_at = now
        await flush_or_conflict(self._session, "An office with that name already exists.")
        return office

    async def create_sender(self, request: SenderCreateRequest) -> Sender:
        """Add a sender mailbox to an office.

        Raises:
            NotFoundError: If the office does not exist in the tenant.
            ConflictError: If the email is already a sender.
        """
        await self.require_office(request.office_id)
        now = self._clock.now()
        sender = Sender(
            tenant_id=require_session_tenant_id(self._session),
            office_id=request.office_id,
            display_name=request.display_name,
            email=str(request.email).lower(),
            daily_cap=request.daily_cap,
            is_active=request.is_active,
            service_line=request.service_line,
            job_title=request.job_title,
            phone=request.phone,
            mobile=request.mobile,
            signature_mode=request.signature_mode,
            signature_html=clean_custom_signature(request.signature_html)
            if request.signature_html
            else None,
            signature_asset_id=request.signature_asset_id,
            created_at=now,
            updated_at=now,
        )
        self._session.add(sender)
        await flush_or_conflict(self._session, f"{request.email} is already a sender.")
        return sender

    async def update_sender(self, sender_id: uuid.UUID, request: SenderUpdateRequest) -> Sender:
        """Update a sender (cap, active flag, clearing a block flag).

        Raises:
            NotFoundError: If missing.
        """
        sender = await self.senders.get(sender_id)
        if sender is None:
            raise NotFoundError("Sender not found.")
        changes = request.model_dump(exclude_unset=True)
        if changes.pop("clear_flag", False):
            sender.flagged_at = None
            sender.flag_reason = None
        if changes.get("signature_html"):
            changes["signature_html"] = clean_custom_signature(changes["signature_html"])
        for field, value in changes.items():
            setattr(sender, field, value)
        sender.updated_at = self._clock.now()
        return sender

    async def delete_office(self, office_id: uuid.UUID) -> None:
        """Delete an office that nothing depends on any more.

        Refused while agents or contacts belong to it, while it has email history, or while
        a workflow sends from it: pausing the office (kill switch) is the way to stop it.

        Raises:
            NotFoundError: If missing.
            ConflictError: When something still depends on it.
        """
        office = await self.require_office(office_id)
        agents = await self._session.scalar(
            select(func.count()).where(Sender.office_id == office.id)
        )
        contacts = await self._session.scalar(
            select(func.count()).where(Contact.office_id == office.id)
        )
        if agents or contacts:
            raise ConflictError(
                f"'{office.name}' still has {agents or 0} agent(s) and {contacts or 0} "
                "contact(s). Move or delete them first, or pause the office instead."
            )
        if await self._session.scalar(
            select(Message.id).where(Message.office_id == office.id).limit(1)
        ):
            raise ConflictError(
                f"'{office.name}' has email history. Pause the office instead of deleting it."
            )
        users = await find_workflows_using(self._session, "office_id", office.id)
        if users:
            raise ConflictError(f"'{office.name}' is used by {describe_users(users)}.")
        await self._session.delete(office)
        await self._session.flush()

    async def delete_sender(self, sender_id: uuid.UUID) -> None:
        """Delete an agent that never sent an email.

        An agent with history is switched off instead: its threads and reports keep its name,
        and a replying contact keeps talking to the same agent.

        Raises:
            NotFoundError: If missing.
            ConflictError: When the agent sent email or a workflow uses it.
        """
        sender = await self.senders.get(sender_id)
        if sender is None:
            raise NotFoundError("Sender not found.")
        if await self._session.scalar(
            select(Message.id).where(Message.sender_id == sender.id).limit(1)
        ):
            raise ConflictError(
                f"{sender.display_name} has sent email. Switch the agent off instead: the "
                "history and the agent's threads stay intact."
            )
        users = await find_workflows_using(self._session, "sender_id", sender.id)
        if users:
            raise ConflictError(f"{sender.display_name} is used by {describe_users(users)}.")
        await self._session.delete(sender)
        await self._session.flush()

    async def set_office_signature_image(
        self, office_id: uuid.UUID, *, filename: str, content: bytes
    ) -> Office:
        """Use a picture (PNG/JPEG/GIF/WebP, up to 5 MB) as the office's email signature.

        The text signature stays: plain-text emails and AI reply drafts still use it.

        Raises:
            NotFoundError: If the office does not exist in the tenant.
            BusinessRuleViolationError: For non-images or oversized files.
        """
        office = await self.require_office(office_id)
        asset = await AssetService(self._session, self._clock).store_image(
            kind=AssetKind.SIGNATURE, filename=filename, content=content
        )
        office.signature_asset_id = asset.id
        office.updated_at = self._clock.now()
        return office

    async def clear_office_signature_image(self, office_id: uuid.UUID) -> Office:
        """Go back to the text signature.

        Raises:
            NotFoundError: If the office does not exist in the tenant.
        """
        office = await self.require_office(office_id)
        office.signature_asset_id = None
        office.updated_at = self._clock.now()
        return office

    async def set_sender_signature_image(
        self, sender_id: uuid.UUID, *, filename: str, content: bytes
    ) -> Sender:
        """Use a picture as the agent's signature (switches the agent to image mode).

        Raises:
            NotFoundError: If the agent does not exist in the tenant.
            BusinessRuleViolationError: For non-images or oversized files.
        """
        sender = await self.senders.get(sender_id)
        if sender is None:
            raise NotFoundError("Sender not found.")
        asset = await AssetService(self._session, self._clock).store_image(
            kind=AssetKind.SIGNATURE, filename=filename, content=content
        )
        sender.signature_asset_id = asset.id
        sender.signature_mode = SignatureMode.IMAGE
        sender.updated_at = self._clock.now()
        return sender
