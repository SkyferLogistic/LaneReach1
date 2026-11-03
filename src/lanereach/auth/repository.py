"""Database access for authentication and API key management.

Pre-authentication lookups (which tenant does this API key or IdP identity belong to?) cannot
run in a tenant session because the tenant is not known yet. They call narrow
`SECURITY DEFINER` SQL functions (created in the foundation migration) from a system session;
the application role still cannot read the underlying tables across tenants.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lanereach.auth.models import ApiKey, WebhookReceipt
from lanereach.auth.oidc import RegisteredIdentityProvider
from lanereach.core.pagination import paginate_newest_first
from lanereach.core.tenancy import open_system_session, require_session_tenant_id


@dataclass(frozen=True, slots=True)
class ApiKeyCredential:
    """What authentication needs to know about an API key found by prefix."""

    api_key_id: uuid.UUID
    tenant_id: uuid.UUID
    key_hash: str
    scopes: list[str]
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None


class AuthLookupRepository:
    """Cross-tenant lookups used before a tenant is known.

    Args:
        session_factory: Factory bound to the application role.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def find_identity_provider(
        self, issuer: str, external_tenant_id: str
    ) -> RegisteredIdentityProvider | None:
        """Find the active tenant registered for an IdP identity.

        Args:
            issuer: The token's `iss`.
            external_tenant_id: The token's tenant/org claim.

        Returns:
            The registration, or None when unknown, inactive, or the tenant is suspended.
        """
        async with open_system_session(self._session_factory) as session:
            row = (
                await session.execute(
                    text("SELECT tenant_id, jwks_uri FROM auth_find_identity_provider(:i, :e)"),
                    {"i": issuer, "e": external_tenant_id},
                )
            ).first()
        if row is None:
            return None
        return RegisteredIdentityProvider(tenant_id=row.tenant_id, jwks_uri=row.jwks_uri)

    async def find_api_key_credential(self, key_prefix: str) -> ApiKeyCredential | None:
        """Find an API key of an active tenant by its lookup prefix.

        Args:
            key_prefix: First characters of the presented key.

        Returns:
            The stored credential, or None.
        """
        async with open_system_session(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT api_key_id, tenant_id, key_hash, scopes, expires_at, revoked_at,"
                        " last_used_at FROM auth_find_api_key(:prefix)"
                    ),
                    {"prefix": key_prefix},
                )
            ).first()
        if row is None:
            return None
        return ApiKeyCredential(
            api_key_id=row.api_key_id,
            tenant_id=row.tenant_id,
            key_hash=row.key_hash,
            scopes=list(row.scopes),
            expires_at=row.expires_at,
            revoked_at=row.revoked_at,
            last_used_at=row.last_used_at,
        )

    async def list_active_tenants(self) -> list[tuple[uuid.UUID, str, str]]:
        """Active tenants, for the dev-mode login screen only.

        Returns:
            (id, name, slug) tuples ordered by name.
        """
        async with open_system_session(self._session_factory) as session:
            rows = await session.execute(
                text("SELECT tenant_id, name, slug FROM auth_list_active_tenants()")
            )
            return [(row.tenant_id, row.name, row.slug) for row in rows]

    async def is_tenant_active(self, tenant_id: uuid.UUID) -> bool:
        """Return whether a tenant exists and is active.

        Args:
            tenant_id: The tenant to check.

        Returns:
            True for an existing, active tenant.
        """
        async with open_system_session(self._session_factory) as session:
            result = await session.scalar(
                text("SELECT auth_tenant_is_active(:tenant_id)"), {"tenant_id": tenant_id}
            )
        return bool(result)


class ApiKeyRepository:
    """Tenant-scoped access to `api_keys`.

    Args:
        session: A tenant-scoped session.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_api_key(self, api_key: ApiKey) -> ApiKey:
        """Persist a new API key for the session's tenant.

        Args:
            api_key: The key row; its `tenant_id` is set from the session.

        Returns:
            The persisted row.
        """
        api_key.tenant_id = require_session_tenant_id(self._session)
        self._session.add(api_key)
        await self._session.flush()
        return api_key

    async def get_api_key(self, api_key_id: uuid.UUID) -> ApiKey | None:
        """Load an API key visible to the session's tenant.

        Args:
            api_key_id: Key ID.

        Returns:
            The key, or None when it does not exist or belongs to another tenant.
        """
        return await self._session.get(ApiKey, api_key_id)

    async def list_api_keys(
        self, *, limit: int, cursor: str | None
    ) -> tuple[Sequence[ApiKey], str | None]:
        """List the tenant's API keys, newest first.

        Args:
            limit: Page size.
            cursor: Cursor from the previous page.

        Returns:
            The page's keys and the next cursor.
        """
        return await paginate_newest_first(
            self._session,
            select(ApiKey),
            created_at_column=ApiKey.created_at,
            id_column=ApiKey.id,
            limit=limit,
            cursor=cursor,
        )

    async def record_api_key_use(self, api_key_id: uuid.UUID, used_at: datetime) -> None:
        """Update `last_used_at`.

        Args:
            api_key_id: Key ID.
            used_at: Time of use.
        """
        await self._session.execute(
            update(ApiKey).where(ApiKey.id == api_key_id).values(last_used_at=used_at)
        )


class WebhookReceiptRepository:
    """Tenant-scoped access to `webhook_receipts`.

    Args:
        session: A tenant-scoped session.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_webhook_receipt(
        self, *, provider: str, event_id: str, received_at: datetime
    ) -> bool:
        """Record that a webhook event was received.

        Args:
            provider: Sending provider name.
            event_id: Provider's event ID.
            received_at: Receipt time.

        Returns:
            True if this is the first receipt; False if the event was already recorded
            (a replay).
        """
        inserted = await self._session.scalar(
            insert(WebhookReceipt)
            .values(
                id=uuid.uuid4(),
                tenant_id=require_session_tenant_id(self._session),
                provider=provider,
                event_id=event_id,
                received_at=received_at,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "provider", "event_id"])
            .returning(WebhookReceipt.id)
        )
        return inserted is not None
