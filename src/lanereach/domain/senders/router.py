"""Agent endpoints: CSV import and signature preview."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import JSONResponse

from lanereach.auth.dependencies import (
    CurrentPrincipal,
    IdempotencyGuardDependency,
    TenantSession,
    require_scopes,
)
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import NotFoundError
from lanereach.core.idempotency import run_idempotent
from lanereach.core.openapi import READ, WRITE
from lanereach.core.schemas import ResponseModel
from lanereach.domain.senders.health import build_mailbox_health
from lanereach.domain.senders.models import Sender
from lanereach.domain.senders.service import import_agents_csv, render_agent_signature
from lanereach.tenants.models import Tenant

router = APIRouter(prefix="/senders", tags=["Offices & senders"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Admin = Annotated[Principal, Depends(require_scopes(Scope.ADMIN_MANAGE))]


class AgentImportResponse(ResponseModel):
    """What a CSV import did."""

    created: int
    updated: int
    errors: list[str]


class SignatureResponse(ResponseModel):
    """An agent's signature."""

    html: str
    text: str


@router.post(
    "/import",
    response_model=AgentImportResponse,
    summary="Import agents from CSV (name, email, office, service_line, title, phone, daily_cap)",
    responses=WRITE,
)
async def import_agents(
    _principal: Admin,
    session: TenantSession,
    container: ContainerDependency,
    idempotency: IdempotencyGuardDependency,
    file: Annotated[UploadFile, File(description="UTF-8 CSV, comma or semicolon separated.")],
) -> AgentImportResponse | JSONResponse:
    """Create or update agents by email."""
    content = await file.read()

    async def run() -> AgentImportResponse:
        summary = await import_agents_csv(session, container.clock, content)
        return AgentImportResponse(
            created=summary.created, updated=summary.updated, errors=summary.errors
        )

    return await run_idempotent(idempotency, status.HTTP_200_OK, run)


class MailboxHealthResponse(ResponseModel):
    """Health of one agent mailbox over the last 30 days."""

    sender_id: uuid.UUID
    display_name: str
    email: str
    office_id: uuid.UUID
    service_line: str
    is_active: bool
    flagged: bool
    flag_reason: str | None
    daily_cap: int
    used_today: int
    sent_7d: int
    sent_30d: int
    tracked_30d: int
    opened_30d: int
    replies_30d: int
    bounces_30d: int
    complaints_30d: int
    bounce_rate: float
    complaint_rate: float
    reply_rate: float
    open_rate: float
    score: int
    status: str
    reasons: list[str]


@router.get(
    "/health",
    response_model=list[MailboxHealthResponse],
    summary="Mailbox health of every agent",
    description="Score 0-100 from bounce and complaint rates (30 days) and the flag; status "
    "`healthy`, `watch`, `at_risk` or `paused`, with the reasons. Worst first.",
    responses=READ,
)
async def list_mailbox_health(
    _reader: Reader, session: TenantSession, container: ContainerDependency
) -> list[MailboxHealthResponse]:
    """Mailbox health, worst first."""
    rows = await build_mailbox_health(session, container.clock.now())
    return [MailboxHealthResponse.model_validate(row) for row in rows]


@router.get(
    "/{sender_id}/signature",
    response_model=SignatureResponse,
    summary="Preview an agent's signature",
    responses=READ,
)
async def preview_signature(
    sender_id: uuid.UUID,
    principal: CurrentPrincipal,
    _reader: Reader,
    session: TenantSession,
    container: ContainerDependency,
) -> SignatureResponse:
    """The signature exactly as emails will carry it."""
    sender = await session.get(Sender, sender_id)
    if sender is None:
        raise NotFoundError("Agent not found.")
    tenant = await session.get(Tenant, principal.tenant_id)
    assert tenant is not None  # noqa: S101 - authenticated tenants exist
    html, text = render_agent_signature(sender, tenant, container.settings)
    return SignatureResponse(html=html, text=text)
