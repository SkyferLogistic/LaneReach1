"""Email verification: never send outreach to an address verification found undeliverable."""

from __future__ import annotations

from lanereach.automation.guardrails.base import (
    EmailKind,
    GuardrailContext,
    GuardrailDecision,
    GuardrailOutcome,
    OutboundEmail,
    allow,
)

INVALID = "invalid"


class EmailVerificationGuardrail:
    """Blocks cold and follow-up emails to addresses verified as invalid.

    Bounces hurt the agent's mailbox reputation for every later email. Risky and catch-all
    addresses are still sent to; replies are exempt (the contact wrote to us).
    """

    name = "email_verification"
    applies_to = frozenset({EmailKind.COLD, EmailKind.FOLLOW_UP})

    async def evaluate(self, context: GuardrailContext, email: OutboundEmail) -> GuardrailDecision:
        """Block an invalid address."""
        del context
        if email.contact.email_status == INVALID:
            return GuardrailDecision(
                self.name,
                GuardrailOutcome.BLOCK,
                f"{email.contact.email} failed email verification",
            )
        return allow(self.name)
