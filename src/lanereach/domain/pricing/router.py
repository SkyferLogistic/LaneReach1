"""Pricing endpoints: rate sheets, rates, rate sources, market checks and rate-priced quotes."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import JSONResponse, Response
from pydantic import Field

from lanereach.ai.router import AIRateLimit, get_ai_runtime
from lanereach.ai.tasks.runtime import AIRuntime
from lanereach.auth.dependencies import IdempotencyGuardDependency, TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import BusinessRuleViolationError
from lanereach.core.idempotency import run_idempotent
from lanereach.core.openapi import READ, WRITE
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.domain.pricing.importer import FIELDS
from lanereach.domain.pricing.matching import RateOption, ShipmentQuery, describe_sources
from lanereach.domain.pricing.models import RateSheet
from lanereach.domain.pricing.service import (
    SHEET_SOURCE_UPLOAD,
    DealPricing,
    Lane,
    MarketCheckInput,
    MarketCheckService,
    PricedQuote,
    PricingService,
    RateFilter,
    RateSheetService,
    RateSourceInput,
    RateSourceService,
    lane_history,
    render_lane_link,
    search_rates,
    validate_lane_template,
)
from lanereach.domain.quotes.service import QuoteService
from lanereach.domain.senders.models import ServiceLine

router = APIRouter(prefix="/pricing", tags=["Pricing"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Writer = Annotated[Principal, Depends(require_scopes(Scope.CRM_WRITE))]
AIUser = Annotated[Principal, Depends(require_scopes(Scope.AI_RUN, Scope.CRM_WRITE))]

Money = Annotated[Decimal, Field(ge=0, le=Decimal("9999999999.99"), decimal_places=2)]
Currency = Annotated[str, Field(pattern=r"^[A-Za-z]{3}$")]
RATE_SHEET_EXTENSIONS = (".xlsx", ".xlsm", ".csv")


# --- schemas ---------------------------------------------------------------------------------


class RowErrorResponse(ResponseModel):
    """A row that could not be read."""

    tab: str
    row: int
    message: str


class RateSheetResponse(ResponseModel):
    """A rate sheet."""

    id: uuid.UUID
    name: str
    source: str
    library_path: str | None
    status: str
    row_count: int
    tabs: list[str]
    default_currency: str
    error_count: int = 0
    imported_at: datetime
    updated_at: datetime


class RateSheetDetailResponse(RateSheetResponse):
    """A rate sheet with its problems and column mapping."""

    errors: list[RowErrorResponse]
    detected_mapping: dict[str, dict[str, str]]
    mapping_override: dict[str, dict[str, str]]
    fields: list[str]


class MappingRequest(StrictRequestModel):
    """Which column header holds which field, per tab (empty to go back to detection)."""

    mapping: dict[str, dict[str, str]] = Field(default_factory=dict, max_length=50)
    default_currency: Currency | None = None


class RateResponse(ResponseModel):
    """One rate."""

    id: uuid.UUID
    sheet_id: uuid.UUID
    sheet_name: str = ""
    tab: str
    row_number: int
    service_line: str
    mode: str | None
    origin: str
    origin_code: str | None
    destination: str
    destination_code: str | None
    equipment: str | None
    carrier: str | None
    charge_type: str
    description: str
    unit: str
    amount: float
    currency: str
    minimum: float | None
    valid_from: date | None
    valid_to: date | None
    transit_days: int | None
    notes: str | None


class RatePageResponse(ResponseModel):
    """A page of rates."""

    items: list[RateResponse]
    total: int


class RateSourceRequest(StrictRequestModel):
    """A website the team checks (a link, never a login)."""

    name: str = Field(min_length=1, max_length=120)
    website: str = Field(min_length=8, max_length=500)
    lane_url_template: str | None = Field(
        default=None,
        max_length=2000,
        description="https link with {origin} {destination} {mode} {equipment} {date}.",
    )
    service_lines: list[ServiceLine] = Field(default_factory=list, max_length=10)
    is_active: bool = True
    notes: str | None = Field(default=None, max_length=500)


class RateSourceResponse(ResponseModel):
    """A rate source."""

    id: uuid.UUID
    name: str
    website: str
    lane_url_template: str | None
    service_lines: list[str]
    is_active: bool
    notes: str | None
    updated_at: datetime


class LinkPreviewRequest(StrictRequestModel):
    """A lane to try a link template with."""

    lane_url_template: str = Field(min_length=8, max_length=2000)
    origin: str = Field(default="Chennai", max_length=120)
    destination: str = Field(default="Jebel Ali", max_length=120)
    mode: str | None = Field(default="fcl", max_length=20)
    equipment: str | None = Field(default="40HC", max_length=30)


class LinkPreviewResponse(ResponseModel):
    """The link a template gives."""

    url: str


class MarketCheckRequest(StrictRequestModel):
    """A rate seen on a website."""

    source_id: uuid.UUID | None = None
    source_name: str | None = Field(default=None, max_length=120)
    amount: Money
    currency: Currency
    unit: str = Field(default="container", max_length=20, description="kg, cbm, container...")
    equipment: str | None = Field(default=None, max_length=30)
    valid_until: date | None = None
    transit_days: int | None = Field(default=None, ge=0, le=365)
    note: str | None = Field(default=None, max_length=500)


class MarketCheckResponse(ResponseModel):
    """A recorded market rate."""

    id: uuid.UUID
    deal_id: uuid.UUID
    source_id: uuid.UUID | None
    source_name: str
    amount: float
    currency: str
    unit: str
    equipment: str | None
    valid_until: date | None
    transit_days: int | None
    note: str | None
    checked_by: str
    created_at: datetime


class ShipmentQueryResponse(ResponseModel):
    """What was priced."""

    origin: str | None
    destination: str | None
    mode: str | None
    service_line: str
    equipment: str | None
    container_quantity: int | None
    weight_kg: float | None
    volume_cbm: float | None
    on_date: date | None


class RateOptionResponse(ResponseModel):
    """Freight plus surcharges from one sheet for one lane."""

    index: int
    sheet_id: uuid.UUID
    sheet_name: str
    tab: str
    carrier: str | None
    origin: str
    destination: str
    mode: str | None
    equipment: str | None
    currency: str
    total: float
    valid_to: date | None
    transit_days: int | None
    exact_lane: bool
    reasons: list[str]
    needs_attention: list[str]
    sources: list[str]
    rates: list[RateResponse]


class SourceLinkResponse(ResponseModel):
    """A rate website with the deal's lane filled in."""

    source_id: uuid.UUID
    name: str
    url: str


class LaneSuggestionResponse(ResponseModel):
    """The sheet places the AI matched the deal's lane to."""

    origin: str | None
    destination: str | None
    confidence: float
    usable: bool
    ai_run_id: uuid.UUID


class DealPricingResponse(ResponseModel):
    """The deal's Pricing tab."""

    query: ShipmentQueryResponse
    options: list[RateOptionResponse]
    links: list[SourceLinkResponse]
    checks: list[MarketCheckResponse]
    lane: LaneSuggestionResponse | None = None
    sheets: int


class LaneChoice(StrictRequestModel):
    """Optionally price another spelling of the lane (places from the sheets)."""

    origin: str | None = Field(default=None, max_length=200)
    destination: str | None = Field(default=None, max_length=200)

    def lane(self) -> Lane | None:
        """Both places, or None."""
        if self.origin and self.destination:
            return (self.origin, self.destination)
        return None


class DraftFromRatesRequest(LaneChoice):
    """Draft a quote from a rate option."""

    option_index: int = Field(default=0, ge=0, le=20)
    markup_percent: Annotated[Decimal, Field(ge=0, le=100, decimal_places=2)] = Decimal(0)
    validity_days: int | None = Field(default=None, ge=1, le=90)
    notes: str = Field(default="", max_length=2000)


class FillFromRatesRequest(LaneChoice):
    """Fill a draft quote from a rate option."""

    option_index: int = Field(default=0, ge=0, le=20)
    markup_percent: Annotated[Decimal, Field(ge=0, le=100, decimal_places=2)] = Decimal(0)


class PricedQuoteResponse(ResponseModel):
    """A quote priced from the sheets."""

    quote_id: uuid.UUID
    number: str
    currency: str
    total_amount: float
    sheet_name: str
    sources: list[str]
    needs_attention: list[str]


class LanePointResponse(ResponseModel):
    """One price on a lane over time."""

    kind: Literal["sheet", "market"]
    label: str
    observed_on: date
    amount: float
    currency: str
    unit: str
    equipment: str | None


# --- dependencies ----------------------------------------------------------------------------


def get_sheet_service(session: TenantSession, container: ContainerDependency) -> RateSheetService:
    """Rate sheets for the request."""
    return RateSheetService(session, container.clock)


def get_source_service(session: TenantSession, container: ContainerDependency) -> RateSourceService:
    """Rate sources for the request."""
    return RateSourceService(session, container.clock)


def get_check_service(session: TenantSession, container: ContainerDependency) -> MarketCheckService:
    """Market checks for the request."""
    return MarketCheckService(session, container.clock)


def get_pricing_service(session: TenantSession, container: ContainerDependency) -> PricingService:
    """Deterministic deal pricing (no AI) for the request."""
    return PricingService(
        session, container.clock, QuoteService(session, container.clock, container.task_queue)
    )


Sheets = Annotated[RateSheetService, Depends(get_sheet_service)]
Sources = Annotated[RateSourceService, Depends(get_source_service)]
Checks = Annotated[MarketCheckService, Depends(get_check_service)]
Pricing = Annotated[PricingService, Depends(get_pricing_service)]


def _sheet(sheet: RateSheet) -> RateSheetResponse:
    response = RateSheetResponse.model_validate(sheet)
    response.error_count = len(sheet.errors or [])
    return response


def _sheet_detail(sheet: RateSheet) -> RateSheetDetailResponse:
    mapping = sheet.column_mapping or {}
    errors = sheet.errors or []
    return RateSheetDetailResponse(
        **_sheet(sheet).model_dump(),
        errors=[RowErrorResponse(**error) for error in errors],
        detected_mapping=mapping.get("detected") or {},
        mapping_override=mapping.get("override") or {},
        fields=list(FIELDS),
    )


def _query(query: ShipmentQuery) -> ShipmentQueryResponse:
    return ShipmentQueryResponse(
        origin=query.origin,
        destination=query.destination,
        mode=query.mode,
        service_line=query.service_line,
        equipment=query.equipment,
        container_quantity=query.container_quantity,
        weight_kg=float(query.weight_kg) if query.weight_kg is not None else None,
        volume_cbm=float(query.volume_cbm) if query.volume_cbm is not None else None,
        on_date=query.on_date,
    )


def _option(index: int, option: RateOption) -> RateOptionResponse:
    return RateOptionResponse(
        index=index,
        sheet_id=option.sheet_id,
        sheet_name=option.sheet_name,
        tab=option.tab,
        carrier=option.carrier,
        origin=option.origin,
        destination=option.destination,
        mode=option.mode,
        equipment=option.equipment,
        currency=option.currency,
        total=float(option.total),
        valid_to=option.valid_to,
        transit_days=option.transit_days,
        exact_lane=option.exact_lane,
        reasons=option.reasons,
        needs_attention=option.other_currency,
        sources=describe_sources(option),
        rates=[
            RateResponse.model_validate(rate).model_copy(update={"sheet_name": option.sheet_name})
            for rate in option.rates
        ],
    )


async def _deal_pricing(
    pricing: DealPricing,
    deal_id: uuid.UUID,
    sources: RateSourceService,
    checks: MarketCheckService,
    sheets: RateSheetService,
) -> DealPricingResponse:
    links = [
        SourceLinkResponse(
            source_id=source.id,
            name=source.name,
            url=render_lane_link(source.lane_url_template, pricing.query)
            if source.lane_url_template
            else source.website,
        )
        for source in await sources.list_sources(active_only=True)
        if not source.service_lines
        or pricing.query.service_line in source.service_lines
        or pricing.query.service_line == "any"
    ]
    lane = pricing.lane
    return DealPricingResponse(
        query=_query(pricing.query),
        options=[_option(index, option) for index, option in enumerate(pricing.options)],
        links=links,
        checks=[
            MarketCheckResponse.model_validate(row) for row in await checks.list_checks(deal_id)
        ],
        lane=LaneSuggestionResponse(
            origin=lane.origin,
            destination=lane.destination,
            confidence=lane.confidence,
            usable=lane.usable,
            ai_run_id=lane.ai_run_id,
        )
        if lane
        else None,
        sheets=len(await sheets.list_sheets()),
    )


def _priced(priced: PricedQuote) -> PricedQuoteResponse:
    return PricedQuoteResponse(
        quote_id=priced.quote.id,
        number=priced.quote.number,
        currency=priced.quote.currency,
        total_amount=float(priced.quote.total_amount),
        sheet_name=priced.option.sheet_name,
        sources=priced.sources,
        needs_attention=priced.needs_attention,
    )


# --- rate sheets -----------------------------------------------------------------------------


@router.get(
    "/sheets", response_model=list[RateSheetResponse], summary="Rate sheets", responses=READ
)
async def list_rate_sheets(_principal: Reader, sheets: Sheets) -> list[RateSheetResponse]:
    """Every rate sheet, from the library's `Rates/` folder and uploads."""
    return [_sheet(sheet) for sheet in await sheets.list_sheets()]


@router.post(
    "/sheets/upload",
    response_model=RateSheetDetailResponse,
    summary="Upload an Excel or CSV rate sheet",
    description="Replaces the rates of an uploaded sheet with the same name. Sheets kept in the "
    "library's Rates folder (SharePoint/OneDrive) sync by themselves.",
    responses=WRITE,
)
async def upload_rate_sheet(
    _principal: Writer,
    sheets: Sheets,
    container: ContainerDependency,
    idempotency: IdempotencyGuardDependency,
    file: Annotated[UploadFile, File(description="The .xlsx or .csv rate sheet.")],
    name: Annotated[str | None, Form(max_length=300)] = None,
    default_currency: Annotated[str | None, Form(pattern=r"^[A-Za-z]{3}$")] = None,
) -> RateSheetDetailResponse | JSONResponse:
    """Import an uploaded sheet."""
    content = await file.read()
    filename = file.filename or "rates.xlsx"

    async def upload() -> RateSheetDetailResponse:
        if not filename.lower().endswith(RATE_SHEET_EXTENSIONS):
            raise BusinessRuleViolationError("Rate sheets must be .xlsx or .csv files.")
        if len(content) > container.settings.max_upload_bytes:
            limit = container.settings.max_upload_bytes // (1024 * 1024)
            raise BusinessRuleViolationError(f"Files must be {limit} MB or smaller.")
        sheet = await sheets.import_sheet(
            name=(name or filename).strip()[:300] or filename,
            filename=filename,
            content=content,
            source=SHEET_SOURCE_UPLOAD,
            default_currency=default_currency,
        )
        return _sheet_detail(sheet)

    return await run_idempotent(idempotency, status.HTTP_200_OK, upload)


@router.get(
    "/sheets/{sheet_id}",
    response_model=RateSheetDetailResponse,
    summary="A rate sheet with its row problems and column mapping",
    responses=READ,
)
async def read_rate_sheet(
    sheet_id: uuid.UUID, _principal: Reader, sheets: Sheets
) -> RateSheetDetailResponse:
    """A sheet."""
    return _sheet_detail(await sheets.require_sheet(sheet_id))


@router.get(
    "/sheets/{sheet_id}/preview",
    response_model=dict[str, list[list[str]]],
    summary="The first rows of every tab",
    responses=READ,
)
async def preview_rate_sheet(
    sheet_id: uuid.UUID, _principal: Reader, sheets: Sheets
) -> dict[str, list[list[str]]]:
    """For the column-mapping editor."""
    return await sheets.preview_rows(sheet_id)


@router.put(
    "/sheets/{sheet_id}/mapping",
    response_model=RateSheetDetailResponse,
    summary="Choose the columns and re-import",
    responses=WRITE,
)
async def update_rate_sheet_mapping(
    sheet_id: uuid.UUID, payload: MappingRequest, _principal: Writer, sheets: Sheets
) -> RateSheetDetailResponse:
    """Save which header holds which field and re-read the stored file."""
    return _sheet_detail(
        await sheets.update_mapping(sheet_id, payload.mapping, payload.default_currency)
    )


@router.post(
    "/sheets/{sheet_id}/reimport",
    response_model=RateSheetDetailResponse,
    summary="Re-read a rate sheet",
    responses=WRITE,
)
async def reimport_rate_sheet(
    sheet_id: uuid.UUID, _principal: Writer, sheets: Sheets, idempotency: IdempotencyGuardDependency
) -> RateSheetDetailResponse | JSONResponse:
    """Import the stored file again."""

    async def reimport() -> RateSheetDetailResponse:
        return _sheet_detail(await sheets.reimport_sheet(sheet_id))

    return await run_idempotent(idempotency, status.HTTP_200_OK, reimport)


@router.delete(
    "/sheets/{sheet_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an uploaded rate sheet",
    responses=WRITE,
)
async def delete_rate_sheet(sheet_id: uuid.UUID, _principal: Writer, sheets: Sheets) -> Response:
    """Delete an uploaded sheet (409 for library sheets: remove the file in the library)."""
    await sheets.delete_sheet(sheet_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- rates -----------------------------------------------------------------------------------


@router.get("/rates", response_model=RatePageResponse, summary="Search rates", responses=READ)
async def list_rates(
    _principal: Reader,
    session: TenantSession,
    container: ContainerDependency,
    lane: Annotated[
        str | None, Query(max_length=120, description="Place, code or carrier.")
    ] = None,
    origin: Annotated[str | None, Query(max_length=120)] = None,
    destination: Annotated[str | None, Query(max_length=120)] = None,
    service_line: Annotated[ServiceLine | None, Query()] = None,
    equipment: Annotated[str | None, Query(max_length=30)] = None,
    sheet_id: Annotated[uuid.UUID | None, Query()] = None,
    include_expired: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> RatePageResponse:
    """Rates valid today (or every rate with `include_expired`)."""
    rows, total = await search_rates(
        session,
        RateFilter(
            lane=lane,
            origin=origin,
            destination=destination,
            service_line=service_line.value if service_line else None,
            equipment=equipment,
            sheet_id=sheet_id,
            valid_on=container.clock.now().date(),
            include_expired=include_expired,
        ),
        limit=limit,
        offset=offset,
    )
    return RatePageResponse(
        items=[
            RateResponse.model_validate(rate).model_copy(update={"sheet_name": name})
            for rate, name in rows
        ],
        total=total,
    )


@router.get(
    "/lanes/history",
    response_model=list[LanePointResponse],
    summary="Sheet and market prices of a lane over time",
    responses=READ,
)
async def read_lane_history(
    _principal: Reader,
    session: TenantSession,
    origin: Annotated[str, Query(min_length=2, max_length=120)],
    destination: Annotated[str, Query(min_length=2, max_length=120)],
    equipment: Annotated[str | None, Query(max_length=30)] = None,
) -> list[LanePointResponse]:
    """Freight rates in the sheets (by validity start or import) and market checks on deals."""
    points = await lane_history(session, origin, destination, equipment)
    return [LanePointResponse(**point) for point in points]


# --- rate sources ----------------------------------------------------------------------------


def _source_input(payload: RateSourceRequest) -> RateSourceInput:
    return RateSourceInput(
        name=payload.name,
        website=payload.website,
        lane_url_template=payload.lane_url_template,
        service_lines=[line.value for line in payload.service_lines],
        is_active=payload.is_active,
        notes=payload.notes,
    )


@router.get(
    "/sources", response_model=list[RateSourceResponse], summary="Rate websites", responses=READ
)
async def list_rate_sources(_principal: Reader, sources: Sources) -> list[RateSourceResponse]:
    """The websites the team checks for rates."""
    return [RateSourceResponse.model_validate(row) for row in await sources.list_sources()]


@router.post(
    "/sources",
    response_model=RateSourceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a rate website",
    responses=WRITE,
)
async def create_rate_source(
    payload: RateSourceRequest,
    _principal: Writer,
    sources: Sources,
    idempotency: IdempotencyGuardDependency,
) -> RateSourceResponse | JSONResponse:
    """Add a website (https link; no logins are stored)."""

    async def create() -> RateSourceResponse:
        return RateSourceResponse.model_validate(await sources.save_source(_source_input(payload)))

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, create)


@router.post(
    "/sources/preview-link",
    response_model=LinkPreviewResponse,
    summary="Try a lane link template",
    responses=READ,
)
async def preview_rate_source_link(
    payload: LinkPreviewRequest, _principal: Reader
) -> LinkPreviewResponse:
    """The link a template gives for an example lane."""
    template = validate_lane_template(payload.lane_url_template) or ""
    return LinkPreviewResponse(
        url=render_lane_link(
            template,
            ShipmentQuery(
                origin=payload.origin,
                destination=payload.destination,
                mode=payload.mode,
                equipment=payload.equipment,
            ),
        )
    )


@router.put(
    "/sources/{source_id}",
    response_model=RateSourceResponse,
    summary="Change a rate website",
    responses=WRITE,
)
async def update_rate_source(
    source_id: uuid.UUID, payload: RateSourceRequest, _principal: Writer, sources: Sources
) -> RateSourceResponse:
    """Change a website."""
    return RateSourceResponse.model_validate(
        await sources.save_source(_source_input(payload), source_id)
    )


@router.delete(
    "/sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a rate website",
    responses=WRITE,
)
async def delete_rate_source(
    source_id: uuid.UUID, _principal: Writer, sources: Sources
) -> Response:
    """Delete a website (market checks keep its name)."""
    await sources.delete_source(source_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- deals -----------------------------------------------------------------------------------


@router.get(
    "/deals/{deal_id}",
    response_model=DealPricingResponse,
    summary="Rates, website links and market checks for a deal",
    responses=READ,
)
async def read_deal_pricing(
    deal_id: uuid.UUID,
    _principal: Reader,
    pricing: Pricing,
    sources: Sources,
    checks: Checks,
    sheets: Sheets,
    origin: Annotated[str | None, Query(max_length=200, description="Sheet origin to use.")] = None,
    destination: Annotated[str | None, Query(max_length=200)] = None,
) -> DealPricingResponse:
    """Deterministic matching; pass a sheet origin and destination to price another spelling."""
    lane = LaneChoice(origin=origin, destination=destination).lane()
    return await _deal_pricing(
        await pricing.deal_pricing(deal_id, lane), deal_id, sources, checks, sheets
    )


@router.post(
    "/deals/{deal_id}/match-lane",
    response_model=DealPricingResponse,
    summary="Let the AI match the deal's lane to the rate sheets",
    description="Only places present in the sheets can be chosen; prices still come from the "
    "sheets.",
    responses=WRITE,
    dependencies=[AIRateLimit],
)
async def match_deal_lane(
    deal_id: uuid.UUID,
    _principal: AIUser,
    session: TenantSession,
    container: ContainerDependency,
    runtime: Annotated[AIRuntime, Depends(get_ai_runtime)],
    sources: Sources,
    checks: Checks,
    sheets: Sheets,
) -> DealPricingResponse:
    """AI lane matching (counts against the AI budget)."""
    service = PricingService(
        session,
        container.clock,
        QuoteService(session, container.clock, container.task_queue),
        runtime,
    )
    return await _deal_pricing(
        await service.suggest_lane(deal_id), deal_id, sources, checks, sheets
    )


@router.post(
    "/deals/{deal_id}/quotes",
    response_model=PricedQuoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Draft a quote from the rate sheets",
    description="Creates a DRAFT; it is never sent automatically.",
    responses=WRITE,
)
async def draft_quote_from_rates(
    deal_id: uuid.UUID,
    payload: DraftFromRatesRequest,
    principal: Writer,
    pricing: Pricing,
    idempotency: IdempotencyGuardDependency,
) -> PricedQuoteResponse | JSONResponse:
    """Draft a quote priced from a rate option."""

    async def draft() -> PricedQuoteResponse:
        return _priced(
            await pricing.draft_quote_from_rates(
                deal_id,
                principal.subject,
                option_index=payload.option_index,
                markup_percent=payload.markup_percent,
                validity_days=payload.validity_days,
                notes=payload.notes,
                lane=payload.lane(),
            )
        )

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, draft)


@router.post(
    "/quotes/{quote_id}/fill",
    response_model=PricedQuoteResponse,
    summary="Fill a draft quote from the rate sheets",
    responses=WRITE,
)
async def fill_quote_from_rates(
    quote_id: uuid.UUID,
    payload: FillFromRatesRequest,
    principal: Writer,
    pricing: Pricing,
    idempotency: IdempotencyGuardDependency,
) -> PricedQuoteResponse | JSONResponse:
    """Replace a draft's lines with a rate option's prices (409 once sent)."""

    async def fill() -> PricedQuoteResponse:
        return _priced(
            await pricing.fill_quote_from_rates(
                quote_id,
                principal.subject,
                option_index=payload.option_index,
                markup_percent=payload.markup_percent,
                lane=payload.lane(),
            )
        )

    return await run_idempotent(idempotency, status.HTTP_200_OK, fill)


@router.get(
    "/deals/{deal_id}/checks",
    response_model=list[MarketCheckResponse],
    summary="Market rates recorded for a deal",
    responses=READ,
)
async def list_market_checks(
    deal_id: uuid.UUID, _principal: Reader, checks: Checks
) -> list[MarketCheckResponse]:
    """Cheapest first."""
    return [MarketCheckResponse.model_validate(row) for row in await checks.list_checks(deal_id)]


@router.post(
    "/deals/{deal_id}/checks",
    response_model=MarketCheckResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a rate seen on a website",
    responses=WRITE,
)
async def record_market_check(
    deal_id: uuid.UUID,
    payload: MarketCheckRequest,
    principal: Writer,
    checks: Checks,
    idempotency: IdempotencyGuardDependency,
) -> MarketCheckResponse | JSONResponse:
    """Record a market rate on the deal (shown next to the sheet rates)."""

    async def record() -> MarketCheckResponse:
        check = await checks.record_check(
            deal_id,
            principal.subject,
            MarketCheckInput(
                amount=payload.amount,
                currency=payload.currency,
                unit=payload.unit,
                source_id=payload.source_id,
                source_name=payload.source_name,
                equipment=payload.equipment,
                valid_until=payload.valid_until,
                transit_days=payload.transit_days,
                note=payload.note,
            ),
        )
        return MarketCheckResponse.model_validate(check)

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, record)


@router.delete(
    "/checks/{check_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a market check",
    responses=WRITE,
)
async def delete_market_check(check_id: uuid.UUID, _principal: Writer, checks: Checks) -> Response:
    """Delete a recorded market rate."""
    await checks.delete_check(check_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
