"""Validation of registered JWT claims against the injected `Clock`.

PyJWT verifies signatures, but its time checks read the system clock. LaneReach validates
`exp`, `nbf`, `iat`, `iss` and `aud` itself so tests control time and the rules are identical
for dev and OIDC tokens.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from lanereach.core.errors import AuthenticationError

REQUIRED_TIME_CLAIMS = ("exp", "nbf", "iat")


def validate_registered_claims(
    claims: Mapping[str, Any],
    *,
    now: datetime,
    issuer: str,
    audience: str,
    leeway_seconds: int,
) -> None:
    """Check a decoded token's registered claims.

    Args:
        claims: Claims from a token whose signature has already been verified.
        now: Current time from the `Clock`.
        issuer: The exact expected `iss`.
        audience: The audience LaneReach accepts; `aud` may be a string or a list.
        leeway_seconds: Allowed clock skew between the issuer and LaneReach.

    Raises:
        AuthenticationError: If any claim is missing, malformed or fails its check.
    """
    timestamp = now.timestamp()
    for claim in REQUIRED_TIME_CLAIMS:
        value = claims.get(claim)
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise AuthenticationError(f"Token is missing a valid '{claim}' claim.")

    if timestamp >= claims["exp"] + leeway_seconds:
        raise AuthenticationError("Token has expired.")
    if timestamp < claims["nbf"] - leeway_seconds:
        raise AuthenticationError("Token is not valid yet.")
    if timestamp < claims["iat"] - leeway_seconds:
        raise AuthenticationError("Token was issued in the future.")

    if claims.get("iss") != issuer:
        raise AuthenticationError("Token issuer is not accepted.")

    token_audience = claims.get("aud")
    audiences = [token_audience] if isinstance(token_audience, str) else token_audience
    if not isinstance(audiences, list) or audience not in audiences:
        raise AuthenticationError("Token audience is not accepted.")

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise AuthenticationError("Token is missing a subject.")


def read_string_list_claim(claims: Mapping[str, Any], name: str) -> list[str]:
    """Read a claim that should be a list of strings, tolerating a single string.

    Args:
        claims: Decoded token claims.
        name: Claim name.

    Returns:
        The string values; an empty list when the claim is absent or malformed.
    """
    value = claims.get(name)
    if isinstance(value, str):
        return value.split()
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []
