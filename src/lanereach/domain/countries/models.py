"""Destination-country table: language template and `{to_country}` phrase per country."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import Base, CreatedAtMixin, TenantScopedMixin, UUIDPrimaryKeyMixin


class Country(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """How LaneReach writes to agents in one country.

    `language` names the template variant (for example `fr` for France, Belgium and Morocco, or
    `pt-br` for Brazil) and `to_country_phrase` is the destination written the way that language
    needs it (`vers la France`, `Türkiye'ye`, `to the UK`). When `allow_language_fallback` is
    false and no script exists in `language`, emails are held rather than sent in another
    language (the USA and Canada need their own lane wording).
    """

    __tablename__ = "countries"
    __table_args__ = (UniqueConstraint("tenant_id", "country"),)

    country: Mapped[str] = mapped_column(String(2))
    name: Mapped[str] = mapped_column(String(100))
    language: Mapped[str] = mapped_column(String(12))
    to_country_phrase: Mapped[str] = mapped_column(String(200))
    allow_language_fallback: Mapped[bool] = mapped_column(default=True)
    is_enabled: Mapped[bool] = mapped_column(default=True)
    updated_at: Mapped[datetime]
