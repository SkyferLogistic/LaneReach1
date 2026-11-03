"""Process-wide service container.

The container owns long-lived resources (database engine, Redis client, HTTP client, JWKS
cache) and the injected `Clock`. `create_app` builds one and stores it on `app.state`; FastAPI
dependencies read from it. Tests build containers with a `ManualClock` and fake HTTP
transports instead of patching globals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from lanereach.ai.llm.client import LLMClient
from lanereach.ai.llm.factory import build_llm_client
from lanereach.auth.oidc import JWKSCache
from lanereach.automation.engine.queue import CeleryTaskQueue, InlineTaskQueue, TaskQueue
from lanereach.automation.nodes.registry import NodeRegistry, load_node_catalog
from lanereach.core.clock import Clock, SystemClock
from lanereach.core.config import Settings
from lanereach.core.database import create_engine, create_session_factory
from lanereach.integrations.mailboxes.transport import MailGateway, StdlibMailGateway


@dataclass
class ServiceContainer:
    """Long-lived dependencies shared by every request.

    Attributes:
        settings: Process configuration.
        clock: Time source.
        engine: Engine connected as the application (non-owner, RLS-bound) role.
        session_factory: Factory for sessions on `engine`.
        redis: Redis client (rate limits, Celery results).
        http_client: Outbound HTTP client (JWKS downloads, notifier webhooks).
        jwks_cache: Cached OIDC signing keys.
        node_registry: Workflow node types available to workflows.
        task_queue: Where background engine work is sent (Celery, or inline for tests).
        llm: LLM client used by AI tasks (mock, Anthropic or Azure OpenAI per settings).
        mail: SMTP/IMAP access to agent mailboxes (a fake in tests: no real email).
    """

    settings: Settings
    clock: Clock
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis: Redis
    http_client: httpx.AsyncClient
    jwks_cache: JWKSCache
    node_registry: NodeRegistry
    task_queue: TaskQueue
    llm: LLMClient
    mail: MailGateway = field(default_factory=StdlibMailGateway)

    async def aclose(self) -> None:
        """Release connections held by the container."""
        await self.http_client.aclose()
        await self.redis.aclose()
        await self.engine.dispose()


def build_service_container(
    settings: Settings,
    *,
    clock: Clock | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
    task_queue: TaskQueue | None = None,
    llm: LLMClient | None = None,
) -> ServiceContainer:
    """Build a container from settings. No network connection is opened yet.

    Args:
        settings: Process configuration.
        clock: Time source; defaults to `SystemClock`.
        http_transport: Optional transport for the outbound HTTP client (tests use
            `httpx.MockTransport` to serve fake JWKS documents).
        task_queue: Override the task queue (defaults from `TASK_QUEUE`).
        llm: Override the LLM client (tests inject a mock with fixtures).

    Returns:
        The container.
    """
    resolved_clock = clock or SystemClock()
    engine = create_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.database_pool_size,
        echo=settings.database_echo,
    )
    http_client = httpx.AsyncClient(
        timeout=settings.oidc_http_timeout_seconds,
        transport=http_transport,
        follow_redirects=False,
    )
    return ServiceContainer(
        settings=settings,
        clock=resolved_clock,
        engine=engine,
        session_factory=create_session_factory(engine),
        redis=Redis.from_url(settings.redis_url.get_secret_value()),
        http_client=http_client,
        jwks_cache=JWKSCache(
            http_client,
            resolved_clock,
            ttl=timedelta(seconds=settings.oidc_jwks_cache_ttl_seconds),
            min_refresh_interval=timedelta(seconds=settings.oidc_jwks_min_refresh_interval_seconds),
        ),
        node_registry=load_node_catalog(),
        task_queue=task_queue or _build_task_queue(settings),
        llm=llm or build_llm_client(settings, resolved_clock),
    )


def _build_task_queue(settings: Settings) -> TaskQueue:
    if settings.task_queue == "inline":
        return InlineTaskQueue()
    from lanereach.workers.celery_app import create_celery_app

    return CeleryTaskQueue(create_celery_app())
