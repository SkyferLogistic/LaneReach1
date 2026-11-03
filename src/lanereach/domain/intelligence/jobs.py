"""Daily sales-intelligence run for every tenant (Celery beat).

Per tenant: rescore open deals (no AI call), build last week's digest when missing (one AI call a
week, with an in-app notification), and suggest follow-ups for quiet deals (capped per run).
One failing tenant does not stop the others.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import text

from lanereach.ai.controls import AIBudgetGuard
from lanereach.ai.runs.service import SessionAIRunRecorder
from lanereach.ai.tasks.runtime import AIRuntime
from lanereach.core.container import ServiceContainer
from lanereach.core.tenancy import open_system_session, open_tenant_session
from lanereach.domain.intelligence.digest import DigestService
from lanereach.domain.intelligence.follow_ups import FollowUpService
from lanereach.domain.intelligence.scoring import LeadScoringService
from lanereach.integrations.notifications.notifier import NotificationMessage, RecordingNotifier

logger = structlog.get_logger(__name__)


async def run_sales_intelligence(container: ServiceContainer) -> dict[str, int]:
    """Rescore, digest and suggest follow-ups in every tenant.

    Returns:
        Totals: tenants, deals rescored, digests built, suggestions made, tenants that failed.
    """
    async with open_system_session(container.session_factory) as system:
        tenant_ids: list[uuid.UUID] = list(
            (await system.scalars(text("SELECT tenant_id FROM maintenance_list_tenants()"))).all()
        )
    totals = {
        "tenants": len(tenant_ids),
        "rescored": 0,
        "digests": 0,
        "suggestions": 0,
        "errors": 0,
    }
    settings = container.settings
    for tenant_id in tenant_ids:
        try:
            async with open_tenant_session(container.session_factory, tenant_id) as session:
                runtime = AIRuntime(
                    llm=container.llm,
                    recorder=SessionAIRunRecorder(session, tenant_id, container.clock, settings),
                    settings=settings,
                    budget=AIBudgetGuard.for_session(session, container.clock),
                )
                totals["rescored"] += await LeadScoringService(
                    session, container.clock
                ).rescore_open_deals()
                digest = await DigestService(
                    session, container.clock, runtime, quiet_days=settings.quiet_deal_days
                ).ensure_last_week()
                if digest is not None:
                    totals["digests"] += 1
                    await RecordingNotifier(session, container.clock).notify(
                        NotificationMessage(
                            channel="digest",
                            recipient="team",
                            subject=f"Weekly digest: {digest.headline}"[:300],
                            body=digest.summary,
                            payload={
                                "digest_id": str(digest.id),
                                "week_start": digest.week_start.isoformat(),
                            },
                        )
                    )
                totals["suggestions"] += await FollowUpService(
                    session, container.clock, runtime, quiet_days=settings.quiet_deal_days
                ).suggest_for_quiet_deals(settings.follow_up_suggestions_per_run)
        except Exception:
            logger.exception("sales_intelligence_failed", tenant_id=str(tenant_id))
            totals["errors"] += 1
    return totals
