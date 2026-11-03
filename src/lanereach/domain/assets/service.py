"""Store email images and build the signed public links recipients' mail clients load.

Images are served without authentication (mail clients cannot log in), so each link carries an
HMAC over the tenant and asset IDs: links cannot be guessed or enumerated. Only raster formats
are accepted and the content type comes from the file's magic bytes, never from the uploader
(SVG could carry scripts).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.core.clock import Clock
from lanereach.core.config import Settings
from lanereach.core.errors import BusinessRuleViolationError
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.assets.models import AssetKind, EmailAsset
from lanereach.domain.senders.models import ServiceLine

MAX_IMAGE_BYTES = 5 * 1024 * 1024
_SIGNATURE_BYTES = 16
_ASSET_REFERENCE = re.compile(r"lr-asset://([0-9a-f-]{36})")
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


@dataclass(frozen=True, slots=True)
class AssetTarget:
    """Which asset a public link points at."""

    tenant_id: uuid.UUID
    asset_id: uuid.UUID


def detect_image_type(content: bytes) -> str:
    """The image's real content type.

    Args:
        content: File bytes.

    Returns:
        `image/png`, `image/jpeg`, `image/gif` or `image/webp`.

    Raises:
        BusinessRuleViolationError: For anything else (including SVG) or files over 5 MB.
    """
    if len(content) > MAX_IMAGE_BYTES:
        raise BusinessRuleViolationError("Images must be 5 MB or smaller.")
    for magic, content_type in _MAGIC:
        if content.startswith(magic):
            return content_type
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    raise BusinessRuleViolationError("Only PNG, JPEG, GIF and WebP images can be used in emails.")


def _key(settings: Settings) -> bytes:
    # Derived from the unsubscribe secret with a different context: one secret to manage, and
    # an unsubscribe token can never be replayed as an asset link (or the other way round).
    secret = settings.unsubscribe_signing_secret.get_secret_value().encode()
    return hmac.new(secret, b"lanereach:email-assets", hashlib.sha256).digest()


def create_asset_token(settings: Settings, tenant_id: uuid.UUID, asset_id: uuid.UUID) -> str:
    """URL-safe signed token for an asset."""
    payload = tenant_id.bytes + asset_id.bytes
    signature = hmac.new(_key(settings), payload, hashlib.sha256).digest()[:_SIGNATURE_BYTES]
    return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")


def read_asset_token(settings: Settings, token: str) -> AssetTarget | None:
    """Verify an asset token; None when malformed or forged."""
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, TypeError):
        return None
    if len(raw) != 32 + _SIGNATURE_BYTES:
        return None
    payload, signature = raw[:32], raw[32:]
    expected = hmac.new(_key(settings), payload, hashlib.sha256).digest()[:_SIGNATURE_BYTES]
    if not hmac.compare_digest(signature, expected):
        return None
    return AssetTarget(uuid.UUID(bytes=payload[:16]), uuid.UUID(bytes=payload[16:]))


def asset_url(settings: Settings, tenant_id: uuid.UUID, asset_id: uuid.UUID) -> str:
    """Public link to an asset."""
    token = create_asset_token(settings, tenant_id, asset_id)
    return f"{settings.public_base_url.rstrip('/')}/api/v1/public/assets/{token}"


def resolve_asset_references(html: str, settings: Settings, tenant_id: uuid.UUID) -> str:
    """Replace `lr-asset://<id>` references in stored HTML with public links (at send time).

    Args:
        html: Template HTML.
        settings: Public base URL and signing secret.
        tenant_id: The template's tenant.

    Returns:
        HTML ready for mail clients.
    """
    return _ASSET_REFERENCE.sub(
        lambda match: asset_url(settings, tenant_id, uuid.UUID(match.group(1))), html
    )


async def find_banner(
    session: AsyncSession, service_line: ServiceLine, country: str | None
) -> EmailAsset | None:
    """Banner for a country: the country's own banner, else the default of the service line.

    Args:
        session: Tenant session.
        service_line: The script's service line.
        country: The contact's country.

    Returns:
        The banner asset, or None when the tenant has none.
    """
    rows = (
        await session.scalars(
            select(EmailAsset).where(
                EmailAsset.kind == AssetKind.BANNER,
                EmailAsset.service_line.in_([service_line, ServiceLine.ANY]),
            )
        )
    ).all()

    def rank(asset: EmailAsset) -> tuple[int, int]:
        country_rank = 0 if country and asset.country == country else 1
        line_rank = 0 if asset.service_line is service_line else 1
        return country_rank, line_rank

    candidates = [asset for asset in rows if asset.country in (None, country)]
    return min(candidates, key=rank) if candidates else None


class AssetService:
    """Store and find email images.

    Args:
        session: Tenant session.
        clock: Time source.
    """

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def store_image(
        self,
        *,
        kind: AssetKind,
        filename: str,
        content: bytes,
        service_line: ServiceLine = ServiceLine.ANY,
        country: str | None = None,
        source_item_id: str | None = None,
        source_etag: str | None = None,
    ) -> EmailAsset:
        """Store an image (replacing the asset with the same library item, if any).

        Raises:
            BusinessRuleViolationError: For non-image content or oversized files.
        """
        content_type = detect_image_type(content)
        now = self._clock.now()
        asset = None
        if source_item_id is not None:
            asset = await self._session.scalar(
                select(EmailAsset).where(EmailAsset.source_item_id == source_item_id)
            )
        if asset is None:
            asset = EmailAsset(tenant_id=require_session_tenant_id(self._session), created_at=now)
            self._session.add(asset)
        asset.kind = kind
        asset.filename = filename[:300]
        asset.content_type = content_type
        asset.data = content
        asset.size_bytes = len(content)
        asset.sha256 = hashlib.sha256(content).hexdigest()
        asset.service_line = service_line
        asset.country = country
        asset.source_item_id = source_item_id
        asset.source_etag = source_etag
        asset.updated_at = now
        await self._session.flush()
        return asset

    async def find_banner(
        self, service_line: ServiceLine, country: str | None
    ) -> EmailAsset | None:
        """Banner for a country: the country's own, else the service line's default."""
        return await find_banner(self._session, service_line, country)

    async def list_assets(self, kind: AssetKind | None = None) -> Sequence[EmailAsset]:
        """Assets, banners first by country."""
        statement = select(EmailAsset).order_by(
            EmailAsset.kind, EmailAsset.country, EmailAsset.filename
        )
        if kind is not None:
            statement = statement.where(EmailAsset.kind == kind)
        return (await self._session.scalars(statement)).all()

    async def delete_by_source_prefix(self, prefix: str) -> None:
        """Delete the inline images of a re-imported script (they are re-extracted)."""
        await self._session.execute(
            delete(EmailAsset)
            .where(EmailAsset.source_item_id.startswith(prefix, autoescape=True))
            .execution_options(synchronize_session=False)
        )
