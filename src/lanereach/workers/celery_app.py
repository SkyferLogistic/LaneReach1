"""Celery application (worker + beat).

Tasks receive a `tenant_id` in their payload and open a tenant-scoped session with
`open_tenant_session` before touching data, exactly like API requests do. Beat runs the
dispatcher every `ENGINE_DISPATCH_INTERVAL_SECONDS` (default 30).

Start with ``celery -A lanereach.workers.celery_app worker`` / ``... beat``.
"""

from __future__ import annotations

from celery import Celery

from lanereach.core.config import get_settings
from lanereach.core.model_registry import load_all_models


def create_celery_app() -> Celery:
    """Build the Celery application from settings.

    Returns:
        The configured Celery app.
    """
    settings = get_settings()
    load_all_models()
    app = Celery("lanereach", include=["lanereach.workers.tasks"])
    app.conf.update(
        broker_url=settings.celery_broker_url.get_secret_value(),
        result_backend=None,
        task_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # A task is acknowledged only after it finishes, so a crashed worker's task is retried.
        # Every task must therefore be idempotent.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        # The dispatcher is the safety net behind after-commit enqueueing (see runner.py).
        beat_schedule={
            "dispatch-due-work": {
                "task": "lanereach.dispatch_due_work",
                "schedule": settings.engine_dispatch_interval_seconds,
            },
            "purge-expired-data": {
                "task": "lanereach.purge_expired_data",
                "schedule": settings.retention_purge_interval_seconds,
            },
            "deliver-integrations": {
                "task": "lanereach.deliver_integrations",
                "schedule": settings.integration_delivery_interval_seconds,
            },
            "expire-quotes": {
                "task": "lanereach.expire_quotes",
                "schedule": 3600,
            },
            "sync-script-libraries": {
                "task": "lanereach.sync_script_libraries",
                "schedule": settings.script_sync_interval_seconds,
            },
            # Real sending only (SENDING_PROVIDER=smtp): in dry-run mode nothing is queued.
            "deliver-outbox": {
                "task": "lanereach.deliver_outbox",
                "schedule": settings.outbox_delivery_interval_seconds,
            },
            "poll-mailboxes": {
                "task": "lanereach.poll_mailboxes",
                "schedule": settings.mailbox_poll_interval_seconds,
            },
            "sync-help-index": {
                "task": "lanereach.sync_help_index",
                "schedule": 24 * 3600,
            },
            # Lead rescoring, last week's digest when missing, follow-ups for quiet deals.
            "sales-intelligence": {
                "task": "lanereach.sales_intelligence",
                "schedule": 24 * 3600,
            },
        },
    )
    return app


celery_app = create_celery_app()
