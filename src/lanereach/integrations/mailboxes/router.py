"""Agent mailbox endpoints: connect, test and disconnect SMTP/IMAP, list provider presets."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse
from pydantic import Field

from lanereach.auth.dependencies import IdempotencyGuardDependency, TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.idempotency import run_idempotent
from lanereach.core.openapi import READ, WRITE
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.integrations.mailboxes.models import (
    PRESETS,
    MailboxPreset,
    MailboxStatus,
    MailSecurity,
    SenderMailbox,
)
from lanereach.integrations.mailboxes.service import MailboxInput, MailboxService

router = APIRouter(tags=["Agents"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Admin = Annotated[Principal, Depends(require_scopes(Scope.ADMIN_MANAGE))]

Host = Annotated[str, Field(min_length=3, max_length=255, pattern=r"^[A-Za-z0-9.-]+$")]
Port = Annotated[int, Field(ge=1, le=65535)]


class MailboxPresetResponse(ResponseModel):
    """A known email provider's servers."""

    key: MailboxPreset
    label: str
    smtp_host: str
    smtp_port: int
    smtp_security: MailSecurity
    imap_host: str
    imap_port: int
    imap_security: MailSecurity
    help: str


class MailboxResponse(ResponseModel):
    """An agent's mailbox connection (the password is never returned)."""

    sender_id: uuid.UUID
    preset: str
    smtp_host: str
    smtp_port: int
    smtp_security: MailSecurity
    imap_host: str
    imap_port: int
    imap_security: MailSecurity
    username: str
    password_saved: bool = True
    status: MailboxStatus
    last_error: str | None
    verified_at: datetime | None
    last_polled_at: datetime | None
    updated_at: datetime


class MailboxStateResponse(ResponseModel):
    """Whether the agent has a mailbox, and which provider mode the deployment uses."""

    sending_provider: str = Field(description="`dry_run` records only; `smtp` really sends.")
    mailbox: MailboxResponse | None


class MailboxRequest(StrictRequestModel):
    """SMTP and IMAP settings; leave `password` out to keep the saved one."""

    preset: MailboxPreset = MailboxPreset.CUSTOM
    smtp_host: Host
    smtp_port: Port
    smtp_security: MailSecurity
    imap_host: Host
    imap_port: Port
    imap_security: MailSecurity
    username: str = Field(min_length=3, max_length=320)
    password: str | None = Field(default=None, min_length=1, max_length=500)


def get_mailbox_service(session: TenantSession, container: ContainerDependency) -> MailboxService:
    """Mailbox service for the request."""
    return MailboxService(session, container.clock, container.settings, container.mail)


Mailboxes = Annotated[MailboxService, Depends(get_mailbox_service)]


def _describe(mailbox: SenderMailbox | None) -> MailboxResponse | None:
    return MailboxResponse.model_validate(mailbox) if mailbox is not None else None


@router.get(
    "/mailbox-presets",
    response_model=list[MailboxPresetResponse],
    summary="Known email providers",
    responses=READ,
)
async def list_mailbox_presets(_principal: Reader) -> list[MailboxPresetResponse]:
    """Servers of Microsoft 365, Google Workspace and Zoho (and a blank custom one)."""
    return [MailboxPresetResponse(key=key, **values) for key, values in PRESETS.items()]


@router.get(
    "/senders/{sender_id}/mailbox",
    response_model=MailboxStateResponse,
    summary="The agent's mailbox connection",
    responses=READ,
)
async def read_mailbox(
    sender_id: uuid.UUID, _principal: Reader, mailboxes: Mailboxes, container: ContainerDependency
) -> MailboxStateResponse:
    """The connection (without its password) and the deployment's sending mode."""
    return MailboxStateResponse(
        sending_provider=container.settings.sending_provider,
        mailbox=_describe(await mailboxes.get_mailbox(sender_id)),
    )


@router.put(
    "/senders/{sender_id}/mailbox",
    response_model=MailboxResponse,
    summary="Connect the agent's mailbox",
    description="Saves SMTP/IMAP settings (the password is encrypted). The mailbox must pass "
    "`POST …/mailbox/test` before it sends.",
    responses=WRITE,
)
async def save_mailbox(
    sender_id: uuid.UUID, payload: MailboxRequest, _principal: Admin, mailboxes: Mailboxes
) -> MailboxResponse:
    """Connect or change the mailbox (admins)."""
    mailbox = await mailboxes.save_mailbox(
        sender_id,
        MailboxInput(
            preset=payload.preset,
            smtp_host=payload.smtp_host,
            smtp_port=payload.smtp_port,
            smtp_security=payload.smtp_security,
            imap_host=payload.imap_host,
            imap_port=payload.imap_port,
            imap_security=payload.imap_security,
            username=payload.username,
            password=payload.password,
        ),
    )
    return MailboxResponse.model_validate(mailbox)


@router.post(
    "/senders/{sender_id}/mailbox/test",
    response_model=MailboxResponse,
    summary="Test the agent's mailbox",
    description="Logs in to SMTP and IMAP; nothing is sent or read. `status` becomes `verified` "
    "or `error` with the server's answer in `last_error`.",
    responses=WRITE,
)
async def check_mailbox_login(
    sender_id: uuid.UUID,
    _principal: Admin,
    mailboxes: Mailboxes,
    idempotency: IdempotencyGuardDependency,
) -> MailboxResponse | JSONResponse:
    """Check the login (admins)."""

    async def check() -> MailboxResponse:
        return MailboxResponse.model_validate(await mailboxes.test_mailbox(sender_id))

    return await run_idempotent(idempotency, status.HTTP_200_OK, check)


@router.delete(
    "/senders/{sender_id}/mailbox",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect the agent's mailbox",
    responses=WRITE,
)
async def delete_mailbox(sender_id: uuid.UUID, _principal: Admin, mailboxes: Mailboxes) -> Response:
    """Remove the connection and its saved password (admins)."""
    await mailboxes.delete_mailbox(sender_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
