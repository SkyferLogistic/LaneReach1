"""HMAC signatures for inbound webhooks.

Header format (`X-LaneReach-Signature`)::

    t=<unix seconds>,v1=<hex hmac_sha256(secret, t + "." + body)>

Several `v1=` entries may be present so a sender can sign with an old and a new secret while
rotating. Signatures older than the tolerance (default 5 minutes) are rejected; replays inside
the tolerance window are blocked separately by recording event IDs (`WebhookReceipt`).
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime

from lanereach.core.errors import AuthenticationError

SIGNATURE_HEADER = "X-LaneReach-Signature"
SIGNATURE_VERSION = "v1"


def compute_webhook_signature(secret: str, timestamp: int, body: bytes) -> str:
    """Compute the `v1` signature of a webhook payload.

    Args:
        secret: The shared signing secret.
        timestamp: Unix time (seconds) the payload was signed.
        body: Raw request body exactly as sent.

    Returns:
        Lowercase hex HMAC-SHA256 digest.
    """
    signed_payload = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()


def build_webhook_signature_header(secret: str, body: bytes, now: datetime) -> str:
    """Build a signature header value, as a sender would.

    Used by tests, the `.http` files and outbound webhooks.

    Args:
        secret: The shared signing secret.
        body: Raw request body.
        now: Signing time.

    Returns:
        The header value, for example `t=1727000000,v1=5f2c...`.
    """
    timestamp = int(now.timestamp())
    signature = compute_webhook_signature(secret, timestamp, body)
    return f"t={timestamp},{SIGNATURE_VERSION}={signature}"


def verify_webhook_signature(
    *,
    secret: str,
    header_value: str | None,
    body: bytes,
    now: datetime,
    tolerance_seconds: int,
) -> int:
    """Verify a webhook signature header.

    Args:
        secret: The shared signing secret.
        header_value: The raw `X-LaneReach-Signature` header, or None if absent.
        body: Raw request body.
        now: Current time from the `Clock`.
        tolerance_seconds: Maximum accepted age (and future skew) of the timestamp.

    Returns:
        The verified signing timestamp.

    Raises:
        AuthenticationError: If the header is missing or malformed, the timestamp is outside
            the tolerance, or no signature matches.
    """
    if not header_value:
        raise AuthenticationError("Webhook signature is missing.")

    timestamp: int | None = None
    candidates: list[str] = []
    for part in header_value.split(","):
        name, separator, value = part.strip().partition("=")
        if not separator:
            continue
        if name == "t":
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise AuthenticationError("Webhook signature timestamp is invalid.") from exc
        elif name == SIGNATURE_VERSION:
            candidates.append(value)

    if timestamp is None or not candidates:
        raise AuthenticationError("Webhook signature header is malformed.")
    if abs(now.timestamp() - timestamp) > tolerance_seconds:
        raise AuthenticationError("Webhook signature timestamp is outside the tolerance.")

    expected = compute_webhook_signature(secret, timestamp, body)
    # Check every candidate without short-circuiting so timing does not reveal which matched.
    matched = False
    for candidate in candidates:
        matched |= hmac.compare_digest(expected, candidate)
    if not matched:
        raise AuthenticationError("Webhook signature does not match.")
    return timestamp
