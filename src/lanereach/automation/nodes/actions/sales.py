"""Action nodes for the sales desk: draft a quote, update the contact, share out new leads.

`create_quote_draft` prepares the pricing team's work: priced from the rate sheets when a sheet
covers the lane, else the usual charges of the deal's mode with empty prices. It never sends a
quote. `update_contact_field` only changes a fixed list of fields, and
`assign_round_robin` rotates owners across a list of users.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import EmailStr, Field
from sqlalchemy import func, select

from lanereach.automation.engine.context import ExecutionContext
from lanereach.automation.engine.results import Continue, NodeResult
from lanereach.automation.enrollments.models import Enrollment, EnrollmentStep
from lanereach.automation.nodes.actions.connect import workflow_deal
from lanereach.automation.nodes.actions.crm import DEAL_VARIABLE, _values
from lanereach.automation.nodes.base import BaseNode, NodeCategory, NodeConfig
from lanereach.automation.nodes.registry import register_node
from lanereach.domain.contacts.models import ContactStage
from lanereach.domain.templates.rendering import render_template_text

QUOTE_VARIABLE = "quote_id"
ROUND_ROBIN_OUTPUT = "assigned_owner"


class CreateQuoteDraftConfig(NodeConfig):
    """How the draft starts."""

    validity_days: int = Field(
        default=14, ge=1, le=90, description="Days the quote stays valid once sent."
    )
    notes: str = Field(
        default="",
        max_length=2000,
        description="Notes printed on the quote, e.g. what the rates exclude.",
    )
    price_from_rate_sheets: bool = Field(
        default=True,
        description="Fill prices from the best matching rate-sheet rates when a sheet covers "
        "the lane (otherwise the typical charges are added with empty prices).",
    )
    markup_percent: Decimal = Field(
        default=Decimal(0),
        ge=0,
        le=100,
        decimal_places=2,
        description="Added to sheet prices (leave 0 when the sheet already holds selling rates).",
    )


@register_node
class CreateQuoteDraftNode(BaseNode[CreateQuoteDraftConfig]):
    """Starts a quote on the contact's open deal with the usual charges of its mode."""

    node_type = "create_quote_draft"
    category = NodeCategory.ACTION
    display_name = "Create quote draft"
    description = (
        "Starts a draft quote on the contact's open deal, priced from the rate sheets when one "
        "covers the lane, else with the typical charges of the deal's service line and empty "
        "prices for the pricing team. It never sends the quote."
    )
    config_model = CreateQuoteDraftConfig

    async def execute(
        self, context: ExecutionContext, config: CreateQuoteDraftConfig
    ) -> NodeResult:
        """Create the draft, or continue when the contact has no open deal."""
        # Imported here: the quote service depends on the engine, not the other way round.
        from datetime import timedelta

        from lanereach.ai.workflow_steps import ai_runtime_for
        from lanereach.core.errors import BusinessRuleViolationError
        from lanereach.domain.logistics import charge_presets
        from lanereach.domain.pricing.service import PricingService
        from lanereach.domain.quotes.models import ChargeType
        from lanereach.domain.quotes.service import LineInput, QuoteInput, QuoteService

        services = context.require_services()
        deal = await workflow_deal(context)
        if deal is None:
            context.output["skipped"] = "no open deal"
            return Continue()
        quotes = QuoteService(services.session, services.clock, services.task_queue)
        actor = f"enrollment:{context.enrollment_id}"
        if config.price_from_rate_sheets:
            pricing = PricingService(
                services.session, services.clock, quotes, ai_runtime_for(services, context)
            )
            try:
                priced = await pricing.draft_quote_from_rates(
                    deal.id,
                    actor,
                    markup_percent=config.markup_percent,
                    validity_days=config.validity_days,
                    notes=config.notes,
                )
            except BusinessRuleViolationError:
                context.output["rate_sheets"] = "no rate covers the lane"
            else:
                context.variables[DEAL_VARIABLE] = str(deal.id)
                context.variables[QUOTE_VARIABLE] = str(priced.quote.id)
                context.output["quote_number"] = priced.quote.number
                context.output["priced_from"] = priced.option.sheet_name
                return Continue()
        lines = [
            LineInput(
                charge_type=ChargeType(preset.charge_type),
                description=preset.description,
                quantity=Decimal(1),
                unit=preset.unit,
                unit_price=Decimal("0.00"),
            )
            for preset in charge_presets(deal.service_line)
        ]
        quote = await quotes.create_quote(
            deal.id,
            actor,
            QuoteInput(
                currency=deal.currency,
                valid_until=(context.now + timedelta(days=config.validity_days)).date(),
                notes=config.notes,
                lines=lines,
            ),
        )
        context.variables[DEAL_VARIABLE] = str(deal.id)
        context.variables[QUOTE_VARIABLE] = str(quote.id)
        context.output["quote_number"] = quote.number
        return Continue()


ContactField = Literal[
    "language", "country", "job_title", "city", "timezone", "stage", "owner_email"
]


class UpdateContactFieldConfig(NodeConfig):
    """Which field gets which value."""

    field_name: ContactField = Field(description="The contact field to set.")
    value: str = Field(
        min_length=1,
        max_length=200,
        description="The new value; placeholders like {{company.name}} work.",
    )


@register_node
class UpdateContactFieldNode(BaseNode[UpdateContactFieldConfig]):
    """Sets one field of the contact (language, country, job title, stage, owner...)."""

    node_type = "update_contact_field"
    category = NodeCategory.ACTION
    display_name = "Update contact field"
    description = (
        "Sets the contact's language, country, city, time zone, job title, stage or owner. "
        "An invalid value (for example an unknown country code) leaves the contact unchanged."
    )
    config_model = UpdateContactFieldConfig

    async def execute(
        self, context: ExecutionContext, config: UpdateContactFieldConfig
    ) -> NodeResult:
        """Set the field; an unusable value is recorded and skipped, never guessed."""
        services = context.require_services()
        contact = context.require_subject().contact
        value = render_template_text(config.value, _values(context), lenient=True).strip()
        normalised = normalise_contact_value(config.field_name, value)
        if normalised is None:
            context.output["skipped"] = f"invalid {config.field_name}"
            return Continue()
        if config.field_name == "stage":
            from lanereach.domain.contacts.service import publish_contact_stage_changed

            stage = ContactStage(normalised)
            if contact.stage is not stage:
                previous = contact.stage
                contact.stage = stage
                await publish_contact_stage_changed(
                    services.session, services.clock, contact, previous, services.task_queue
                )
        else:
            setattr(contact, config.field_name, normalised)
        contact.updated_at = services.clock.now()
        context.output["updated"] = config.field_name
        return Continue()


def normalise_contact_value(field_name: str, value: str) -> str | None:
    """The value as the contact stores it, or None when it is not valid for the field.

    Args:
        field_name: One of `ContactField`.
        value: Rendered value.

    Returns:
        The value to store, or None.
    """
    if not value:
        return None
    if field_name in ("language", "country"):
        code = value.strip().lower() if field_name == "language" else value.strip().upper()
        return code if len(code) == 2 and code.isalpha() else None
    if field_name == "stage":
        return value if value in {stage.value for stage in ContactStage} else None
    if field_name == "owner_email":
        return value.lower() if "@" in value and " " not in value else None
    if field_name == "timezone":
        from zoneinfo import available_timezones

        return value if value in available_timezones() else None
    limit = 100 if field_name == "city" else 200
    return value[:limit]


class AssignRoundRobinConfig(NodeConfig):
    """The users to share contacts between."""

    owners: list[EmailStr] = Field(
        min_length=2, max_length=20, description="Users who take turns owning new contacts."
    )


@register_node
class AssignRoundRobinNode(BaseNode[AssignRoundRobinConfig]):
    """Gives each contact to the next user in the list."""

    node_type = "assign_round_robin"
    category = NodeCategory.ACTION
    display_name = "Assign owner (round robin)"
    description = (
        "Shares contacts fairly: each contact reaching this step goes to the next user in the "
        "list, in turn."
    )
    config_model = AssignRoundRobinConfig

    async def execute(
        self, context: ExecutionContext, config: AssignRoundRobinConfig
    ) -> NodeResult:
        """Assign the next owner.

        The turn is the number of contacts this step already handled, so the rotation needs no
        extra state and survives restarts.
        """
        services = context.require_services()
        subject = context.require_subject()
        handled = await services.session.scalar(
            select(func.count())
            .select_from(EnrollmentStep)
            .join(Enrollment, Enrollment.id == EnrollmentStep.enrollment_id)
            .where(
                Enrollment.workflow_id == subject.workflow.id,
                EnrollmentStep.node_id == context.node_id,
                EnrollmentStep.output.has_key(ROUND_ROBIN_OUTPUT),
            )
        )
        owner = str(config.owners[int(handled or 0) % len(config.owners)]).lower()
        subject.contact.owner_email = owner
        subject.contact.updated_at = services.clock.now()
        context.output[ROUND_ROBIN_OUTPUT] = owner
        return Continue()
