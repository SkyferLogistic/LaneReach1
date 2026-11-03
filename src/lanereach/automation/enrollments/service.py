"""Enrolling contacts and managing enrollments."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.engine.queue import TaskQueue
from lanereach.automation.enrollments.models import (
    FINAL_ENROLLMENT_STATUSES,
    OPEN_ENROLLMENT_STATUSES,
    Enrollment,
    EnrollmentStatus,
    EnrollmentStep,
)
from lanereach.automation.workflows.graph import WorkflowGraph
from lanereach.automation.workflows.models import Workflow, WorkflowStatus, WorkflowVersion
from lanereach.core.clock import Clock
from lanereach.core.errors import BusinessRuleViolationError, ConflictError, NotFoundError
from lanereach.core.repository import TenantRepository
from lanereach.core.tenancy import require_session_tenant_id, run_after_commit
from lanereach.domain.contacts.models import Contact
from lanereach.domain.messages.models import Message

logger = structlog.get_logger(__name__)


class EnrollmentSkipReason(StrEnum):
    """Why a contact was not enrolled."""

    ALREADY_ENROLLED = "already_enrolled"
    WORKFLOW_NOT_PUBLISHED = "workflow_not_published"
    WORKFLOW_PAUSED = "workflow_paused"
    CONTACT_NOT_FOUND = "contact_not_found"


@dataclass(frozen=True)
class EnrollmentAttempt:
    """Result of trying to enroll one contact."""

    contact_id: uuid.UUID
    enrollment: Enrollment | None
    skipped: EnrollmentSkipReason | None = None


async def enroll_contact_in_workflow(
    session: AsyncSession,
    clock: Clock,
    *,
    workflow: Workflow,
    contact: Contact,
    enrolled_by: str,
    trigger_event_id: uuid.UUID | None = None,
    variables: dict[str, Any] | None = None,
    task_queue: TaskQueue | None = None,
    require_published: bool = True,
    version: int | None = None,
) -> EnrollmentAttempt:
    """Start a contact on a workflow's current version.

    A contact never has two open enrollments in the same workflow: the partial unique index
    makes the insert a no-op when one exists, which is also race-free under concurrency.

    Args:
        session: Tenant session.
        clock: Time source.
        workflow: The workflow.
        contact: The contact.
        enrolled_by: Who or what enrolled them (`event:<id>`, a user subject, ...).
        trigger_event_id: The event that caused it, if any.
        variables: Initial enrollment variables (for example the classified reply).
        task_queue: Where to enqueue the first advance after commit.
        require_published: False only for test-runs of draft versions.
        version: Version to run (defaults to the published version).

    Returns:
        The attempt (with the enrollment, or why it was skipped).
    """
    if require_published and workflow.status is not WorkflowStatus.PUBLISHED:
        return EnrollmentAttempt(contact.id, None, EnrollmentSkipReason.WORKFLOW_NOT_PUBLISHED)
    if require_published and workflow.paused_at is not None:
        return EnrollmentAttempt(contact.id, None, EnrollmentSkipReason.WORKFLOW_PAUSED)
    run_version = version or workflow.current_version
    if run_version is None:
        return EnrollmentAttempt(contact.id, None, EnrollmentSkipReason.WORKFLOW_NOT_PUBLISHED)
    stored = await session.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow.id, WorkflowVersion.version == run_version
        )
    )
    assert stored is not None  # noqa: S101 - versions are never deleted
    trigger_id = WorkflowGraph.model_validate(stored.graph).trigger.id

    now = clock.now()
    tenant_id = require_session_tenant_id(session)
    enrollment_id = await session.scalar(
        insert(Enrollment)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            workflow_id=workflow.id,
            workflow_version=run_version,
            contact_id=contact.id,
            status=EnrollmentStatus.ACTIVE,
            current_node_id=trigger_id,
            next_run_at=now,
            variables=variables or {},
            attempts=0,
            step_count=0,
            trigger_event_id=trigger_event_id,
            enrolled_by=enrolled_by,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "workflow_id", "contact_id"],
            index_where=text("status IN ('active', 'waiting', 'paused')"),
        )
        .returning(Enrollment.id)
    )
    if enrollment_id is None:
        return EnrollmentAttempt(contact.id, None, EnrollmentSkipReason.ALREADY_ENROLLED)
    enrollment = await session.get(Enrollment, enrollment_id)
    assert enrollment is not None  # noqa: S101 - just inserted
    if task_queue is not None:

        async def enqueue() -> None:
            await task_queue.enqueue_advance(tenant_id, enrollment_id)

        run_after_commit(session, enqueue)
    logger.info(
        "contact_enrolled",
        enrollment_id=str(enrollment_id),
        workflow_id=str(workflow.id),
        version=run_version,
    )
    return EnrollmentAttempt(contact.id, enrollment)


class EnrollmentRepository(TenantRepository[Enrollment]):
    """Queries over enrollments and their steps."""

    model = Enrollment

    async def list_enrollments(
        self,
        *,
        workflow_id: uuid.UUID | None,
        contact_id: uuid.UUID | None,
        status: EnrollmentStatus | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[Sequence[Enrollment], str | None]:
        """List enrollments newest first, optionally filtered.

        Args:
            workflow_id: Workflow filter.
            contact_id: Contact filter.
            status: Status filter.
            limit: Page size.
            cursor: Cursor from the previous page.

        Returns:
            The page and the next cursor.
        """
        statement = select(Enrollment)
        if workflow_id is not None:
            statement = statement.where(Enrollment.workflow_id == workflow_id)
        if contact_id is not None:
            statement = statement.where(Enrollment.contact_id == contact_id)
        if status is not None:
            statement = statement.where(Enrollment.status == status)
        return await self.page(statement, limit=limit, cursor=cursor)

    async def list_steps(self, enrollment_id: uuid.UUID) -> Sequence[EnrollmentStep]:
        """Every step of an enrollment, in order.

        Args:
            enrollment_id: Enrollment ID.

        Returns:
            The steps.
        """
        rows = await self.session.scalars(
            select(EnrollmentStep)
            .where(EnrollmentStep.enrollment_id == enrollment_id)
            .order_by(EnrollmentStep.step_index)
        )
        return rows.all()

    async def list_open_for_contact(self, contact_id: uuid.UUID) -> Sequence[Enrollment]:
        """Open enrollments of a contact (locked, for stop-on-reply).

        Args:
            contact_id: Contact ID.

        Returns:
            The enrollments.
        """
        rows = await self.session.scalars(
            select(Enrollment)
            .where(
                Enrollment.contact_id == contact_id,
                Enrollment.status.in_(OPEN_ENROLLMENT_STATUSES),
            )
            .with_for_update()
        )
        return rows.all()


class EnrollmentService:
    """Enrollment operations for the API.

    Args:
        session: Tenant session.
        clock: Time source.
        task_queue: Where to enqueue advances after commit.
    """

    def __init__(self, session: AsyncSession, clock: Clock, task_queue: TaskQueue | None) -> None:
        self._session = session
        self._clock = clock
        self._task_queue = task_queue
        self.repository = EnrollmentRepository(session)

    async def enroll_contacts(
        self, workflow_id: uuid.UUID, contact_ids: list[uuid.UUID], enrolled_by: str
    ) -> list[EnrollmentAttempt]:
        """Manually enroll contacts in a published workflow.

        Args:
            workflow_id: Workflow ID.
            contact_ids: Contacts to enroll.
            enrolled_by: The caller's subject.

        Returns:
            One attempt per contact.

        Raises:
            NotFoundError: If the workflow does not exist.
        """
        workflow = await self._session.get(Workflow, workflow_id)
        if workflow is None:
            raise NotFoundError("Workflow not found.")
        attempts: list[EnrollmentAttempt] = []
        for contact_id in dict.fromkeys(contact_ids):
            contact = await self._session.get(Contact, contact_id)
            if contact is None:
                attempts.append(
                    EnrollmentAttempt(contact_id, None, EnrollmentSkipReason.CONTACT_NOT_FOUND)
                )
                continue
            attempts.append(
                await enroll_contact_in_workflow(
                    self._session,
                    self._clock,
                    workflow=workflow,
                    contact=contact,
                    enrolled_by=enrolled_by,
                    task_queue=self._task_queue,
                )
            )
        return attempts

    async def require_enrollment(
        self, enrollment_id: uuid.UUID, *, for_update: bool = False
    ) -> Enrollment:
        """Load an enrollment or raise 404.

        Args:
            enrollment_id: Enrollment ID.
            for_update: Lock the row.

        Returns:
            The enrollment.

        Raises:
            NotFoundError: If it does not exist in the caller's tenant.
        """
        enrollment = await self.repository.get(enrollment_id, for_update=for_update)
        if enrollment is None:
            raise NotFoundError("Enrollment not found.")
        return enrollment

    async def pause_enrollment(self, enrollment_id: uuid.UUID) -> Enrollment:
        """Pause an open enrollment (keeps its place and wake-up time).

        Args:
            enrollment_id: Enrollment ID.

        Returns:
            The enrollment.

        Raises:
            NotFoundError: If missing.
            BusinessRuleViolationError: If the enrollment already finished.
        """
        enrollment = await self.require_enrollment(enrollment_id, for_update=True)
        self._ensure_open(enrollment)
        if enrollment.status is not EnrollmentStatus.PAUSED:
            enrollment.status = EnrollmentStatus.PAUSED
            enrollment.updated_at = self._clock.now()
        return enrollment

    async def resume_enrollment(self, enrollment_id: uuid.UUID) -> Enrollment:
        """Resume a paused enrollment; it continues when due.

        Args:
            enrollment_id: Enrollment ID.

        Returns:
            The enrollment.

        Raises:
            NotFoundError: If missing.
            BusinessRuleViolationError: If the enrollment already finished.
        """
        enrollment = await self.require_enrollment(enrollment_id, for_update=True)
        self._ensure_open(enrollment)
        if enrollment.status is EnrollmentStatus.PAUSED:
            now = self._clock.now()
            # A pause never moves the wake-up time, so the right status follows from it.
            is_waiting = enrollment.next_run_at is not None and enrollment.next_run_at > now
            enrollment.status = EnrollmentStatus.WAITING if is_waiting else EnrollmentStatus.ACTIVE
            enrollment.updated_at = now
            self._enqueue_advance(enrollment)
        return enrollment

    async def cancel_enrollment(self, enrollment_id: uuid.UUID, reason: str) -> Enrollment:
        """Stop an open enrollment for good.

        Args:
            enrollment_id: Enrollment ID.
            reason: Recorded stop reason.

        Returns:
            The enrollment.

        Raises:
            NotFoundError: If missing.
            BusinessRuleViolationError: If the enrollment already finished.
        """
        enrollment = await self.require_enrollment(enrollment_id, for_update=True)
        self._ensure_open(enrollment)
        stop_enrollment(enrollment, reason, self._clock)
        return enrollment

    async def delete_enrollment(self, enrollment_id: uuid.UUID) -> None:
        """Delete a finished enrollment that sent no email (its steps go with it).

        Raises:
            NotFoundError: If missing.
            ConflictError: While it is open, or when it sent email (campaign results and the
                inbox need that history).
        """
        enrollment = await self.require_enrollment(enrollment_id, for_update=True)
        if enrollment.status in OPEN_ENROLLMENT_STATUSES:
            raise ConflictError("Cancel the enrollment before deleting it.")
        sent = await self._session.scalar(
            select(Message.id).where(Message.enrollment_id == enrollment.id).limit(1)
        )
        if sent is not None:
            raise ConflictError(
                "This enrollment sent email; it stays for the campaign's results and the "
                "conversation history. Erase the contact to remove everything about them."
            )
        await self._session.delete(enrollment)
        await self._session.flush()

    async def wake_enrollment(self, enrollment_id: uuid.UUID) -> Enrollment:
        """Make a waiting enrollment due now (testing aid: skips the remaining wait).

        Guardrails still apply when the next email is attempted.

        Args:
            enrollment_id: Enrollment ID.

        Returns:
            The enrollment.

        Raises:
            NotFoundError: If missing.
            BusinessRuleViolationError: If the enrollment is not waiting or active.
        """
        enrollment = await self.require_enrollment(enrollment_id, for_update=True)
        if enrollment.status not in (EnrollmentStatus.WAITING, EnrollmentStatus.ACTIVE):
            raise BusinessRuleViolationError("Only waiting or active enrollments can be woken.")
        enrollment.next_run_at = self._clock.now()
        enrollment.updated_at = enrollment.next_run_at
        self._enqueue_advance(enrollment)
        return enrollment

    def _enqueue_advance(self, enrollment: Enrollment) -> None:
        if self._task_queue is None:
            return
        queue, tenant_id, enrollment_id = self._task_queue, enrollment.tenant_id, enrollment.id

        async def enqueue() -> None:
            await queue.enqueue_advance(tenant_id, enrollment_id)

        run_after_commit(self._session, enqueue)

    @staticmethod
    def _ensure_open(enrollment: Enrollment) -> None:
        if enrollment.status in FINAL_ENROLLMENT_STATUSES:
            raise BusinessRuleViolationError(f"The enrollment is already {enrollment.status}.")


def stop_enrollment(enrollment: Enrollment, reason: str, clock: Clock) -> None:
    """Mark an enrollment stopped.

    Args:
        enrollment: The (locked) enrollment.
        reason: Why.
        clock: Time source.
    """
    now = clock.now()
    enrollment.status = EnrollmentStatus.STOPPED
    enrollment.stop_reason = reason
    enrollment.next_run_at = None
    enrollment.completed_at = now
    enrollment.updated_at = now
