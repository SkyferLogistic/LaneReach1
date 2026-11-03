"""CRM business logic: pipelines, deals on the board, the deal timeline, tasks and notes.

Every tenant gets a default "Freight quotes" pipeline the first time the CRM is used. A lead
(a reply asking for rates) opens a deal in its first stage; moving the deal to a won or lost
stage closes it, mirrors the outcome onto the lead and publishes `deal.stage_changed`, which
can start workflows.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.engine.queue import TaskQueue
from lanereach.automation.events.models import EventType
from lanereach.automation.events.service import publish_event
from lanereach.core.clock import Clock
from lanereach.core.errors import BusinessRuleViolationError, ConflictError, NotFoundError
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.companies.models import Company
from lanereach.domain.contacts.models import Contact
from lanereach.domain.crm.models import (
    Deal,
    DealEvent,
    DealStatus,
    Note,
    Pipeline,
    PipelineStage,
    StageKind,
    Task,
    TaskPriority,
)
from lanereach.domain.leads.models import Lead, LeadStatus
from lanereach.domain.leads.service import update_lead
from lanereach.domain.logistics import ALL_MODE_KEYS, line_for_mode
from lanereach.domain.messages.models import Message
from lanereach.domain.offices.models import Office
from lanereach.domain.senders.models import ServiceLine

DEFAULT_PIPELINE_NAME = "Freight quotes"
DEFAULT_STAGES: tuple[tuple[str, StageKind, int], ...] = (
    ("Quote requested", StageKind.OPEN, 10),
    ("Sourcing rates", StageKind.OPEN, 30),
    ("Quoted", StageKind.OPEN, 50),
    ("Negotiating", StageKind.OPEN, 70),
    ("Won", StageKind.WON, 100),
    ("Lost", StageKind.LOST, 0),
)
MAX_STAGES = 12
TIMELINE_LIMIT = 100
_MODES = {"air", "ocean", "sea", "fcl", "lcl", "road", "rail", "courier"}


# --- pipelines ---------------------------------------------------------------------------


async def ensure_default_pipeline(session: AsyncSession, clock: Clock) -> Pipeline:
    """The tenant's default pipeline, created with the freight stages on first use.

    Args:
        session: Tenant session.
        clock: Time source.

    Returns:
        The default pipeline.
    """
    pipeline = await session.scalar(select(Pipeline).where(Pipeline.is_default.is_(True)))
    if pipeline is not None:
        return pipeline
    pipeline = await session.scalar(select(Pipeline).order_by(Pipeline.position).limit(1))
    if pipeline is not None:
        pipeline.is_default = True
        return pipeline
    now = clock.now()
    tenant_id = require_session_tenant_id(session)
    pipeline = Pipeline(
        tenant_id=tenant_id,
        name=DEFAULT_PIPELINE_NAME,
        position=0,
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    session.add(pipeline)
    await session.flush()
    for position, (name, kind, probability) in enumerate(DEFAULT_STAGES):
        session.add(
            PipelineStage(
                tenant_id=tenant_id,
                pipeline_id=pipeline.id,
                name=name,
                position=position,
                kind=kind,
                probability=probability,
                created_at=now,
                updated_at=now,
            )
        )
    await session.flush()
    return pipeline


@dataclass(frozen=True, slots=True)
class StageInput:
    """A stage as given by the console."""

    name: str
    kind: StageKind = StageKind.OPEN
    probability: int = 0
    id: uuid.UUID | None = None


class PipelineService:
    """Pipelines and their stages.

    Args:
        session: Tenant session.
        clock: Time source.
    """

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def list_pipelines(self) -> list[tuple[Pipeline, list[PipelineStage]]]:
        """Pipelines (the default first) with their stages in board order."""
        await ensure_default_pipeline(self._session, self._clock)
        pipelines = (
            await self._session.scalars(
                select(Pipeline).order_by(
                    Pipeline.is_default.desc(), Pipeline.position, Pipeline.name
                )
            )
        ).all()
        stages = (
            await self._session.scalars(select(PipelineStage).order_by(PipelineStage.position))
        ).all()
        by_pipeline: dict[uuid.UUID, list[PipelineStage]] = {}
        for stage in stages:
            by_pipeline.setdefault(stage.pipeline_id, []).append(stage)
        return [(pipeline, by_pipeline.get(pipeline.id, [])) for pipeline in pipelines]

    async def require_pipeline(self, pipeline_id: uuid.UUID) -> Pipeline:
        """Load a pipeline or raise 404."""
        pipeline = await self._session.get(Pipeline, pipeline_id)
        if pipeline is None:
            raise NotFoundError("Pipeline not found.")
        return pipeline

    async def save_pipeline(
        self, pipeline_id: uuid.UUID | None, *, name: str, stages: Sequence[StageInput]
    ) -> tuple[Pipeline, list[PipelineStage]]:
        """Create a pipeline or replace the stages of an existing one (in the given order).

        Stages keep their id (and their deals) when listed; a removed stage must be empty.

        Raises:
            BusinessRuleViolationError: Without a won and a lost stage, with duplicate names or
                too many stages, or when removing a stage that still holds deals.
            ConflictError: If the name is taken.
        """
        self._check_stages(stages)
        # The default pipeline exists before any other (else the first custom one would
        # become the default).
        await ensure_default_pipeline(self._session, self._clock)
        now = self._clock.now()
        tenant_id = require_session_tenant_id(self._session)
        # Checked before the new row exists (its id is only set on flush).
        same_name = select(Pipeline.id).where(func.lower(Pipeline.name) == name.lower())
        if pipeline_id is not None:
            same_name = same_name.where(Pipeline.id != pipeline_id)
        if await self._session.scalar(same_name.limit(1)) is not None:
            raise ConflictError(f"A pipeline named '{name}' already exists.")
        if pipeline_id is None:
            count = await self._session.scalar(select(func.count()).select_from(Pipeline)) or 0
            pipeline = Pipeline(
                tenant_id=tenant_id,
                name=name,
                position=int(count),
                is_default=False,
                created_at=now,
                updated_at=now,
            )
            self._session.add(pipeline)
            existing: dict[uuid.UUID, PipelineStage] = {}
        else:
            pipeline = await self.require_pipeline(pipeline_id)
            pipeline.name = name
            pipeline.updated_at = now
            existing = {
                stage.id: stage
                for stage in (
                    await self._session.scalars(
                        select(PipelineStage).where(PipelineStage.pipeline_id == pipeline.id)
                    )
                ).all()
            }
        await self._session.flush()
        kept = {stage.id for stage in stages if stage.id is not None}
        for stage_id in set(existing) - kept:
            used = await self._session.scalar(
                select(func.count()).select_from(Deal).where(Deal.stage_id == stage_id)
            )
            if used:
                raise BusinessRuleViolationError(
                    f"Stage '{existing[stage_id].name}' still has deals; move them first."
                )
            await self._session.delete(existing[stage_id])
        await self._session.flush()
        # Temporary names avoid clashing with the unique (pipeline, name) while reordering.
        for stage in existing.values():
            if stage.id in kept:
                stage.name = f"~{stage.id}"
        await self._session.flush()
        result: list[PipelineStage] = []
        for position, item in enumerate(stages):
            if item.id is not None:
                found = existing.get(item.id)
                if found is None:
                    raise NotFoundError("Stage not found in this pipeline.")
                stage = found
            else:
                stage = PipelineStage(tenant_id=tenant_id, pipeline_id=pipeline.id, created_at=now)
                self._session.add(stage)
            stage.name = item.name
            stage.kind = item.kind
            stage.probability = item.probability
            stage.position = position
            stage.updated_at = now
            result.append(stage)
        await self._session.flush()
        return pipeline, result

    async def delete_pipeline(self, pipeline_id: uuid.UUID) -> None:
        """Delete an empty, non-default pipeline.

        Raises:
            BusinessRuleViolationError: For the default pipeline or one with deals.
        """
        pipeline = await self.require_pipeline(pipeline_id)
        if pipeline.is_default:
            raise BusinessRuleViolationError("The default pipeline cannot be deleted.")
        deals = await self._session.scalar(
            select(func.count()).select_from(Deal).where(Deal.pipeline_id == pipeline.id)
        )
        if deals:
            raise BusinessRuleViolationError("The pipeline still has deals.")
        await self._session.delete(pipeline)

    @staticmethod
    def _check_stages(stages: Sequence[StageInput]) -> None:
        if not 2 <= len(stages) <= MAX_STAGES:
            raise BusinessRuleViolationError(f"A pipeline has 2 to {MAX_STAGES} stages.")
        names = [stage.name.strip().lower() for stage in stages]
        if len(set(names)) != len(names):
            raise BusinessRuleViolationError("Stage names must be different.")
        kinds = {stage.kind for stage in stages}
        if StageKind.WON not in kinds or StageKind.LOST not in kinds:
            raise BusinessRuleViolationError("A pipeline needs a won stage and a lost stage.")
        if StageKind.OPEN not in kinds:
            raise BusinessRuleViolationError("A pipeline needs at least one open stage.")


# --- deals -------------------------------------------------------------------------------


def _place(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    parts = [value.get("port_code") or value.get("city"), value.get("country")]
    text = " ".join(str(part) for part in parts if part)
    return text[:120] or None


def deal_title(
    company: Company | None, contact: Contact, origin: str | None, destination: str | None
) -> str:
    """Deal title like `IST Cargo: YYZ → IST` (who, then the lane when known)."""
    who = company.name if company else contact.display_name
    lane = f"{origin or '?'} → {destination or '?'}" if origin or destination else "freight quote"
    return f"{who}: {lane}"[:200]


async def open_deal_for_lead(
    session: AsyncSession,
    clock: Clock,
    lead: Lead,
    *,
    actor: str,
    task_queue: TaskQueue | None = None,
) -> Deal:
    """Open a deal in the default pipeline's first stage for a lead (once per lead).

    Args:
        session: Tenant session.
        clock: Time source.
        lead: The lead (already flushed).
        actor: Who or what created it (`reply-pipeline`, `enrollment:<id>`).
        task_queue: Where `deal.stage_changed` processing is enqueued.

    Returns:
        The deal (the existing one when the lead already has a deal).
    """
    existing = await session.scalar(select(Deal).where(Deal.lead_id == lead.id))
    if existing is not None:
        return existing
    pipeline = await ensure_default_pipeline(session, clock)
    first = await session.scalar(
        select(PipelineStage)
        .where(PipelineStage.pipeline_id == pipeline.id, PipelineStage.kind == StageKind.OPEN)
        .order_by(PipelineStage.position)
        .limit(1)
    )
    assert first is not None  # noqa: S101 - every pipeline has an open stage
    contact = await session.get(Contact, lead.contact_id)
    assert contact is not None  # noqa: S101 - leads reference their contact
    company = await session.get(Company, lead.company_id) if lead.company_id else None
    office = await session.get(Office, lead.office_id) if lead.office_id else None
    shipment = lead.shipment or {}
    origin, destination = _place(shipment.get("origin")), _place(shipment.get("destination"))
    mode = str(shipment.get("mode") or "").lower()
    now = clock.now()
    deal = Deal(
        tenant_id=lead.tenant_id,
        pipeline_id=pipeline.id,
        stage_id=first.id,
        contact_id=contact.id,
        company_id=company.id if company else None,
        office_id=lead.office_id,
        lead_id=lead.id,
        title=deal_title(company, contact, origin, destination),
        currency="USD",
        service_line=line_for_mode(mode),
        mode=mode if mode in ALL_MODE_KEYS or mode in _MODES else None,
        incoterm=str(shipment.get("incoterm"))[:3] if shipment.get("incoterm") else None,
        cargo_ready_date=_date(shipment.get("cargo_ready_date")),
        weight_kg=_decimal(shipment.get("weight_kg")),
        volume_cbm=_decimal(shipment.get("volume_cbm")),
        pieces=_int(shipment.get("container_quantity")),
        equipment=_equipment(shipment.get("container_type")),
        origin=origin,
        destination=destination,
        commodity=str(shipment.get("commodity"))[:200] if shipment.get("commodity") else None,
        owner_email=contact.owner_email or (office.owner_email if office else None),
        status=DealStatus.OPEN,
        board_position=await _next_position(session, first.id),
        stage_entered_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(deal)
    await session.flush()
    _record(session, deal, "created", {"stage": first.name, "lead_id": str(lead.id)}, actor, now)
    # One event for "a new quote request": webhooks, Slack alerts and workflows listen to it.
    await publish_event(
        session,
        clock,
        event_type=EventType.LEAD_CREATED,
        contact_id=lead.contact_id,
        payload={
            "lead_id": str(lead.id),
            "deal_id": str(deal.id),
            "title": deal.title,
            "source": lead.source,
            "shipment": shipment,
        },
        idempotency_key=f"lead.created:{lead.id}",
        task_queue=task_queue,
    )
    return deal


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)).quantize(Decimal("0.001")) if value not in (None, "") else None
    except (ArithmeticError, ValueError):
        return None


def _equipment(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    return text[:30] if text and text != "OTHER" else None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


async def _next_position(session: AsyncSession, stage_id: uuid.UUID) -> float:
    last = await session.scalar(
        select(func.max(Deal.board_position)).where(Deal.stage_id == stage_id)
    )
    return float(last or 0) + 1024.0


def _record(
    session: AsyncSession, deal: Deal, kind: str, data: dict[str, Any], actor: str, now: datetime
) -> None:
    session.add(
        DealEvent(
            tenant_id=deal.tenant_id,
            deal_id=deal.id,
            kind=kind,
            data=data,
            actor=actor,
            created_at=now,
        )
    )


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """One line of a deal's history."""

    kind: str
    occurred_at: datetime
    title: str
    detail: str | None = None
    actor: str | None = None
    ref_id: uuid.UUID | None = None


class DealService:
    """Deals on the board.

    Args:
        session: Tenant session.
        clock: Time source.
        task_queue: Where workflow events are enqueued after commit.
    """

    def __init__(self, session: AsyncSession, clock: Clock, task_queue: TaskQueue | None) -> None:
        self._session = session
        self._clock = clock
        self._task_queue = task_queue

    async def list_deals(
        self,
        *,
        pipeline_id: uuid.UUID | None = None,
        status: DealStatus | None = None,
        owner_email: str | None = None,
        service_line: ServiceLine | None = None,
        contact_id: uuid.UUID | None = None,
        search: str | None = None,
        limit: int = 500,
    ) -> Sequence[Deal]:
        """Deals in board order (stage position, then card position)."""
        statement = select(Deal).join(PipelineStage, PipelineStage.id == Deal.stage_id)
        if pipeline_id is not None:
            statement = statement.where(Deal.pipeline_id == pipeline_id)
        if status is not None:
            statement = statement.where(Deal.status == status)
        if owner_email:
            statement = statement.where(func.lower(Deal.owner_email) == owner_email.lower())
        if service_line is not None:
            statement = statement.where(Deal.service_line == service_line)
        if contact_id is not None:
            statement = statement.where(Deal.contact_id == contact_id)
        if search:
            like = f"%{search.strip()}%"
            statement = statement.where(
                or_(Deal.title.ilike(like), Deal.origin.ilike(like), Deal.destination.ilike(like))
            )
        statement = statement.order_by(PipelineStage.position, Deal.board_position).limit(limit)
        return (await self._session.scalars(statement)).all()

    async def require_deal(self, deal_id: uuid.UUID) -> Deal:
        """Load a deal or raise 404."""
        deal = await self._session.get(Deal, deal_id)
        if deal is None:
            raise NotFoundError("Deal not found.")
        return deal

    async def delete_deal(self, deal_id: uuid.UUID) -> None:
        """Delete a deal (its history goes with it; tasks, notes and meetings stay on the contact).

        Raises:
            NotFoundError: If missing.
        """
        deal = await self.require_deal(deal_id)
        await self._session.delete(deal)
        await self._session.flush()

    async def _require_stage(self, stage_id: uuid.UUID) -> PipelineStage:
        stage = await self._session.get(PipelineStage, stage_id)
        if stage is None:
            raise NotFoundError("Stage not found.")
        return stage

    async def create_deal(
        self,
        actor: str,
        *,
        contact_id: uuid.UUID,
        title: str,
        pipeline_id: uuid.UUID | None = None,
        stage_id: uuid.UUID | None = None,
        **fields: Any,
    ) -> Deal:
        """Create a deal by hand (or from a workflow).

        Raises:
            NotFoundError: For an unknown contact, pipeline or stage.
            BusinessRuleViolationError: When the stage is not in the pipeline.
        """
        contact = await self._session.get(Contact, contact_id)
        if contact is None:
            raise NotFoundError("Contact not found.")
        pipeline = (
            await PipelineService(self._session, self._clock).require_pipeline(pipeline_id)
            if pipeline_id
            else await ensure_default_pipeline(self._session, self._clock)
        )
        if stage_id is not None:
            stage = await self._require_stage(stage_id)
            if stage.pipeline_id != pipeline.id:
                raise BusinessRuleViolationError("That stage belongs to another pipeline.")
        else:
            first = await self._session.scalar(
                select(PipelineStage)
                .where(PipelineStage.pipeline_id == pipeline.id)
                .order_by(PipelineStage.position)
                .limit(1)
            )
            assert first is not None  # noqa: S101 - pipelines have stages
            stage = first
        now = self._clock.now()
        deal = Deal(
            tenant_id=contact.tenant_id,
            pipeline_id=pipeline.id,
            stage_id=stage.id,
            contact_id=contact.id,
            company_id=fields.pop("company_id", None) or contact.company_id,
            office_id=fields.pop("office_id", None) or contact.office_id,
            title=title,
            status=DealStatus.OPEN,
            owner_email=fields.pop("owner_email", None) or contact.owner_email,
            board_position=await _next_position(self._session, stage.id),
            stage_entered_at=now,
            created_at=now,
            updated_at=now,
        )
        for name, value in fields.items():
            setattr(deal, name, value)
        self._session.add(deal)
        await self._session.flush()
        _record(self._session, deal, "created", {"stage": stage.name}, actor, now)
        if stage.kind is not StageKind.OPEN:
            await self._close(deal, stage, actor, now, previous_status=DealStatus.OPEN)
        return deal

    async def update_deal(self, deal_id: uuid.UUID, actor: str, changes: dict[str, Any]) -> Deal:
        """Change a deal's details (not its stage; see `move_deal`)."""
        deal = await self.require_deal(deal_id)
        now = self._clock.now()
        if "value_amount" in changes and changes["value_amount"] != deal.value_amount:
            _record(
                self._session,
                deal,
                "value_changed",
                {
                    "from": str(deal.value_amount) if deal.value_amount is not None else None,
                    "to": str(changes["value_amount"])
                    if changes["value_amount"] is not None
                    else None,
                    "currency": changes.get("currency", deal.currency),
                },
                actor,
                now,
            )
        for name, value in changes.items():
            setattr(deal, name, value)
        deal.updated_at = now
        return deal

    async def move_deal(
        self,
        deal_id: uuid.UUID,
        actor: str,
        *,
        stage_id: uuid.UUID,
        index: int | None = None,
        lost_reason: str | None = None,
    ) -> Deal:
        """Move a deal to a stage (and a position in its column).

        Reaching a won or lost stage closes the deal and mirrors the outcome on its lead;
        leaving one reopens it. A stage change publishes `deal.stage_changed`.

        Args:
            deal_id: The deal.
            actor: Who moved it.
            stage_id: Target stage (same pipeline).
            index: Position in the target column (0 = top); None puts it at the bottom.
            lost_reason: Why it was lost (kept on the deal).

        Returns:
            The deal.

        Raises:
            BusinessRuleViolationError: When the stage belongs to another pipeline.
        """
        deal = await self.require_deal(deal_id)
        stage = await self._require_stage(stage_id)
        if stage.pipeline_id != deal.pipeline_id:
            raise BusinessRuleViolationError("Deals move between stages of their own pipeline.")
        deal.board_position = await self._position_at(stage.id, deal.id, index)
        now = self._clock.now()
        deal.updated_at = now
        if lost_reason is not None:
            deal.lost_reason = lost_reason[:300] or None
        if stage.id == deal.stage_id:
            return deal
        previous = await self._require_stage(deal.stage_id)
        previous_status = deal.status
        deal.stage_id = stage.id
        deal.stage_entered_at = now
        _record(
            self._session,
            deal,
            "stage_changed",
            {"from": previous.name, "to": stage.name, "kind": stage.kind.value},
            actor,
            now,
        )
        await self._close(deal, stage, actor, now, previous_status=previous_status)
        await publish_event(
            self._session,
            self._clock,
            event_type=EventType.DEAL_STAGE_CHANGED,
            contact_id=deal.contact_id,
            payload={
                "deal_id": str(deal.id),
                "pipeline_id": str(deal.pipeline_id),
                "stage_id": str(stage.id),
                "stage": stage.name,
                "kind": stage.kind.value,
                "previous_stage": previous.name,
            },
            idempotency_key=f"deal:{deal.id}:{previous.id}->{stage.id}:{now.isoformat()}",
            task_queue=self._task_queue,
        )
        return deal

    async def _close(
        self,
        deal: Deal,
        stage: PipelineStage,
        actor: str,
        now: datetime,
        *,
        previous_status: DealStatus,
    ) -> None:
        status = DealStatus(stage.kind.value)
        deal.status = status
        deal.won_at = now if status is DealStatus.WON else None
        deal.lost_at = now if status is DealStatus.LOST else None
        if status is not previous_status:
            _record(
                self._session,
                deal,
                status.value if status is not DealStatus.OPEN else "reopened",
                {},
                actor,
                now,
            )
        if deal.lead_id is None:
            return
        # The lead mirrors the outcome, so "Lead status changed" workflows keep working.
        target = {
            DealStatus.WON: LeadStatus.WON,
            DealStatus.LOST: LeadStatus.LOST,
            DealStatus.OPEN: LeadStatus.QUOTING,
        }[status]
        lead = await self._session.get(Lead, deal.lead_id)
        if (
            lead is not None
            and (lead.status is not target)
            and not (
                status is DealStatus.OPEN and lead.status in {LeadStatus.QUOTING, LeadStatus.QUOTED}
            )
        ):
            await update_lead(self._session, self._clock, self._task_queue, lead.id, status=target)

    async def _position_at(
        self, stage_id: uuid.UUID, deal_id: uuid.UUID, index: int | None
    ) -> float:
        positions = [
            position
            for position in (
                await self._session.scalars(
                    select(Deal.board_position)
                    .where(Deal.stage_id == stage_id, Deal.id != deal_id)
                    .order_by(Deal.board_position)
                )
            ).all()
        ]
        if index is None or index >= len(positions):
            return (positions[-1] if positions else 0.0) + 1024.0
        if index <= 0:
            return (positions[0] if positions else 1024.0) - 1024.0
        return (positions[index - 1] + positions[index]) / 2

    async def timeline(self, deal_id: uuid.UUID) -> list[TimelineEntry]:
        """The deal's history: events, notes, tasks and the contact's emails, newest first."""
        deal = await self.require_deal(deal_id)
        entries: list[TimelineEntry] = []
        for event in (
            await self._session.scalars(select(DealEvent).where(DealEvent.deal_id == deal.id))
        ).all():
            entries.append(
                TimelineEntry(
                    kind=f"deal.{event.kind}",
                    occurred_at=event.created_at,
                    title=_event_title(event),
                    actor=event.actor,
                    ref_id=event.id,
                )
            )
        for note in (
            await self._session.scalars(
                select(Note).where(or_(Note.deal_id == deal.id, Note.contact_id == deal.contact_id))
            )
        ).all():
            entries.append(
                TimelineEntry(
                    "note", note.created_at, "Note", note.body[:500], note.author, note.id
                )
            )
        for task in (
            await self._session.scalars(select(Task).where(Task.deal_id == deal.id))
        ).all():
            entries.append(
                TimelineEntry(
                    "task.done" if task.done_at else "task",
                    task.done_at or task.created_at,
                    task.title,
                    f"due {task.due_at:%Y-%m-%d}" if task.due_at else None,
                    task.assignee_email,
                    task.id,
                )
            )
        for message in (
            await self._session.scalars(
                select(Message)
                .where(Message.contact_id == deal.contact_id)
                .order_by(Message.occurred_at.desc())
                .limit(TIMELINE_LIMIT)
            )
        ).all():
            bucket = ((message.processing_result or {}).get("classification") or {}).get("bucket")
            entries.append(
                TimelineEntry(
                    f"email.{message.direction.value}",
                    message.occurred_at,
                    message.subject,
                    bucket,
                    message.from_email,
                    message.id,
                )
            )
        # Imported here: meetings belong to the integrations package, which depends on CRM.
        from lanereach.integrations.meetings.models import Meeting

        for meeting in (
            await self._session.scalars(
                select(Meeting)
                .where(or_(Meeting.deal_id == deal.id, Meeting.contact_id == deal.contact_id))
                .order_by(Meeting.starts_at.desc())
                .limit(TIMELINE_LIMIT)
            )
        ).all():
            entries.append(
                TimelineEntry(
                    f"meeting.{meeting.status.value}",
                    meeting.created_at,
                    f"Meeting {meeting.status.value}: {meeting.title}",
                    f"{meeting.starts_at:%d %b %Y %H:%M} UTC · {meeting.provider}",
                    meeting.invitee_email,
                    meeting.id,
                )
            )
        entries.sort(key=lambda entry: entry.occurred_at, reverse=True)
        return entries[:TIMELINE_LIMIT]


def _event_title(event: DealEvent) -> str:
    data = event.data or {}
    if event.kind == "created":
        return f"Deal opened in {data.get('stage', 'the first stage')}"
    if event.kind == "stage_changed":
        return f"Moved from {data.get('from')} to {data.get('to')}"
    if event.kind == "value_changed":
        return f"Value changed to {data.get('to') or 'none'} {data.get('currency') or ''}".strip()
    if event.kind == "won":
        return "Deal won"
    if event.kind == "lost":
        return "Deal lost"
    if event.kind == "reopened":
        return "Deal reopened"
    if event.kind == "priced_from_rates":
        return (
            f"Quote {data.get('quote', '')} priced from rate sheet {data.get('sheet', '')}".strip()
        )
    if event.kind == "market_check":
        return (
            f"Market rate from {data.get('source')}: {data.get('amount')} {data.get('currency')} "
            f"per {data.get('unit')}"
        )
    return event.kind.replace("_", " ")


# --- tasks & notes -----------------------------------------------------------------------


class TaskService:
    """To-dos for people.

    Args:
        session: Tenant session.
        clock: Time source.
    """

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def list_tasks(
        self,
        *,
        assignee_email: str | None = None,
        done: bool | None = False,
        due_before: datetime | None = None,
        contact_id: uuid.UUID | None = None,
        deal_id: uuid.UUID | None = None,
        limit: int = 200,
    ) -> Sequence[Task]:
        """Tasks, open ones by due date (undated last), done ones newest first."""
        statement = select(Task)
        if assignee_email:
            statement = statement.where(func.lower(Task.assignee_email) == assignee_email.lower())
        if done is True:
            statement = statement.where(Task.done_at.is_not(None)).order_by(Task.done_at.desc())
        elif done is False:
            statement = statement.where(Task.done_at.is_(None)).order_by(
                Task.due_at.asc().nulls_last(), Task.created_at
            )
        else:
            statement = statement.order_by(Task.created_at.desc())
        if due_before is not None:
            statement = statement.where(Task.due_at <= due_before)
        if contact_id is not None:
            statement = statement.where(Task.contact_id == contact_id)
        if deal_id is not None:
            statement = statement.where(Task.deal_id == deal_id)
        return (await self._session.scalars(statement.limit(limit))).all()

    async def require_task(self, task_id: uuid.UUID) -> Task:
        """Load a task or raise 404."""
        task = await self._session.get(Task, task_id)
        if task is None:
            raise NotFoundError("Task not found.")
        return task

    async def create_task(
        self,
        created_by: str,
        *,
        title: str,
        notes: str = "",
        due_at: datetime | None = None,
        assignee_email: str | None = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        contact_id: uuid.UUID | None = None,
        deal_id: uuid.UUID | None = None,
    ) -> Task:
        """Create a task (a deal's contact is filled in when only the deal is given).

        Raises:
            NotFoundError: For an unknown contact or deal.
        """
        if deal_id is not None:
            deal = await self._session.get(Deal, deal_id)
            if deal is None:
                raise NotFoundError("Deal not found.")
            contact_id = contact_id or deal.contact_id
        if contact_id is not None and await self._session.get(Contact, contact_id) is None:
            raise NotFoundError("Contact not found.")
        now = self._clock.now()
        task = Task(
            tenant_id=require_session_tenant_id(self._session),
            title=title,
            notes=notes,
            due_at=due_at,
            assignee_email=assignee_email.lower() if assignee_email else None,
            priority=priority,
            contact_id=contact_id,
            deal_id=deal_id,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        self._session.add(task)
        await self._session.flush()
        return task

    async def update_task(self, task_id: uuid.UUID, changes: dict[str, Any]) -> Task:
        """Change a task; `done: true/false` completes or reopens it."""
        task = await self.require_task(task_id)
        now = self._clock.now()
        done = changes.pop("done", None)
        if done is True and task.done_at is None:
            task.done_at = now
        elif done is False:
            task.done_at = None
        if changes.get("assignee_email"):
            changes["assignee_email"] = str(changes["assignee_email"]).lower()
        for name, value in changes.items():
            setattr(task, name, value)
        task.updated_at = now
        return task

    async def delete_task(self, task_id: uuid.UUID) -> None:
        """Delete a task."""
        await self._session.delete(await self.require_task(task_id))


class NoteService:
    """Notes on contacts and deals.

    Args:
        session: Tenant session.
        clock: Time source.
    """

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def list_notes(
        self, *, contact_id: uuid.UUID | None = None, deal_id: uuid.UUID | None = None
    ) -> Sequence[Note]:
        """Notes of a contact or deal, newest first."""
        statement = select(Note).order_by(Note.created_at.desc()).limit(200)
        if contact_id is not None:
            statement = statement.where(Note.contact_id == contact_id)
        if deal_id is not None:
            statement = statement.where(Note.deal_id == deal_id)
        return (await self._session.scalars(statement)).all()

    async def create_note(
        self,
        author: str,
        *,
        body: str,
        contact_id: uuid.UUID | None = None,
        deal_id: uuid.UUID | None = None,
    ) -> Note:
        """Add a note (to a deal, a contact, or both).

        Raises:
            BusinessRuleViolationError: Without a contact or deal.
            NotFoundError: For an unknown contact or deal.
        """
        if contact_id is None and deal_id is None:
            raise BusinessRuleViolationError("A note belongs to a contact or a deal.")
        if deal_id is not None:
            deal = await self._session.get(Deal, deal_id)
            if deal is None:
                raise NotFoundError("Deal not found.")
            contact_id = contact_id or deal.contact_id
        if contact_id is not None and await self._session.get(Contact, contact_id) is None:
            raise NotFoundError("Contact not found.")
        now = self._clock.now()
        note = Note(
            tenant_id=require_session_tenant_id(self._session),
            body=body,
            contact_id=contact_id,
            deal_id=deal_id,
            author=author,
            created_at=now,
            updated_at=now,
        )
        self._session.add(note)
        await self._session.flush()
        return note

    async def delete_note(self, note_id: uuid.UUID, actor: str, *, is_admin: bool) -> None:
        """Delete a note (its author or an admin).

        Raises:
            NotFoundError: If missing.
            BusinessRuleViolationError: When someone else's note is deleted by a non-admin.
        """
        note = await self._session.get(Note, note_id)
        if note is None:
            raise NotFoundError("Note not found.")
        if note.author != actor and not is_admin:
            raise BusinessRuleViolationError("Only the author or an admin can delete this note.")
        await self._session.delete(note)


@dataclass(frozen=True, slots=True)
class LineSummary:
    """Open and won business for one service line (air, ocean, road...)."""

    service_line: str
    open_deals: int
    open_value: Decimal
    won_in_period: int
    won_value_in_period: Decimal


@dataclass(frozen=True, slots=True)
class PipelineSummary:
    """Numbers for the dashboard."""

    open_deals: int
    open_value: Decimal
    weighted_value: Decimal
    won_in_period: int
    won_value_in_period: Decimal
    tasks_due_today: int
    tasks_overdue: int
    by_line: tuple[LineSummary, ...] = ()


async def pipeline_summary(session: AsyncSession, now: datetime, since: date) -> PipelineSummary:
    """Open pipeline value (raw and weighted by stage probability), wins and due tasks.

    Values are summed as stored, whatever their currency (the console shows the main one).
    The per-line split shows which modes the business actually comes from.
    """
    rows = await session.execute(
        select(Deal.value_amount, PipelineStage.probability, Deal.service_line)
        .join(PipelineStage, PipelineStage.id == Deal.stage_id)
        .where(Deal.status == DealStatus.OPEN)
    )
    open_deals = 0
    open_value = Decimal(0)
    weighted = Decimal(0)
    lines: dict[str, list[Decimal | int]] = {}
    for value, probability, line in rows:
        open_deals += 1
        bucket = lines.setdefault(str(line), [0, Decimal(0), 0, Decimal(0)])
        bucket[0] = int(bucket[0]) + 1
        if value is not None:
            open_value += value
            weighted += value * Decimal(probability) / Decimal(100)
            bucket[1] = Decimal(bucket[1]) + value
    won_rows = await session.execute(
        select(Deal.service_line, func.count(), func.coalesce(func.sum(Deal.value_amount), 0))
        .where(Deal.status == DealStatus.WON, func.date(Deal.won_at) >= since)
        .group_by(Deal.service_line)
    )
    won_count = 0
    won_value = Decimal(0)
    for line, count, value in won_rows:
        bucket = lines.setdefault(str(line), [0, Decimal(0), 0, Decimal(0)])
        bucket[2] = int(count)
        bucket[3] = Decimal(value or 0)
        won_count += int(count)
        won_value += Decimal(value or 0)
    by_line = tuple(
        sorted(
            (
                LineSummary(
                    service_line=line,
                    open_deals=int(values[0]),
                    open_value=Decimal(values[1]).quantize(Decimal("0.01")),
                    won_in_period=int(values[2]),
                    won_value_in_period=Decimal(values[3]).quantize(Decimal("0.01")),
                )
                for line, values in lines.items()
            ),
            key=lambda item: (item.open_value + item.won_value_in_period, item.open_deals),
            reverse=True,
        )
    )
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    due_today = await session.scalar(
        select(func.count()).where(
            Task.done_at.is_(None), Task.due_at >= start_of_day, Task.due_at <= end_of_day
        )
    )
    overdue = await session.scalar(
        select(func.count()).where(Task.done_at.is_(None), Task.due_at < start_of_day)
    )
    return PipelineSummary(
        open_deals=open_deals,
        open_value=open_value.quantize(Decimal("0.01")),
        weighted_value=weighted.quantize(Decimal("0.01")),
        won_in_period=int(won_count),
        won_value_in_period=Decimal(won_value or 0).quantize(Decimal("0.01")),
        tasks_due_today=int(due_today or 0),
        tasks_overdue=int(overdue or 0),
        by_line=by_line,
    )


@dataclass(frozen=True, slots=True)
class DealLabels:
    """Names shown on a deal card."""

    contact_name: str
    contact_email: str
    company_name: str | None


async def deal_labels(session: AsyncSession, deals: Sequence[Deal]) -> dict[uuid.UUID, DealLabels]:
    """Contact and company names for deal cards, in two queries (not one per deal)."""
    contact_ids = {deal.contact_id for deal in deals}
    company_ids = {deal.company_id for deal in deals if deal.company_id}
    contacts = (
        {
            row.id: row
            for row in (
                await session.scalars(select(Contact).where(Contact.id.in_(contact_ids)))
            ).all()
        }
        if contact_ids
        else {}
    )
    companies = (
        {
            row.id: row.name
            for row in (
                await session.scalars(select(Company).where(Company.id.in_(company_ids)))
            ).all()
        }
        if company_ids
        else {}
    )
    labels: dict[uuid.UUID, DealLabels] = {}
    for deal in deals:
        contact = contacts.get(deal.contact_id)
        labels[deal.id] = DealLabels(
            contact_name=contact.display_name if contact else "",
            contact_email=contact.email if contact else "",
            company_name=companies.get(deal.company_id) if deal.company_id else None,
        )
    return labels


IDLE_DAYS = (3, 7, 14, 30)


async def publish_idle_deals(
    session: AsyncSession, clock: Clock, task_queue: TaskQueue | None
) -> int:
    """Publish `deal.idle` for open deals that stayed 3, 7, 14 or 30 days in their stage.

    Runs hourly. The idempotency key includes when the deal entered the stage, so a deal that
    moves and comes back gets fresh reminders, and each threshold is published once per stay.

    Args:
        session: Tenant session.
        clock: Time source.
        task_queue: Where to enqueue processing.

    Returns:
        Idle events published or already published this run.
    """
    now = clock.now()
    rows = (
        await session.execute(
            select(Deal, PipelineStage.name)
            .join(PipelineStage, PipelineStage.id == Deal.stage_id)
            .where(Deal.status == DealStatus.OPEN)
        )
    ).all()
    published = 0
    for deal, stage_name in rows:
        days_in_stage = (now - deal.stage_entered_at).days
        for days in IDLE_DAYS:
            if days_in_stage < days:
                break
            await publish_event(
                session,
                clock,
                event_type=EventType.DEAL_IDLE,
                contact_id=deal.contact_id,
                payload={
                    "deal_id": str(deal.id),
                    "pipeline_id": str(deal.pipeline_id),
                    "stage": stage_name,
                    "days": days,
                    "days_in_stage": days_in_stage,
                },
                idempotency_key=f"deal.idle:{deal.id}:{deal.stage_entered_at.isoformat()}:{days}",
                task_queue=task_queue,
            )
            published += 1
    return published
