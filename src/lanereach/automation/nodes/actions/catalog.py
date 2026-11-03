"""Action nodes: sending email, creating leads, notifying people and updating contacts.

Every email-sending action goes through `send_email_step`, which runs the guardrail chain
before the sending provider is called; nothing here decides whether an email may be sent.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Literal, Self

from pydantic import EmailStr, Field, model_validator
from sqlalchemy import select

from lanereach.automation.engine.context import ExecutionContext
from lanereach.automation.engine.email import find_reply_thread
from lanereach.automation.engine.results import Continue, Fail, NodeResult, Wait
from lanereach.automation.engine.steps import (
    notification_values,
    render_notification_text,
    send_email_step,
)
from lanereach.automation.enrollments.service import (
    EnrollmentSkipReason,
    enroll_contact_in_workflow,
)
from lanereach.automation.events.models import EventType
from lanereach.automation.events.service import publish_event
from lanereach.automation.guardrails.base import EmailKind
from lanereach.automation.nodes.base import BaseNode, NodeCategory, NodeConfig
from lanereach.automation.nodes.fields import NotificationText, TagName, TemplateKey
from lanereach.automation.nodes.registry import register_node
from lanereach.automation.workflows.models import Workflow
from lanereach.domain.contacts.models import ContactStage
from lanereach.domain.contacts.service import publish_contact_stage_changed
from lanereach.domain.crm.service import open_deal_for_lead
from lanereach.domain.leads.models import Lead, LeadPriority, LeadStatus
from lanereach.domain.messages.models import Message, MessageDirection
from lanereach.domain.offices.models import Office
from lanereach.domain.senders.models import ServiceLine
from lanereach.integrations.notifications.notifier import NotificationMessage

# How long `reply_in_thread` waits before checking a pending AI draft again. Approving the
# draft wakes the enrollment at once; this is only the fallback.
DRAFT_REVIEW_RECHECK = timedelta(hours=1)


class SendEmailConfig(NodeConfig):
    """Which office sends which template."""

    office_id: uuid.UUID = Field(description="Office whose voice and mailboxes are used.")
    template_key: TemplateKey
    sender_id: uuid.UUID | None = Field(
        default=None,
        description="Specific sender mailbox. Leave empty to rotate across the office's senders.",
    )
    service_line: ServiceLine = Field(
        default=ServiceLine.ANY,
        description="Send from air or ocean agents only. `any` follows the script's service line.",
    )
    tracking: bool = Field(
        default=True,
        description="Track opens and clicks (HTML scripts only, when tracking is on in "
        "Settings). Plain-text emails are never tracked.",
    )


@register_node
class SendEmailNode(BaseNode[SendEmailConfig]):
    """Sends a cold outreach email (subject to every guardrail)."""

    node_type = "send_email"
    category = NodeCategory.ACTION
    display_name = "Send email"
    description = (
        "Sends a cold email from an office using a template, in the contact's language. "
        "Guardrails may defer or block it."
    )
    config_model = SendEmailConfig
    is_cold_send = True

    async def execute(self, context: ExecutionContext, config: SendEmailConfig) -> NodeResult:
        """Send the template from the office, through the guardrails."""
        office = await context.require_services().session.get(Office, config.office_id)
        if office is None:
            return Fail(error=f"office {config.office_id} does not exist", retryable=False)
        return await send_email_step(
            context,
            kind=EmailKind.COLD,
            office=office,
            template_key=config.template_key,
            sender_id=config.sender_id,
            service_line=config.service_line,
            track=config.tracking,
        )


class ReplyInThreadConfig(NodeConfig):
    """What to reply with."""

    template_key: TemplateKey | None = Field(
        default=None, description="Template to reply with (used when no AI draft is chosen)."
    )
    use_ai_draft: bool = Field(
        default=False, description="Reply with the latest approved AI draft for this thread."
    )

    @model_validator(mode="after")
    def _require_content_source(self) -> Self:
        if self.template_key is None and not self.use_ai_draft:
            raise ValueError("set template_key or use_ai_draft")
        return self


@register_node
class ReplyInThreadNode(BaseNode[ReplyInThreadConfig]):
    """Replies in the contact's existing email thread."""

    node_type = "reply_in_thread"
    category = NodeCategory.ACTION
    display_name = "Reply in thread"
    description = (
        "Replies in the same email thread the contact wrote in. Exempt from the cold-email "
        "frequency cap, but still blocked for suppressed contacts."
    )
    config_model = ReplyInThreadConfig

    async def execute(self, context: ExecutionContext, config: ReplyInThreadConfig) -> NodeResult:
        """Reply in the contact's latest thread, from the office that owns the thread."""
        services = context.require_services()
        subject = context.require_subject()
        thread = await find_reply_thread(services.session, subject.contact)
        if thread is None:
            return Fail(error="there is no email thread with this contact yet", retryable=False)
        last_outbound_office = await services.session.scalar(
            select(Message.office_id)
            .where(
                Message.thread_id == thread.thread_id,
                Message.direction == MessageDirection.OUTBOUND,
            )
            .order_by(Message.occurred_at.desc())
            .limit(1)
        )
        office_id = last_outbound_office or (subject.office.id if subject.office else None)
        office = await services.session.get(Office, office_id) if office_id else None
        if office is None:
            return Fail(error="no office owns this thread", retryable=False)
        draft = None
        if config.use_ai_draft:
            # Imported here: the AI package builds on the engine.
            from lanereach.ai.workflow_steps import resolve_ai_draft

            draft = await resolve_ai_draft(context)
        draft_status = draft.get("status") if draft else None
        prepared_body = None
        if draft_status == "sent":
            # The reviewer's approval already sent it in this thread.
            context.output["draft"] = "already sent from the review queue"
            return Continue()
        if draft_status == "pending":
            return Wait(
                until=context.now + DRAFT_REVIEW_RECHECK,
                reason="waiting for the AI draft to be reviewed",
                handle=None,
            )
        if draft_status == "approved" and draft:
            prepared_body = str(draft.get("body") or "") or None
        if prepared_body is None and config.template_key is None:
            if draft_status == "rejected":
                context.output["draft"] = "rejected by the reviewer; nothing sent"
                return Continue()
            return Fail(error="no approved AI draft to send", retryable=False)
        return await send_email_step(
            context,
            kind=EmailKind.REPLY,
            office=office,
            template_key=config.template_key,
            thread=thread,
            prepared_body=prepared_body,
        )


class CreatePricingLeadConfig(NodeConfig):
    """How the lead is handed to pricing."""

    priority: Literal["normal", "high"] = "normal"
    notify_pricing: bool = Field(default=True, description="Notify the pricing team.")


@register_node
class CreatePricingLeadNode(BaseNode[CreatePricingLeadConfig]):
    """Creates a quote-ready lead for the pricing team."""

    node_type = "create_pricing_lead"
    category = NodeCategory.ACTION
    display_name = "Create pricing lead"
    description = "Creates a lead with the extracted shipment details for the pricing team."
    config_model = CreatePricingLeadConfig
    is_terminal = True

    async def execute(
        self, context: ExecutionContext, config: CreatePricingLeadConfig
    ) -> NodeResult:
        """Create the lead with the shipment details found so far and notify pricing.

        The reply pipeline already creates a lead for rate and service queries; when one exists
        for the same reply it is reused (linked to this enrollment, raised to `high` when this
        node asks for it) so pricing never sees the same request twice.
        """
        services = context.require_services()
        subject = context.require_subject()
        now = services.clock.now()
        reply = _mapping(context.variables.get("reply"))
        shipment = context.variables.get("lead")
        shipment = shipment if isinstance(shipment, dict) else None
        message_id = context.variables.get("reply_message_id")
        existing = (
            await services.session.scalar(
                select(Lead).where(Lead.message_id == uuid.UUID(str(message_id))).limit(1)
            )
            if message_id
            else None
        )
        if existing is not None:
            existing.enrollment_id = existing.enrollment_id or context.enrollment_id
            if config.priority == "high":
                existing.priority = LeadPriority.HIGH
            existing.shipment = existing.shipment or shipment
            existing.updated_at = now
            context.output["lead_id"] = str(existing.id)
            context.output["reused_lead"] = True
            return Continue()
        lead = Lead(
            tenant_id=context.tenant_id,
            contact_id=subject.contact.id,
            company_id=subject.company.id if subject.company else None,
            office_id=subject.office.id if subject.office else None,
            message_id=uuid.UUID(str(message_id)) if message_id else None,
            enrollment_id=context.enrollment_id,
            status=LeadStatus.NEW,
            priority=LeadPriority(config.priority),
            source=f"workflow:{subject.workflow.name}"[:50],
            summary=reply.get("summary"),
            shipment=shipment,
            created_at=now,
            updated_at=now,
        )
        services.session.add(lead)
        await services.session.flush()
        context.output["lead_id"] = str(lead.id)
        deal = await open_deal_for_lead(
            services.session,
            services.clock,
            lead,
            actor=f"enrollment:{context.enrollment_id}",
            task_queue=services.task_queue,
        )
        context.output["deal_id"] = str(deal.id)
        if config.notify_pricing:
            await services.notifier.notify(
                NotificationMessage(
                    channel="pricing",
                    recipient=services.settings.pricing_notification_recipient,
                    subject=f"New {config.priority} lead: {subject.contact.display_name}",
                    body=describe_lead(subject.company.name if subject.company else None, shipment),
                    payload={"lead_id": str(lead.id), "contact_id": str(subject.contact.id)},
                    contact_id=subject.contact.id,
                    enrollment_id=context.enrollment_id,
                )
            )
        return Continue()


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _place(place: object) -> str:
    data = _mapping(place)
    return str(data.get("city") or data.get("port_code") or data.get("country") or "?")


def describe_lead(company: str | None, shipment: dict[str, Any] | None) -> str:
    """One-glance summary of a lead for the pricing notification.

    Args:
        company: Company name.
        shipment: Extracted shipment details.

    Returns:
        Multi-line text.
    """
    parts = [f"Company: {company or 'unknown'}"]
    if shipment:
        parts += [
            f"Route: {_place(shipment.get('origin'))} -> {_place(shipment.get('destination'))}",
            f"Mode: {shipment.get('mode') or 'unknown'}",
            f"Containers: {shipment.get('container_quantity') or '?'} x "
            f"{shipment.get('container_type') or '?'}",
            f"Incoterm: {shipment.get('incoterm') or '?'}",
        ]
    return "\n".join(parts)


class NotifyOfficeOwnerConfig(NodeConfig):
    """What to tell the office owner."""

    message: NotificationText
    include_reply_summary: bool = Field(
        default=True, description="Attach the AI summary of the latest reply."
    )


@register_node
class NotifyOfficeOwnerNode(BaseNode[NotifyOfficeOwnerConfig]):
    """Notifies the owner of the contact's office."""

    node_type = "notify_office_owner"
    category = NodeCategory.ACTION
    display_name = "Notify office owner"
    description = "Sends a notification to the owner of the office handling this contact."
    config_model = NotifyOfficeOwnerConfig
    is_terminal = True

    async def execute(
        self, context: ExecutionContext, config: NotifyOfficeOwnerConfig
    ) -> NodeResult:
        """Notify the owner of the contact's office."""
        services = context.require_services()
        subject = context.require_subject()
        if subject.office is None:
            return Fail(error="the contact has no office, so no owner to notify", retryable=False)
        body = render_notification_text(config.message, notification_values(context))
        reply = _mapping(context.variables.get("reply"))
        if config.include_reply_summary and reply.get("summary"):
            body += f"\n\nReply ({reply.get('bucket')}): {reply['summary']}"
        await services.notifier.notify(
            NotificationMessage(
                channel="office_owner",
                recipient=subject.office.owner_email,
                subject=f"{subject.workflow.name}: {subject.contact.display_name}",
                body=body,
                payload={
                    "contact_id": str(subject.contact.id),
                    "office_id": str(subject.office.id),
                },
                contact_id=subject.contact.id,
                enrollment_id=context.enrollment_id,
            )
        )
        return Continue()


class NotifyTeamsChannelConfig(NodeConfig):
    """Which Teams channel to post to."""

    channel: str = Field(
        pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$",
        description="Configured channel name, for example `pricing-desk`.",
    )
    message: NotificationText


@register_node
class NotifyTeamsChannelNode(BaseNode[NotifyTeamsChannelConfig]):
    """Posts a message to a Microsoft Teams channel."""

    node_type = "notify_teams_channel"
    category = NodeCategory.ACTION
    display_name = "Notify Teams channel"
    description = "Posts a message to a configured Microsoft Teams channel."
    config_model = NotifyTeamsChannelConfig
    is_terminal = True

    async def execute(
        self, context: ExecutionContext, config: NotifyTeamsChannelConfig
    ) -> NodeResult:
        """Post to the Teams channel."""
        services = context.require_services()
        subject = context.require_subject()
        await services.notifier.notify(
            NotificationMessage(
                channel=f"teams:{config.channel}",
                recipient=config.channel,
                subject=f"{subject.workflow.name}: {subject.contact.display_name}",
                body=render_notification_text(config.message, notification_values(context)),
                payload={"contact_id": str(subject.contact.id)},
                contact_id=subject.contact.id,
                enrollment_id=context.enrollment_id,
            )
        )
        return Continue()


class TagConfig(NodeConfig):
    """Which tag."""

    tag: TagName


@register_node
class AddTagNode(BaseNode[TagConfig]):
    """Adds a tag to the contact."""

    node_type = "add_tag"
    category = NodeCategory.ACTION
    display_name = "Add tag"
    description = "Adds a tag to the contact (other workflows may start on it)."
    config_model = TagConfig

    async def execute(self, context: ExecutionContext, config: TagConfig) -> NodeResult:
        """Add the tag and publish `contact.tag_added` when it is new."""
        services = context.require_services()
        contact = context.require_subject().contact
        if any(tag.casefold() == config.tag.casefold() for tag in contact.tags or []):
            return Continue()
        contact.tags = [*(contact.tags or []), config.tag]
        contact.updated_at = services.clock.now()
        await publish_event(
            services.session,
            services.clock,
            event_type=EventType.CONTACT_TAG_ADDED,
            contact_id=contact.id,
            payload={"tag": config.tag, "source": f"enrollment:{context.enrollment_id}"},
            idempotency_key=f"tag:{context.idempotency_key}",
            task_queue=services.task_queue,
        )
        return Continue()


@register_node
class RemoveTagNode(BaseNode[TagConfig]):
    """Removes a tag from the contact."""

    node_type = "remove_tag"
    category = NodeCategory.ACTION
    display_name = "Remove tag"
    description = "Removes a tag from the contact if present."
    config_model = TagConfig

    async def execute(self, context: ExecutionContext, config: TagConfig) -> NodeResult:
        """Remove the tag (case-insensitive)."""
        contact = context.require_subject().contact
        remaining = [tag for tag in contact.tags or [] if tag.casefold() != config.tag.casefold()]
        if len(remaining) != len(contact.tags or []):
            contact.tags = remaining
            contact.updated_at = context.require_services().clock.now()
        return Continue()


class ChangeContactStageConfig(NodeConfig):
    """Target funnel stage."""

    stage: ContactStage


@register_node
class ChangeContactStageNode(BaseNode[ChangeContactStageConfig]):
    """Moves the contact to another funnel stage."""

    node_type = "change_contact_stage"
    category = NodeCategory.ACTION
    display_name = "Change contact stage"
    description = "Moves the contact to the chosen funnel stage."
    config_model = ChangeContactStageConfig

    async def execute(
        self, context: ExecutionContext, config: ChangeContactStageConfig
    ) -> NodeResult:
        """Set the stage (a change publishes `contact.stage_changed`)."""
        services = context.require_services()
        contact = context.require_subject().contact
        if contact.stage is not config.stage:
            previous = contact.stage
            contact.stage = config.stage
            contact.updated_at = services.clock.now()
            await publish_contact_stage_changed(
                services.session, services.clock, contact, previous, services.task_queue
            )
        return Continue()


class AssignOwnerConfig(NodeConfig):
    """Who becomes the owner."""

    owner_email: EmailStr = Field(description="Email of the LaneReach user who owns the contact.")


@register_node
class AssignOwnerNode(BaseNode[AssignOwnerConfig]):
    """Assigns the contact to a user."""

    node_type = "assign_owner"
    category = NodeCategory.ACTION
    display_name = "Assign owner"
    description = "Makes the chosen user the owner of the contact."
    config_model = AssignOwnerConfig

    async def execute(self, context: ExecutionContext, config: AssignOwnerConfig) -> NodeResult:
        """Set the owner."""
        contact = context.require_subject().contact
        contact.owner_email = str(config.owner_email).lower()
        contact.updated_at = context.require_services().clock.now()
        return Continue()


class EnrollInWorkflowConfig(NodeConfig):
    """The workflow to hand the contact to."""

    workflow_id: uuid.UUID = Field(description="A published workflow (not this one).")


@register_node
class EnrollInWorkflowNode(BaseNode[EnrollInWorkflowConfig]):
    """Starts the contact on another workflow (for example a nurture sequence)."""

    node_type = "enroll_in_workflow"
    category = NodeCategory.ACTION
    display_name = "Enroll in workflow"
    description = (
        "Starts the contact on another published workflow, whatever its trigger. Skipped when "
        "the contact is already in it."
    )
    config_model = EnrollInWorkflowConfig

    async def execute(
        self, context: ExecutionContext, config: EnrollInWorkflowConfig
    ) -> NodeResult:
        """Enroll the contact; an unusable target fails the step (it will not fix itself)."""
        services = context.require_services()
        subject = context.require_subject()
        if config.workflow_id == subject.workflow.id:
            return Fail(error="a workflow cannot enroll contacts into itself", retryable=False)
        target = await services.session.get(Workflow, config.workflow_id)
        if target is None:
            return Fail(error="the target workflow does not exist", retryable=False)
        attempt = await enroll_contact_in_workflow(
            services.session,
            services.clock,
            workflow=target,
            contact=subject.contact,
            enrolled_by=f"enrollment:{context.enrollment_id}",
            task_queue=services.task_queue,
        )
        if attempt.skipped in {
            EnrollmentSkipReason.WORKFLOW_NOT_PUBLISHED,
            EnrollmentSkipReason.WORKFLOW_PAUSED,
        }:
            return Fail(error=f"'{target.name}' is not running", retryable=False)
        context.output["enrolled_in"] = {
            "workflow_id": str(target.id),
            "enrollment_id": str(attempt.enrollment.id) if attempt.enrollment else None,
            "skipped": attempt.skipped.value if attempt.skipped else None,
        }
        return Continue()
