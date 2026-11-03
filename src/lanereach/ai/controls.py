"""Per-company AI controls: a monthly AI budget and, per area, AI or rules.

Not every step needs a model. Lead scores work from the shipment facts and reply speed alone,
a quiet deal can get the company's own follow-up message, the weekly digest can be the numbers
without a written summary, and each kind of reply can be answered with a built-in message (see
`DraftSource`). The budget is enforced in the engine: once a month's estimated AI cost reaches
it, no model is called until the next month (or a higher budget), and every step falls back to
its built-in message or rules.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lanereach.ai.llm.client import AIBudgetReachedError
from lanereach.ai.runs.models import AIRun
from lanereach.core.clock import Clock
from lanereach.core.tenancy import open_tenant_session, require_session_tenant_id
from lanereach.tenants.models import Tenant


class AIControls(BaseModel):
    """A company's AI controls (stored on the tenant as JSON).

    Attributes:
        monthly_budget_usd: Estimated AI spend allowed per calendar month (UTC); None = no cap.
        lead_reading: `ai` reads buying intent from each new deal's reply; `rules` scores from
            the shipment facts, reply speed and history only.
        follow_ups: `ai` drafts follow-ups for quiet deals; `built_in` uses the company's
            follow-up message.
        digest: `ai` writes the weekly summary; `rules` shows the numbers with a plain summary.
    """

    model_config = ConfigDict(extra="ignore")

    monthly_budget_usd: float | None = Field(default=None, ge=0, le=1_000_000)
    lead_reading: Literal["ai", "rules"] = "ai"
    follow_ups: Literal["ai", "built_in"] = "ai"
    digest: Literal["ai", "rules"] = "ai"


async def load_ai_controls(session: AsyncSession) -> AIControls:
    """The AI controls of the session's tenant (defaults when none are set)."""
    tenant = await session.get(Tenant, require_session_tenant_id(session))
    return AIControls.model_validate(tenant.ai_controls or {}) if tenant else AIControls()


def month_start(now: datetime) -> datetime:
    """Midnight UTC on the first day of `now`'s month."""
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def month_to_date_spend(session: AsyncSession, now: datetime) -> float:
    """Estimated AI cost of the session's tenant since the start of the month (USD)."""
    total = await session.scalar(
        select(func.coalesce(func.sum(AIRun.estimated_cost), 0)).where(
            AIRun.created_at >= month_start(now)
        )
    )
    return float(total or 0)


class AIBudgetGuard:
    """Refuses AI calls once the tenant's monthly budget is spent.

    Args:
        open_session: Opens a tenant session to read the budget and the month's spend.
        clock: Time source.
    """

    def __init__(
        self,
        open_session: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        clock: Clock,
    ) -> None:
        self._open_session = open_session
        self._clock = clock

    @classmethod
    def for_session(cls, session: AsyncSession, clock: Clock) -> AIBudgetGuard:
        """Guard reading through the caller's session (engine units of work)."""

        @asynccontextmanager
        async def reuse() -> AsyncIterator[AsyncSession]:
            yield session

        return cls(reuse, clock)

    @classmethod
    def for_tenant(
        cls,
        session_factory: async_sessionmaker[AsyncSession],
        tenant_id: uuid.UUID,
        clock: Clock,
    ) -> AIBudgetGuard:
        """Guard opening its own tenant session (requests and background jobs)."""
        return cls(lambda: open_tenant_session(session_factory, tenant_id), clock)

    async def ensure_available(self) -> None:
        """Raise when this month's AI budget is used up.

        Raises:
            AIBudgetReachedError: Spend has reached the budget.
        """
        async with self._open_session() as session:
            controls = await load_ai_controls(session)
            if controls.monthly_budget_usd is None:
                return
            spent = await month_to_date_spend(session, self._clock.now())
        if spent >= controls.monthly_budget_usd:
            raise AIBudgetReachedError(
                f"This month's AI budget (${controls.monthly_budget_usd:,.2f}) is used up; "
                "built-in messages and rules are used until next month or a higher budget."
            )
