"""Signature images for offices, mobile (WhatsApp) numbers for agents.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add `offices.signature_asset_id` and `senders.mobile`."""
    op.add_column("offices", sa.Column("signature_asset_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_tenant_scoped_signature_asset_id",
        "offices",
        "email_assets",
        ["tenant_id", "signature_asset_id"],
        ["tenant_id", "id"],
        ondelete="SET NULL (signature_asset_id)",
    )
    op.add_column("senders", sa.Column("mobile", sa.String(length=50), nullable=True))


def downgrade() -> None:
    """Remove the columns."""
    op.drop_column("senders", "mobile")
    op.drop_constraint("fk_tenant_scoped_signature_asset_id", "offices", type_="foreignkey")
    op.drop_column("offices", "signature_asset_id")
