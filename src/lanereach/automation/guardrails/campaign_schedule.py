"""Campaign schedule: a workflow's emails go out only between its dates, on its weekdays."""

from __future__ import annotations

from lanereach.automation.guardrails.base import (
    EmailKind,
    GuardrailContext,
    GuardrailDecision,
    GuardrailOutcome,
    OutboundEmail,
    allow,
    window_bounds,
)
from lanereach.automation.workflows.schedule import next_campaign_moment

_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class CampaignScheduleGuardrail:
    """Defers a campaign email to its next scheduled day; blocks it after the end date.

    Replies to a contact who wrote back are not held by the campaign's calendar.
    """

    name = "campaign_schedule"
    applies_to = frozenset({EmailKind.COLD, EmailKind.FOLLOW_UP})

    async def evaluate(self, context: GuardrailContext, email: OutboundEmail) -> GuardrailDecision:
        """Allow inside the schedule, defer before it, block after it."""
        schedule = email.schedule
        if schedule is None:
            return allow(self.name, "no campaign schedule")
        zone = await context.recipient_zone(email)
        start, _ = window_bounds(email.tenant)
        moment = next_campaign_moment(schedule, context.now, zone, start)
        if moment is None:
            return GuardrailDecision(
                self.name,
                GuardrailOutcome.BLOCK,
                f"the campaign ended on {schedule.end_date}",
            )
        if moment == context.now:
            return allow(self.name)
        days = ", ".join(_DAY_NAMES[day - 1] for day in schedule.weekdays)
        return GuardrailDecision(
            self.name,
            GuardrailOutcome.DEFER,
            f"the campaign sends {days}"
            + (f" from {schedule.start_date}" if schedule.start_date else "")
            + f" ({zone.key})",
            moment,
        )
