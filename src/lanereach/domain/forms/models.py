"""Web forms ("Request a quote") on the forwarder's website, and what visitors sent."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Index, Integer, String, Text, UniqueConstraint
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


class WebForm(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A form the website posts to `POST /api/v1/public/forms/{key}`.

    `public_key` is in the page's HTML, so it is not a secret: the endpoint only ever creates
    or updates a contact and records a submission, with a honeypot field and rate limits.
    """

    __tablename__ = "web_forms"
    __table_args__ = (
        UniqueConstraint("public_key"),
        tenant_unique_id(),
        tenant_foreign_key("office_id", "offices", nullable_column=True),
    )

    name: Mapped[str] = mapped_column(String(100))
    public_key: Mapped[str] = mapped_column(String(40))
    office_id: Mapped[uuid.UUID | None]
    tag: Mapped[str | None] = mapped_column(String(64))
    fields: Mapped[list[str]] = mapped_column(JSONB, default=list)
    redirect_url: Mapped[str | None] = mapped_column(String(1000))
    thank_you_text: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    submission_count: Mapped[int] = mapped_column(Integer, default=0)
    last_submission_at: Mapped[datetime | None]
    created_by: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime]


class FormSubmission(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """One submission (the answers, without the honeypot)."""

    __tablename__ = "form_submissions"
    __table_args__ = (
        Index(
            "ix_form_submissions_tenant_id_form_id_created_at", "tenant_id", "form_id", "created_at"
        ),
        tenant_foreign_key("form_id", "web_forms"),
        tenant_foreign_key("contact_id", "contacts", nullable_column=True),
    )

    form_id: Mapped[uuid.UUID]
    contact_id: Mapped[uuid.UUID | None]
    email: Mapped[str] = mapped_column(String(320))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
