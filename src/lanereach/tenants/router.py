"""Tenant settings: the tenant-wide kill switch and sending window."""

from __future__ import annotations

from datetime import datetime, time
from typing import Annotated, Self

from fastapi import APIRouter, Depends
from pydantic import Field, model_validator

from lanereach.auth.dependencies import CurrentPrincipal, TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ClockDependency
from lanereach.core.errors import BusinessRuleViolationError
from lanereach.core.openapi import READ, WRITE
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.domain.senders.models import ServiceLine
from lanereach.tenants.branding import TenantBranding, load_branding
from lanereach.tenants.models import Tenant

router = APIRouter(prefix="/settings", tags=["Settings"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Admin = Annotated[Principal, Depends(require_scopes(Scope.ADMIN_MANAGE))]


class SendingSettingsResponse(ResponseModel):
    """Tenant-wide sending controls."""

    tenant_name: str
    sending_paused: bool
    sending_paused_at: datetime | None
    sending_window_start: time
    sending_window_end: time
    track_opens: bool
    track_clicks: bool


class SendingSettingsUpdateRequest(StrictRequestModel):
    """Changes to sending controls. `sending_paused` is the tenant kill switch."""

    sending_paused: bool | None = None
    sending_window_start: time | None = None
    sending_window_end: time | None = None
    track_opens: bool | None = None
    track_clicks: bool | None = None

    @model_validator(mode="after")
    def _check_window(self) -> Self:
        if (
            self.sending_window_start is not None
            and self.sending_window_end is not None
            and self.sending_window_start >= self.sending_window_end
        ):
            raise ValueError("the window must start before it ends")
        return self


def _describe(tenant: Tenant) -> SendingSettingsResponse:
    return SendingSettingsResponse(
        tenant_name=tenant.name,
        sending_paused=tenant.sending_paused_at is not None,
        sending_paused_at=tenant.sending_paused_at,
        sending_window_start=tenant.sending_window_start,
        sending_window_end=tenant.sending_window_end,
        track_opens=tenant.track_opens,
        track_clicks=tenant.track_clicks,
    )


async def _tenant(session: TenantSession, principal: CurrentPrincipal) -> Tenant:
    tenant = await session.get(Tenant, principal.tenant_id)
    assert tenant is not None  # noqa: S101 - authenticated tenants exist (RLS shows own row)
    return tenant


@router.get(
    "/sending", response_model=SendingSettingsResponse, summary="Sending controls", responses=READ
)
async def read_sending_settings(
    _principal: Reader, tenant: Annotated[Tenant, Depends(_tenant)]
) -> SendingSettingsResponse:
    """Read the kill switch and window."""
    return _describe(tenant)


@router.patch(
    "/sending",
    response_model=SendingSettingsResponse,
    summary="Pause/resume all sending, change the window or open/click tracking",
    description="While paused, every email is deferred; after resuming, deferred emails go out "
    "in the recipient's next sending window. Tracking applies to HTML emails sent afterwards.",
    responses=WRITE,
)
async def update_sending_settings(
    payload: SendingSettingsUpdateRequest,
    _principal: Admin,
    tenant: Annotated[Tenant, Depends(_tenant)],
    clock: ClockDependency,
) -> SendingSettingsResponse:
    """Update sending controls."""
    now = clock.now()
    if payload.sending_paused is True and tenant.sending_paused_at is None:
        tenant.sending_paused_at = now
    elif payload.sending_paused is False:
        tenant.sending_paused_at = None
    if payload.sending_window_start is not None:
        tenant.sending_window_start = payload.sending_window_start
    if payload.sending_window_end is not None:
        tenant.sending_window_end = payload.sending_window_end
    if payload.track_opens is not None:
        tenant.track_opens = payload.track_opens
    if payload.track_clicks is not None:
        tenant.track_clicks = payload.track_clicks
    if tenant.sending_window_start >= tenant.sending_window_end:
        raise BusinessRuleViolationError("The sending window must start before it ends.")
    tenant.updated_at = now
    return _describe(tenant)


class ServiceSettingsResponse(ResponseModel):
    """Which service lines the company sells (menus show only these)."""

    service_lines: list[ServiceLine]


class ServiceSettingsRequest(StrictRequestModel):
    """The service lines to offer; empty means every line."""

    service_lines: list[ServiceLine] = Field(default_factory=list, max_length=len(ServiceLine))


def _service_lines(tenant: Tenant) -> list[ServiceLine]:
    known = {line.value for line in ServiceLine}
    chosen = [ServiceLine(value) for value in tenant.service_lines or [] if value in known]
    return chosen or [line for line in ServiceLine if line is not ServiceLine.ANY]


@router.get(
    "/services",
    response_model=ServiceSettingsResponse,
    summary="Service lines on offer",
    responses=READ,
)
async def read_service_settings(
    _principal: Reader, tenant: Annotated[Tenant, Depends(_tenant)]
) -> ServiceSettingsResponse:
    """The lines the company sells (all when none were chosen)."""
    return ServiceSettingsResponse(service_lines=_service_lines(tenant))


@router.put(
    "/services",
    response_model=ServiceSettingsResponse,
    summary="Choose the service lines on offer",
    responses=WRITE,
)
async def update_service_settings(
    payload: ServiceSettingsRequest,
    _principal: Admin,
    tenant: Annotated[Tenant, Depends(_tenant)],
    clock: ClockDependency,
) -> ServiceSettingsResponse:
    """Hide lines the company does not sell from menus and filters (data stays)."""
    tenant.service_lines = [
        line.value for line in payload.service_lines if line is not ServiceLine.ANY
    ]
    tenant.updated_at = clock.now()
    return ServiceSettingsResponse(service_lines=_service_lines(tenant))


@router.get(
    "/branding",
    response_model=TenantBranding,
    summary="Company look of generated agent signatures",
    responses=READ,
)
async def read_branding(
    _principal: Reader, tenant: Annotated[Tenant, Depends(_tenant)]
) -> TenantBranding:
    """Logo, tagline, offices strip, contact details and badges."""
    return load_branding(tenant.branding)


@router.put(
    "/branding",
    response_model=TenantBranding,
    summary="Change the company look of generated signatures",
    description="Logo and badges can also come from the library's `Brand/` folder.",
    responses=WRITE,
)
async def update_branding(
    payload: TenantBranding,
    _principal: Admin,
    tenant: Annotated[Tenant, Depends(_tenant)],
    clock: ClockDependency,
) -> TenantBranding:
    """Replace the branding (admin only)."""
    tenant.branding = payload.model_dump(mode="json")
    tenant.updated_at = clock.now()
    return payload
