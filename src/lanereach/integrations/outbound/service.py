"""Fan-out of events to webhooks and Slack, and their delivery with signatures and retries.

When the worker processes an event (after matching triggers), `fan_out_event` adds one
`integration_deliveries` row per matching webhook subscription and Slack connection. Celery beat
then calls `deliver_all_tenants` every `INTEGRATION_DELIVERY_INTERVAL_SECONDS`: each due delivery
is sent once; failures are retried after 1, 5, 30 and 120 minutes, then the delivery is dead. A
webhook with five dead deliveries in a row is switched off so a broken endpoint stops costing
work, and admins see why.

Webhook requests carry `X-LaneReach-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of
"<t>.<body>" with the subscription's signing secret>` (the same scheme as Stripe and Calendly),
so receivers can check the sender and reject replays older than a few minutes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.events.models import Event, EventType
from lanereach.core.clock import Clock
from lanereach.core.container import ServiceContainer
from lanereach.core.secrets import SecretBox, SecretDecryptionError, build_secret_box
from lanereach.core.tenancy import (
    open_system_session,
    open_tenant_session,
    require_session_tenant_id,
)
from lanereach.domain.companies.models import Company
from lanereach.domain.contacts.models import Contact
from lanereach.integrations.outbound.models import (
    DeliveryStatus,
    DeliveryTarget,
    IntegrationConnection,
    IntegrationDelivery,
    IntegrationKind,
    WebhookSubscription,
)
from lanereach.integrations.outbound.url_guard import (
    Resolver,
    UnsafeUrlError,
    check_url_is_public,
)
from lanereach.reporting.insights import POSITIVE_BUCKETS

logger = structlog.get_logger(__name__)

RETRY_DELAYS = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(minutes=120),
)
DISABLE_AFTER_DEAD = 5
REQUEST_TIMEOUT_SECONDS = 10.0
DELIVERY_BATCH = 50
USER_AGENT = "LaneReach-Webhooks/1.0"

# Event names a webhook can subscribe to: every event type, plus won/lost deals as their own.
DERIVED_EVENTS = ("deal.won", "deal.lost")
WEBHOOK_EVENTS: tuple[str, ...] = tuple(
    sorted(
        {item.value for item in EventType if item not in (EventType.SCHEDULE_TICK,)}
        | set(DERIVED_EVENTS)
    )
)

SLACK_ALERTS: dict[str, str] = {
    "positive_reply": "Positive replies",
    "new_lead": "New quote requests",
    "deal_won": "Deals won",
    "meeting_booked": "Meetings booked",
    "form_submitted": "Web form requests",
    "email_clicked": "Link clicks",
}


def event_names(event: Event) -> list[str]:
    """Names a webhook can match for an event, most specific first.

    Args:
        event: The event.

    Returns:
        For example `["deal.won", "deal.stage_changed"]`.
    """
    names = [event.type.value]
    if event.type is EventType.DEAL_STAGE_CHANGED and event.payload.get("kind") in ("won", "lost"):
        names.insert(0, f"deal.{event.payload['kind']}")
    return names


def slack_alert_for(event: Event) -> str | None:
    """Which Slack alert an event is, if any.

    Args:
        event: The event.

    Returns:
        A key of `SLACK_ALERTS`, or None.
    """
    classification = event.payload.get("classification") or {}
    bucket = classification.get("bucket") if isinstance(classification, dict) else None
    if event.type is EventType.REPLY_CLASSIFIED and bucket in POSITIVE_BUCKETS:
        return "positive_reply"
    if event.type is EventType.LEAD_CREATED:
        return "new_lead"
    if event.type is EventType.DEAL_STAGE_CHANGED and event.payload.get("kind") == "won":
        return "deal_won"
    return {
        EventType.MEETING_BOOKED: "meeting_booked",
        EventType.FORM_SUBMITTED: "form_submitted",
        EventType.EMAIL_CLICKED: "email_clicked",
    }.get(event.type)


async def _contact_summary(
    session: AsyncSession, contact_id: uuid.UUID | None
) -> dict[str, Any] | None:
    if contact_id is None:
        return None
    contact = await session.get(Contact, contact_id)
    if contact is None:
        return None
    company = await session.get(Company, contact.company_id) if contact.company_id else None
    return {
        "id": str(contact.id),
        "email": contact.email,
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "company": company.name if company else None,
        "country": contact.country,
        "office_id": str(contact.office_id) if contact.office_id else None,
    }


def build_webhook_payload(
    delivery_id: uuid.UUID, name: str, event: Event, contact: dict[str, Any] | None
) -> dict[str, Any]:
    """The JSON body of a webhook call.

    Args:
        delivery_id: Id of the delivery (receivers deduplicate on it).
        name: Event name.
        event: The event.
        contact: Contact summary.

    Returns:
        The body.
    """
    return {
        "id": str(delivery_id),
        "type": name,
        "occurred_at": event.occurred_at.isoformat(),
        "event_id": str(event.id),
        "data": {**event.payload, "contact": contact},
    }


async def fan_out_event(session: AsyncSession, clock: Clock, event: Event) -> int:
    """Queue deliveries of an event to matching webhooks and Slack connections.

    Args:
        session: Tenant session (the rows commit with the event's processing).
        clock: Time source.
        event: The processed event.

    Returns:
        Deliveries queued.
    """
    names = event_names(event)
    alert = slack_alert_for(event)
    subscriptions = (
        await session.scalars(
            select(WebhookSubscription).where(WebhookSubscription.is_active.is_(True))
        )
    ).all()
    slack = (
        (
            await session.scalars(
                select(IntegrationConnection).where(
                    IntegrationConnection.kind == IntegrationKind.SLACK,
                    IntegrationConnection.is_active.is_(True),
                )
            )
        ).all()
        if alert
        else []
    )
    matches: list[tuple[DeliveryTarget, uuid.UUID, str]] = []
    for subscription in subscriptions:
        name = next((item for item in names if item in subscription.event_types), None)
        if name:
            matches.append((DeliveryTarget.WEBHOOK, subscription.id, name))
    for connection in slack:
        if alert in (connection.config.get("alerts") or []):
            matches.append((DeliveryTarget.SLACK, connection.id, alert or ""))
    if not matches:
        return 0
    contact = await _contact_summary(session, event.contact_id)
    now = clock.now()
    existing = set(
        (
            await session.scalars(
                select(IntegrationDelivery.target_id).where(
                    IntegrationDelivery.event_id == event.id
                )
            )
        ).all()
    )
    queued = 0
    for target_type, target_id, name in matches:
        if target_id in existing:
            continue
        delivery_id = uuid.uuid4()
        session.add(
            IntegrationDelivery(
                id=delivery_id,
                tenant_id=require_session_tenant_id(session),
                target_type=target_type,
                target_id=target_id,
                event_id=event.id,
                event_type=name,
                payload=build_webhook_payload(delivery_id, name, event, contact)
                if target_type is DeliveryTarget.WEBHOOK
                else {
                    "alert": name,
                    "event_type": event.type.value,
                    "data": event.payload,
                    "contact": contact,
                },
                status=DeliveryStatus.PENDING,
                next_attempt_at=now,
                created_at=now,
            )
        )
        queued += 1
    await session.flush()
    return queued


def sign_webhook_body(secret: str, timestamp: int, body: bytes) -> str:
    """The `X-LaneReach-Signature` header value.

    Args:
        secret: The subscription's signing secret.
        timestamp: Unix seconds.
        body: The exact request body.

    Returns:
        `t=<timestamp>,v1=<hex digest>`.
    """
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def build_slack_message(alert: str, payload: dict[str, Any], console_url: str) -> dict[str, Any]:
    """A Slack Block Kit message for an alert.

    Args:
        alert: Key of `SLACK_ALERTS`.
        payload: The delivery payload (event data and contact).
        console_url: Link back to the console.

    Returns:
        The JSON body for the incoming webhook.
    """
    data = payload.get("data") or {}
    contact = payload.get("contact") or {}
    who = " ".join(part for part in (contact.get("first_name"), contact.get("last_name")) if part)
    who = who or contact.get("email") or "A contact"
    company = f" ({contact['company']})" if contact.get("company") else ""
    titles = {
        "positive_reply": f":speech_balloon: {who}{company} replied positively",
        "new_lead": f":package: New quote request from {who}{company}",
        "deal_won": f":trophy: Deal won: {data.get('stage', 'Won')} · {who}{company}",
        "meeting_booked": f":calendar: {who}{company} booked a meeting",
        "form_submitted": f":memo: Web form request from {who}{company}",
        "email_clicked": f":link: {who}{company} clicked a link",
    }
    if alert == "workflow_message":
        titles[alert] = str(data.get("message") or "LaneReach")[:300]
    title = titles.get(alert, f"LaneReach: {alert}")
    details: list[str] = []
    classification = data.get("classification") or {}
    if isinstance(classification, dict) and classification.get("summary"):
        details.append(str(classification["summary"])[:300])
    shipment = data.get("shipment") or {}
    if isinstance(shipment, dict) and (shipment.get("origin") or shipment.get("destination")):
        details.append(
            f"Lane: {shipment.get('origin') or '?'} → {shipment.get('destination') or '?'}"
        )
    for key in ("title", "starts_at", "form", "url"):
        if data.get(key):
            details.append(f"{key.replace('_', ' ').capitalize()}: {str(data[key])[:200]}")
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*"}}
    ]
    if details:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(details)}})
    link = (
        f"{console_url.rstrip('/')}/contacts/{contact['id']}" if contact.get("id") else console_url
    )
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open in LaneReach"},
                    "url": link,
                }
            ],
        }
    )
    return {"text": title, "blocks": blocks}


@dataclass
class DeliveryRun:
    """What one delivery pass did."""

    delivered: int = 0
    retried: int = 0
    dead: int = 0
    tenants: int = 0
    errors: list[str] = field(default_factory=list)


class IntegrationDeliverer:
    """Sends due deliveries of one tenant.

    Args:
        session: Tenant session.
        clock: Time source.
        http: Outbound HTTP client (no redirects).
        box: Secret box.
        console_url: Public console URL for links in Slack messages.
        allow_private_urls: Development mode (see `url_guard`).
        resolver: DNS resolver (tests pass a fake).
    """

    def __init__(
        self,
        session: AsyncSession,
        clock: Clock,
        http: httpx.AsyncClient,
        box: SecretBox,
        *,
        console_url: str,
        allow_private_urls: bool = False,
        resolver: Resolver | None = None,
    ) -> None:
        self._session = session
        self._clock = clock
        self._http = http
        self._box = box
        self._console_url = console_url
        self._allow_private = allow_private_urls
        self._resolver = resolver

    async def deliver_due(self, run: DeliveryRun, *, limit: int = DELIVERY_BATCH) -> None:
        """Send every due delivery (at most `limit`).

        Args:
            run: Totals to add to.
            limit: Batch size.
        """
        now = self._clock.now()
        due = (
            await self._session.scalars(
                select(IntegrationDelivery)
                .where(
                    IntegrationDelivery.status == DeliveryStatus.PENDING,
                    IntegrationDelivery.next_attempt_at <= now,
                )
                .order_by(IntegrationDelivery.next_attempt_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for delivery in due:
            await self.attempt(delivery, run)

    async def attempt(self, delivery: IntegrationDelivery, run: DeliveryRun) -> None:
        """Send one delivery now and record the outcome.

        Args:
            delivery: The delivery.
            run: Totals to add to.
        """
        now = self._clock.now()
        delivery.attempts += 1
        try:
            status_code = await self._send(delivery)
        except _PermanentDeliveryError as exc:
            await self._fail(delivery, str(exc), None, now, run, permanent=True)
            return
        except (httpx.HTTPError, UnsafeUrlError, SecretDecryptionError) as exc:
            await self._fail(delivery, f"{type(exc).__name__}: {exc}", None, now, run)
            return
        if 200 <= status_code < 300:
            delivery.status = DeliveryStatus.DELIVERED
            delivery.delivered_at = now
            delivery.next_attempt_at = None
            delivery.last_status_code = status_code
            delivery.last_error = None
            await self._mark_target(delivery, now, success=True)
            run.delivered += 1
            return
        # 4xx other than 408/429 will not get better by retrying (wrong URL, rejected payload).
        permanent = 400 <= status_code < 500 and status_code not in (408, 429)
        await self._fail(
            delivery, f"HTTP {status_code}", status_code, now, run, permanent=permanent
        )

    async def _send(self, delivery: IntegrationDelivery) -> int:
        if delivery.target_type is DeliveryTarget.WEBHOOK:
            subscription = await self._session.get(WebhookSubscription, delivery.target_id)
            if subscription is None or not subscription.is_active:
                raise _PermanentDeliveryError("the webhook was removed or switched off")
            url = self._box.decrypt(subscription.url_encrypted)
            await check_url_is_public(
                url, allow_private=self._allow_private, resolver=self._resolver
            )
            body = json.dumps(delivery.payload, separators=(",", ":"), sort_keys=True).encode()
            timestamp = int(self._clock.now().timestamp())
            secret = self._box.decrypt(subscription.signing_secret_encrypted)
            response = await self._http.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT,
                    "X-LaneReach-Event": delivery.event_type,
                    "X-LaneReach-Delivery": str(delivery.id),
                    "X-LaneReach-Signature": sign_webhook_body(secret, timestamp, body),
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
                follow_redirects=False,
            )
            return response.status_code
        connection = await self._session.get(IntegrationConnection, delivery.target_id)
        if connection is None or not connection.is_active or not connection.secret_encrypted:
            raise _PermanentDeliveryError("the Slack connection was removed or switched off")
        url = self._box.decrypt(connection.secret_encrypted)
        await check_url_is_public(url, allow_private=self._allow_private, resolver=self._resolver)
        response = await self._http.post(
            url,
            json=build_slack_message(delivery.event_type, delivery.payload, self._console_url),
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
        return response.status_code

    async def _fail(
        self,
        delivery: IntegrationDelivery,
        error: str,
        status_code: int | None,
        now: datetime,
        run: DeliveryRun,
        *,
        permanent: bool = False,
    ) -> None:
        delivery.last_error = error[:500]
        delivery.last_status_code = status_code
        if permanent or delivery.attempts > len(RETRY_DELAYS):
            delivery.status = DeliveryStatus.DEAD
            delivery.next_attempt_at = None
            run.dead += 1
            await self._mark_target(delivery, now, success=False)
        else:
            delivery.next_attempt_at = now + RETRY_DELAYS[delivery.attempts - 1]
            run.retried += 1
        logger.warning(
            "integration_delivery_failed",
            delivery_id=str(delivery.id),
            target=delivery.target_type.value,
            attempts=delivery.attempts,
            error=error[:120],
        )

    async def _mark_target(
        self, delivery: IntegrationDelivery, now: datetime, *, success: bool
    ) -> None:
        if delivery.target_type is DeliveryTarget.WEBHOOK:
            subscription = await self._session.get(WebhookSubscription, delivery.target_id)
            if subscription is None:
                return
            subscription.last_delivery_at = now
            subscription.last_status_code = delivery.last_status_code
            subscription.consecutive_failures = (
                0 if success else subscription.consecutive_failures + 1
            )
            if subscription.consecutive_failures >= DISABLE_AFTER_DEAD and subscription.is_active:
                subscription.is_active = False
                subscription.disabled_reason = (
                    f"Switched off after {DISABLE_AFTER_DEAD} failed deliveries in a row "
                    f"(last: {delivery.last_error})"
                )[:300]
            subscription.updated_at = now
        else:
            connection = await self._session.get(IntegrationConnection, delivery.target_id)
            if connection is None:
                return
            connection.last_used_at = now
            connection.last_error = None if success else delivery.last_error
            connection.updated_at = now


class _PermanentDeliveryError(Exception):
    """A delivery that can never succeed."""


def build_deliverer(container: ServiceContainer, session: AsyncSession) -> IntegrationDeliverer:
    """A deliverer wired from the container.

    Args:
        container: Application container.
        session: Tenant session.

    Returns:
        The deliverer.
    """
    settings = container.settings
    return IntegrationDeliverer(
        session,
        container.clock,
        container.http_client,
        build_secret_box(settings),
        console_url=settings.console_base_url,
        allow_private_urls=settings.integrations_allow_private_urls,
    )


async def deliver_tenant(
    container: ServiceContainer, tenant_id: uuid.UUID, run: DeliveryRun
) -> None:
    """Send the due deliveries of one tenant.

    Args:
        container: Application container.
        tenant_id: The tenant.
        run: Totals to add to.
    """
    async with open_tenant_session(container.session_factory, tenant_id) as session:
        deliverer = build_deliverer(container, session)
        await deliverer.deliver_due(run)


async def deliver_all_tenants(container: ServiceContainer) -> DeliveryRun:
    """Send due deliveries in every tenant (Celery beat).

    Args:
        container: Application container.

    Returns:
        Totals and per-tenant errors.
    """
    run = DeliveryRun()
    async with open_system_session(container.session_factory) as system:
        tenant_ids: list[uuid.UUID] = list(
            (await system.scalars(text("SELECT tenant_id FROM maintenance_list_tenants()"))).all()
        )
    run.tenants = len(tenant_ids)
    for tenant_id in tenant_ids:
        try:
            await deliver_tenant(container, tenant_id, run)
        except Exception:
            logger.exception("integration_delivery_run_failed", tenant_id=str(tenant_id))
            run.errors.append(str(tenant_id))
    return run
