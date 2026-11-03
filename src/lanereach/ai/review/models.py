"""Review item table."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    StrEnumType,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
    tenant_foreign_key,
)


class ReviewKind(StrEnum):
    """What needs a human decision."""

    REPLY_DRAFT = "reply_draft"
    CLASSIFICATION = "classification"
    LOCALIZED_TEMPLATE = "localized_template"


class ReviewStatus(StrEnum):
    """Where the item is in review."""

    PENDING = "pending"
    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"


class ReviewItem(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """AI output waiting for a person.

    `payload` holds the draft (subject/body), the classification and context the reviewer
    needs. Approving a reply draft sends it in the original thread through the guardrails.
    """

    __tablename__ = "review_items"
    __table_args__ = (
        Index("ix_review_items_tenant_id_status", "tenant_id", "status"),
        Index("ix_review_items_tenant_id_message_id", "tenant_id", "message_id"),
        tenant_foreign_key("contact_id", "contacts", nullable_column=True),
        tenant_foreign_key("message_id", "messages", nullable_column=True),
        tenant_foreign_key("office_id", "offices", nullable_column=True),
        tenant_foreign_key("enrollment_id", "enrollments", nullable_column=True),
    )

    kind: Mapped[ReviewKind] = mapped_column(StrEnumType(ReviewKind, 30))
    status: Mapped[ReviewStatus] = mapped_column(
        StrEnumType(ReviewStatus, 20), default=ReviewStatus.PENDING
    )
    payload: Mapped[dict[str, Any]]
    reason: Mapped[str | None] = mapped_column(Text)
    contact_id: Mapped[uuid.UUID | None]
    message_id: Mapped[uuid.UUID | None]
    office_id: Mapped[uuid.UUID | None]
    enrollment_id: Mapped[uuid.UUID | None]
    ai_run_id: Mapped[uuid.UUID | None]
    reviewer: Mapped[str | None] = mapped_column(String(200))
    decision_note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None]
    sent_message_id: Mapped[str | None] = mapped_column(String(300))
    updated_at: Mapped[datetime]
