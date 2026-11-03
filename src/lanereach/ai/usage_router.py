"""AI usage and controls: `/settings/ai`."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import Field

from lanereach.ai.controls import AIControls
from lanereach.ai.usage import AIUsageService, AIUsageSummary
from lanereach.auth.dependencies import TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.openapi import READ, WRITE
from lanereach.core.schemas import ResponseModel, StrictRequestModel

router = APIRouter(prefix="/settings/ai", tags=["Settings"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Admin = Annotated[Principal, Depends(require_scopes(Scope.ADMIN_MANAGE))]


class AIControlsBody(StrictRequestModel):
    """The company's AI controls."""

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "monthly_budget_usd": 50,
                    "lead_reading": "ai",
                    "follow_ups": "built_in",
                    "digest": "rules",
                }
            ]
        }
    }

    monthly_budget_usd: float | None = Field(
        default=None,
        ge=0,
        le=1_000_000,
        description="Estimated AI spend allowed per calendar month (UTC), in USD; null = no cap. "
        "When reached, no model is called until next month: built-in messages and rules "
        "take over.",
    )
    lead_reading: Literal["ai", "rules"] = Field(
        default="ai",
        description="`ai` reads buying intent from each new deal's reply; `rules` scores from "
        "the shipment facts, reply speed and history only.",
    )
    follow_ups: Literal["ai", "built_in"] = Field(
        default="ai",
        description="Who writes follow-ups for quiet deals: the AI or the company's message.",
    )
    digest: Literal["ai", "rules"] = Field(
        default="ai", description="`ai` writes the weekly summary; `rules` shows the numbers."
    )


class AIControlsResponse(ResponseModel):
    """The company's AI controls."""

    monthly_budget_usd: float | None
    lead_reading: Literal["ai", "rules"]
    follow_ups: Literal["ai", "built_in"]
    digest: Literal["ai", "rules"]


class TaskUsageResponse(ResponseModel):
    """One AI task this month."""

    task: str
    runs: int
    failed: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    model_tier: Literal["light", "main"]


class AISettingsResponse(ResponseModel):
    """AI controls and this month's usage."""

    controls: AIControlsResponse
    month_start: datetime
    spent_usd: float
    budget_used_percent: float | None
    budget_reached: bool
    provider: str
    main_model: str
    light_model: str | None
    prices_configured: bool = Field(
        description="False when no LLM_PRICE_* is set: costs show as 0 and a budget never fills up."
    )
    by_task: list[TaskUsageResponse]


def get_ai_usage_service(session: TenantSession, container: ContainerDependency) -> AIUsageService:
    """AI usage service for the request."""
    return AIUsageService(session, container.clock, container.settings)


Service = Annotated[AIUsageService, Depends(get_ai_usage_service)]


def _to_response(summary: AIUsageSummary) -> AISettingsResponse:
    controls = summary.controls
    return AISettingsResponse(
        controls=AIControlsResponse(
            monthly_budget_usd=controls.monthly_budget_usd,
            lead_reading=controls.lead_reading,
            follow_ups=controls.follow_ups,
            digest=controls.digest,
        ),
        month_start=summary.month_start,
        spent_usd=summary.spent_usd,
        budget_used_percent=summary.budget_used_percent,
        budget_reached=summary.budget_reached,
        provider=summary.provider,
        main_model=summary.main_model,
        light_model=summary.light_model,
        prices_configured=summary.prices_configured,
        by_task=[
            TaskUsageResponse(
                task=usage.task,
                runs=usage.runs,
                failed=usage.failed,
                tokens_in=usage.tokens_in,
                tokens_out=usage.tokens_out,
                cost_usd=usage.cost_usd,
                model_tier="light" if usage.light_model else "main",
            )
            for usage in summary.by_task
        ],
    )


@router.get(
    "",
    response_model=AISettingsResponse,
    summary="AI controls and this month's AI usage",
    responses=READ,
)
async def read_ai_settings(_principal: Reader, service: Service) -> AISettingsResponse:
    """Read the AI controls and usage."""
    return _to_response(await service.summary())


@router.put(
    "",
    response_model=AISettingsResponse,
    summary="Change the monthly AI budget and where AI is used",
    description="Each kind of reply chooses AI or a built-in message in the reply policy "
    "(`PUT /ai/reply-policies/{bucket}` with `draft_source`).",
    responses=WRITE,
)
async def update_ai_settings(
    payload: AIControlsBody, _principal: Admin, service: Service
) -> AISettingsResponse:
    """Save the AI controls."""
    return _to_response(
        await service.update_controls(AIControls.model_validate(payload.model_dump()))
    )
