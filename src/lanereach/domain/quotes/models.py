"""Quotes: the priced offer the pricing team sends for a deal, with its charge lines."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Date, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    StrEnumType,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
    tenant_foreign_key,
    tenant_unique_id,
)


class QuoteStatus(StrEnum):
    """Where a quote stands."""

    DRAFT = "draft"
    SENT = "sent"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ChargeType(StrEnum):
    """What a quote line charges for."""

    FREIGHT = "freight"
    ORIGIN = "origin"
    DESTINATION = "destination"
    CUSTOMS = "customs"
    INSURANCE = "insurance"
    STORAGE = "storage"
    SURCHARGE = "surcharge"
    OTHER = "other"


class Quote(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A priced offer for a deal. Totals are kept on the row so lists need no sums."""

    __tablename__ = "quotes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number"),
        tenant_unique_id(),
        Index("ix_quotes_tenant_id_status_valid_until", "tenant_id", "status", "valid_until"),
        tenant_foreign_key("deal_id", "deals"),
        tenant_foreign_key("contact_id", "contacts"),
    )

    deal_id: Mapped[uuid.UUID]
    contact_id: Mapped[uuid.UUID]
    number: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    service_line: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str | None] = mapped_column(String(20))
    incoterm: Mapped[str | None] = mapped_column(String(3))
    origin: Mapped[str | None] = mapped_column(String(120))
    destination: Mapped[str | None] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    valid_until: Mapped[date] = mapped_column(Date)
    transit_days: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[QuoteStatus] = mapped_column(
        StrEnumType(QuoteStatus, 20), default=QuoteStatus.DRAFT
    )
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    sent_at: Mapped[datetime | None]
    sent_message_id: Mapped[str | None] = mapped_column(String(300))
    decided_at: Mapped[datetime | None]
    decision_note: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime]


class QuoteLine(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """One charge of a quote (quantity x unit price, in the quote's currency)."""

    __tablename__ = "quote_lines"
    __table_args__ = (
        Index("ix_quote_lines_tenant_id_quote_id", "tenant_id", "quote_id"),
        tenant_foreign_key("quote_id", "quotes"),
    )

    quote_id: Mapped[uuid.UUID]
    position: Mapped[int] = mapped_column(Integer)
    charge_type: Mapped[ChargeType] = mapped_column(StrEnumType(ChargeType, 20))
    description: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit: Mapped[str] = mapped_column(String(20))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
