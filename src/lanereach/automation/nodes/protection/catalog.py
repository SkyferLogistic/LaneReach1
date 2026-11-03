"""Protection nodes: stop outreach to a contact or company."""

from __future__ import annotations

import uuid
from datetime import timedelta

from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from lanereach.automation.engine.context import ExecutionContext
from lanereach.automation.engine.results import Continue, NodeResult
from lanereach.automation.enrollments.models import WorkflowCompanyPause
from lanereach.automation.nodes.base import BaseNode, NodeCategory, NodeConfig
from lanereach.automation.nodes.registry import register_node
from lanereach.domain.contacts.models import ContactStage
from lanereach.domain.suppressions.models import Suppression, SuppressionReason

_DO_NOT_CONTACT_REASONS = {
    SuppressionReason.DO_NOT_CONTACT,
    SuppressionReason.UNSUBSCRIBED,
    SuppressionReason.COMPLAINT,
}


class SuppressContactConfig(NodeConfig):
    """Why the contact is suppressed."""

    reason: SuppressionReason = SuppressionReason.DO_NOT_CONTACT


@register_node
class SuppressContactNode(BaseNode[SuppressContactConfig]):
    """Adds the contact to the suppression list."""

    node_type = "suppress_contact"
    category = NodeCategory.PROTECTION
    display_name = "Suppress contact"
    description = "Stops all email to this contact, across every workflow and office."
    config_model = SuppressContactConfig
    is_terminal = True

    async def execute(self, context: ExecutionContext, config: SuppressContactConfig) -> NodeResult:
        """Add a permanent suppression (once) and mark Do Not Contact when appropriate."""
        services = context.require_services()
        contact = context.require_subject().contact
        now = services.clock.now()
        existing = await services.session.scalar(
            select(func.count())
            .select_from(Suppression)
            .where(
                func.lower(Suppression.email) == contact.email.lower(),
                Suppression.expires_at.is_(None),
            )
        )
        if not existing:
            services.session.add(
                Suppression(
                    tenant_id=context.tenant_id,
                    contact_id=contact.id,
                    email=contact.email.lower(),
                    reason=config.reason,
                    source=f"workflow:{context.require_subject().workflow.name}"[:100],
                    created_at=now,
                )
            )
        if config.reason in _DO_NOT_CONTACT_REASONS:
            contact.stage = ContactStage.DO_NOT_CONTACT
            contact.updated_at = now
        return Continue()


class FreezeCompanyConfig(NodeConfig):
    """Why the company is frozen."""

    reason: str = Field(min_length=1, max_length=200)


@register_node
class FreezeCompanyNode(BaseNode[FreezeCompanyConfig]):
    """Freezes the contact's company."""

    node_type = "freeze_company"
    category = NodeCategory.PROTECTION
    display_name = "Freeze company"
    description = "Blocks outreach to every contact and branch of this company, for all offices."
    config_model = FreezeCompanyConfig
    is_terminal = True

    async def execute(self, context: ExecutionContext, config: FreezeCompanyConfig) -> NodeResult:
        """Freeze the contact's company (no-op without a company)."""
        company = context.require_subject().company
        if company is None:
            context.output["note"] = "contact has no company"
            return Continue()
        if company.frozen_at is None:
            now = context.require_services().clock.now()
            company.frozen_at = now
            company.frozen_reason = config.reason
            company.updated_at = now
        return Continue()


class PauseWorkflowForCompanyConfig(NodeConfig):
    """For how long."""

    days: int | None = Field(
        default=None, ge=1, le=365, description="Pause length. Leave empty to pause until resumed."
    )


@register_node
class PauseWorkflowForCompanyNode(BaseNode[PauseWorkflowForCompanyConfig]):
    """Pauses this workflow for everyone at the contact's company."""

    node_type = "pause_workflow_for_company"
    category = NodeCategory.PROTECTION
    display_name = "Pause for company"
    description = "Pauses this workflow for all contacts at the same company."
    config_model = PauseWorkflowForCompanyConfig
    is_terminal = True

    async def execute(
        self, context: ExecutionContext, config: PauseWorkflowForCompanyConfig
    ) -> NodeResult:
        """Pause this workflow for the contact's company (extends an existing pause)."""
        services = context.require_services()
        subject = context.require_subject()
        if subject.company is None:
            context.output["note"] = "contact has no company"
            return Continue()
        now = services.clock.now()
        until = now + timedelta(days=config.days) if config.days else None
        await services.session.execute(
            insert(WorkflowCompanyPause)
            .values(
                id=uuid.uuid4(),
                tenant_id=context.tenant_id,
                workflow_id=subject.workflow.id,
                company_id=subject.company.id,
                until=until,
                created_at=now,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "workflow_id", "company_id"],
                set_={"until": until},
            )
        )
        return Continue()
