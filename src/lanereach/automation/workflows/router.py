"""Workflow endpoints: CRUD, versions, validation, publishing and pausing."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.ai.llm.factory import build_llm_client
from lanereach.ai.llm.providers.mock import MockLLMProvider
from lanereach.auth.dependencies import IdempotencyGuardDependency, TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.automation.engine.services import EngineServices, build_engine_services
from lanereach.automation.workflows.graph import WorkflowGraph
from lanereach.automation.workflows.models import Workflow, WorkflowStatus, WorkflowVersion
from lanereach.automation.workflows.schedule import CampaignSchedule, load_schedule
from lanereach.automation.workflows.schemas import (
    CampaignStatsResponse,
    GraphValidationRequest,
    TestRunRequest,
    TestRunResponse,
    WorkflowCreateRequest,
    WorkflowPublishRequest,
    WorkflowResponse,
    WorkflowSummaryResponse,
    WorkflowUpdateRequest,
    WorkflowVersionResponse,
    WorkflowVersionSummary,
)
from lanereach.automation.workflows.service import WorkflowService
from lanereach.automation.workflows.stats import build_campaign_stats
from lanereach.automation.workflows.validator import ValidationReport
from lanereach.core.clock import ManualClock
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import ProblemDocument
from lanereach.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page

router = APIRouter(prefix="/automation/workflows", tags=["Automation"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Writer = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_WRITE))]
Publisher = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_PUBLISH))]

_PROBLEM: dict[str, Any] = {"model": ProblemDocument}
_READ_ERRORS: dict[int | str, dict[str, Any]] = {401: _PROBLEM, 403: _PROBLEM, 404: _PROBLEM}
_WRITE_ERRORS: dict[int | str, dict[str, Any]] = {**_READ_ERRORS, 409: _PROBLEM, 422: _PROBLEM}


def get_workflow_service(session: TenantSession, container: ContainerDependency) -> WorkflowService:
    """Build the workflow service for this request.

    Args:
        session: The request's tenant session.
        container: The service container.

    Returns:
        The service.
    """
    return WorkflowService(
        session,
        container.clock,
        container.node_registry,
        max_cold_emails=container.settings.max_cold_emails_per_sequence,
    )


Service = Annotated[WorkflowService, Depends(get_workflow_service)]


def build_summary(workflow: Workflow) -> WorkflowSummaryResponse:
    """Render a workflow for lists.

    Args:
        workflow: The workflow row.

    Returns:
        The summary.
    """
    return WorkflowSummaryResponse(
        id=workflow.id,
        name=workflow.name,
        description=workflow.description,
        status=workflow.status,
        current_version=workflow.current_version,
        latest_version=workflow.latest_version,
        has_unpublished_changes=workflow.current_version != workflow.latest_version,
        is_paused=workflow.paused_at is not None,
        paused_at=workflow.paused_at,
        schedule=load_schedule(workflow.schedule),
        created_by=workflow.created_by,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


def build_version(workflow: Workflow, version: WorkflowVersion) -> WorkflowVersionResponse:
    """Render a version including its graph.

    Args:
        workflow: The version's workflow.
        version: The version row.

    Returns:
        The version response.
    """
    return WorkflowVersionResponse(
        **_version_fields(workflow, version), graph=WorkflowGraph.model_validate(version.graph)
    )


def build_workflow(workflow: Workflow, version: WorkflowVersion) -> WorkflowResponse:
    """Render a workflow with a version.

    Args:
        workflow: The workflow row.
        version: Usually the latest version.

    Returns:
        The workflow response.
    """
    return WorkflowResponse(
        **build_summary(workflow).model_dump(), latest=build_version(workflow, version)
    )


def _version_fields(workflow: Workflow, version: WorkflowVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "workflow_id": version.workflow_id,
        "version": version.version,
        "created_by": version.created_by,
        "created_at": version.created_at,
        "published_at": version.published_at,
        "is_current": version.version == workflow.current_version,
    }


@router.post(
    "",
    response_model=WorkflowResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workflow",
    description="Creates a draft with version 1. The graph may be incomplete; it is fully "
    "validated when publishing.",
    responses=_WRITE_ERRORS,
)
async def create_workflow(
    payload: WorkflowCreateRequest,
    principal: Writer,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> WorkflowResponse | JSONResponse:
    """Create a workflow."""
    if (replay := await idempotency.claim()) is not None:
        return replay
    created = await service.create_workflow(
        principal, name=payload.name, description=payload.description, graph=payload.graph
    )
    response = build_workflow(created.workflow, created.version)
    await idempotency.complete(status.HTTP_201_CREATED, response)
    return response


@router.get(
    "",
    response_model=Page[WorkflowSummaryResponse],
    summary="List workflows",
    responses=_READ_ERRORS,
)
async def list_workflows(
    _principal: Reader,
    service: Service,
    status_filter: Annotated[WorkflowStatus | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(alias="q", max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[WorkflowSummaryResponse]:
    """List workflows newest first."""
    workflows, next_cursor = await service.list_workflows(
        status=status_filter, search=search, limit=limit, cursor=cursor
    )
    return Page(items=[build_summary(workflow) for workflow in workflows], next_cursor=next_cursor)


@router.post(
    "/validate-graph",
    response_model=ValidationReport,
    summary="Validate a graph without saving it",
    description="Used by the canvas for live feedback. Nothing is stored.",
    responses={401: _PROBLEM, 403: _PROBLEM, 422: _PROBLEM},
)
async def validate_unsaved_graph(
    payload: GraphValidationRequest, _principal: Writer, service: Service
) -> ValidationReport:
    """Validate a graph."""
    return await service.validate_graph(payload.graph)


@router.get(
    "/{workflow_id}",
    response_model=WorkflowResponse,
    summary="Get a workflow with its latest version",
    responses=_READ_ERRORS,
)
async def read_workflow(
    workflow_id: uuid.UUID, _principal: Reader, service: Service
) -> WorkflowResponse:
    """Get a workflow."""
    loaded = await service.get_workflow(workflow_id)
    return build_workflow(loaded.workflow, loaded.version)


@router.put(
    "/{workflow_id}",
    response_model=WorkflowResponse,
    summary="Save a new draft version",
    description="Every save creates a new immutable version. The published version (what new "
    "enrollments use) only changes when you publish. Pass `base_version` to detect "
    "concurrent edits.",
    responses=_WRITE_ERRORS,
)
async def update_workflow(
    workflow_id: uuid.UUID,
    payload: WorkflowUpdateRequest,
    principal: Writer,
    service: Service,
) -> WorkflowResponse:
    """Save a new version."""
    updated = await service.update_workflow(
        principal,
        workflow_id,
        graph=payload.graph,
        name=payload.name,
        description=payload.description,
        base_version=payload.base_version,
    )
    return build_workflow(updated.workflow, updated.version)


@router.post(
    "/{workflow_id}/validate",
    response_model=ValidationReport,
    summary="Validate a stored version",
    description="Validates the latest version unless `version` is given. Errors name node IDs.",
    responses=_READ_ERRORS,
)
async def validate_workflow(
    workflow_id: uuid.UUID,
    _principal: Writer,
    service: Service,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> ValidationReport:
    """Validate a version."""
    return await service.validate_workflow(workflow_id, version)


@router.post(
    "/{workflow_id}/publish",
    response_model=WorkflowSummaryResponse,
    summary="Publish a version",
    description="Refused with 422 (`workflow-invalid`) when the version has validation errors; "
    "`errors[]` lists them with node IDs in `location`.",
    responses=_WRITE_ERRORS,
)
async def publish_workflow(
    workflow_id: uuid.UUID,
    _principal: Publisher,
    service: Service,
    idempotency: IdempotencyGuardDependency,
    payload: Annotated[WorkflowPublishRequest | None, Body()] = None,
) -> WorkflowSummaryResponse | JSONResponse:
    """Publish a version."""
    if (replay := await idempotency.claim()) is not None:
        return replay
    workflow = await service.publish_workflow(workflow_id, payload.version if payload else None)
    response = build_summary(workflow)
    await idempotency.complete(status.HTTP_200_OK, response)
    return response


@router.post(
    "/{workflow_id}/pause",
    response_model=WorkflowSummaryResponse,
    summary="Pause a published workflow",
    description="No new enrollments start and running enrollments stop advancing until resumed.",
    responses=_WRITE_ERRORS,
)
async def pause_workflow(
    workflow_id: uuid.UUID,
    _principal: Publisher,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> WorkflowSummaryResponse | JSONResponse:
    """Pause a workflow (idempotent: pausing a paused workflow is a no-op)."""
    if (replay := await idempotency.claim()) is not None:
        return replay
    response = build_summary(await service.pause_workflow(workflow_id))
    await idempotency.complete(status.HTTP_200_OK, response)
    return response


@router.post(
    "/{workflow_id}/resume",
    response_model=WorkflowSummaryResponse,
    summary="Resume a paused workflow",
    responses=_WRITE_ERRORS,
)
async def resume_workflow(
    workflow_id: uuid.UUID,
    _principal: Publisher,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> WorkflowSummaryResponse | JSONResponse:
    """Resume a workflow (idempotent)."""
    if (replay := await idempotency.claim()) is not None:
        return replay
    response = build_summary(await service.resume_workflow(workflow_id))
    await idempotency.complete(status.HTTP_200_OK, response)
    return response


@router.post(
    "/{workflow_id}/archive",
    response_model=WorkflowSummaryResponse,
    summary="Archive a workflow",
    description="Archived workflows are read-only and accept no new enrollments.",
    responses=_WRITE_ERRORS,
)
async def archive_workflow(
    workflow_id: uuid.UUID,
    _principal: Publisher,
    service: Service,
    idempotency: IdempotencyGuardDependency,
) -> WorkflowSummaryResponse | JSONResponse:
    """Archive a workflow (idempotent)."""
    if (replay := await idempotency.claim()) is not None:
        return replay
    response = build_summary(await service.archive_workflow(workflow_id))
    await idempotency.complete(status.HTTP_200_OK, response)
    return response


@router.delete(
    "/{workflow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a workflow",
    description="Deletes the workflow, its versions and its finished enrollments. Refused (409) "
    "while enrollments are running or another workflow enrolls into it: archive it first.",
    responses=_WRITE_ERRORS,
)
async def delete_workflow(
    workflow_id: uuid.UUID, _principal: Publisher, service: Service
) -> Response:
    """Delete a workflow."""
    await service.delete_workflow(workflow_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/{workflow_id}/schedule",
    response_model=WorkflowSummaryResponse,
    summary="Set or clear the campaign schedule",
    description="Emails of this workflow go out only from `start_date` to `end_date`, on "
    "`weekdays` (recipient local days), inside the tenant's sending window. After the end "
    "date its emails are blocked and the enrollments stop. Send `null` (or nothing) to clear "
    "it. Replies "
    "to contacts who answered are not held.",
    responses=_WRITE_ERRORS,
)
async def set_workflow_schedule(
    workflow_id: uuid.UUID,
    _principal: Publisher,
    service: Service,
    payload: Annotated[CampaignSchedule | None, Body()] = None,
) -> WorkflowSummaryResponse:
    """Change when the campaign sends."""
    return build_summary(await service.set_workflow_schedule(workflow_id, payload))


@router.get(
    "/{workflow_id}/stats",
    response_model=CampaignStatsResponse,
    summary="Campaign results",
    description="Enrollments by status, emails sent, unique human opens and clicks (tracked "
    "HTML emails only), contacts who replied (and positively), bounces, unsubscribes, quote "
    "requests, deals and wins, per-step counts with the branch taken, and the top links.",
    responses=_READ_ERRORS,
)
async def read_workflow_stats(
    workflow_id: uuid.UUID, _principal: Reader, service: Service, session: TenantSession
) -> CampaignStatsResponse:
    """Count the campaign's funnel."""
    await service.get_workflow(workflow_id)
    stats = await build_campaign_stats(session, workflow_id)
    return CampaignStatsResponse.model_validate(stats)


@router.post(
    "/{workflow_id}/test-run",
    response_model=TestRunResponse,
    summary="Simulate a contact through the workflow",
    description="Runs the real engine with a simulated clock, the dry-run provider and the mock "
    "LLM inside a transaction that is rolled back: returns the step-by-step path, guardrail "
    "decisions and the emails that would be sent, and persists nothing. Drafts can be tested.",
    responses=_WRITE_ERRORS,
)
async def simulate_workflow(
    workflow_id: uuid.UUID,
    payload: TestRunRequest,
    _principal: Writer,
    service: Service,
    container: ContainerDependency,
) -> TestRunResponse:
    """Simulate a run."""
    variables: dict[str, Any] = dict(payload.variables)
    if payload.reply is not None:
        variables["reply"] = payload.reply.model_dump(mode="json")
    if payload.lead is not None:
        variables["lead"] = payload.lead

    # Test-runs always use the deterministic mock LLM: a simulation must be free, repeatable
    # and must never send contact data to an external model.
    simulation_llm = build_llm_client(container.settings, container.clock, MockLLMProvider())

    def build_services(session: AsyncSession, clock: ManualClock) -> EngineServices:
        return build_engine_services(
            session=session,
            clock=clock,
            settings=container.settings,
            registry=container.node_registry,
            task_queue=None,
            llm=simulation_llm,
        )

    result = await service.test_run_workflow(
        workflow_id,
        contact_id=payload.contact_id,
        version=payload.version,
        start_at=payload.start_at or container.clock.now(),
        variables=variables,
        build_services=build_services,
    )
    return TestRunResponse.model_validate(result, from_attributes=True)


@router.get(
    "/{workflow_id}/versions",
    response_model=list[WorkflowVersionSummary],
    summary="List versions",
    responses=_READ_ERRORS,
)
async def list_workflow_versions(
    workflow_id: uuid.UUID, _principal: Reader, service: Service
) -> list[WorkflowVersionSummary]:
    """List versions newest first."""
    workflow, versions = await service.list_versions(workflow_id)
    return [WorkflowVersionSummary(**_version_fields(workflow, item)) for item in versions]


@router.get(
    "/{workflow_id}/versions/{version}",
    response_model=WorkflowVersionResponse,
    summary="Get one version with its graph",
    responses=_READ_ERRORS,
)
async def read_workflow_version(
    workflow_id: uuid.UUID, version: int, _principal: Reader, service: Service
) -> WorkflowVersionResponse:
    """Get a version."""
    loaded = await service.get_workflow_version(workflow_id, version)
    return build_version(loaded.workflow, loaded.version)
