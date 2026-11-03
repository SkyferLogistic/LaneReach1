"""Contact endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import JSONResponse, Response

from lanereach.auth.dependencies import IdempotencyGuardDependency, TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.idempotency import run_idempotent
from lanereach.core.openapi import READ, WRITE
from lanereach.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from lanereach.core.schemas import ResponseModel
from lanereach.domain.contacts.erase import erase_contact
from lanereach.domain.contacts.importer import ContactImporter
from lanereach.domain.contacts.models import ContactStage
from lanereach.domain.contacts.schemas import (
    ContactCreateRequest,
    ContactResponse,
    ContactUpdateRequest,
    MessageResponse,
    TagRequest,
)
from lanereach.domain.contacts.service import ContactService

router = APIRouter(prefix="/contacts", tags=["Companies & contacts"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Writer = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_WRITE))]
Admin = Annotated[Principal, Depends(require_scopes(Scope.ADMIN_MANAGE))]


def get_contact_service(session: TenantSession, container: ContainerDependency) -> ContactService:
    """Contact service for the request."""
    return ContactService(session, container.clock, container.task_queue)


Service = Annotated[ContactService, Depends(get_contact_service)]


@router.get("", response_model=Page[ContactResponse], summary="List contacts", responses=READ)
async def list_contacts(
    _principal: Reader,
    service: Service,
    search: Annotated[str | None, Query(alias="q", max_length=200)] = None,
    tag: Annotated[str | None, Query(max_length=50)] = None,
    company_id: Annotated[uuid.UUID | None, Query()] = None,
    stage: Annotated[ContactStage | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[ContactResponse]:
    """List contacts newest first."""
    contacts, next_cursor = await service.repository.search(
        query=search, tag=tag, company_id=company_id, stage=stage, limit=limit, cursor=cursor
    )
    return Page(
        items=[ContactResponse.model_validate(item) for item in contacts], next_cursor=next_cursor
    )


class ContactImportResponse(ResponseModel):
    """What a CSV import did."""

    created: int
    updated: int
    tagged: int
    companies_created: int
    errors: list[str]


@router.post(
    "/import",
    response_model=ContactImportResponse,
    summary="Import contacts from CSV (matched by email)",
    description="Columns: `email` and optionally `first_name, last_name, job_title, company, "
    "country, city, language, network, office, tags, owner_email, timezone` (up to 5,000 "
    "rows). Missing companies are created. `tag` is added to every row, which starts the "
    "workflows listening to it; new contacts also publish `contact.created`.",
    responses=WRITE,
)
async def import_contacts(
    _principal: Writer,
    session: TenantSession,
    container: ContainerDependency,
    idempotency: IdempotencyGuardDependency,
    file: Annotated[UploadFile, File(description="UTF-8 CSV, comma or semicolon separated.")],
    tag: Annotated[str | None, Form(max_length=50)] = None,
) -> ContactImportResponse | JSONResponse:
    """Import contacts."""
    content = await file.read()

    async def run() -> ContactImportResponse:
        importer = ContactImporter(session, container.clock, container.task_queue)
        summary = await importer.import_csv(content, tag=tag or None)
        return ContactImportResponse.model_validate(summary)

    return await run_idempotent(idempotency, status.HTTP_200_OK, run)


@router.post(
    "",
    response_model=ContactResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a contact",
    description="Publishes `contact.created`; workflows with that trigger may enroll the contact.",
    responses=WRITE,
)
async def create_contact(
    payload: ContactCreateRequest,
    _principal: Writer,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> ContactResponse | JSONResponse:
    """Create a contact."""

    async def create() -> ContactResponse:
        return ContactResponse.model_validate(await service.create_contact(payload))

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, create)


@router.get(
    "/{contact_id}", response_model=ContactResponse, summary="Get a contact", responses=READ
)
async def read_contact(
    contact_id: uuid.UUID, _principal: Reader, service: Service
) -> ContactResponse:
    """Get a contact."""
    return ContactResponse.model_validate(await service.require_contact(contact_id))


@router.patch(
    "/{contact_id}", response_model=ContactResponse, summary="Update a contact", responses=WRITE
)
async def update_contact(
    contact_id: uuid.UUID, payload: ContactUpdateRequest, _principal: Writer, service: Service
) -> ContactResponse:
    """Update a contact."""
    return ContactResponse.model_validate(await service.update_contact(contact_id, payload))


@router.post(
    "/{contact_id}/tags",
    response_model=ContactResponse,
    summary="Add a tag",
    description="Publishes `contact.tag_added` when the tag is new.",
    responses=WRITE,
)
async def add_tag(
    contact_id: uuid.UUID,
    payload: TagRequest,
    _principal: Writer,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> ContactResponse | JSONResponse:
    """Add a tag."""

    async def add() -> ContactResponse:
        return ContactResponse.model_validate(await service.add_tag(contact_id, payload.tag))

    return await run_idempotent(idempotency, status.HTTP_200_OK, add)


@router.delete(
    "/{contact_id}/tags/{tag}",
    response_model=ContactResponse,
    summary="Remove a tag",
    responses=WRITE,
)
async def remove_tag(
    contact_id: uuid.UUID, tag: str, _principal: Writer, service: Service
) -> ContactResponse:
    """Remove a tag."""
    return ContactResponse.model_validate(await service.remove_tag(contact_id, tag))


@router.delete(
    "/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete and erase a contact",
    description="Removes the contact with its emails, enrollments, quote requests, deals, "
    "meetings, notes and every copy of their data (outbox, notifications, events). The address "
    "stays suppressed so a later import never emails them again. Admins only; cannot be undone.",
    responses=WRITE,
)
async def delete_contact(
    contact_id: uuid.UUID, principal: Admin, session: TenantSession, container: ContainerDependency
) -> Response:
    """Erase a contact."""
    await erase_contact(session, container.clock, contact_id, principal.subject)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{contact_id}/messages",
    response_model=list[MessageResponse],
    summary="Conversation with a contact",
    responses=READ,
)
async def list_contact_messages(
    contact_id: uuid.UUID, _principal: Reader, service: Service
) -> list[MessageResponse]:
    """List the contact's emails, oldest first."""
    await service.require_contact(contact_id)
    return [
        MessageResponse.model_validate(item)
        for item in await service.repository.messages_of(contact_id)
    ]
