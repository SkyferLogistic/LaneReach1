"""Trigger nodes. A workflow has exactly one; it decides which events enroll a contact.

Triggers do not do work themselves: the event matcher (M4) enrolls a contact when an event of
the trigger's `event_type` arrives and the trigger's config matches. Executing a trigger simply
continues to the first node.
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from lanereach.ai.schemas import ReplyBucket
from lanereach.automation.conditions import ConditionExpression
from lanereach.automation.engine.context import ExecutionContext
from lanereach.automation.engine.results import Continue, NodeResult
from lanereach.automation.nodes.base import BaseNode, EmptyNodeConfig, NodeCategory, NodeConfig
from lanereach.automation.nodes.fields import TagName
from lanereach.automation.nodes.registry import register_node
from lanereach.domain.contacts.models import ContactStage
from lanereach.domain.crm.models import StageKind
from lanereach.domain.leads.models import LeadStatus

_FILTER_DESCRIPTION = "Only enroll contacts matching this condition. Leave empty for everyone."


class TriggerNode[ConfigT: NodeConfig](BaseNode[ConfigT]):
    """Base for triggers: declares the event type that can fire it."""

    category = NodeCategory.TRIGGER
    event_type: ClassVar[str]

    async def execute(self, context: ExecutionContext, config: ConfigT) -> NodeResult:
        """Continue to the first node of the workflow."""
        return Continue()


class ContactFilterConfig(NodeConfig):
    """Optional audience filter."""

    condition: ConditionExpression | None = Field(default=None, description=_FILTER_DESCRIPTION)


@register_node
class ContactCreatedTrigger(TriggerNode[ContactFilterConfig]):
    """Enrolls contacts when they are added to LaneReach."""

    node_type = "trigger_contact_created"
    display_name = "Contact created"
    description = "Starts when a new contact is added (import, API or manually)."
    config_model = ContactFilterConfig
    event_type = "contact.created"


class TagAddedConfig(ContactFilterConfig):
    """Which tag starts the workflow."""

    tag: TagName


@register_node
class TagAddedTrigger(TriggerNode[TagAddedConfig]):
    """Enrolls contacts when a tag is added to them."""

    node_type = "trigger_tag_added"
    display_name = "Tag added"
    description = "Starts when the chosen tag is added to a contact."
    config_model = TagAddedConfig
    event_type = "contact.tag_added"


class ReplyClassifiedConfig(NodeConfig):
    """Which reply buckets start the workflow."""

    buckets: list[ReplyBucket] = Field(
        min_length=1,
        description="Start when an inbound reply is classified into one of these buckets.",
        examples=[["rate_query", "service_query"]],
    )


@register_node
class ReplyClassifiedTrigger(TriggerNode[ReplyClassifiedConfig]):
    """Enrolls the replying contact when their reply is classified."""

    node_type = "trigger_reply_classified"
    display_name = "Reply classified"
    description = "Starts when a contact's reply is classified into one of the chosen buckets."
    config_model = ReplyClassifiedConfig
    event_type = "reply.classified"


@register_node
class ManualEnrollTrigger(TriggerNode[EmptyNodeConfig]):
    """Enrolls contacts only when a user (or the API) enrolls them explicitly."""

    node_type = "trigger_manual_enroll"
    display_name = "Manual enrollment"
    description = "Starts when contacts are enrolled by hand or through the API."
    event_type = "manual.enroll"


class ScheduledDateConfig(ContactFilterConfig):
    """When to enroll the matching audience."""

    run_at: AwareDatetime = Field(
        description="Enroll matching contacts once this moment has passed (with timezone).",
        examples=["2026-10-05T08:00:00+04:00"],
    )


@register_node
class ScheduledDateTrigger(TriggerNode[ScheduledDateConfig]):
    """Enrolls a filtered audience at a set date and time."""

    node_type = "trigger_scheduled_date"
    display_name = "Scheduled date"
    description = "Starts at the chosen date and time for every contact matching the filter."
    config_model = ScheduledDateConfig
    event_type = "schedule.tick"


class LeadStatusChangedConfig(ContactFilterConfig):
    """Which lead statuses start the workflow."""

    statuses: list[LeadStatus] = Field(
        min_length=1,
        description="Start when pricing moves a lead of the contact into one of these statuses.",
        examples=[["won"]],
    )


@register_node
class LeadStatusChangedTrigger(TriggerNode[LeadStatusChangedConfig]):
    """Enrolls the lead's contact when pricing changes the lead's status."""

    node_type = "trigger_lead_status_changed"
    display_name = "Lead status changed"
    description = "Starts when a lead is marked quoting, quoted, won or lost (the ones chosen)."
    config_model = LeadStatusChangedConfig
    event_type = "lead.status_changed"


class CustomEventConfig(ContactFilterConfig):
    """Which custom event starts the workflow."""

    event_name: str = Field(
        pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$",
        description="The `payload.name` your CRM, website or script sends with a `custom.event`.",
        examples=["quote_requested"],
    )


@register_node
class CustomEventTrigger(TriggerNode[CustomEventConfig]):
    """Enrolls contacts when another system reports something happened."""

    node_type = "trigger_custom_event"
    display_name = "Custom event"
    description = (
        "Starts when your CRM, website or another system sends an event with this name "
        "(POST /automation/events with an API key)."
    )
    config_model = CustomEventConfig
    event_type = "custom.event"


class DealStageChangedConfig(ContactFilterConfig):
    """Which stage changes start the workflow (any listed name or kind matches)."""

    pipeline_id: uuid.UUID | None = Field(default=None, description="Empty = any pipeline.")
    stage_names: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Stage names, e.g. Quoted.",
        examples=[["Quoted"]],
    )
    kinds: list[StageKind] = Field(
        default_factory=list, description="Or stage kinds: open, won, lost.", examples=[["won"]]
    )

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.stage_names and not self.kinds:
            raise ValueError("list stage names or kinds")
        return self


@register_node
class DealStageChangedTrigger(TriggerNode[DealStageChangedConfig]):
    """Enrolls the deal's contact when the deal reaches a stage."""

    node_type = "trigger_deal_stage_changed"
    display_name = "Deal stage changed"
    description = "Starts when a CRM deal is moved to one of the chosen stages (or won / lost)."
    config_model = DealStageChangedConfig
    event_type = "deal.stage_changed"


@register_node
class MeetingBookedTrigger(TriggerNode[ContactFilterConfig]):
    """Enrolls a contact who booked a meeting (Calendly or Cal.com)."""

    node_type = "trigger_meeting_booked"
    display_name = "Meeting booked"
    description = (
        "Starts when a contact books (or reschedules) a meeting through a connected Calendly "
        "or Cal.com account. `meeting_id` and `meeting_starts_at` become enrollment variables."
    )
    config_model = ContactFilterConfig
    event_type = "meeting.booked"


class FormSubmittedConfig(ContactFilterConfig):
    """Which web forms start the workflow."""

    form_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=20, description="Empty = any form."
    )


@register_node
class FormSubmittedTrigger(TriggerNode[FormSubmittedConfig]):
    """Enrolls a contact who sent a web form (for example "Request a quote")."""

    node_type = "trigger_form_submitted"
    display_name = "Web form submitted"
    description = (
        "Starts when someone sends one of your website forms. The answers (origin, "
        "destination, mode, message...) become enrollment variables."
    )
    config_model = FormSubmittedConfig
    event_type = "form.submitted"


@register_node
class EmailOpenedTrigger(TriggerNode[ContactFilterConfig]):
    """Enrolls a contact who opened a tracked email."""

    node_type = "trigger_email_opened"
    display_name = "Email opened"
    description = (
        "Starts the first time a contact opens a tracked email (mail scanners excluded). "
        "Apple Mail may count as opened."
    )
    config_model = ContactFilterConfig
    event_type = "email.opened"


class EmailClickedConfig(ContactFilterConfig):
    """Which clicks start the workflow."""

    url_contains: str | None = Field(
        default=None,
        max_length=200,
        description="Only links containing this text, e.g. `rates` or `/air-freight`.",
    )


@register_node
class EmailClickedTrigger(TriggerNode[EmailClickedConfig]):
    """Enrolls a contact who clicked a link in a tracked email."""

    node_type = "trigger_email_clicked"
    display_name = "Link clicked"
    description = "Starts when a contact clicks a link (optionally only links containing a text)."
    config_model = EmailClickedConfig
    event_type = "email.clicked"


class ContactStageChangedConfig(ContactFilterConfig):
    """Which stages start the workflow."""

    stages: list[ContactStage] = Field(min_length=1, description="The stages to react to.")


@register_node
class ContactStageChangedTrigger(TriggerNode[ContactStageChangedConfig]):
    """Enrolls a contact who moved to a funnel stage."""

    node_type = "trigger_contact_stage_changed"
    display_name = "Contact stage changed"
    description = "Starts when a contact moves to one of the chosen stages (e.g. customer)."
    config_model = ContactStageChangedConfig
    event_type = "contact.stage_changed"


class QuoteStatusChangedConfig(ContactFilterConfig):
    """Which quote outcomes start the workflow."""

    statuses: list[Literal["sent", "accepted", "rejected", "expired"]] = Field(
        min_length=1, description="For example `expired` to chase, `accepted` to hand over."
    )


@register_node
class QuoteStatusChangedTrigger(TriggerNode[QuoteStatusChangedConfig]):
    """Enrolls the contact when one of their quotes is sent, accepted, rejected or expires."""

    node_type = "trigger_quote_status_changed"
    display_name = "Quote status changed"
    description = (
        "Starts when a quote is sent, accepted, rejected or expires. `quote_id`, `quote_number` "
        "and `deal_id` become enrollment variables."
    )
    config_model = QuoteStatusChangedConfig
    event_type = "quote.status_changed"


@register_node
class ReplyReceivedTrigger(TriggerNode[ContactFilterConfig]):
    """Enrolls a contact as soon as any reply from them arrives."""

    node_type = "trigger_reply_received"
    display_name = "Reply received"
    description = (
        "Starts when any reply from a contact arrives, before the AI classifies it (auto-replies "
        "and bounces excluded). Use Reply classified to react to what the reply says."
    )
    config_model = ContactFilterConfig
    event_type = "reply.received"


@register_node
class LeadCreatedTrigger(TriggerNode[ContactFilterConfig]):
    """Enrolls the contact of a new quote request."""

    node_type = "trigger_lead_created"
    display_name = "Quote request created"
    description = (
        "Starts when a new quote request (lead) is created, from a reply, a web form or by hand. "
        "`lead_id` and `deal_id` become enrollment variables."
    )
    config_model = ContactFilterConfig
    event_type = "lead.created"


@register_node
class MeetingCanceledTrigger(TriggerNode[ContactFilterConfig]):
    """Enrolls a contact who canceled a booked meeting."""

    node_type = "trigger_meeting_canceled"
    display_name = "Meeting canceled"
    description = "Starts when a contact cancels a meeting booked through Calendly or Cal.com."
    config_model = ContactFilterConfig
    event_type = "meeting.canceled"


type BounceType = Literal["hard", "soft"]


class EmailBouncedConfig(ContactFilterConfig):
    """Which bounces start the workflow."""

    bounce_types: list[BounceType] = Field(
        default_factory=lambda: list[BounceType](["hard"]),
        min_length=1,
        description="Hard = the address does not exist; soft = a temporary problem (full mailbox).",
    )


@register_node
class EmailBouncedTrigger(TriggerNode[EmailBouncedConfig]):
    """Enrolls a contact whose email bounced."""

    node_type = "trigger_email_bounced"
    display_name = "Email bounced"
    description = (
        "Starts when an email to the contact bounces. Hard bounces are already suppressed by the "
        "guardrails; use this to tell the owner or find another contact at the company."
    )
    config_model = EmailBouncedConfig
    event_type = "email.bounced"


class QuoteExpiringConfig(ContactFilterConfig):
    """How long before the validity date."""

    days_before: Literal[1, 2, 3, 5, 7] = Field(
        default=3, description="Days left before the sent quote expires."
    )


@register_node
class QuoteExpiringTrigger(TriggerNode[QuoteExpiringConfig]):
    """Enrolls the contact a few days before their sent quote expires."""

    node_type = "trigger_quote_expiring"
    display_name = "Quote expiring soon"
    description = (
        "Starts the chosen number of days before a sent quote's validity date, while it is still "
        "unanswered. `quote_id`, `quote_number`, `deal_id` and `quote_valid_until` become "
        "enrollment variables."
    )
    config_model = QuoteExpiringConfig
    event_type = "quote.expiring"


class DealIdleConfig(ContactFilterConfig):
    """How long a deal sits in a stage."""

    days: Literal[3, 7, 14, 30] = Field(default=7, description="Days in the same stage.")
    pipeline_id: uuid.UUID | None = Field(default=None, description="Empty = any pipeline.")
    stage_names: list[str] = Field(
        default_factory=list, max_length=12, description="Empty = any open stage."
    )


@register_node
class DealIdleTrigger(TriggerNode[DealIdleConfig]):
    """Enrolls the deal's contact when an open deal has not moved for a while."""

    node_type = "trigger_deal_idle"
    display_name = "Deal idle"
    description = (
        "Starts when an open deal has stayed in the same stage for the chosen number of days "
        "(checked every hour). `deal_id` and `deal_stage` become enrollment variables."
    )
    config_model = DealIdleConfig
    event_type = "deal.idle"


def trigger_event_type(node_class: type[BaseNode[Any]]) -> str | None:
    """Return the event type a trigger listens to.

    Args:
        node_class: A registered node class.

    Returns:
        The event type, or None if the class is not a trigger.
    """
    return node_class.event_type if issubclass(node_class, TriggerNode) else None
