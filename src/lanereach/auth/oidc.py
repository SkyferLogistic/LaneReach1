"""Validation of RS256 bearer tokens issued by external OIDC identity providers.

The validator is IdP-agnostic (Microsoft Entra ID first; Keycloak, Auth0 or Entra External ID
later). Each customer tenant registers its issuer and external tenant/org ID in
`tenant_identity_providers`; the validator:

1. reads the *unverified* header and claims only to find `kid`, `iss` and the tenant claim;
2. looks the issuer + external tenant up in the registry, which yields the LaneReach tenant and
   the JWKS URI. An unregistered issuer is rejected before any network call, so a token can
   never make LaneReach fetch an attacker-chosen URL;
3. fetches the signing key from the cached JWKS (refreshed on an unknown `kid`);
4. verifies the signature with RS256 only (no `alg` confusion) and then the registered claims.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

import httpx
import jwt
import structlog

from lanereach.auth.jwt_claims import read_string_list_claim, validate_registered_claims
from lanereach.auth.principal import AuthMethod, Principal
from lanereach.auth.roles import parse_roles, scopes_for_roles
from lanereach.core.clock import Clock
from lanereach.core.config import Settings
from lanereach.core.errors import AuthenticationError, ServiceUnavailableError

OIDC_ALGORITHM = "RS256"

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RegisteredIdentityProvider:
    """A registry entry for an issuer + external tenant pair."""

    tenant_id: uuid.UUID
    jwks_uri: str


class IdentityProviderRegistry(Protocol):
    """Looks up which LaneReach tenant an external identity belongs to."""

    async def find_identity_provider(
        self, issuer: str, external_tenant_id: str
    ) -> RegisteredIdentityProvider | None:
        """Return the registered provider, or None when the pair is unknown or inactive."""
        ...


@dataclass
class _CachedKeySet:
    keys: dict[str, jwt.PyJWK]
    fetched_at: datetime | None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class JWKSCache:
    """Caches JSON Web Key Sets per JWKS URI.

    Keys are refreshed when the cache entry is older than `ttl`, or when a token presents an
    unknown `kid` (key rotation) — but at most once per `min_refresh_interval`, so a flood of
    tokens with random `kid` values cannot turn LaneReach into a JWKS request amplifier.

    Args:
        http_client: Client used to download key sets.
        clock: Time source for cache expiry.
        ttl: Maximum age of a cached key set.
        min_refresh_interval: Minimum time between two downloads of the same key set.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        clock: Clock,
        *,
        ttl: timedelta,
        min_refresh_interval: timedelta,
    ) -> None:
        self._http_client = http_client
        self._clock = clock
        self._ttl = ttl
        self._min_refresh_interval = min_refresh_interval
        self._entries: dict[str, _CachedKeySet] = {}
        self._entries_lock = asyncio.Lock()

    async def get_signing_key(self, jwks_uri: str, key_id: str) -> jwt.PyJWK | None:
        """Return the key with `key_id` from the key set at `jwks_uri`.

        Args:
            jwks_uri: Registered JWKS URI of the issuer.
            key_id: The token's `kid` header.

        Returns:
            The key, or None if the key set does not contain it even after a refresh.

        Raises:
            ServiceUnavailableError: If the key set cannot be downloaded or parsed.
        """
        entry = await self._get_entry(jwks_uri)
        async with entry.lock:
            if self._should_download(entry, key_id):
                entry.keys = await self._download_key_set(jwks_uri)
                entry.fetched_at = self._clock.now()
            return entry.keys.get(key_id)

    def _should_download(self, entry: _CachedKeySet, key_id: str) -> bool:
        if entry.fetched_at is None:
            return True
        age = self._clock.now() - entry.fetched_at
        if age >= self._ttl:
            return True
        return key_id not in entry.keys and age >= self._min_refresh_interval

    async def _get_entry(self, jwks_uri: str) -> _CachedKeySet:
        async with self._entries_lock:
            return self._entries.setdefault(jwks_uri, _CachedKeySet(keys={}, fetched_at=None))

    async def _download_key_set(self, jwks_uri: str) -> dict[str, jwt.PyJWK]:
        try:
            response = await self._http_client.get(jwks_uri)
            response.raise_for_status()
            document: Any = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("jwks_download_failed", jwks_uri=jwks_uri, error=type(exc).__name__)
            raise ServiceUnavailableError("Identity provider keys are unavailable.") from exc

        keys: dict[str, jwt.PyJWK] = {}
        for raw_key in document.get("keys", []) if isinstance(document, dict) else []:
            if not isinstance(raw_key, dict) or not raw_key.get("kid"):
                continue
            if raw_key.get("use", "sig") != "sig" or raw_key.get("kty") != "RSA":
                continue
            try:
                keys[str(raw_key["kid"])] = jwt.PyJWK(raw_key, algorithm=OIDC_ALGORITHM)
            except (jwt.PyJWKError, jwt.InvalidKeyError):
                logger.warning("jwks_key_skipped", jwks_uri=jwks_uri, kid=raw_key.get("kid"))
        return keys


class OIDCTokenValidator:
    """Authenticates OIDC bearer tokens.

    Args:
        settings: Supplies audience, leeway and claim names.
        clock: Current time for claim checks.
        jwks_cache: Source of signing keys.
        registry: Maps issuer + external tenant to a LaneReach tenant.
    """

    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        jwks_cache: JWKSCache,
        registry: IdentityProviderRegistry,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._jwks_cache = jwks_cache
        self._registry = registry

    async def authenticate_oidc_token(self, token: str) -> Principal:
        """Validate `token` and build the principal it represents.

        Args:
            token: The encoded JWT.

        Returns:
            The authenticated principal.

        Raises:
            AuthenticationError: If the token is malformed, from an unregistered issuer,
                signed by an unknown key, or fails any claim check.
            ServiceUnavailableError: If the issuer's key set cannot be downloaded.
        """
        try:
            header = jwt.get_unverified_header(token)
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise AuthenticationError("Token is invalid.") from exc

        if header.get("alg") != OIDC_ALGORITHM:
            raise AuthenticationError("Token algorithm is not accepted.")
        key_id = header.get("kid")
        if not isinstance(key_id, str) or not key_id:
            raise AuthenticationError("Token is missing a key ID.")

        issuer = unverified.get("iss")
        external_tenant_id = unverified.get(self._settings.oidc_tenant_claim)
        if not isinstance(issuer, str) or not isinstance(external_tenant_id, str):
            raise AuthenticationError("Token is missing issuer or tenant claims.")

        provider = await self._registry.find_identity_provider(issuer, external_tenant_id)
        if provider is None:
            raise AuthenticationError("Identity provider is not registered.")

        signing_key = await self._jwks_cache.get_signing_key(provider.jwks_uri, key_id)
        if signing_key is None:
            raise AuthenticationError("Token signing key is unknown.")

        try:
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=[OIDC_ALGORITHM],
                options={
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                    "verify_aud": False,
                    "verify_iss": False,
                },
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError("Token signature is invalid.") from exc

        validate_registered_claims(
            claims,
            now=self._clock.now(),
            issuer=issuer,
            audience=self._settings.jwt_audience,
            leeway_seconds=self._settings.jwt_leeway_seconds,
        )
        roles = parse_roles(read_string_list_claim(claims, self._settings.oidc_roles_claim))
        return Principal(
            subject=str(claims["sub"]),
            tenant_id=provider.tenant_id,
            roles=roles,
            scopes=scopes_for_roles(roles),
            auth_method=AuthMethod.OIDC,
        )
