"""Email verification endpoints: one contact, or a batch."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select

from lanereach.auth.dependencies import TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import NotFoundError
from lanereach.core.openapi import WRITE
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.core.secrets import build_secret_box
from lanereach.domain.contacts.models import Contact
from lanereach.integrations.verification.service import (
    MAX_BATCH,
    build_email_verifier,
    verify_contact_emails,
)

router = APIRouter(prefix="/contacts", tags=["Contacts"])

Writer = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_WRITE))]


class EmailVerificationResponse(ResponseModel):
    """The verdict on one contact's address."""

    contact_id: uuid.UUID
    email: str
    email_status: str
    reason: str
    email_verified_at: datetime | None
    verifier: str


class BatchVerificationRequest(StrictRequestModel):
    """Which contacts to verify: the listed ones, else those never verified."""

    contact_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_BATCH)
    limit: int = Field(default=200, ge=1, le=MAX_BATCH)


class BatchVerificationResponse(ResponseModel):
    """What the batch found."""

    verifier: str
    checked: int
    by_status: dict[str, int]


@router.post(
    "/{contact_id}/verify-email",
    response_model=EmailVerificationResponse,
    summary="Verify a contact's email address",
    description="Uses the connected verification service (MillionVerifier) or, until one is "
    "connected, offline rules. `invalid` addresses are blocked by the `email_verification` "
    "guardrail; `risky` and `catch_all` are only flagged.",
    responses=WRITE,
)
async def verify_contact_email(
    contact_id: uuid.UUID,
    _principal: Writer,
    session: TenantSession,
    container: ContainerDependency,
) -> EmailVerificationResponse:
    """Verify one address."""
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise NotFoundError("Contact not found.")
    verifier = await build_email_verifier(
        session, container.http_client, build_secret_box(container.settings)
    )
    _, results = await verify_contact_emails(container.clock, verifier, [contact])
    result = results[str(contact.id)]
    return EmailVerificationResponse(
        contact_id=contact.id,
        email=contact.email,
        email_status=contact.email_status,
        reason=result.reason,
        email_verified_at=contact.email_verified_at,
        verifier=verifier.name,
    )


@router.post(
    "/verify-emails",
    response_model=BatchVerificationResponse,
    summary="Verify many contacts' addresses",
    description="The listed contacts, or up to `limit` contacts never verified "
    f"(at most {MAX_BATCH} per call).",
    responses=WRITE,
)
async def verify_contact_emails_in_bulk(
    payload: BatchVerificationRequest,
    _principal: Writer,
    session: TenantSession,
    container: ContainerDependency,
) -> BatchVerificationResponse:
    """Verify a batch."""
    statement = select(Contact)
    if payload.contact_ids:
        statement = statement.where(Contact.id.in_(payload.contact_ids))
    else:
        statement = (
            statement.where(Contact.email_verified_at.is_(None))
            .order_by(Contact.created_at)
            .limit(payload.limit)
        )
    contacts = list((await session.scalars(statement)).all())
    verifier = await build_email_verifier(
        session, container.http_client, build_secret_box(container.settings)
    )
    summary, _ = await verify_contact_emails(container.clock, verifier, contacts)
    return BatchVerificationResponse(
        verifier=summary.verifier, checked=summary.checked, by_status=summary.by_status
    )
