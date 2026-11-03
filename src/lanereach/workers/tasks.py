"""Celery tasks. Each task runs one unit of async engine work in a fresh event loop.

Tasks take the tenant from their payload and open a tenant-scoped session through the runner,
exactly like API requests. `acks_late` is on, so every task is idempotent: re-running a
processed event or an advanced enrollment is a no-op.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from lanereach.automation.engine.queue import ADVANCE_TASK, EVENT_TASK, INBOUND_TASK
from lanereach.automation.engine.runner import EngineRunner, build_engine_runner
from lanereach.core.config import get_settings
from lanereach.core.container import build_service_container
from lanereach.core.logging import configure_logging
from lanereach.workers.celery_app import celery_app

DISPATCH_TASK = "lanereach.dispatch_due_work"
RETENTION_TASK = "lanereach.purge_expired_data"


def run_engine_work(work: Callable[[EngineRunner], Awaitable[Any]]) -> Any:
    """Run async engine work with a container scoped to this task.

    The container (engine, Redis, HTTP client) is created and disposed inside the task's own
    event loop: asyncpg connections cannot be shared across loops.

    Args:
        work: Receives the runner and performs the work.

    Returns:
        Whatever `work` returns.
    """

    async def main() -> Any:
        settings = get_settings()
        configure_logging(level=settings.log_level, json_output=settings.log_json)
        container = build_service_container(settings)
        try:
            return await work(build_engine_runner(container))
        finally:
            await container.aclose()

    return asyncio.run(main())


@celery_app.task(name="lanereach.check_worker_health")
def check_worker_health() -> str:
    """Return a constant so operators can verify a worker consumes the queue.

    Returns:
        The string `"ok"`.
    """
    return "ok"


@celery_app.task(name=EVENT_TASK)
def process_event_task(tenant_id: str, event_id: str) -> str:
    """Match an event to triggers.

    Args:
        tenant_id: Tenant of the event.
        event_id: The event.

    Returns:
        The resulting status.
    """
    outcome = run_engine_work(
        lambda runner: runner.process_event(uuid.UUID(tenant_id), uuid.UUID(event_id))
    )
    return "skipped" if outcome is None else str(outcome.status)


@celery_app.task(name=ADVANCE_TASK)
def advance_enrollment_task(tenant_id: str, enrollment_id: str) -> str:
    """Advance an enrollment.

    Args:
        tenant_id: Tenant of the enrollment.
        enrollment_id: The enrollment.

    Returns:
        The resulting status.
    """
    outcome = run_engine_work(
        lambda runner: runner.advance_enrollment(uuid.UUID(tenant_id), uuid.UUID(enrollment_id))
    )
    return str(outcome.status)


@celery_app.task(name=INBOUND_TASK)
def process_inbound_message_task(tenant_id: str, message_id: str) -> str:
    """Run the inbound reply pipeline.

    Args:
        tenant_id: Tenant of the message.
        message_id: The stored inbound message.

    Returns:
        A short outcome description.
    """
    run_engine_work(
        lambda runner: runner.process_inbound_message(uuid.UUID(tenant_id), uuid.UUID(message_id))
    )
    return "processed"


@celery_app.task(name=DISPATCH_TASK)
def dispatch_due_work_task() -> dict[str, int]:
    """Hand due events and enrollments to the queue (Celery beat).

    Returns:
        Counts of what was dispatched.
    """
    summary = run_engine_work(lambda runner: runner.dispatch_due_work())
    return {
        "tenants": summary.tenants,
        "events": summary.events,
        "enrollments": summary.enrollments,
        "scheduled_enrollments": summary.scheduled_enrollments,
        "inbound_messages": summary.inbound_messages,
    }


@celery_app.task(name=RETENTION_TASK)
def purge_expired_data_task() -> dict[str, int]:
    """Purge email bodies and AI outputs past the retention period (Celery beat, daily).

    Returns:
        Counts of purged rows.
    """
    from lanereach.maintenance.retention import purge_all_tenants

    run = run_engine_work(lambda runner: purge_all_tenants(runner.container))
    return {"tenants": run.tenants, **run.purged.__dict__, "errors": len(run.errors)}


SCRIPT_SYNC_TASK = "lanereach.sync_script_libraries"


@celery_app.task(name=SCRIPT_SYNC_TASK)
def sync_script_libraries_task() -> dict[str, int]:
    """Import script and banner changes from every tenant's library (Celery beat).

    Returns:
        Tenants synced, scripts imported and tenants that failed.
    """
    from lanereach.domain.scripts.sync import sync_all_script_libraries

    run = run_engine_work(lambda runner: sync_all_script_libraries(runner.container))
    return {"tenants": run.tenants, "imported": run.imported, "errors": len(run.errors)}


DELIVERY_TASK = "lanereach.deliver_integrations"


@celery_app.task(name=DELIVERY_TASK)
def deliver_integrations_task() -> dict[str, int]:
    """Send due webhook and Slack deliveries in every tenant (Celery beat).

    Returns:
        Delivered, retried and dead deliveries, and tenants that failed.
    """
    from lanereach.integrations.outbound.service import deliver_all_tenants

    run = run_engine_work(lambda runner: deliver_all_tenants(runner.container))
    return {
        "tenants": run.tenants,
        "delivered": run.delivered,
        "retried": run.retried,
        "dead": run.dead,
        "errors": len(run.errors),
    }


QUOTE_EXPIRY_TASK = "lanereach.expire_quotes"


@celery_app.task(name=QUOTE_EXPIRY_TASK)
def expire_quotes_task() -> dict[str, int]:
    """Mark sent quotes past their validity as expired (Celery beat, hourly).

    Returns:
        Tenants processed, quotes expired and tenants that failed.
    """
    from lanereach.domain.quotes.service import expire_quotes_in_all_tenants

    return run_engine_work(lambda runner: expire_quotes_in_all_tenants(runner.container))  # type: ignore[no-any-return]


OUTBOX_DELIVERY_TASK = "lanereach.deliver_outbox"
MAILBOX_POLL_TASK = "lanereach.poll_mailboxes"


@celery_app.task(name=OUTBOX_DELIVERY_TASK)
def deliver_outbox_task() -> dict[str, int]:
    """Send queued emails through the agents' mailboxes (Celery beat, SMTP mode).

    Returns:
        Sent, retried and failed emails, and tenants that failed.
    """
    from lanereach.integrations.mailboxes.service import deliver_outbox_in_all_tenants

    return run_engine_work(lambda runner: deliver_outbox_in_all_tenants(runner.container))  # type: ignore[no-any-return]


@celery_app.task(name=MAILBOX_POLL_TASK)
def poll_mailboxes_task() -> dict[str, int]:
    """Read new replies and bounces from the agents' mailboxes (Celery beat).

    Returns:
        Mailboxes polled, messages stored and skipped, and errors.
    """
    from lanereach.integrations.mailboxes.service import poll_mailboxes_in_all_tenants

    return run_engine_work(lambda runner: poll_mailboxes_in_all_tenants(runner.container))  # type: ignore[no-any-return]


HELP_INDEX_TASK = "lanereach.sync_help_index"


@celery_app.task(name=HELP_INDEX_TASK)
def sync_help_index_task() -> dict[str, int]:
    """Refresh every tenant's help search index (Celery beat, daily).

    Returns:
        Tenants, changed sections, embedded sections and tenants that failed.
    """
    from lanereach.assistant.index import sync_help_index_in_all_tenants

    return run_engine_work(lambda runner: sync_help_index_in_all_tenants(runner.container))  # type: ignore[no-any-return]


SALES_INTELLIGENCE_TASK = "lanereach.sales_intelligence"


@celery_app.task(name=SALES_INTELLIGENCE_TASK)
def sales_intelligence_task() -> dict[str, int]:
    """Rescore deals, build last week's digest and suggest follow-ups (Celery beat, daily).

    Returns:
        Totals of the run.
    """
    from lanereach.domain.intelligence.jobs import run_sales_intelligence

    return run_engine_work(lambda runner: run_sales_intelligence(runner.container))  # type: ignore[no-any-return]
