"""Dashboard endpoint: sending, replies, leads, capacity and script coverage."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field, computed_field

from lanereach.auth.dependencies import TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ClockDependency
from lanereach.core.openapi import READ
from lanereach.core.schemas import ResponseModel
from lanereach.domain.senders.models import ServiceLine
from lanereach.reporting.insights import MAX_ACTIVITY, build_insights, list_recent_activity
from lanereach.reporting.service import DashboardPeriod, build_dashboard_stats

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]


class PeriodTotalsResponse(ResponseModel):
    """Totals over one period."""

    sent: int
    replies: int
    leads: int
    bounces: int
    reply_rate: float
    tracked: int = Field(default=0, description="Emails sent with open/click tracking.")
    opened: int = Field(default=0, description="Tracked emails opened by a person.")
    clicked: int = 0
    open_rate: float = 0.0
    click_rate: float = 0.0


class DailyPointResponse(ResponseModel):
    """One day of the chart (UTC)."""

    day: date
    sent: int
    replies: int
    leads: int


class LineCapacityResponse(ResponseModel):
    """Today's emails against the caps of one service line's active agents."""

    service_line: ServiceLine
    agents: int
    sent_today: int
    daily_cap: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def used(self) -> float:
        """Share of the cap used today (0 to 1)."""
        return round(min(self.sent_today / self.daily_cap, 1.0), 4) if self.daily_cap else 0.0


class AgentActivityResponse(ResponseModel):
    """Emails an agent sent in the period."""

    sender_id: uuid.UUID
    name: str
    service_line: ServiceLine
    sent: int


class PipelineResponse(ResponseModel):
    """Work in progress now."""

    running: int
    waiting: int
    paused: int
    pending_reviews: int
    new_leads: int
    published_workflows: int


class ScriptCoverageResponse(ResponseModel):
    """Script library coverage of the countries table."""

    active_versions: int
    ai_drafts: int
    errors: int
    languages_covered: list[str]
    languages_needed: list[str]
    held_countries: list[str]


class DashboardStatsResponse(ResponseModel):
    """Everything the dashboard shows."""

    days: int
    start: date
    end: date
    totals: PeriodTotalsResponse
    previous: PeriodTotalsResponse
    series: list[DailyPointResponse]
    reply_buckets: dict[str, int]
    capacity: list[LineCapacityResponse]
    top_agents: list[AgentActivityResponse]
    pipeline: PipelineResponse
    scripts: ScriptCoverageResponse
    generated_at: datetime


@router.get(
    "/stats",
    response_model=DashboardStatsResponse,
    summary="Dashboard numbers for the last 7, 30 or 90 days",
    description="Emails sent, replies (with their buckets), leads and bounces per UTC day, the "
    "same totals for the previous period, today's air/ocean capacity, the busiest agents, work "
    "in progress, and which script languages exist or are missing (held countries).",
    responses=READ,
)
async def read_dashboard_stats(
    _principal: Reader,
    session: TenantSession,
    clock: ClockDependency,
    days: Annotated[DashboardPeriod, Query(description="Period length in days.")] = (
        DashboardPeriod.WEEK
    ),
) -> DashboardStatsResponse:
    """Dashboard numbers."""
    stats = await build_dashboard_stats(session, clock.now(), int(days))
    return DashboardStatsResponse.model_validate(stats)


class FunnelResponse(ResponseModel):
    """Distinct contacts at each stage of the funnel in the period."""

    contacted: int
    replied: int
    interested: int
    leads: int
    won: int


class CountryResultResponse(ResponseModel):
    """Outreach to one destination country."""

    country: str
    sent: int
    replies: int
    leads: int
    reply_rate: float


class HeatCellResponse(ResponseModel):
    """Replies in one weekday (1 = Monday) and hour, on the sending office's clock."""

    weekday: int
    hour: int
    replies: int


class InsightsResponse(ResponseModel):
    """Funnel, results per country and when replies arrive."""

    days: int
    funnel: FunnelResponse
    countries: list[CountryResultResponse]
    heatmap: list[HeatCellResponse]
    best_slot: HeatCellResponse | None


class ActivityItemResponse(ResponseModel):
    """One recent event: an email sent, a reply or bounce received, a lead created."""

    id: uuid.UUID
    kind: Literal["sent", "reply", "bounce", "lead"]
    occurred_at: datetime
    contact_id: uuid.UUID | None
    contact_name: str | None
    contact_email: str | None
    country: str | None
    title: str
    bucket: str | None
    agent_name: str | None
    service_line: ServiceLine | None


@router.get(
    "/insights",
    response_model=InsightsResponse,
    summary="Funnel, results per destination country and reply heatmap",
    description="Distinct contacts contacted, replied, interested, turned into leads and won; "
    "emails, replies and leads per destination country (for the lane map); replies per weekday "
    "and hour in the sending office's local time, with the best slot.",
    responses=READ,
)
async def read_dashboard_insights(
    _principal: Reader,
    session: TenantSession,
    clock: ClockDependency,
    days: Annotated[DashboardPeriod, Query(description="Period length in days.")] = (
        DashboardPeriod.MONTH
    ),
) -> InsightsResponse:
    """Insights."""
    insights = await build_insights(session, clock.now(), int(days))
    return InsightsResponse.model_validate(insights)


@router.get(
    "/activity",
    response_model=list[ActivityItemResponse],
    summary="Latest activity: emails sent, replies, bounces and new leads",
    responses=READ,
)
async def read_recent_activity(
    _principal: Reader,
    session: TenantSession,
    limit: Annotated[int, Query(ge=1, le=MAX_ACTIVITY)] = 20,
) -> list[ActivityItemResponse]:
    """Activity feed, newest first."""
    items = await list_recent_activity(session, limit)
    return [ActivityItemResponse.model_validate(item) for item in items]
