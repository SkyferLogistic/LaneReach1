"""Office and sender endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from fastapi.responses import JSONResponse, Response

from lanereach.auth.dependencies import IdempotencyGuardDependency, TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ClockDependency
from lanereach.core.idempotency import run_idempotent
from lanereach.core.openapi import READ, WRITE
from lanereach.domain.offices.schemas import (
    OfficeCreateRequest,
    OfficeResponse,
    OfficeUpdateRequest,
    SenderCreateRequest,
    SenderResponse,
    SenderUpdateRequest,
)
from lanereach.domain.offices.service import OfficeService
from lanereach.integrations.mailboxes.service import mailbox_statuses

router = APIRouter(tags=["Offices & senders"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Admin = Annotated[Principal, Depends(require_scopes(Scope.ADMIN_MANAGE))]


def get_office_service(session: TenantSession, clock: ClockDependency) -> OfficeService:
    """Office service for the request."""
    return OfficeService(session, clock)


Service = Annotated[OfficeService, Depends(get_office_service)]


@router.get("/offices", response_model=list[OfficeResponse], summary="List offices", responses=READ)
async def list_offices(_principal: Reader, service: Service) -> list[OfficeResponse]:
    """List offices by name."""
    return [OfficeResponse.model_validate(office) for office in await service.offices.list_all()]


@router.post(
    "/offices",
    response_model=OfficeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an office",
    responses=WRITE,
)
async def create_office(
    payload: OfficeCreateRequest,
    _principal: Admin,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> OfficeResponse | JSONResponse:
    """Create an office."""

    async def create() -> OfficeResponse:
        return OfficeResponse.model_validate(await service.create_office(payload))

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, create)


@router.get(
    "/offices/{office_id}", response_model=OfficeResponse, summary="Get an office", responses=READ
)
async def read_office(office_id: uuid.UUID, _principal: Reader, service: Service) -> OfficeResponse:
    """Get an office."""
    return OfficeResponse.model_validate(await service.require_office(office_id))


@router.patch(
    "/offices/{office_id}",
    response_model=OfficeResponse,
    summary="Update an office (including its sending kill switch)",
    responses=WRITE,
)
async def update_office(
    office_id: uuid.UUID, payload: OfficeUpdateRequest, _principal: Admin, service: Service
) -> OfficeResponse:
    """Update an office."""
    return OfficeResponse.model_validate(await service.update_office(office_id, payload))


@router.get(
    "/senders", response_model=list[SenderResponse], summary="List sender mailboxes", responses=READ
)
async def list_senders(
    _principal: Reader,
    service: Service,
    session: TenantSession,
    office_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[SenderResponse]:
    """List senders, optionally of one office, with their mailbox status."""
    senders = await service.senders.list_for(office_id)
    statuses = await mailbox_statuses(session, [sender.id for sender in senders])
    return [
        SenderResponse.model_validate(sender).model_copy(
            update={"mailbox_status": statuses.get(sender.id)}
        )
        for sender in senders
    ]


@router.post(
    "/senders",
    response_model=SenderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a sender mailbox",
    responses=WRITE,
)
async def create_sender(
    payload: SenderCreateRequest,
    _principal: Admin,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> SenderResponse | JSONResponse:
    """Add a sender."""

    async def create() -> SenderResponse:
        return SenderResponse.model_validate(await service.create_sender(payload))

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, create)


@router.patch(
    "/senders/{sender_id}",
    response_model=SenderResponse,
    summary="Update a sender mailbox",
    responses=WRITE,
)
async def update_sender(
    sender_id: uuid.UUID, payload: SenderUpdateRequest, _principal: Admin, service: Service
) -> SenderResponse:
    """Update a sender."""
    return SenderResponse.model_validate(await service.update_sender(sender_id, payload))


@router.delete(
    "/senders/{sender_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an agent that never sent email",
    description="An agent with email history cannot be deleted (409): switch it off instead.",
    responses=WRITE,
)
async def delete_sender(sender_id: uuid.UUID, _principal: Admin, service: Service) -> Response:
    """Delete a sender mailbox."""
    await service.delete_sender(sender_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/offices/{office_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an office",
    description="Refused (409) while it has agents, contacts, email history or workflows.",
    responses=WRITE,
)
async def delete_office(office_id: uuid.UUID, _principal: Admin, service: Service) -> Response:
    """Delete an office."""
    await service.delete_office(office_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


SignatureImage = Annotated[
    UploadFile, File(description="PNG, JPEG, GIF or WebP, up to 5 MB (about 600 px wide).")
]


@router.post(
    "/offices/{office_id}/signature-image",
    response_model=OfficeResponse,
    summary="Use a picture as the office's email signature",
    description="HTML emails end with the picture instead of the text signature; plain-text "
    "emails and AI reply drafts keep the text.",
    responses=WRITE,
)
async def upload_office_signature_image(
    office_id: uuid.UUID,
    file: SignatureImage,
    _principal: Admin,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> OfficeResponse | JSONResponse:
    """Upload the office's signature picture."""
    content = await file.read()

    async def upload() -> OfficeResponse:
        office = await service.set_office_signature_image(
            office_id, filename=file.filename or "signature.png", content=content
        )
        return OfficeResponse.model_validate(office)

    return await run_idempotent(idempotency, status.HTTP_200_OK, upload)


@router.delete(
    "/offices/{office_id}/signature-image",
    response_model=OfficeResponse,
    summary="Go back to the office's text signature",
    responses=WRITE,
)
async def remove_office_signature_image(
    office_id: uuid.UUID, _principal: Admin, service: Service
) -> OfficeResponse:
    """Remove the signature picture."""
    return OfficeResponse.model_validate(await service.clear_office_signature_image(office_id))


@router.post(
    "/senders/{sender_id}/signature-image",
    response_model=SenderResponse,
    summary="Use a picture as an agent's signature",
    description="Switches the agent's signature mode to `image`.",
    responses=WRITE,
)
async def upload_sender_signature_image(
    sender_id: uuid.UUID,
    file: SignatureImage,
    _principal: Admin,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> SenderResponse | JSONResponse:
    """Upload the agent's signature picture."""
    content = await file.read()

    async def upload() -> SenderResponse:
        sender = await service.set_sender_signature_image(
            sender_id, filename=file.filename or "signature.png", content=content
        )
        return SenderResponse.model_validate(sender)

    return await run_idempotent(idempotency, status.HTTP_200_OK, upload)
