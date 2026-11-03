"""Recording and querying AI runs."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from typing import Any, Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lanereach.ai.llm.tiers import uses_light_model
from lanereach.ai.runs.models import AIRun, AIRunStatus
from lanereach.core.clock import Clock
from lanereach.core.config import Settings
from lanereach.core.repository import TenantRepository
from lanereach.core.tenancy import open_tenant_session

logger = structlog.get_logger(__name__)


def hash_prompt_input(*parts: str) -> str:
    """Stable hash identifying a prompt input without storing it.

    Args:
        *parts: Rendered prompt parts.

    Returns:
        SHA-256 hex digest.
    """
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def estimate_cost(
    settings: Settings, tokens_in: int, tokens_out: int, task: str | None = None
) -> float:
    """Estimated USD cost from the configured per-million-token prices.

    Args:
        settings: Supplies `LLM_PRICE_*` (and `LLM_LIGHT_PRICE_*` for light-model tasks).
        tokens_in: Input tokens.
        tokens_out: Output tokens.
        task: The task that ran; a task served by the light model is priced at its rates.

    Returns:
        Cost in USD, rounded to 6 decimals.
    """
    if task is not None and uses_light_model(settings, task):
        price_in = settings.llm_light_price_input_per_mtok
        price_out = settings.llm_light_price_output_per_mtok
    else:
        price_in = settings.llm_price_input_per_mtok
        price_out = settings.llm_price_output_per_mtok
    cost = (tokens_in * price_in + tokens_out * price_out) / 1_000_000
    return round(cost, 6)


class RunRecorder(Protocol):
    """Anything that records AI runs."""

    async def record_run(
        self,
        *,
        task: str,
        prompt_version: str,
        provider: str,
        model: str,
        input_ref: str | None,
        input_hash: str,
        status: AIRunStatus,
        output: dict[str, Any] | None = None,
        confidence: float | None = None,
        error: str | None = None,
        latency_ms: int = 0,
        tokens_in: int = 0,
        tokens_out: int = 0,
        attempts: int = 0,
    ) -> uuid.UUID:
        """Store one run and return its ID."""
        ...


def _build_run(tenant_id: uuid.UUID, clock: Clock, settings: Settings, **fields: Any) -> AIRun:
    error = fields.pop("error", None)
    return AIRun(
        tenant_id=tenant_id,
        error=error[:2000] if error else None,
        estimated_cost=estimate_cost(
            settings, fields.get("tokens_in", 0), fields.get("tokens_out", 0), fields.get("task")
        ),
        created_at=clock.now(),
        output_json=fields.pop("output", None),
        **fields,
    )


class SessionAIRunRecorder:
    """Writes AI runs into the caller's session (same transaction).

    Used by the reply pipeline and AI workflow nodes: during a test-run the runs roll back
    with everything else, and in real runs they commit with the step that used them.

    Args:
        session: Tenant-scoped session.
        tenant_id: Tenant of the session.
        clock: Time source.
        settings: Pricing configuration.
    """

    def __init__(
        self, session: AsyncSession, tenant_id: uuid.UUID, clock: Clock, settings: Settings
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._clock = clock
        self._settings = settings

    async def record_run(self, **fields: Any) -> uuid.UUID:
        """Store one run in the current transaction."""
        run = _build_run(self._tenant_id, self._clock, self._settings, **fields)
        self._session.add(run)
        await self._session.flush()
        return run.id


class AIRunRecorder:
    """Writes AI runs in their own transaction.

    Independent transactions mean a failed LLM call is still recorded when the request that
    triggered it rolls back (the failure is exactly what operators need to see).

    Args:
        session_factory: Factory bound to the application role.
        tenant_id: Tenant the runs belong to.
        clock: Time source.
        settings: Pricing configuration.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        tenant_id: uuid.UUID,
        clock: Clock,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._tenant_id = tenant_id
        self._clock = clock
        self._settings = settings

    async def record_run(
        self,
        *,
        task: str,
        prompt_version: str,
        provider: str,
        model: str,
        input_ref: str | None,
        input_hash: str,
        status: AIRunStatus,
        output: dict[str, Any] | None = None,
        confidence: float | None = None,
        error: str | None = None,
        latency_ms: int = 0,
        tokens_in: int = 0,
        tokens_out: int = 0,
        attempts: int = 0,
    ) -> uuid.UUID:
        """Store one run.

        Returns:
            The run ID.
        """
        run = AIRun(
            tenant_id=self._tenant_id,
            task=task,
            prompt_version=prompt_version,
            provider=provider,
            model=model,
            input_ref=input_ref,
            input_hash=input_hash,
            output_json=output,
            confidence=confidence,
            status=status,
            error=error[:2000] if error else None,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            attempts=attempts,
            estimated_cost=estimate_cost(self._settings, tokens_in, tokens_out, task),
            created_at=self._clock.now(),
        )
        async with open_tenant_session(self._session_factory, self._tenant_id) as session:
            session.add(run)
        logger.info(
            "ai_run_recorded",
            ai_run_id=str(run.id),
            task=task,
            status=status.value,
            provider=provider,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )
        return run.id


class AIRunRepository(TenantRepository[AIRun]):
    """Queries over AI runs."""

    model = AIRun

    async def list_runs(
        self,
        *,
        task: str | None,
        status: AIRunStatus | None,
        input_ref: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[Sequence[AIRun], str | None]:
        """Runs newest first with optional filters."""
        statement = select(AIRun)
        if task:
            statement = statement.where(AIRun.task == task)
        if status is not None:
            statement = statement.where(AIRun.status == status)
        if input_ref:
            statement = statement.where(AIRun.input_ref == input_ref)
        return await self.page(statement, limit=limit, cursor=cursor)
