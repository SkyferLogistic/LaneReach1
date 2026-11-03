"""Public image endpoint for email clients (signed links, no authentication)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import NotFoundError
from lanereach.core.tenancy import open_tenant_session
from lanereach.domain.assets.models import EmailAsset
from lanereach.domain.assets.service import read_asset_token

router = APIRouter(tags=["Public"])


@router.get(
    "/public/assets/{token}",
    response_class=Response,
    summary="An email image (public, signed link)",
    description="Loaded by recipients' mail clients. The token is an HMAC over tenant and "
    "image IDs, so links cannot be guessed; unknown or forged tokens get 404.",
    responses={
        200: {"content": {"image/png": {}, "image/jpeg": {}, "image/gif": {}, "image/webp": {}}}
    },
)
async def read_public_asset(token: str, container: ContainerDependency) -> Response:
    """Serve an email image."""
    target = read_asset_token(container.settings, token)
    if target is None:
        raise NotFoundError("Image not found.")
    async with open_tenant_session(container.session_factory, target.tenant_id) as session:
        asset = await session.get(EmailAsset, target.asset_id)
        if asset is None:
            raise NotFoundError("Image not found.")
        content, content_type, etag = bytes(asset.data), asset.content_type, asset.sha256[:16]
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=86400",
            "ETag": f'"{etag}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )
