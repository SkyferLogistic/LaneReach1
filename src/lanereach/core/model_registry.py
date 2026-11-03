"""Imports every ORM model so `Base.metadata` is complete (for Alembic and tests)."""

from __future__ import annotations

from sqlalchemy import MetaData

from lanereach.core.database import Base


def load_all_models() -> MetaData:
    """Import all model modules and return the populated metadata.

    Returns:
        `Base.metadata` with every LaneReach table registered.
    """
    import lanereach.ai.policies.models
    import lanereach.ai.review.models
    import lanereach.ai.runs.models
    import lanereach.assistant.models
    import lanereach.auth.models
    import lanereach.automation.enrollments.models
    import lanereach.automation.events.models
    import lanereach.automation.workflows.models
    import lanereach.core.idempotency
    import lanereach.domain.assets.models
    import lanereach.domain.audit.models
    import lanereach.domain.calendars.models
    import lanereach.domain.companies.models
    import lanereach.domain.contacts.models
    import lanereach.domain.countries.models
    import lanereach.domain.crm.models
    import lanereach.domain.forms.models
    import lanereach.domain.inbox.models
    import lanereach.domain.intelligence.models
    import lanereach.domain.leads.models
    import lanereach.domain.messages.models
    import lanereach.domain.offices.models
    import lanereach.domain.pricing.models
    import lanereach.domain.quotes.models
    import lanereach.domain.senders.models
    import lanereach.domain.suppressions.models
    import lanereach.domain.templates.models
    import lanereach.integrations.documents.models
    import lanereach.integrations.mailboxes.models
    import lanereach.integrations.meetings.models
    import lanereach.integrations.notifications.models
    import lanereach.integrations.outbound.models
    import lanereach.integrations.sending.models
    import lanereach.integrations.tracking.models
    import lanereach.tenants.models  # noqa: F401

    return Base.metadata
