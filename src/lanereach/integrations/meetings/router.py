"""Meeting endpoints: the public Calendly / Cal.com webhook and the meetings of a contact."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select, text

from lanereach.auth.dependencies import TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ClockDependency, ContainerDependency
from lanereach.core.errors import AuthenticationError, NotFoundError, RequestTooLargeError
from lanereach.core.openapi import READ, problem_responses
from lanereach.core.schemas import ResponseModel
from lanereach.core.secrets import SecretDecryptionError, build_secret_box
from lanereach.core.tenancy import open_system_session, open_tenant_session
from lanereach.integrations.meetings.models import Meeting, MeetingStatus
from lanereach.integrations.meetings.providers import (
    WebhookRejectedError,
    read_cal_com,
    read_calendly,
    verify_cal_com,
    verify_calendly,
)
from lanereach.integrations.meetings.service import record_booking
from lanereach.integrations.outbound.models import IntegrationConnection, IntegrationKind

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["Integrations"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
MAX_BODY = 256_000
_PROVIDERS = {"calendly": IntegrationKind.CALENDLY, "cal-com": IntegrationKind.CAL_COM}


class MeetingResponse(ResponseModel):
    """A booked meeting."""

    id: uuid.UUID
    contact_id: uuid.UUID
    deal_id: uuid.UUID | None
    provider: str
    title: str
    starts_at: datetime
    ends_at: datetime | None
    status: MeetingStatus
    join_url: str | None
    invitee_email: str
    invitee_name: str | None
    organizer_email: str | None
    created_at: datetime


class BookingReceipt(ResponseModel):
    """What the webhook did."""

    received: bool
    meeting_id: uuid.UUID | None = None


@router.post(
    "/webhooks/meetings/{provider}/{public_key}",
    response_model=BookingReceipt,
    summary="Calendly / Cal.com booking webhook (public, signed)",
    description="Point the Calendly or Cal.com webhook at the URL shown in Integrations. The "
    "signature is checked with the connection's signing key (Calendly: "
    "`Calendly-Webhook-Signature`, Cal.com: `X-Cal-Signature-256`); unsigned or wrong requests "
    "get 401. Events other than bookings, reschedules and cancellations are acknowledged and "
    "ignored.",
    responses=problem_responses(401, 404, 413, 422),
)
async def receive_meeting_webhook(
    provider: str, public_key: str, request: Request, container: ContainerDependency
) -> BookingReceipt:
    """Record a booking change."""
    kind = _PROVIDERS.get(provider)
    if kind is None:
        raise NotFoundError("Unknown meeting provider.")
    body = await request.body()
    if len(body) > MAX_BODY:
        raise RequestTooLargeError("The webhook body is too large.")
    # The public key names the connection across tenants: look it up without a tenant, then
    # do everything else inside that tenant.
    async with open_system_session(container.session_factory) as system:
        tenant_id = await system.scalar(
            text("SELECT tenant_id FROM integration_connection_owner(:key)"), {"key": public_key}
        )
    if tenant_id is None:
        raise NotFoundError("Unknown webhook.")
    async with open_tenant_session(container.session_factory, tenant_id) as session:
        connection = await session.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.public_key == public_key, IntegrationConnection.kind == kind
            )
        )
        if connection is None or not connection.is_active or not connection.secret_encrypted:
            raise NotFoundError("Unknown webhook.")
        try:
            secret = build_secret_box(container.settings).decrypt(connection.secret_encrypted)
            if kind is IntegrationKind.CALENDLY:
                verify_calendly(
                    body,
                    request.headers.get("calendly-webhook-signature"),
                    secret,
                    container.clock.now(),
                )
                notice = read_calendly(body)
            else:
                verify_cal_com(body, request.headers.get("x-cal-signature-256"), secret)
                notice = read_cal_com(body)
        except (WebhookRejectedError, SecretDecryptionError) as exc:
            logger.warning("meeting_webhook_rejected", provider=provider, reason=str(exc))
            raise AuthenticationError("The webhook signature is not valid.") from exc
        if notice is None:
            return BookingReceipt(received=True)
        outcome = await record_booking(
            session, container.clock, connection, notice, task_queue=container.task_queue
        )
        return BookingReceipt(received=True, meeting_id=outcome.meeting.id)


@router.get(
    "/meetings",
    response_model=list[MeetingResponse],
    summary="Meetings of a contact or deal",
    responses=READ,
)
async def list_meetings(
    _principal: Reader,
    session: TenantSession,
    clock: ClockDependency,
    contact_id: Annotated[uuid.UUID | None, Query()] = None,
    deal_id: Annotated[uuid.UUID | None, Query()] = None,
    upcoming: Annotated[bool, Query(description="Only meetings that have not started.")] = False,
) -> list[MeetingResponse]:
    """Meetings, soonest first."""
    statement = select(Meeting)
    if contact_id:
        statement = statement.where(Meeting.contact_id == contact_id)
    if deal_id:
        statement = statement.where(Meeting.deal_id == deal_id)
    if upcoming:
        statement = statement.where(Meeting.starts_at >= clock.now())
    rows = (await session.scalars(statement.order_by(Meeting.starts_at).limit(200))).all()
    return [MeetingResponse.model_validate(row) for row in rows]
