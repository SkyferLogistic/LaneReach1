"""Country calendar table: per-tenant overrides of weekends and holidays."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import Base, CreatedAtMixin, TenantScopedMixin, UUIDPrimaryKeyMixin


class CountryCalendar(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """Working-week definition for a recipient country.

    `weekend_days` uses ISO weekdays (1 = Monday … 7 = Sunday). Public holidays come from the
    `holidays` library; `extra_holidays` adds dates and `extra_workdays` removes them (for
    example a Saturday declared a working day).
    """

    __tablename__ = "country_calendars"
    __table_args__ = (UniqueConstraint("tenant_id", "country"),)

    country: Mapped[str] = mapped_column(String(2))
    timezone: Mapped[str] = mapped_column(String(64))
    weekend_days: Mapped[list[int]] = mapped_column(ARRAY(Integer))
    extra_holidays: Mapped[list[date]] = mapped_column(ARRAY(Date), default=list)
    extra_workdays: Mapped[list[date]] = mapped_column(ARRAY(Date), default=list)
    updated_at: Mapped[datetime]
