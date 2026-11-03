"""Suppression and country-calendar endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Self

from fastapi import APIRouter, Depends, Path, Query, status
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, EmailStr, Field, model_validator

from lanereach.auth.dependencies import IdempotencyGuardDependency, TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ClockDependency
from lanereach.core.idempotency import run_idempotent
from lanereach.core.openapi import READ, WRITE
from lanereach.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.domain.calendars.service import describe_country_calendars, set_country_calendar
from lanereach.domain.suppressions.models import SuppressionReason
from lanereach.domain.suppressions.service import SuppressionService

router = APIRouter(tags=["Suppressions & calendars"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Writer = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_WRITE))]
Admin = Annotated[Principal, Depends(require_scopes(Scope.ADMIN_MANAGE))]


class SuppressionCreateRequest(StrictRequestModel):
    """Suppress a contact or an address. Give `contact_id` or `email`."""

    contact_id: uuid.UUID | None = None
    email: EmailStr | None = None
    reason: SuppressionReason = SuppressionReason.DO_NOT_CONTACT
    note: str | None = Field(default=None, max_length=500)
    expires_at: AwareDatetime | None = Field(
        default=None, description="Leave empty for a permanent suppression."
    )

    @model_validator(mode="after")
    def _require_target(self) -> Self:
        if self.contact_id is None and self.email is None:
            raise ValueError("give contact_id or email")
        return self


class SuppressionResponse(ResponseModel):
    """A suppression entry."""

    id: uuid.UUID
    contact_id: uuid.UUID | None
    email: str
    reason: SuppressionReason
    source: str
    note: str | None
    expires_at: datetime | None
    created_at: datetime


class CountryCalendarRequest(StrictRequestModel):
    """Working week of a country (overrides the built-in default)."""

    timezone: str = Field(examples=["Asia/Riyadh"])
    weekend_days: list[int] = Field(
        min_length=0, max_length=6, description="ISO weekdays: 1 = Monday ... 7 = Sunday."
    )
    extra_holidays: list[date] = Field(default_factory=list, max_length=100)
    extra_workdays: list[date] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _check_days(self) -> Self:
        if any(day < 1 or day > 7 for day in self.weekend_days) or len(
            set(self.weekend_days)
        ) != len(self.weekend_days):
            raise ValueError("weekend_days must be distinct ISO weekdays (1-7)")
        return self


class CountryCalendarResponse(ResponseModel):
    """A country's working week."""

    country: str
    timezone: str
    weekend_days: list[int]
    extra_holidays: list[date]
    extra_workdays: list[date]
    is_default: bool


def get_suppression_service(session: TenantSession, clock: ClockDependency) -> SuppressionService:
    """Suppression service for the request."""
    return SuppressionService(session, clock)


Service = Annotated[SuppressionService, Depends(get_suppression_service)]


@router.get(
    "/suppressions",
    response_model=Page[SuppressionResponse],
    summary="List suppressions",
    responses=READ,
)
async def list_suppressions(
    _principal: Reader,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[SuppressionResponse]:
    """List suppressions newest first."""
    rows, next_cursor = await service.repository.page(limit=limit, cursor=cursor)
    return Page(
        items=[SuppressionResponse.model_validate(row) for row in rows], next_cursor=next_cursor
    )


@router.post(
    "/suppressions",
    response_model=SuppressionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Suppress a contact or address",
    responses=WRITE,
)
async def create_suppression(
    payload: SuppressionCreateRequest,
    principal: Writer,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> SuppressionResponse | JSONResponse:
    """Add a suppression."""

    async def create() -> SuppressionResponse:
        return SuppressionResponse.model_validate(
            await service.suppress(
                contact_id=payload.contact_id,
                email=str(payload.email) if payload.email else None,
                reason=payload.reason,
                note=payload.note,
                expires_at=payload.expires_at,
                source=f"user:{principal.subject}",
            )
        )

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, create)


@router.delete(
    "/suppressions/{suppression_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Lift a suppression",
    responses=WRITE,
)
async def delete_suppression(
    suppression_id: uuid.UUID, _principal: Admin, service: Service
) -> None:
    """Lift a suppression."""
    await service.lift(suppression_id)


@router.get(
    "/country-calendars",
    response_model=list[CountryCalendarResponse],
    summary="Working weeks per country (defaults and overrides)",
    responses=READ,
)
async def list_country_calendars(
    _principal: Reader, session: TenantSession
) -> list[CountryCalendarResponse]:
    """List the built-in calendars merged with tenant overrides."""
    return [
        CountryCalendarResponse.model_validate(view, from_attributes=True)
        for view in await describe_country_calendars(session)
    ]


@router.put(
    "/country-calendars/{country}",
    response_model=CountryCalendarResponse,
    summary="Override a country's working week",
    responses=WRITE,
)
async def upsert_country_calendar(
    country: Annotated[str, Path(pattern=r"^[A-Z]{2}$")],
    payload: CountryCalendarRequest,
    _principal: Admin,
    session: TenantSession,
    clock: ClockDependency,
) -> CountryCalendarResponse:
    """Create or replace a country calendar override."""
    view = await set_country_calendar(
        session,
        clock,
        country,
        timezone=payload.timezone,
        weekend_days=payload.weekend_days,
        extra_holidays=payload.extra_holidays,
        extra_workdays=payload.extra_workdays,
    )
    return CountryCalendarResponse.model_validate(view, from_attributes=True)
