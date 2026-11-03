"""Business-day arithmetic in the recipient's country.

A calendar combines, in order of precedence:

1. the tenant's `country_calendars` row (weekend days, timezone, extra holidays/workdays);
2. built-in defaults for common markets (for example Saudi Arabia and Egypt rest on
   Friday-Saturday, the UAE on Saturday-Sunday since 2022);
3. public holidays from the `holidays` library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import holidays
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.core.clock import Clock
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.calendars.models import CountryCalendar

_SAT_SUN = frozenset({6, 7})
_FRI_SAT = frozenset({5, 6})

# (weekend ISO days, primary timezone) for markets LaneReach serves out of the box.
DEFAULT_CALENDARS: dict[str, tuple[frozenset[int], str]] = {
    "AE": (_SAT_SUN, "Asia/Dubai"),
    "SA": (_FRI_SAT, "Asia/Riyadh"),
    "EG": (_FRI_SAT, "Africa/Cairo"),
    "IN": (frozenset({7}), "Asia/Kolkata"),
    "CN": (_SAT_SUN, "Asia/Shanghai"),
    "FR": (_SAT_SUN, "Europe/Paris"),
    "ES": (_SAT_SUN, "Europe/Madrid"),
    "DE": (_SAT_SUN, "Europe/Berlin"),
    "BR": (_SAT_SUN, "America/Sao_Paulo"),
    "US": (_SAT_SUN, "America/New_York"),
    "CA": (_SAT_SUN, "America/Toronto"),
    "LK": (_SAT_SUN, "Asia/Colombo"),
    "GB": (_SAT_SUN, "Europe/London"),
    "NL": (_SAT_SUN, "Europe/Amsterdam"),
    "PT": (_SAT_SUN, "Europe/Lisbon"),
    "QA": (_FRI_SAT, "Asia/Qatar"),
    "KW": (_FRI_SAT, "Asia/Kuwait"),
    "OM": (_FRI_SAT, "Asia/Muscat"),
    "BH": (_FRI_SAT, "Asia/Bahrain"),
}
_FALLBACK = (_SAT_SUN, "UTC")
_MAX_SEARCH_DAYS = 366


@lru_cache(maxsize=512)
def _public_holidays(country: str, year: int) -> frozenset[date]:
    try:
        return frozenset(holidays.country_holidays(country, years=year).keys())
    except NotImplementedError:
        return frozenset()


@dataclass(frozen=True)
class BusinessCalendar:
    """The working week of one country."""

    country: str
    timezone: ZoneInfo
    weekend_days: frozenset[int]
    extra_holidays: frozenset[date] = field(default_factory=frozenset)
    extra_workdays: frozenset[date] = field(default_factory=frozenset)

    def is_holiday(self, day: date) -> bool:
        """Return whether `day` is a public or tenant-defined holiday.

        Args:
            day: A local date.

        Returns:
            True for holidays.
        """
        return day in self.extra_holidays or day in _public_holidays(self.country, day.year)

    def is_business_day(self, day: date) -> bool:
        """Return whether people work on `day` in this country.

        Args:
            day: A local date.

        Returns:
            True for working days.
        """
        if day in self.extra_workdays:
            return True
        return day.isoweekday() not in self.weekend_days and not self.is_holiday(day)

    def next_business_day(self, day: date, *, include_today: bool = True) -> date:
        """Return the first business day on or after `day`.

        Args:
            day: Starting local date.
            include_today: Whether `day` itself may be returned.

        Returns:
            The business day.

        Raises:
            ValueError: If no business day exists within a year (misconfigured calendar).
        """
        candidate = day if include_today else day + timedelta(days=1)
        for _ in range(_MAX_SEARCH_DAYS):
            if self.is_business_day(candidate):
                return candidate
            candidate += timedelta(days=1)
        raise ValueError(f"no business day within a year for {self.country}")

    def add_business_days(self, day: date, count: int) -> date:
        """Return the date `count` business days after `day`.

        Args:
            day: Starting local date (not counted).
            count: Business days to add (at least 1).

        Returns:
            The resulting local date.
        """
        current = day
        for _ in range(count):
            current = self.next_business_day(current, include_today=False)
        return current

    def local_date(self, instant: datetime) -> date:
        """Return the local date of `instant` in this country.

        Args:
            instant: An aware datetime.

        Returns:
            The local calendar date.
        """
        return instant.astimezone(self.timezone).date()

    def at_local_time(self, day: date, moment: time, zone: ZoneInfo | None = None) -> datetime:
        """Combine a local date and time into an aware datetime.

        Args:
            day: Local date.
            moment: Local wall-clock time.
            zone: Timezone to use instead of the calendar's (for example the contact's).

        Returns:
            The aware datetime.
        """
        return datetime.combine(day, moment, tzinfo=zone or self.timezone)


def default_calendar(country: str | None) -> BusinessCalendar:
    """Return the built-in calendar for a country.

    Args:
        country: ISO 3166-1 alpha-2 code, or None when unknown.

    Returns:
        The calendar (Saturday-Sunday weekend in UTC when the country is unknown).
    """
    code = (country or "").upper()
    weekend, zone = DEFAULT_CALENDARS.get(code, _FALLBACK)
    return BusinessCalendar(country=code or "ZZ", timezone=ZoneInfo(zone), weekend_days=weekend)


class CountryCalendarService:
    """Resolves the calendar for a country, applying tenant overrides.

    Args:
        session: Tenant-scoped session.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._cache: dict[str, BusinessCalendar] = {}

    async def get_calendar(self, country: str | None) -> BusinessCalendar:
        """Return the calendar for a recipient country.

        Args:
            country: ISO 3166-1 alpha-2 code, or None.

        Returns:
            The resolved calendar.
        """
        code = (country or "").upper()
        if code in self._cache:
            return self._cache[code]
        calendar = default_calendar(code)
        if code:
            override = await self._session.scalar(
                select(CountryCalendar).where(CountryCalendar.country == code)
            )
            if override is not None:
                calendar = BusinessCalendar(
                    country=code,
                    timezone=ZoneInfo(override.timezone),
                    weekend_days=frozenset(override.weekend_days),
                    extra_holidays=frozenset(override.extra_holidays or []),
                    extra_workdays=frozenset(override.extra_workdays or []),
                )
        self._cache[code] = calendar
        return calendar


@dataclass(frozen=True)
class CountryCalendarView:
    """A country's working week as the API shows it (default or tenant override)."""

    country: str
    timezone: str
    weekend_days: list[int]
    extra_holidays: list[date]
    extra_workdays: list[date]
    is_default: bool


def _view_of(row: CountryCalendar) -> CountryCalendarView:
    return CountryCalendarView(
        country=row.country,
        timezone=row.timezone,
        weekend_days=sorted(row.weekend_days),
        extra_holidays=list(row.extra_holidays or []),
        extra_workdays=list(row.extra_workdays or []),
        is_default=False,
    )


async def describe_country_calendars(session: AsyncSession) -> list[CountryCalendarView]:
    """Built-in calendars merged with the tenant's overrides, by country.

    Args:
        session: Tenant session.

    Returns:
        One entry per country.
    """
    overrides = {row.country: row for row in (await session.scalars(select(CountryCalendar))).all()}
    views = []
    for country in sorted(set(DEFAULT_CALENDARS) | set(overrides)):
        row = overrides.get(country)
        if row is not None:
            views.append(_view_of(row))
        else:
            weekend, zone = DEFAULT_CALENDARS[country]
            views.append(CountryCalendarView(country, zone, sorted(weekend), [], [], True))
    return views


async def set_country_calendar(
    session: AsyncSession,
    clock: Clock,
    country: str,
    *,
    timezone: str,
    weekend_days: list[int],
    extra_holidays: list[date],
    extra_workdays: list[date],
) -> CountryCalendarView:
    """Create or replace the tenant's override of a country's working week.

    Args:
        session: Tenant session.
        clock: Time source.
        country: ISO 3166-1 alpha-2 code.
        timezone: IANA timezone.
        weekend_days: ISO weekdays that are not worked.
        extra_holidays: Additional non-working dates.
        extra_workdays: Dates worked despite the weekend or a holiday.

    Returns:
        The override.
    """
    row = await session.scalar(select(CountryCalendar).where(CountryCalendar.country == country))
    now = clock.now()
    if row is None:
        row = CountryCalendar(
            tenant_id=require_session_tenant_id(session), country=country, created_at=now
        )
        session.add(row)
    row.timezone = timezone
    row.weekend_days = weekend_days
    row.extra_holidays = extra_holidays
    row.extra_workdays = extra_workdays
    row.updated_at = now
    await session.flush()
    return _view_of(row)
