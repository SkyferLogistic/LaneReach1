"""Enrollment, step and per-company pause tables."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Index, Integer, String, Text, UniqueConstraint, text
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


class EnrollmentStatus(StrEnum):
    """Lifecycle of an enrollment.

    `active` runs as soon as `next_run_at` is due; `waiting` sleeps until `next_run_at`;
    `paused` is skipped until resumed; the rest are final.
    """

    ACTIVE = "active"
    WAITING = "waiting"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


OPEN_ENROLLMENT_STATUSES = (
    EnrollmentStatus.ACTIVE,
    EnrollmentStatus.WAITING,
    EnrollmentStatus.PAUSED,
)
FINAL_ENROLLMENT_STATUSES = (
    EnrollmentStatus.COMPLETED,
    EnrollmentStatus.STOPPED,
    EnrollmentStatus.FAILED,
)


class Enrollment(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """One contact going through one workflow version.

    `current_node_id` is where the enrollment is. After a `Wait`, `resume_handle` says which
    output of that node to follow when it wakes; when it is null the node runs again (a
    guardrail deferred it). The partial unique index guarantees a contact has at most one open
    enrollment per workflow.
    """

    __tablename__ = "enrollments"
    __table_args__ = (
        tenant_unique_id(),
        tenant_foreign_key("workflow_id", "workflows"),
        tenant_foreign_key("contact_id", "contacts"),
        Index(
            "uq_enrollments_one_open_per_contact",
            "tenant_id",
            "workflow_id",
            "contact_id",
            unique=True,
            postgresql_where=text("status IN ('active', 'waiting', 'paused')"),
        ),
        Index("ix_enrollments_due", "tenant_id", "status", "next_run_at"),
        Index("ix_enrollments_tenant_id_contact_id", "tenant_id", "contact_id"),
    )

    workflow_id: Mapped[uuid.UUID]
    workflow_version: Mapped[int] = mapped_column(Integer)
    contact_id: Mapped[uuid.UUID]
    status: Mapped[EnrollmentStatus] = mapped_column(
        StrEnumType(EnrollmentStatus, 20), default=EnrollmentStatus.ACTIVE
    )
    current_node_id: Mapped[str] = mapped_column(String(64))
    resume_handle: Mapped[str | None] = mapped_column(String(64))
    next_run_at: Mapped[datetime | None]
    variables: Mapped[dict[str, Any]]
    stop_reason: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    trigger_event_id: Mapped[uuid.UUID | None]
    enrolled_by: Mapped[str] = mapped_column(String(200))
    completed_at: Mapped[datetime | None]
    updated_at: Mapped[datetime]


class StepResult(StrEnum):
    """What a step did."""

    CONTINUE = "continue"
    WAIT = "wait"
    DEFER = "defer"
    STOP = "stop"
    FAIL = "fail"


class EnrollmentStep(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """One executed node of an enrollment, with every guardrail decision taken.

    `idempotency_key` (`enrollment_id:step_index`) is also passed to the sending provider, so a
    step re-run after a crash can never send twice.
    """

    __tablename__ = "enrollment_steps"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key"),
        Index("ix_enrollment_steps_enrollment", "tenant_id", "enrollment_id", "step_index"),
        tenant_foreign_key("enrollment_id", "enrollments"),
    )

    enrollment_id: Mapped[uuid.UUID]
    step_index: Mapped[int] = mapped_column(Integer)
    node_id: Mapped[str] = mapped_column(String(64))
    node_type: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime]
    result: Mapped[StepResult] = mapped_column(StrEnumType(StepResult, 20))
    handle_taken: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(Text)
    wait_until: Mapped[datetime | None]
    guardrail_decisions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(100))


class WorkflowCompanyPause(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A workflow paused for every contact of one company (`pause_workflow_for_company`)."""

    __tablename__ = "workflow_company_pauses"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workflow_id", "company_id"),
        tenant_foreign_key("workflow_id", "workflows"),
        tenant_foreign_key("company_id", "companies"),
    )

    workflow_id: Mapped[uuid.UUID]
    company_id: Mapped[uuid.UUID]
    until: Mapped[datetime | None]
