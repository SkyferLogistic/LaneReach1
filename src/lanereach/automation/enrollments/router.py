"""Enrollment endpoints."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse, Response
from pydantic import Field

from lanereach.auth.dependencies import IdempotencyGuardDependency, TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.automation.engine.dependencies import (
    EngineRunnerDependency,
    EngineServicesDependency,
    NonProduction,
)
from lanereach.automation.engine.executor import EnrollmentExecutor
from lanereach.automation.engine.runner import DispatchSummary
from lanereach.automation.enrollments.models import Enrollment, EnrollmentStatus, EnrollmentStep
from lanereach.automation.enrollments.service import EnrollmentService
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.idempotency import run_idempotent
from lanereach.core.openapi import READ, WRITE
from lanereach.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from lanereach.core.schemas import ResponseModel, StrictRequestModel

router = APIRouter(prefix="/automation", tags=["Enrollments"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Writer = Annotated[Principal, Depends(require_scopes(Scope.ENROLLMENTS_WRITE))]
Admin = Annotated[Principal, Depends(require_scopes(Scope.ADMIN_MANAGE))]


class EnrollmentCreateRequest(StrictRequestModel):
    """Contacts to enroll."""

    contact_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class EnrollmentAttemptResponse(ResponseModel):
    """Outcome for one contact."""

    contact_id: uuid.UUID
    enrolled: bool
    enrollment_id: uuid.UUID | None
    skipped_reason: str | None


class EnrollmentResponse(ResponseModel):
    """An enrollment."""

    id: uuid.UUID
    workflow_id: uuid.UUID
    workflow_version: int
    contact_id: uuid.UUID
    status: EnrollmentStatus
    current_node_id: str
    resume_handle: str | None
    next_run_at: datetime | None
    variables: dict[str, Any]
    stop_reason: str | None
    attempts: int
    last_error: str | None
    step_count: int
    enrolled_by: str
    trigger_event_id: uuid.UUID | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EnrollmentStepResponse(ResponseModel):
    """One executed node, with every guardrail decision."""

    id: uuid.UUID
    step_index: int
    node_id: str
    node_type: str
    started_at: datetime
    finished_at: datetime
    result: str
    handle_taken: str | None
    detail: str | None
    wait_until: datetime | None
    guardrail_decisions: list[dict[str, Any]]
    output: dict[str, Any] | None
    error: str | None


class CancelRequest(StrictRequestModel):
    """Why the enrollment is cancelled."""

    reason: str = Field(default="cancelled by user", max_length=500)


class AdvanceResponse(ResponseModel):
    """Result of running one tick now."""

    advanced: bool
    steps_executed: int
    reason: str | None
    enrollment: EnrollmentResponse


class DispatchResponse(ResponseModel):
    """What the dispatcher found."""

    tenants: int
    events: int
    enrollments: int
    scheduled_enrollments: int
    inbound_messages: int
    errors: list[str]


def get_enrollment_service(
    session: TenantSession, container: ContainerDependency
) -> EnrollmentService:
    """Enrollment service for the request."""
    return EnrollmentService(session, container.clock, container.task_queue)


Service = Annotated[EnrollmentService, Depends(get_enrollment_service)]


@router.post(
    "/workflows/{workflow_id}/enrollments",
    response_model=list[EnrollmentAttemptResponse],
    summary="Enroll contacts in a published workflow",
    description="Contacts already enrolled (open enrollment) are skipped.",
    responses=WRITE,
)
async def enroll_contacts(
    workflow_id: uuid.UUID,
    payload: EnrollmentCreateRequest,
    principal: Writer,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> list[EnrollmentAttemptResponse] | JSONResponse:
    """Enroll contacts."""

    async def enroll() -> list[EnrollmentAttemptResponse]:
        attempts = await service.enroll_contacts(
            workflow_id, payload.contact_ids, principal.subject
        )
        return [
            EnrollmentAttemptResponse(
                contact_id=attempt.contact_id,
                enrolled=attempt.enrollment is not None,
                enrollment_id=attempt.enrollment.id if attempt.enrollment else None,
                skipped_reason=attempt.skipped.value if attempt.skipped else None,
            )
            for attempt in attempts
        ]

    return await run_idempotent(idempotency, status.HTTP_200_OK, enroll)


@router.get(
    "/enrollments",
    response_model=Page[EnrollmentResponse],
    summary="List enrollments",
    responses=READ,
)
async def list_enrollments(
    _principal: Reader,
    service: Service,
    workflow_id: Annotated[uuid.UUID | None, Query()] = None,
    contact_id: Annotated[uuid.UUID | None, Query()] = None,
    status_filter: Annotated[EnrollmentStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[EnrollmentResponse]:
    """List enrollments newest first."""
    rows, next_cursor = await service.repository.list_enrollments(
        workflow_id=workflow_id,
        contact_id=contact_id,
        status=status_filter,
        limit=limit,
        cursor=cursor,
    )
    return Page(
        items=[EnrollmentResponse.model_validate(row) for row in rows], next_cursor=next_cursor
    )


@router.get(
    "/enrollments/{enrollment_id}",
    response_model=EnrollmentResponse,
    summary="Get an enrollment",
    responses=READ,
)
async def read_enrollment(
    enrollment_id: uuid.UUID, _principal: Reader, service: Service
) -> EnrollmentResponse:
    """Get an enrollment."""
    return EnrollmentResponse.model_validate(await service.require_enrollment(enrollment_id))


@router.get(
    "/enrollments/{enrollment_id}/steps",
    response_model=list[EnrollmentStepResponse],
    summary="Steps of an enrollment, with guardrail decisions",
    responses=READ,
)
async def list_enrollment_steps(
    enrollment_id: uuid.UUID, _principal: Reader, service: Service
) -> list[EnrollmentStepResponse]:
    """List steps in order."""
    await service.require_enrollment(enrollment_id)
    steps: list[EnrollmentStep] = list(await service.repository.list_steps(enrollment_id))
    return [EnrollmentStepResponse.model_validate(step) for step in steps]


@router.post(
    "/enrollments/{enrollment_id}/pause",
    response_model=EnrollmentResponse,
    summary="Pause",
    responses=WRITE,
)
async def pause_enrollment(
    enrollment_id: uuid.UUID,
    _principal: Writer,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> EnrollmentResponse | JSONResponse:
    """Pause an enrollment."""
    return await run_idempotent(
        idempotency, 200, lambda: _respond_after(service.pause_enrollment(enrollment_id))
    )


@router.post(
    "/enrollments/{enrollment_id}/resume",
    response_model=EnrollmentResponse,
    summary="Resume",
    responses=WRITE,
)
async def resume_enrollment(
    enrollment_id: uuid.UUID,
    _principal: Writer,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> EnrollmentResponse | JSONResponse:
    """Resume an enrollment."""
    return await run_idempotent(
        idempotency, 200, lambda: _respond_after(service.resume_enrollment(enrollment_id))
    )


@router.post(
    "/enrollments/{enrollment_id}/cancel",
    response_model=EnrollmentResponse,
    summary="Cancel",
    responses=WRITE,
)
async def cancel_enrollment(
    enrollment_id: uuid.UUID,
    _principal: Writer,
    service: Service,
    idempotency: IdempotencyGuardDependency,
    payload: CancelRequest | None = None,
) -> EnrollmentResponse | JSONResponse:
    """Cancel an enrollment."""
    reason = payload.reason if payload else CancelRequest().reason
    return await run_idempotent(
        idempotency, 200, lambda: _respond_after(service.cancel_enrollment(enrollment_id, reason))
    )


@router.delete(
    "/enrollments/{enrollment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a finished enrollment",
    description="Refused (409) while it is open or when it sent email.",
    responses=WRITE,
)
async def delete_enrollment(
    enrollment_id: uuid.UUID, _principal: Admin, service: Service
) -> Response:
    """Delete an enrollment."""
    await service.delete_enrollment(enrollment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _respond_after(operation: Awaitable[Enrollment]) -> EnrollmentResponse:
    return EnrollmentResponse.model_validate(await operation)


@router.post(
    "/enrollments/{enrollment_id}/advance",
    response_model=AdvanceResponse,
    summary="Run the enrollment now (if due)",
    description="Runs one executor tick immediately instead of waiting for the worker. Waits and "
    "guardrails are respected: a not-yet-due enrollment is left alone.",
    responses=WRITE,
)
async def advance_enrollment_now(
    enrollment_id: uuid.UUID,
    _principal: Writer,
    service: Service,
    services: EngineServicesDependency,
    idempotency: IdempotencyGuardDependency,
) -> AdvanceResponse | JSONResponse:
    """Advance now."""

    async def advance() -> AdvanceResponse:
        await service.require_enrollment(enrollment_id)
        outcome = await EnrollmentExecutor(services).advance_enrollment(enrollment_id)
        enrollment = await service.require_enrollment(enrollment_id)
        return AdvanceResponse(
            advanced=outcome.advanced,
            steps_executed=outcome.steps_executed,
            reason=outcome.reason,
            enrollment=EnrollmentResponse.model_validate(enrollment),
        )

    return await run_idempotent(idempotency, status.HTTP_200_OK, advance)


@router.post(
    "/enrollments/{enrollment_id}/wake",
    response_model=EnrollmentResponse,
    summary="Skip the remaining wait (dev/staging only)",
    description="Testing aid: makes a waiting enrollment due now. Guardrails still apply to the "
    "next email, so a sending-window deferral will defer again.",
    dependencies=[NonProduction],
    responses=WRITE,
)
async def wake_enrollment(
    enrollment_id: uuid.UUID, _principal: Writer, service: Service
) -> EnrollmentResponse:
    """Wake an enrollment."""
    return EnrollmentResponse.model_validate(await service.wake_enrollment(enrollment_id))


@router.post(
    "/dispatch",
    response_model=DispatchResponse,
    summary="Run the dispatcher now (dev/staging only)",
    description="What Celery beat does every 30 seconds, for the caller's tenant only: hand out "
    "pending events and due enrollments, and fire scheduled triggers.",
    dependencies=[NonProduction],
    responses=WRITE,
)
async def dispatch_now(principal: Admin, runner: EngineRunnerDependency) -> DispatchResponse:
    """Dispatch due work of the caller's tenant."""
    summary: DispatchSummary = await runner.dispatch_due_work(only_tenant_id=principal.tenant_id)
    return DispatchResponse(
        tenants=summary.tenants,
        events=summary.events,
        enrollments=summary.enrollments,
        scheduled_enrollments=summary.scheduled_enrollments,
        inbound_messages=summary.inbound_messages,
        errors=summary.errors,
    )
