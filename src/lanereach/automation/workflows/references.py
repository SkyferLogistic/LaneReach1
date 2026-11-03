"""Checks that a workflow's references (offices, senders, templates, workflows) exist."""

from __future__ import annotations

import uuid

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.nodes.registry import NodeRegistry
from lanereach.automation.workflows.graph import WorkflowGraph
from lanereach.automation.workflows.models import Workflow, WorkflowStatus
from lanereach.automation.workflows.validator import (
    ValidationCode,
    ValidationIssue,
    ValidationReport,
)
from lanereach.domain.offices.models import Office
from lanereach.domain.senders.models import Sender
from lanereach.domain.templates.models import Template

_TEMPLATE_NODES = {"send_email", "reply_in_thread", "ai_draft_reply", "ai_localize_template"}


async def check_workflow_references(
    session: AsyncSession, graph: WorkflowGraph, registry: NodeRegistry, report: ValidationReport
) -> ValidationReport:
    """Add reference errors and warnings to a structural validation report.

    Nodes whose config is already invalid are skipped (the structural report covers them).

    Args:
        session: Tenant session (only the tenant's own rows are visible).
        graph: The graph.
        registry: Node types.
        report: Report from `validate_workflow_graph`, extended in place.

    Returns:
        The same report.
    """
    templates = set((await session.scalars(select(Template.key).distinct())).all())
    offices = {row.id: row for row in (await session.scalars(select(Office))).all()}
    active_senders = dict(
        (office_id, count)
        for office_id, count in (
            await session.execute(
                select(Sender.office_id, func.count())
                .where(Sender.is_active.is_(True), Sender.flagged_at.is_(None))
                .group_by(Sender.office_id)
            )
        ).all()
    )
    senders = {row.id: row.office_id for row in (await session.scalars(select(Sender))).all()}
    workflows = {row.id: row for row in (await session.scalars(select(Workflow))).all()}
    for node in graph.nodes:
        node_class = registry.find(node.type)
        if node_class is None:
            continue
        try:
            config = node_class.parse_config(node.config)
        except ValidationError:
            continue
        template_key = getattr(config, "template_key", None)
        if node.type in _TEMPLATE_NODES and template_key and template_key not in templates:
            report.errors.append(
                ValidationIssue(
                    code=ValidationCode.UNKNOWN_TEMPLATE,
                    message=f"Template '{template_key}' does not exist.",
                    node_id=node.id,
                )
            )
        office_id: uuid.UUID | None = getattr(config, "office_id", None)
        if office_id is not None:
            if office_id not in offices:
                report.errors.append(
                    ValidationIssue(
                        code=ValidationCode.UNKNOWN_OFFICE,
                        message=f"Office {office_id} does not exist.",
                        node_id=node.id,
                    )
                )
            elif not active_senders.get(office_id):
                report.warnings.append(
                    ValidationIssue(
                        code=ValidationCode.OFFICE_WITHOUT_SENDER,
                        message=f"Office '{offices[office_id].name}' has no active sender mailbox.",
                        node_id=node.id,
                    )
                )
        sender_id: uuid.UUID | None = getattr(config, "sender_id", None)
        if sender_id is not None and senders.get(sender_id) != office_id:
            report.errors.append(
                ValidationIssue(
                    code=ValidationCode.UNKNOWN_SENDER,
                    message="The sender does not exist or belongs to another office.",
                    node_id=node.id,
                )
            )
        target_id: uuid.UUID | None = getattr(config, "workflow_id", None)
        if target_id is not None:
            target = workflows.get(target_id)
            if target is None:
                report.errors.append(
                    ValidationIssue(
                        code=ValidationCode.UNKNOWN_WORKFLOW,
                        message=f"Workflow {target_id} does not exist.",
                        node_id=node.id,
                    )
                )
            elif target.status is not WorkflowStatus.PUBLISHED or target.paused_at is not None:
                report.warnings.append(
                    ValidationIssue(
                        code=ValidationCode.WORKFLOW_NOT_RUNNING,
                        message=f"Workflow '{target.name}' is not published and running yet.",
                        node_id=node.id,
                    )
                )
    return report
