"""Workflow test-runs: simulate one contact through a workflow version, persisting nothing.

The simulation uses the real executor, nodes and guardrails inside a database savepoint that is
always rolled back, with a `ManualClock` that jumps to each wait's wake-up time. The dry-run
provider and recording notifier write into the same savepoint, so the emails and
notifications a real run would produce can be shown and then disappear.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.engine.executor import EnrollmentExecutor
from lanereach.automation.engine.services import EngineServices
from lanereach.automation.enrollments.models import (
    FINAL_ENROLLMENT_STATUSES,
    OPEN_ENROLLMENT_STATUSES,
    Enrollment,
    EnrollmentStatus,
    EnrollmentStep,
)
from lanereach.automation.enrollments.service import enroll_contact_in_workflow
from lanereach.automation.workflows.models import Workflow
from lanereach.core.clock import ManualClock
from lanereach.domain.contacts.models import Contact
from lanereach.integrations.notifications.models import Notification
from lanereach.integrations.sending.models import OutboxMessage

MAX_SIMULATED_DAYS = 400


@dataclass(frozen=True)
class SimulatedStep:
    """One step of the simulated path."""

    step_index: int
    node_id: str
    node_type: str
    at: datetime
    result: str
    handle: str | None
    detail: str | None
    wait_until: datetime | None
    guardrail_decisions: list[dict[str, Any]]
    output: dict[str, Any] | None
    error: str | None


@dataclass(frozen=True)
class SimulatedEmail:
    """An email the run would have sent."""

    at: datetime
    kind: str
    from_email: str
    from_name: str
    to_email: str
    subject: str
    body: str
    headers: dict[str, Any]


@dataclass(frozen=True)
class SimulatedNotification:
    """A notification the run would have delivered."""

    at: datetime
    channel: str
    recipient: str
    subject: str
    body: str


@dataclass
class TestRunResult:
    """The simulated path, emails and notifications."""

    status: EnrollmentStatus
    stop_reason: str | None
    started_at: datetime
    finished_at: datetime
    truncated: bool
    steps: list[SimulatedStep] = field(default_factory=list)
    emails: list[SimulatedEmail] = field(default_factory=list)
    notifications: list[SimulatedNotification] = field(default_factory=list)


async def simulate_workflow_run(
    session: AsyncSession,
    build_services: Any,
    *,
    workflow: Workflow,
    version: int,
    contact: Contact,
    start_at: datetime,
    variables: dict[str, Any],
    max_ticks: int = 60,
) -> TestRunResult:
    """Run `contact` through `version` of `workflow` in a rolled-back savepoint.

    Args:
        session: Tenant session of the request.
        build_services: `Callable[[AsyncSession, ManualClock], EngineServices]` creating
            services without a task queue (nothing may leave the simulation).
        workflow: The workflow (drafts and paused workflows can be tested).
        version: Version to simulate.
        contact: The contact to simulate (a real one; its history feeds the guardrails).
        start_at: Simulated start time.
        variables: Initial enrollment variables (for example a simulated classified reply).
        max_ticks: Upper bound on executor ticks.

    Returns:
        The simulated result.
    """
    clock = ManualClock(start_at)
    savepoint = await session.begin_nested()
    try:
        services: EngineServices = build_services(session, clock)
        workflow.paused_at = None
        for open_enrollment in (
            await session.scalars(
                select(Enrollment).where(
                    Enrollment.workflow_id == workflow.id,
                    Enrollment.contact_id == contact.id,
                    Enrollment.status.in_(OPEN_ENROLLMENT_STATUSES),
                )
            )
        ).all():
            open_enrollment.status = EnrollmentStatus.STOPPED
        await session.flush()
        attempt = await enroll_contact_in_workflow(
            session,
            clock,
            workflow=workflow,
            contact=contact,
            enrolled_by="test-run",
            variables=variables,
            require_published=False,
            version=version,
        )
        assert attempt.enrollment is not None  # noqa: S101 - open enrollments were stopped
        enrollment = attempt.enrollment
        executor = EnrollmentExecutor(services)
        deadline = start_at + timedelta(days=MAX_SIMULATED_DAYS)
        truncated = True
        for _ in range(max_ticks):
            await executor.advance_enrollment(enrollment.id)
            # Flush, never refresh: refresh() would discard the executor's unflushed changes.
            await session.flush()
            if enrollment.status in FINAL_ENROLLMENT_STATUSES:
                truncated = False
                break
            if enrollment.status is EnrollmentStatus.WAITING and enrollment.next_run_at:
                if enrollment.next_run_at > deadline:
                    break
                clock.set(max(clock.now(), enrollment.next_run_at))
        return TestRunResult(
            status=enrollment.status,
            stop_reason=enrollment.stop_reason,
            started_at=start_at,
            finished_at=clock.now(),
            truncated=truncated,
            steps=await _collect_steps(session, enrollment.id),
            emails=await _collect_emails(session, enrollment.id),
            notifications=await _collect_notifications(session, enrollment.id),
        )
    finally:
        await savepoint.rollback()


async def _collect_steps(session: AsyncSession, enrollment_id: uuid.UUID) -> list[SimulatedStep]:
    rows = await session.scalars(
        select(EnrollmentStep)
        .where(EnrollmentStep.enrollment_id == enrollment_id)
        .order_by(EnrollmentStep.step_index)
    )
    return [
        SimulatedStep(
            step_index=row.step_index,
            node_id=row.node_id,
            node_type=row.node_type,
            at=row.started_at,
            result=row.result.value,
            handle=row.handle_taken,
            detail=row.detail,
            wait_until=row.wait_until,
            guardrail_decisions=list(row.guardrail_decisions or []),
            output=dict(row.output) if row.output else None,
            error=row.error,
        )
        for row in rows
    ]


async def _collect_emails(session: AsyncSession, enrollment_id: uuid.UUID) -> list[SimulatedEmail]:
    rows = await session.scalars(
        select(OutboxMessage)
        .where(OutboxMessage.enrollment_id == enrollment_id)
        .order_by(OutboxMessage.created_at)
    )
    return [
        SimulatedEmail(
            at=row.created_at,
            kind=row.kind,
            from_email=row.from_email,
            from_name=row.from_name,
            to_email=row.to_email,
            subject=row.subject,
            body=row.body,
            headers=dict(row.headers),
        )
        for row in rows
    ]


async def _collect_notifications(
    session: AsyncSession, enrollment_id: uuid.UUID
) -> list[SimulatedNotification]:
    rows = await session.scalars(
        select(Notification)
        .where(Notification.enrollment_id == enrollment_id)
        .order_by(Notification.created_at)
    )
    return [
        SimulatedNotification(row.created_at, row.channel, row.recipient, row.subject, row.body)
        for row in rows
    ]
