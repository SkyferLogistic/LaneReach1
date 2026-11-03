"""Campaign schedules: when a workflow's emails may go out (dates and weekdays).

The schedule narrows the tenant-wide rules, it never widens them: the sending window, business
days and every other guardrail still apply. Days are the recipient's local days, like the
sending window.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

WEEKDAYS = (1, 2, 3, 4, 5, 6, 7)


class CampaignSchedule(BaseModel):
    """Start and end dates and the weekdays a campaign sends on.

    Attributes:
        start_date: First day emails may go out (recipient local); None = right away.
        end_date: Last day; after it the campaign's emails are blocked and enrollments stop.
        weekdays: ISO weekdays (1 = Monday ... 7 = Sunday) the campaign sends on.
    """

    model_config = ConfigDict(extra="forbid")

    start_date: date | None = None
    end_date: date | None = None
    weekdays: list[int] = Field(
        default_factory=lambda: [1, 2, 3, 4, 5],
        min_length=1,
        max_length=7,
        description="ISO weekdays: 1 = Monday ... 7 = Sunday.",
    )

    @model_validator(mode="after")
    def _check(self) -> Self:
        if any(day not in WEEKDAYS for day in self.weekdays):
            raise ValueError("weekdays are 1 (Monday) to 7 (Sunday)")
        if len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError("weekdays must not repeat")
        self.weekdays = sorted(self.weekdays)
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("the end date is before the start date")
        return self


def load_schedule(raw: dict[str, Any] | None) -> CampaignSchedule | None:
    """Read a stored schedule; a damaged one counts as no schedule.

    Args:
        raw: The `workflows.schedule` value.

    Returns:
        The schedule, or None.
    """
    if not raw:
        return None
    try:
        return CampaignSchedule.model_validate(raw)
    except ValidationError:
        return None


def next_campaign_moment(
    schedule: CampaignSchedule, now: datetime, zone: ZoneInfo, window_start: time
) -> datetime | None:
    """The first moment at or after `now` the campaign may send.

    Args:
        schedule: The campaign schedule.
        now: Current time.
        zone: Recipient timezone.
        window_start: Start of the sending window (local); a later day starts there.

    Returns:
        `now` when sending is allowed now, a later moment to defer to, or None when the
        campaign has ended.
    """
    local = now.astimezone(zone)
    day = local.date()
    moved = False
    if schedule.start_date and day < schedule.start_date:
        day, moved = schedule.start_date, True
    for _ in range(7):
        if day.isoweekday() in schedule.weekdays:
            break
        day, moved = day + timedelta(days=1), True
    if schedule.end_date and day > schedule.end_date:
        return None
    return datetime.combine(day, window_start, tzinfo=zone) if moved else now
