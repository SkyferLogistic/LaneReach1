"""The weekly digest: the week's numbers, and a short summary the AI writes from them.

The numbers are computed here (deterministic, Monday to Sunday UTC): quote requests and the lanes
heating up against the previous four weeks, win/loss by lane over 90 days, replies by kind, quotes
sent and decided, deals waiting for a quote and the offices they wait in, quiet deals. The AI
writes the headline and highlights from those numbers only (validated); without a model, or when
its answer fails validation, a rule-written digest is stored instead.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.ai.controls import load_ai_controls
from lanereach.ai.llm.client import LLMError
from lanereach.ai.schemas import DigestHighlight, DigestNarrative
from lanereach.ai.tasks.runtime import AIRuntime
from lanereach.ai.tasks.write_digest import write_digest
from lanereach.core.clock import Clock
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.crm.models import Deal, DealStatus
from lanereach.domain.intelligence.follow_ups import find_quiet_deals
from lanereach.domain.intelligence.models import InsightDigest
from lanereach.domain.messages.models import Message, MessageDirection
from lanereach.domain.offices.models import Office
from lanereach.domain.pricing.places import place_code
from lanereach.domain.quotes.models import Quote, QuoteStatus

logger = structlog.get_logger(__name__)

HISTORY_WEEKS = 4
WIN_LOSS_DAYS = 90
WAITING_DAYS = 2
TOP = 5


def plural(count: int, word: str, many: str | None = None) -> str:
    """`1 deal`, `3 deals`."""
    return f"{count} {word if count == 1 else many or word + 's'}"


def week_of(day: date) -> date:
    """The Monday of a day's week."""
    return day - timedelta(days=day.weekday())


def last_completed_week(today: date) -> date:
    """The Monday of the previous week."""
    return week_of(today) - timedelta(days=7)


def lane_label(origin: str | None, destination: str | None) -> str | None:
    """`INMAA → AEJEA` (codes when known, else the names), or None without a lane."""
    if not origin or not destination:
        return None

    def part(text: str) -> str:
        return place_code(text) or " ".join(text.split())[:40]

    return f"{part(origin)} → {part(destination)}"


def _start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


async def collect_week_stats(
    session: AsyncSession, week_start: date, now: datetime, *, quiet_days: int
) -> dict[str, Any]:
    """The numbers of one week (Monday `week_start` to Sunday)."""
    start, end = _start(week_start), _start(week_start + timedelta(days=7))
    history_start = _start(week_start - timedelta(days=7 * HISTORY_WEEKS))

    created = (
        await session.execute(
            select(Deal.origin, Deal.destination, Deal.created_at).where(
                Deal.created_at >= history_start, Deal.created_at < end
            )
        )
    ).all()
    this_week: Counter[str] = Counter()
    before: Counter[str] = Counter()
    requests_this_week = requests_before = 0
    for origin, destination, created_at in created:
        lane = lane_label(origin, destination)
        if created_at >= start:
            requests_this_week += 1
            if lane:
                this_week[lane] += 1
        else:
            requests_before += 1
            if lane:
                before[lane] += 1
    rising = sorted(
        (
            (lane, count, round(before[lane] / HISTORY_WEEKS, 1))
            for lane, count in this_week.items()
            if count >= 2 and count > before[lane] / HISTORY_WEEKS
        ),
        key=lambda item: (-(item[1] - item[2]), item[0]),
    )[:TOP]
    heating = [
        {"lane": lane, "this_week": count, "previous_4_week_average": average}
        for lane, count, average in rising
    ]

    since = now - timedelta(days=WIN_LOSS_DAYS)
    decided = (
        await session.execute(
            select(Deal.origin, Deal.destination, Deal.status, Deal.lost_reason).where(
                Deal.status.in_([DealStatus.WON, DealStatus.LOST]),
                func.coalesce(Deal.won_at, Deal.lost_at) >= since,
            )
        )
    ).all()
    by_lane: dict[str, Counter[str]] = defaultdict(Counter)
    reasons: Counter[str] = Counter()
    for origin, destination, status, lost_reason in decided:
        lane = lane_label(origin, destination) or "No lane"
        by_lane[lane][status.value] += 1
        if status is DealStatus.LOST and lost_reason:
            reasons[" ".join(lost_reason.split())[:80]] += 1
    busiest = sorted(
        by_lane.items(), key=lambda item: (-(item[1]["won"] + item[1]["lost"]), item[0])
    )
    win_loss = [
        {
            "lane": lane,
            "won": counts["won"],
            "lost": counts["lost"],
            "win_rate_percent": round(100 * counts["won"] / (counts["won"] + counts["lost"])),
        }
        for lane, counts in busiest[:TOP]
    ]

    bucket = Message.processing_result["classification"]["bucket"].astext
    replies = dict(
        (str(kind), int(count))
        for kind, count in (
            await session.execute(
                select(bucket, func.count())
                .where(
                    Message.direction == MessageDirection.INBOUND,
                    Message.occurred_at >= start,
                    Message.occurred_at < end,
                    bucket.is_not(None),
                )
                .group_by(bucket)
            )
        ).all()
    )

    sent = await session.scalar(
        select(func.count()).where(Quote.sent_at >= start, Quote.sent_at < end)
    )
    decisions = dict(
        (status.value, int(count))
        for status, count in (
            await session.execute(
                select(Quote.status, func.count())
                .where(Quote.decided_at >= start, Quote.decided_at < end)
                .group_by(Quote.status)
            )
        ).all()
    )

    quoted = select(Quote.deal_id)
    waiting_rows = (
        await session.execute(
            select(Deal.created_at, Office.name)
            .outerjoin(Office, Office.id == Deal.office_id)
            .where(
                Deal.status == DealStatus.OPEN,
                Deal.lead_id.is_not(None),
                Deal.id.not_in(quoted),
                Deal.created_at <= now - timedelta(days=WAITING_DAYS),
            )
        )
    ).all()
    waiting_days = [(now - created_at).days for created_at, _office in waiting_rows]
    per_office: dict[str, list[int]] = defaultdict(list)
    for (_created_at, office), days in zip(waiting_rows, waiting_days, strict=True):
        per_office[office or "No office"].append(days)
    slowest = sorted(per_office.items(), key=lambda item: (-len(item[1]), item[0]))
    slow_offices = [
        {
            "office": office,
            "deals_waiting": len(days),
            "average_days_waiting": round(sum(days) / len(days), 1),
        }
        for office, days in slowest[:TOP]
    ]

    return {
        "week": {
            "start": week_start.isoformat(),
            "end": (week_start + timedelta(days=6)).isoformat(),
        },
        # The windows are part of the numbers, so the digest may mention them.
        "history_weeks": HISTORY_WEEKS,
        "win_loss_days": WIN_LOSS_DAYS,
        "waiting_threshold_days": WAITING_DAYS,
        "quote_requests": {
            "this_week": requests_this_week,
            "previous_4_week_average": round(requests_before / HISTORY_WEEKS, 1),
        },
        "lanes_heating_up": heating,
        "win_loss_by_lane": win_loss,
        "lost_reasons": [
            {"reason": reason, "deals": count} for reason, count in reasons.most_common(3)
        ],
        "replies": {"total": sum(replies.values()), "by_kind": replies},
        "quotes": {
            "sent": int(sent or 0),
            "accepted": decisions.get(QuoteStatus.ACCEPTED.value, 0),
            "rejected": decisions.get(QuoteStatus.REJECTED.value, 0),
        },
        "waiting_for_quote": {
            "deals": len(waiting_days),
            "oldest_days": max(waiting_days, default=0),
        },
        "slow_offices": slow_offices,
        "quiet_deals": len(await find_quiet_deals(session, now, quiet_days)),
    }


def rule_digest(stats: dict[str, Any]) -> DigestNarrative:
    """A digest written by rules from the same numbers (no AI)."""
    highlights: list[DigestHighlight] = []
    requests = stats["quote_requests"]
    for lane in stats["lanes_heating_up"][:2]:
        highlights.append(
            DigestHighlight(
                kind="lanes",
                title=f"{lane['lane']} is heating up"[:90],
                detail=f"{plural(lane['this_week'], 'quote request')} this week, against "
                f"{lane['previous_4_week_average']} a week before. "
                "Make sure the lane is in a rate sheet.",
            )
        )
    for row in stats["win_loss_by_lane"][:1]:
        highlights.append(
            DigestHighlight(
                kind="win_loss",
                title=f"{row['lane']}: {row['win_rate_percent']}% won"[:90],
                detail=f"{row['won']} won and {row['lost']} lost in the last 90 days.",
            )
        )
    waiting = stats["waiting_for_quote"]
    if waiting["deals"]:
        highlights.append(
            DigestHighlight(
                kind="speed",
                title=f"{plural(waiting['deals'], 'deal')} waiting for a quote",
                detail=f"The oldest has waited {waiting['oldest_days']} days. Price these first.",
            )
        )
    quotes = stats["quotes"]
    if quotes["sent"] or quotes["accepted"] or quotes["rejected"]:
        highlights.append(
            DigestHighlight(
                kind="quotes",
                title=f"{plural(quotes['sent'], 'quote')} sent",
                detail=f"{quotes['accepted']} accepted and {quotes['rejected']} rejected "
                "this week.",
            )
        )
    if stats["replies"]["total"]:
        highlights.append(
            DigestHighlight(
                kind="replies",
                title=f"{plural(stats['replies']['total'], 'reply', 'replies')} read",
                detail="Every reply was sorted by kind; rate requests went to pricing.",
            )
        )
    if stats["lanes_heating_up"]:
        top = stats["lanes_heating_up"][0]
        headline = (
            f"{top['lane']} is heating up: {plural(top['this_week'], 'quote request')} this week."
        )
    elif requests["this_week"]:
        headline = f"{plural(requests['this_week'], 'quote request')} came in this week."
    else:
        headline = "A quiet week: no new quote requests."
    summary = (
        f"{plural(requests['this_week'], 'quote request')} this week, against "
        f"{requests['previous_4_week_average']} "
        f"a week over the previous four weeks. Open deals gone quiet: {stats['quiet_deals']}."
    )
    return DigestNarrative(headline=headline[:140], summary=summary, highlights=highlights[:6])


class DigestService:
    """Builds and stores weekly digests.

    Args:
        session: Tenant session.
        clock: Time source.
        runtime: AI runtime (None: rule-written digests).
        quiet_days: Days without activity that make a deal quiet.
    """

    def __init__(
        self, session: AsyncSession, clock: Clock, runtime: AIRuntime | None, *, quiet_days: int
    ) -> None:
        self._session = session
        self._clock = clock
        self._runtime = runtime
        self._quiet_days = quiet_days

    async def list_digests(self, limit: int = 12) -> list[InsightDigest]:
        """Latest digests first."""
        return list(
            (
                await self._session.scalars(
                    select(InsightDigest).order_by(InsightDigest.week_start.desc()).limit(limit)
                )
            ).all()
        )

    async def generate(self, week_start: date) -> InsightDigest:
        """Build (or rebuild) the digest of a week."""
        week_start = week_of(week_start)
        now = self._clock.now()
        stats = await collect_week_stats(
            self._session, week_start, now, quiet_days=self._quiet_days
        )
        narrative, written_by, run_id = rule_digest(stats), "rules", None
        # A company that chose "numbers only" gets the rule-written summary, no AI call.
        if self._runtime is not None and (await load_ai_controls(self._session)).digest == "ai":
            try:
                result = await write_digest(self._runtime, stats, input_ref=f"digest:{week_start}")
            except LLMError as exc:
                logger.warning(
                    "write_digest_failed", week=str(week_start), error=type(exc).__name__
                )
            else:
                narrative, written_by, run_id = result.output, "ai", result.ai_run_id
        digest = await self._session.scalar(
            select(InsightDigest).where(InsightDigest.week_start == week_start)
        )
        if digest is None:
            digest = InsightDigest(
                tenant_id=require_session_tenant_id(self._session),
                week_start=week_start,
                created_at=now,
            )
            self._session.add(digest)
        digest.week_end = week_start + timedelta(days=6)
        digest.stats = stats
        digest.headline = narrative.headline
        digest.summary = narrative.summary
        digest.highlights = [item.model_dump() for item in narrative.highlights]
        digest.written_by = written_by
        digest.ai_run_id = run_id
        digest.updated_at = now
        await self._session.flush()
        return digest

    async def ensure_last_week(self) -> InsightDigest | None:
        """The previous week's digest, built when missing (weekly job; None when it existed)."""
        week = last_completed_week(self._clock.now().date())
        existing = await self._session.scalar(
            select(InsightDigest.id).where(InsightDigest.week_start == week)
        )
        if existing is not None:
            return None
        return await self.generate(week)
