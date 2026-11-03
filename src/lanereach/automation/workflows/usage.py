"""Which workflows point at an office, an agent or another workflow (before deleting it).

Workflow graphs keep ids in node configs (`office_id`, `sender_id`, `workflow_id`) without a
foreign key, so deleting the target would leave a node that fails at run time. Deletes check
here first and refuse with the names of the workflows to change.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.workflows.models import Workflow, WorkflowStatus, WorkflowVersion


def _configs(graph: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [graph.get("trigger") or {}, *(graph.get("nodes") or [])]
    return [node.get("config") or {} for node in nodes if isinstance(node, dict)]


async def find_workflows_using(
    session: AsyncSession, key: str, value: uuid.UUID, *, exclude: uuid.UUID | None = None
) -> list[str]:
    """Names of non-archived workflows whose latest or live version uses `key = value`.

    Args:
        session: Tenant session.
        key: Config key, for example `office_id`.
        value: The id.
        exclude: A workflow to ignore (the one being deleted).

    Returns:
        Workflow names, sorted.
    """
    rows = (
        await session.execute(
            select(Workflow.id, Workflow.name, WorkflowVersion.graph)
            .join(WorkflowVersion, WorkflowVersion.workflow_id == Workflow.id)
            .where(
                Workflow.status != WorkflowStatus.ARCHIVED,
                or_(
                    WorkflowVersion.version == Workflow.latest_version,
                    WorkflowVersion.version == Workflow.current_version,
                ),
            )
        )
    ).all()
    wanted = str(value)
    names = {
        name
        for workflow_id, name, graph in rows
        if workflow_id != exclude
        and any(str(config.get(key)) == wanted for config in _configs(graph or {}))
    }
    return sorted(names)


def describe_users(names: list[str], limit: int = 3) -> str:
    """Names for an error message, for example `'A', 'B' and 2 more`."""
    shown = ", ".join(f"'{name}'" for name in names[:limit])
    extra = len(names) - limit
    return f"{shown} and {extra} more" if extra > 0 else shown
