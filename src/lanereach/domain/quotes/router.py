"""Quote endpoints: the catalogue, CRUD, send in the thread, accept/reject, printable page."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import Field

from lanereach.auth.dependencies import IdempotencyGuardDependency, TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.automation.engine.dependencies import EngineServicesDependency
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.idempotency import run_idempotent
from lanereach.core.openapi import READ, WRITE
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.domain.companies.models import Company
from lanereach.domain.contacts.models import Contact
from lanereach.domain.logistics import INCOTERMS, LINE_LABELS, MODES, UNITS, charge_presets
from lanereach.domain.quotes.document import render_quote_html, render_quote_page
from lanereach.domain.quotes.models import ChargeType, Quote, QuoteStatus
from lanereach.domain.quotes.service import LineInput, QuoteInput, QuoteService
from lanereach.domain.senders.models import ServiceLine
from lanereach.tenants.branding import load_branding
from lanereach.tenants.models import Tenant

router = APIRouter(prefix="/crm/quotes", tags=["CRM"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Writer = Annotated[Principal, Depends(require_scopes(Scope.CRM_WRITE))]

Money = Annotated[Decimal, Field(ge=0, le=Decimal("9999999999.99"), decimal_places=2)]


class ModeResponse(ResponseModel):
    """A transport mode or service type."""

    key: str
    label: str


class ChargePresetResponse(ResponseModel):
    """A typical charge for the builder."""

    charge_type: ChargeType
    description: str
    unit: str


class ServiceLineResponse(ResponseModel):
    """A service line with its modes and typical charges."""

    key: ServiceLine
    label: str
    modes: list[ModeResponse]
    presets: list[ChargePresetResponse]


class LogisticsCatalogResponse(ResponseModel):
    """Everything the quote builder and deal forms offer."""

    service_lines: list[ServiceLineResponse]
    incoterms: list[str]
    units: list[str]
    charge_types: list[ChargeType]


class QuoteLineRequest(StrictRequestModel):
    """One charge."""

    charge_type: ChargeType = ChargeType.FREIGHT
    description: str = Field(min_length=1, max_length=200)
    quantity: Annotated[Decimal, Field(gt=0, le=Decimal("99999999"), decimal_places=3)] = Decimal(
        "1"
    )
    unit: str = Field(default="shipment", min_length=1, max_length=20)
    unit_price: Money


class QuoteRequest(StrictRequestModel):
    """A quote's content (lines are replaced as a whole)."""

    title: str | None = Field(default=None, max_length=200)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    valid_until: date | None = None
    transit_days: int | None = Field(default=None, ge=0, le=365)
    incoterm: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    service_line: ServiceLine | None = None
    mode: str | None = Field(default=None, max_length=20)
    origin: str | None = Field(default=None, max_length=120)
    destination: str | None = Field(default=None, max_length=120)
    notes: str = Field(default="", max_length=4000)
    lines: list[QuoteLineRequest] = Field(default_factory=list, max_length=40)


class QuoteCreateRequest(QuoteRequest):
    """A new quote for a deal."""

    deal_id: uuid.UUID


class QuoteLineResponse(ResponseModel):
    """A charge line."""

    id: uuid.UUID
    position: int
    charge_type: ChargeType
    description: str
    quantity: float
    unit: str
    unit_price: float
    amount: float


class QuoteResponse(ResponseModel):
    """A quote (lines included when read one by one)."""

    id: uuid.UUID
    deal_id: uuid.UUID
    contact_id: uuid.UUID
    number: str
    title: str
    service_line: str
    mode: str | None
    incoterm: str | None
    origin: str | None
    destination: str | None
    currency: str
    valid_until: date
    transit_days: int | None
    notes: str
    status: QuoteStatus
    total_amount: float
    sent_at: datetime | None
    decided_at: datetime | None
    decision_note: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    contact_name: str | None = None
    company_name: str | None = None
    lines: list[QuoteLineResponse] = Field(default_factory=list)


class SendQuoteRequest(StrictRequestModel):
    """Optional words above the quote table."""

    intro: str | None = Field(default=None, max_length=2000)


class DecisionRequest(StrictRequestModel):
    """The customer's answer."""

    accepted: bool
    note: str | None = Field(default=None, max_length=500)


def get_quote_service(session: TenantSession, container: ContainerDependency) -> QuoteService:
    """Quote service for the request."""
    return QuoteService(session, container.clock, container.task_queue)


Quotes = Annotated[QuoteService, Depends(get_quote_service)]


def _input(payload: QuoteRequest) -> QuoteInput:
    return QuoteInput(
        title=payload.title,
        currency=payload.currency,
        valid_until=payload.valid_until,
        transit_days=payload.transit_days,
        incoterm=payload.incoterm,
        service_line=payload.service_line.value if payload.service_line else None,
        mode=payload.mode,
        origin=payload.origin,
        destination=payload.destination,
        notes=payload.notes,
        lines=[
            LineInput(line.charge_type, line.description, line.quantity, line.unit, line.unit_price)
            for line in payload.lines
        ],
    )


async def _describe(
    session: TenantSession, quotes: QuoteService, rows: list[Quote], *, with_lines: bool
) -> list[QuoteResponse]:
    contact_ids = {row.contact_id for row in rows}
    contacts = {
        row.id: row for row in [await session.get(Contact, cid) for cid in contact_ids] if row
    }
    company_ids = {row.company_id for row in contacts.values() if row.company_id}
    companies = {
        row.id: row.name for row in [await session.get(Company, cid) for cid in company_ids] if row
    }
    result = []
    for row in rows:
        response = QuoteResponse.model_validate(row)
        contact = contacts.get(row.contact_id)
        response.contact_name = contact.display_name if contact else None
        response.company_name = (
            companies.get(contact.company_id) if contact and contact.company_id else None
        )
        if with_lines:
            response.lines = [
                QuoteLineResponse.model_validate(line) for line in await quotes.lines_of(row.id)
            ]
        result.append(response)
    return result


@router.get(
    "/catalog",
    response_model=LogisticsCatalogResponse,
    summary="Service lines, modes, Incoterms and charge presets",
    responses=READ,
)
async def read_logistics_catalog(_principal: Reader) -> LogisticsCatalogResponse:
    """What forwarders sell, for forms and the quote builder."""
    lines = [line for line in ServiceLine if line is not ServiceLine.ANY]
    return LogisticsCatalogResponse(
        service_lines=[
            ServiceLineResponse(
                key=line,
                label=LINE_LABELS[line],
                modes=[
                    ModeResponse(key=mode.key, label=mode.label) for mode in MODES.get(line, ())
                ],
                presets=[
                    ChargePresetResponse(
                        charge_type=ChargeType(preset.charge_type),
                        description=preset.description,
                        unit=preset.unit,
                    )
                    for preset in charge_presets(line)
                ],
            )
            for line in lines
        ],
        incoterms=list(INCOTERMS),
        units=list(UNITS),
        charge_types=list(ChargeType),
    )


@router.get("", response_model=list[QuoteResponse], summary="Quotes", responses=READ)
async def list_quotes(
    _principal: Reader,
    session: TenantSession,
    quotes: Quotes,
    status_filter: Annotated[QuoteStatus | None, Query(alias="status")] = None,
    deal_id: Annotated[uuid.UUID | None, Query()] = None,
    contact_id: Annotated[uuid.UUID | None, Query()] = None,
    expiring_within_days: Annotated[
        int | None, Query(ge=0, le=90, description="Sent quotes expiring soon.")
    ] = None,
) -> list[QuoteResponse]:
    """Newest first (soonest to expire first with `expiring_within_days`)."""
    rows = await quotes.list_quotes(
        status=status_filter,
        deal_id=deal_id,
        contact_id=contact_id,
        expiring_within_days=expiring_within_days,
    )
    return await _describe(session, quotes, rows, with_lines=False)


@router.post(
    "",
    response_model=QuoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a quote for a deal",
    responses=WRITE,
)
async def create_quote(
    payload: QuoteCreateRequest,
    principal: Writer,
    session: TenantSession,
    quotes: Quotes,
    idempotency: IdempotencyGuardDependency,
) -> QuoteResponse | JSONResponse:
    """Create a draft (number `Q-<year>-<n>`, validity 14 days unless given)."""

    async def create() -> QuoteResponse:
        quote = await quotes.create_quote(payload.deal_id, principal.subject, _input(payload))
        [response] = await _describe(session, quotes, [quote], with_lines=True)
        return response

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, create)


@router.get(
    "/{quote_id}", response_model=QuoteResponse, summary="A quote with its lines", responses=READ
)
async def read_quote(
    quote_id: uuid.UUID, _principal: Reader, session: TenantSession, quotes: Quotes
) -> QuoteResponse:
    """A quote."""
    [response] = await _describe(
        session, quotes, [await quotes.require_quote(quote_id)], with_lines=True
    )
    return response


@router.put(
    "/{quote_id}", response_model=QuoteResponse, summary="Change a draft quote", responses=WRITE
)
async def update_quote(
    quote_id: uuid.UUID,
    payload: QuoteRequest,
    _principal: Writer,
    session: TenantSession,
    quotes: Quotes,
) -> QuoteResponse:
    """Replace a draft's content (409 once sent: duplicate it)."""
    [response] = await _describe(
        session, quotes, [await quotes.update_quote(quote_id, _input(payload))], with_lines=True
    )
    return response


@router.post(
    "/{quote_id}/duplicate",
    response_model=QuoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate as a new draft",
    responses=WRITE,
)
async def duplicate_quote(
    quote_id: uuid.UUID,
    principal: Writer,
    session: TenantSession,
    quotes: Quotes,
    idempotency: IdempotencyGuardDependency,
) -> QuoteResponse | JSONResponse:
    """Copy a quote into a new draft."""

    async def duplicate() -> QuoteResponse:
        [response] = await _describe(
            session,
            quotes,
            [await quotes.duplicate_quote(quote_id, principal.subject)],
            with_lines=True,
        )
        return response

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, duplicate)


@router.delete(
    "/{quote_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a quote", responses=WRITE
)
async def delete_quote(quote_id: uuid.UUID, _principal: Writer, quotes: Quotes) -> Response:
    """Delete a quote."""
    await quotes.delete_quote(quote_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{quote_id}/send",
    response_model=QuoteResponse,
    summary="Email the quote to the contact",
    description="Sent in the latest thread with the contact (or as a new email) from the thread's "
    "agent with their signature, through the send guardrails: blocked 422, deferred 409. The "
    "deal moves to its 'Quoted' stage when it was earlier.",
    responses=WRITE,
)
async def send_quote(
    quote_id: uuid.UUID,
    principal: Writer,
    session: TenantSession,
    quotes: Quotes,
    services: EngineServicesDependency,
    idempotency: IdempotencyGuardDependency,
    payload: SendQuoteRequest | None = None,
) -> QuoteResponse | JSONResponse:
    """Send the quote."""

    async def send() -> QuoteResponse:
        quote = await quotes.send_quote(
            services,
            quote_id,
            intro=payload.intro if payload else None,
            request_key=str(uuid.uuid4()),
            actor=principal.subject,
        )
        [response] = await _describe(session, quotes, [quote], with_lines=True)
        return response

    return await run_idempotent(idempotency, status.HTTP_200_OK, send)


@router.post(
    "/{quote_id}/decision",
    response_model=QuoteResponse,
    summary="Record acceptance or rejection",
    responses=WRITE,
)
async def decide_quote(
    quote_id: uuid.UUID,
    payload: DecisionRequest,
    principal: Writer,
    session: TenantSession,
    quotes: Quotes,
    idempotency: IdempotencyGuardDependency,
) -> QuoteResponse | JSONResponse:
    """Accepted quotes set the deal's value when it had none."""

    async def decide() -> QuoteResponse:
        quote = await quotes.decide_quote(
            quote_id, principal.subject, accepted=payload.accepted, note=payload.note
        )
        [response] = await _describe(session, quotes, [quote], with_lines=True)
        return response

    return await run_idempotent(idempotency, status.HTTP_200_OK, decide)


@router.get(
    "/{quote_id}/document",
    response_class=HTMLResponse,
    summary="Printable quote (Save as PDF from the browser)",
    responses=READ,
)
async def read_quote_document(
    quote_id: uuid.UUID, principal: Reader, session: TenantSession, quotes: Quotes
) -> HTMLResponse:
    """The quote as the customer receives it, on its own page."""
    quote = await quotes.require_quote(quote_id)
    contact = await session.get(Contact, quote.contact_id)
    company = (
        await session.get(Company, contact.company_id) if contact and contact.company_id else None
    )
    tenant = await session.get(Tenant, principal.tenant_id)
    branding = load_branding(tenant.branding if tenant else None)
    body = render_quote_html(
        quote,
        await quotes.lines_of(quote.id),
        branding=branding,
        company_name=branding.company_name or (tenant.name if tenant else ""),
        contact_name=(contact.first_name or contact.display_name) if contact else "",
        customer_company=company.name if company else None,
    )
    return HTMLResponse(
        render_quote_page(body, quote.number),
        headers={
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:"
            )
        },
    )
