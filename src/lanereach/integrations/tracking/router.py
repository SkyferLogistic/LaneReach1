"""Public open-pixel and click endpoints (signed tokens, no authentication).

Tracking must never get in the recipient's way: a broken or forged pixel token still gets the
image, and a failure while recording a valid click still redirects.
"""

from __future__ import annotations

import base64

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import NotFoundError
from lanereach.core.tenancy import open_tenant_session
from lanereach.integrations.tracking.service import record_email_engagement
from lanereach.integrations.tracking.tokens import (
    TrackingKind,
    TrackingTarget,
    read_tracking_token,
)

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["Public"])

_PIXEL = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
_NO_STORE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


async def _record(container: ContainerDependency, target: TrackingTarget, request: Request) -> None:
    try:
        async with open_tenant_session(container.session_factory, target.tenant_id) as session:
            await record_email_engagement(
                session,
                container.clock,
                target,
                user_agent=request.headers.get("user-agent"),
                task_queue=container.task_queue,
            )
    except Exception:
        logger.exception("email_tracking_failed", tenant_id=str(target.tenant_id))


@router.get(
    "/t/o/{token}",
    response_class=Response,
    summary="Open pixel (public, signed link)",
    description="A 1x1 transparent GIF in tracked HTML emails. Every request gets the image; "
    "only valid tokens are recorded.",
    responses={200: {"content": {"image/gif": {}}}},
)
async def track_email_open(
    token: str, request: Request, container: ContainerDependency
) -> Response:
    """Record an open and return the pixel."""
    target = read_tracking_token(container.settings, token)
    if target is not None and target.kind is TrackingKind.OPEN:
        await _record(container, target, request)
    return Response(content=_PIXEL, media_type="image/gif", headers=_NO_STORE)


@router.get(
    "/t/c/{token}",
    response_class=RedirectResponse,
    status_code=302,
    summary="Tracked link (public, signed link)",
    description="Records the click and redirects to the link's target. The target is inside "
    "the signed token, so only links LaneReach put into its own emails redirect.",
    responses={302: {"description": "Redirect to the link's target."}, 404: {}},
)
async def track_email_click(
    token: str, request: Request, container: ContainerDependency
) -> RedirectResponse:
    """Record a click and redirect."""
    target = read_tracking_token(container.settings, token)
    if target is None or target.kind is not TrackingKind.CLICK or target.url is None:
        raise NotFoundError("This link is not valid.")
    await _record(container, target, request)
    return RedirectResponse(target.url, status_code=302, headers=_NO_STORE)
