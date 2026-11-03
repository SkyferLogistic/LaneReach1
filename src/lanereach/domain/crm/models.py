"""CRM tables: pipelines and their stages, deals, deal history, tasks and notes.

A deal is the pricing team's view of an opportunity: it usually starts from a lead (a reply
asking for rates, with the shipment the AI extracted) and moves through the stages of a
pipeline until it is won or lost.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Date,
    Float,
    Index,
    Integer,
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
    StrEnumType,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
    tenant_foreign_key,
    tenant_unique_id,
)
from lanereach.domain.senders.models import ServiceLine


class StageKind(StrEnum):
    """What reaching a stage means for the deal."""

    OPEN = "open"
    WON = "won"
    LOST = "lost"


class DealStatus(StrEnum):
    """Outcome of a deal (follows the kind of its stage)."""

    OPEN = "open"
    WON = "won"
    LOST = "lost"


class TaskPriority(StrEnum):
    """How urgent a task is."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class Pipeline(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A sales process, for example "Freight quotes"."""

    __tablename__ = "pipelines"
    __table_args__ = (UniqueConstraint("tenant_id", "name"), tenant_unique_id())

    name: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_default: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime]


class PipelineStage(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """One column of a pipeline board."""

    __tablename__ = "pipeline_stages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "pipeline_id", "name"),
        tenant_unique_id(),
        tenant_foreign_key("pipeline_id", "pipelines"),
    )

    pipeline_id: Mapped[uuid.UUID]
    name: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[StageKind] = mapped_column(StrEnumType(StageKind, 10), default=StageKind.OPEN)
    probability: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime]


class Deal(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """An opportunity with a contact, moving through a pipeline.

    `board_position` orders cards inside a stage (fractional, so a card can be dropped between
    two others without renumbering the column). `stage_entered_at` powers "days in stage".
    """

    __tablename__ = "deals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "lead_id"),
        tenant_unique_id(),
        tenant_foreign_key("pipeline_id", "pipelines"),
        tenant_foreign_key("stage_id", "pipeline_stages"),
        tenant_foreign_key("contact_id", "contacts"),
        tenant_foreign_key("company_id", "companies", nullable_column=True),
        tenant_foreign_key("office_id", "offices", nullable_column=True),
        tenant_foreign_key("lead_id", "leads", nullable_column=True),
        Index("ix_deals_tenant_id_pipeline_id_stage_id", "tenant_id", "pipeline_id", "stage_id"),
    )

    pipeline_id: Mapped[uuid.UUID]
    stage_id: Mapped[uuid.UUID]
    contact_id: Mapped[uuid.UUID]
    company_id: Mapped[uuid.UUID | None]
    office_id: Mapped[uuid.UUID | None]
    lead_id: Mapped[uuid.UUID | None]
    title: Mapped[str] = mapped_column(String(200))
    value_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    service_line: Mapped[ServiceLine] = mapped_column(
        StrEnumType(ServiceLine, 10), default=ServiceLine.ANY
    )
    mode: Mapped[str | None] = mapped_column(String(20))
    origin: Mapped[str | None] = mapped_column(String(120))
    destination: Mapped[str | None] = mapped_column(String(120))
    commodity: Mapped[str | None] = mapped_column(String(200))
    incoterm: Mapped[str | None] = mapped_column(String(3))
    cargo_ready_date: Mapped[date | None] = mapped_column(Date)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    volume_cbm: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    pieces: Mapped[int | None] = mapped_column(Integer)
    # Container or truck type (`40HC`, `20RF`, `FTL`), used to match rate-sheet rates.
    equipment: Mapped[str | None] = mapped_column(String(30))
    # Lead score (0-100) with its reasons. The AI part (intent read from the latest reply) is kept
    # so the nightly rescore can update the other signals without another model call.
    score: Mapped[int | None] = mapped_column(Integer)
    score_reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    scored_at: Mapped[datetime | None]
    ai_intent: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    owner_email: Mapped[str | None] = mapped_column(String(320))
    expected_close_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[DealStatus] = mapped_column(StrEnumType(DealStatus, 10), default=DealStatus.OPEN)
    won_at: Mapped[datetime | None]
    lost_at: Mapped[datetime | None]
    lost_reason: Mapped[str | None] = mapped_column(String(300))
    board_position: Mapped[float] = mapped_column(Float, default=0.0)
    stage_entered_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class DealEvent(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """Something that happened to a deal (for its timeline)."""

    __tablename__ = "deal_events"
    __table_args__ = (
        tenant_foreign_key("deal_id", "deals"),
        Index("ix_deal_events_tenant_id_deal_id", "tenant_id", "deal_id"),
    )

    deal_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(String(30))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    actor: Mapped[str] = mapped_column(String(200))


class Task(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A to-do for a person, optionally about a contact or a deal."""

    __tablename__ = "tasks"
    __table_args__ = (
        tenant_foreign_key("contact_id", "contacts", nullable_column=True),
        tenant_foreign_key("deal_id", "deals", nullable_column=True),
        Index("ix_tasks_tenant_id_done_at_due_at", "tenant_id", "done_at", "due_at"),
    )

    title: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str] = mapped_column(Text, default="")
    due_at: Mapped[datetime | None]
    done_at: Mapped[datetime | None]
    assignee_email: Mapped[str | None] = mapped_column(String(320))
    priority: Mapped[TaskPriority] = mapped_column(
        StrEnumType(TaskPriority, 10), default=TaskPriority.NORMAL
    )
    contact_id: Mapped[uuid.UUID | None]
    deal_id: Mapped[uuid.UUID | None]
    created_by: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime]


class Note(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A free-text note on a contact or a deal."""

    __tablename__ = "notes"
    __table_args__ = (
        tenant_foreign_key("contact_id", "contacts", nullable_column=True),
        tenant_foreign_key("deal_id", "deals", nullable_column=True),
    )

    body: Mapped[str] = mapped_column(Text)
    contact_id: Mapped[uuid.UUID | None]
    deal_id: Mapped[uuid.UUID | None]
    author: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime]
