"""Locally signed HS256 tokens for development and tests.

Dev tokens let anyone mint a token for any tenant and role, so they are only accepted when
`AUTH_MODE=dev`, and `Settings` refuses `AUTH_MODE=dev` in production.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt

from lanereach.auth.jwt_claims import read_string_list_claim, validate_registered_claims
from lanereach.auth.principal import AuthMethod, Principal
from lanereach.auth.roles import Role, Scope, parse_roles, parse_scopes, scopes_for_roles
from lanereach.core.clock import Clock
from lanereach.core.config import Settings
from lanereach.core.errors import AuthenticationError

DEV_TOKEN_ALGORITHM = "HS256"  # noqa: S105 - an algorithm name, not a secret


@dataclass(frozen=True, slots=True)
class IssuedDevToken:
    """A freshly minted dev token."""

    access_token: str
    expires_at: datetime


def issue_dev_token(
    settings: Settings,
    clock: Clock,
    *,
    tenant_id: uuid.UUID,
    subject: str,
    roles: frozenset[Role],
    scopes: frozenset[Scope] | None,
    lifetime: timedelta,
) -> IssuedDevToken:
    """Mint an HS256 token for a tenant and role set.

    Args:
        settings: Supplies the signing secret, issuer and audience.
        clock: Supplies `iat`/`nbf`/`exp`.
        tenant_id: LaneReach tenant the token acts in.
        subject: The `sub` claim.
        roles: Roles to embed.
        scopes: Explicit scopes; when None, scopes are derived from roles at validation time.
        lifetime: How long the token is valid, capped by `DEV_TOKEN_MAX_LIFETIME_SECONDS`.

    Returns:
        The encoded token and its expiry.
    """
    issued_at = clock.now()
    capped = min(lifetime, timedelta(seconds=settings.dev_token_max_lifetime_seconds))
    expires_at = issued_at + capped
    claims: dict[str, object] = {
        "iss": settings.dev_token_issuer,
        "aud": settings.jwt_audience,
        "sub": subject,
        "tid": str(tenant_id),
        "roles": sorted(role.value for role in roles),
        "iat": int(issued_at.timestamp()),
        "nbf": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    if scopes is not None:
        claims["scopes"] = sorted(scope.value for scope in scopes)
    token = jwt.encode(
        claims, settings.dev_token_secret.get_secret_value(), algorithm=DEV_TOKEN_ALGORITHM
    )
    return IssuedDevToken(access_token=token, expires_at=expires_at)


def authenticate_dev_token(token: str, settings: Settings, clock: Clock) -> Principal:
    """Validate a dev token and build the principal it represents.

    Args:
        token: The encoded JWT.
        settings: Supplies the signing secret, issuer and audience.
        clock: Current time for expiry checks.

    Returns:
        The authenticated principal.

    Raises:
        AuthenticationError: If the signature, algorithm or any claim is invalid.
    """
    try:
        claims = jwt.decode(
            token,
            settings.dev_token_secret.get_secret_value(),
            algorithms=[DEV_TOKEN_ALGORITHM],
            options={
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iat": False,
                "verify_aud": False,
                "verify_iss": False,
            },
        )
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Token is invalid.") from exc

    validate_registered_claims(
        claims,
        now=clock.now(),
        issuer=settings.dev_token_issuer,
        audience=settings.jwt_audience,
        leeway_seconds=settings.jwt_leeway_seconds,
    )
    try:
        tenant_id = uuid.UUID(str(claims.get("tid")))
    except ValueError as exc:
        raise AuthenticationError("Token is missing a valid tenant.") from exc

    roles = parse_roles(read_string_list_claim(claims, "roles"))
    explicit_scopes = "scopes" in claims
    scopes = (
        parse_scopes(read_string_list_claim(claims, "scopes"))
        if explicit_scopes
        else scopes_for_roles(roles)
    )
    return Principal(
        subject=str(claims["sub"]),
        tenant_id=tenant_id,
        roles=roles,
        scopes=scopes,
        auth_method=AuthMethod.DEV,
    )
