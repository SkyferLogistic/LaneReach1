"""Tenant reply-policy overrides."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.ai.policy import ActionMode, DraftSource
from lanereach.ai.schemas import ReplyBucket
from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    StrEnumType,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
)


class TenantReplyPolicy(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A tenant's treatment of one reply bucket (overrides the default policy)."""

    __tablename__ = "tenant_reply_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "bucket"),)

    bucket: Mapped[ReplyBucket] = mapped_column(StrEnumType(ReplyBucket, 30))
    action_mode: Mapped[ActionMode | None] = mapped_column(StrEnumType(ActionMode, 30))
    auto_send_enabled: Mapped[bool] = mapped_column(default=False)
    draft_source: Mapped[DraftSource] = mapped_column(
        StrEnumType(DraftSource, 20), default=DraftSource.AI, server_default=DraftSource.AI.value
    )
    updated_by: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime]
