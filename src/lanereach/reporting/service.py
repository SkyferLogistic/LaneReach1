"""Dashboard numbers: what went out, what came back, and what is waiting.

Days are UTC calendar days: offices sit in different time zones, and one clock for the whole
chart keeps the series comparable. Mailbox capacity is the exception: it is counted on each
office's own day, because that is the day the daily caps apply to.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import IntEnum

from sqlalchemy import Date, SQLColumnExpression, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.ai.review.models import ReviewItem, ReviewStatus
from lanereach.automation.enrollments.models import Enrollment, EnrollmentStatus
from lanereach.automation.guardrails.mailbox_capacity import office_day
from lanereach.automation.workflows.models import Workflow, WorkflowStatus
from lanereach.domain.countries.models import Country
from lanereach.domain.leads.models import Lead, LeadStatus
from lanereach.domain.messages.models import Message, MessageDirection, MessageKind
from lanereach.domain.offices.models import Office
from lanereach.domain.senders.models import MailboxDailyUsage, Sender, ServiceLine
from lanereach.domain.templates.models import Template, TemplateSource, TemplateStatus


class DashboardPeriod(IntEnum):
    """Periods the dashboard offers (days)."""

    WEEK = 7
    MONTH = 30
    QUARTER = 90


PERIODS = tuple(int(period) for period in DashboardPeriod)
TOP_AGENTS = 6


@dataclass(frozen=True, slots=True)
class PeriodTotals:
    """Totals over one period."""

    sent: int
    replies: int
    leads: int
    bounces: int
    tracked: int = 0
    opened: int = 0
    clicked: int = 0

    @property
    def reply_rate(self) -> float:
        """Replies per email sent (0 when nothing was sent)."""
        return round(self.replies / self.sent, 4) if self.sent else 0.0

    @property
    def open_rate(self) -> float:
        """Human opens per tracked email sent in the period."""
        return round(self.opened / self.tracked, 4) if self.tracked else 0.0

    @property
    def click_rate(self) -> float:
        """Human clicks per tracked email sent in the period."""
        return round(self.clicked / self.tracked, 4) if self.tracked else 0.0


@dataclass(frozen=True, slots=True)
class DailyPoint:
    """One day of the chart."""

    day: date
    sent: int
    replies: int
    leads: int


@dataclass(frozen=True, slots=True)
class LineCapacity:
    """Today's sending against the daily caps of one service line's agents."""

    service_line: ServiceLine
    agents: int
    sent_today: int
    daily_cap: int


@dataclass(frozen=True, slots=True)
class AgentActivity:
    """Emails an agent sent in the period."""

    sender_id: uuid.UUID
    name: str
    service_line: ServiceLine
    sent: int


@dataclass(frozen=True, slots=True)
class PipelineCounts:
    """Work in progress right now."""

    running: int
    waiting: int
    paused: int
    pending_reviews: int
    new_leads: int
    published_workflows: int


@dataclass(frozen=True, slots=True)
class ScriptCoverage:
    """How much of the countries table the script library covers."""

    active_versions: int
    ai_drafts: int
    errors: int
    languages_covered: list[str]
    languages_needed: list[str]
    held_countries: list[str]


@dataclass(frozen=True, slots=True)
class DashboardStats:
    """Everything the dashboard shows."""

    days: int
    start: date
    end: date
    totals: PeriodTotals
    previous: PeriodTotals
    series: list[DailyPoint]
    reply_buckets: dict[str, int]
    capacity: list[LineCapacity]
    top_agents: list[AgentActivity]
    pipeline: PipelineCounts
    scripts: ScriptCoverage
    generated_at: datetime


def _utc_day(column: SQLColumnExpression[datetime]) -> SQLColumnExpression[date]:
    return cast(func.timezone("UTC", column), Date)


def _period_bounds(now: datetime, days: int) -> tuple[date, date]:
    end = now.astimezone(UTC).date()
    return end - timedelta(days=days - 1), end


async def _daily_counts(
    session: AsyncSession,
    column: SQLColumnExpression[datetime],
    since: date,
    until: date,
    *conditions: SQLColumnExpression[bool],
) -> dict[date, int]:
    day = _utc_day(column)
    rows = await session.execute(
        select(day, func.count()).where(day >= since, day <= until, *conditions).group_by(day)
    )
    return {row[0]: int(row[1]) for row in rows}


_SENT = (Message.direction == MessageDirection.OUTBOUND,)
_REPLIES = (Message.direction == MessageDirection.INBOUND, Message.kind == MessageKind.REPLY)
_BOUNCES = (Message.direction == MessageDirection.INBOUND, Message.kind == MessageKind.BOUNCE)


async def _totals(session: AsyncSession, since: date, until: date) -> PeriodTotals:
    async def count(
        column: SQLColumnExpression[datetime], *conditions: SQLColumnExpression[bool]
    ) -> int:
        day = _utc_day(column)
        value = await session.scalar(
            select(func.count()).where(day >= since, day <= until, *conditions)
        )
        return int(value or 0)

    return PeriodTotals(
        sent=await count(Message.occurred_at, *_SENT),
        replies=await count(Message.occurred_at, *_REPLIES),
        leads=await count(Lead.created_at),
        bounces=await count(Message.occurred_at, *_BOUNCES),
        tracked=await count(Message.occurred_at, *_SENT, Message.tracking_id.is_not(None)),
        opened=await count(Message.occurred_at, *_SENT, Message.first_opened_at.is_not(None)),
        clicked=await count(Message.occurred_at, *_SENT, Message.first_clicked_at.is_not(None)),
    )


async def _reply_buckets(session: AsyncSession, since: date, until: date) -> dict[str, int]:
    bucket = Message.processing_result["classification"]["bucket"].astext
    day = _utc_day(Message.occurred_at)
    rows = await session.execute(
        select(bucket, func.count())
        .where(
            Message.direction == MessageDirection.INBOUND,
            day >= since,
            day <= until,
            bucket.is_not(None),
        )
        .group_by(bucket)
    )
    counts = {str(row[0]): int(row[1]) for row in rows}
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


async def _capacity(session: AsyncSession, now: datetime) -> list[LineCapacity]:
    agents = (
        await session.scalars(
            select(Sender).where(Sender.is_active.is_(True), Sender.flagged_at.is_(None))
        )
    ).all()
    offices = {office.id: office for office in (await session.scalars(select(Office))).all()}
    usage = {
        (row.sender_id, row.day): row.sent_count
        for row in (
            await session.scalars(
                select(MailboxDailyUsage).where(
                    MailboxDailyUsage.day >= now.date() - timedelta(days=1),
                    MailboxDailyUsage.day <= now.date() + timedelta(days=1),
                )
            )
        ).all()
    }
    agent_count: dict[ServiceLine, int] = defaultdict(int)
    sent_today: dict[ServiceLine, int] = defaultdict(int)
    daily_cap: dict[ServiceLine, int] = defaultdict(int)
    for agent in agents:
        office = offices.get(agent.office_id)
        today = office_day(now, office.timezone) if office else now.date()
        agent_count[agent.service_line] += 1
        sent_today[agent.service_line] += usage.get((agent.id, today), 0)
        daily_cap[agent.service_line] += agent.daily_cap
    return [
        LineCapacity(line, agent_count[line], sent_today[line], daily_cap[line])
        for line in ServiceLine
        if line in agent_count
    ]


async def _top_agents(session: AsyncSession, since: date, until: date) -> list[AgentActivity]:
    day = _utc_day(Message.occurred_at)
    sent = func.count().label("sent")
    rows = await session.execute(
        select(Sender.id, Sender.display_name, Sender.service_line, sent)
        .join(Message, Message.sender_id == Sender.id)
        .where(*_SENT, day >= since, day <= until)
        .group_by(Sender.id, Sender.display_name, Sender.service_line)
        .order_by(sent.desc(), Sender.display_name)
        .limit(TOP_AGENTS)
    )
    return [AgentActivity(row[0], row[1], row[2], int(row[3])) for row in rows]


async def _pipeline(session: AsyncSession) -> PipelineCounts:
    by_status = {
        row[0]: int(row[1])
        for row in await session.execute(
            select(Enrollment.status, func.count())
            .where(
                Enrollment.status.in_(
                    [EnrollmentStatus.ACTIVE, EnrollmentStatus.WAITING, EnrollmentStatus.PAUSED]
                )
            )
            .group_by(Enrollment.status)
        )
    }

    async def count(condition: SQLColumnExpression[bool]) -> int:
        # The FROM clause is taken from the condition's table.
        return int(await session.scalar(select(func.count()).where(condition)) or 0)

    return PipelineCounts(
        running=by_status.get(EnrollmentStatus.ACTIVE, 0),
        waiting=by_status.get(EnrollmentStatus.WAITING, 0),
        paused=by_status.get(EnrollmentStatus.PAUSED, 0),
        pending_reviews=await count(ReviewItem.status == ReviewStatus.PENDING),
        new_leads=await count(Lead.status == LeadStatus.NEW),
        published_workflows=await count(Workflow.status == WorkflowStatus.PUBLISHED),
    )


async def _script_coverage(session: AsyncSession) -> ScriptCoverage:
    library = (
        await session.scalars(select(Template).where(Template.source == TemplateSource.SHAREPOINT))
    ).all()
    active = {row.language for row in library if row.status is TemplateStatus.ACTIVE}
    countries = (await session.scalars(select(Country).where(Country.is_enabled.is_(True)))).all()
    needed = sorted({country.language for country in countries})
    held = sorted(
        country.country
        for country in countries
        if not country.allow_language_fallback and country.language not in active
    )
    return ScriptCoverage(
        active_versions=sum(row.status is TemplateStatus.ACTIVE for row in library),
        ai_drafts=sum(row.status is TemplateStatus.AI_DRAFT for row in library),
        errors=sum(row.status is TemplateStatus.ERROR for row in library),
        languages_covered=[language for language in needed if language in active],
        languages_needed=needed,
        held_countries=held,
    )


async def build_dashboard_stats(session: AsyncSession, now: datetime, days: int) -> DashboardStats:
    """Collect the dashboard numbers for the last `days` days (today included).

    Args:
        session: Tenant session.
        now: Current time (from the `Clock`).
        days: Period length: 7, 30 or 90.

    Returns:
        The stats, with the previous period of the same length for comparison.

    Raises:
        ValueError: For an unsupported period.
    """
    if days not in PERIODS:
        raise ValueError(f"days must be one of {PERIODS}")
    start, end = _period_bounds(now, days)
    previous_start, previous_end = start - timedelta(days=days), start - timedelta(days=1)
    sent = await _daily_counts(session, Message.occurred_at, start, end, *_SENT)
    replies = await _daily_counts(session, Message.occurred_at, start, end, *_REPLIES)
    leads = await _daily_counts(session, Lead.created_at, start, end)
    series = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        series.append(DailyPoint(day, sent.get(day, 0), replies.get(day, 0), leads.get(day, 0)))
    return DashboardStats(
        days=days,
        start=start,
        end=end,
        totals=await _totals(session, start, end),
        previous=await _totals(session, previous_start, previous_end),
        series=series,
        reply_buckets=await _reply_buckets(session, start, end),
        capacity=await _capacity(session, now),
        top_agents=await _top_agents(session, start, end),
        pipeline=await _pipeline(session),
        scripts=await _script_coverage(session),
        generated_at=now,
    )
