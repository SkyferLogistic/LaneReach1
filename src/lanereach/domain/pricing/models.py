"""Rate sheets and their rates, the websites the team checks, and the rates seen there.

A rate sheet is one Excel or CSV file (from the library's `Rates/` folder, or uploaded). Its rows
become `rate_entries`; re-importing a sheet replaces its entries. Rate sources are the websites
pricing checks by hand: LaneReach stores a link template, never a password. A market check is a
rate a person saw on such a site, recorded against a deal.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
    tenant_foreign_key,
    tenant_unique_id,
)


class RateSheet(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """One imported rate file."""

    __tablename__ = "rate_sheets"
    __table_args__ = (UniqueConstraint("tenant_id", "name"), tenant_unique_id())

    name: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(10))  # "library" or "upload"
    library_path: Mapped[str | None] = mapped_column(String(500))
    etag: Mapped[str | None] = mapped_column(String(200))
    file_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10))  # "ok", "partial", "failed"
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    column_mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    tabs: Mapped[list[str]] = mapped_column(JSONB, default=list)
    default_currency: Mapped[str] = mapped_column(String(3), default="USD")
    # The file itself, so a changed column mapping can be re-applied without a new upload.
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    imported_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class RateEntry(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """One rate of a sheet: a charge for a lane, mode and equipment, valid for a period."""

    __tablename__ = "rate_entries"
    __table_args__ = (
        tenant_foreign_key("sheet_id", "rate_sheets"),
        Index("ix_rate_entries_lane", "tenant_id", "origin_key", "destination_key"),
        Index("ix_rate_entries_codes", "tenant_id", "origin_code", "destination_code"),
    )

    sheet_id: Mapped[uuid.UUID]
    tab: Mapped[str] = mapped_column(String(100))
    row_number: Mapped[int] = mapped_column(Integer)
    service_line: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str | None] = mapped_column(String(30))
    origin: Mapped[str] = mapped_column(String(200))
    origin_code: Mapped[str | None] = mapped_column(String(10))
    origin_key: Mapped[str] = mapped_column(String(200))
    destination: Mapped[str] = mapped_column(String(200))
    destination_code: Mapped[str | None] = mapped_column(String(10))
    destination_key: Mapped[str] = mapped_column(String(200))
    equipment: Mapped[str | None] = mapped_column(String(30))
    carrier: Mapped[str | None] = mapped_column(String(120))
    charge_type: Mapped[str] = mapped_column(String(20), default="freight")
    description: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3))
    minimum: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    transit_days: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(String(500))


class RateSource(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A website the team checks for rates (a link, never a login)."""

    __tablename__ = "rate_sources"
    __table_args__ = (UniqueConstraint("tenant_id", "name"), tenant_unique_id())

    name: Mapped[str] = mapped_column(String(120))
    website: Mapped[str] = mapped_column(String(500))
    lane_url_template: Mapped[str | None] = mapped_column(Text)
    service_lines: Mapped[list[str]] = mapped_column(JSONB, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(String(500))
    updated_at: Mapped[datetime]


class MarketCheck(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A rate a person saw on a website for a deal."""

    __tablename__ = "market_checks"
    __table_args__ = (
        tenant_foreign_key("deal_id", "deals"),
        tenant_foreign_key("source_id", "rate_sources", nullable_column=True),
    )

    deal_id: Mapped[uuid.UUID]
    source_id: Mapped[uuid.UUID | None]
    source_name: Mapped[str] = mapped_column(String(120))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3))
    unit: Mapped[str] = mapped_column(String(20))
    equipment: Mapped[str | None] = mapped_column(String(30))
    valid_until: Mapped[date | None] = mapped_column(Date)
    transit_days: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(500))
    checked_by: Mapped[str] = mapped_column(String(320))
