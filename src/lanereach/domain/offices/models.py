"""Office table."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
    tenant_foreign_key,
    tenant_unique_id,
)


class Office(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A forwarder office (for example its UAE office) that owns outreach in its region.

    `sending_paused_at` is the office-level kill switch: while set, every email from this
    office is deferred. `signature` is the text signature (also used by AI reply drafts);
    `signature_asset_id` optionally replaces it with a picture in HTML emails.
    """

    __tablename__ = "offices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name"),
        tenant_unique_id(),
        tenant_foreign_key("signature_asset_id", "email_assets", nullable_column=True),
    )

    name: Mapped[str] = mapped_column(String(200))
    country: Mapped[str] = mapped_column(String(2))
    timezone: Mapped[str] = mapped_column(String(64))
    default_language: Mapped[str] = mapped_column(String(2), default="en")
    owner_name: Mapped[str] = mapped_column(String(200))
    owner_email: Mapped[str] = mapped_column(String(320))
    signature: Mapped[str] = mapped_column(Text, default="")
    # A signature picture (PNG/JPEG) used instead of the text signature in HTML emails.
    signature_asset_id: Mapped[uuid.UUID | None]
    sending_paused_at: Mapped[datetime | None]
    updated_at: Mapped[datetime]
