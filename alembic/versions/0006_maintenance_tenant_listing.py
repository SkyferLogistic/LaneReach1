"""Tenant listing for scheduled maintenance (data retention runs in every tenant).

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Every tenant, suspended ones included: retention applies to all stored data.
    op.execute(
        """
        CREATE FUNCTION maintenance_list_tenants()
        RETURNS TABLE (tenant_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
        AS $$ SELECT t.id FROM public.tenants AS t ORDER BY t.created_at $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS maintenance_list_tenants()")
