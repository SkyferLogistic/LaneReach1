"""Review queue endpoints: AI output waiting for a person."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from pydantic import Field

from lanereach.ai.review.models import ReviewItem, ReviewKind, ReviewStatus
from lanereach.ai.review.service import ReviewService
from lanereach.auth.dependencies import IdempotencyGuardDependency, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.automation.engine.dependencies import EngineServicesDependency
from lanereach.core.idempotency import run_idempotent
from lanereach.core.openapi import problem_responses
from lanereach.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from lanereach.core.schemas import ResponseModel, StrictRequestModel

router = APIRouter(prefix="/ai/reviews", tags=["AI Review"])

Reviewer = Annotated[Principal, Depends(require_scopes(Scope.AI_REVIEW))]
_DECIDE = problem_responses(401, 403, 404, 409, 422)


class ReviewItemResponse(ResponseModel):
    """An item in the review queue."""

    id: uuid.UUID
    kind: ReviewKind
    status: ReviewStatus
    payload: dict[str, Any]
    reason: str | None
    contact_id: uuid.UUID | None
    message_id: uuid.UUID | None
    office_id: uuid.UUID | None
    enrollment_id: uuid.UUID | None
    ai_run_id: uuid.UUID | None
    reviewer: str | None
    decision_note: str | None
    decided_at: datetime | None
    sent_message_id: str | None
    created_at: datetime
    updated_at: datetime


class EditAndApproveRequest(StrictRequestModel):
    """The reviewer's corrected draft."""

    subject: str | None = Field(default=None, min_length=1, max_length=300)
    body: str | None = Field(default=None, min_length=1, max_length=20_000)


class RejectRequest(StrictRequestModel):
    """Why the item is rejected."""

    note: str | None = Field(default=None, max_length=2000)


def get_review_service(services: EngineServicesDependency) -> ReviewService:
    """Review service for the request."""
    return ReviewService(services)


Service = Annotated[ReviewService, Depends(get_review_service)]


async def _respond(operation: Awaitable[ReviewItem]) -> ReviewItemResponse:
    return ReviewItemResponse.model_validate(await operation)


@router.get(
    "",
    response_model=Page[ReviewItemResponse],
    summary="List review items",
    description="Newest first; filter by `status` (default: all) and `kind`.",
    responses=problem_responses(401, 403),
)
async def list_review_items(
    _principal: Reviewer,
    service: Service,
    status_filter: Annotated[ReviewStatus | None, Query(alias="status")] = None,
    kind: Annotated[ReviewKind | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[ReviewItemResponse]:
    """List review items."""
    rows, next_cursor = await service.repository.list_items(
        status=status_filter, kind=kind, limit=limit, cursor=cursor
    )
    return Page(
        items=[ReviewItemResponse.model_validate(row) for row in rows], next_cursor=next_cursor
    )


@router.get(
    "/{item_id}",
    response_model=ReviewItemResponse,
    summary="Get a review item",
    responses=problem_responses(401, 403, 404),
)
async def read_review_item(
    item_id: uuid.UUID, _principal: Reviewer, service: Service
) -> ReviewItemResponse:
    """Get a review item."""
    return await _respond(service.require_item(item_id))


@router.post(
    "/{item_id}/approve",
    response_model=ReviewItemResponse,
    summary="Approve (a reply draft is sent in the original thread)",
    description="The reply still passes every send guardrail: a blocked reply returns 422, a "
    "deferred one (kill switch, sending window, mailbox capacity) returns 409 and the item "
    "stays pending.",
    responses=_DECIDE,
)
async def approve_review_item(
    item_id: uuid.UUID,
    principal: Reviewer,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> ReviewItemResponse | JSONResponse:
    """Approve an item."""
    return await run_idempotent(
        idempotency, status.HTTP_200_OK, lambda: _respond(service.approve(item_id, principal))
    )


@router.post(
    "/{item_id}/edit-and-approve",
    response_model=ReviewItemResponse,
    summary="Correct the draft, then approve it",
    description="The original draft is kept in `payload.original` for evaluation.",
    responses=_DECIDE,
)
async def edit_and_approve_review_item(
    item_id: uuid.UUID,
    payload: EditAndApproveRequest,
    principal: Reviewer,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> ReviewItemResponse | JSONResponse:
    """Edit and approve an item."""
    return await run_idempotent(
        idempotency,
        status.HTTP_200_OK,
        lambda: _respond(
            service.approve(item_id, principal, subject=payload.subject, body=payload.body)
        ),
    )


@router.post(
    "/{item_id}/reject",
    response_model=ReviewItemResponse,
    summary="Reject (nothing is sent)",
    responses=_DECIDE,
)
async def reject_review_item(
    item_id: uuid.UUID,
    payload: RejectRequest,
    principal: Reviewer,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> ReviewItemResponse | JSONResponse:
    """Reject an item."""
    return await run_idempotent(
        idempotency,
        status.HTTP_200_OK,
        lambda: _respond(service.reject(item_id, principal, payload.note)),
    )
