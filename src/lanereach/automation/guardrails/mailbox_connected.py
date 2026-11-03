"""Connected mailbox: with real sending, an agent sends only through a verified mailbox."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from lanereach.automation.guardrails.base import (
    ALL_KINDS,
    GuardrailContext,
    GuardrailDecision,
    GuardrailOutcome,
    OutboundEmail,
    allow,
)
from lanereach.integrations.mailboxes.models import MailboxStatus, SenderMailbox

RECHECK = timedelta(hours=1)


class MailboxConnectedGuardrail:
    """Defers email from an agent whose mailbox is not connected and verified (SMTP mode).

    In dry-run mode nothing leaves LaneReach, so no mailbox is needed. Without a sender at all
    the mailbox capacity guardrail decides.
    """

    name = "mailbox_connected"
    applies_to = ALL_KINDS

    async def evaluate(self, context: GuardrailContext, email: OutboundEmail) -> GuardrailDecision:
        """Defer an hour when the agent's mailbox is missing or failing."""
        if not context.requires_connected_mailbox or email.sender is None:
            return allow(self.name)
        status = await context.session.scalar(
            select(SenderMailbox.status).where(SenderMailbox.sender_id == email.sender.id)
        )
        if status is MailboxStatus.VERIFIED:
            return allow(self.name)
        reason = (
            f"{email.sender.email} has no connected mailbox (Agents → Mailbox)"
            if status is None
            else f"{email.sender.email}'s mailbox is {status.value}: test it again (Agents)"
        )
        return GuardrailDecision(self.name, GuardrailOutcome.DEFER, reason, context.now + RECHECK)
