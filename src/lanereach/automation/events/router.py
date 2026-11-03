"""Event endpoints: inject events (testing, integrations) and inspect them."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Self

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from pydantic import EmailStr, Field, model_validator
from sqlalchemy import func, select

from lanereach.auth.dependencies import IdempotencyGuardDependency, TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.automation.events.models import EventStatus, EventType
from lanereach.automation.events.service import EventRepository, publish_event
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import BusinessRuleViolationError, NotFoundError
from lanereach.core.idempotency import run_idempotent
from lanereach.core.openapi import READ, WRITE
from lanereach.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.domain.contacts.models import Contact

router = APIRouter(prefix="/automation/events", tags=["Events"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Publisher = Annotated[Principal, Depends(require_scopes(Scope.EVENTS_WRITE))]

INJECTABLE_EVENTS = {
    EventType.CONTACT_CREATED,
    EventType.CONTACT_TAG_ADDED,
    EventType.REPLY_CLASSIFIED,
    EventType.MANUAL_ENROLL,
    EventType.CUSTOM,
}
CUSTOM_EVENT_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class EventCreateRequest(StrictRequestModel):
    """An event to publish (for example to simulate a classified reply)."""

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "type": "custom.event",
                    "contact_email": "buyer@importer.example",
                    "payload": {"name": "quote_requested", "lane": "YYZ-IST"},
                }
            ]
        }
    }

    type: EventType = Field(examples=["contact.tag_added"])
    contact_id: uuid.UUID | None = None
    contact_email: EmailStr | None = Field(
        default=None, description="Instead of `contact_id` (handy for CRMs and web forms)."
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        examples=[{"tag": "uae-importers"}],
        description="`contact.tag_added`: `{tag}`; `reply.classified`: `{classification: "
        "{bucket, confidence, summary}}`; `manual.enroll`: `{workflow_id}`; `custom.event`: "
        "`{name}` (lower-case, e.g. `quote_requested`) plus any data for the workflow.",
    )
    idempotency_key: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.contact_id is None) == (self.contact_email is None):
            raise ValueError("give either contact_id or contact_email")
        if self.type is EventType.CUSTOM and not CUSTOM_EVENT_NAME.fullmatch(
            str(self.payload.get("name", ""))
        ):
            raise ValueError(
                "custom events need payload.name: lower-case letters, digits, '_', '.', '-'"
            )
        return self


class EventResponse(ResponseModel):
    """An event and how it was processed."""

    id: uuid.UUID
    type: EventType
    contact_id: uuid.UUID | None
    payload: dict[str, Any]
    occurred_at: datetime
    status: EventStatus
    processed_at: datetime | None
    enrollments_created: int
    error: str | None
    created_at: datetime


@router.post(
    "",
    response_model=EventResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Publish an event",
    description="Stores the event; a worker matches it to triggers of published workflows.",
    responses=WRITE,
)
async def create_event(
    payload: EventCreateRequest,
    principal: Publisher,
    session: TenantSession,
    container: ContainerDependency,
    idempotency: IdempotencyGuardDependency,
) -> EventResponse | JSONResponse:
    """Publish an event."""

    async def publish() -> EventResponse:
        if payload.type not in INJECTABLE_EVENTS:
            raise BusinessRuleViolationError(f"'{payload.type}' events cannot be injected.")
        if payload.contact_id is not None:
            contact = await session.get(Contact, payload.contact_id)
        else:
            contact = await session.scalar(
                select(Contact).where(
                    func.lower(Contact.email) == str(payload.contact_email).lower()
                )
            )
        if contact is None:
            raise NotFoundError("Contact not found.")
        event = await publish_event(
            session,
            container.clock,
            event_type=payload.type,
            contact_id=contact.id,
            payload={**payload.payload, "enrolled_by": principal.subject},
            idempotency_key=payload.idempotency_key,
            task_queue=container.task_queue,
        )
        return EventResponse.model_validate(event)

    return await run_idempotent(idempotency, status.HTTP_202_ACCEPTED, publish)


@router.get("", response_model=Page[EventResponse], summary="List events", responses=READ)
async def list_events(
    _principal: Reader,
    session: TenantSession,
    event_type: Annotated[EventType | None, Query(alias="type")] = None,
    status_filter: Annotated[EventStatus | None, Query(alias="status")] = None,
    contact_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[EventResponse]:
    """List events newest first."""
    events, next_cursor = await EventRepository(session).list_events(
        event_type=event_type,
        status=status_filter,
        contact_id=contact_id,
        limit=limit,
        cursor=cursor,
    )
    return Page(
        items=[EventResponse.model_validate(event) for event in events], next_cursor=next_cursor
    )
