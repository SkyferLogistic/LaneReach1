"""Authentication and API key business logic."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lanereach.auth.api_keys import (
    api_key_matches_hash,
    api_key_prefix,
    generate_api_key,
    looks_like_api_key,
)
from lanereach.auth.dev_tokens import IssuedDevToken, authenticate_dev_token, issue_dev_token
from lanereach.auth.models import ApiKey, ApiKeyEnvironment
from lanereach.auth.oidc import JWKSCache, OIDCTokenValidator
from lanereach.auth.principal import AuthMethod, Principal
from lanereach.auth.repository import (
    ApiKeyRepository,
    AuthLookupRepository,
    WebhookReceiptRepository,
)
from lanereach.auth.roles import Role, Scope, parse_scopes
from lanereach.auth.webhook_signatures import verify_webhook_signature
from lanereach.core.clock import Clock
from lanereach.core.config import AuthMode, Settings
from lanereach.core.errors import (
    AuthenticationError,
    BusinessRuleViolationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from lanereach.core.tenancy import open_tenant_session

logger = structlog.get_logger(__name__)


class BearerAuthenticator:
    """Resolves a bearer credential to a `Principal`.

    API keys are recognised by their `lr_live_` / `lr_test_` shape; anything else is treated
    as a JWT and validated according to `AUTH_MODE`.

    Args:
        settings: Process configuration.
        clock: Time source.
        session_factory: Factory bound to the application role.
        jwks_cache: OIDC signing key cache.
    """

    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        session_factory: async_sessionmaker[AsyncSession],
        jwks_cache: JWKSCache,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._session_factory = session_factory
        self._lookups = AuthLookupRepository(session_factory)
        self._oidc = OIDCTokenValidator(settings, clock, jwks_cache, self._lookups)

    async def authenticate_bearer_token(self, token: str) -> Principal:
        """Authenticate a bearer credential.

        Args:
            token: The credential from the `Authorization: Bearer` header.

        Returns:
            The authenticated principal.

        Raises:
            AuthenticationError: If the credential is invalid for any reason.
            ServiceUnavailableError: If OIDC signing keys cannot be downloaded.
        """
        if looks_like_api_key(token):
            return await self.authenticate_api_key(token)
        if self._settings.auth_mode is AuthMode.DEV:
            return authenticate_dev_token(token, self._settings, self._clock)
        return await self._oidc.authenticate_oidc_token(token)

    async def authenticate_api_key(self, plaintext: str) -> Principal:
        """Authenticate a tenant API key and record its use.

        `last_used_at` is written at most once per `API_KEY_LAST_USED_WRITE_INTERVAL_SECONDS`
        so a busy integration does not turn every read into a write.

        Args:
            plaintext: The presented key.

        Returns:
            A principal carrying exactly the key's scopes.

        Raises:
            AuthenticationError: If the key is unknown, revoked, expired or belongs to a
                suspended tenant.
        """
        credential = await self._lookups.find_api_key_credential(api_key_prefix(plaintext))
        if credential is None or not api_key_matches_hash(plaintext, credential.key_hash):
            raise AuthenticationError("API key is invalid.")
        now = self._clock.now()
        if credential.revoked_at is not None:
            raise AuthenticationError("API key has been revoked.")
        if credential.expires_at is not None and credential.expires_at <= now:
            raise AuthenticationError("API key has expired.")

        write_interval = timedelta(seconds=self._settings.api_key_last_used_write_interval_seconds)
        if credential.last_used_at is None or now - credential.last_used_at >= write_interval:
            async with open_tenant_session(self._session_factory, credential.tenant_id) as session:
                await ApiKeyRepository(session).record_api_key_use(credential.api_key_id, now)

        return Principal(
            subject=f"api_key:{credential.api_key_id}",
            tenant_id=credential.tenant_id,
            roles=frozenset(),
            scopes=parse_scopes(credential.scopes),
            auth_method=AuthMethod.API_KEY,
        )

    async def list_active_tenants(self) -> list[tuple[uuid.UUID, str, str]]:
        """Active tenants (dev login screen only).

        Returns:
            (id, name, slug) tuples.
        """
        return await self._lookups.list_active_tenants()

    async def issue_dev_token_for_tenant(
        self,
        *,
        tenant_id: uuid.UUID,
        subject: str,
        roles: frozenset[Role],
        scopes: frozenset[Scope] | None,
        lifetime: timedelta,
    ) -> IssuedDevToken:
        """Mint a dev token after checking the tenant exists and is active.

        Args:
            tenant_id: Tenant the token acts in.
            subject: Token subject.
            roles: Roles to embed.
            scopes: Explicit scopes, or None to derive them from roles.
            lifetime: Requested lifetime.

        Returns:
            The token.

        Raises:
            NotFoundError: If the tenant does not exist or is suspended.
        """
        if not await self._lookups.is_tenant_active(tenant_id):
            raise NotFoundError("Tenant not found.")
        return issue_dev_token(
            self._settings,
            self._clock,
            tenant_id=tenant_id,
            subject=subject,
            roles=roles,
            scopes=scopes,
            lifetime=lifetime,
        )


@dataclass(frozen=True, slots=True)
class CreatedApiKey:
    """A newly created key plus its one-time plaintext."""

    api_key: ApiKey
    plaintext: str


class ApiKeyService:
    """Creates, lists and revokes the current tenant's API keys.

    Args:
        session: Tenant-scoped session.
        settings: Process configuration (key family depends on the environment).
        clock: Time source.
    """

    def __init__(self, session: AsyncSession, settings: Settings, clock: Clock) -> None:
        self._repository = ApiKeyRepository(session)
        self._settings = settings
        self._clock = clock

    async def create_api_key(
        self,
        principal: Principal,
        *,
        name: str,
        scopes: frozenset[Scope],
        expires_at: datetime | None,
    ) -> CreatedApiKey:
        """Create an API key for the principal's tenant.

        A caller can only delegate scopes it holds itself, so an API key can never be used to
        mint a more powerful one.

        Args:
            principal: The caller.
            name: Human label for the key.
            scopes: Scopes to grant.
            expires_at: Optional expiry.

        Returns:
            The stored key and its plaintext (shown once).

        Raises:
            PermissionDeniedError: If `scopes` exceeds the caller's scopes.
            BusinessRuleViolationError: If `expires_at` is not in the future.
        """
        excess = scopes - principal.scopes
        if excess:
            raise PermissionDeniedError(
                "Cannot grant scopes you do not hold: " + ", ".join(sorted(excess))
            )
        now = self._clock.now()
        if expires_at is not None and expires_at <= now:
            raise BusinessRuleViolationError("expires_at must be in the future.")

        environment = (
            ApiKeyEnvironment.LIVE if self._settings.is_production else ApiKeyEnvironment.TEST
        )
        generated = generate_api_key(environment)
        api_key = ApiKey(
            name=name,
            key_prefix=generated.prefix,
            key_hash=generated.key_hash,
            environment=generated.environment,
            scopes=sorted(scope.value for scope in scopes),
            created_by=principal.subject,
            created_at=now,
            expires_at=expires_at,
        )
        await self._repository.add_api_key(api_key)
        logger.info("api_key_created", api_key_id=str(api_key.id), key_prefix=api_key.key_prefix)
        return CreatedApiKey(api_key=api_key, plaintext=generated.plaintext)

    async def list_api_keys(
        self, *, limit: int, cursor: str | None
    ) -> tuple[Sequence[ApiKey], str | None]:
        """List the tenant's API keys, newest first.

        Args:
            limit: Page size.
            cursor: Cursor from the previous page.

        Returns:
            The keys on this page and the next cursor.
        """
        return await self._repository.list_api_keys(limit=limit, cursor=cursor)

    async def revoke_api_key(self, api_key_id: uuid.UUID) -> ApiKey:
        """Revoke a key. Revoking an already revoked key is a no-op.

        Args:
            api_key_id: Key to revoke.

        Returns:
            The revoked key.

        Raises:
            NotFoundError: If the key does not exist in the caller's tenant.
        """
        api_key = await self._repository.get_api_key(api_key_id)
        if api_key is None:
            raise NotFoundError("API key not found.")
        if api_key.revoked_at is None:
            api_key.revoked_at = self._clock.now()
            logger.info("api_key_revoked", api_key_id=str(api_key.id))
        return api_key


def verify_webhook_delivery(
    settings: Settings,
    clock: Clock,
    *,
    provider: str,
    signature_header: str | None,
    body: bytes,
) -> None:
    """Verify the HMAC signature of a webhook from `provider`.

    Args:
        settings: Supplies the per-provider signing secrets and tolerance.
        clock: Current time.
        provider: Provider name from the URL.
        signature_header: Raw `X-LaneReach-Signature` header.
        body: Raw request body.

    Raises:
        AuthenticationError: If the provider has no secret configured or the signature is
            invalid. Both cases look identical to the caller.
    """
    secret = settings.webhook_signing_secrets.get(provider)
    if secret is None:
        raise AuthenticationError("Webhook signature does not match.")
    verify_webhook_signature(
        secret=secret.get_secret_value(),
        header_value=signature_header,
        body=body,
        now=clock.now(),
        tolerance_seconds=settings.webhook_signature_tolerance_seconds,
    )


async def ensure_webhook_event_not_replayed(
    session: AsyncSession, clock: Clock, *, provider: str, event_id: str
) -> None:
    """Record a webhook event ID, rejecting it if it was already processed.

    Args:
        session: Tenant-scoped session of the tenant the event belongs to.
        clock: Time source.
        provider: Provider name.
        event_id: Provider's event ID.

    Raises:
        ConflictError: If the event was already received (replay).
    """
    repository = WebhookReceiptRepository(session)
    is_new = await repository.record_webhook_receipt(
        provider=provider, event_id=event_id, received_at=clock.now()
    )
    if not is_new:
        raise ConflictError("Webhook event was already processed.")
