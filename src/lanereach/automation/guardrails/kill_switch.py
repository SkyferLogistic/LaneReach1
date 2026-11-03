"""Kill switch: a paused tenant or office sends nothing."""

from __future__ import annotations

from datetime import timedelta

from lanereach.automation.guardrails.base import (
    ALL_KINDS,
    GuardrailContext,
    GuardrailDecision,
    GuardrailOutcome,
    OutboundEmail,
    allow,
)


class KillSwitchGuardrail:
    """Defers every email while sending is paused for the tenant or the office.

    The email is re-checked every `KILL_SWITCH_RECHECK_MINUTES`; once sending resumes, the
    window and business-day guardrails place it in the next allowed slot.
    """

    name = "kill_switch"
    applies_to = ALL_KINDS

    async def evaluate(self, context: GuardrailContext, email: OutboundEmail) -> GuardrailDecision:
        """Defer when sending is paused."""
        if email.tenant.sending_paused_at is not None:
            scope = "tenant"
        elif email.office.sending_paused_at is not None:
            scope = f"office '{email.office.name}'"
        else:
            return allow(self.name)
        return GuardrailDecision(
            self.name,
            GuardrailOutcome.DEFER,
            f"sending is paused for the {scope}",
            context.now + timedelta(minutes=context.kill_switch_recheck_minutes),
        )
