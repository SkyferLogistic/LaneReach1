"""Outbox inspection, provider webhooks, simulated provider events and one-click unsubscribe."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import AwareDatetime, EmailStr, Field

from lanereach.auth.dependencies import IdempotencyGuardDependency, TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.repository import AuthLookupRepository
from lanereach.auth.roles import Scope
from lanereach.auth.service import ensure_webhook_event_not_replayed, verify_webhook_delivery
from lanereach.automation.engine.dependencies import NonProduction
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import BusinessRuleViolationError, ConflictError, NotFoundError
from lanereach.core.idempotency import run_idempotent
from lanereach.core.openapi import READ, WRITE, problem_responses
from lanereach.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.core.tenancy import open_tenant_session
from lanereach.domain.contacts.models import Contact
from lanereach.domain.suppressions.service import unsubscribe_contact
from lanereach.integrations.sending.dry_run import DRY_RUN_PROVIDER, DryRunSendingProvider
from lanereach.integrations.sending.models import OutboxStatus
from lanereach.integrations.sending.outbox import (
    OutboxFilter,
    count_outbox_by_status,
    list_outbox,
    load_outbox_thread,
    require_outbox_message,
)
from lanereach.integrations.sending.provider import ProviderEvent, ProviderEventType
from lanereach.integrations.sending.unsubscribe import UnsubscribeTarget, read_unsubscribe_token
from lanereach.integrations.sending.webhooks import (
    IngestedEvent,
    enqueue_inbound_processing,
    ingest_provider_event,
)

router = APIRouter(tags=["Sending"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
EventWriter = Annotated[Principal, Depends(require_scopes(Scope.EVENTS_WRITE))]

# Providers whose webhook format LaneReach understands (real providers are added with them).
WEBHOOK_PROVIDERS = frozenset({DRY_RUN_PROVIDER})
MAX_WEBHOOK_EVENTS = 100


class OutboxMessageResponse(ResponseModel):
    """An email from the sent log."""

    id: uuid.UUID
    provider_message_id: str
    thread_id: str
    in_reply_to: str | None
    kind: str
    to_email: str
    to_name: str | None
    from_email: str
    from_name: str
    subject: str
    body: str
    html_body: str | None = Field(
        default=None, description="HTML version (scripts); purged with the body."
    )
    sender_id: uuid.UUID | None = None
    headers: dict[str, Any]
    contact_id: uuid.UUID | None
    office_id: uuid.UUID | None
    enrollment_id: uuid.UUID | None
    status: OutboxStatus = Field(
        description="`dry_run`: recorded only (nothing left); `queued`: waiting for delivery "
        "through the agent's mailbox; `sent`: accepted by the mailbox; `failed`: refused or "
        "undeliverable after retries (see `error`)."
    )
    provider: str
    sent_at: datetime | None = None
    error: str | None = None
    attempts: int = 0
    created_at: datetime


class OutboxCountsResponse(ResponseModel):
    """Emails matching the filters, per status."""

    all: int
    dry_run: int
    queued: int
    sent: int
    failed: int


class ThreadMessageResponse(ResponseModel):
    """One message of a conversation (outbound or reply)."""

    id: uuid.UUID
    direction: str
    kind: str
    subject: str
    body: str | None = Field(description="Empty once purged by the retention policy.")
    from_email: str
    to_email: str
    occurred_at: datetime


def _outbox_filter(
    q: Annotated[str | None, Query(max_length=100, description="Subject or address.")] = None,
    status_filter: Annotated[OutboxStatus | None, Query(alias="status")] = None,
    kind: Annotated[str | None, Query(max_length=20)] = None,
    sender_id: Annotated[uuid.UUID | None, Query()] = None,
    office_id: Annotated[uuid.UUID | None, Query()] = None,
    contact_id: Annotated[uuid.UUID | None, Query()] = None,
    enrollment_id: Annotated[uuid.UUID | None, Query()] = None,
    sent_from: Annotated[date | None, Query(description="First day (UTC).")] = None,
    sent_to: Annotated[date | None, Query(description="Last day (UTC), included.")] = None,
) -> OutboxFilter:
    return OutboxFilter(
        search=q or None,
        status=status_filter,
        kind=kind,
        sender_id=sender_id,
        office_id=office_id,
        contact_id=contact_id,
        enrollment_id=enrollment_id,
        sent_from=sent_from,
        sent_to=sent_to,
    )


OutboxQuery = Annotated[OutboxFilter, Depends(_outbox_filter)]


@router.get(
    "/outbox",
    response_model=Page[OutboxMessageResponse],
    summary="The sent log: every email, newest first",
    responses=READ,
)
async def list_outbox_messages(
    _principal: Reader,
    session: TenantSession,
    query: OutboxQuery,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[OutboxMessageResponse]:
    """Search and filter the emails LaneReach sent (or recorded in dry-run mode)."""
    rows, next_cursor = await list_outbox(session, query, limit=limit, cursor=cursor)
    return Page(
        items=[OutboxMessageResponse.model_validate(row) for row in rows], next_cursor=next_cursor
    )


@router.get(
    "/outbox/counts",
    response_model=OutboxCountsResponse,
    summary="Sent log counts per status",
    responses=READ,
)
async def read_outbox_counts(
    _principal: Reader, session: TenantSession, query: OutboxQuery
) -> OutboxCountsResponse:
    """Counts for the status tabs (same filters as the list, status ignored)."""
    return OutboxCountsResponse(**await count_outbox_by_status(session, query))


@router.get(
    "/outbox/{message_id}",
    response_model=OutboxMessageResponse,
    summary="One email of the sent log",
    responses=READ,
)
async def read_outbox_message(
    message_id: uuid.UUID, _principal: Reader, session: TenantSession
) -> OutboxMessageResponse:
    """Get an outbox email."""
    return OutboxMessageResponse.model_validate(await require_outbox_message(session, message_id))


@router.get(
    "/outbox/{message_id}/thread",
    response_model=list[ThreadMessageResponse],
    summary="The email's whole conversation",
    responses=READ,
)
async def read_outbox_thread(
    message_id: uuid.UUID, _principal: Reader, session: TenantSession
) -> list[ThreadMessageResponse]:
    """Every message sent and received in the email's thread, oldest first."""
    return [
        ThreadMessageResponse(
            id=item.id,
            direction=item.direction.value,
            kind=item.kind.value,
            subject=item.subject,
            body=item.body,
            from_email=item.from_email,
            to_email=item.to_email,
            occurred_at=item.occurred_at,
        )
        for item in await load_outbox_thread(session, message_id)
    ]


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title}</title></head>"
        "<body style='font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem'>"
        f"{body}</body></html>"
    )


_UNSUBSCRIBED_PAGE = _page(
    "Unsubscribed",
    "<h1>You are unsubscribed</h1><p>You will not receive further emails from us.</p>",
)
# Mail security scanners and link previewers open links: a GET must never unsubscribe.
_CONFIRM_PAGE = _page(
    "Unsubscribe",
    "<h1>Unsubscribe?</h1><p>Confirm that you no longer want to receive our emails.</p>"
    "<form method='post'><input type='hidden' name='List-Unsubscribe' value='One-Click'>"
    "<button type='submit' style='font-size:1rem;padding:.5rem 1rem'>Unsubscribe</button></form>",
)


def _read_token(container: ContainerDependency, token: str) -> UnsubscribeTarget:
    target = read_unsubscribe_token(
        container.settings.unsubscribe_signing_secret.get_secret_value(), token
    )
    if target is None:
        raise NotFoundError("This unsubscribe link is not valid.")
    return target


@router.get(
    "/unsubscribe/{token}",
    response_class=HTMLResponse,
    summary="Unsubscribe confirmation page (public)",
    description="Linked from every email's `List-Unsubscribe` header. Shows a confirmation "
    "button; opening the link alone never unsubscribes (link scanners open links).",
)
async def show_unsubscribe_page(token: str, container: ContainerDependency) -> HTMLResponse:
    """Confirm the unsubscribe."""
    _read_token(container, token)
    return HTMLResponse(_CONFIRM_PAGE)


@router.post(
    "/unsubscribe/{token}",
    response_class=HTMLResponse,
    summary="Unsubscribe (public; RFC 8058 one-click POST and the confirmation form)",
)
async def unsubscribe_one_click(token: str, container: ContainerDependency) -> HTMLResponse:
    """Unsubscribe the contact the signed token names (idempotent)."""
    target = _read_token(container, token)
    async with open_tenant_session(container.session_factory, target.tenant_id) as session:
        await unsubscribe_contact(session, container.clock, target.contact_id)
    return HTMLResponse(_UNSUBSCRIBED_PAGE)


class IngestedEventResponse(ResponseModel):
    """What happened to one provider event."""

    event_id: str
    type: str
    message_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    skipped_reason: str | None


class WebhookResponse(ResponseModel):
    """Result of a webhook delivery."""

    received: int
    stored: int
    events: list[IngestedEventResponse]


def _to_response(results: list[IngestedEvent]) -> WebhookResponse:
    return WebhookResponse(
        received=len(results),
        stored=sum(1 for result in results if result.skipped_reason is None),
        events=[
            IngestedEventResponse(
                event_id=result.event_id,
                type=result.type,
                message_id=result.message_id,
                contact_id=result.contact_id,
                skipped_reason=result.skipped_reason,
            )
            for result in results
        ],
    )


@router.post(
    "/webhooks/sending/{provider}",
    response_model=WebhookResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Webhooks"],
    summary="Receive reply, bounce and complaint events from a sending provider",
    description="Authenticated by `X-LaneReach-Signature: t=<unix>,v1=<hmac_sha256(secret, "
    "t + '.' + body)>` (5-minute tolerance). The signed body carries `tenant_id` and "
    "`events`; every `event_id` is accepted once (replays are reported, not re-processed). "
    "Stored messages are processed by the reply pipeline after the request commits.",
    responses=problem_responses(401, 404, 422),
)
async def receive_sending_webhook(
    provider: str,
    request: Request,
    container: ContainerDependency,
    x_lanereach_signature: Annotated[str | None, Header()] = None,
) -> WebhookResponse:
    """Verify, de-duplicate and store provider events."""
    if provider not in WEBHOOK_PROVIDERS:
        raise NotFoundError("Unknown sending provider.")
    body = await request.body()
    verify_webhook_delivery(
        container.settings,
        container.clock,
        provider=provider,
        signature_header=x_lanereach_signature,
        body=body,
    )
    try:
        payload = json.loads(body)
        tenant_id = uuid.UUID(str(payload["tenant_id"]))
    except (ValueError, KeyError, TypeError) as exc:
        raise BusinessRuleViolationError("The body must be JSON with a valid tenant_id.") from exc
    events_raw = payload.get("events")
    if not isinstance(events_raw, list) or len(events_raw) > MAX_WEBHOOK_EVENTS:
        raise BusinessRuleViolationError(
            f"`events` must be a list of at most {MAX_WEBHOOK_EVENTS}."
        )
    if not await AuthLookupRepository(container.session_factory).is_tenant_active(tenant_id):
        raise NotFoundError("Unknown tenant.")
    results: list[IngestedEvent] = []
    async with open_tenant_session(container.session_factory, tenant_id) as session:
        for event in DryRunSendingProvider(session, container.clock).parse_webhook(payload):
            try:
                await ensure_webhook_event_not_replayed(
                    session, container.clock, provider=provider, event_id=event.event_id
                )
            except ConflictError:
                results.append(
                    IngestedEvent(event.event_id, event.type.value, None, None, "replayed event")
                )
                continue
            results.append(
                await ingest_provider_event(
                    session, container.clock, provider=provider, event=event
                )
            )
        enqueue_inbound_processing(
            session,
            container.task_queue,
            [r.message_id for r in results if r.skipped_reason is None and r.message_id],
        )
    return _to_response(results)


class SimulatedSendingEvent(StrictRequestModel):
    """A reply, bounce or complaint to simulate from a contact (dev/staging only)."""

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "type": "reply",
                    "contact_id": "00000000-0000-0000-0000-000000000000",
                    "body": "Please quote 2x40HC Chennai to Rotterdam, FOB, monthly.",
                }
            ]
        }
    }

    type: ProviderEventType = ProviderEventType.REPLY
    contact_id: uuid.UUID
    subject: str | None = Field(default=None, max_length=300)
    body: str = Field(default="", max_length=50_000)
    in_reply_to: str | None = Field(
        default=None,
        max_length=300,
        description="Message-ID answered; defaults to the latest email sent to the contact.",
    )
    bounce_type: Literal["hard", "soft", "blocked"] = "hard"
    from_email: EmailStr | None = None
    occurred_at: AwareDatetime | None = None


@router.post(
    "/test-tools/sending-events",
    response_model=WebhookResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Test data"],
    summary="Simulate a reply, bounce or complaint (dev/staging only)",
    description="Stores the event exactly as the provider webhook would and runs the reply "
    "pipeline after commit. Use it to test reply-handling workflows without a real mailbox.",
    dependencies=[NonProduction],
    responses=WRITE,
)
async def simulate_sending_event(
    payload: SimulatedSendingEvent,
    _principal: EventWriter,
    session: TenantSession,
    container: ContainerDependency,
    idempotency: IdempotencyGuardDependency,
) -> WebhookResponse | JSONResponse:
    """Store a simulated provider event."""

    async def simulate() -> WebhookResponse:
        contact = await session.get(Contact, payload.contact_id)
        if contact is None:
            raise NotFoundError("Contact not found.")
        is_reply = payload.type is ProviderEventType.REPLY
        data: dict[str, Any] = {
            "type": payload.type.value,
            "subject": payload.subject or "",
            "body": payload.body,
            "from_email": str(payload.from_email or contact.email),
            "recipient": contact.email,
            "bounce_type": payload.bounce_type,
        }
        if payload.in_reply_to:
            data["in_reply_to" if is_reply else "original_message_id"] = payload.in_reply_to
        else:
            # No Message-ID given: answer the latest email sent to this contact.
            data["from_email" if is_reply else "recipient"] = contact.email
        event = ProviderEvent(
            type=payload.type,
            event_id=f"sim-{uuid.uuid4().hex}",
            occurred_at=payload.occurred_at or container.clock.now(),
            data=data,
        )
        result = await ingest_provider_event(
            session, container.clock, provider="simulated", event=event
        )
        if result.message_id and result.skipped_reason is None:
            enqueue_inbound_processing(session, container.task_queue, [result.message_id])
        return _to_response([result])

    return await run_idempotent(idempotency, status.HTTP_202_ACCEPTED, simulate)
