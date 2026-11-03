"""Frequency cap: at most one cold email per contact per 30 days, across all offices."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from lanereach.automation.guardrails.base import (
    COLD_ONLY,
    GuardrailContext,
    GuardrailDecision,
    GuardrailOutcome,
    OutboundEmail,
    allow,
)
from lanereach.domain.messages.models import Message, MessageDirection, MessageKind


class FrequencyCapGuardrail:
    """Defers a cold email until the cap window since the contact's last cold email has passed.

    Counted across every office of the tenant, so two offices cannot both email one person in
    the same month. Replies in an existing thread are exempt.
    """

    name = "frequency_cap"
    applies_to = COLD_ONLY

    async def evaluate(self, context: GuardrailContext, email: OutboundEmail) -> GuardrailDecision:
        """Defer while the contact received a cold email within the cap window."""
        window = timedelta(days=context.frequency_cap_days)
        last_sent = await context.session.scalar(
            select(func.max(Message.occurred_at)).where(
                Message.contact_id == email.contact.id,
                Message.direction == MessageDirection.OUTBOUND,
                Message.kind == MessageKind.COLD,
            )
        )
        if last_sent is not None and last_sent + window > context.now:
            return GuardrailDecision(
                self.name,
                GuardrailOutcome.DEFER,
                f"contact received a cold email on {last_sent.date().isoformat()}; the limit is "
                f"1 per {context.frequency_cap_days} days",
                last_sent + window,
            )
        return allow(self.name)
