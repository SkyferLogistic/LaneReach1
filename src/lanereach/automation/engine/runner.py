"""Entry points for background engine work (Celery tasks and the inline queue call these).

Each unit of work opens its own tenant-scoped session: workers never share a session across
tenants, and they take the tenant from the job payload, exactly like API requests take it from
the principal.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.engine.executor import AdvanceOutcome, EnrollmentExecutor
from lanereach.automation.engine.queue import InlineTaskQueue, TaskQueue
from lanereach.automation.engine.services import EngineServices, build_engine_services
from lanereach.automation.enrollments.models import Enrollment, EnrollmentStatus
from lanereach.automation.events.scheduled import fire_scheduled_triggers
from lanereach.automation.events.service import EventProcessingOutcome, EventRepository
from lanereach.automation.events.trigger_matcher import process_event
from lanereach.automation.workflows.models import Workflow
from lanereach.core.clock import Clock
from lanereach.core.container import ServiceContainer
from lanereach.core.tenancy import open_system_session, open_tenant_session, run_after_commit
from lanereach.domain.messages.models import Message, MessageDirection

logger = structlog.get_logger(__name__)

type InboundHandler = Callable[[EngineServices, uuid.UUID], Awaitable[object]]

# Inbound messages are enqueued right after they are stored; the dispatcher only picks up
# the ones still unprocessed after this grace period (so it does not race the first attempt).
INBOUND_REDISPATCH_AFTER = timedelta(minutes=2)


@dataclass
class DispatchSummary:
    """What one dispatcher run found and handed off."""

    tenants: int = 0
    events: int = 0
    enrollments: int = 0
    scheduled_enrollments: int = 0
    inbound_messages: int = 0
    errors: list[str] = field(default_factory=list)


def build_engine_runner(
    container: ServiceContainer, *, inbound_handler: InboundHandler | None = None
) -> EngineRunner:
    """Create the runner and, for the inline queue, bind the queue to it.

    Args:
        container: Application container.
        inbound_handler: Inbound reply pipeline; defaults to `process_inbound_reply`.

    Returns:
        The runner.
    """
    if inbound_handler is None:
        # Imported here: the AI pipeline itself builds on the engine (email delivery, events).
        from lanereach.ai.pipeline import process_inbound_reply

        inbound_handler = process_inbound_reply
    runner = EngineRunner(container, container.task_queue, inbound_handler=inbound_handler)
    if isinstance(container.task_queue, InlineTaskQueue):
        container.task_queue.bind(
            process_event=runner.process_event,
            advance_enrollment=runner.advance_enrollment,
            process_inbound_message=runner.process_inbound_message,
        )
    return runner


class EngineRunner:
    """Runs engine work for one process.

    Args:
        container: Application container (session factory, settings, registry, clock).
        task_queue: Where follow-up work goes.
        inbound_handler: Inbound reply pipeline (registered by the AI module).
    """

    def __init__(
        self,
        container: ServiceContainer,
        task_queue: TaskQueue,
        *,
        inbound_handler: InboundHandler | None = None,
    ) -> None:
        self._container = container
        self._task_queue = task_queue
        self._inbound_handler = inbound_handler

    @property
    def container(self) -> ServiceContainer:
        """The application container this runner works with."""
        return self._container

    def services_for(self, session: AsyncSession, *, clock: Clock | None = None) -> EngineServices:
        """Engine services bound to a session.

        Args:
            session: Tenant session.
            clock: Override the clock (test-runs).

        Returns:
            The services.
        """
        container = self._container
        return build_engine_services(
            session=session,
            clock=clock or container.clock,
            settings=container.settings,
            registry=container.node_registry,
            task_queue=self._task_queue,
            teams_http_client=container.http_client,
            llm=container.llm,
        )

    async def advance_enrollment(
        self, tenant_id: uuid.UUID, enrollment_id: uuid.UUID
    ) -> AdvanceOutcome:
        """Advance one enrollment in its tenant.

        Args:
            tenant_id: Tenant from the job payload.
            enrollment_id: Enrollment to advance.

        Returns:
            What the tick did.
        """
        async with open_tenant_session(self._container.session_factory, tenant_id) as session:
            outcome = await EnrollmentExecutor(self.services_for(session)).advance_enrollment(
                enrollment_id
            )
            if outcome.advanced and outcome.status is EnrollmentStatus.ACTIVE:
                # The step budget ran out; continue right after this commit.
                queue = self._task_queue

                async def continue_later() -> None:
                    await queue.enqueue_advance(tenant_id, enrollment_id)

                run_after_commit(session, continue_later)
        return outcome

    async def process_event(
        self, tenant_id: uuid.UUID, event_id: uuid.UUID
    ) -> EventProcessingOutcome | None:
        """Match one event to triggers in its tenant.

        Args:
            tenant_id: Tenant from the job payload.
            event_id: Event to process.

        Returns:
            The outcome, or None if already processed.
        """
        async with open_tenant_session(self._container.session_factory, tenant_id) as session:
            return await process_event(self.services_for(session), event_id)

    async def process_inbound_message(self, tenant_id: uuid.UUID, message_id: uuid.UUID) -> object:
        """Run the inbound reply pipeline for one message.

        Args:
            tenant_id: Tenant from the job payload.
            message_id: Stored inbound message.

        Returns:
            The pipeline result.

        Raises:
            RuntimeError: If no pipeline is registered.
        """
        if self._inbound_handler is None:
            raise RuntimeError("the inbound reply pipeline is not registered")
        async with open_tenant_session(self._container.session_factory, tenant_id) as session:
            return await self._inbound_handler(self.services_for(session), message_id)

    async def dispatch_due_work(
        self, *, only_tenant_id: uuid.UUID | None = None
    ) -> DispatchSummary:
        """Find due work in every tenant and hand it to the task queue.

        Called by Celery beat every `ENGINE_DISPATCH_INTERVAL_SECONDS`. It is the safety net
        behind after-commit enqueueing: anything never enqueued is found here.

        Args:
            only_tenant_id: Restrict to one tenant (the API's "run now" button must never touch
                or reveal other tenants).

        Returns:
            Counts of what was dispatched.
        """
        container = self._container
        now = container.clock.now()
        summary = DispatchSummary()
        async with open_system_session(container.session_factory) as system:
            tenant_ids = list(
                (
                    await system.scalars(
                        text("SELECT tenant_id FROM automation_tenants_with_work(:now)"),
                        {"now": now},
                    )
                ).all()
            )
        if only_tenant_id is not None:
            tenant_ids = [tenant for tenant in tenant_ids if tenant == only_tenant_id]
        summary.tenants = len(tenant_ids)
        batch = container.settings.engine_batch_size
        for tenant_id in tenant_ids:
            try:
                async with open_tenant_session(container.session_factory, tenant_id) as session:
                    event_ids = await EventRepository(session).list_pending_ids(batch)
                    enrollment_ids = list(
                        (
                            await session.scalars(
                                select(Enrollment.id)
                                .join(Workflow, Workflow.id == Enrollment.workflow_id)
                                .where(
                                    Enrollment.status.in_(
                                        (EnrollmentStatus.ACTIVE, EnrollmentStatus.WAITING)
                                    ),
                                    Enrollment.next_run_at <= now,
                                    Workflow.paused_at.is_(None),
                                )
                                .order_by(Enrollment.next_run_at)
                                .limit(batch)
                            )
                        ).all()
                    )
                    summary.scheduled_enrollments += await fire_scheduled_triggers(
                        self.services_for(session)
                    )
                    inbound_ids = list(
                        (
                            await session.scalars(
                                select(Message.id)
                                .where(
                                    Message.direction == MessageDirection.INBOUND,
                                    Message.processed_at.is_(None),
                                    Message.created_at <= now - INBOUND_REDISPATCH_AFTER,
                                )
                                .order_by(Message.created_at)
                                .limit(batch)
                            )
                        ).all()
                    )
                for event_id in event_ids:
                    await self._task_queue.enqueue_event(tenant_id, event_id)
                for enrollment_id in enrollment_ids:
                    await self._task_queue.enqueue_advance(tenant_id, enrollment_id)
                for message_id in inbound_ids:
                    await self._task_queue.enqueue_inbound_message(tenant_id, message_id)
                summary.inbound_messages += len(inbound_ids)
                summary.events += len(event_ids)
                summary.enrollments += len(enrollment_ids)
            except Exception as exc:
                logger.exception("dispatch_failed", tenant_id=str(tenant_id))
                summary.errors.append(f"{tenant_id}: {type(exc).__name__}")
        return summary
