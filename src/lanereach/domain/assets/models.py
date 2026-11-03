"""Email assets: images placed in emails (country banners, logos, badges, inline images)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Index, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    StrEnumType,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
    tenant_unique_id,
)
from lanereach.domain.senders.models import ServiceLine


class AssetKind(StrEnum):
    """What an image is used for."""

    BANNER = "banner"
    LOGO = "logo"
    BADGE = "badge"
    SIGNATURE = "signature"
    QR = "qr"
    PROMO = "promo"
    INLINE = "inline"


class EmailAsset(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """One image, stored so recipients' mail clients can load it from a signed public link.

    Banners are per service line and country (`country` None is the default banner). Images
    embedded in a Word script become `inline` assets of that script.
    """

    __tablename__ = "email_assets"
    __table_args__ = (
        tenant_unique_id(),
        Index("ix_email_assets_tenant_id_kind", "tenant_id", "kind"),
        Index("ix_email_assets_tenant_id_source_item_id", "tenant_id", "source_item_id"),
    )

    kind: Mapped[AssetKind] = mapped_column(StrEnumType(AssetKind, 20))
    service_line: Mapped[ServiceLine] = mapped_column(
        StrEnumType(ServiceLine, 10), default=ServiceLine.ANY
    )
    country: Mapped[str | None] = mapped_column(String(2))
    filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    width: Mapped[int | None] = mapped_column(Integer)
    source_item_id: Mapped[str | None] = mapped_column(String(300))
    source_etag: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime]
