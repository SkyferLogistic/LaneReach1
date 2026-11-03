"""Company freeze: a complaint or block freezes outreach to the whole company."""

from __future__ import annotations

from lanereach.automation.guardrails.base import (
    ALL_KINDS,
    GuardrailContext,
    GuardrailDecision,
    GuardrailOutcome,
    OutboundEmail,
    allow,
)


class CompanyFreezeGuardrail:
    """Blocks email to any contact or branch of a frozen company, from every office."""

    name = "company_freeze"
    applies_to = ALL_KINDS

    async def evaluate(self, context: GuardrailContext, email: OutboundEmail) -> GuardrailDecision:
        """Block when the recipient's company is frozen."""
        company = email.company
        if company is not None and company.frozen_at is not None:
            reason = company.frozen_reason or "frozen"
            return GuardrailDecision(
                self.name,
                GuardrailOutcome.BLOCK,
                f"company '{company.name}' is frozen ({reason})",
            )
        return allow(self.name)
