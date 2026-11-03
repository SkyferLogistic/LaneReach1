"""Mailbox capacity: a sender mailbox never exceeds its daily cap."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from lanereach.automation.guardrails.base import (
    ALL_KINDS,
    GuardrailContext,
    GuardrailDecision,
    GuardrailOutcome,
    OutboundEmail,
    allow,
)
from lanereach.domain.senders.models import MailboxDailyUsage


def office_day(now: datetime, office_timezone: str) -> date:
    """Return the office-local date used for daily mailbox counters.

    Args:
        now: Current time.
        office_timezone: IANA timezone of the office.

    Returns:
        The local date.
    """
    return now.astimezone(ZoneInfo(office_timezone)).date()


class MailboxCapacityGuardrail:
    """Defers email when the sender mailbox reached its daily cap (protects deliverability)."""

    name = "mailbox_capacity"
    applies_to = ALL_KINDS

    async def evaluate(self, context: GuardrailContext, email: OutboundEmail) -> GuardrailDecision:
        """Defer to the next office-local day when the sender's cap is reached."""
        zone = ZoneInfo(email.office.timezone)
        today = office_day(context.now, email.office.timezone)
        tomorrow = datetime.combine(today + timedelta(days=1), time(0, 0), tzinfo=zone)
        sender = email.sender
        if sender is None:
            return GuardrailDecision(
                self.name, GuardrailOutcome.DEFER, "no sender mailbox has capacity", tomorrow
            )
        sent = await context.session.scalar(
            select(MailboxDailyUsage.sent_count).where(
                MailboxDailyUsage.sender_id == sender.id, MailboxDailyUsage.day == today
            )
        )
        if (sent or 0) >= sender.daily_cap:
            return GuardrailDecision(
                self.name,
                GuardrailOutcome.DEFER,
                f"{sender.email} reached its daily cap of {sender.daily_cap}",
                tomorrow,
            )
        return allow(self.name)
