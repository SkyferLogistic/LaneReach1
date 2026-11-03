"""AI run table: one row per AI task execution (LLM call or rule match)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import Float, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    StrEnumType,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
)


class AIRunStatus(StrEnum):
    """How an AI run ended."""

    SUCCEEDED = "succeeded"
    RULE_MATCHED = "rule_matched"
    FAILED = "failed"


class AIRun(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """An AI task execution with usage and cost.

    The raw email is not duplicated here: `input_ref` points at the message and `input_hash`
    identifies the exact prompt input.
    """

    __tablename__ = "ai_runs"
    __table_args__ = (
        Index("ix_ai_runs_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_ai_runs_tenant_id_task", "tenant_id", "task"),
    )

    task: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(100))
    input_ref: Mapped[str | None] = mapped_column(String(100))
    input_hash: Mapped[str] = mapped_column(String(64))
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[AIRunStatus] = mapped_column(StrEnumType(AIRunStatus, 20))
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
