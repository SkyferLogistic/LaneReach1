"""Auth tables: identity-provider mappings, API keys and webhook receipts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    StrEnumType,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
)


class TenantIdentityProvider(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """Maps an external IdP identity (issuer + external tenant/org ID) to a LaneReach tenant.

    `jwks_uri` is stored rather than discovered from the token so the API never fetches a URL
    derived from an unverified token claim (SSRF protection).
    """

    __tablename__ = "tenant_identity_providers"
    __table_args__ = (UniqueConstraint("issuer", "external_tenant_id"),)

    issuer: Mapped[str] = mapped_column(String(500))
    external_tenant_id: Mapped[str] = mapped_column(String(200))
    jwks_uri: Mapped[str] = mapped_column(String(500))
    display_name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(default=True)


class ApiKeyEnvironment(StrEnum):
    """Which key family a key belongs to; visible in its prefix (`lr_live_` / `lr_test_`)."""

    LIVE = "live"
    TEST = "test"


class ApiKey(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A tenant API key for machine-to-machine access.

    Only the SHA-256 hash of the key is stored. `key_prefix` (the first characters of the key)
    is indexed so authentication can find the row without scanning hashes.
    """

    __tablename__ = "api_keys"

    name: Mapped[str] = mapped_column(String(200))
    key_prefix: Mapped[str] = mapped_column(String(32), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    environment: Mapped[ApiKeyEnvironment] = mapped_column(StrEnumType(ApiKeyEnvironment, 10))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    created_by: Mapped[str] = mapped_column(String(200))
    last_used_at: Mapped[datetime | None]
    expires_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]


class WebhookReceipt(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """Records a processed webhook event ID so a replayed delivery is rejected."""

    __tablename__ = "webhook_receipts"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", "event_id"),)

    provider: Mapped[str] = mapped_column(String(50))
    event_id: Mapped[str] = mapped_column(String(200))
    received_at: Mapped[datetime]
