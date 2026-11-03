"""What the AI workflow nodes do when an enrollment reaches them.

AI nodes only produce data (a classification, shipment details, a draft, a localized
template) and store it on the enrollment or in the review queue. They never send email: a
draft is sent by a reviewer's approval or by a later `reply_in_thread` node, both through the
send guardrails.

Results the reply pipeline already produced for the same message are reused, so a workflow
triggered by `reply.classified` does not pay for (or disagree with) a second classification.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select

from lanereach.ai.controls import AIBudgetGuard
from lanereach.ai.llm.client import LLMError
from lanereach.ai.pipeline import load_tenant_policy
from lanereach.ai.policy import ActionMode
from lanereach.ai.review.models import ReviewItem, ReviewKind, ReviewStatus
from lanereach.ai.review.service import create_review_item
from lanereach.ai.rules.reply_rules import ReplyMessage
from lanereach.ai.runs.service import SessionAIRunRecorder
from lanereach.ai.schemas import ReplyBucket
from lanereach.ai.tasks.classify_reply import ReplyContext, classify_reply
from lanereach.ai.tasks.draft_reply import DraftContext, draft_reply
from lanereach.ai.tasks.extract_shipment_details import extract_shipment_details
from lanereach.ai.tasks.localize_template import localize_template
from lanereach.ai.tasks.runtime import AIRuntime
from lanereach.automation.engine.context import ExecutionContext
from lanereach.automation.engine.email import choose_sender, find_template
from lanereach.automation.engine.facts import LEAD_VARIABLE, REPLY_VARIABLE
from lanereach.automation.engine.results import Continue, Fail, NodeResult
from lanereach.automation.engine.services import EngineServices
from lanereach.core.languages import split_language_variant
from lanereach.domain.messages.models import Message, MessageDirection, MessageKind

logger = structlog.get_logger(__name__)

DRAFT_VARIABLE = "draft"
REPLY_MESSAGE_VARIABLE = "reply_message_id"


def ai_runtime_for(services: EngineServices, context: ExecutionContext) -> AIRuntime | None:
    """The AI runtime of a workflow step (runs recorded in the step's transaction).

    Args:
        services: Engine services.
        context: The step's context (tenant of the AI runs).

    Returns:
        The runtime, or None when no LLM is configured.
    """
    if services.llm is None:
        return None
    return AIRuntime(
        llm=services.llm,
        recorder=SessionAIRunRecorder(
            services.session, context.tenant_id, services.clock, services.settings
        ),
        settings=services.settings,
        budget=AIBudgetGuard.for_session(services.session, services.clock),
    )


async def _latest_reply(services: EngineServices, context: ExecutionContext) -> Message | None:
    """The reply the enrollment is about: the triggering message, else the latest reply."""
    raw_id = context.variables.get(REPLY_MESSAGE_VARIABLE)
    if isinstance(raw_id, str):
        try:
            message = await services.session.get(Message, uuid.UUID(raw_id))
        except ValueError:
            message = None
        if message is not None:
            return message
    return await services.session.scalar(
        select(Message)
        .where(
            Message.contact_id == context.contact_id,
            Message.direction == MessageDirection.INBOUND,
            Message.kind == MessageKind.REPLY,
        )
        .order_by(Message.occurred_at.desc())
        .limit(1)
    )


def _reply_message(message: Message) -> ReplyMessage:
    return ReplyMessage(
        subject=message.subject,
        body=message.body or "",
        received_on=message.occurred_at.date(),
        from_email=message.from_email,
    )


def _stored(message: Message, key: str) -> dict[str, Any] | None:
    value = (message.processing_result or {}).get(key)
    return value if isinstance(value, dict) else None


_NO_AI = Fail(error="AI is not configured for this environment", retryable=False)
_NO_REPLY = Fail(error="the contact has not replied yet", retryable=False)


async def classify_reply_step(
    context: ExecutionContext, *, review_below_confidence: float | None
) -> NodeResult:
    """`ai_classify_reply`: store the classification in `variables.reply`.

    Args:
        context: The executing step.
        review_below_confidence: Workflow-specific review threshold.

    Returns:
        Continue, or a failure (retryable when the AI provider is unavailable).
    """
    services = context.require_services()
    message = await _latest_reply(services, context)
    if message is None:
        return _NO_REPLY
    classification = _stored(message, "classification")
    source = "pipeline"
    if classification is None:
        runtime = ai_runtime_for(services, context)
        if runtime is None:
            return _NO_AI
        subject = context.require_subject()
        try:
            outcome = await classify_reply(
                runtime,
                _reply_message(message),
                ReplyContext(
                    office_name=subject.office.name if subject.office else "our office",
                    contact_country=subject.contact.country or "unknown",
                    input_ref=str(message.id),
                ),
            )
        except LLMError as exc:
            return Fail(error=f"classification failed: {type(exc).__name__}", retryable=True)
        classification = outcome.classification.model_dump(mode="json")
        source = outcome.source
    classification = dict(classification)
    threshold = review_below_confidence
    if threshold is not None and float(classification.get("confidence") or 0) < threshold:
        classification["needs_human"] = True
    context.variables[REPLY_VARIABLE] = classification
    context.variables[REPLY_MESSAGE_VARIABLE] = str(message.id)
    context.output["classification"] = {"bucket": classification.get("bucket"), "source": source}
    return Continue()


async def extract_shipment_step(context: ExecutionContext) -> NodeResult:
    """`ai_extract_shipment_details`: store shipment details in `variables.lead`.

    Args:
        context: The executing step.

    Returns:
        Continue, or a failure.
    """
    services = context.require_services()
    message = await _latest_reply(services, context)
    if message is None:
        return _NO_REPLY
    shipment = _stored(message, "shipment")
    if shipment is None:
        runtime = ai_runtime_for(services, context)
        if runtime is None:
            return _NO_AI
        try:
            result = await extract_shipment_details(
                runtime, _reply_message(message), input_ref=str(message.id)
            )
        except LLMError as exc:
            return Fail(error=f"extraction failed: {type(exc).__name__}", retryable=True)
        shipment = result.output.model_dump(mode="json")
    context.variables[LEAD_VARIABLE] = shipment
    context.variables[REPLY_MESSAGE_VARIABLE] = str(message.id)
    context.output["shipment_fields"] = sorted(k for k, v in shipment.items() if v)
    return Continue()


async def draft_reply_step(
    context: ExecutionContext, *, template_key: str | None, tone: str
) -> NodeResult:
    """`ai_draft_reply`: draft a reply and queue it for review (or pre-approve it).

    A draft is pre-approved only when the tenant enabled `auto_send` for the reply's bucket and
    the classification is confident; a later `reply_in_thread` node (with `use_ai_draft`)
    then sends it through the guardrails. Otherwise it waits in the review queue; approving it
    there sends it. A pending draft the pipeline already queued for the same message is reused.

    Args:
        context: The executing step.
        template_key: Template to base the draft on.
        tone: Tone of voice.

    Returns:
        Continue, or a failure.
    """
    services = context.require_services()
    subject = context.require_subject()
    message = await _latest_reply(services, context)
    if message is None:
        return _NO_REPLY
    if (message.processing_result or {}).get("sent_message_id"):
        # The pipeline already auto-sent an answer to this message: never answer twice.
        context.variables[DRAFT_VARIABLE] = {"review_item_id": None, "status": "sent"}
        context.output["draft_status"] = "sent"
        return Continue()
    existing = await services.session.scalar(
        select(ReviewItem)
        .where(ReviewItem.message_id == message.id, ReviewItem.kind == ReviewKind.REPLY_DRAFT)
        .order_by(ReviewItem.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        # Reuse the draft the pipeline (or an earlier attempt of this step) already made.
        if existing.enrollment_id is None:
            existing.enrollment_id = context.enrollment_id
        _store_draft(context, existing, draft_status_of(existing))
        return Continue()
    office = subject.office
    if office is None:
        return Fail(error="the contact has no office to reply from", retryable=False)
    runtime = ai_runtime_for(services, context)
    if runtime is None:
        return _NO_AI
    reply = context.variables.get(REPLY_VARIABLE)
    reply = reply if isinstance(reply, dict) else _stored(message, "classification") or {}
    try:
        bucket = ReplyBucket(str(reply.get("bucket") or ReplyBucket.INTERESTED))
    except ValueError:
        bucket = ReplyBucket.UNCLEAR
    language = str(reply.get("language") or subject.contact.language or office.default_language)
    template = (
        await find_template(services.session, template_key, [language, office.default_language])
        if template_key
        else None
    )
    sender = await choose_sender(services.session, office, services.clock.now(), None)
    try:
        result = await draft_reply(
            runtime,
            _reply_message(message),
            DraftContext(
                office_name=office.name,
                office_signature=office.signature,
                sender_name=sender.display_name if sender else office.owner_name,
                contact_first_name=subject.contact.first_name,
                language=language,
                bucket=bucket,
                tone=tone,
                template_key=template_key,
                template_body=template.body if template else None,
            ),
            input_ref=str(message.id),
        )
    except LLMError as exc:
        return Fail(error=f"drafting failed: {type(exc).__name__}", retryable=True)
    draft = result.output
    policy = (await load_tenant_policy(services)).get(bucket)
    confident = float(reply.get("confidence") or 0) >= (
        services.settings.ai_review_confidence_threshold
    ) and not reply.get("needs_human")
    auto_send = bool(
        policy
        and policy.action_mode is ActionMode.AUTO_SEND
        and policy.auto_send_enabled
        and confident
    )
    item = await create_review_item(
        services,
        kind=ReviewKind.REPLY_DRAFT,
        payload={
            "subject": draft.subject,
            "body": draft.body,
            "language": draft.language,
            "classification": reply,
            "inbound_subject": message.subject,
            "inbound_excerpt": (message.body or "")[:1000],
            "contact_email": subject.contact.email,
        },
        reason=f"Drafted by workflow '{subject.workflow.name}'",
        contact_id=subject.contact.id,
        message_id=message.id,
        office_id=office.id,
        enrollment_id=context.enrollment_id,
        ai_run_id=result.ai_run_id,
    )
    if auto_send:
        # Pre-approved: the workflow's `reply_in_thread` node sends it (guardrails still apply).
        item.status = ReviewStatus.APPROVED
        item.reviewer = "policy:auto_send"
        item.decided_at = services.clock.now()
    _store_draft(context, item, "approved" if auto_send else "pending")
    return Continue()


def draft_status_of(item: ReviewItem) -> str:
    """The draft's state as `reply_in_thread` sees it.

    Args:
        item: A reply-draft review item.

    Returns:
        `pending`, `rejected`, `sent` (the approval sent it) or `approved` (pre-approved by an
        auto-send policy; the workflow sends it).
    """
    if item.status is ReviewStatus.PENDING:
        return "pending"
    if item.status is ReviewStatus.REJECTED:
        return "rejected"
    return "sent" if item.sent_message_id else "approved"


async def resolve_ai_draft(context: ExecutionContext) -> dict[str, Any] | None:
    """The enrollment's AI draft with its current review state.

    The state is read from the review item rather than trusted from the enrollment variables,
    so a decision taken while the enrollment was not linked to the item is still seen.

    Args:
        context: The executing step.

    Returns:
        `{status, subject, body, review_item_id}`, or None when the workflow drafted nothing.
    """
    draft = context.variables.get(DRAFT_VARIABLE)
    if not isinstance(draft, dict):
        return None
    raw_id = draft.get("review_item_id")
    if not isinstance(raw_id, str):
        return draft
    item = await context.require_services().session.get(ReviewItem, uuid.UUID(raw_id))
    if item is None:
        return None
    return {
        "review_item_id": raw_id,
        "status": draft_status_of(item),
        "subject": item.payload.get("subject"),
        "body": item.payload.get("body"),
    }


def _store_draft(context: ExecutionContext, item: ReviewItem, status: str) -> None:
    context.variables[DRAFT_VARIABLE] = {
        "review_item_id": str(item.id),
        "status": status,
        "subject": item.payload.get("subject"),
        "body": item.payload.get("body"),
    }
    context.output["review_item_id"] = str(item.id)
    context.output["draft_status"] = status


async def localize_template_step(
    context: ExecutionContext, *, template_key: str, target_language: str, region: str | None
) -> NodeResult:
    """`ai_localize_template`: queue a localized version of a template for review.

    Skipped when the template already exists in the target language. Approving the review
    item saves the new language version.

    Args:
        context: The executing step.
        template_key: Template to localize.
        target_language: Target language.
        region: Target region.

    Returns:
        Continue, or a failure.
    """
    services = context.require_services()
    subject = context.require_subject()
    if await find_template(services.session, template_key, [target_language]):
        context.output["localization"] = "template already exists in this language"
        return Continue()
    source_languages = [subject.office.default_language] if subject.office else []
    source = await find_template(services.session, template_key, [*source_languages, "en"])
    if source is None:
        return Fail(error=f"template '{template_key}' does not exist", retryable=False)
    pending = await services.session.scalar(
        select(ReviewItem.id).where(
            ReviewItem.kind == ReviewKind.LOCALIZED_TEMPLATE,
            ReviewItem.status == ReviewStatus.PENDING,
            ReviewItem.payload["template_key"].astext == template_key,
            ReviewItem.payload["language"].astext == target_language,
        )
    )
    if pending is not None:
        context.output["review_item_id"] = str(pending)
        return Continue()
    runtime = ai_runtime_for(services, context)
    if runtime is None:
        return _NO_AI
    try:
        result = await localize_template(
            runtime,
            subject=source.subject,
            body=source.body,
            target_language=split_language_variant(target_language)[0],
            region=region or split_language_variant(target_language)[1],
            input_ref=str(source.id),
        )
    except LLMError as exc:
        return Fail(error=f"localization failed: {type(exc).__name__}", retryable=True)
    item = await create_review_item(
        services,
        kind=ReviewKind.LOCALIZED_TEMPLATE,
        payload={
            "template_key": template_key,
            "language": target_language,
            "region": region,
            "source_language": source.language,
            "subject": result.output.subject,
            "body": result.output.body,
        },
        reason=f"Localization requested by workflow '{subject.workflow.name}'",
        enrollment_id=context.enrollment_id,
        ai_run_id=result.ai_run_id,
    )
    context.output["review_item_id"] = str(item.id)
    return Continue()
