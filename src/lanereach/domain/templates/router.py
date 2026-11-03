"""Template endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from pydantic import Field, computed_field

from lanereach.auth.dependencies import IdempotencyGuardDependency, TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ClockDependency
from lanereach.core.idempotency import run_idempotent
from lanereach.core.languages import LANGUAGE_VARIANT_PATTERN
from lanereach.core.openapi import READ, WRITE
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.domain.senders.models import ServiceLine
from lanereach.domain.templates.models import TemplateSource, TemplateStatus
from lanereach.domain.templates.rendering import find_placeholders
from lanereach.domain.templates.service import TemplateService

router = APIRouter(prefix="/templates", tags=["Templates"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Writer = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_WRITE))]


class TemplateCreateRequest(StrictRequestModel):
    """One language version of a template."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$", examples=["intro"])
    language: str = Field(
        pattern=LANGUAGE_VARIANT_PATTERN,
        examples=["en", "pt-br"],
        description="ISO 639-1 code, optionally with a variant (`pt-br`, `zh-hant`, `en-na`).",
    )
    name: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=300, examples=["Shipping to {{company.name}}"])
    body: str = Field(
        min_length=1,
        max_length=20_000,
        examples=["Hello {{contact.first_name|there}},\n\n...\n\n{{sender.name}}"],
    )
    service_line: ServiceLine = ServiceLine.ANY


class TemplateUpdateRequest(StrictRequestModel):
    """Changes to a template version."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    subject: str | None = Field(default=None, min_length=1, max_length=300)
    body: str | None = Field(default=None, min_length=1, max_length=20_000)


class TemplateResponse(ResponseModel):
    """A template version."""

    id: uuid.UUID
    key: str
    language: str
    name: str
    subject: str
    body: str
    html_body: str | None
    service_line: ServiceLine
    status: TemplateStatus
    source: TemplateSource
    source_path: str | None
    source_web_url: str | None
    pdf_web_url: str | None
    synced_at: datetime | None
    sync_error: str | None
    import_warnings: list[str]
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def placeholders(self) -> list[str]:
        """Placeholders used in the subject and body."""
        return list(dict.fromkeys(find_placeholders(self.subject) + find_placeholders(self.body)))


def get_template_service(session: TenantSession, clock: ClockDependency) -> TemplateService:
    """Template service for the request."""
    return TemplateService(session, clock)


Service = Annotated[TemplateService, Depends(get_template_service)]


@router.get("", response_model=list[TemplateResponse], summary="List templates", responses=READ)
async def list_templates(
    _principal: Reader, service: Service, key: Annotated[str | None, Query()] = None
) -> list[TemplateResponse]:
    """List template versions."""
    return [
        TemplateResponse.model_validate(item) for item in await service.repository.list_by_key(key)
    ]


@router.post(
    "",
    response_model=TemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a template version",
    responses=WRITE,
)
async def create_template(
    payload: TemplateCreateRequest,
    _principal: Writer,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> TemplateResponse | JSONResponse:
    """Create a template version."""

    async def create() -> TemplateResponse:
        return TemplateResponse.model_validate(
            await service.create_template(**payload.model_dump())
        )

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, create)


@router.get(
    "/{template_id}", response_model=TemplateResponse, summary="Get a template", responses=READ
)
async def read_template(
    template_id: uuid.UUID, _principal: Reader, service: Service
) -> TemplateResponse:
    """Get a template version."""
    return TemplateResponse.model_validate(await service.require_template(template_id))


@router.put(
    "/{template_id}", response_model=TemplateResponse, summary="Update a template", responses=WRITE
)
async def update_template(
    template_id: uuid.UUID, payload: TemplateUpdateRequest, _principal: Writer, service: Service
) -> TemplateResponse:
    """Update a template version."""
    return TemplateResponse.model_validate(
        await service.update_template(template_id, payload.model_dump(exclude_unset=True))
    )


@router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a template version",
    responses=WRITE,
)
async def delete_template(template_id: uuid.UUID, _principal: Writer, service: Service) -> None:
    """Delete a template version."""
    await service.delete_template(template_id)
