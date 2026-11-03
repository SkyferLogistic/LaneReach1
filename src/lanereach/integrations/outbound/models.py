"""Integrations: connections, outgoing webhook subscriptions and the deliveries sent to them.

Connections are Slack, Calendly, Cal.com and email verification; webhooks serve Zapier, Make,
n8n or any system.

Secrets (webhook and Slack URLs, signing keys, API keys) are stored encrypted
(`lanereach.core.secrets`) and never returned by the API; `secret_hint` reminds people which one
is saved.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    StrEnumType,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
    tenant_unique_id,
)


class IntegrationKind(StrEnum):
    """Which service a connection talks to."""

    SLACK = "slack"
    CALENDLY = "calendly"
    CAL_COM = "cal_com"
    MILLIONVERIFIER = "millionverifier"


class IntegrationConnection(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A connected service.

    `public_key` names the connection in the URLs other services call (meeting webhooks); it
    is random and not a secret by itself: requests are verified with the signing secret.
    """

    __tablename__ = "integration_connections"
    __table_args__ = (
        UniqueConstraint("public_key"),
        tenant_unique_id(),
    )

    kind: Mapped[IntegrationKind] = mapped_column(StrEnumType(IntegrationKind, 30))
    name: Mapped[str] = mapped_column(String(100))
    public_key: Mapped[str] = mapped_column(String(40))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    secret_encrypted: Mapped[str | None] = mapped_column(Text)
    secret_hint: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime]


class WebhookSubscription(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """An HTTPS endpoint that receives signed JSON for chosen event types."""

    __tablename__ = "webhook_subscriptions"
    __table_args__ = (tenant_unique_id(),)

    name: Mapped[str] = mapped_column(String(100))
    url_encrypted: Mapped[str] = mapped_column(Text)
    url_hint: Mapped[str] = mapped_column(String(200))
    signing_secret_encrypted: Mapped[str] = mapped_column(Text)
    event_types: Mapped[list[str]] = mapped_column(JSONB, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    disabled_reason: Mapped[str | None] = mapped_column(String(300))
    last_delivery_at: Mapped[datetime | None]
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime]


class DeliveryTarget(StrEnum):
    """Where a delivery goes."""

    WEBHOOK = "webhook"
    SLACK = "slack"


class DeliveryStatus(StrEnum):
    """State of a delivery."""

    PENDING = "pending"
    DELIVERED = "delivered"
    DEAD = "dead"


class IntegrationDelivery(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """One event sent (or to be sent) to one webhook or Slack channel, with its retries."""

    __tablename__ = "integration_deliveries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "target_id", "event_id"),
        Index("ix_integration_deliveries_due", "tenant_id", "status", "next_attempt_at"),
        Index("ix_integration_deliveries_target", "tenant_id", "target_id", "created_at"),
    )

    target_type: Mapped[DeliveryTarget] = mapped_column(StrEnumType(DeliveryTarget, 20))
    target_id: Mapped[uuid.UUID]
    event_id: Mapped[uuid.UUID | None]
    event_type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[DeliveryStatus] = mapped_column(
        StrEnumType(DeliveryStatus, 20), default=DeliveryStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None]
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(500))
    delivered_at: Mapped[datetime | None]
