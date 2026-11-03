"""Reply-policy endpoints: how each reply bucket is handled in this tenant."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import Field

from lanereach.ai.policies.service import EffectiveReplyPolicy, ReplyPolicyService
from lanereach.ai.policy import ActionMode, DraftSource, Handover, ProtectionAction
from lanereach.ai.schemas import ReplyBucket
from lanereach.auth.dependencies import TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.openapi import problem_responses
from lanereach.core.schemas import ResponseModel, StrictRequestModel

router = APIRouter(prefix="/ai/reply-policies", tags=["AI"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Admin = Annotated[Principal, Depends(require_scopes(Scope.ADMIN_MANAGE))]


class ReplyPolicyResponse(ResponseModel):
    """Effective treatment of one reply bucket."""

    bucket: ReplyBucket
    default_action_mode: ActionMode
    action_mode: ActionMode
    auto_send_enabled: bool
    draft_source: DraftSource
    protection_actions: list[ProtectionAction]
    handover: Handover | None
    configurable: bool
    overridden: bool
    updated_by: str | None


class ReplyPolicyUpdateRequest(StrictRequestModel):
    """New treatment of a conversational bucket."""

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"action_mode": "auto_send", "auto_send_enabled": True},
                {"draft_source": "built_in"},
            ]
        }
    }

    action_mode: ActionMode | None = None
    auto_send_enabled: bool = False
    draft_source: DraftSource | None = Field(
        default=None,
        description="Who writes the answer: `ai` or `built_in` (the company's message for this "
        "kind of reply, no AI call). Omitted keeps the current choice.",
    )


def get_reply_policy_service(
    session: TenantSession, container: ContainerDependency
) -> ReplyPolicyService:
    """Reply-policy service for the request."""
    return ReplyPolicyService(session, container.clock)


Service = Annotated[ReplyPolicyService, Depends(get_reply_policy_service)]


def _to_response(policy: EffectiveReplyPolicy) -> ReplyPolicyResponse:
    return ReplyPolicyResponse(
        bucket=policy.bucket,
        default_action_mode=policy.default_action_mode,
        action_mode=policy.action_mode,
        auto_send_enabled=policy.auto_send_enabled,
        draft_source=policy.draft_source,
        protection_actions=list(policy.protection_actions),
        handover=policy.handover,
        configurable=policy.configurable,
        overridden=policy.overridden,
        updated_by=policy.updated_by,
    )


@router.get(
    "",
    response_model=list[ReplyPolicyResponse],
    summary="Effective reply policy for every bucket",
    responses=problem_responses(401, 403),
)
async def list_reply_policies(_principal: Reader, service: Service) -> list[ReplyPolicyResponse]:
    """List the reply policy."""
    return [_to_response(policy) for policy in await service.list_effective_policies()]


@router.put(
    "/{bucket}",
    response_model=ReplyPolicyResponse,
    summary="Change how a conversational bucket is answered",
    description="Only buckets where a person expects an answer can change; protective "
    "buckets (bounces, complaints, unsubscribes...) are fixed. `auto_send` needs "
    "`auto_send_enabled: true`; low-confidence replies are always drafted for review.",
    responses=problem_responses(401, 403, 422),
)
async def update_reply_policy(
    bucket: ReplyBucket,
    payload: ReplyPolicyUpdateRequest,
    principal: Admin,
    service: Service,
) -> ReplyPolicyResponse:
    """Override one bucket."""
    return _to_response(
        await service.set_bucket_policy(
            bucket,
            action_mode=payload.action_mode,
            auto_send_enabled=payload.auto_send_enabled,
            updated_by=principal.subject,
            draft_source=payload.draft_source,
        )
    )
