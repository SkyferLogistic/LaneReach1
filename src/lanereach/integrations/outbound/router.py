"""Integrations endpoints (admins): overview, connections, webhooks, deliveries, test sends."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import Response
from pydantic import Field

from lanereach.auth.dependencies import TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import BusinessRuleViolationError
from lanereach.core.openapi import READ, WRITE
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.core.secrets import build_secret_box
from lanereach.integrations.outbound.admin import ConnectionInput, IntegrationService
from lanereach.integrations.outbound.models import (
    DeliveryStatus,
    DeliveryTarget,
    IntegrationConnection,
    IntegrationKind,
)
from lanereach.integrations.outbound.service import SLACK_ALERTS, WEBHOOK_EVENTS, build_deliverer

router = APIRouter(prefix="/integrations", tags=["Integrations"])

Admin = Annotated[Principal, Depends(require_scopes(Scope.ADMIN_MANAGE))]

_MEETING_PATHS = {IntegrationKind.CALENDLY: "calendly", IntegrationKind.CAL_COM: "cal-com"}


class ConnectionResponse(ResponseModel):
    """A connected service (its secret is never returned)."""

    id: uuid.UUID
    kind: IntegrationKind
    name: str
    config: dict[str, Any]
    secret_hint: str | None
    is_active: bool
    last_used_at: datetime | None
    last_error: str | None
    created_at: datetime
    webhook_url: str | None = Field(description="Where Calendly / Cal.com must send bookings.")


class ConnectionCreateRequest(StrictRequestModel):
    """Connect a service."""

    kind: IntegrationKind
    name: str = Field(min_length=1, max_length=100)
    secret: str = Field(
        min_length=8,
        max_length=2000,
        description="Slack: the incoming-webhook URL. Calendly / Cal.com: the webhook signing "
        "key. MillionVerifier: the API key.",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Slack: `alerts` (" + ", ".join(SLACK_ALERTS) + "). Meetings: `office_id` "
        "for new contacts.",
    )


class ConnectionUpdateRequest(StrictRequestModel):
    """Change a connection; fields left out stay."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    secret: str | None = Field(default=None, min_length=8, max_length=2000)
    config: dict[str, Any] | None = None
    is_active: bool | None = None


class WebhookSubscriptionResponse(ResponseModel):
    """A webhook subscription (URL and signing secret are never returned)."""

    id: uuid.UUID
    name: str
    url_hint: str
    event_types: list[str]
    is_active: bool
    consecutive_failures: int
    disabled_reason: str | None
    last_delivery_at: datetime | None
    last_status_code: int | None
    created_at: datetime


class WebhookSubscriptionCreatedResponse(WebhookSubscriptionResponse):
    """A new webhook with its signing secret (shown only now)."""

    signing_secret: str


class WebhookCreateRequest(StrictRequestModel):
    """Subscribe an HTTPS endpoint (Zapier catch hook, Make, n8n, your own)."""

    name: str = Field(min_length=1, max_length=100, examples=["Zapier: new quote requests"])
    url: str = Field(
        min_length=10, max_length=2000, examples=["https://hooks.zapier.com/hooks/catch/1/abc/"]
    )
    event_types: list[str] = Field(
        min_length=1, max_length=len(WEBHOOK_EVENTS), examples=[["lead.created", "deal.won"]]
    )


class WebhookUpdateRequest(StrictRequestModel):
    """Change a webhook; fields left out stay."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = Field(default=None, min_length=10, max_length=2000)
    event_types: list[str] | None = Field(
        default=None, min_length=1, max_length=len(WEBHOOK_EVENTS)
    )
    is_active: bool | None = None


class SecretResponse(ResponseModel):
    """A new signing secret (shown only now)."""

    signing_secret: str


class DeliveryResponse(ResponseModel):
    """One delivery to a webhook or Slack channel."""

    id: uuid.UUID
    target_type: DeliveryTarget
    event_type: str
    status: DeliveryStatus
    attempts: int
    next_attempt_at: datetime | None
    last_status_code: int | None
    last_error: str | None
    delivered_at: datetime | None
    created_at: datetime


class EnvironmentIntegration(ResponseModel):
    """An integration configured by the operator (environment), shown for completeness."""

    key: str
    name: str
    status: str
    detail: str


class IntegrationsOverviewResponse(ResponseModel):
    """Everything on the Integrations page."""

    connections: list[ConnectionResponse]
    webhooks: list[WebhookSubscriptionResponse]
    webhook_events: list[str]
    slack_alerts: dict[str, str]
    environment: list[EnvironmentIntegration]


def get_integration_service(
    session: TenantSession, container: ContainerDependency
) -> IntegrationService:
    """Integration service for the request."""
    return IntegrationService(
        session,
        container.clock,
        build_secret_box(container.settings),
        allow_private_urls=container.settings.integrations_allow_private_urls,
    )


Integrations = Annotated[IntegrationService, Depends(get_integration_service)]


def _connection(connection: IntegrationConnection, public_base_url: str) -> ConnectionResponse:
    path = _MEETING_PATHS.get(connection.kind)
    webhook_url = (
        f"{public_base_url.rstrip('/')}/api/v1/webhooks/meetings/{path}/{connection.public_key}"
        if path
        else None
    )
    return ConnectionResponse(
        id=connection.id,
        kind=connection.kind,
        name=connection.name,
        config=connection.config,
        secret_hint=connection.secret_hint,
        is_active=connection.is_active,
        last_used_at=connection.last_used_at,
        last_error=connection.last_error,
        created_at=connection.created_at,
        webhook_url=webhook_url,
    )


def _environment(container: ContainerDependency) -> list[EnvironmentIntegration]:
    settings = container.settings
    teams = sorted(settings.teams_webhook_urls)
    return [
        EnvironmentIntegration(
            key="mailbox",
            name="Microsoft 365 mailboxes",
            status="dry_run" if settings.sending_provider == "dry_run" else "connected",
            detail="Emails go to the outbox (dry run) until a mailbox provider is connected."
            if settings.sending_provider == "dry_run"
            else f"Sending through {settings.sending_provider}.",
        ),
        EnvironmentIntegration(
            key="teams",
            name="Microsoft Teams",
            status="connected" if teams else "available",
            detail=f"Channels: {', '.join(teams)}"
            if teams
            else "Set TEAMS_WEBHOOK_URLS to post alerts to Teams.",
        ),
        EnvironmentIntegration(
            key="ai",
            name="AI model",
            status="mock" if settings.llm_provider == "mock" else "connected",
            detail=f"Provider: {settings.llm_provider}",
        ),
    ]


@router.get(
    "", response_model=IntegrationsOverviewResponse, summary="Integrations overview", responses=READ
)
async def read_integrations(
    _principal: Admin, integrations: Integrations, container: ContainerDependency
) -> IntegrationsOverviewResponse:
    """Connections, webhooks, what can be subscribed and operator-level integrations."""
    base = container.settings.public_base_url
    return IntegrationsOverviewResponse(
        connections=[_connection(item, base) for item in await integrations.list_connections()],
        webhooks=[
            WebhookSubscriptionResponse.model_validate(item)
            for item in await integrations.list_webhooks()
        ],
        webhook_events=list(WEBHOOK_EVENTS),
        slack_alerts=SLACK_ALERTS,
        environment=_environment(container),
    )


@router.post(
    "/connections",
    response_model=ConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Connect Slack, Calendly, Cal.com or MillionVerifier",
    responses=WRITE,
)
async def create_connection(
    payload: ConnectionCreateRequest,
    principal: Admin,
    integrations: Integrations,
    container: ContainerDependency,
) -> ConnectionResponse:
    """Connect a service; the secret is stored encrypted."""
    connection = await integrations.create_connection(
        ConnectionInput(payload.kind, payload.name, payload.secret, payload.config),
        principal.subject,
    )
    return _connection(connection, container.settings.public_base_url)


@router.patch(
    "/connections/{connection_id}",
    response_model=ConnectionResponse,
    summary="Change a connection",
    responses=WRITE,
)
async def update_connection(
    connection_id: uuid.UUID,
    payload: ConnectionUpdateRequest,
    _principal: Admin,
    integrations: Integrations,
    container: ContainerDependency,
) -> ConnectionResponse:
    """Rename, replace the secret, change settings, switch on or off."""
    connection = await integrations.update_connection(
        connection_id,
        name=payload.name,
        secret=payload.secret,
        config=payload.config,
        is_active=payload.is_active,
    )
    return _connection(connection, container.settings.public_base_url)


@router.delete(
    "/connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect a service",
    responses=WRITE,
)
async def delete_connection(
    connection_id: uuid.UUID, _principal: Admin, integrations: Integrations
) -> Response:
    """Disconnect."""
    await integrations.delete_connection(connection_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/connections/{connection_id}/test",
    response_model=DeliveryResponse,
    summary="Send a test alert to a Slack channel",
    responses=WRITE,
)
async def send_connection_test(
    connection_id: uuid.UUID,
    _principal: Admin,
    integrations: Integrations,
    session: TenantSession,
    container: ContainerDependency,
) -> DeliveryResponse:
    """Post a test message now."""
    connection = await integrations.require_connection(connection_id)
    if connection.kind is not IntegrationKind.SLACK:
        raise BusinessRuleViolationError("Only Slack connections can be tested this way.")
    delivery = await integrations.send_test(
        build_deliverer(container, session), DeliveryTarget.SLACK, connection.id
    )
    return DeliveryResponse.model_validate(delivery)


@router.post(
    "/webhooks",
    response_model=WebhookSubscriptionCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Subscribe a webhook",
    description="The URL must be https and public (private and internal addresses are refused). "
    "The response carries the signing secret once: every call is signed with "
    '`X-LaneReach-Signature: t=<unix>,v1=<hex HMAC-SHA256 of "<t>.<body>">`.',
    responses=WRITE,
)
async def create_webhook(
    payload: WebhookCreateRequest, principal: Admin, integrations: Integrations
) -> WebhookSubscriptionCreatedResponse:
    """Subscribe an endpoint."""
    webhook, secret = await integrations.create_webhook(
        name=payload.name, url=payload.url, event_types=payload.event_types, actor=principal.subject
    )
    return WebhookSubscriptionCreatedResponse(
        **WebhookSubscriptionResponse.model_validate(webhook).model_dump(), signing_secret=secret
    )


@router.patch(
    "/webhooks/{webhook_id}",
    response_model=WebhookSubscriptionResponse,
    summary="Change a webhook",
    responses=WRITE,
)
async def update_webhook(
    webhook_id: uuid.UUID,
    payload: WebhookUpdateRequest,
    _principal: Admin,
    integrations: Integrations,
) -> WebhookSubscriptionResponse:
    """Change events, URL or name; switch on (clears the failure count) or off."""
    webhook = await integrations.update_webhook(
        webhook_id,
        name=payload.name,
        url=payload.url,
        event_types=payload.event_types,
        is_active=payload.is_active,
    )
    return WebhookSubscriptionResponse.model_validate(webhook)


@router.post(
    "/webhooks/{webhook_id}/rotate-secret",
    response_model=SecretResponse,
    summary="Replace a webhook's signing secret",
    responses=WRITE,
)
async def rotate_webhook_secret(
    webhook_id: uuid.UUID, _principal: Admin, integrations: Integrations
) -> SecretResponse:
    """New secret, shown once."""
    return SecretResponse(signing_secret=await integrations.rotate_webhook_secret(webhook_id))


@router.delete(
    "/webhooks/{webhook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a webhook",
    responses=WRITE,
)
async def delete_webhook(
    webhook_id: uuid.UUID, _principal: Admin, integrations: Integrations
) -> Response:
    """Delete."""
    await integrations.delete_webhook(webhook_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/webhooks/{webhook_id}/test",
    response_model=DeliveryResponse,
    summary="Send a signed ping to a webhook",
    responses=WRITE,
)
async def send_webhook_test(
    webhook_id: uuid.UUID,
    _principal: Admin,
    integrations: Integrations,
    session: TenantSession,
    container: ContainerDependency,
) -> DeliveryResponse:
    """Send `ping` now and show the answer."""
    webhook = await integrations.require_webhook(webhook_id)
    delivery = await integrations.send_test(
        build_deliverer(container, session), DeliveryTarget.WEBHOOK, webhook.id
    )
    return DeliveryResponse.model_validate(delivery)


@router.get(
    "/deliveries",
    response_model=list[DeliveryResponse],
    summary="Recent deliveries of a webhook or Slack connection",
    responses=READ,
)
async def list_deliveries(
    _principal: Admin,
    integrations: Integrations,
    target_id: Annotated[uuid.UUID, Query()],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[DeliveryResponse]:
    """Newest first."""
    return [
        DeliveryResponse.model_validate(item)
        for item in await integrations.list_deliveries(target_id, limit)
    ]


@router.post(
    "/deliveries/{delivery_id}/retry",
    response_model=DeliveryResponse,
    summary="Retry a failed delivery",
    responses=WRITE,
)
async def retry_delivery(
    delivery_id: uuid.UUID, _principal: Admin, integrations: Integrations
) -> DeliveryResponse:
    """Queue it again for the next delivery run."""
    return DeliveryResponse.model_validate(await integrations.retry_delivery(delivery_id))
