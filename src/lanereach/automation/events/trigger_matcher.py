"""Matching events to workflow triggers."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import structlog
from sqlalchemy import select

from lanereach.automation.conditions import evaluate_condition
from lanereach.automation.engine.facts import LEAD_VARIABLE, REPLY_VARIABLE, build_condition_facts
from lanereach.automation.engine.services import EngineServices
from lanereach.automation.enrollments.models import Enrollment
from lanereach.automation.enrollments.service import enroll_contact_in_workflow
from lanereach.automation.events.models import Event, EventStatus, EventType
from lanereach.automation.events.service import EventProcessingOutcome
from lanereach.automation.nodes.base import NodeConfig
from lanereach.automation.nodes.triggers.catalog import (
    ContactFilterConfig,
    ContactStageChangedConfig,
    CustomEventConfig,
    DealIdleConfig,
    DealStageChangedConfig,
    EmailBouncedConfig,
    EmailClickedConfig,
    FormSubmittedConfig,
    LeadStatusChangedConfig,
    QuoteExpiringConfig,
    QuoteStatusChangedConfig,
    ReplyClassifiedConfig,
    TagAddedConfig,
    trigger_event_type,
)
from lanereach.automation.workflows.graph import WorkflowGraph
from lanereach.automation.workflows.models import Workflow, WorkflowStatus, WorkflowVersion
from lanereach.domain.companies.models import Company
from lanereach.domain.contacts.models import Contact

logger = structlog.get_logger(__name__)


async def load_live_workflows(services: EngineServices) -> list[tuple[Workflow, WorkflowGraph]]:
    """Published, unpaused workflows with the graph of their current version.

    Args:
        services: Engine services.

    Returns:
        Workflow and parsed graph pairs.
    """
    rows = (
        await services.session.execute(
            select(Workflow, WorkflowVersion)
            .join(
                WorkflowVersion,
                (WorkflowVersion.workflow_id == Workflow.id)
                & (WorkflowVersion.version == Workflow.current_version),
            )
            .where(Workflow.status == WorkflowStatus.PUBLISHED, Workflow.paused_at.is_(None))
        )
    ).all()
    return [(workflow, WorkflowGraph.model_validate(version.graph)) for workflow, version in rows]


def trigger_accepts_event(
    config: NodeConfig, event: Event, workflow: Workflow, facts: Mapping[str, Any]
) -> bool:
    """Check a trigger's config against an event of the trigger's type.

    Args:
        config: Parsed trigger config.
        event: The event.
        workflow: The workflow owning the trigger.
        facts: Condition facts about the event's contact.

    Returns:
        Whether the contact should be enrolled.
    """
    payload = event.payload
    if event.type is EventType.MANUAL_ENROLL:
        return str(payload.get("workflow_id")) == str(workflow.id)
    if isinstance(config, ReplyClassifiedConfig):
        bucket = payload.get("classification", {}).get("bucket")
        return bucket in {item.value for item in config.buckets}
    if isinstance(config, TagAddedConfig):
        added = str(payload.get("tag", "")).casefold()
        if added != config.tag.casefold():
            return False
    statuses = (
        {item.value for item in config.statuses}
        if isinstance(config, LeadStatusChangedConfig)
        else None
    )
    if statuses is not None and payload.get("status") not in statuses:
        return False
    if isinstance(config, CustomEventConfig) and payload.get("name") != config.event_name:
        return False
    if isinstance(config, DealStageChangedConfig) and not deal_stage_matches(config, payload):
        return False
    if (
        isinstance(config, QuoteStatusChangedConfig)
        and payload.get("status") not in config.statuses
    ):
        return False
    if isinstance(config, QuoteExpiringConfig) and payload.get("days_left") != config.days_before:
        return False
    if isinstance(config, DealIdleConfig) and not deal_idle_matches(config, payload):
        return False
    if (
        isinstance(config, EmailBouncedConfig)
        and payload.get("bounce_type") not in config.bounce_types
    ):
        return False
    if isinstance(config, ContactStageChangedConfig) and payload.get("stage") not in {
        item.value for item in config.stages
    }:
        return False
    if (
        isinstance(config, EmailClickedConfig)
        and config.url_contains
        and config.url_contains.casefold() not in str(payload.get("url") or "").casefold()
    ):
        return False
    if (
        isinstance(config, FormSubmittedConfig)
        and config.form_ids
        and payload.get("form_id") not in {str(item) for item in config.form_ids}
    ):
        return False
    if isinstance(config, ContactFilterConfig) and config.condition is not None:
        return evaluate_condition(config.condition, facts)
    return True


def deal_stage_matches(config: DealStageChangedConfig, payload: Mapping[str, Any]) -> bool:
    """Whether a `deal.stage_changed` payload is one the trigger listens to."""
    if config.pipeline_id is not None and payload.get("pipeline_id") != str(config.pipeline_id):
        return False
    names = {name.strip().casefold() for name in config.stage_names}
    kinds = {kind.value for kind in config.kinds}
    return str(payload.get("stage", "")).casefold() in names or payload.get("kind") in kinds


def deal_idle_matches(config: DealIdleConfig, payload: Mapping[str, Any]) -> bool:
    """Whether a `deal.idle` payload is one the trigger listens to."""
    if payload.get("days") != config.days:
        return False
    if config.pipeline_id is not None and payload.get("pipeline_id") != str(config.pipeline_id):
        return False
    names = {name.strip().casefold() for name in config.stage_names}
    return not names or str(payload.get("stage", "")).casefold() in names


def initial_variables(event: Event) -> dict[str, Any]:
    """Enrollment variables carried over from the event (for example the classified reply).

    Args:
        event: The triggering event.

    Returns:
        Variables for the new enrollment.
    """
    variables: dict[str, Any] = {}
    classification = event.payload.get("classification")
    if isinstance(classification, dict):
        variables[REPLY_VARIABLE] = classification
    shipment = event.payload.get("shipment")
    if isinstance(shipment, dict):
        variables[LEAD_VARIABLE] = shipment
    if "message_id" in event.payload:
        variables["reply_message_id"] = event.payload["message_id"]
    if event.type is EventType.LEAD_STATUS_CHANGED:
        variables["lead_id"] = event.payload.get("lead_id")
        variables["lead_status"] = event.payload.get("status")
    if event.type in (EventType.LEAD_CREATED, EventType.DEAL_IDLE):
        variables["lead_id"] = event.payload.get("lead_id")
        variables["deal_id"] = event.payload.get("deal_id")
        if event.payload.get("stage"):
            variables["deal_stage"] = event.payload.get("stage")
    if event.type is EventType.QUOTE_EXPIRING:
        variables["quote_id"] = event.payload.get("quote_id")
        variables["quote_number"] = event.payload.get("number")
        variables["deal_id"] = event.payload.get("deal_id")
        variables["quote_valid_until"] = event.payload.get("valid_until")
    if event.type is EventType.EMAIL_BOUNCED:
        variables["bounce_type"] = event.payload.get("bounce_type")
    if event.type is EventType.DEAL_STAGE_CHANGED:
        variables["deal_id"] = event.payload.get("deal_id")
        variables["deal_stage"] = event.payload.get("stage")
    if event.type in (EventType.EMAIL_OPENED, EventType.EMAIL_CLICKED):
        variables["email_subject"] = event.payload.get("subject")
        if event.payload.get("url"):
            variables["clicked_url"] = str(event.payload["url"])[:500]
    if event.type is EventType.QUOTE_STATUS_CHANGED:
        variables["quote_id"] = event.payload.get("quote_id")
        variables["quote_number"] = event.payload.get("number")
        variables["deal_id"] = event.payload.get("deal_id")
        variables["quote_status"] = event.payload.get("status")
    if event.type is EventType.CONTACT_STAGE_CHANGED:
        variables["contact_stage"] = event.payload.get("stage")
    if event.type in (EventType.MEETING_BOOKED, EventType.MEETING_CANCELED):
        variables["meeting_id"] = event.payload.get("meeting_id")
        variables["meeting_starts_at"] = event.payload.get("starts_at")
    if event.type is EventType.FORM_SUBMITTED:
        variables["form_id"] = event.payload.get("form_id")
        answers = event.payload.get("answers")
        for key, value in (answers if isinstance(answers, dict) else {}).items():
            if isinstance(value, str) and key not in {"email", "message"}:
                variables.setdefault(f"form_{key}"[:64], value[:500])
    if event.type is EventType.CUSTOM:
        # Simple values of the event become enrollment variables, usable in conditions as
        # `enrollment.variables.<name>` (nested data and the reserved keys are left out).
        variables["event_name"] = event.payload.get("name")
        for key, value in event.payload.items():
            if key not in {"name", "enrolled_by"} and isinstance(value, str | int | float | bool):
                variables.setdefault(
                    str(key)[:64], value if not isinstance(value, str) else value[:500]
                )
    return variables


async def match_event_to_triggers(services: EngineServices, event: Event) -> list[Enrollment]:
    """Enroll the event's contact in every live workflow whose trigger accepts the event.

    Args:
        services: Engine services.
        event: The event (already locked by the caller).

    Returns:
        The enrollments created.
    """
    if event.contact_id is None:
        return []
    session = services.session
    contact = await session.get(Contact, event.contact_id)
    if contact is None:
        return []
    company = await session.get(Company, contact.company_id) if contact.company_id else None
    variables = initial_variables(event)
    facts = build_condition_facts(contact, company, variables)

    created: list[Enrollment] = []
    for workflow, graph in await load_live_workflows(services):
        node_class = services.registry.find(graph.trigger.type)
        if node_class is None or trigger_event_type(node_class) != event.type.value:
            continue
        config = node_class.parse_config(graph.trigger.config)
        if not trigger_accepts_event(config, event, workflow, facts):
            continue
        attempt = await enroll_contact_in_workflow(
            session,
            services.clock,
            workflow=workflow,
            contact=contact,
            enrolled_by=str(event.payload.get("enrolled_by") or f"event:{event.type.value}"),
            trigger_event_id=event.id,
            variables=dict(variables),
            task_queue=services.task_queue,
        )
        if attempt.enrollment is not None:
            created.append(attempt.enrollment)
    return created


async def process_event(
    services: EngineServices, event_id: uuid.UUID
) -> EventProcessingOutcome | None:
    """Process one pending event (idempotent: processed events are skipped).

    The event row is locked with `SKIP LOCKED` so two workers never process it twice. A failure
    marks the event `failed` with the error instead of retrying forever.

    Args:
        services: Engine services.
        event_id: Event to process.

    Returns:
        The outcome, or None if the event was already processed or locked elsewhere.
    """
    session = services.session
    event = await session.scalar(
        select(Event)
        .where(Event.id == event_id, Event.status == EventStatus.PENDING)
        .with_for_update(skip_locked=True)
    )
    if event is None:
        return None
    enrollments: Sequence[Enrollment] = []
    try:
        async with session.begin_nested():
            enrollments = await match_event_to_triggers(services, event)
        event.status = EventStatus.PROCESSED
    except Exception as exc:
        logger.exception("event_processing_failed", event_id=str(event_id))
        event.status = EventStatus.FAILED
        event.error = f"{type(exc).__name__}: {exc}"[:2000]
    await _fan_out_to_integrations(services, event)
    event.processed_at = services.clock.now()
    event.enrollments_created = len(enrollments)
    return EventProcessingOutcome(event.id, event.status, [item.id for item in enrollments])


async def _fan_out_to_integrations(services: EngineServices, event: Event) -> None:
    """Queue the event for webhooks and Slack; a failure here never fails the workflows."""
    # Imported here: the integrations package depends on the engine, not the other way round.
    from lanereach.integrations.outbound.service import fan_out_event

    try:
        async with services.session.begin_nested():
            await fan_out_event(services.session, services.clock, event)
    except Exception:
        logger.exception("integration_fan_out_failed", event_id=str(event.id))
