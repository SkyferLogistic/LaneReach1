"""How well the AI is doing, from what people did with its work (no extra AI call).

* Replies: how many were sorted by rules, by the AI, or went to a person because the AI could
  not decide (or was unavailable).
* Drafts: of the answers people decided on, how many went out as written, were edited first or
  were rejected, for AI drafts and built-in messages separately.
* Follow-ups: suggestions used versus dismissed.
* Lead scores: the average score of deals won versus deals lost (a useful score is higher for
  the deals that were won).
* AI runs: how many failed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.ai.review.models import ReviewItem, ReviewKind, ReviewStatus
from lanereach.ai.runs.models import AIRun, AIRunStatus
from lanereach.domain.crm.models import Deal, DealStatus
from lanereach.domain.intelligence.models import FollowUpSuggestion
from lanereach.domain.messages.models import Message, MessageDirection


def _json_text(column: Any, key: str) -> ColumnElement[str]:
    """`column ->> 'key'` with the key inlined (a bound key breaks GROUP BY in PostgreSQL)."""
    return column.op("->>")(literal_column(f"'{key}'"))  # type: ignore[no-any-return]


SORTED_BY = {"rules": "rules", "provider": "rules", "llm": "ai", "fallback": "person"}


@dataclass(frozen=True)
class DraftOutcomes:
    """What people did with drafts of one writer."""

    sent_as_written: int = 0
    edited: int = 0
    rejected: int = 0
    waiting: int = 0

    @property
    def decided(self) -> int:
        """Drafts a person approved, edited or rejected."""
        return self.sent_as_written + self.edited + self.rejected

    @property
    def unchanged_percent(self) -> int | None:
        """Share of decided drafts sent exactly as written (None before any decision)."""
        return round(self.sent_as_written * 100 / self.decided) if self.decided else None


@dataclass(frozen=True)
class AIQuality:
    """The AI's track record over a period."""

    days: int
    replies_by: dict[str, int]
    drafts: dict[str, DraftOutcomes]
    follow_ups: dict[str, dict[str, int]]
    won_average_score: int | None
    lost_average_score: int | None
    won_scored: int
    lost_scored: int
    ai_runs: int
    ai_runs_failed: int


async def measure_ai_quality(session: AsyncSession, now: datetime, days: int = 30) -> AIQuality:
    """The AI's track record over the last `days` days.

    Args:
        session: Tenant session.
        now: Current time.
        days: Period length.

    Returns:
        The measures (zeros and Nones when nothing happened yet).
    """
    since = now - timedelta(days=days)

    replies_by = {"rules": 0, "ai": 0, "person": 0}
    sources = await session.execute(
        select(_json_text(Message.processing_result, "source"), func.count())
        .where(
            Message.direction == MessageDirection.INBOUND,
            Message.processed_at >= since,
            Message.processing_result.is_not(None),
        )
        .group_by(_json_text(Message.processing_result, "source"))
    )
    for source, count in sources.all():
        bucket = SORTED_BY.get(source or "", "person")
        replies_by[bucket] += int(count)

    tallies: dict[str, dict[ReviewStatus, int]] = {"ai": {}, "built_in": {}}
    drafts = await session.execute(
        select(_json_text(ReviewItem.payload, "written_by"), ReviewItem.status, func.count())
        .where(ReviewItem.kind == ReviewKind.REPLY_DRAFT, ReviewItem.created_at >= since)
        .group_by(_json_text(ReviewItem.payload, "written_by"), ReviewItem.status)
    )
    for writer, status, count in drafts.all():
        tallies["built_in" if writer == "built_in" else "ai"][status] = int(count)
    outcomes = {
        writer: DraftOutcomes(
            sent_as_written=counts.get(ReviewStatus.APPROVED, 0),
            edited=counts.get(ReviewStatus.EDITED, 0),
            rejected=counts.get(ReviewStatus.REJECTED, 0),
            waiting=counts.get(ReviewStatus.PENDING, 0),
        )
        for writer, counts in tallies.items()
    }

    follow_ups: dict[str, dict[str, int]] = {
        "ai": {"used": 0, "dismissed": 0, "open": 0},
        "built_in": {"used": 0, "dismissed": 0, "open": 0},
    }
    rows = await session.execute(
        select(FollowUpSuggestion.written_by, FollowUpSuggestion.status, func.count())
        .where(FollowUpSuggestion.created_at >= since)
        .group_by(FollowUpSuggestion.written_by, FollowUpSuggestion.status)
    )
    for writer, state, count in rows.all():
        target = follow_ups["built_in" if writer == "built_in" else "ai"]
        if state in target:
            target[state] += int(count)

    async def average_score(status: DealStatus, closed_at: Any) -> tuple[int | None, int]:
        average, scored = (
            await session.execute(
                select(func.avg(Deal.score), func.count(Deal.score)).where(
                    Deal.status == status,
                    closed_at >= since,
                    Deal.score.is_not(None),
                )
            )
        ).one()
        return (round(float(average)) if average is not None else None), int(scored)

    won_average, won_scored = await average_score(DealStatus.WON, Deal.won_at)
    lost_average, lost_scored = await average_score(DealStatus.LOST, Deal.lost_at)

    runs, failed = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(AIRun.status == AIRunStatus.FAILED),
            ).where(AIRun.created_at >= since)
        )
    ).one()

    return AIQuality(
        days=days,
        replies_by=replies_by,
        drafts=outcomes,
        follow_ups=follow_ups,
        won_average_score=won_average,
        lost_average_score=lost_average,
        won_scored=won_scored,
        lost_scored=lost_scored,
        ai_runs=int(runs),
        ai_runs_failed=int(failed),
    )
