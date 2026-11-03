"""FastAPI dependencies for process-wide resources."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from lanereach.core.clock import Clock
from lanereach.core.config import Settings
from lanereach.core.container import ServiceContainer


def get_container(request: Request) -> ServiceContainer:
    """Return the service container of the running application.

    Args:
        request: The current request.

    Returns:
        The container stored on `app.state` by `create_app`.
    """
    container: ServiceContainer = request.app.state.container
    return container


def get_app_settings(container: Annotated[ServiceContainer, Depends(get_container)]) -> Settings:
    """Return the application settings.

    Args:
        container: The service container.

    Returns:
        The settings.
    """
    return container.settings


def get_clock(container: Annotated[ServiceContainer, Depends(get_container)]) -> Clock:
    """Return the application clock.

    Args:
        container: The service container.

    Returns:
        The clock.
    """
    return container.clock


ContainerDependency = Annotated[ServiceContainer, Depends(get_container)]
SettingsDependency = Annotated[Settings, Depends(get_app_settings)]
ClockDependency = Annotated[Clock, Depends(get_clock)]
