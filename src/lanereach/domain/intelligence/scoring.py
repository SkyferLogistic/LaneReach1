"""Lead scores: how promising an open deal is (0-100), with the reasons in plain language.

The score is a transparent points model, so a salesperson can see why a deal is hot. One input
is read by AI: the buying intent and urgency in the prospect's latest reply (`assess_lead`); the
others are facts from LaneReach (how complete the shipment is, how fast they answered, regular
volumes, the company's history, quotes, idle time). The AI part is stored on the deal, so the
nightly rescore updates the facts without another model call. Scores only order the work list.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.ai.llm.client import LLMError
from lanereach.ai.tasks.assess_lead import assess_lead
from lanereach.ai.tasks.runtime import AIRuntime
from lanereach.core.clock import Clock
from lanereach.core.errors import NotFoundError
from lanereach.domain.crm.models import Deal, DealStatus
from lanereach.domain.leads.models import Lead
from lanereach.domain.messages.models import Message, MessageDirection
from lanereach.domain.quotes.models import Quote, QuoteStatus

logger = structlog.get_logger(__name__)

BASE = 30
HOT = 70


@dataclass(frozen=True)
class ScoreInput:
    """Everything the score looks at."""

    intent: float | None = None
    urgency: str | None = None
    signals: tuple[str, ...] = ()
    concerns: tuple[str, ...] = ()
    shipment_fields: int = 0
    missing: tuple[str, ...] = ()
    reply_hours: float | None = None
    replies: int = 0
    frequency: str | None = None
    company_won: int = 0
    company_lost: int = 0
    days_idle: int = 0
    quote_accepted: bool = False
    quote_sent: bool = False
    quote_rejected: bool = False


@dataclass
class Score:
    """A score and why."""

    value: int
    reasons: list[dict[str, Any]] = field(default_factory=list)

    @property
    def band(self) -> str:
        """hot, warm or cold."""
        return "hot" if self.value >= HOT else "warm" if self.value >= 45 else "cold"


def compute_lead_score(features: ScoreInput) -> Score:
    """The points model (pure; every point has a reason).

    Args:
        features: The deal's signals.

    Returns:
        The score (0-100) and its reasons, biggest effect first.
    """
    reasons: list[tuple[str, int]] = []

    def add(label: str, points: int, *, note: bool = False) -> None:
        if points or note:
            reasons.append((label, points))

    if features.intent is not None:
        level = "high" if features.intent >= 0.7 else "medium" if features.intent >= 0.4 else "low"
        add(f"AI read {level} buying intent in the latest reply", round(features.intent * 30) - 8)
        if features.urgency == "high":
            add("AI: the shipment is urgent", 8)
        elif features.urgency == "low":
            add("AI: no rush on their side", -5)
        if features.signals:
            add("Signals: " + ", ".join(features.signals[:3]), 0, note=True)
        if features.concerns:
            add("AI noted: " + ", ".join(features.concerns[:2]), -3 * len(features.concerns[:2]))
    if features.shipment_fields:
        add(
            f"Shipment details {features.shipment_fields} of 5 complete",
            features.shipment_fields * 3,
        )
    if features.missing:
        add("Still missing: " + ", ".join(features.missing[:3]), 0, note=True)
    if features.reply_hours is not None:
        if features.reply_hours <= 4:
            add("Replied within 4 hours", 8)
        elif features.reply_hours <= 24:
            add("Replied within a day", 5)
        elif features.reply_hours > 24 * 7:
            add("Took over a week to reply", -4)
    if features.replies >= 2:
        add(f"{features.replies} replies in the conversation", 4)
    if features.frequency in ("weekly", "monthly", "quarterly"):
        add(
            f"Regular shipments ({features.frequency})",
            8 if features.frequency != "quarterly" else 5,
        )
    if features.company_won:
        add(
            f"Company has {features.company_won} won deal(s) with you",
            min(15, 10 + 2 * (features.company_won - 1)),
        )
    if features.company_lost and not features.company_won:
        add(f"Company has {features.company_lost} lost deal(s)", -3)
    if features.quote_accepted:
        add("A quote was accepted", 20)
    elif features.quote_rejected:
        add("The last quote was rejected", -10)
    elif features.quote_sent:
        add("Quote sent, waiting for an answer", 5)
    if features.days_idle > 30:
        add(f"No activity for {features.days_idle} days", -15)
    elif features.days_idle > 14:
        add(f"No activity for {features.days_idle} days", -8)
    elif features.days_idle > 7:
        add(f"No activity for {features.days_idle} days", -3)

    value = max(0, min(100, BASE + sum(points for _label, points in reasons)))
    ordered = sorted(reasons, key=lambda item: -abs(item[1]))
    return Score(value, [{"label": label, "points": points} for label, points in ordered])


_SHIPMENT_PARTS: tuple[tuple[str, str], ...] = (
    ("lane", "origin and destination"),
    ("mode", "mode"),
    ("size", "containers or weight"),
    ("ready", "ready date"),
    ("commodity", "commodity"),
)


def shipment_completeness(
    shipment: dict[str, Any] | None, deal: Deal
) -> tuple[int, tuple[str, ...]]:
    """How many of the five things pricing needs are known, and which are missing."""
    data = shipment or {}
    known = {
        "lane": bool(
            (data.get("origin") or deal.origin) and (data.get("destination") or deal.destination)
        ),
        "mode": bool((data.get("mode") not in (None, "", "UNKNOWN")) or deal.mode),
        "size": bool(
            data.get("container_type")
            or data.get("weight_kg")
            or data.get("volume_cbm")
            or deal.equipment
            or deal.weight_kg
            or deal.volume_cbm
        ),
        "ready": bool(data.get("cargo_ready_date") or deal.cargo_ready_date),
        "commodity": bool(data.get("commodity") or deal.commodity),
    }
    missing = tuple(label for key, label in _SHIPMENT_PARTS if not known[key])
    return sum(known.values()), missing


class LeadScoringService:
    """Scores deals.

    Args:
        session: Tenant session.
        clock: Time source.
        runtime: AI runtime for reading intent (None: reuse the stored AI reading).
    """

    def __init__(
        self, session: AsyncSession, clock: Clock, runtime: AIRuntime | None = None
    ) -> None:
        self._session = session
        self._clock = clock
        self._runtime = runtime

    async def score_deal(self, deal_id: uuid.UUID, *, read_reply: bool = False) -> Deal:
        """Score one deal (404 for an unknown deal)."""
        deal = await self._session.get(Deal, deal_id)
        if deal is None:
            raise NotFoundError("Deal not found.")
        if read_reply and self._runtime is not None:
            message = await self._latest_reply(deal)
            if message is not None:
                await self.read_intent(deal, message)
        await self._apply(deal)
        return deal

    async def read_intent(self, deal: Deal, message: Message) -> None:
        """Let the AI read buying intent from a reply and keep it on the deal."""
        if self._runtime is None or not message.body:
            return
        context = (
            f"{deal.mode or 'mode unknown'}; lane {deal.origin or '?'} -> {deal.destination or '?'}"
        )
        try:
            result = await assess_lead(
                self._runtime,
                subject=message.subject,
                body=message.body,
                deal_context=context,
                input_ref=str(message.id),
            )
        except LLMError as exc:
            logger.warning("assess_lead_failed", deal_id=str(deal.id), error=type(exc).__name__)
            return
        reading = result.output
        deal.ai_intent = {
            "intent": reading.intent,
            "urgency": reading.urgency,
            "signals": reading.buying_signals,
            "concerns": reading.concerns,
            "summary": reading.summary,
            "message_id": str(message.id),
            "ai_run_id": str(result.ai_run_id),
            "read_at": self._clock.now().isoformat(),
        }

    async def _latest_reply(self, deal: Deal) -> Message | None:
        return await self._session.scalar(
            select(Message)
            .where(
                Message.contact_id == deal.contact_id,
                Message.direction == MessageDirection.INBOUND,
                Message.body.is_not(None),
            )
            .order_by(Message.occurred_at.desc())
            .limit(1)
        )

    async def features(self, deal: Deal) -> ScoreInput:
        """Collect the signals of a deal."""
        now = self._clock.now()
        shipment = None
        if deal.lead_id is not None:
            lead = await self._session.get(Lead, deal.lead_id)
            shipment = lead.shipment if lead else None
        fields, missing = shipment_completeness(shipment, deal)
        messages = (
            await self._session.execute(
                select(Message.direction, Message.occurred_at)
                .where(Message.contact_id == deal.contact_id)
                .order_by(Message.occurred_at)
            )
        ).all()
        replies = [at for direction, at in messages if direction == MessageDirection.INBOUND]
        reply_hours = None
        if replies:
            first_reply = replies[0]
            sent_before = [
                at
                for direction, at in messages
                if direction == MessageDirection.OUTBOUND and at <= first_reply
            ]
            if sent_before:
                reply_hours = (first_reply - sent_before[-1]).total_seconds() / 3600
        company_won = company_lost = 0
        if deal.company_id is not None:
            outcomes = (
                await self._session.execute(
                    select(Deal.status, func.count())
                    .where(Deal.company_id == deal.company_id, Deal.id != deal.id)
                    .group_by(Deal.status)
                )
            ).all()
            counts = {status: int(count) for status, count in outcomes}
            company_won, company_lost = (
                counts.get(DealStatus.WON, 0),
                counts.get(DealStatus.LOST, 0),
            )
        statuses = set(
            (
                await self._session.scalars(select(Quote.status).where(Quote.deal_id == deal.id))
            ).all()
        )
        last_activity = max([deal.updated_at, deal.stage_entered_at, *(at for _d, at in messages)])
        intent = deal.ai_intent or {}
        return ScoreInput(
            intent=intent.get("intent"),
            urgency=intent.get("urgency"),
            signals=tuple(intent.get("signals") or ()),
            concerns=tuple(intent.get("concerns") or ()),
            shipment_fields=fields,
            missing=missing,
            reply_hours=reply_hours,
            replies=len(replies),
            frequency=str((shipment or {}).get("frequency") or "") or None,
            company_won=company_won,
            company_lost=company_lost,
            days_idle=max(0, (now - last_activity).days),
            quote_accepted=QuoteStatus.ACCEPTED in statuses,
            quote_sent=QuoteStatus.SENT in statuses,
            quote_rejected=QuoteStatus.REJECTED in statuses,
        )

    async def _apply(self, deal: Deal) -> None:
        score = compute_lead_score(await self.features(deal))
        deal.score = score.value
        deal.score_reasons = score.reasons
        deal.scored_at = self._clock.now()
        await self._session.flush()

    async def rescore_open_deals(self, *, stale_after: timedelta = timedelta(hours=20)) -> int:
        """Rescore open deals not scored recently (no AI call; keeps the stored AI reading)."""
        cutoff: datetime = self._clock.now() - stale_after
        deals = (
            await self._session.scalars(
                select(Deal).where(
                    Deal.status == DealStatus.OPEN,
                    (Deal.scored_at.is_(None)) | (Deal.scored_at < cutoff),
                )
            )
        ).all()
        for deal in deals:
            await self._apply(deal)
        return len(deals)
