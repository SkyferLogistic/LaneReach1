"""Suppression table: contacts and addresses that must not receive email."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    StrEnumType,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
    tenant_foreign_key,
)


class SuppressionReason(StrEnum):
    """Why a contact must not receive email."""

    DO_NOT_CONTACT = "do_not_contact"
    UNSUBSCRIBED = "unsubscribed"
    BOUNCED_HARD = "bounced_hard"
    BOUNCED_SOFT = "bounced_soft"
    BLOCKED = "blocked"
    COMPLAINT = "complaint"
    MANUAL = "manual"


class Suppression(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A suppression entry. Matched by contact or (lower-cased) email address.

    `expires_at` supports temporary suppressions (for example a soft-bounce retry pause);
    permanent ones leave it empty.
    """

    __tablename__ = "suppressions"
    __table_args__ = (
        Index("ix_suppressions_tenant_id_email", "tenant_id", "email"),
        Index("ix_suppressions_tenant_id_contact_id", "tenant_id", "contact_id"),
        # Deleting a contact keeps its email-address suppression.
        tenant_foreign_key("contact_id", "contacts", nullable_column=True),
    )

    contact_id: Mapped[uuid.UUID | None]
    email: Mapped[str] = mapped_column(String(320))
    reason: Mapped[SuppressionReason] = mapped_column(StrEnumType(SuppressionReason, 30))
    source: Mapped[str] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(String(500))
    expires_at: Mapped[datetime | None]
