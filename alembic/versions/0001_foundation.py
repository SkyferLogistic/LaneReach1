"""Foundation: tenants, identity providers, API keys, webhook receipts, idempotency keys.

Every table gets a tenant isolation RLS policy. Three SECURITY DEFINER functions provide the
only cross-tenant reads the application role can perform, all needed before a tenant is known:
resolving an IdP identity, resolving an API key prefix, and checking a tenant is active.

Revision ID: 0001
Revises:
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from lanereach.core.tenancy import tenant_rls_statements

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMP = sa.DateTime(timezone=True)


def _tenant_id_column(table: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        "tenant_id",
        sa.Uuid(),
        sa.ForeignKey("tenants.id", ondelete="CASCADE", name=f"fk_{table}_tenant_id_tenants"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.Column("updated_at", TIMESTAMP, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="ck_tenants_status"),
    )

    op.create_table(
        "tenant_identity_providers",
        sa.Column("id", sa.Uuid(), nullable=False),
        _tenant_id_column("tenant_identity_providers"),
        sa.Column("issuer", sa.String(500), nullable=False),
        sa.Column("external_tenant_id", sa.String(200), nullable=False),
        sa.Column("jwks_uri", sa.String(500), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tenant_identity_providers"),
        sa.UniqueConstraint(
            "issuer",
            "external_tenant_id",
            name="uq_tenant_identity_providers_issuer_external_tenant_id",
        ),
    )
    op.create_index(
        "ix_tenant_identity_providers_tenant_id", "tenant_identity_providers", ["tenant_id"]
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        _tenant_id_column("api_keys"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("key_prefix", sa.String(32), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(10), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.Column("last_used_at", TIMESTAMP, nullable=True),
        sa.Column("expires_at", TIMESTAMP, nullable=True),
        sa.Column("revoked_at", TIMESTAMP, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_api_keys"),
        sa.UniqueConstraint("key_prefix", name="uq_api_keys_key_prefix"),
        sa.CheckConstraint("environment IN ('live', 'test')", name="ck_api_keys_environment"),
    )
    op.create_index("ix_api_keys_tenant_id", "api_keys", ["tenant_id"])

    op.create_table(
        "webhook_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        _tenant_id_column("webhook_receipts"),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("event_id", sa.String(200), nullable=False),
        sa.Column("received_at", TIMESTAMP, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_webhook_receipts"),
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "event_id",
            name="uq_webhook_receipts_tenant_id_provider_event_id",
        ),
    )
    op.create_index("ix_webhook_receipts_tenant_id", "webhook_receipts", ["tenant_id"])

    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        _tenant_id_column("idempotency_keys"),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.Column("expires_at", TIMESTAMP, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_keys"),
        sa.UniqueConstraint("tenant_id", "key", name="uq_idempotency_keys_tenant_id_key"),
    )
    op.create_index("ix_idempotency_keys_tenant_id", "idempotency_keys", ["tenant_id"])

    for statement in tenant_rls_statements("tenants", tenant_column="id"):
        op.execute(statement)
    for table in ("tenant_identity_providers", "api_keys", "webhook_receipts", "idempotency_keys"):
        for statement in tenant_rls_statements(table):
            op.execute(statement)

    # Pre-authentication lookups. SECURITY DEFINER runs them as the owner (which bypasses RLS);
    # each returns only the minimum needed and only for active tenants. search_path is pinned
    # so a malicious object in another schema cannot hijack name resolution.
    op.execute(
        """
        CREATE FUNCTION auth_find_identity_provider(p_issuer text, p_external_tenant_id text)
        RETURNS TABLE (tenant_id uuid, jwks_uri text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
        AS $$
            SELECT p.tenant_id, p.jwks_uri
            FROM public.tenant_identity_providers AS p
            JOIN public.tenants AS t ON t.id = p.tenant_id
            WHERE p.issuer = p_issuer
              AND p.external_tenant_id = p_external_tenant_id
              AND p.is_active
              AND t.status = 'active'
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION auth_find_api_key(p_key_prefix text)
        RETURNS TABLE (
            api_key_id uuid, tenant_id uuid, key_hash text, scopes text[],
            expires_at timestamptz, revoked_at timestamptz, last_used_at timestamptz
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
        AS $$
            SELECT k.id, k.tenant_id, k.key_hash, k.scopes, k.expires_at, k.revoked_at,
                   k.last_used_at
            FROM public.api_keys AS k
            JOIN public.tenants AS t ON t.id = k.tenant_id
            WHERE k.key_prefix = p_key_prefix
              AND t.status = 'active'
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION auth_tenant_is_active(p_tenant_id uuid)
        RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
        AS $$
            SELECT EXISTS (
                SELECT 1 FROM public.tenants WHERE id = p_tenant_id AND status = 'active'
            )
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth_tenant_is_active(uuid)")
    op.execute("DROP FUNCTION IF EXISTS auth_find_api_key(text)")
    op.execute("DROP FUNCTION IF EXISTS auth_find_identity_provider(text, text)")
    for table in (
        "idempotency_keys",
        "webhook_receipts",
        "api_keys",
        "tenant_identity_providers",
        "tenants",
    ):
        op.drop_table(table)
