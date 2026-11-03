"""AI runs: one row per AI task execution (LLM call or rule match), with usage and cost.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from lanereach.core.tenancy import tenant_rls_statements

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_runs",
        sa.Column("task", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("input_ref", sa.String(length=100), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_ai_runs_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_runs")),
    )
    op.create_index(op.f("ix_ai_runs_tenant_id"), "ai_runs", ["tenant_id"], unique=False)
    op.create_index(
        "ix_ai_runs_tenant_id_created_at", "ai_runs", ["tenant_id", "created_at"], unique=False
    )
    op.create_index("ix_ai_runs_tenant_id_task", "ai_runs", ["tenant_id", "task"], unique=False)

    for statement in tenant_rls_statements("ai_runs"):
        op.execute(statement)


def downgrade() -> None:
    op.drop_index("ix_ai_runs_tenant_id_task", table_name="ai_runs")
    op.drop_index("ix_ai_runs_tenant_id_created_at", table_name="ai_runs")
    op.drop_index(op.f("ix_ai_runs_tenant_id"), table_name="ai_runs")
    op.drop_table("ai_runs")
