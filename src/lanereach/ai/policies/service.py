"""Read and change a tenant's reply policy."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.ai.policies.models import TenantReplyPolicy
from lanereach.ai.policy import (
    DEFAULT_POLICIES,
    ActionMode,
    DraftSource,
    Handover,
    ProtectionAction,
)
from lanereach.ai.schemas import ReplyBucket
from lanereach.core.clock import Clock
from lanereach.core.errors import BusinessRuleViolationError
from lanereach.core.tenancy import require_session_tenant_id

# Buckets where a person wrote to us and expects an answer. Every other bucket (bounces,
# complaints, unsubscribes, automatic replies...) has a fixed, protective treatment that a
# tenant cannot loosen.
CONFIGURABLE_BUCKETS = frozenset(
    bucket
    for bucket, policy in DEFAULT_POLICIES.items()
    if policy.action_mode is not ActionMode.NONE
)


@dataclass(frozen=True)
class EffectiveReplyPolicy:
    """The treatment of one bucket after the tenant's override."""

    bucket: ReplyBucket
    default_action_mode: ActionMode
    action_mode: ActionMode
    auto_send_enabled: bool
    draft_source: DraftSource
    protection_actions: tuple[ProtectionAction, ...]
    handover: Handover | None
    configurable: bool
    overridden: bool
    updated_by: str | None


class ReplyPolicyService:
    """Tenant reply-policy overrides.

    Args:
        session: Tenant session.
        clock: Time source.
    """

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def list_effective_policies(self) -> list[EffectiveReplyPolicy]:
        """Every bucket with its effective treatment.

        Returns:
            One entry per bucket, in bucket order.
        """
        overrides = {
            row.bucket: row for row in (await self._session.scalars(select(TenantReplyPolicy)))
        }
        return [self._effective(bucket, overrides.get(bucket)) for bucket in ReplyBucket]

    async def set_bucket_policy(
        self,
        bucket: ReplyBucket,
        *,
        action_mode: ActionMode | None,
        auto_send_enabled: bool,
        updated_by: str,
        draft_source: DraftSource | None = None,
    ) -> EffectiveReplyPolicy:
        """Override the treatment of one conversational bucket.

        Args:
            bucket: Bucket to change.
            action_mode: New action mode; None restores the default.
            auto_send_enabled: Allow `auto_send` (only honoured with that action mode).
            updated_by: Who changed it (principal subject).
            draft_source: Who writes the answer; None keeps the current choice.

        Returns:
            The new effective policy.

        Raises:
            BusinessRuleViolationError: For a protective bucket, `none` on a conversational
                bucket (a human reply would go unanswered), or auto-send enabled without the
                `auto_send` action mode.
        """
        if bucket not in CONFIGURABLE_BUCKETS:
            raise BusinessRuleViolationError(
                f"The '{bucket}' bucket has a fixed, protective treatment and cannot be changed."
            )
        if action_mode is ActionMode.NONE:
            raise BusinessRuleViolationError("A human reply must be answered or forwarded.")
        if auto_send_enabled and action_mode is not ActionMode.AUTO_SEND:
            raise BusinessRuleViolationError("auto_send_enabled requires action_mode 'auto_send'.")
        row = await self._session.scalar(
            select(TenantReplyPolicy).where(TenantReplyPolicy.bucket == bucket)
        )
        now = self._clock.now()
        if row is None:
            row = TenantReplyPolicy(
                tenant_id=require_session_tenant_id(self._session),
                bucket=bucket,
                draft_source=DraftSource.AI,
                created_at=now,
            )
            self._session.add(row)
        row.action_mode = action_mode
        row.auto_send_enabled = auto_send_enabled
        if draft_source is not None:
            row.draft_source = draft_source
        row.updated_by = updated_by
        row.updated_at = now
        await self._session.flush()
        return self._effective(bucket, row)

    @staticmethod
    def _effective(bucket: ReplyBucket, row: TenantReplyPolicy | None) -> EffectiveReplyPolicy:
        default = DEFAULT_POLICIES[bucket]
        mode = row.action_mode if row and row.action_mode else default.action_mode
        return EffectiveReplyPolicy(
            bucket=bucket,
            default_action_mode=default.action_mode,
            action_mode=mode,
            auto_send_enabled=bool(row and row.auto_send_enabled),
            draft_source=row.draft_source if row and row.draft_source else DraftSource.AI,
            protection_actions=default.protection_actions,
            handover=default.handover,
            configurable=bucket in CONFIGURABLE_BUCKETS,
            overridden=row is not None
            and (row.action_mode is not None or row.draft_source is DraftSource.BUILT_IN),
            updated_by=row.updated_by if row else None,
        )
