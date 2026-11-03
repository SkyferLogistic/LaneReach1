"""Data retention: purge email bodies and AI outputs older than `DATA_RETENTION_DAYS`.

Emails are personal data. After the retention period LaneReach keeps the facts it needs for
reporting and guardrails (who was emailed when, from which office, the reply bucket, leads)
and removes the content: message and outbox bodies, AI run outputs, review payloads and the
pipeline's stored summaries. Rows are kept, so frequency caps and history still work. Open and
click events (who read what, when) are deleted; the per-message counters stay. Audit events are
kept for `AUDIT_RETENTION_DAYS` (two years) whatever the data retention period.

Runs daily from Celery beat, one tenant-scoped transaction per tenant.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

import structlog
from sqlalchemy import case, delete, func, null, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.ai.review.models import ReviewItem
from lanereach.ai.runs.models import AIRun
from lanereach.core.clock import Clock
from lanereach.core.container import ServiceContainer
from lanereach.core.tenancy import open_system_session, open_tenant_session
from lanereach.domain.audit.models import AuditEvent
from lanereach.domain.messages.models import Message
from lanereach.integrations.sending.models import OutboxMessage
from lanereach.integrations.tracking.models import EmailEvent

logger = structlog.get_logger(__name__)

PURGED_BODY = ""
AUDIT_RETENTION_DAYS = 730


@dataclass
class PurgeSummary:
    """Rows whose content was purged."""

    messages: int = 0
    outbox: int = 0
    ai_runs: int = 0
    review_items: int = 0
    email_events: int = 0
    audit_events: int = 0

    def add(self, other: PurgeSummary) -> None:
        """Accumulate another tenant's counts."""
        self.messages += other.messages
        self.outbox += other.outbox
        self.ai_runs += other.ai_runs
        self.review_items += other.review_items
        self.email_events += other.email_events
        self.audit_events += other.audit_events


@dataclass
class RetentionRun:
    """Result of purging every tenant."""

    tenants: int = 0
    purged: PurgeSummary = field(default_factory=PurgeSummary)
    errors: list[str] = field(default_factory=list)


def _count(result: object) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


async def purge_expired_personal_data(
    session: AsyncSession, clock: Clock, *, retention_days: int
) -> PurgeSummary:
    """Purge content older than the retention period in the session's tenant.

    Args:
        session: Tenant-scoped session.
        clock: Time source.
        retention_days: `DATA_RETENTION_DAYS`.

    Returns:
        Counts of purged rows.
    """
    now = clock.now()
    cutoff = now - timedelta(days=retention_days)
    bucket = Message.processing_result["classification"]["bucket"]
    messages = await session.execute(
        update(Message)
        .where(Message.occurred_at < cutoff, Message.body_purged_at.is_(None))
        .values(
            body=None,
            body_purged_at=now,
            # Keep only the bucket of a processed reply (reporting); drop summaries and drafts.
            processing_result=case(
                (Message.processing_result.is_(None), None),
                else_=func.jsonb_build_object("purged", True, "bucket", bucket),
            ),
        )
        .execution_options(synchronize_session=False)
    )
    outbox = await session.execute(
        update(OutboxMessage)
        .where(OutboxMessage.created_at < cutoff, OutboxMessage.body != PURGED_BODY)
        .values(body=PURGED_BODY, html_body=None)
        .execution_options(synchronize_session=False)
    )
    ai_runs = await session.execute(
        update(AIRun)
        .where(AIRun.created_at < cutoff, AIRun.output_json.is_not(None))
        .values(output_json=null())
        .execution_options(synchronize_session=False)
    )
    review_items = await session.execute(
        update(ReviewItem)
        .where(ReviewItem.created_at < cutoff, ReviewItem.payload["purged"].astext.is_(None))
        .values(payload=func.jsonb_build_object("purged", True))
        .execution_options(synchronize_session=False)
    )
    email_events = await session.execute(
        delete(EmailEvent)
        .where(EmailEvent.occurred_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    audit_events = await session.execute(
        delete(AuditEvent)
        .where(AuditEvent.occurred_at < now - timedelta(days=AUDIT_RETENTION_DAYS))
        .execution_options(synchronize_session=False)
    )
    summary = PurgeSummary(
        messages=_count(messages),
        outbox=_count(outbox),
        ai_runs=_count(ai_runs),
        review_items=_count(review_items),
        email_events=_count(email_events),
        audit_events=_count(audit_events),
    )
    logger.info("retention_purged", **summary.__dict__)
    return summary


async def purge_all_tenants(container: ServiceContainer) -> RetentionRun:
    """Purge expired content in every tenant (Celery beat, daily).

    Args:
        container: Application container.

    Returns:
        Totals and per-tenant errors (one failing tenant does not stop the others).
    """
    run = RetentionRun()
    async with open_system_session(container.session_factory) as system:
        tenant_ids: list[uuid.UUID] = list(
            (await system.scalars(text("SELECT tenant_id FROM maintenance_list_tenants()"))).all()
        )
    run.tenants = len(tenant_ids)
    for tenant_id in tenant_ids:
        try:
            async with open_tenant_session(container.session_factory, tenant_id) as session:
                run.purged.add(
                    await purge_expired_personal_data(
                        session,
                        container.clock,
                        retention_days=container.settings.data_retention_days,
                    )
                )
        except Exception as exc:
            logger.exception("retention_purge_failed", tenant_id=str(tenant_id))
            run.errors.append(f"{tenant_id}: {type(exc).__name__}")
    return run
