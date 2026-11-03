"""Signed unsubscribe tokens for `List-Unsubscribe` links.

The token encodes tenant and contact IDs and an HMAC, so the public unsubscribe endpoint can
suppress the right contact without authentication and without letting anyone unsubscribe
arbitrary contacts.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from dataclasses import dataclass

_SIGNATURE_BYTES = 16


@dataclass(frozen=True, slots=True)
class UnsubscribeTarget:
    """Who a token unsubscribes."""

    tenant_id: uuid.UUID
    contact_id: uuid.UUID


def _sign(secret: str, payload: bytes) -> bytes:
    return hmac.new(secret.encode(), payload, hashlib.sha256).digest()[:_SIGNATURE_BYTES]


def create_unsubscribe_token(secret: str, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> str:
    """Create a URL-safe token for a contact.

    Args:
        secret: `UNSUBSCRIBE_SIGNING_SECRET`.
        tenant_id: Tenant of the contact.
        contact_id: The contact.

    Returns:
        The token.
    """
    payload = tenant_id.bytes + contact_id.bytes
    raw = payload + _sign(secret, payload)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def read_unsubscribe_token(secret: str, token: str) -> UnsubscribeTarget | None:
    """Verify a token and return its target.

    Args:
        secret: `UNSUBSCRIBE_SIGNING_SECRET`.
        token: Token from the unsubscribe link.

    Returns:
        The target, or None if the token is malformed or its signature is wrong.
    """
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, TypeError):
        return None
    if len(raw) != 32 + _SIGNATURE_BYTES:
        return None
    payload, signature = raw[:32], raw[32:]
    if not hmac.compare_digest(signature, _sign(secret, payload)):
        return None
    return UnsubscribeTarget(uuid.UUID(bytes=payload[:16]), uuid.UUID(bytes=payload[16:]))
