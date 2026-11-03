"""Email template table.

A template (a "script") is one language version of an email. Scripts written in Word and kept in
SharePoint are imported here (HTML plus a plain-text alternative); SharePoint stays the master
and the sync keeps these rows up to date.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    StrEnumType,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
)
from lanereach.domain.senders.models import ServiceLine

# `en`, `fr`, and regional variants such as `en-na`, `pt-br`, `pt-pt`, `zh-hans`, `zh-hant`.
TEMPLATE_LANGUAGE_PATTERN = r"^[a-z]{2}(-[a-z0-9]{2,4})?$"


class TemplateStatus(StrEnum):
    """Whether a template version may be sent."""

    ACTIVE = "active"
    AI_DRAFT = "ai_draft"
    INACTIVE = "inactive"
    ERROR = "error"


class TemplateSource(StrEnum):
    """Where the template's content comes from."""

    MANUAL = "manual"
    SHAREPOINT = "sharepoint"


class Template(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """One language version of an email template (for example `intro` in French).

    Placeholders use `{{scope.field}}` with an optional fallback: `{{contact.first_name|there}}`.
    Only `active` versions are sent: an AI translation waits as `ai_draft` until approved, and a
    document that could not be imported is kept as `error` with the reason.
    """

    __tablename__ = "templates"
    __table_args__ = (UniqueConstraint("tenant_id", "key", "language"),)

    key: Mapped[str] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(12))
    name: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    html_body: Mapped[str | None] = mapped_column(Text)
    service_line: Mapped[ServiceLine] = mapped_column(
        StrEnumType(ServiceLine, 10), default=ServiceLine.ANY
    )
    status: Mapped[TemplateStatus] = mapped_column(
        StrEnumType(TemplateStatus, 20), default=TemplateStatus.ACTIVE
    )
    source: Mapped[TemplateSource] = mapped_column(
        StrEnumType(TemplateSource, 20), default=TemplateSource.MANUAL
    )
    source_item_id: Mapped[str | None] = mapped_column(String(300))
    source_path: Mapped[str | None] = mapped_column(String(500))
    source_etag: Mapped[str | None] = mapped_column(String(200))
    source_web_url: Mapped[str | None] = mapped_column(String(1000))
    pdf_web_url: Mapped[str | None] = mapped_column(String(1000))
    synced_at: Mapped[datetime | None]
    sync_error: Mapped[str | None] = mapped_column(Text)
    import_warnings: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime]
