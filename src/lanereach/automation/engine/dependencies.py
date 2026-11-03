"""FastAPI dependencies for engine services."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from lanereach.auth.dependencies import TenantSession
from lanereach.automation.engine.runner import EngineRunner
from lanereach.automation.engine.services import EngineServices, build_engine_services
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import NotFoundError


def get_engine_services(session: TenantSession, container: ContainerDependency) -> EngineServices:
    """Engine services bound to the request's tenant session.

    Args:
        session: The request's tenant session.
        container: The service container.

    Returns:
        The services (follow-up work is enqueued after the request commits).
    """
    return build_engine_services(
        session=session,
        clock=container.clock,
        settings=container.settings,
        registry=container.node_registry,
        task_queue=container.task_queue,
        teams_http_client=container.http_client,
        llm=container.llm,
    )


def get_engine_runner(request: Request) -> EngineRunner:
    """The application's engine runner.

    Args:
        request: The current request.

    Returns:
        The runner created by `create_app`.
    """
    runner: EngineRunner = request.app.state.engine_runner
    return runner


def require_non_production(container: ContainerDependency) -> None:
    """Hide dev/staging-only endpoints in production (404, as if they did not exist).

    Args:
        container: The service container.

    Raises:
        NotFoundError: In production.
    """
    if not container.settings.exposes_test_data_endpoints:
        raise NotFoundError("Not found.")


EngineServicesDependency = Annotated[EngineServices, Depends(get_engine_services)]
EngineRunnerDependency = Annotated[EngineRunner, Depends(get_engine_runner)]
NonProduction = Depends(require_non_production)
