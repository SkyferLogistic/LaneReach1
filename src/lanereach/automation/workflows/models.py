"""Workflow and workflow version tables.

A `Workflow` is the named, long-lived object users see; each save creates an immutable
`WorkflowVersion` holding the graph. Publishing points `current_version` at one version; running
enrollments keep the version they started on.

Child rows reference their parent through `(tenant_id, workflow_id)` so a foreign key can never
point at another tenant's row (foreign key checks do not apply RLS).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    StrEnumType,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
)


class WorkflowStatus(StrEnum):
    """Lifecycle state of a workflow."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class Workflow(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A named automation, for example `office-intro-sequence`."""

    __tablename__ = "workflows"
    __table_args__ = (UniqueConstraint("tenant_id", "name"), UniqueConstraint("tenant_id", "id"))

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[WorkflowStatus] = mapped_column(
        StrEnumType(WorkflowStatus, 20), default=WorkflowStatus.DRAFT
    )
    current_version: Mapped[int | None] = mapped_column(Integer)
    latest_version: Mapped[int] = mapped_column(Integer)
    paused_at: Mapped[datetime | None]
    created_by: Mapped[str] = mapped_column(String(200))
    # Campaign schedule (start/end dates, weekdays), applied by the `campaign_schedule`
    # guardrail to the workflow's emails; None sends whenever the other guardrails allow.
    schedule: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime]


class WorkflowVersion(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """An immutable snapshot of a workflow graph."""

    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "version"),
        ForeignKeyConstraint(
            ["tenant_id", "workflow_id"],
            ["workflows.tenant_id", "workflows.id"],
            ondelete="CASCADE",
            name="fk_workflow_versions_workflow",
        ),
    )

    workflow_id: Mapped[uuid.UUID]
    version: Mapped[int] = mapped_column(Integer)
    graph: Mapped[dict[str, Any]]
    created_by: Mapped[str] = mapped_column(String(200))
    published_at: Mapped[datetime | None]
