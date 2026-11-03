"""CRM endpoints: pipelines, deals (board, moves, timeline), tasks and notes."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse, Response
from pydantic import EmailStr, Field

from lanereach.auth.dependencies import IdempotencyGuardDependency, TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ClockDependency, ContainerDependency
from lanereach.core.idempotency import run_idempotent
from lanereach.core.openapi import READ, WRITE
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.domain.crm.models import Deal, DealStatus, StageKind, TaskPriority
from lanereach.domain.crm.service import (
    DealService,
    NoteService,
    PipelineService,
    StageInput,
    TaskService,
    deal_labels,
    pipeline_summary,
)
from lanereach.domain.senders.models import ServiceLine

router = APIRouter(prefix="/crm", tags=["CRM"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Writer = Annotated[Principal, Depends(require_scopes(Scope.CRM_WRITE))]
Admin = Annotated[Principal, Depends(require_scopes(Scope.ADMIN_MANAGE))]

Money = Annotated[Decimal, Field(ge=0, le=Decimal("999999999999.99"), decimal_places=2)]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$", examples=["USD"])]


# --- schemas -----------------------------------------------------------------------------


class StageRequest(StrictRequestModel):
    """A stage of a pipeline (keep `id` to keep its deals)."""

    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=100)
    kind: StageKind = StageKind.OPEN
    probability: int = Field(default=0, ge=0, le=100)


class PipelineRequest(StrictRequestModel):
    """A pipeline and its stages, in board order."""

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "Ocean contracts",
                    "stages": [
                        {"name": "RFQ received", "kind": "open", "probability": 20},
                        {"name": "Tender submitted", "kind": "open", "probability": 50},
                        {"name": "Awarded", "kind": "won", "probability": 100},
                        {"name": "Not awarded", "kind": "lost"},
                    ],
                }
            ]
        }
    }

    name: str = Field(min_length=1, max_length=100)
    stages: list[StageRequest] = Field(min_length=2, max_length=12)


class PipelineStageResponse(ResponseModel):
    """A pipeline stage."""

    id: uuid.UUID
    name: str
    position: int
    kind: StageKind
    probability: int


class CrmPipelineResponse(ResponseModel):
    """A pipeline with its stages."""

    id: uuid.UUID
    name: str
    position: int
    is_default: bool
    stages: list[PipelineStageResponse]


class DealCreateRequest(StrictRequestModel):
    """A deal created by hand."""

    contact_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    pipeline_id: uuid.UUID | None = None
    stage_id: uuid.UUID | None = None
    value_amount: Money | None = None
    currency: Currency = "USD"
    service_line: ServiceLine = ServiceLine.ANY
    mode: str | None = Field(default=None, max_length=20)
    origin: str | None = Field(default=None, max_length=120)
    destination: str | None = Field(default=None, max_length=120)
    commodity: str | None = Field(default=None, max_length=200)
    incoterm: str | None = Field(default=None, pattern=r"^[A-Z]{3}$", examples=["FOB"])
    cargo_ready_date: date | None = None
    weight_kg: Decimal | None = Field(default=None, ge=0, le=Decimal("99999999999"))
    volume_cbm: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999"))
    pieces: int | None = Field(default=None, ge=0, le=1_000_000)
    equipment: str | None = Field(default=None, max_length=30, examples=["40HC"])
    owner_email: EmailStr | None = None
    expected_close_date: date | None = None


class DealUpdateRequest(StrictRequestModel):
    """Changes to a deal's details (use `/move` to change its stage)."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    value_amount: Money | None = None
    currency: Currency | None = None
    service_line: ServiceLine | None = None
    mode: str | None = Field(default=None, max_length=20)
    origin: str | None = Field(default=None, max_length=120)
    destination: str | None = Field(default=None, max_length=120)
    commodity: str | None = Field(default=None, max_length=200)
    incoterm: str | None = Field(default=None, pattern=r"^[A-Z]{3}$", examples=["FOB"])
    cargo_ready_date: date | None = None
    weight_kg: Decimal | None = Field(default=None, ge=0, le=Decimal("99999999999"))
    volume_cbm: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999"))
    pieces: int | None = Field(default=None, ge=0, le=1_000_000)
    equipment: str | None = Field(default=None, max_length=30, examples=["40HC"])
    owner_email: EmailStr | None = None
    expected_close_date: date | None = None
    lost_reason: str | None = Field(default=None, max_length=300)


class DealMoveRequest(StrictRequestModel):
    """Where a deal was dropped on the board."""

    stage_id: uuid.UUID
    index: int | None = Field(default=None, ge=0, description="Position in the column (0 = top).")
    lost_reason: str | None = Field(default=None, max_length=300)


class DealResponse(ResponseModel):
    """A deal card."""

    id: uuid.UUID
    pipeline_id: uuid.UUID
    stage_id: uuid.UUID
    contact_id: uuid.UUID
    company_id: uuid.UUID | None
    office_id: uuid.UUID | None
    lead_id: uuid.UUID | None
    title: str
    value_amount: float | None
    currency: str
    service_line: ServiceLine
    mode: str | None
    origin: str | None
    destination: str | None
    commodity: str | None
    incoterm: str | None = None
    cargo_ready_date: date | None = None
    weight_kg: float | None = None
    volume_cbm: float | None = None
    pieces: int | None = None
    equipment: str | None = None
    score: int | None = None
    score_reasons: list[dict[str, Any]] = Field(default_factory=list)
    scored_at: datetime | None = None
    owner_email: str | None
    expected_close_date: date | None
    status: DealStatus
    won_at: datetime | None
    lost_at: datetime | None
    lost_reason: str | None
    board_position: float
    stage_entered_at: datetime
    created_at: datetime
    updated_at: datetime
    contact_name: str = ""
    contact_email: str = ""
    company_name: str | None = None


class TimelineEntryResponse(ResponseModel):
    """One line of a deal's history."""

    kind: str
    occurred_at: datetime
    title: str
    detail: str | None
    actor: str | None
    ref_id: uuid.UUID | None


class TaskCreateRequest(StrictRequestModel):
    """A new task."""

    title: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=5000)
    due_at: datetime | None = None
    assignee_email: EmailStr | None = None
    priority: TaskPriority = TaskPriority.NORMAL
    contact_id: uuid.UUID | None = None
    deal_id: uuid.UUID | None = None


class TaskUpdateRequest(StrictRequestModel):
    """Changes to a task; `done` completes or reopens it."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=5000)
    due_at: datetime | None = None
    assignee_email: EmailStr | None = None
    priority: TaskPriority | None = None
    done: bool | None = None


class TaskResponse(ResponseModel):
    """A task."""

    id: uuid.UUID
    title: str
    notes: str
    due_at: datetime | None
    done_at: datetime | None
    assignee_email: str | None
    priority: TaskPriority
    contact_id: uuid.UUID | None
    deal_id: uuid.UUID | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class NoteCreateRequest(StrictRequestModel):
    """A new note."""

    body: str = Field(min_length=1, max_length=5000)
    contact_id: uuid.UUID | None = None
    deal_id: uuid.UUID | None = None


class NoteResponse(ResponseModel):
    """A note."""

    id: uuid.UUID
    body: str
    contact_id: uuid.UUID | None
    deal_id: uuid.UUID | None
    author: str
    created_at: datetime


class LineSummaryResponse(ResponseModel):
    """Open and won business for one service line."""

    service_line: str
    open_deals: int
    open_value: float
    won_in_period: int
    won_value_in_period: float


class CrmSummaryResponse(ResponseModel):
    """CRM numbers for the dashboard."""

    open_deals: int
    open_value: float
    weighted_value: float
    won_in_period: int
    won_value_in_period: float
    tasks_due_today: int
    tasks_overdue: int
    by_line: list[LineSummaryResponse] = Field(default_factory=list)


# --- dependencies ------------------------------------------------------------------------


def get_deal_service(session: TenantSession, container: ContainerDependency) -> DealService:
    """Deal service for the request."""
    return DealService(session, container.clock, container.task_queue)


def get_pipeline_service(session: TenantSession, clock: ClockDependency) -> PipelineService:
    """Pipeline service for the request."""
    return PipelineService(session, clock)


def get_task_service(session: TenantSession, clock: ClockDependency) -> TaskService:
    """Task service for the request."""
    return TaskService(session, clock)


def get_note_service(session: TenantSession, clock: ClockDependency) -> NoteService:
    """Note service for the request."""
    return NoteService(session, clock)


Deals = Annotated[DealService, Depends(get_deal_service)]
Pipelines = Annotated[PipelineService, Depends(get_pipeline_service)]
Tasks = Annotated[TaskService, Depends(get_task_service)]
Notes = Annotated[NoteService, Depends(get_note_service)]


def _stages(payload: PipelineRequest) -> list[StageInput]:
    return [
        StageInput(
            name=stage.name.strip(), kind=stage.kind, probability=stage.probability, id=stage.id
        )
        for stage in payload.stages
    ]


async def _deal_responses(session: TenantSession, deals: list[Deal]) -> list[DealResponse]:
    labels = await deal_labels(session, deals)
    responses = []
    for deal in deals:
        response = DealResponse.model_validate(deal)
        label = labels.get(deal.id)
        if label is not None:
            response.contact_name = label.contact_name
            response.contact_email = label.contact_email
            response.company_name = label.company_name
        responses.append(response)
    return responses


# --- pipelines ---------------------------------------------------------------------------


@router.get(
    "/pipelines", response_model=list[CrmPipelineResponse], summary="Pipelines", responses=READ
)
async def list_pipelines(_principal: Reader, pipelines: Pipelines) -> list[CrmPipelineResponse]:
    """Pipelines with their stages (a default "Freight quotes" pipeline is created on first use)."""
    return [
        CrmPipelineResponse(
            id=pipeline.id,
            name=pipeline.name,
            position=pipeline.position,
            is_default=pipeline.is_default,
            stages=[PipelineStageResponse.model_validate(stage) for stage in stages],
        )
        for pipeline, stages in await pipelines.list_pipelines()
    ]


@router.post(
    "/pipelines",
    response_model=CrmPipelineResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a pipeline",
    responses=WRITE,
)
async def create_pipeline(
    payload: PipelineRequest,
    _principal: Admin,
    pipelines: Pipelines,
    idempotency: IdempotencyGuardDependency,
) -> CrmPipelineResponse | JSONResponse:
    """Create a pipeline (it needs an open, a won and a lost stage)."""

    async def create() -> CrmPipelineResponse:
        pipeline, stages = await pipelines.save_pipeline(
            None, name=payload.name.strip(), stages=_stages(payload)
        )
        return CrmPipelineResponse(
            id=pipeline.id,
            name=pipeline.name,
            position=pipeline.position,
            is_default=pipeline.is_default,
            stages=[PipelineStageResponse.model_validate(stage) for stage in stages],
        )

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, create)


@router.put(
    "/pipelines/{pipeline_id}",
    response_model=CrmPipelineResponse,
    summary="Rename a pipeline and replace its stages",
    description="Stages listed with their `id` keep their deals; removed stages must be empty.",
    responses=WRITE,
)
async def update_pipeline(
    pipeline_id: uuid.UUID, payload: PipelineRequest, _principal: Admin, pipelines: Pipelines
) -> CrmPipelineResponse:
    """Update a pipeline."""
    pipeline, stages = await pipelines.save_pipeline(
        pipeline_id, name=payload.name.strip(), stages=_stages(payload)
    )
    return CrmPipelineResponse(
        id=pipeline.id,
        name=pipeline.name,
        position=pipeline.position,
        is_default=pipeline.is_default,
        stages=[PipelineStageResponse.model_validate(stage) for stage in stages],
    )


@router.delete(
    "/pipelines/{pipeline_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an empty pipeline",
    responses=WRITE,
)
async def delete_pipeline(pipeline_id: uuid.UUID, _principal: Admin, pipelines: Pipelines) -> None:
    """Delete a pipeline without deals (not the default one)."""
    await pipelines.delete_pipeline(pipeline_id)


# --- deals -------------------------------------------------------------------------------


@router.get(
    "/deals", response_model=list[DealResponse], summary="Deals (board order)", responses=READ
)
async def list_deals(
    _principal: Reader,
    session: TenantSession,
    deals: Deals,
    pipeline_id: Annotated[uuid.UUID | None, Query()] = None,
    status_filter: Annotated[DealStatus | None, Query(alias="status")] = None,
    owner_email: Annotated[str | None, Query(max_length=320)] = None,
    service_line: Annotated[ServiceLine | None, Query()] = None,
    contact_id: Annotated[uuid.UUID | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
) -> list[DealResponse]:
    """Deals of a pipeline in board order, with contact and company names."""
    rows = await deals.list_deals(
        pipeline_id=pipeline_id,
        status=status_filter,
        owner_email=owner_email,
        service_line=service_line,
        contact_id=contact_id,
        search=search,
    )
    return await _deal_responses(session, list(rows))


@router.post(
    "/deals",
    response_model=DealResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a deal",
    responses=WRITE,
)
async def create_deal(
    payload: DealCreateRequest,
    principal: Writer,
    session: TenantSession,
    deals: Deals,
    idempotency: IdempotencyGuardDependency,
) -> DealResponse | JSONResponse:
    """Create a deal (in the default pipeline's first stage unless told otherwise)."""

    async def create() -> DealResponse:
        fields = payload.model_dump(
            exclude={"contact_id", "title", "pipeline_id", "stage_id"}, exclude_none=True
        )
        if "owner_email" in fields:
            fields["owner_email"] = str(fields["owner_email"]).lower()
        deal = await deals.create_deal(
            principal.subject,
            contact_id=payload.contact_id,
            title=payload.title,
            pipeline_id=payload.pipeline_id,
            stage_id=payload.stage_id,
            **fields,
        )
        [response] = await _deal_responses(session, [deal])
        return response

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, create)


@router.get("/deals/{deal_id}", response_model=DealResponse, summary="A deal", responses=READ)
async def read_deal(
    deal_id: uuid.UUID, _principal: Reader, session: TenantSession, deals: Deals
) -> DealResponse:
    """Get a deal."""
    [response] = await _deal_responses(session, [await deals.require_deal(deal_id)])
    return response


@router.delete(
    "/deals/{deal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a deal",
    description="Its stage history goes with it; tasks, notes and meetings stay on the contact.",
    responses=WRITE,
)
async def delete_deal(deal_id: uuid.UUID, _principal: Writer, deals: Deals) -> Response:
    """Delete a deal."""
    await deals.delete_deal(deal_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/deals/{deal_id}", response_model=DealResponse, summary="Update a deal", responses=WRITE
)
async def update_deal(
    deal_id: uuid.UUID,
    payload: DealUpdateRequest,
    principal: Writer,
    session: TenantSession,
    deals: Deals,
) -> DealResponse:
    """Change a deal's details."""
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("owner_email"):
        changes["owner_email"] = str(changes["owner_email"]).lower()
    deal = await deals.update_deal(deal_id, principal.subject, changes)
    [response] = await _deal_responses(session, [deal])
    return response


@router.post(
    "/deals/{deal_id}/move",
    response_model=DealResponse,
    summary="Move a deal to a stage (drag and drop)",
    description="A won or lost stage closes the deal (and its lead); a stage change publishes "
    "`deal.stage_changed`, which can start workflows.",
    responses=WRITE,
)
async def move_deal(
    deal_id: uuid.UUID,
    payload: DealMoveRequest,
    principal: Writer,
    session: TenantSession,
    deals: Deals,
    idempotency: IdempotencyGuardDependency,
) -> DealResponse | JSONResponse:
    """Move a deal."""

    async def move() -> DealResponse:
        deal = await deals.move_deal(
            deal_id,
            principal.subject,
            stage_id=payload.stage_id,
            index=payload.index,
            lost_reason=payload.lost_reason,
        )
        [response] = await _deal_responses(session, [deal])
        return response

    return await run_idempotent(idempotency, status.HTTP_200_OK, move)


@router.get(
    "/deals/{deal_id}/timeline",
    response_model=list[TimelineEntryResponse],
    summary="A deal's history (stage changes, notes, tasks, emails)",
    responses=READ,
)
async def read_deal_timeline(
    deal_id: uuid.UUID, _principal: Reader, deals: Deals
) -> list[TimelineEntryResponse]:
    """The deal's timeline, newest first."""
    return [TimelineEntryResponse.model_validate(entry) for entry in await deals.timeline(deal_id)]


# --- tasks -------------------------------------------------------------------------------


@router.get("/tasks", response_model=list[TaskResponse], summary="Tasks", responses=READ)
async def list_tasks(
    principal: Reader,
    tasks: Tasks,
    clock: ClockDependency,
    mine: Annotated[bool, Query(description="Only tasks assigned to me.")] = False,
    assignee_email: Annotated[str | None, Query(max_length=320)] = None,
    done: Annotated[
        bool | None, Query(description="true = done, false = open, empty = all.")
    ] = False,
    due_within_days: Annotated[int | None, Query(ge=0, le=365)] = None,
    contact_id: Annotated[uuid.UUID | None, Query()] = None,
    deal_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[TaskResponse]:
    """Tasks: open ones by due date, done ones newest first."""
    due_before = (
        clock.now() + timedelta(days=due_within_days) if due_within_days is not None else None
    )
    rows = await tasks.list_tasks(
        assignee_email=principal.subject if mine else assignee_email,
        done=done,
        due_before=due_before,
        contact_id=contact_id,
        deal_id=deal_id,
    )
    return [TaskResponse.model_validate(task) for task in rows]


@router.post(
    "/tasks",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a task",
    responses=WRITE,
)
async def create_task(
    payload: TaskCreateRequest,
    principal: Writer,
    tasks: Tasks,
    idempotency: IdempotencyGuardDependency,
) -> TaskResponse | JSONResponse:
    """Create a task (assigned to me unless someone else is named)."""

    async def create() -> TaskResponse:
        task = await tasks.create_task(
            principal.subject,
            title=payload.title,
            notes=payload.notes,
            due_at=payload.due_at,
            assignee_email=str(payload.assignee_email)
            if payload.assignee_email
            else principal.subject,
            priority=payload.priority,
            contact_id=payload.contact_id,
            deal_id=payload.deal_id,
        )
        return TaskResponse.model_validate(task)

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, create)


@router.patch(
    "/tasks/{task_id}", response_model=TaskResponse, summary="Update a task", responses=WRITE
)
async def update_task(
    task_id: uuid.UUID, payload: TaskUpdateRequest, _principal: Writer, tasks: Tasks
) -> TaskResponse:
    """Change, complete or reopen a task."""
    return TaskResponse.model_validate(
        await tasks.update_task(task_id, payload.model_dump(exclude_unset=True))
    )


@router.delete(
    "/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a task",
    responses=WRITE,
)
async def delete_task(task_id: uuid.UUID, _principal: Writer, tasks: Tasks) -> None:
    """Delete a task."""
    await tasks.delete_task(task_id)


# --- notes -------------------------------------------------------------------------------


@router.get("/notes", response_model=list[NoteResponse], summary="Notes", responses=READ)
async def list_notes(
    _principal: Reader,
    notes: Notes,
    contact_id: Annotated[uuid.UUID | None, Query()] = None,
    deal_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[NoteResponse]:
    """Notes of a contact or deal, newest first."""
    return [
        NoteResponse.model_validate(note)
        for note in await notes.list_notes(contact_id=contact_id, deal_id=deal_id)
    ]


@router.post(
    "/notes",
    response_model=NoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a note",
    responses=WRITE,
)
async def create_note(
    payload: NoteCreateRequest,
    principal: Writer,
    notes: Notes,
    idempotency: IdempotencyGuardDependency,
) -> NoteResponse | JSONResponse:
    """Add a note to a contact or a deal."""

    async def create() -> NoteResponse:
        note = await notes.create_note(
            principal.subject,
            body=payload.body,
            contact_id=payload.contact_id,
            deal_id=payload.deal_id,
        )
        return NoteResponse.model_validate(note)

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, create)


@router.delete(
    "/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a note",
    responses=WRITE,
)
async def delete_note(note_id: uuid.UUID, principal: Writer, notes: Notes) -> None:
    """Delete a note (its author or an admin)."""
    await notes.delete_note(
        note_id, principal.subject, is_admin=principal.has_scope(Scope.ADMIN_MANAGE)
    )


@router.get("/summary", response_model=CrmSummaryResponse, summary="CRM numbers", responses=READ)
async def read_crm_summary(
    _principal: Reader,
    session: TenantSession,
    clock: ClockDependency,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> CrmSummaryResponse:
    """Open pipeline value (raw and weighted), wins in the period and tasks due."""
    now = clock.now()
    summary = await pipeline_summary(session, now, (now - timedelta(days=days - 1)).date())
    return CrmSummaryResponse.model_validate(summary)
