"""Company rotation: only one office may start outreach to a company per ISO week."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from lanereach.automation.guardrails.base import (
    COLD_ONLY,
    GuardrailContext,
    GuardrailDecision,
    GuardrailOutcome,
    OutboundEmail,
    allow,
)
from lanereach.domain.companies.models import CompanyOfficeStart


def iso_week_of(instant: datetime) -> tuple[int, int]:
    """Return the ISO (year, week) of an instant in UTC.

    Args:
        instant: An aware datetime.

    Returns:
        ISO year and week number.
    """
    calendar = instant.astimezone(UTC).isocalendar()
    return calendar.year, calendar.week


def start_of_next_iso_week(instant: datetime) -> datetime:
    """Return Monday 00:00 UTC of the week after `instant`.

    Args:
        instant: An aware datetime.

    Returns:
        The start of the next ISO week.
    """
    utc = instant.astimezone(UTC)
    monday = (utc - timedelta(days=utc.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday + timedelta(days=7)


class CompanyRotationGuardrail:
    """Defers a cold email when another office already started on the company this week.

    Prevents a prospect from hearing from several offices of the same forwarder in one week.
    The office that sends first owns the week (recorded in `company_office_starts`).
    """

    name = "company_rotation"
    applies_to = COLD_ONLY

    async def evaluate(self, context: GuardrailContext, email: OutboundEmail) -> GuardrailDecision:
        """Defer to next week when another office owns this week."""
        if email.company is None:
            return allow(self.name, "contact has no company")
        year, week = iso_week_of(context.now)
        owner = await context.session.scalar(
            select(CompanyOfficeStart.office_id).where(
                CompanyOfficeStart.company_id == email.company.id,
                CompanyOfficeStart.iso_year == year,
                CompanyOfficeStart.iso_week == week,
            )
        )
        if owner is not None and owner != email.office.id:
            return GuardrailDecision(
                self.name,
                GuardrailOutcome.DEFER,
                f"another office already contacted '{email.company.name}' in week "
                f"{year}-W{week:02d}",
                start_of_next_iso_week(context.now),
            )
        return allow(self.name)
