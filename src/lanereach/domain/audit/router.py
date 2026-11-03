"""Audit log endpoint (admins)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from lanereach.auth.dependencies import TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.openapi import problem_responses
from lanereach.core.schemas import ResponseModel
from lanereach.domain.audit.service import list_audit_events

router = APIRouter(prefix="/audit-events", tags=["Settings"])

Admin = Annotated[Principal, Depends(require_scopes(Scope.ADMIN_MANAGE))]


class AuditEventResponse(ResponseModel):
    """One audited action."""

    id: uuid.UUID
    actor: str
    action: str
    entity_type: str
    entity_id: str | None
    summary: str
    changes: dict[str, Any]
    request_id: str | None
    occurred_at: datetime


class AuditPageResponse(ResponseModel):
    """A page of the audit log."""

    items: list[AuditEventResponse]
    next_cursor: str | None


@router.get(
    "",
    response_model=AuditPageResponse,
    summary="Audit log",
    description="Who changed sending controls, campaigns, settings, API keys, suppressions, "
    "agents and offices, newest first. Kept for two years.",
    responses=problem_responses(401, 403, 422),
)
async def list_audit_log(
    _principal: Admin,
    session: TenantSession,
    action: Annotated[
        str | None, Query(max_length=80, description="Prefix, e.g. `workflow.`")
    ] = None,
    entity_type: Annotated[str | None, Query(max_length=40)] = None,
    entity_id: Annotated[str | None, Query(max_length=100)] = None,
    actor: Annotated[str | None, Query(max_length=200)] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPageResponse:
    """List audit events."""
    page = await list_audit_events(
        session,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        limit=limit,
        cursor=cursor,
    )
    return AuditPageResponse(
        items=[AuditEventResponse.model_validate(item) for item in page.items],
        next_cursor=page.next_cursor,
    )
