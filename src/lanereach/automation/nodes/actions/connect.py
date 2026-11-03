"""Action nodes that reach other tools or update records.

They verify the address, send to a webhook, post to Slack, add a note and update the deal.

Webhook and Slack messages are queued as `integration_deliveries` and sent by the delivery run
with the same signatures, retries and SSRF checks as event deliveries; a node never calls an
outside URL itself, so a slow receiver can never hold up an enrollment.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
from pydantic import EmailStr, Field
from sqlalchemy import select

from lanereach.automation.engine.context import ExecutionContext
from lanereach.automation.engine.results import Continue, Fail, NodeResult
from lanereach.automation.nodes.actions.crm import DEAL_VARIABLE, _render, _values
from lanereach.automation.nodes.base import BaseNode, NodeCategory, NodeConfig
from lanereach.automation.nodes.registry import register_node
from lanereach.core.secrets import build_secret_box
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.crm.models import Deal, DealStatus
from lanereach.domain.crm.service import DealService, NoteService
from lanereach.domain.templates.rendering import render_template_text
from lanereach.integrations.outbound.models import (
    DeliveryStatus,
    DeliveryTarget,
    IntegrationConnection,
    IntegrationDelivery,
    IntegrationKind,
    WebhookSubscription,
)
from lanereach.integrations.verification.service import (
    build_email_verifier,
    verify_contact_emails,
)
from lanereach.integrations.verification.verifier import EmailStatus

VALID, RISKY, INVALID, UNKNOWN = "valid", "risky", "invalid", "unknown"
EMAIL_STATUS_HANDLES = (VALID, RISKY, INVALID, UNKNOWN)
# Enrollment variables worth handing to another system (never message bodies).
_SHARED_VARIABLES = (
    "reply",
    "lead",
    "deal_id",
    "deal_stage",
    "meeting_id",
    "form_id",
    "event_name",
)


def email_status_handle(status: str) -> str:
    """The output a stored email status leaves by (catch-all counts as risky).

    Args:
        status: `contacts.email_status`.

    Returns:
        One of `EMAIL_STATUS_HANDLES`.
    """
    if status == EmailStatus.CATCH_ALL:
        return RISKY
    return status if status in EMAIL_STATUS_HANDLES else UNKNOWN


async def workflow_deal(context: ExecutionContext) -> Deal | None:
    """The enrollment's deal.

    That is the one the workflow created or moved, else the contact's latest open deal.

    Args:
        context: The executing step.

    Returns:
        The deal, or None.
    """
    services = context.require_services()
    known = context.variables.get(DEAL_VARIABLE)
    if known:
        deal = await services.session.get(Deal, uuid.UUID(str(known)))
        if deal is not None:
            return deal
    return await services.session.scalar(
        select(Deal)
        .where(Deal.contact_id == context.contact_id, Deal.status == DealStatus.OPEN)
        .order_by(Deal.updated_at.desc())
        .limit(1)
    )


def _delivery_id(context: ExecutionContext) -> uuid.UUID:
    # Stable per step: a retried step finds its delivery instead of queueing a second one.
    return uuid.uuid5(
        uuid.NAMESPACE_URL, f"lanereach:{context.tenant_id}:{context.idempotency_key}"
    )


async def _queue(
    context: ExecutionContext,
    target_type: DeliveryTarget,
    target_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> IntegrationDelivery:
    services = context.require_services()
    delivery_id = _delivery_id(context)
    existing = await services.session.get(IntegrationDelivery, delivery_id)
    if existing is not None:
        return existing
    now = services.clock.now()
    delivery = IntegrationDelivery(
        id=delivery_id,
        tenant_id=require_session_tenant_id(services.session),
        target_type=target_type,
        target_id=target_id,
        event_id=None,
        event_type=event_type,
        payload=payload,
        status=DeliveryStatus.PENDING,
        attempts=0,
        next_attempt_at=now,
        created_at=now,
    )
    services.session.add(delivery)
    await services.session.flush()
    return delivery


def _contact_payload(context: ExecutionContext) -> dict[str, Any]:
    subject = context.require_subject()
    contact = subject.contact
    return {
        "id": str(contact.id),
        "email": contact.email,
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "company": subject.company.name if subject.company else None,
        "country": contact.country,
        "office_id": str(contact.office_id) if contact.office_id else None,
    }


class VerifyEmailConfig(NodeConfig):
    """Verification options."""

    reuse_days: int = Field(
        default=30,
        ge=0,
        le=365,
        description="Reuse a verdict younger than this many days instead of checking again.",
    )


@register_node
class VerifyEmailNode(BaseNode[VerifyEmailConfig]):
    """Verifies the contact's address and branches on the result."""

    node_type = "verify_email"
    category = NodeCategory.ACTION
    display_name = "Verify email"
    description = (
        "Checks the address (MillionVerifier when connected, offline rules otherwise) and "
        "leaves by valid, risky, invalid or unknown. Invalid addresses are blocked from "
        "outreach anyway."
    )
    config_model = VerifyEmailConfig
    output_handles = EMAIL_STATUS_HANDLES

    async def execute(self, context: ExecutionContext, config: VerifyEmailConfig) -> NodeResult:
        """Verify (or reuse a recent verdict) and follow its output."""
        services = context.require_services()
        contact = context.require_subject().contact
        fresh = (
            contact.email_verified_at is not None
            and context.now - contact.email_verified_at < timedelta(days=config.reuse_days)
        )
        if not fresh:
            async with httpx.AsyncClient(follow_redirects=False) as http:
                verifier = await build_email_verifier(
                    services.session, http, build_secret_box(services.settings)
                )
                await verify_contact_emails(services.clock, verifier, [contact])
        handle = email_status_handle(contact.email_status)
        context.output["email_status"] = contact.email_status
        return Continue(handle)


class SendToWebhookConfig(NodeConfig):
    """Which webhook receives the step."""

    webhook_id: uuid.UUID = Field(description="A webhook from Integrations (Zapier, Make, n8n...).")
    label: str = Field(
        default="workflow_step",
        pattern=r"^[a-z0-9][a-z0-9_.-]{0,59}$",
        description="Sent as `type`, so one Zap can tell your steps apart.",
    )


@register_node
class SendToWebhookNode(BaseNode[SendToWebhookConfig]):
    """Sends the contact and the enrollment's data to a webhook."""

    node_type = "send_to_webhook"
    category = NodeCategory.ACTION
    display_name = "Send to webhook"
    description = (
        "Posts the contact, company and this enrollment's data (reply type, shipment, deal) to "
        "a webhook, signed and retried like every LaneReach webhook."
    )
    config_model = SendToWebhookConfig

    async def execute(self, context: ExecutionContext, config: SendToWebhookConfig) -> NodeResult:
        """Queue the delivery."""
        services = context.require_services()
        webhook = await services.session.get(WebhookSubscription, config.webhook_id)
        if webhook is None:
            return Fail(error="the webhook no longer exists", retryable=False)
        if not webhook.is_active:
            context.output["skipped"] = "webhook switched off"
            return Continue()
        delivery_id = _delivery_id(context)
        payload = {
            "id": str(delivery_id),
            "type": config.label,
            "occurred_at": context.now.isoformat(),
            "event_id": None,
            "data": {
                "workflow_id": str(context.require_subject().workflow.id),
                "enrollment_id": str(context.enrollment_id),
                "step": context.node_id,
                "variables": {
                    key: context.variables[key]
                    for key in _SHARED_VARIABLES
                    if key in context.variables
                },
                "contact": _contact_payload(context),
            },
        }
        delivery = await _queue(context, DeliveryTarget.WEBHOOK, webhook.id, config.label, payload)
        context.output["delivery_id"] = str(delivery.id)
        return Continue()


class NotifySlackConfig(NodeConfig):
    """Which channel and what to say."""

    connection_id: uuid.UUID = Field(description="A Slack connection from Integrations.")
    message: str = Field(
        min_length=1,
        max_length=1000,
        description="Placeholders like {{contact.full_name}}, {{company.name}}, {{office.name}}.",
        examples=["{{contact.full_name}} ({{company.name}}) is ready for a rate call"],
    )


@register_node
class NotifySlackNode(BaseNode[NotifySlackConfig]):
    """Posts a message about the contact to a Slack channel."""

    node_type = "notify_slack"
    category = NodeCategory.ACTION
    display_name = "Post to Slack"
    description = "Posts your message (with placeholders) and a link to the contact to Slack."
    config_model = NotifySlackConfig

    async def execute(self, context: ExecutionContext, config: NotifySlackConfig) -> NodeResult:
        """Queue the Slack message."""
        services = context.require_services()
        connection = await services.session.get(IntegrationConnection, config.connection_id)
        if connection is None or connection.kind is not IntegrationKind.SLACK:
            return Fail(error="the Slack connection no longer exists", retryable=False)
        text = render_template_text(config.message, _values(context), lenient=True)[:1000]
        payload = {
            "alert": "workflow_message",
            "data": {"message": text},
            "contact": _contact_payload(context),
        }
        await _queue(context, DeliveryTarget.SLACK, connection.id, "workflow_message", payload)
        return Continue()


class AddNoteConfig(NodeConfig):
    """The note."""

    body: str = Field(
        min_length=1,
        max_length=2000,
        description="Placeholders like {{contact.first_name}} and {{company.name}} work here.",
    )


@register_node
class AddNoteNode(BaseNode[AddNoteConfig]):
    """Adds a note to the enrollment's deal (or to the contact without one)."""

    node_type = "add_note"
    category = NodeCategory.ACTION
    display_name = "Add note"
    description = "Adds a note to the deal (or the contact) so the team sees what happened."
    config_model = AddNoteConfig

    async def execute(self, context: ExecutionContext, config: AddNoteConfig) -> NodeResult:
        """Add the note."""
        services = context.require_services()
        deal = await workflow_deal(context)
        note = await NoteService(services.session, services.clock).create_note(
            f"enrollment:{context.enrollment_id}",
            body=render_template_text(config.body, _values(context), lenient=True)[:2000],
            contact_id=context.contact_id,
            deal_id=deal.id if deal else None,
        )
        context.output["note_id"] = str(note.id)
        return Continue()


class UpdateDealConfig(NodeConfig):
    """What to change; empty fields stay as they are."""

    value_amount: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999.99"))
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    owner_email: EmailStr | None = None
    expected_close_in_days: int | None = Field(default=None, ge=0, le=365)
    title: str | None = Field(default=None, max_length=200, description="Placeholders allowed.")


@register_node
class UpdateDealNode(BaseNode[UpdateDealConfig]):
    """Changes the enrollment's deal: value, currency, owner, expected close, title."""

    node_type = "update_deal"
    category = NodeCategory.ACTION
    display_name = "Update deal"
    description = "Sets the deal's value, currency, owner, expected close date or title."
    config_model = UpdateDealConfig

    async def execute(self, context: ExecutionContext, config: UpdateDealConfig) -> NodeResult:
        """Update the deal, or continue when the contact has none."""
        services = context.require_services()
        deal = await workflow_deal(context)
        if deal is None:
            context.output["skipped"] = "no open deal"
            return Continue()
        changes: dict[str, Any] = {}
        if config.value_amount is not None:
            changes["value_amount"] = config.value_amount
        if config.currency:
            changes["currency"] = config.currency
        if config.owner_email:
            changes["owner_email"] = str(config.owner_email).lower()
        if config.expected_close_in_days is not None:
            changes["expected_close_date"] = (
                context.now + timedelta(days=config.expected_close_in_days)
            ).date()
        if config.title:
            changes["title"] = _render(config.title, context)
        if changes:
            await DealService(services.session, services.clock, services.task_queue).update_deal(
                deal.id, f"enrollment:{context.enrollment_id}", changes
            )
        context.variables[DEAL_VARIABLE] = str(deal.id)
        return Continue()
