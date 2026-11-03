"""CRM action nodes: create a deal, move a deal to a stage, create a task."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import EmailStr, Field, model_validator
from sqlalchemy import func, select

from lanereach.automation.engine.context import ExecutionContext
from lanereach.automation.engine.results import Continue, Fail, NodeResult
from lanereach.automation.nodes.base import BaseNode, NodeCategory, NodeConfig
from lanereach.automation.nodes.registry import register_node
from lanereach.domain.crm.models import Deal, DealStatus, PipelineStage, TaskPriority
from lanereach.domain.crm.service import DealService, TaskService, ensure_default_pipeline
from lanereach.domain.senders.models import ServiceLine
from lanereach.domain.templates.rendering import render_template_text

DEAL_VARIABLE = "deal_id"


def _values(context: ExecutionContext) -> dict[str, str | None]:
    subject = context.require_subject()
    contact, company = subject.contact, subject.company
    return {
        "contact.first_name": contact.first_name,
        "contact.full_name": contact.display_name,
        "contact.email": contact.email,
        "contact.country": contact.country,
        "company.name": company.name if company else None,
        "office.name": subject.office.name if subject.office else None,
    }


def _render(text: str, context: ExecutionContext) -> str:
    return render_template_text(text, _values(context), lenient=True)[:200]


def add_business_days(start: datetime, days: int) -> datetime:
    """`start` plus `days` working days (Saturday and Sunday skipped).

    Args:
        start: From when.
        days: Working days to add (0 = the same day).

    Returns:
        The due moment.
    """
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


async def _stage_by_name(
    context: ExecutionContext, pipeline_id: uuid.UUID | None, name: str | None
) -> PipelineStage | None:
    services = context.require_services()
    session = services.session
    pipeline = pipeline_id or (await ensure_default_pipeline(session, services.clock)).id
    statement = select(PipelineStage).where(PipelineStage.pipeline_id == pipeline)
    if name:
        statement = statement.where(func.lower(PipelineStage.name) == name.strip().lower())
    return await session.scalar(statement.order_by(PipelineStage.position).limit(1))


class CreateDealConfig(NodeConfig):
    """The deal to open for the contact."""

    title: str = Field(
        default="{{company.name|New}} freight quote",
        min_length=1,
        max_length=200,
        description="Placeholders: {{contact.full_name}}, {{company.name}}, {{contact.country}}.",
    )
    pipeline_id: uuid.UUID | None = Field(default=None, description="Empty = the default pipeline.")
    stage_name: str | None = Field(
        default=None, max_length=100, description="Empty = the pipeline's first stage."
    )
    value_amount: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999"))
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    service_line: ServiceLine = ServiceLine.ANY
    skip_if_open: bool = Field(
        default=True, description="Do nothing when the contact already has an open deal here."
    )


@register_node
class CreateDealNode(BaseNode[CreateDealConfig]):
    """Opens a CRM deal for the contact."""

    node_type = "create_deal"
    category = NodeCategory.ACTION
    display_name = "Create deal"
    description = "Opens a deal for the contact in a pipeline (the default one unless chosen)."
    config_model = CreateDealConfig

    async def execute(self, context: ExecutionContext, config: CreateDealConfig) -> NodeResult:
        """Create the deal and remember it for later CRM steps (`deal_id` variable)."""
        services = context.require_services()
        subject = context.require_subject()
        stage = await _stage_by_name(context, config.pipeline_id, config.stage_name)
        if stage is None:
            return Fail(error=f"stage '{config.stage_name}' does not exist", retryable=False)
        if config.skip_if_open:
            existing = await services.session.scalar(
                select(Deal).where(
                    Deal.contact_id == subject.contact.id,
                    Deal.pipeline_id == stage.pipeline_id,
                    Deal.status == DealStatus.OPEN,
                )
            )
            if existing is not None:
                context.variables[DEAL_VARIABLE] = str(existing.id)
                context.output["deal_id"] = str(existing.id)
                context.output["reused_deal"] = True
                return Continue()
        deal = await DealService(services.session, services.clock, services.task_queue).create_deal(
            f"enrollment:{context.enrollment_id}",
            contact_id=subject.contact.id,
            title=_render(config.title, context),
            pipeline_id=stage.pipeline_id,
            stage_id=stage.id,
            value_amount=config.value_amount,
            currency=config.currency,
            service_line=config.service_line,
        )
        context.variables[DEAL_VARIABLE] = str(deal.id)
        context.output["deal_id"] = str(deal.id)
        return Continue()


class MoveDealStageConfig(NodeConfig):
    """Where the contact's deal goes."""

    stage_name: str = Field(min_length=1, max_length=100, examples=["Quoted"])
    pipeline_id: uuid.UUID | None = Field(default=None, description="Empty = the default pipeline.")


@register_node
class MoveDealStageNode(BaseNode[MoveDealStageConfig]):
    """Moves the contact's deal to a stage."""

    node_type = "move_deal_stage"
    category = NodeCategory.ACTION
    display_name = "Move deal stage"
    description = (
        "Moves the deal of this workflow (or the contact's latest open deal) to a stage; "
        "skipped when there is none."
    )
    config_model = MoveDealStageConfig

    async def execute(self, context: ExecutionContext, config: MoveDealStageConfig) -> NodeResult:
        """Move the deal; a missing stage fails, a missing deal is skipped."""
        services = context.require_services()
        subject = context.require_subject()
        stage = await _stage_by_name(context, config.pipeline_id, config.stage_name)
        if stage is None or stage.name.lower() != config.stage_name.strip().lower():
            return Fail(error=f"stage '{config.stage_name}' does not exist", retryable=False)
        deal = None
        known = context.variables.get(DEAL_VARIABLE)
        if known:
            deal = await services.session.get(Deal, uuid.UUID(str(known)))
        if deal is None or deal.pipeline_id != stage.pipeline_id:
            deal = await services.session.scalar(
                select(Deal)
                .where(
                    Deal.contact_id == subject.contact.id,
                    Deal.pipeline_id == stage.pipeline_id,
                    Deal.status == DealStatus.OPEN,
                )
                .order_by(Deal.created_at.desc())
                .limit(1)
            )
        if deal is None:
            context.output["skipped"] = "no open deal for this contact"
            return Continue()
        await DealService(services.session, services.clock, services.task_queue).move_deal(
            deal.id, f"enrollment:{context.enrollment_id}", stage_id=stage.id
        )
        context.output["deal_id"] = str(deal.id)
        return Continue()


class CreateTaskConfig(NodeConfig):
    """The to-do and who gets it."""

    title: str = Field(
        min_length=1,
        max_length=200,
        examples=["Call {{contact.full_name}} about the quote"],
        description="Placeholders: {{contact.full_name}}, {{company.name}}, {{contact.country}}.",
    )
    due_in_business_days: int = Field(default=1, ge=0, le=60)
    assignee: Literal["office_owner", "contact_owner", "deal_owner", "email"] = "office_owner"
    assignee_email: EmailStr | None = Field(default=None, description="When assignee is `email`.")
    priority: TaskPriority = TaskPriority.NORMAL

    @model_validator(mode="after")
    def _check(self) -> CreateTaskConfig:
        if self.assignee == "email" and self.assignee_email is None:
            raise ValueError("assignee_email is required when assignee is 'email'")
        return self


@register_node
class CreateTaskNode(BaseNode[CreateTaskConfig]):
    """Creates a task for a person."""

    node_type = "create_task"
    category = NodeCategory.ACTION
    display_name = "Create task"
    description = (
        "Creates a to-do (for example: call back) for the office owner, contact owner or anyone."
    )
    config_model = CreateTaskConfig

    async def execute(self, context: ExecutionContext, config: CreateTaskConfig) -> NodeResult:
        """Create the task, linked to the contact and the workflow's deal when there is one."""
        services = context.require_services()
        subject = context.require_subject()
        deal = None
        known = context.variables.get(DEAL_VARIABLE)
        if known:
            deal = await services.session.get(Deal, uuid.UUID(str(known)))
        assignee = {
            "office_owner": subject.office.owner_email if subject.office else None,
            "contact_owner": subject.contact.owner_email,
            "deal_owner": deal.owner_email if deal else None,
            "email": str(config.assignee_email) if config.assignee_email else None,
        }[config.assignee]
        if assignee is None and subject.office is not None:
            assignee = subject.office.owner_email
        task = await TaskService(services.session, services.clock).create_task(
            f"enrollment:{context.enrollment_id}",
            title=_render(config.title, context),
            due_at=add_business_days(context.now, config.due_in_business_days),
            assignee_email=assignee,
            priority=config.priority,
            contact_id=subject.contact.id,
            deal_id=deal.id if deal else None,
        )
        context.output["task_id"] = str(task.id)
        return Continue()
