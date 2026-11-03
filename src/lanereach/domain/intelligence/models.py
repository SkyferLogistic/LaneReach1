"""Weekly digests and follow-up suggestions."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, Index, Integer, String, Text, UniqueConstraint
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


class InsightDigest(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """One week's digest: the numbers and the summary written from them."""

    __tablename__ = "insight_digests"
    __table_args__ = (UniqueConstraint("tenant_id", "week_start"), tenant_unique_id())

    week_start: Mapped[date] = mapped_column(Date)
    week_end: Mapped[date] = mapped_column(Date)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB)
    headline: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text)
    highlights: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    written_by: Mapped[str] = mapped_column(String(10))  # "ai" or "rules"
    ai_run_id: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime]


class FollowUpSuggestion(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A suggested next step for a deal that went quiet (a draft for a person)."""

    __tablename__ = "follow_up_suggestions"
    __table_args__ = (
        tenant_foreign_key("deal_id", "deals"),
        Index("ix_follow_up_suggestions_tenant_id_status", "tenant_id", "status"),
        Index("ix_follow_up_suggestions_tenant_id_deal_id", "tenant_id", "deal_id"),
    )

    deal_id: Mapped[uuid.UUID]
    days_quiet: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(20))  # email, call, wait, close_lost
    reason: Mapped[str] = mapped_column(String(300))
    subject: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(5))
    status: Mapped[str] = mapped_column(String(12))  # open, used, dismissed
    ai_run_id: Mapped[uuid.UUID | None]
    # "ai" or "built_in" (the company's follow-up message, no AI call).
    written_by: Mapped[str] = mapped_column(String(10), default="ai", server_default="ai")
    decided_at: Mapped[datetime | None]
    decided_by: Mapped[str | None] = mapped_column(String(320))
    task_id: Mapped[uuid.UUID | None]
