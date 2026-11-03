"""Sending window: only send during the recipient's local sending hours."""

from __future__ import annotations

from lanereach.automation.guardrails.base import (
    WORKFLOW_KINDS,
    GuardrailContext,
    GuardrailDecision,
    GuardrailOutcome,
    OutboundEmail,
    allow,
    window_bounds,
)
from lanereach.automation.guardrails.business_days import next_window_start


class SendingWindowGuardrail:
    """Defers email outside the window (default 09:00-11:30 recipient local time).

    Morning delivery lands the email near the top of the recipient's inbox.
    """

    name = "sending_window"
    applies_to = WORKFLOW_KINDS

    async def evaluate(self, context: GuardrailContext, email: OutboundEmail) -> GuardrailDecision:
        """Defer to the next window opening when outside the window."""
        zone = await context.recipient_zone(email)
        start, end = window_bounds(email.tenant)
        local_time = context.now.astimezone(zone).time()
        if start <= local_time < end:
            return allow(self.name)
        calendar = await context.recipient_calendar(email)
        return GuardrailDecision(
            self.name,
            GuardrailOutcome.DEFER,
            f"{local_time:%H:%M} is outside the {start:%H:%M}-{end:%H:%M} window ({zone.key})",
            next_window_start(calendar, zone, context.now, start),
        )
