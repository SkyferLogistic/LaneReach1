"""Lead endpoints for the pricing team."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import delete, select

from lanereach.auth.dependencies import TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import NotFoundError
from lanereach.core.openapi import READ, WRITE
from lanereach.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from lanereach.core.repository import TenantRepository
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.domain.crm.models import Deal
from lanereach.domain.leads.models import Lead, LeadPriority, LeadStatus
from lanereach.domain.leads.service import export_leads_csv
from lanereach.domain.leads.service import update_lead as update_lead_status

router = APIRouter(prefix="/leads", tags=["Leads"])

Pricing = Annotated[Principal, Depends(require_scopes(Scope.AI_REVIEW))]


class LeadResponse(ResponseModel):
    """A quote-ready lead."""

    id: uuid.UUID
    contact_id: uuid.UUID
    company_id: uuid.UUID | None
    office_id: uuid.UUID | None
    message_id: uuid.UUID | None
    enrollment_id: uuid.UUID | None
    status: LeadStatus
    priority: LeadPriority
    source: str
    summary: str | None
    shipment: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class LeadUpdateRequest(StrictRequestModel):
    """Pricing's progress on a lead."""

    status: LeadStatus | None = None
    priority: LeadPriority | None = None


class LeadRepository(TenantRepository[Lead]):
    """Queries over leads."""

    model = Lead


@router.get("", response_model=Page[LeadResponse], summary="List leads", responses=READ)
async def list_leads(
    _principal: Pricing,
    session: TenantSession,
    status_filter: Annotated[LeadStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[LeadResponse]:
    """List leads newest first."""
    statement = select(Lead)
    if status_filter is not None:
        statement = statement.where(Lead.status == status_filter)
    rows, next_cursor = await LeadRepository(session).page(statement, limit=limit, cursor=cursor)
    return Page(items=[LeadResponse.model_validate(row) for row in rows], next_cursor=next_cursor)


@router.get(
    "/export",
    response_class=Response,
    summary="Export leads as CSV",
    description="Newest first, with the contact, company and extracted shipment (opens in Excel).",
    responses={**READ, 200: {"content": {"text/csv": {}}}},
)
async def export_leads(
    _principal: Pricing,
    session: TenantSession,
    status_filter: Annotated[LeadStatus | None, Query(alias="status")] = None,
) -> Response:
    """Download leads."""
    content = await export_leads_csv(session, status_filter)
    return Response(
        content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="leads.csv"'},
    )


@router.get("/{lead_id}", response_model=LeadResponse, summary="Get a lead", responses=READ)
async def read_lead(
    lead_id: uuid.UUID, _principal: Pricing, session: TenantSession
) -> LeadResponse:
    """Get a lead."""
    lead = await LeadRepository(session).get(lead_id)
    if lead is None:
        raise NotFoundError("Lead not found.")
    return LeadResponse.model_validate(lead)


@router.patch("/{lead_id}", response_model=LeadResponse, summary="Update a lead", responses=WRITE)
async def update_lead(
    lead_id: uuid.UUID,
    payload: LeadUpdateRequest,
    _principal: Pricing,
    session: TenantSession,
    container: ContainerDependency,
) -> LeadResponse:
    """Update a lead's status or priority (a status change publishes `lead.status_changed`)."""
    lead = await update_lead_status(
        session,
        container.clock,
        container.task_queue,
        lead_id,
        status=payload.status,
        priority=payload.priority,
    )
    return LeadResponse.model_validate(lead)


@router.delete(
    "/{lead_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a quote request",
    description="Also deletes the deal it opened (a deal kept without its request would show "
    "a lane nobody asked for).",
    responses=WRITE,
)
async def delete_lead(lead_id: uuid.UUID, _principal: Pricing, session: TenantSession) -> Response:
    """Delete a lead and its deal."""
    lead = await LeadRepository(session).get(lead_id)
    if lead is None:
        raise NotFoundError("Lead not found.")
    await session.execute(delete(Deal).where(Deal.lead_id == lead.id))
    await session.delete(lead)
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
