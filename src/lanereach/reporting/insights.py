"""Insights: outreach funnel, results per country, reply heatmap and the activity feed.

Periods are UTC days, like the dashboard stats. The reply heatmap is the exception: it is read on
the sending office's clock, because "replies come in on Tuesday mornings" is only useful in the
office's own time (messages without an office fall back to UTC).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy import Date, Integer, SQLColumnExpression, cast, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.domain.contacts.models import Contact
from lanereach.domain.leads.models import Lead, LeadStatus
from lanereach.domain.messages.models import Message, MessageDirection, MessageKind
from lanereach.domain.offices.models import Office
from lanereach.domain.senders.models import Sender, ServiceLine
from lanereach.reporting.service import PERIODS

# Replies that show interest in doing business (the rest are protective or neutral).
POSITIVE_BUCKETS = (
    "interested",
    "meeting_request",
    "rate_query",
    "service_query",
    "partnership_query",
    "referral",
)
MAX_ACTIVITY = 50

ActivityKind = Literal["sent", "reply", "bounce", "lead"]


@dataclass(frozen=True, slots=True)
class FunnelCounts:
    """Distinct contacts at each stage in the period."""

    contacted: int
    replied: int
    interested: int
    leads: int
    won: int


@dataclass(frozen=True, slots=True)
class CountryResult:
    """Outreach to one destination country in the period."""

    country: str
    sent: int
    replies: int
    leads: int

    @property
    def reply_rate(self) -> float:
        """Replies per email sent."""
        return round(self.replies / self.sent, 4) if self.sent else 0.0


@dataclass(frozen=True, slots=True)
class HeatCell:
    """Replies received in one weekday/hour slot (1 = Monday, office local time)."""

    weekday: int
    hour: int
    replies: int


@dataclass(frozen=True, slots=True)
class Insights:
    """Everything the insight cards show."""

    days: int
    funnel: FunnelCounts
    countries: list[CountryResult]
    heatmap: list[HeatCell]
    best_slot: HeatCell | None


@dataclass(frozen=True, slots=True)
class ActivityItem:
    """One thing that happened: an email out, a reply or bounce in, a new lead."""

    id: uuid.UUID
    kind: ActivityKind
    occurred_at: datetime
    contact_id: uuid.UUID | None
    contact_name: str | None
    contact_email: str | None
    country: str | None
    title: str
    bucket: str | None = None
    agent_name: str | None = None
    service_line: ServiceLine | None = None


def _utc_day(column: SQLColumnExpression[datetime]) -> SQLColumnExpression[date]:
    return cast(func.timezone("UTC", column), Date)


def _bounds(now: datetime, days: int) -> tuple[date, date]:
    if days not in PERIODS:
        raise ValueError(f"days must be one of {PERIODS}")
    end = now.astimezone(UTC).date()
    return end - timedelta(days=days - 1), end


_BUCKET = Message.processing_result["classification"]["bucket"].astext


async def _funnel(session: AsyncSession, since: date, until: date) -> FunnelCounts:
    day = _utc_day(Message.occurred_at)
    in_period = (day >= since, day <= until)

    async def distinct_contacts(*conditions: SQLColumnExpression[bool]) -> int:
        value = await session.scalar(
            select(func.count(func.distinct(Message.contact_id))).where(*in_period, *conditions)
        )
        return int(value or 0)

    lead_day = _utc_day(Lead.created_at)
    leads = await session.scalar(
        select(func.count(func.distinct(Lead.contact_id))).where(
            lead_day >= since, lead_day <= until
        )
    )
    won = await session.scalar(
        select(func.count(func.distinct(Lead.contact_id))).where(
            lead_day >= since, lead_day <= until, Lead.status == LeadStatus.WON
        )
    )
    return FunnelCounts(
        contacted=await distinct_contacts(Message.direction == MessageDirection.OUTBOUND),
        replied=await distinct_contacts(
            Message.direction == MessageDirection.INBOUND, Message.kind == MessageKind.REPLY
        ),
        interested=await distinct_contacts(
            Message.direction == MessageDirection.INBOUND, _BUCKET.in_(POSITIVE_BUCKETS)
        ),
        leads=int(leads or 0),
        won=int(won or 0),
    )


async def _countries(session: AsyncSession, since: date, until: date) -> list[CountryResult]:
    day = _utc_day(Message.occurred_at)
    rows = await session.execute(
        select(
            Contact.country,
            func.count().filter(Message.direction == MessageDirection.OUTBOUND),
            func.count().filter(
                Message.direction == MessageDirection.INBOUND, Message.kind == MessageKind.REPLY
            ),
        )
        .select_from(Message)
        .join(Contact, Contact.id == Message.contact_id)
        .where(day >= since, day <= until, Contact.country.is_not(None))
        .group_by(Contact.country)
    )
    counts: dict[str, list[int]] = {str(row[0]): [int(row[1]), int(row[2]), 0] for row in rows}
    lead_day = _utc_day(Lead.created_at)
    for country, leads in await session.execute(
        select(Contact.country, func.count())
        .select_from(Lead)
        .join(Contact, Contact.id == Lead.contact_id)
        .where(lead_day >= since, lead_day <= until, Contact.country.is_not(None))
        .group_by(Contact.country)
    ):
        counts.setdefault(str(country), [0, 0, 0])[2] = int(leads)
    results = [CountryResult(code, *values) for code, values in counts.items()]
    return sorted(results, key=lambda item: (-item.sent, -item.replies, item.country))


async def _heatmap(session: AsyncSession, since: date, until: date) -> list[HeatCell]:
    local = func.timezone(func.coalesce(Office.timezone, "UTC"), Message.occurred_at)
    weekday = cast(extract("isodow", local), Integer)
    hour = cast(extract("hour", local), Integer)
    day = _utc_day(Message.occurred_at)
    rows = await session.execute(
        select(weekday, hour, func.count())
        .select_from(Message)
        .outerjoin(Office, Office.id == Message.office_id)
        .where(
            Message.direction == MessageDirection.INBOUND,
            Message.kind == MessageKind.REPLY,
            day >= since,
            day <= until,
        )
        .group_by(weekday, hour)
    )
    return sorted(
        (HeatCell(int(row[0]), int(row[1]), int(row[2])) for row in rows),
        key=lambda cell: (cell.weekday, cell.hour),
    )


async def build_insights(session: AsyncSession, now: datetime, days: int) -> Insights:
    """Collect the funnel, country results and reply heatmap for the last `days` days.

    Args:
        session: Tenant session.
        now: Current time (from the `Clock`).
        days: 7, 30 or 90.

    Returns:
        The insights.

    Raises:
        ValueError: For an unsupported period.
    """
    since, until = _bounds(now, days)
    heatmap = await _heatmap(session, since, until)
    best = max(heatmap, key=lambda cell: cell.replies, default=None)
    return Insights(
        days=days,
        funnel=await _funnel(session, since, until),
        countries=await _countries(session, since, until),
        heatmap=heatmap,
        best_slot=best,
    )


async def list_recent_activity(session: AsyncSession, limit: int = 20) -> list[ActivityItem]:
    """The latest emails out, replies and bounces in, and new leads, newest first.

    Args:
        session: Tenant session.
        limit: How many items (at most `MAX_ACTIVITY`).

    Returns:
        The items.
    """
    limit = max(1, min(limit, MAX_ACTIVITY))
    items: list[ActivityItem] = []
    messages = await session.execute(
        select(Message, Contact, Sender)
        .outerjoin(Contact, Contact.id == Message.contact_id)
        .outerjoin(Sender, Sender.id == Message.sender_id)
        .where(Message.kind != MessageKind.COMPLAINT)
        .order_by(Message.occurred_at.desc())
        .limit(limit)
    )
    for message, contact, sender in messages:
        if message.direction is MessageDirection.OUTBOUND:
            kind: ActivityKind = "sent"
        elif message.kind is MessageKind.BOUNCE:
            kind = "bounce"
        else:
            kind = "reply"
        bucket = ((message.processing_result or {}).get("classification") or {}).get("bucket")
        items.append(
            ActivityItem(
                id=message.id,
                kind=kind,
                occurred_at=message.occurred_at,
                contact_id=message.contact_id,
                contact_name=contact.display_name if contact else None,
                contact_email=contact.email if contact else None,
                country=contact.country if contact else None,
                title=message.subject,
                bucket=bucket if kind == "reply" else None,
                agent_name=sender.display_name if sender else None,
                service_line=sender.service_line if sender else None,
            )
        )
    leads = await session.execute(
        select(Lead, Contact)
        .outerjoin(Contact, Contact.id == Lead.contact_id)
        .order_by(Lead.created_at.desc())
        .limit(limit)
    )
    for lead, contact in leads:
        items.append(
            ActivityItem(
                id=lead.id,
                kind="lead",
                occurred_at=lead.created_at,
                contact_id=lead.contact_id,
                contact_name=contact.display_name if contact else None,
                contact_email=contact.email if contact else None,
                country=contact.country if contact else None,
                title=lead.summary or "New pricing lead",
            )
        )
    items.sort(key=lambda item: item.occurred_at, reverse=True)
    return items[:limit]
