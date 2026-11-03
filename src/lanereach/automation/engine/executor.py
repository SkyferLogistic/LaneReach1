"""The workflow executor: `advance_enrollment`.

One call ("tick") locks the enrollment row with `FOR UPDATE SKIP LOCKED` (so concurrent workers
never advance the same enrollment), then runs nodes until one returns `Wait`, `Stop` or
`Fail`, or `MAX_STEPS_PER_TICK` is reached. Every node execution is recorded as an
`EnrollmentStep`.

Crash safety: a tick is one transaction. If the process dies mid-tick nothing is committed and
the next tick re-runs the same step with the same `enrollment_id:step_index` idempotency key,
which the sending provider uses to avoid sending twice. Each node runs in a savepoint, so an
exception in one node rolls back only that node's partial writes.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select

from lanereach.automation.engine.context import EnrollmentSubject, ExecutionContext
from lanereach.automation.engine.facts import build_condition_facts
from lanereach.automation.engine.results import Continue, Fail, NodeResult, Stop, Wait
from lanereach.automation.engine.services import EngineServices
from lanereach.automation.enrollments.models import (
    Enrollment,
    EnrollmentStatus,
    EnrollmentStep,
    StepResult,
    WorkflowCompanyPause,
)
from lanereach.automation.workflows.graph import GraphNode, WorkflowGraph
from lanereach.automation.workflows.models import Workflow, WorkflowStatus, WorkflowVersion
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.companies.models import Company, CompanyBranch
from lanereach.domain.contacts.models import Contact
from lanereach.domain.offices.models import Office
from lanereach.tenants.models import Tenant

logger = structlog.get_logger(__name__)

RETRY_BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30))
COMPANY_PAUSE_RECHECK = timedelta(days=1)


@dataclass(frozen=True)
class AdvanceOutcome:
    """What one tick did."""

    enrollment_id: uuid.UUID
    advanced: bool
    status: EnrollmentStatus | None
    steps_executed: int
    reason: str | None = None


@dataclass
class _LoadedGraph:
    nodes: dict[str, GraphNode]
    edges: dict[tuple[str, str], str]


def _load_graph(graph: dict[str, Any]) -> _LoadedGraph:
    parsed = WorkflowGraph.model_validate(graph)
    return _LoadedGraph(
        nodes={node.id: node for node in parsed.all_nodes()},
        edges={(edge.source, edge.handle): edge.target for edge in parsed.edges},
    )


class EnrollmentExecutor:
    """Advances enrollments.

    Args:
        services: Engine services bound to the tenant session of this unit of work.
    """

    def __init__(self, services: EngineServices) -> None:
        self._services = services
        self._session = services.session

    async def advance_enrollment(self, enrollment_id: uuid.UUID) -> AdvanceOutcome:
        """Run one tick of an enrollment.

        Args:
            enrollment_id: The enrollment to advance.

        Returns:
            What happened. Enrollments that are locked by another worker, not due, paused or
            final are left untouched (`advanced=False`).
        """
        now = self._services.clock.now()
        enrollment = await self._session.scalar(
            select(Enrollment)
            .where(Enrollment.id == enrollment_id)
            .with_for_update(skip_locked=True)
        )
        if enrollment is None:
            return AdvanceOutcome(enrollment_id, False, None, 0, "locked or missing")
        if enrollment.status not in (EnrollmentStatus.ACTIVE, EnrollmentStatus.WAITING):
            return AdvanceOutcome(enrollment_id, False, enrollment.status, 0, "not runnable")
        if enrollment.next_run_at is not None and enrollment.next_run_at > now:
            return AdvanceOutcome(enrollment_id, False, enrollment.status, 0, "not due")

        workflow = await self._session.get(Workflow, enrollment.workflow_id)
        assert workflow is not None  # noqa: S101 - FK guarantees it
        if workflow.status is WorkflowStatus.ARCHIVED:
            self._finish(enrollment, EnrollmentStatus.STOPPED, "workflow archived")
            return AdvanceOutcome(enrollment_id, True, enrollment.status, 0)
        if workflow.paused_at is not None:
            return AdvanceOutcome(enrollment_id, False, enrollment.status, 0, "workflow paused")

        subject = await self._load_subject(workflow, enrollment)
        if subject is None:
            self._finish(enrollment, EnrollmentStatus.FAILED, "contact no longer exists")
            return AdvanceOutcome(enrollment_id, True, enrollment.status, 0)
        if await self._defer_for_company_pause(enrollment, subject, now):
            return AdvanceOutcome(enrollment_id, False, enrollment.status, 0, "paused for company")

        version = await self._session.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_id == workflow.id,
                WorkflowVersion.version == enrollment.workflow_version,
            )
        )
        assert version is not None  # noqa: S101 - versions are never deleted
        graph = _load_graph(version.graph)

        if enrollment.resume_handle is not None:
            target = graph.edges.get((enrollment.current_node_id, enrollment.resume_handle))
            enrollment.resume_handle = None
            if target is None:
                self._finish(
                    enrollment, EnrollmentStatus.COMPLETED, "reached the end of the workflow"
                )
                return AdvanceOutcome(enrollment_id, True, enrollment.status, 0)
            enrollment.current_node_id = target

        branch_count = await self._branch_count(subject.company)
        executed = 0
        max_steps = self._services.settings.max_steps_per_tick
        while executed < max_steps:
            node = graph.nodes.get(enrollment.current_node_id)
            if node is None:
                self._finish(
                    enrollment,
                    EnrollmentStatus.FAILED,
                    f"node '{enrollment.current_node_id}' is missing",
                )
                break
            result, context = await self._run_node(enrollment, node, subject, branch_count)
            executed += 1
            if not self._apply_result(enrollment, node, result, context, graph):
                break
        else:
            # Step budget used up: stay due so the next tick continues immediately.
            enrollment.status = EnrollmentStatus.ACTIVE
            enrollment.next_run_at = self._services.clock.now()
        enrollment.updated_at = self._services.clock.now()
        return AdvanceOutcome(enrollment_id, True, enrollment.status, executed)

    async def _load_subject(
        self, workflow: Workflow, enrollment: Enrollment
    ) -> EnrollmentSubject | None:
        contact = await self._session.get(Contact, enrollment.contact_id)
        if contact is None:
            return None
        tenant = await self._session.get(Tenant, enrollment.tenant_id)
        assert tenant is not None  # noqa: S101 - the session's own tenant
        company = (
            await self._session.get(Company, contact.company_id) if contact.company_id else None
        )
        office = await self._session.get(Office, contact.office_id) if contact.office_id else None
        return EnrollmentSubject(tenant, workflow, contact, company, office)

    async def _branch_count(self, company: Company | None) -> int | None:
        if company is None:
            return None
        count = await self._session.scalar(
            select(func.count())
            .select_from(CompanyBranch)
            .where(CompanyBranch.company_id == company.id)
        )
        return int(count or 0)

    async def _defer_for_company_pause(
        self, enrollment: Enrollment, subject: EnrollmentSubject, now: datetime
    ) -> bool:
        if subject.company is None:
            return False
        pause = await self._session.scalar(
            select(WorkflowCompanyPause).where(
                WorkflowCompanyPause.workflow_id == enrollment.workflow_id,
                WorkflowCompanyPause.company_id == subject.company.id,
            )
        )
        if pause is None or (pause.until is not None and pause.until <= now):
            return False
        enrollment.status = EnrollmentStatus.WAITING
        enrollment.next_run_at = pause.until or now + COMPANY_PAUSE_RECHECK
        enrollment.updated_at = now
        return True

    async def _run_node(
        self,
        enrollment: Enrollment,
        node: GraphNode,
        subject: EnrollmentSubject,
        branch_count: int | None,
    ) -> tuple[NodeResult, ExecutionContext]:
        services = self._services
        context = ExecutionContext(
            tenant_id=enrollment.tenant_id,
            enrollment_id=enrollment.id,
            contact_id=enrollment.contact_id,
            node_id=node.id,
            now=services.clock.now(),
            facts=build_condition_facts(
                subject.contact, subject.company, enrollment.variables, branch_count=branch_count
            ),
            # Deep copy: nested JSON mutated in place would otherwise not be detected as a change.
            variables=copy.deepcopy(enrollment.variables),
            step_index=enrollment.step_count,
            services=services,
            subject=subject,
        )
        started = services.clock.now()
        result: NodeResult
        try:
            node_class = services.registry.get(node.type)
            config = node_class.parse_config(node.config)
            async with self._session.begin_nested():
                result = await node_class().execute(context, config)
        except Exception as exc:
            logger.exception("node_failed", enrollment_id=str(enrollment.id), node_id=node.id)
            result = Fail(error=f"{type(exc).__name__}: {exc}"[:1000], retryable=True)
            context.guardrail_decisions.clear()
        enrollment.variables = context.variables
        self._record_step(enrollment, node, result, context, started)
        return result, context

    def _record_step(
        self,
        enrollment: Enrollment,
        node: GraphNode,
        result: NodeResult,
        context: ExecutionContext,
        started: datetime,
    ) -> None:
        kind, handle, detail, wait_until, error = _describe(result)
        self._session.add(
            EnrollmentStep(
                tenant_id=require_session_tenant_id(self._session),
                enrollment_id=enrollment.id,
                step_index=enrollment.step_count,
                node_id=node.id,
                node_type=node.type,
                started_at=started,
                finished_at=self._services.clock.now(),
                result=kind,
                handle_taken=handle,
                detail=detail,
                wait_until=wait_until,
                guardrail_decisions=list(context.guardrail_decisions),
                output=context.output or None,
                error=error,
                idempotency_key=context.idempotency_key,
                created_at=started,
            )
        )
        enrollment.step_count += 1

    def _apply_result(
        self,
        enrollment: Enrollment,
        node: GraphNode,
        result: NodeResult,
        context: ExecutionContext,
        graph: _LoadedGraph,
    ) -> bool:
        """Update the enrollment for a node result. Returns whether to keep running."""
        now = self._services.clock.now()
        if isinstance(result, Continue):
            enrollment.attempts = 0
            enrollment.last_error = None
            target = graph.edges.get((node.id, result.handle))
            if target is None:
                self._finish(
                    enrollment, EnrollmentStatus.COMPLETED, "reached the end of the workflow"
                )
                return False
            enrollment.current_node_id = target
            enrollment.status = EnrollmentStatus.ACTIVE
            return True
        if isinstance(result, Wait):
            enrollment.attempts = 0
            enrollment.status = EnrollmentStatus.WAITING
            enrollment.next_run_at = result.until
            enrollment.resume_handle = result.handle
            return False
        if isinstance(result, Stop):
            status = EnrollmentStatus.COMPLETED if result.completed else EnrollmentStatus.STOPPED
            self._finish(enrollment, status, result.reason)
            return False
        enrollment.last_error = result.error
        if result.retryable and enrollment.attempts < len(RETRY_BACKOFF):
            enrollment.status = EnrollmentStatus.WAITING
            enrollment.next_run_at = now + RETRY_BACKOFF[enrollment.attempts]
            enrollment.resume_handle = None
            enrollment.attempts += 1
            return False
        self._finish(enrollment, EnrollmentStatus.FAILED, result.error)
        return False

    def _finish(self, enrollment: Enrollment, status: EnrollmentStatus, reason: str) -> None:
        now = self._services.clock.now()
        enrollment.status = status
        enrollment.stop_reason = reason
        enrollment.next_run_at = None
        enrollment.resume_handle = None
        enrollment.completed_at = now
        enrollment.updated_at = now


def _describe(
    result: NodeResult,
) -> tuple[StepResult, str | None, str | None, datetime | None, str | None]:
    if isinstance(result, Continue):
        return StepResult.CONTINUE, result.handle, None, None, None
    if isinstance(result, Wait):
        kind = StepResult.WAIT if result.handle is not None else StepResult.DEFER
        return kind, result.handle, result.reason, result.until, None
    if isinstance(result, Stop):
        return StepResult.STOP, None, result.reason, None, None
    return (
        StepResult.FAIL,
        None,
        "retryable" if result.retryable else "permanent",
        None,
        result.error,
    )
