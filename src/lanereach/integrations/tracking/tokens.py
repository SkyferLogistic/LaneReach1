"""Signed tracking tokens and the HTML rewriting that puts them into an email.

A token carries the tenant, the message's `tracking_id` and, for a click, the target URL, all
under an HMAC. The click endpoint therefore only redirects to links LaneReach itself put into
an email (never an open redirect), and needs no database row per link.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import re
import uuid
from dataclasses import dataclass
from enum import IntEnum

from lanereach.core.config import Settings

_SIGNATURE_BYTES = 16
_IDS = 32
MAX_TRACKED_URL_LENGTH = 1500
_LINK = re.compile(r"(<a\b[^>]*?\bhref\s*=\s*)([\"'])(.*?)\2", re.IGNORECASE | re.DOTALL)
_BODY_END = re.compile(r"</body\s*>", re.IGNORECASE)
# Enrollment variables of `wait_for_engagement` nodes start with this prefix; a new open or
# click wakes such an enrollment so it can take its branch at once.
ENGAGEMENT_WAIT_PREFIX = "engagement:"


class TrackingKind(IntEnum):
    """What a token records."""

    OPEN = 1
    CLICK = 2


@dataclass(frozen=True, slots=True)
class TrackingTarget:
    """What a valid token points at."""

    kind: TrackingKind
    tenant_id: uuid.UUID
    tracking_id: uuid.UUID
    url: str | None = None


def _key(settings: Settings) -> bytes:
    # One secret to manage (the unsubscribe secret), a separate key per purpose.
    secret = settings.unsubscribe_signing_secret.get_secret_value().encode()
    return hmac.new(secret, b"lanereach:email-tracking", hashlib.sha256).digest()


def create_tracking_token(
    settings: Settings, tenant_id: uuid.UUID, tracking_id: uuid.UUID, url: str | None = None
) -> str:
    """A URL-safe signed token for the open pixel (no `url`) or a link.

    Args:
        settings: Signing secret.
        tenant_id: The message's tenant.
        tracking_id: The message's tracking id.
        url: Target of a tracked link.

    Returns:
        The token.
    """
    kind = TrackingKind.CLICK if url is not None else TrackingKind.OPEN
    payload = bytes([kind]) + tenant_id.bytes + tracking_id.bytes + (url or "").encode()
    signature = hmac.new(_key(settings), payload, hashlib.sha256).digest()[:_SIGNATURE_BYTES]
    return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")


def read_tracking_token(settings: Settings, token: str) -> TrackingTarget | None:
    """Verify a token.

    Args:
        settings: Signing secret.
        token: Token from the pixel or link.

    Returns:
        The target, or None when the token is malformed or forged.
    """
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, TypeError):
        return None
    if len(raw) < 1 + _IDS + _SIGNATURE_BYTES:
        return None
    payload, signature = raw[:-_SIGNATURE_BYTES], raw[-_SIGNATURE_BYTES:]
    expected = hmac.new(_key(settings), payload, hashlib.sha256).digest()[:_SIGNATURE_BYTES]
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        kind = TrackingKind(payload[0])
        url = payload[1 + _IDS :].decode() or None
    except (ValueError, UnicodeDecodeError):
        return None
    if (kind is TrackingKind.CLICK) != (url is not None):
        return None
    return TrackingTarget(
        kind, uuid.UUID(bytes=payload[1:17]), uuid.UUID(bytes=payload[17:33]), url
    )


def _public(settings: Settings, path: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/api/v1/{path}"


def _trackable(url: str, settings: Settings) -> bool:
    lowered = url.lower()
    if not lowered.startswith(("http://", "https://")) or len(url) > MAX_TRACKED_URL_LENGTH:
        return False
    # Our own links (unsubscribe, images) keep working without a detour.
    return not lowered.startswith(settings.public_base_url.rstrip("/").lower() + "/")


def add_tracking(
    html_body: str,
    settings: Settings,
    tenant_id: uuid.UUID,
    tracking_id: uuid.UUID,
    *,
    opens: bool,
    clicks: bool,
) -> str:
    """Rewrite links through the click endpoint and add the open pixel.

    Only `http(s)` links to other sites are rewritten: `mailto:`, `tel:`, anchors and
    LaneReach's own links (unsubscribe, images) stay as they are.

    Args:
        html_body: Rendered HTML.
        settings: Public base URL and signing secret.
        tenant_id: The tenant.
        tracking_id: The message's tracking id.
        opens: Add the pixel.
        clicks: Rewrite links.

    Returns:
        The tracked HTML.
    """
    tracked = html_body
    if clicks:

        def rewrite(match: re.Match[str]) -> str:
            target = html.unescape(match.group(3).strip())
            if not _trackable(target, settings):
                return match.group(0)
            token = create_tracking_token(settings, tenant_id, tracking_id, target)
            quote = match.group(2)
            return f"{match.group(1)}{quote}{_public(settings, f't/c/{token}')}{quote}"

        tracked = _LINK.sub(rewrite, tracked)
    if opens:
        token = create_tracking_token(settings, tenant_id, tracking_id)
        pixel = (
            f'<img src="{_public(settings, f"t/o/{token}")}" width="1" height="1" alt="" '
            'style="display:block;width:1px;height:1px;border:0;opacity:0;">'
        )
        ends = list(_BODY_END.finditer(tracked))
        if ends:
            position = ends[-1].start()
            tracked = tracked[:position] + pixel + tracked[position:]
        else:
            tracked += pixel
    return tracked
