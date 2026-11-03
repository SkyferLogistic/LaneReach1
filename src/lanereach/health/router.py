"""Liveness and readiness endpoints (unauthenticated, no tenant data)."""

from __future__ import annotations

import asyncio
from typing import Literal

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from lanereach.core.config import Settings
from lanereach.core.container import ServiceContainer
from lanereach.core.dependencies import ContainerDependency

KNOWN_LLM_PROVIDERS = frozenset({"anthropic", "azure_openai", "mock"})
_CHECK_TIMEOUT_SECONDS = 2.0

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/health", tags=["Health"])


class LivenessResponse(BaseModel):
    """The process is up."""

    status: Literal["ok"] = "ok"


class DependencyCheck(BaseModel):
    """Result of checking one dependency."""

    ok: bool
    detail: str | None = None


class ReadinessResponse(BaseModel):
    """Whether the process can serve traffic, per dependency."""

    status: Literal["ready", "not_ready"]
    checks: dict[str, DependencyCheck]


@router.get("/live", response_model=LivenessResponse, summary="Liveness probe")
async def read_liveness() -> LivenessResponse:
    """Return 200 while the process is running."""
    return LivenessResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description="Checks the database, Redis and LLM configuration. Returns 503 when any fails.",
    responses={503: {"model": ReadinessResponse}},
)
async def read_readiness(container: ContainerDependency) -> JSONResponse:
    """Check dependencies and report readiness."""
    database, redis = await asyncio.gather(_check_database(container), _check_redis(container))
    checks = {
        "database": database,
        "redis": redis,
        "llm": check_llm_configuration(container.settings),
    }
    ready = all(check.ok for check in checks.values())
    body = ReadinessResponse(status="ready" if ready else "not_ready", checks=checks)
    return JSONResponse(status_code=200 if ready else 503, content=body.model_dump())


def check_llm_configuration(settings: Settings) -> DependencyCheck:
    """Check that an LLM provider is configured (no network call).

    Args:
        settings: Process configuration.

    Returns:
        The check result.
    """
    if settings.llm_provider not in KNOWN_LLM_PROVIDERS:
        return DependencyCheck(ok=False, detail=f"unknown provider '{settings.llm_provider}'")
    if settings.llm_provider != "mock" and not settings.llm_model:
        return DependencyCheck(ok=False, detail="LLM_MODEL is not set")
    return DependencyCheck(ok=True, detail=settings.llm_provider)


async def _check_database(container: ServiceContainer) -> DependencyCheck:
    try:
        async with (
            asyncio.timeout(_CHECK_TIMEOUT_SECONDS),
            container.engine.connect() as connection,
        ):
            await connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
        logger.warning("readiness_database_failed", error=type(exc).__name__)
        return DependencyCheck(ok=False, detail="unreachable")
    return DependencyCheck(ok=True)


async def _check_redis(container: ServiceContainer) -> DependencyCheck:
    try:
        async with asyncio.timeout(_CHECK_TIMEOUT_SECONDS):
            await container.redis.ping()
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
        logger.warning("readiness_redis_failed", error=type(exc).__name__)
        return DependencyCheck(ok=False, detail="unreachable")
    return DependencyCheck(ok=True)
