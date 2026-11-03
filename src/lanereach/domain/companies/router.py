"""Company endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse, Response

from lanereach.auth.dependencies import IdempotencyGuardDependency, TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ClockDependency
from lanereach.core.idempotency import run_idempotent
from lanereach.core.openapi import READ, WRITE
from lanereach.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from lanereach.domain.companies.schemas import (
    BranchCreateRequest,
    BranchResponse,
    CompanyCreateRequest,
    CompanyResponse,
    CompanyUpdateRequest,
)
from lanereach.domain.companies.service import CompanyService

router = APIRouter(prefix="/companies", tags=["Companies & contacts"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Writer = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_WRITE))]


def get_company_service(session: TenantSession, clock: ClockDependency) -> CompanyService:
    """Company service for the request."""
    return CompanyService(session, clock)


Service = Annotated[CompanyService, Depends(get_company_service)]


@router.get("", response_model=Page[CompanyResponse], summary="List companies", responses=READ)
async def list_companies(
    _principal: Reader,
    service: Service,
    search: Annotated[str | None, Query(alias="q", max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[CompanyResponse]:
    """List companies newest first."""
    companies, next_cursor = await service.repository.search(search, limit=limit, cursor=cursor)
    return Page(items=await service.describe(companies), next_cursor=next_cursor)


@router.post(
    "",
    response_model=CompanyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a company",
    responses=WRITE,
)
async def create_company(
    payload: CompanyCreateRequest,
    _principal: Writer,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> CompanyResponse | JSONResponse:
    """Create a company with branches."""

    async def create() -> CompanyResponse:
        [described] = await service.describe([await service.create_company(payload)])
        return described

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, create)


@router.get(
    "/{company_id}", response_model=CompanyResponse, summary="Get a company", responses=READ
)
async def read_company(
    company_id: uuid.UUID, _principal: Reader, service: Service
) -> CompanyResponse:
    """Get a company."""
    [described] = await service.describe([await service.require_company(company_id)])
    return described


@router.patch(
    "/{company_id}",
    response_model=CompanyResponse,
    summary="Update a company (including freeze/unfreeze)",
    responses=WRITE,
)
async def update_company(
    company_id: uuid.UUID, payload: CompanyUpdateRequest, _principal: Writer, service: Service
) -> CompanyResponse:
    """Update a company."""
    [described] = await service.describe([await service.update_company(company_id, payload)])
    return described


@router.post(
    "/{company_id}/branches",
    response_model=BranchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a branch",
    responses=WRITE,
)
async def add_branch(
    company_id: uuid.UUID,
    payload: BranchCreateRequest,
    _principal: Writer,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> BranchResponse | JSONResponse:
    """Add a branch."""

    async def create() -> BranchResponse:
        return BranchResponse.model_validate(await service.add_branch(company_id, payload))

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, create)


@router.delete(
    "/{company_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a company",
    description="Refused (409) while contacts belong to it.",
    responses=WRITE,
)
async def delete_company(company_id: uuid.UUID, _principal: Writer, service: Service) -> Response:
    """Delete a company without contacts."""
    await service.delete_company(company_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
