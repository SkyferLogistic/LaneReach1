"""Firing `trigger_scheduled_date` triggers."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from lanereach.automation.conditions import evaluate_condition
from lanereach.automation.engine.facts import build_condition_facts
from lanereach.automation.engine.services import EngineServices
from lanereach.automation.enrollments.service import enroll_contact_in_workflow
from lanereach.automation.events.models import ScheduledTriggerRun
from lanereach.automation.events.trigger_matcher import load_live_workflows
from lanereach.automation.nodes.triggers.catalog import ScheduledDateConfig
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.companies.models import Company
from lanereach.domain.contacts.models import Contact

logger = structlog.get_logger(__name__)
SCHEDULED_TRIGGER = "trigger_scheduled_date"


async def fire_scheduled_triggers(services: EngineServices) -> int:
    """Enroll the audience of every scheduled trigger whose time has come.

    Each (workflow, version) fires once: the run is claimed with an insert on a unique key
    before enrolling, so concurrent dispatchers cannot fire it twice.

    Args:
        services: Engine services.

    Returns:
        Number of contacts enrolled.
    """
    session = services.session
    now = services.clock.now()
    enrolled = 0
    for workflow, graph in await load_live_workflows(services):
        if graph.trigger.type != SCHEDULED_TRIGGER or workflow.current_version is None:
            continue
        config = ScheduledDateConfig.model_validate(graph.trigger.config)
        if config.run_at > now:
            continue
        run_id = await session.scalar(
            insert(ScheduledTriggerRun)
            .values(
                id=uuid.uuid4(),
                tenant_id=require_session_tenant_id(session),
                workflow_id=workflow.id,
                version=workflow.current_version,
                enrolled_count=0,
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "workflow_id", "version"])
            .returning(ScheduledTriggerRun.id)
        )
        if run_id is None:
            continue
        count = 0
        for contact in (await session.scalars(select(Contact))).all():
            company = await session.get(Company, contact.company_id) if contact.company_id else None
            if config.condition is not None and not evaluate_condition(
                config.condition, build_condition_facts(contact, company, {})
            ):
                continue
            attempt = await enroll_contact_in_workflow(
                session,
                services.clock,
                workflow=workflow,
                contact=contact,
                enrolled_by="schedule",
                task_queue=services.task_queue,
            )
            count += attempt.enrollment is not None
        run = await session.get(ScheduledTriggerRun, run_id)
        assert run is not None  # noqa: S101 - just inserted
        run.enrolled_count = count
        enrolled += count
        logger.info("scheduled_trigger_fired", workflow_id=str(workflow.id), enrolled=count)
    return enrolled
