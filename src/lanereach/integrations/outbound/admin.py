"""Managing integrations: connections, webhook subscriptions, test sends and retries."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.core.clock import Clock
from lanereach.core.errors import BusinessRuleViolationError, NotFoundError
from lanereach.core.secrets import SecretBox, secret_hint
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.integrations.outbound.models import (
    DeliveryStatus,
    DeliveryTarget,
    IntegrationConnection,
    IntegrationDelivery,
    IntegrationKind,
    WebhookSubscription,
)
from lanereach.integrations.outbound.service import (
    SLACK_ALERTS,
    WEBHOOK_EVENTS,
    DeliveryRun,
    IntegrationDeliverer,
)
from lanereach.integrations.outbound.url_guard import (
    Resolver,
    UnsafeUrlError,
    check_url_is_public,
)

SLACK_HOST = "https://hooks.slack.com/"
MAX_WEBHOOKS = 25


@dataclass(frozen=True, slots=True)
class ConnectionInput:
    """What an admin sends to connect a service."""

    kind: IntegrationKind
    name: str
    secret: str | None
    config: dict[str, Any]


def _check_config(kind: IntegrationKind, config: dict[str, Any]) -> dict[str, Any]:
    if kind is IntegrationKind.SLACK:
        alerts = [alert for alert in config.get("alerts") or [] if alert in SLACK_ALERTS]
        if not alerts:
            raise BusinessRuleViolationError("Choose at least one alert for the Slack channel.")
        return {
            "alerts": list(dict.fromkeys(alerts)),
            "channel": str(config.get("channel") or "")[:80],
        }
    if kind in (IntegrationKind.CALENDLY, IntegrationKind.CAL_COM):
        office = config.get("office_id")
        return {"office_id": str(uuid.UUID(str(office))) if office else None}
    return {"block_risky": bool(config.get("block_risky", False))}


class IntegrationService:
    """Integrations of the tenant.

    Args:
        session: Tenant session.
        clock: Time source.
        box: Secret box.
        allow_private_urls: Development mode for webhook URLs.
        resolver: DNS resolver (tests pass a fake).
    """

    def __init__(
        self,
        session: AsyncSession,
        clock: Clock,
        box: SecretBox,
        *,
        allow_private_urls: bool = False,
        resolver: Resolver | None = None,
    ) -> None:
        self._session = session
        self._clock = clock
        self._box = box
        self._allow_private = allow_private_urls
        self._resolver = resolver

    async def _check_url(self, url: str) -> None:
        try:
            await check_url_is_public(
                url, allow_private=self._allow_private, resolver=self._resolver
            )
        except UnsafeUrlError as exc:
            raise BusinessRuleViolationError(str(exc)) from exc

    # --- connections -------------------------------------------------------------------

    async def list_connections(self) -> list[IntegrationConnection]:
        """Connections, by kind and name."""
        return list(
            (
                await self._session.scalars(
                    select(IntegrationConnection).order_by(
                        IntegrationConnection.kind, IntegrationConnection.name
                    )
                )
            ).all()
        )

    async def require_connection(self, connection_id: uuid.UUID) -> IntegrationConnection:
        """A connection, or 404."""
        connection = await self._session.get(IntegrationConnection, connection_id)
        if connection is None:
            raise NotFoundError("Integration not found.")
        return connection

    async def create_connection(self, data: ConnectionInput, actor: str) -> IntegrationConnection:
        """Connect a service.

        Raises:
            BusinessRuleViolationError: For a missing or unsafe secret, or a second
                verification service.
        """
        if not data.secret:
            raise BusinessRuleViolationError(
                "The secret (URL, signing key or API key) is required."
            )
        if data.kind is IntegrationKind.SLACK:
            if not data.secret.startswith(SLACK_HOST):
                raise BusinessRuleViolationError(
                    "Use a Slack incoming-webhook URL (https://hooks.slack.com/...)."
                )
            await self._check_url(data.secret)
        if data.kind is IntegrationKind.MILLIONVERIFIER:
            existing = await self._session.scalar(
                select(func.count()).where(
                    IntegrationConnection.kind == IntegrationKind.MILLIONVERIFIER
                )
            )
            if existing:
                raise BusinessRuleViolationError(
                    "An email verification service is already connected."
                )
        now = self._clock.now()
        connection = IntegrationConnection(
            tenant_id=require_session_tenant_id(self._session),
            kind=data.kind,
            name=data.name.strip()[:100],
            public_key=secrets.token_urlsafe(18),
            config=_check_config(data.kind, data.config),
            secret_encrypted=self._box.encrypt(data.secret),
            secret_hint=secret_hint(data.secret),
            is_active=True,
            created_by=actor,
            created_at=now,
            updated_at=now,
        )
        self._session.add(connection)
        await self._session.flush()
        return connection

    async def update_connection(
        self,
        connection_id: uuid.UUID,
        *,
        name: str | None,
        secret: str | None,
        config: dict[str, Any] | None,
        is_active: bool | None,
    ) -> IntegrationConnection:
        """Rename, replace the secret, change settings or switch a connection on/off."""
        connection = await self.require_connection(connection_id)
        if name is not None:
            connection.name = name.strip()[:100]
        if secret:
            if connection.kind is IntegrationKind.SLACK:
                if not secret.startswith(SLACK_HOST):
                    raise BusinessRuleViolationError("Use a Slack incoming-webhook URL.")
                await self._check_url(secret)
            connection.secret_encrypted = self._box.encrypt(secret)
            connection.secret_hint = secret_hint(secret)
        if config is not None:
            connection.config = _check_config(connection.kind, config)
        if is_active is not None:
            connection.is_active = is_active
            connection.last_error = None if is_active else connection.last_error
        connection.updated_at = self._clock.now()
        await self._session.flush()
        return connection

    async def delete_connection(self, connection_id: uuid.UUID) -> None:
        """Remove a connection (pending Slack deliveries die with it)."""
        connection = await self.require_connection(connection_id)
        await self._session.delete(connection)
        await self._session.flush()

    # --- webhooks ------------------------------------------------------------------------

    async def list_webhooks(self) -> list[WebhookSubscription]:
        """Webhook subscriptions, newest first."""
        return list(
            (
                await self._session.scalars(
                    select(WebhookSubscription).order_by(WebhookSubscription.created_at.desc())
                )
            ).all()
        )

    async def require_webhook(self, webhook_id: uuid.UUID) -> WebhookSubscription:
        """A webhook, or 404."""
        webhook = await self._session.get(WebhookSubscription, webhook_id)
        if webhook is None:
            raise NotFoundError("Webhook not found.")
        return webhook

    @staticmethod
    def _check_events(event_types: list[str]) -> list[str]:
        unknown = sorted(set(event_types) - set(WEBHOOK_EVENTS))
        if unknown:
            raise BusinessRuleViolationError(f"Unknown event types: {', '.join(unknown)}")
        if not event_types:
            raise BusinessRuleViolationError("Choose at least one event.")
        return sorted(set(event_types))

    async def create_webhook(
        self, *, name: str, url: str, event_types: list[str], actor: str
    ) -> tuple[WebhookSubscription, str]:
        """Subscribe a URL; returns the signing secret (shown once).

        Raises:
            BusinessRuleViolationError: For an unsafe URL, unknown events or too many webhooks.
        """
        count = await self._session.scalar(select(func.count()).select_from(WebhookSubscription))
        if (count or 0) >= MAX_WEBHOOKS:
            raise BusinessRuleViolationError(f"At most {MAX_WEBHOOKS} webhooks.")
        await self._check_url(url)
        signing_secret = "whsec_" + secrets.token_urlsafe(24)
        now = self._clock.now()
        webhook = WebhookSubscription(
            tenant_id=require_session_tenant_id(self._session),
            name=name.strip()[:100],
            url_encrypted=self._box.encrypt(url),
            url_hint=secret_hint(url),
            signing_secret_encrypted=self._box.encrypt(signing_secret),
            event_types=self._check_events(event_types),
            is_active=True,
            consecutive_failures=0,
            created_by=actor,
            created_at=now,
            updated_at=now,
        )
        self._session.add(webhook)
        await self._session.flush()
        return webhook, signing_secret

    async def update_webhook(
        self,
        webhook_id: uuid.UUID,
        *,
        name: str | None,
        url: str | None,
        event_types: list[str] | None,
        is_active: bool | None,
    ) -> WebhookSubscription:
        """Change a webhook; switching it on again clears the failure count."""
        webhook = await self.require_webhook(webhook_id)
        if name is not None:
            webhook.name = name.strip()[:100]
        if url:
            await self._check_url(url)
            webhook.url_encrypted = self._box.encrypt(url)
            webhook.url_hint = secret_hint(url)
        if event_types is not None:
            webhook.event_types = self._check_events(event_types)
        if is_active is not None:
            webhook.is_active = is_active
            if is_active:
                webhook.consecutive_failures = 0
                webhook.disabled_reason = None
        webhook.updated_at = self._clock.now()
        await self._session.flush()
        return webhook

    async def rotate_webhook_secret(self, webhook_id: uuid.UUID) -> str:
        """A new signing secret (shown once); the old one stops working at once."""
        webhook = await self.require_webhook(webhook_id)
        signing_secret = "whsec_" + secrets.token_urlsafe(24)
        webhook.signing_secret_encrypted = self._box.encrypt(signing_secret)
        webhook.updated_at = self._clock.now()
        await self._session.flush()
        return signing_secret

    async def delete_webhook(self, webhook_id: uuid.UUID) -> None:
        """Remove a webhook."""
        webhook = await self.require_webhook(webhook_id)
        await self._session.delete(webhook)
        await self._session.flush()

    async def list_deliveries(
        self, target_id: uuid.UUID, limit: int = 50
    ) -> list[IntegrationDelivery]:
        """Recent deliveries of a webhook or Slack connection."""
        return list(
            (
                await self._session.scalars(
                    select(IntegrationDelivery)
                    .where(IntegrationDelivery.target_id == target_id)
                    .order_by(IntegrationDelivery.created_at.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def send_test(
        self, deliverer: IntegrationDeliverer, target_type: DeliveryTarget, target_id: uuid.UUID
    ) -> IntegrationDelivery:
        """Send a `ping` now (not retried) and return its outcome."""
        now = self._clock.now()
        delivery_id = uuid.uuid4()
        payload: dict[str, Any] = (
            {
                "id": str(delivery_id),
                "type": "ping",
                "occurred_at": now.isoformat(),
                "event_id": None,
                "data": {"message": "Test from LaneReach: your endpoint is connected."},
            }
            if target_type is DeliveryTarget.WEBHOOK
            else {
                "alert": "positive_reply",
                "data": {
                    "classification": {
                        "summary": "Test alert from LaneReach: the channel is connected."
                    }
                },
                "contact": {"first_name": "Test", "last_name": "Contact", "company": "LaneReach"},
            }
        )
        delivery = IntegrationDelivery(
            id=delivery_id,
            tenant_id=require_session_tenant_id(self._session),
            target_type=target_type,
            target_id=target_id,
            event_id=None,
            event_type="ping" if target_type is DeliveryTarget.WEBHOOK else "positive_reply",
            payload=payload,
            status=DeliveryStatus.PENDING,
            # One attempt only: a failed test is dead at once instead of retrying later.
            attempts=4,
            next_attempt_at=None,
            created_at=now,
        )
        self._session.add(delivery)
        await self._session.flush()
        await deliverer.attempt(delivery, DeliveryRun())
        delivery.attempts = 1
        await self._session.flush()
        return delivery

    async def retry_delivery(self, delivery_id: uuid.UUID) -> IntegrationDelivery:
        """Queue a dead delivery again (sent by the next delivery run)."""
        delivery = await self._session.get(IntegrationDelivery, delivery_id)
        if delivery is None:
            raise NotFoundError("Delivery not found.")
        if delivery.status is not DeliveryStatus.DEAD:
            raise BusinessRuleViolationError("Only failed deliveries can be retried.")
        delivery.status = DeliveryStatus.PENDING
        delivery.attempts = 0
        delivery.next_attempt_at = self._clock.now()
        await self._session.flush()
        return delivery
