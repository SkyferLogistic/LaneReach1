"""Workflows and immutable workflow versions.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from lanereach.core.tenancy import tenant_rls_statements

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=True),
        sa.Column("latest_version", sa.Integer(), nullable=False),
        sa.Column("paused_at", TIMESTAMP, nullable=True),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.Column("updated_at", TIMESTAMP, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_workflows"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_workflows_tenant_id_tenants",
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_workflows_tenant_id_name"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_workflows_tenant_id_id"),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')", name="ck_workflows_status"
        ),
        sa.CheckConstraint(
            "current_version IS NULL OR current_version <= latest_version",
            name="ck_workflows_current_not_after_latest",
        ),
    )
    op.create_index("ix_workflows_tenant_id", "workflows", ["tenant_id"])

    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("graph", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.Column("published_at", TIMESTAMP, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_versions"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_workflow_versions_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workflow_id"],
            ["workflows.tenant_id", "workflows.id"],
            ondelete="CASCADE",
            name="fk_workflow_versions_workflow",
        ),
        sa.UniqueConstraint(
            "workflow_id", "version", name="uq_workflow_versions_workflow_id_version"
        ),
    )
    op.create_index("ix_workflow_versions_tenant_id", "workflow_versions", ["tenant_id"])

    for table in ("workflows", "workflow_versions"):
        for statement in tenant_rls_statements(table):
            op.execute(statement)

    # Versions are immutable: the graph and number of a saved version never change.
    op.execute(
        """
        CREATE FUNCTION workflow_versions_prevent_graph_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.graph IS DISTINCT FROM OLD.graph
               OR NEW.version IS DISTINCT FROM OLD.version
               OR NEW.workflow_id IS DISTINCT FROM OLD.workflow_id THEN
                RAISE EXCEPTION 'workflow versions are immutable';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER workflow_versions_immutable BEFORE UPDATE ON workflow_versions "
        "FOR EACH ROW EXECUTE FUNCTION workflow_versions_prevent_graph_change()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS workflow_versions_immutable ON workflow_versions")
    op.execute("DROP FUNCTION IF EXISTS workflow_versions_prevent_graph_change()")
    op.drop_table("workflow_versions")
    op.drop_table("workflows")
