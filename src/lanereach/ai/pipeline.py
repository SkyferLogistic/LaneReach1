"""Inbound reply pipeline: `process_inbound_reply` (spec §5.6).

1. Match the message to its contact, company, office, sender and thread.
2. Classify: provider hints (bounce/complaint events), then deterministic rules, then the LLM.
   An AI failure never drops a reply: it becomes `unclear` and goes to human review.
3. Extract shipment details for rate and service queries.
4. Decide the policy (deterministic) and apply its protection actions; "stop on reply" stops
   cold outreach for any human reply.
5. Hand over (pricing lead, office owner, referred contact, re-route), then answer: a draft for
   review, an auto-sent reply (only where the tenant enabled it) or a forward to the owner.
6. Publish `reply.classified` so workflows can react.

Idempotent per message: a processed message returns its stored result without side effects.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

import structlog
from sqlalchemy import func, select

from lanereach.ai.builtin_messages import message_values, write_built_in_reply
from lanereach.ai.controls import AIBudgetGuard, load_ai_controls
from lanereach.ai.history import find_past_answers
from lanereach.ai.llm.client import AIBudgetReachedError, LLMError
from lanereach.ai.llm.providers.heuristics import detect_language
from lanereach.ai.policies.models import TenantReplyPolicy
from lanereach.ai.policy import (
    DEFAULT_POLICIES,
    ActionMode,
    DraftSource,
    Handover,
    ReplyPolicyDecision,
    TenantPolicyOverride,
    decide_reply_policy,
)
from lanereach.ai.protections import ProtectionContext, ReplyProtections
from lanereach.ai.review.models import ReviewKind
from lanereach.ai.review.service import create_review_item
from lanereach.ai.rules.reply_rules import ReplyMessage
from lanereach.ai.runs.service import SessionAIRunRecorder
from lanereach.ai.schemas import ReplyBucket, ReplyClassification, ReplyDraft, ShipmentDetails
from lanereach.ai.tasks.classify_reply import ReplyContext, classify_reply
from lanereach.ai.tasks.draft_reply import DraftContext, draft_reply
from lanereach.ai.tasks.extract_shipment_details import extract_shipment_details
from lanereach.ai.tasks.runtime import AIRuntime
from lanereach.automation.engine.email import (
    choose_sender,
    deliver_email,
    find_template,
    thread_reference,
)
from lanereach.automation.engine.services import EngineServices
from lanereach.automation.events.models import EventType
from lanereach.automation.events.service import publish_event
from lanereach.automation.guardrails.base import EmailKind
from lanereach.core.errors import ConflictError, NotFoundError
from lanereach.domain.companies.models import Company
from lanereach.domain.contacts.models import Contact, ContactStage
from lanereach.domain.crm.service import open_deal_for_lead
from lanereach.domain.leads.models import Lead, LeadPriority, LeadStatus
from lanereach.domain.messages.models import Message, MessageDirection, MessageKind
from lanereach.domain.offices.models import Office
from lanereach.domain.senders.models import Sender
from lanereach.integrations.notifications.notifier import NotificationMessage
from lanereach.tenants.models import Tenant

logger = structlog.get_logger(__name__)

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_STAGE_AFTER = {
    ReplyBucket.INTERESTED: ContactStage.ENGAGED,
    ReplyBucket.MEETING_REQUEST: ContactStage.ENGAGED,
    ReplyBucket.RATE_QUERY: ContactStage.QUALIFIED,
    ReplyBucket.SERVICE_QUERY: ContactStage.QUALIFIED,
    ReplyBucket.EXISTING_PARTNER: ContactStage.PARTNER,
}
_HINT_BUCKETS = {
    "bounce_hard": ReplyBucket.BOUNCE_HARD,
    "bounce_soft": ReplyBucket.BOUNCE_SOFT,
    "blocked": ReplyBucket.BLOCKED,
    "complaint": ReplyBucket.COMPLAINT,
}


@dataclass
class InboundProcessingResult:
    """What the pipeline did with one message (stored on the message)."""

    message_id: str
    contact_id: str
    classification: dict[str, Any]
    source: str
    policy: dict[str, Any]
    actions_taken: list[str] = field(default_factory=list)
    shipment: dict[str, Any] | None = None
    review_item_id: str | None = None
    lead_id: str | None = None
    sent_message_id: str | None = None
    event_id: str | None = None
    already_processed: bool = False

    def to_json(self) -> dict[str, Any]:
        """JSON-compatible form."""
        return asdict(self)


@dataclass
class _Scene:
    message: Message
    contact: Contact
    company: Company | None
    office: Office | None
    sender: Sender | None
    tenant: Tenant


async def process_inbound_reply(
    services: EngineServices, message_id: uuid.UUID
) -> InboundProcessingResult:
    """Classify an inbound message and act on it.

    Args:
        services: Engine services bound to the tenant session of this unit of work.
        message_id: Stored inbound message.

    Returns:
        The processing result.

    Raises:
        NotFoundError: If the message does not exist in the tenant.
        ConflictError: If it was processed and the stored result was purged (retention).
    """
    session = services.session
    message = await session.scalar(
        select(Message).where(Message.id == message_id).with_for_update()
    )
    if message is None or message.direction is not MessageDirection.INBOUND:
        raise NotFoundError("Inbound message not found.")
    if message.processed_at is not None and message.processing_result:
        if "message_id" not in message.processing_result:
            raise ConflictError("This message was processed; its details have been purged.")
        stored = InboundProcessingResult(**message.processing_result)
        stored.already_processed = True
        return stored

    scene = await _load_scene(services, message)
    runtime = AIRuntime(
        llm=services.llm,  # type: ignore[arg-type]
        recorder=SessionAIRunRecorder(session, scene.tenant.id, services.clock, services.settings),
        settings=services.settings,
        budget=AIBudgetGuard.for_session(session, services.clock),
    )
    reply = ReplyMessage(
        subject=message.subject,
        body=message.body or "",
        received_on=message.occurred_at.date(),
        from_email=message.from_email,
    )
    classification, source, return_date = await _classify(services, runtime, scene, reply)
    shipment = await _extract(runtime, scene, reply, classification)
    decision = decide_reply_policy(
        classification,
        await load_tenant_policy(services),
        review_threshold=services.settings.ai_review_confidence_threshold,
    )
    result = InboundProcessingResult(
        message_id=str(message.id),
        contact_id=str(scene.contact.id),
        classification=classification.model_dump(mode="json"),
        source=source,
        policy={
            "action_mode": decision.action_mode.value,
            "protection_actions": [action.value for action in decision.protection_actions],
            "handover": decision.handover.value if decision.handover else None,
            "reasons": list(decision.reasons),
        },
        shipment=shipment.model_dump(mode="json") if shipment else None,
    )

    protection = ProtectionContext(
        contact=scene.contact,
        company=scene.company,
        office=scene.office,
        sender=scene.sender,
        bucket=classification.bucket,
        summary=classification.summary,
        return_date=return_date,
    )
    protections = ReplyProtections(services)
    for action in decision.protection_actions:
        await protections.apply(action, protection)
    result.actions_taken.extend(protection.actions_taken)

    new_stage = _STAGE_AFTER.get(classification.bucket)
    if new_stage and scene.contact.stage not in (
        ContactStage.DO_NOT_CONTACT,
        ContactStage.CUSTOMER,
    ):
        scene.contact.stage = new_stage
        scene.contact.updated_at = services.clock.now()

    await _handover(services, scene, decision, classification, shipment, result)
    if result.lead_id:
        await _score_new_deal(services, runtime, message, uuid.UUID(result.lead_id))
    await _answer(services, runtime, scene, reply, decision, classification, shipment, result)

    event = await publish_event(
        session,
        services.clock,
        event_type=EventType.REPLY_CLASSIFIED,
        contact_id=scene.contact.id,
        payload={
            "message_id": str(message.id),
            "classification": result.classification,
            "shipment": result.shipment,
            "source": source,
        },
        idempotency_key=f"reply.classified:{message.id}",
        task_queue=services.task_queue,
    )
    result.event_id = str(event.id)
    await _publish_reply_outcome(services, message, scene.contact.id, classification.bucket)
    message.processed_at = services.clock.now()
    message.processing_result = result.to_json()
    logger.info(
        "inbound_reply_processed",
        message_id=str(message.id),
        bucket=classification.bucket.value,
        source=source,
        action_mode=decision.action_mode.value,
    )
    return result


async def _score_new_deal(
    services: EngineServices, runtime: AIRuntime, message: Message, lead_id: uuid.UUID
) -> None:
    """Score the deal a rate request opened, with the AI reading intent from this reply."""
    # Imported here: scoring builds on CRM and quotes, which import the engine.
    from lanereach.domain.crm.models import Deal
    from lanereach.domain.intelligence.scoring import LeadScoringService

    deal = await services.session.scalar(select(Deal).where(Deal.lead_id == lead_id))
    if deal is None:
        return
    scoring = LeadScoringService(services.session, services.clock, runtime)
    if (await load_ai_controls(services.session)).lead_reading == "ai":
        await scoring.read_intent(deal, message)
    await scoring.score_deal(deal.id)


NOT_A_PERSON = frozenset(
    {
        ReplyBucket.AUTO_REPLY,
        ReplyBucket.OUT_OF_OFFICE,
        ReplyBucket.BOUNCE_HARD,
        ReplyBucket.BOUNCE_SOFT,
        ReplyBucket.BLOCKED,
    }
)
BOUNCE_TYPES_BY_BUCKET = {
    ReplyBucket.BOUNCE_HARD: "hard",
    ReplyBucket.BLOCKED: "hard",
    ReplyBucket.BOUNCE_SOFT: "soft",
}


async def _publish_reply_outcome(
    services: EngineServices, message: Message, contact_id: uuid.UUID, bucket: ReplyBucket
) -> None:
    """Publish `reply.received` for a person's reply, or `email.bounced` for a bounce.

    They start the "Reply received" and "Email bounced" triggers; `reply.classified` (published
    just before) carries the classification itself.
    """
    bounce_type = BOUNCE_TYPES_BY_BUCKET.get(bucket)
    if bounce_type is not None:
        await publish_event(
            services.session,
            services.clock,
            event_type=EventType.EMAIL_BOUNCED,
            contact_id=contact_id,
            payload={"message_id": str(message.id), "bounce_type": bounce_type},
            idempotency_key=f"email.bounced:{message.id}",
            task_queue=services.task_queue,
        )
    elif bucket not in NOT_A_PERSON and message.kind is MessageKind.REPLY:
        await publish_event(
            services.session,
            services.clock,
            event_type=EventType.REPLY_RECEIVED,
            contact_id=contact_id,
            payload={"message_id": str(message.id), "bucket": bucket.value},
            idempotency_key=f"reply.received:{message.id}",
            task_queue=services.task_queue,
        )


async def load_tenant_policy(services: EngineServices) -> dict[ReplyBucket, TenantPolicyOverride]:
    """The tenant's policy overrides by bucket.

    Args:
        services: Engine services.

    Returns:
        Overrides keyed by bucket.
    """
    rows = (await services.session.scalars(select(TenantReplyPolicy))).all()
    return {
        row.bucket: TenantPolicyOverride(
            action_mode=row.action_mode,
            auto_send_enabled=row.auto_send_enabled,
            draft_source=row.draft_source or DraftSource.AI,
        )
        for row in rows
    }


async def _load_scene(services: EngineServices, message: Message) -> _Scene:
    session = services.session
    contact = await session.get(Contact, message.contact_id)
    assert contact is not None  # noqa: S101 - FK guarantees it
    tenant = await session.get(Tenant, contact.tenant_id)
    assert tenant is not None  # noqa: S101 - own tenant
    company = await session.get(Company, contact.company_id) if contact.company_id else None
    last_outbound = await session.scalar(
        select(Message)
        .where(
            Message.thread_id == message.thread_id, Message.direction == MessageDirection.OUTBOUND
        )
        .order_by(Message.occurred_at.desc())
        .limit(1)
    )
    office_id = (last_outbound.office_id if last_outbound else None) or contact.office_id
    office = await session.get(Office, office_id) if office_id else None
    sender = (
        await session.get(Sender, last_outbound.sender_id)
        if last_outbound and last_outbound.sender_id
        else None
    )
    return _Scene(message, contact, company, office, sender, tenant)


async def _classify(
    services: EngineServices, runtime: AIRuntime, scene: _Scene, reply: ReplyMessage
) -> tuple[ReplyClassification, str, Any]:
    hint = (scene.message.processing_result or {}).get("hint")
    if scene.message.kind in (MessageKind.BOUNCE, MessageKind.COMPLAINT) and hint in _HINT_BUCKETS:
        classification = ReplyClassification(
            bucket=_HINT_BUCKETS[hint],
            confidence=1.0,
            language=detect_language(reply.body),
            summary=f"Provider reported {hint.replace('_', ' ')}.",
            reasoning_short="Sending provider event.",
            needs_human=False,
        )
        return classification, "provider", None
    if services.llm is None:
        return _fallback(reply, "AI is not configured"), "fallback", None
    try:
        outcome = await classify_reply(
            runtime,
            reply,
            ReplyContext(
                office_name=scene.office.name if scene.office else "our office",
                contact_country=scene.contact.country or "unknown",
                input_ref=str(scene.message.id),
            ),
        )
    except LLMError as exc:
        logger.warning(
            "classification_failed", message_id=str(scene.message.id), error=type(exc).__name__
        )
        return _fallback(reply, f"AI unavailable ({type(exc).__name__})"), "fallback", None
    return (
        outcome.classification,
        outcome.source,
        outcome.rule.return_date if outcome.rule else None,
    )


def _fallback(reply: ReplyMessage, reason: str) -> ReplyClassification:
    return ReplyClassification(
        bucket=ReplyBucket.UNCLEAR,
        confidence=0.0,
        language=detect_language(reply.body),
        summary="Could not classify automatically.",
        reasoning_short=reason[:400],
        needs_human=True,
    )


async def _extract(
    runtime: AIRuntime, scene: _Scene, reply: ReplyMessage, classification: ReplyClassification
) -> ShipmentDetails | None:
    if classification.bucket not in (ReplyBucket.RATE_QUERY, ReplyBucket.SERVICE_QUERY):
        return None
    try:
        return (
            await extract_shipment_details(runtime, reply, input_ref=str(scene.message.id))
        ).output
    except LLMError as exc:
        logger.warning(
            "extraction_failed", message_id=str(scene.message.id), error=type(exc).__name__
        )
        return None


async def _notify_owner(services: EngineServices, scene: _Scene, subject: str, body: str) -> bool:
    if scene.office is None:
        return False
    await services.notifier.notify(
        NotificationMessage(
            channel="office_owner",
            recipient=scene.office.owner_email,
            subject=subject,
            body=body,
            payload={"contact_id": str(scene.contact.id), "message_id": str(scene.message.id)},
            contact_id=scene.contact.id,
        )
    )
    return True


async def _handover(
    services: EngineServices,
    scene: _Scene,
    decision: ReplyPolicyDecision,
    classification: ReplyClassification,
    shipment: ShipmentDetails | None,
    result: InboundProcessingResult,
) -> None:
    contact = scene.contact
    who = contact.display_name
    if decision.handover is Handover.PRICING_LEAD:
        now = services.clock.now()
        lead = Lead(
            tenant_id=scene.tenant.id,
            contact_id=contact.id,
            company_id=scene.company.id if scene.company else None,
            office_id=scene.office.id if scene.office else None,
            message_id=scene.message.id,
            status=LeadStatus.NEW,
            priority=LeadPriority.HIGH
            if classification.bucket is ReplyBucket.RATE_QUERY
            else LeadPriority.NORMAL,
            source="reply",
            summary=classification.summary,
            shipment=shipment.model_dump(mode="json") if shipment else None,
            created_at=now,
            updated_at=now,
        )
        services.session.add(lead)
        await services.session.flush()
        result.lead_id = str(lead.id)
        await open_deal_for_lead(
            services.session,
            services.clock,
            lead,
            actor="reply-pipeline",
            task_queue=services.task_queue,
        )
        from lanereach.automation.nodes.actions.catalog import describe_lead

        await services.notifier.notify(
            NotificationMessage(
                channel="pricing",
                recipient=services.settings.pricing_notification_recipient,
                subject=f"New lead: {who}",
                body=describe_lead(scene.company.name if scene.company else None, result.shipment),
                payload={"lead_id": str(lead.id), "contact_id": str(contact.id)},
                contact_id=contact.id,
            )
        )
        await _notify_owner(services, scene, f"Rate request from {who}", classification.summary)
        result.actions_taken.append("created pricing lead and notified pricing")
    elif decision.handover is Handover.OFFICE_OWNER:
        if await _notify_owner(
            services,
            scene,
            f"{classification.bucket.replace('_', ' ').title()}: {who}",
            classification.summary,
        ):
            result.actions_taken.append("handed over to office owner")
    elif decision.handover is Handover.REROUTE_OFFICE:
        if await _notify_owner(
            services,
            scene,
            f"Wrong office: {who}",
            f"{classification.summary} Please re-route this contact.",
        ):
            result.actions_taken.append("asked office owner to re-route")
    elif decision.handover is Handover.REFERRED_CONTACT:
        referred = await _create_referred_contact(services, scene)
        if referred:
            result.actions_taken.append(f"created referred contact {referred}")


async def _create_referred_contact(services: EngineServices, scene: _Scene) -> str | None:
    body = scene.message.body or ""
    ours = {scene.contact.email.lower(), scene.message.to_email.lower()}
    for candidate in _EMAIL.findall(body):
        email = str(candidate).lower()
        if email in ours:
            continue
        exists = await services.session.scalar(
            select(func.count()).select_from(Contact).where(func.lower(Contact.email) == email)
        )
        if exists:
            return None
        now = services.clock.now()
        services.session.add(
            Contact(
                tenant_id=scene.tenant.id,
                email=email,
                company_id=scene.contact.company_id,
                office_id=scene.contact.office_id,
                country=scene.contact.country,
                language=scene.contact.language,
                tags=["referral"],
                created_at=now,
                updated_at=now,
            )
        )
        await services.session.flush()
        return email
    return None


async def _answer(
    services: EngineServices,
    runtime: AIRuntime,
    scene: _Scene,
    reply: ReplyMessage,
    decision: ReplyPolicyDecision,
    classification: ReplyClassification,
    shipment: ShipmentDetails | None,
    result: InboundProcessingResult,
) -> None:
    mode = decision.action_mode
    if mode is ActionMode.NONE:
        return
    if mode is ActionMode.FORWARD_TO_OWNER:
        if decision.handover in (Handover.OFFICE_OWNER, Handover.REROUTE_OFFICE):
            # The handover already put the reply in front of the owner.
            result.actions_taken.append("forwarded to office owner")
            return
        if await _notify_owner(
            services,
            scene,
            f"Forwarded reply from {scene.contact.display_name}",
            f"{classification.summary}\n\nBucket: {classification.bucket}",
        ):
            result.actions_taken.append("forwarded to office owner")
        return

    draft = None
    draft_run_id = None
    written_by = "ai"
    grounded_on = 0
    notes: list[str] = []
    # Protective buckets (complaints, unsubscribes...) are never answered: when they are
    # uncertain, a person checks the classification, not a reply draft.
    answerable = DEFAULT_POLICIES[classification.bucket].action_mode is not ActionMode.NONE
    sender = None
    if answerable and scene.office is not None:
        sender = await choose_sender(services.session, scene.office, services.clock.now(), None)
    use_ai = decision.draft_source is DraftSource.AI and services.llm is not None
    if answerable and scene.office is not None and use_ai:
        template_key = f"reply_{classification.bucket.value}"
        languages = [classification.language, scene.office.default_language, "en"]
        template = await find_template(services.session, template_key, languages)
        past_answers = await find_past_answers(
            services.session,
            bucket=classification.bucket.value,
            text=f"{reply.subject}\n{reply.body}",
            now=services.clock.now(),
            exclude_message_id=scene.message.id,
        )
        grounded_on = len(past_answers)
        try:
            outcome = await draft_reply(
                runtime,
                reply,
                DraftContext(
                    office_name=scene.office.name,
                    office_signature=scene.office.signature,
                    sender_name=sender.display_name if sender else scene.office.owner_name,
                    contact_first_name=scene.contact.first_name,
                    language=classification.language,
                    bucket=classification.bucket,
                    template_key=template.key if template else None,
                    template_body=template.body if template else None,
                    past_answers=past_answers,
                ),
                input_ref=str(scene.message.id),
            )
            draft, draft_run_id = outcome.output, outcome.ai_run_id
        except LLMError as exc:
            logger.warning(
                "draft_failed", message_id=str(scene.message.id), error=type(exc).__name__
            )
            if isinstance(exc, AIBudgetReachedError):
                notes.append("this month's AI budget is used up")
            else:
                notes.append("the AI was not available")

    # The company's built-in message answers when it is the chosen writer, and stands in for a
    # failed AI draft when the classification is certain (an uncertain one goes to a person to
    # check the classification instead). A stand-in is never auto-sent.
    chosen = decision.draft_source is DraftSource.BUILT_IN
    stand_in = draft is None and not chosen and not decision.reasons
    if answerable and scene.office is not None and draft is None and (chosen or stand_in):
        built_in = await write_built_in_reply(
            services.session,
            bucket=classification.bucket,
            languages=[classification.language, scene.office.default_language],
            inbound_subject=scene.message.subject,
            values=message_values(
                contact_first_name=scene.contact.first_name,
                contact_full_name=scene.contact.display_name,
                company_name=scene.company.name if scene.company else None,
                office_name=scene.office.name,
                office_owner_name=scene.office.owner_name,
                sender_name=sender.display_name if sender else None,
            ),
        )
        draft = ReplyDraft(
            subject=built_in.subject,
            body=built_in.body,
            language=built_in.language,
            tone="built-in",
            used_template_key=built_in.template_key,
        )
        written_by = "built_in"
        if built_in.note:
            notes.append(built_in.note)

    auto_send = mode is ActionMode.AUTO_SEND and (written_by == "ai" or chosen)
    if auto_send and draft is not None and scene.office is not None:
        thread = await thread_reference(
            services.session, scene.message.thread_id, scene.message.subject
        )
        delivery = await deliver_email(
            services,
            kind=EmailKind.REPLY,
            tenant=scene.tenant,
            contact=scene.contact,
            company=scene.company,
            office=scene.office,
            sender=await choose_sender(services.session, scene.office, services.clock.now(), None),
            subject=draft.subject,
            body=draft.body,
            idempotency_key=f"auto-reply:{scene.message.id}",
            thread=thread,
        )
        if delivery.sent and delivery.message is not None:
            result.sent_message_id = delivery.message.message_id
            result.actions_taken.append("auto-sent reply")
            return
        result.actions_taken.append(f"auto-send held by {delivery.verdict.deciding_guardrail}")

    label = classification.bucket.value.replace("_", " ")
    if draft is None:
        summary = f"Confirm the {label} classification"
    elif written_by == "built_in":
        summary = f"Built-in message for a {label} reply"
    else:
        summary = f"AI drafted an answer to a {label} reply"
    reason = "; ".join([*decision.reasons, *notes]) or summary
    if decision.reasons and written_by == "built_in":
        reason = f"{summary}; {reason}"
    item = await create_review_item(
        services,
        kind=ReviewKind.REPLY_DRAFT if draft else ReviewKind.CLASSIFICATION,
        payload={
            "subject": draft.subject if draft else None,
            "body": draft.body if draft else None,
            "language": draft.language if draft else classification.language,
            "classification": classification.model_dump(mode="json"),
            "shipment": shipment.model_dump(mode="json") if shipment else None,
            "inbound_subject": scene.message.subject,
            "inbound_excerpt": (scene.message.body or "")[:1000],
            "contact_email": scene.contact.email,
            "written_by": written_by if draft else None,
            "grounded_on": grounded_on if draft and written_by == "ai" else 0,
        },
        reason=reason,
        contact_id=scene.contact.id,
        message_id=scene.message.id,
        office_id=scene.office.id if scene.office else None,
        ai_run_id=draft_run_id,
    )
    result.review_item_id = str(item.id)
    result.actions_taken.append(
        "queued for review" if draft else "queued classification for review"
    )
