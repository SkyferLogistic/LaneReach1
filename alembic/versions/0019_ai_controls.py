"""AI controls: monthly budget and AI-or-rules per area, built-in reply messages.

Adds the tenant's `ai_controls`, who writes each kind of reply (`draft_source`) and who wrote a
follow-up suggestion (`written_by`). Existing rows keep today's behaviour (AI, no budget).

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-03 05:38:08.648419+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "follow_up_suggestions",
        sa.Column("written_by", sa.String(length=10), server_default="ai", nullable=False),
    )
    op.add_column(
        "tenant_reply_policies",
        sa.Column("draft_source", sa.String(length=20), server_default="ai", nullable=False),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "ai_controls",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "ai_controls")
    op.drop_column("tenant_reply_policies", "draft_source")
    op.drop_column("follow_up_suggestions", "written_by")
