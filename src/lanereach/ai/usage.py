"""This month's AI usage per task, and the company's AI controls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.ai.controls import AIControls, load_ai_controls, month_start
from lanereach.ai.llm.tiers import light_model_configured, uses_light_model
from lanereach.ai.runs.models import AIRun, AIRunStatus
from lanereach.core.clock import Clock
from lanereach.core.config import Settings
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.tenants.models import Tenant


@dataclass(frozen=True)
class TaskUsage:
    """One AI task's runs this month."""

    task: str
    runs: int
    failed: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    light_model: bool


@dataclass(frozen=True)
class AIUsageSummary:
    """The month's AI spend against the budget, by task."""

    controls: AIControls
    month_start: datetime
    spent_usd: float
    by_task: list[TaskUsage]
    provider: str
    main_model: str
    light_model: str | None
    prices_configured: bool

    @property
    def budget_used_percent(self) -> float | None:
        """Share of the budget spent (None without a budget)."""
        budget = self.controls.monthly_budget_usd
        if budget is None:
            return None
        if budget == 0:
            return 100.0
        return round(min(self.spent_usd / budget * 100, 999.0), 1)

    @property
    def budget_reached(self) -> bool:
        """Whether AI calls are paused for the rest of the month."""
        budget = self.controls.monthly_budget_usd
        return budget is not None and self.spent_usd >= budget


class AIUsageService:
    """Reads the month's AI usage and changes the AI controls.

    Args:
        session: Tenant session.
        clock: Time source (the month).
        settings: Which models are configured and their prices.
    """

    def __init__(self, session: AsyncSession, clock: Clock, settings: Settings) -> None:
        self._session = session
        self._clock = clock
        self._settings = settings

    async def summary(self) -> AIUsageSummary:
        """This month's usage and the controls.

        Returns:
            The summary, tasks by cost (highest first).
        """
        start = month_start(self._clock.now())
        rows = (
            await self._session.execute(
                select(
                    AIRun.task,
                    func.count(),
                    func.sum(case((AIRun.status == AIRunStatus.FAILED, 1), else_=0)),
                    func.coalesce(func.sum(AIRun.tokens_in), 0),
                    func.coalesce(func.sum(AIRun.tokens_out), 0),
                    func.coalesce(func.sum(AIRun.estimated_cost), 0),
                )
                .where(AIRun.created_at >= start)
                .group_by(AIRun.task)
            )
        ).all()
        by_task = sorted(
            (
                TaskUsage(
                    task=task,
                    runs=int(runs),
                    failed=int(failed or 0),
                    tokens_in=int(tokens_in),
                    tokens_out=int(tokens_out),
                    cost_usd=round(float(cost), 6),
                    light_model=uses_light_model(self._settings, task),
                )
                for task, runs, failed, tokens_in, tokens_out, cost in rows
            ),
            key=lambda usage: (-usage.cost_usd, -usage.runs, usage.task),
        )
        settings = self._settings
        if settings.llm_provider == "azure_openai":
            main_model, light_model = (
                settings.azure_openai_deployment,
                settings.azure_openai_deployment_light,
            )
        elif settings.llm_provider == "anthropic":
            main_model, light_model = settings.llm_model, settings.llm_model_light
        else:
            main_model, light_model = "mock", ""
        return AIUsageSummary(
            controls=await load_ai_controls(self._session),
            month_start=start,
            spent_usd=round(sum(usage.cost_usd for usage in by_task), 6),
            by_task=by_task,
            provider=settings.llm_provider,
            main_model=main_model,
            light_model=light_model if light_model_configured(settings) else None,
            prices_configured=settings.llm_price_input_per_mtok > 0
            or settings.llm_price_output_per_mtok > 0,
        )

    async def update_controls(self, controls: AIControls) -> AIUsageSummary:
        """Save new controls; a lower budget applies from the next AI call.

        Returns:
            The summary with the new controls.
        """
        tenant = await self._session.get(Tenant, require_session_tenant_id(self._session))
        assert tenant is not None  # noqa: S101 - the session's tenant exists (RLS shows its row)
        tenant.ai_controls = controls.model_dump(mode="json")
        tenant.updated_at = self._clock.now()
        await self._session.flush()
        return await self.summary()
