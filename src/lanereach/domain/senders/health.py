"""Mailbox health: how well each agent mailbox is doing, as a score people can act on.

Bounces and spam complaints hurt a mailbox's reputation with receiving servers; a flagged
mailbox (after a "blocked" bounce) already stopped sending. The score only summarises the
numbers next to it; nothing is paused automatically beyond the existing flag.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.guardrails.mailbox_capacity import office_day
from lanereach.domain.messages.models import Message, MessageDirection, MessageKind
from lanereach.domain.offices.models import Office
from lanereach.domain.senders.models import MailboxDailyUsage, Sender

# Receiving servers start to distrust a sender around these rates.
BOUNCE_RATE_WARNING = 0.02
COMPLAINT_RATE_WARNING = 0.001
MIN_SENT_FOR_SCORE = 20


@dataclass(slots=True)
class MailboxHealth:
    """Health of one agent mailbox over the last 30 days."""

    sender_id: uuid.UUID
    display_name: str
    email: str
    office_id: uuid.UUID
    service_line: str
    is_active: bool
    flagged: bool
    flag_reason: str | None
    daily_cap: int
    used_today: int
    sent_7d: int
    sent_30d: int
    tracked_30d: int
    opened_30d: int
    replies_30d: int
    bounces_30d: int
    complaints_30d: int
    score: int = 100
    status: str = "healthy"
    reasons: list[str] = field(default_factory=list)

    @property
    def bounce_rate(self) -> float:
        """Bounces per email sent (30 days)."""
        return self.bounces_30d / self.sent_30d if self.sent_30d else 0.0

    @property
    def complaint_rate(self) -> float:
        """Spam complaints per email sent (30 days)."""
        return self.complaints_30d / self.sent_30d if self.sent_30d else 0.0

    @property
    def reply_rate(self) -> float:
        """Replies per email sent (30 days)."""
        return self.replies_30d / self.sent_30d if self.sent_30d else 0.0

    @property
    def open_rate(self) -> float:
        """Human opens per tracked email (30 days)."""
        return self.opened_30d / self.tracked_30d if self.tracked_30d else 0.0


def score_mailbox(health: MailboxHealth) -> None:
    """Fill `score`, `status` and `reasons` from the numbers.

    Args:
        health: The mailbox's numbers (changed in place).
    """
    score, reasons = 100, []
    if health.bounce_rate > BOUNCE_RATE_WARNING:
        score -= min(50, round((health.bounce_rate - BOUNCE_RATE_WARNING) * 1000))
        reasons.append(f"Bounce rate {health.bounce_rate:.1%} (keep it under 2%)")
    if health.complaint_rate > COMPLAINT_RATE_WARNING:
        score -= 30
        reasons.append(f"{health.complaints_30d} spam complaint(s) in 30 days")
    if health.daily_cap and health.used_today >= health.daily_cap:
        reasons.append("Daily cap reached today")
    if health.sent_30d < MIN_SENT_FOR_SCORE:
        reasons.append("Few emails yet: the score settles after about 20 sends")
    status = "healthy" if score >= 80 else "watch" if score >= 50 else "at_risk"
    if health.flagged:
        score = min(score, 20)
        status = "at_risk"
        reasons.insert(0, f"Flagged: {health.flag_reason or 'blocked by a receiving server'}")
    if not health.is_active:
        status = "paused"
        reasons.insert(0, "Switched off")
    health.score, health.status, health.reasons = max(0, score), status, reasons


async def build_mailbox_health(session: AsyncSession, now: datetime) -> list[MailboxHealth]:
    """Health of every agent mailbox, worst first.

    Args:
        session: Tenant session.
        now: Current time.

    Returns:
        One entry per sender.
    """
    since_30, since_7 = now - timedelta(days=30), now - timedelta(days=7)
    senders = (await session.scalars(select(Sender).order_by(Sender.display_name))).all()
    if not senders:
        return []
    outbound = Message.direction == MessageDirection.OUTBOUND
    inbound = Message.direction == MessageDirection.INBOUND
    rows = (
        await session.execute(
            select(
                Message.sender_id,
                func.count().filter(outbound, Message.occurred_at >= since_7),
                func.count().filter(outbound),
                func.count().filter(outbound, Message.tracking_id.is_not(None)),
                func.count().filter(outbound, Message.first_opened_at.is_not(None)),
                func.count().filter(inbound, Message.kind == MessageKind.REPLY),
                func.count().filter(inbound, Message.kind == MessageKind.BOUNCE),
                func.count().filter(inbound, Message.kind == MessageKind.COMPLAINT),
            )
            .where(Message.sender_id.is_not(None), Message.occurred_at >= since_30)
            .group_by(Message.sender_id)
        )
    ).all()
    counts = {row[0]: [int(value or 0) for value in row[1:]] for row in rows}
    offices = {office.id: office for office in (await session.scalars(select(Office))).all()}
    usage = {
        (row.sender_id, row.day): row.sent_count
        for row in (
            await session.scalars(
                select(MailboxDailyUsage).where(
                    MailboxDailyUsage.day >= (now - timedelta(days=1)).date()
                )
            )
        ).all()
    }
    result = []
    for sender in senders:
        office = offices.get(sender.office_id)
        today = office_day(now, office.timezone if office else "UTC")
        sent_7, sent_30, tracked, opened, replies, bounces, complaints = counts.get(
            sender.id, [0] * 7
        )
        health = MailboxHealth(
            sender_id=sender.id,
            display_name=sender.display_name,
            email=sender.email,
            office_id=sender.office_id,
            service_line=sender.service_line.value,
            is_active=sender.is_active,
            flagged=sender.flagged_at is not None,
            flag_reason=sender.flag_reason,
            daily_cap=sender.daily_cap,
            used_today=usage.get((sender.id, today), 0),
            sent_7d=sent_7,
            sent_30d=sent_30,
            tracked_30d=tracked,
            opened_30d=opened,
            replies_30d=replies,
            bounces_30d=bounces,
            complaints_30d=complaints,
        )
        score_mailbox(health)
        result.append(health)
    return sorted(result, key=lambda item: (item.score, item.display_name.lower()))
