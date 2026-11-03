"""Notification inbox endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from lanereach.auth.dependencies import TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ClockDependency
from lanereach.core.errors import NotFoundError
from lanereach.core.openapi import READ, WRITE
from lanereach.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from lanereach.core.repository import TenantRepository
from lanereach.core.schemas import ResponseModel
from lanereach.integrations.notifications.models import Notification

router = APIRouter(prefix="/notifications", tags=["Notifications"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]


class NotificationResponse(ResponseModel):
    """A notification."""

    id: uuid.UUID
    channel: str
    recipient: str
    subject: str
    body: str
    payload: dict[str, Any]
    contact_id: uuid.UUID | None
    enrollment_id: uuid.UUID | None
    delivered_via: str
    read_at: datetime | None
    created_at: datetime


class NotificationRepository(TenantRepository[Notification]):
    """Queries over notifications."""

    model = Notification


@router.get(
    "", response_model=Page[NotificationResponse], summary="List notifications", responses=READ
)
async def list_notifications(
    _principal: Reader,
    session: TenantSession,
    channel: Annotated[str | None, Query(max_length=60)] = None,
    unread: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[NotificationResponse]:
    """List notifications newest first."""
    statement = select(Notification)
    if channel:
        statement = statement.where(Notification.channel == channel)
    if unread:
        statement = statement.where(Notification.read_at.is_(None))
    rows, next_cursor = await NotificationRepository(session).page(
        statement, limit=limit, cursor=cursor
    )
    return Page(
        items=[NotificationResponse.model_validate(row) for row in rows], next_cursor=next_cursor
    )


@router.post(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    summary="Mark a notification as read",
    responses=WRITE,
)
async def mark_notification_read(
    notification_id: uuid.UUID, _principal: Reader, session: TenantSession, clock: ClockDependency
) -> NotificationResponse:
    """Mark as read (idempotent)."""
    notification = await NotificationRepository(session).get(notification_id)
    if notification is None:
        raise NotFoundError("Notification not found.")
    notification.read_at = notification.read_at or clock.now()
    return NotificationResponse.model_validate(notification)
