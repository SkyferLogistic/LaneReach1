"""Workflow business rules: creation, versioning, validation, publishing, pausing.

Versioning rules (spec §6.3):

* every save creates a new immutable version (`latest_version` increments);
* publishing points `current_version` at a version that passes validation;
* editing a published workflow creates a new *draft* version; `current_version` (what new
  enrollments start on) does not change until that version is published;
* running enrollments finish on the version they started on (the engine stores the version).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.auth.principal import Principal
from lanereach.automation.engine.services import EngineServices
from lanereach.automation.engine.test_run import TestRunResult, simulate_workflow_run
from lanereach.automation.enrollments.models import OPEN_ENROLLMENT_STATUSES, Enrollment
from lanereach.automation.nodes.registry import NodeRegistry
from lanereach.automation.workflows.graph import WorkflowGraph
from lanereach.automation.workflows.models import Workflow, WorkflowStatus, WorkflowVersion
from lanereach.automation.workflows.references import check_workflow_references
from lanereach.automation.workflows.repository import WorkflowRepository
from lanereach.automation.workflows.schedule import CampaignSchedule
from lanereach.automation.workflows.usage import describe_users, find_workflows_using
from lanereach.automation.workflows.validator import ValidationReport, validate_workflow_graph
from lanereach.core.clock import Clock, ManualClock
from lanereach.core.errors import (
    BusinessRuleViolationError,
    ConflictError,
    NotFoundError,
    ProblemFieldError,
)
from lanereach.domain.contacts.models import Contact

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WorkflowWithVersion:
    """A workflow and the version the caller asked for (usually the latest)."""

    workflow: Workflow
    version: WorkflowVersion


class WorkflowPublishRefusedError(BusinessRuleViolationError):
    """Publishing was refused because the graph has validation errors."""

    problem_type = "workflow-invalid"
    title = "Workflow has validation errors"


class WorkflowService:
    """Workflow operations for the current tenant.

    Args:
        session: Tenant-scoped session.
        clock: Time source.
        registry: Node types workflows may use.
        max_cold_emails: `MAX_COLD_EMAILS_PER_SEQUENCE`.
    """

    def __init__(
        self,
        session: AsyncSession,
        clock: Clock,
        registry: NodeRegistry,
        *,
        max_cold_emails: int,
    ) -> None:
        self._session = session
        self._repository = WorkflowRepository(session)
        self._clock = clock
        self._registry = registry
        self._max_cold_emails = max_cold_emails

    async def create_workflow(
        self, principal: Principal, *, name: str, description: str, graph: WorkflowGraph
    ) -> WorkflowWithVersion:
        """Create a draft workflow with version 1.

        Args:
            principal: The caller (recorded as creator).
            name: Unique name within the tenant.
            description: Free text.
            graph: Initial graph (may be incomplete).

        Returns:
            The workflow and version 1.

        Raises:
            ConflictError: If the name is already used.
        """
        now = self._clock.now()
        workflow = Workflow(
            name=name,
            description=description,
            status=WorkflowStatus.DRAFT,
            current_version=None,
            latest_version=1,
            created_by=principal.subject,
            created_at=now,
            updated_at=now,
        )
        version = WorkflowVersion(
            version=1,
            graph=graph.model_dump(mode="json"),
            created_by=principal.subject,
            created_at=now,
        )
        try:
            async with self._session.begin_nested():
                await self._repository.add_workflow(workflow, version)
        except IntegrityError as exc:
            raise ConflictError(f"A workflow named '{name}' already exists.") from exc
        logger.info("workflow_created", workflow_id=str(workflow.id))
        return WorkflowWithVersion(workflow, version)

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
            status: Optional status filter.
            search: Optional name substring.
            limit: Page size.
            cursor: Cursor from the previous page.

        Returns:
            The page and the next cursor.
        """
        return await self._repository.list_workflows(
            status=status, search=search, limit=limit, cursor=cursor
        )

    async def get_workflow(self, workflow_id: uuid.UUID) -> WorkflowWithVersion:
        """Load a workflow with its latest version.

        Args:
            workflow_id: Workflow ID.

        Returns:
            The workflow and its latest version.

        Raises:
            NotFoundError: If the workflow does not exist in the caller's tenant.
        """
        workflow = await self._require_workflow(workflow_id)
        return WorkflowWithVersion(workflow, await self._require_version(workflow, None))

    async def get_workflow_version(
        self, workflow_id: uuid.UUID, version: int
    ) -> WorkflowWithVersion:
        """Load a specific version.

        Args:
            workflow_id: Workflow ID.
            version: Version number.

        Returns:
            The workflow and the version.

        Raises:
            NotFoundError: If the workflow or version does not exist.
        """
        workflow = await self._require_workflow(workflow_id)
        return WorkflowWithVersion(workflow, await self._require_version(workflow, version))

    async def update_workflow(
        self,
        principal: Principal,
        workflow_id: uuid.UUID,
        *,
        graph: WorkflowGraph,
        name: str | None,
        description: str | None,
        base_version: int | None,
    ) -> WorkflowWithVersion:
        """Save a new draft version (and optionally rename the workflow).

        Args:
            principal: The caller (recorded as version author).
            workflow_id: Workflow ID.
            graph: The new graph.
            name: New name, if renaming.
            description: New description, if changing.
            base_version: Version the editor started from (optimistic concurrency).

        Returns:
            The workflow and the new version.

        Raises:
            NotFoundError: If the workflow does not exist.
            ConflictError: If the workflow is archived, another version was saved since
                `base_version`, or the new name is taken.
        """
        workflow = await self._require_workflow(workflow_id, for_update=True)
        self._ensure_not_archived(workflow)
        if base_version is not None and base_version != workflow.latest_version:
            raise ConflictError(
                f"Version {workflow.latest_version} was saved after version {base_version}; "
                "reload the workflow before saving."
            )
        now = self._clock.now()
        version = WorkflowVersion(
            workflow_id=workflow.id,
            version=workflow.latest_version + 1,
            graph=graph.model_dump(mode="json"),
            created_by=principal.subject,
            created_at=now,
        )
        await self._repository.add_version(version)
        workflow.latest_version = version.version
        workflow.updated_at = now
        if description is not None:
            workflow.description = description
        if name is not None and name != workflow.name:
            workflow.name = name
            try:
                async with self._session.begin_nested():
                    await self._session.flush()
            except IntegrityError as exc:
                raise ConflictError(f"A workflow named '{name}' already exists.") from exc
        return WorkflowWithVersion(workflow, version)

    async def validate_graph(self, graph: WorkflowGraph) -> ValidationReport:
        """Validate a graph (structure and references) without storing anything.

        Args:
            graph: The graph.

        Returns:
            The validation report.
        """
        report = validate_workflow_graph(
            graph, registry=self._registry, max_cold_emails=self._max_cold_emails
        )
        return await check_workflow_references(self._session, graph, self._registry, report)

    async def validate_workflow(
        self, workflow_id: uuid.UUID, version: int | None
    ) -> ValidationReport:
        """Validate a stored version (the latest by default).

        Args:
            workflow_id: Workflow ID.
            version: Version number, or None for the latest.

        Returns:
            The validation report.

        Raises:
            NotFoundError: If the workflow or version does not exist.
        """
        workflow = await self._require_workflow(workflow_id)
        stored = await self._require_version(workflow, version)
        return await self.validate_graph(WorkflowGraph.model_validate(stored.graph))

    async def publish_workflow(self, workflow_id: uuid.UUID, version: int | None) -> Workflow:
        """Make a version the one new enrollments start on.

        Args:
            workflow_id: Workflow ID.
            version: Version to publish, or None for the latest.

        Returns:
            The updated workflow.

        Raises:
            NotFoundError: If the workflow or version does not exist.
            ConflictError: If the workflow is archived.
            WorkflowPublishRefusedError: If the version has validation errors; the problem's
                `errors[]` lists them with the node IDs.
        """
        workflow = await self._require_workflow(workflow_id, for_update=True)
        self._ensure_not_archived(workflow)
        stored = await self._require_version(workflow, version)
        report = await self.validate_graph(WorkflowGraph.model_validate(stored.graph))
        if not report.is_valid:
            raise WorkflowPublishRefusedError(
                f"Version {stored.version} has {len(report.errors)} validation error(s).",
                errors=[
                    ProblemFieldError(
                        location=["graph", issue.node_id] if issue.node_id else ["graph"],
                        message=issue.message,
                        code=issue.code.value,
                    )
                    for issue in report.errors
                ],
            )
        now = self._clock.now()
        if stored.published_at is None:
            stored.published_at = now
        workflow.current_version = stored.version
        workflow.status = WorkflowStatus.PUBLISHED
        workflow.updated_at = now
        logger.info("workflow_published", workflow_id=str(workflow.id), version=stored.version)
        return workflow

    async def pause_workflow(self, workflow_id: uuid.UUID) -> Workflow:
        """Pause a published workflow: no new enrollments, running ones stop advancing.

        Args:
            workflow_id: Workflow ID.

        Returns:
            The workflow (unchanged if already paused).

        Raises:
            NotFoundError: If the workflow does not exist.
            BusinessRuleViolationError: If the workflow was never published.
        """
        workflow = await self._require_workflow(workflow_id, for_update=True)
        if workflow.status is not WorkflowStatus.PUBLISHED:
            raise BusinessRuleViolationError("Only published workflows can be paused.")
        if workflow.paused_at is None:
            workflow.paused_at = self._clock.now()
            workflow.updated_at = workflow.paused_at
            logger.info("workflow_paused", workflow_id=str(workflow.id))
        return workflow

    async def set_workflow_schedule(
        self, workflow_id: uuid.UUID, schedule: CampaignSchedule | None
    ) -> Workflow:
        """Set or clear the campaign schedule.

        It applies to the next emails, also those of running enrollments.

        Args:
            workflow_id: Workflow ID.
            schedule: The schedule, or None to send whenever the other guardrails allow.

        Returns:
            The workflow.

        Raises:
            NotFoundError: If the workflow does not exist.
            BusinessRuleViolationError: If the workflow is archived.
        """
        workflow = await self._require_workflow(workflow_id, for_update=True)
        if workflow.status is WorkflowStatus.ARCHIVED:
            raise BusinessRuleViolationError("Archived workflows cannot be changed.")
        workflow.schedule = schedule.model_dump(mode="json") if schedule else None
        workflow.updated_at = self._clock.now()
        logger.info("workflow_schedule_set", workflow_id=str(workflow.id), cleared=schedule is None)
        return workflow

    async def resume_workflow(self, workflow_id: uuid.UUID) -> Workflow:
        """Resume a paused workflow.

        Args:
            workflow_id: Workflow ID.

        Returns:
            The workflow (unchanged if not paused).

        Raises:
            NotFoundError: If the workflow does not exist.
        """
        workflow = await self._require_workflow(workflow_id, for_update=True)
        if workflow.paused_at is not None:
            workflow.paused_at = None
            workflow.updated_at = self._clock.now()
            logger.info("workflow_resumed", workflow_id=str(workflow.id))
        return workflow

    async def archive_workflow(self, workflow_id: uuid.UUID) -> Workflow:
        """Archive a workflow. Archived workflows cannot be edited, published or enrolled into.

        Args:
            workflow_id: Workflow ID.

        Returns:
            The archived workflow.

        Raises:
            NotFoundError: If the workflow does not exist.
        """
        workflow = await self._require_workflow(workflow_id, for_update=True)
        if workflow.status is not WorkflowStatus.ARCHIVED:
            workflow.status = WorkflowStatus.ARCHIVED
            workflow.updated_at = self._clock.now()
            logger.info("workflow_archived", workflow_id=str(workflow.id))
        return workflow

    async def delete_workflow(self, workflow_id: uuid.UUID) -> None:
        """Delete a workflow with its versions and finished enrollments.

        Refused while enrollments are still open (pause or archive first, so nothing stops
        mid-sequence by surprise) and while another workflow enrolls into this one.

        Args:
            workflow_id: Workflow ID.

        Raises:
            NotFoundError: If the workflow does not exist.
            ConflictError: If it has open enrollments or is used by another workflow.
        """
        workflow = await self._require_workflow(workflow_id, for_update=True)
        open_count = await self._session.scalar(
            select(func.count()).where(
                Enrollment.workflow_id == workflow.id,
                Enrollment.status.in_(OPEN_ENROLLMENT_STATUSES),
            )
        )
        if open_count:
            raise ConflictError(
                f"'{workflow.name}' still has {open_count} running enrollment(s). "
                "Archive it (enrollments stop) or wait until they finish, then delete it."
            )
        users = await find_workflows_using(
            self._session, "workflow_id", workflow.id, exclude=workflow.id
        )
        if users:
            raise ConflictError(
                f"'{workflow.name}' is used by {describe_users(users)}. "
                "Change those workflows first."
            )
        await self._session.delete(workflow)
        await self._session.flush()
        logger.info("workflow_deleted", workflow_id=str(workflow_id))

    async def list_versions(
        self, workflow_id: uuid.UUID
    ) -> tuple[Workflow, Sequence[WorkflowVersion]]:
        """List all versions of a workflow, newest first.

        Args:
            workflow_id: Workflow ID.

        Returns:
            The workflow and its versions.

        Raises:
            NotFoundError: If the workflow does not exist.
        """
        workflow = await self._require_workflow(workflow_id)
        return workflow, await self._repository.list_versions(workflow.id)

    async def _require_workflow(
        self, workflow_id: uuid.UUID, *, for_update: bool = False
    ) -> Workflow:
        workflow = await self._repository.get_workflow(workflow_id, for_update=for_update)
        if workflow is None:
            raise NotFoundError("Workflow not found.")
        return workflow

    async def _require_version(self, workflow: Workflow, version: int | None) -> WorkflowVersion:
        number = workflow.latest_version if version is None else version
        stored = await self._repository.get_version(workflow.id, number)
        if stored is None:
            raise NotFoundError(f"Version {number} of this workflow does not exist.")
        return stored

    async def test_run_workflow(
        self,
        workflow_id: uuid.UUID,
        *,
        contact_id: uuid.UUID,
        version: int | None,
        start_at: datetime,
        variables: dict[str, Any],
        build_services: Callable[[AsyncSession, ManualClock], EngineServices],
    ) -> TestRunResult:
        """Simulate a contact through a version (the latest by default); nothing is persisted.

        Args:
            workflow_id: Workflow ID.
            contact_id: Contact to simulate.
            version: Version to simulate.
            start_at: Simulated start time.
            variables: Initial enrollment variables (for example a simulated reply).
            build_services: Builds engine services for the simulation's session and clock.

        Returns:
            The simulated path, emails and notifications.

        Raises:
            NotFoundError: If the workflow, version or contact does not exist.
            WorkflowPublishRefusedError: If the version has validation errors.
        """
        workflow = await self._require_workflow(workflow_id)
        stored = await self._require_version(workflow, version)
        report = await self.validate_graph(WorkflowGraph.model_validate(stored.graph))
        if not report.is_valid:
            raise WorkflowPublishRefusedError(
                "Fix the validation errors before test-running.",
                errors=[
                    ProblemFieldError(
                        location=["graph", issue.node_id] if issue.node_id else ["graph"],
                        message=issue.message,
                        code=issue.code.value,
                    )
                    for issue in report.errors
                ],
            )
        contact = await self._session.get(Contact, contact_id)
        if contact is None:
            raise NotFoundError("Contact not found.")
        return await simulate_workflow_run(
            self._session,
            build_services,
            workflow=workflow,
            version=stored.version,
            contact=contact,
            start_at=start_at,
            variables=variables,
        )

    @staticmethod
    def _ensure_not_archived(workflow: Workflow) -> None:
        if workflow.status is WorkflowStatus.ARCHIVED:
            raise ConflictError("Archived workflows cannot be changed.")
