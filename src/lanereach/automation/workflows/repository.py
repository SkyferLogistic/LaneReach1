"""Database access for workflows and their versions (tenant-scoped sessions only)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.workflows.models import Workflow, WorkflowStatus, WorkflowVersion
from lanereach.core.pagination import paginate_newest_first
from lanereach.core.tenancy import require_session_tenant_id


class WorkflowRepository:
    """Reads and writes `workflows` and `workflow_versions`.

    Args:
        session: A tenant-scoped session; RLS limits every query to its tenant.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_workflow(self, workflow: Workflow, first_version: WorkflowVersion) -> None:
        """Insert a workflow and its first version.

        Args:
            workflow: The new workflow.
            first_version: Version 1 of its graph.

        Raises:
            sqlalchemy.exc.IntegrityError: If the name is already used in the tenant.
        """
        tenant_id = require_session_tenant_id(self._session)
        workflow.tenant_id = tenant_id
        first_version.tenant_id = tenant_id
        self._session.add(workflow)
        await self._session.flush()
        first_version.workflow_id = workflow.id
        self._session.add(first_version)
        await self._session.flush()

    async def get_workflow(
        self, workflow_id: uuid.UUID, *, for_update: bool = False
    ) -> Workflow | None:
        """Load a workflow of the session's tenant.

        Args:
            workflow_id: Workflow ID.
            for_update: Lock the row (used before creating a version so numbers never collide).

        Returns:
            The workflow, or None when missing or owned by another tenant.
        """
        statement = select(Workflow).where(Workflow.id == workflow_id)
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def list_workflows(
        self,
        *,
        status: WorkflowStatus | None,
        search: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[Sequence[Workflow], str | None]:
        """List workflows newest first.

        Args:
            status: Only this status, when given.
            search: Case-insensitive substring of the name, when given.
            limit: Page size.
            cursor: Cursor from the previous page.

        Returns:
            The page and the next cursor.
        """
        statement = select(Workflow)
        if status is not None:
            statement = statement.where(Workflow.status == status)
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            statement = statement.where(Workflow.name.ilike(f"%{escaped}%", escape="\\"))
        return await paginate_newest_first(
            self._session,
            statement,
            created_at_column=Workflow.created_at,
            id_column=Workflow.id,
            limit=limit,
            cursor=cursor,
        )

    async def add_version(self, version: WorkflowVersion) -> WorkflowVersion:
        """Insert a new version.

        Args:
            version: The version row.

        Returns:
            The persisted version.
        """
        version.tenant_id = require_session_tenant_id(self._session)
        self._session.add(version)
        await self._session.flush()
        return version

    async def get_version(self, workflow_id: uuid.UUID, version: int) -> WorkflowVersion | None:
        """Load one version of a workflow.

        Args:
            workflow_id: Workflow ID.
            version: Version number.

        Returns:
            The version, or None.
        """
        return await self._session.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_id == workflow_id, WorkflowVersion.version == version
            )
        )

    async def list_versions(self, workflow_id: uuid.UUID) -> Sequence[WorkflowVersion]:
        """List every version of a workflow, newest first.

        Args:
            workflow_id: Workflow ID.

        Returns:
            The versions.
        """
        result = await self._session.scalars(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow_id)
            .order_by(WorkflowVersion.version.desc())
        )
        return result.all()
