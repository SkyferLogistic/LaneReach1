"""Business days: only send on working days of the recipient's country."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from lanereach.automation.guardrails.base import (
    WORKFLOW_KINDS,
    GuardrailContext,
    GuardrailDecision,
    GuardrailOutcome,
    OutboundEmail,
    allow,
    window_bounds,
)
from lanereach.domain.calendars.service import BusinessCalendar


def next_window_start(
    calendar: BusinessCalendar, zone: ZoneInfo, now: datetime, start: time
) -> datetime:
    """Return the next moment the sending window opens on a business day.

    Args:
        calendar: Recipient country calendar.
        zone: Recipient timezone.
        now: Current time.
        start: Window start (local).

    Returns:
        The aware datetime of the next window opening after `now`.
    """
    local = now.astimezone(zone)
    today = local.date()
    if calendar.is_business_day(today) and local.time() < start:
        return datetime.combine(today, start, tzinfo=zone)
    next_day = calendar.next_business_day(today, include_today=False)
    return datetime.combine(next_day, start, tzinfo=zone)


class BusinessDaysGuardrail:
    """Defers email that would arrive on a weekend or public holiday in the recipient's country.

    Weekends differ by country (Friday-Saturday in Saudi Arabia and Egypt). Approved replies
    are exempt: a person chose to answer now.
    """

    name = "business_days"
    applies_to = WORKFLOW_KINDS

    async def evaluate(self, context: GuardrailContext, email: OutboundEmail) -> GuardrailDecision:
        """Defer to the next business day's window when today is not a working day."""
        calendar = await context.recipient_calendar(email)
        zone = await context.recipient_zone(email)
        local_day = context.now.astimezone(zone).date()
        if calendar.is_business_day(local_day):
            return allow(self.name)
        kind = "public holiday" if calendar.is_holiday(local_day) else "weekend"
        start, _ = window_bounds(email.tenant)
        return GuardrailDecision(
            self.name,
            GuardrailOutcome.DEFER,
            f"{local_day.isoformat()} is a {kind} in {calendar.country}",
            next_window_start(calendar, zone, context.now, start),
        )
