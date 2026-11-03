"""Suppression: never email unsubscribed, bounced, blocked or complaining contacts."""

from __future__ import annotations

from sqlalchemy import func, or_, select

from lanereach.automation.guardrails.base import (
    ALL_KINDS,
    GuardrailContext,
    GuardrailDecision,
    GuardrailOutcome,
    OutboundEmail,
    allow,
)
from lanereach.domain.contacts.models import ContactStage
from lanereach.domain.suppressions.models import Suppression


class SuppressionGuardrail:
    """Blocks suppressed contacts; defers while a temporary suppression is active.

    A suppression matches by contact or by email address, so re-importing a contact under a new
    ID cannot bypass it. Temporary suppressions (for example after a soft bounce) defer until
    they expire.
    """

    name = "suppression"
    applies_to = ALL_KINDS

    async def evaluate(self, context: GuardrailContext, email: OutboundEmail) -> GuardrailDecision:
        """Block or defer when the contact is suppressed."""
        contact = email.contact
        if contact.stage is ContactStage.DO_NOT_CONTACT:
            return GuardrailDecision(self.name, GuardrailOutcome.BLOCK, "contact is Do Not Contact")
        rows = (
            await context.session.scalars(
                select(Suppression).where(
                    or_(
                        Suppression.contact_id == contact.id,
                        func.lower(Suppression.email) == contact.email.lower(),
                    ),
                    or_(Suppression.expires_at.is_(None), Suppression.expires_at > context.now),
                )
            )
        ).all()
        permanent = [row for row in rows if row.expires_at is None]
        if permanent:
            return GuardrailDecision(
                self.name,
                GuardrailOutcome.BLOCK,
                f"contact is suppressed ({permanent[0].reason})",
            )
        expiries = [row.expires_at for row in rows if row.expires_at is not None]
        if expiries:
            return GuardrailDecision(
                self.name,
                GuardrailOutcome.DEFER,
                f"temporarily suppressed ({rows[0].reason})",
                max(expiries),
            )
        return allow(self.name)
