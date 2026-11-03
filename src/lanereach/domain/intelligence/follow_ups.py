"""Follow-up suggestions for deals that went quiet.

A deal is quiet when nothing happened on it (no message with the contact, no stage move, no edit)
for `QUIET_DEAL_DAYS` days. The AI suggests one next step (email, call, wait or close as lost)
and drafts it; a person uses it (LaneReach creates a task with the draft for the deal's owner)
or dismisses it. Nothing is sent from here.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.ai.builtin_messages import (
    BUILT_IN_FOLLOW_UP,
    FOLLOW_UP_TEMPLATE_KEY,
    message_values,
    write_built_in_message,
)
from lanereach.ai.controls import load_ai_controls
from lanereach.ai.llm.client import LLMError
from lanereach.ai.schemas import FollowUpProposal
from lanereach.ai.tasks.runtime import AIRuntime
from lanereach.ai.tasks.suggest_follow_up import ConversationTurn, suggest_follow_up
from lanereach.core.clock import Clock
from lanereach.core.errors import BusinessRuleViolationError, ConflictError, NotFoundError
from lanereach.domain.contacts.models import Contact
from lanereach.domain.crm.models import Deal, DealStatus, PipelineStage, Task, TaskPriority
from lanereach.domain.intelligence.models import FollowUpSuggestion
from lanereach.domain.messages.models import Message, MessageDirection
from lanereach.domain.offices.models import Office
from lanereach.domain.quotes.models import Quote

logger = structlog.get_logger(__name__)

RESUGGEST_AFTER = timedelta(days=7)
# Built-in follow-ups suggest closing a deal quiet this long with no quote waiting.
CLOSE_AFTER_DAYS = 30
CONVERSATION_TURNS = 4
TASK_TITLES = {
    "email": "Send follow-up",
    "call": "Call about",
    "wait": "Check back on",
    "close_lost": "Close as lost?",
}


@dataclass(frozen=True)
class QuietDeal:
    """An open deal and how long nothing happened on it."""

    deal: Deal
    days_quiet: int


async def find_quiet_deals(session: AsyncSession, now: datetime, days: int) -> list[QuietDeal]:
    """Open deals with no activity for at least `days` days, quietest first."""
    last_message = (
        select(Message.contact_id, func.max(Message.occurred_at).label("last_at"))
        .group_by(Message.contact_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(Deal, last_message.c.last_at)
            .outerjoin(last_message, last_message.c.contact_id == Deal.contact_id)
            .where(Deal.status == DealStatus.OPEN)
        )
    ).all()
    quiet = []
    for deal, last_at in rows:
        activity = max(
            item for item in (deal.updated_at, deal.stage_entered_at, last_at) if item is not None
        )
        idle = (now - activity).days
        if idle >= days:
            quiet.append(QuietDeal(deal, idle))
    return sorted(quiet, key=lambda item: -item.days_quiet)


class FollowUpService:
    """Suggests, uses and dismisses follow-ups.

    Args:
        session: Tenant session.
        clock: Time source.
        runtime: AI runtime (needed to suggest).
        quiet_days: Days without activity that make a deal quiet.
    """

    def __init__(
        self, session: AsyncSession, clock: Clock, runtime: AIRuntime | None, *, quiet_days: int
    ) -> None:
        self._session = session
        self._clock = clock
        self._runtime = runtime
        self._quiet_days = quiet_days

    async def list_suggestions(self, status: str | None = "open") -> Sequence[FollowUpSuggestion]:
        """Suggestions, newest first."""
        statement = (
            select(FollowUpSuggestion).order_by(FollowUpSuggestion.created_at.desc()).limit(200)
        )
        if status:
            statement = statement.where(FollowUpSuggestion.status == status)
        return (await self._session.scalars(statement)).all()

    async def suggest(
        self,
        deal_id: uuid.UUID,
        *,
        days_quiet: int | None = None,
        built_in_when_ai_fails: bool = False,
    ) -> FollowUpSuggestion:
        """Suggest a follow-up on a deal (replaces the deal's open suggestion).

        The AI writes it unless the company chose built-in follow-ups (or no model is
        configured): then the next step follows simple rules and the message is the company's
        follow-up template, with no AI call.

        Args:
            deal_id: The deal.
            days_quiet: Days without activity (computed when omitted).
            built_in_when_ai_fails: Use the built-in follow-up when the AI fails (the daily
                job), instead of raising (a person who asked sees the error).

        Raises:
            NotFoundError: For an unknown deal.
            BusinessRuleViolationError: For a closed deal.
            LLMError: When the model fails and `built_in_when_ai_fails` is off.
        """
        deal = await self._session.get(Deal, deal_id)
        if deal is None:
            raise NotFoundError("Deal not found.")
        if deal.status is not DealStatus.OPEN:
            raise BusinessRuleViolationError("Only open deals get follow-up suggestions.")
        now = self._clock.now()
        if days_quiet is None:
            quiet = {
                item.deal.id: item.days_quiet
                for item in await find_quiet_deals(self._session, now, 0)
            }
            days_quiet = quiet.get(deal.id, 0)
        stage = await self._session.get(PipelineStage, deal.stage_id)
        quotes = (
            await self._session.scalars(select(Quote.status).where(Quote.deal_id == deal.id))
        ).all()
        context = (
            f"stage {stage.name if stage else '?'}; "
            f"lane {deal.origin or '?'} -> {deal.destination or '?'}; "
            f"mode {deal.mode or 'unknown'}; {days_quiet} days without activity; "
            f"quotes: {', '.join(status.value for status in quotes) or 'none'}"
        )
        quote_statuses = [status.value for status in quotes]
        proposal: FollowUpProposal | None = None
        run_id: uuid.UUID | None = None
        use_ai = self._runtime is not None
        if use_ai and (await load_ai_controls(self._session)).follow_ups == "built_in":
            use_ai = False
        if use_ai and self._runtime is not None:
            try:
                result = await suggest_follow_up(
                    self._runtime,
                    deal_context=context,
                    conversation=await self._conversation(deal),
                    input_ref=str(deal.id),
                )
            except LLMError:
                if not built_in_when_ai_fails:
                    raise
            else:
                proposal, run_id = result.output, result.ai_run_id
        written_by = "ai" if proposal is not None else "built_in"
        if proposal is None:
            proposal = await self._built_in_proposal(deal, days_quiet, quote_statuses)
        for previous in (
            await self._session.scalars(
                select(FollowUpSuggestion).where(
                    FollowUpSuggestion.deal_id == deal.id, FollowUpSuggestion.status == "open"
                )
            )
        ).all():
            previous.status = "dismissed"
            previous.decided_at = now
            previous.decided_by = "replaced"
        suggestion = FollowUpSuggestion(
            tenant_id=deal.tenant_id,
            deal_id=deal.id,
            days_quiet=days_quiet,
            action=proposal.action,
            reason=proposal.reason,
            subject=proposal.subject,
            body=proposal.body,
            language=proposal.language,
            status="open",
            ai_run_id=run_id,
            written_by=written_by,
            created_at=now,
        )
        self._session.add(suggestion)
        await self._session.flush()
        return suggestion

    async def _built_in_proposal(
        self, deal: Deal, days_quiet: int, quote_statuses: list[str]
    ) -> FollowUpProposal:
        """The next step by simple rules, with the company's follow-up message."""
        if days_quiet >= CLOSE_AFTER_DAYS and "sent" not in quote_statuses:
            return FollowUpProposal(
                action="close_lost",
                reason=f"No activity for {days_quiet} days: close it, or try once more.",
                language="en",
            )
        contact = await self._session.get(Contact, deal.contact_id)
        office = (
            await self._session.get(Office, contact.office_id)
            if contact and contact.office_id
            else None
        )
        lane = (
            f"your {deal.origin} to {deal.destination} shipment"
            if deal.origin and deal.destination
            else None
        )
        message = await write_built_in_message(
            self._session,
            template_key=FOLLOW_UP_TEMPLATE_KEY,
            default_body=BUILT_IN_FOLLOW_UP,
            default_subject=f"Following up: {deal.title}"[:200],
            languages=[(contact.language if contact else None) or "en"],
            values=message_values(
                contact_first_name=contact.first_name if contact else None,
                contact_full_name=contact.display_name if contact else None,
                company_name=None,
                office_name=office.name if office else None,
                office_owner_name=office.owner_name if office else None,
                sender_name=None,
                extra={"deal.lane": lane},
            ),
        )
        waiting = "sent" in quote_statuses
        reason = (
            f"{days_quiet} days quiet with a quote waiting for an answer."
            if waiting
            else f"No activity for {days_quiet} days."
        )
        return FollowUpProposal(
            action="email",
            reason=reason[:300],
            subject=message.subject[:200],
            body=message.body[:1500],
            language=message.language[:2],
        )

    async def _conversation(self, deal: Deal) -> list[ConversationTurn]:
        messages = (
            await self._session.scalars(
                select(Message)
                .where(Message.contact_id == deal.contact_id)
                .order_by(Message.occurred_at.desc())
                .limit(CONVERSATION_TURNS)
            )
        ).all()
        return [
            ConversationTurn(
                direction="them" if message.direction is MessageDirection.INBOUND else "us",
                occurred_on=message.occurred_at.date().isoformat(),
                subject=message.subject,
                body=message.body or "(text no longer kept)",
            )
            for message in reversed(messages)
        ]

    async def suggest_for_quiet_deals(self, limit: int) -> int:
        """Suggest follow-ups for quiet deals without a recent suggestion (daily job)."""
        now = self._clock.now()
        recent = set(
            (
                await self._session.scalars(
                    select(FollowUpSuggestion.deal_id).where(
                        FollowUpSuggestion.created_at >= now - RESUGGEST_AFTER
                    )
                )
            ).all()
        )
        made = 0
        for item in await find_quiet_deals(self._session, now, self._quiet_days):
            if made >= limit:
                break
            if item.deal.id in recent:
                continue
            try:
                await self.suggest(
                    item.deal.id, days_quiet=item.days_quiet, built_in_when_ai_fails=True
                )
            except LLMError as exc:
                logger.warning(
                    "follow_up_suggestion_failed",
                    deal_id=str(item.deal.id),
                    error=type(exc).__name__,
                )
                continue
            made += 1
        return made

    async def _open(self, suggestion_id: uuid.UUID) -> FollowUpSuggestion:
        suggestion = await self._session.get(FollowUpSuggestion, suggestion_id)
        if suggestion is None:
            raise NotFoundError("Suggestion not found.")
        if suggestion.status != "open":
            raise ConflictError("This suggestion was already used or dismissed.")
        return suggestion

    async def use(self, suggestion_id: uuid.UUID, actor: str) -> tuple[FollowUpSuggestion, Task]:
        """Turn a suggestion into a task for the deal's owner (the draft is in the notes)."""
        suggestion = await self._open(suggestion_id)
        deal = await self._session.get(Deal, suggestion.deal_id)
        if deal is None:
            raise NotFoundError("Deal not found.")
        contact = await self._session.get(Contact, deal.contact_id)
        now = self._clock.now()
        notes = suggestion.reason
        if suggestion.body:
            notes += (
                f"\n\nDraft ({suggestion.language}), review before sending:\n"
                f"Subject: {suggestion.subject}\n\n{suggestion.body}"
            )
        task = Task(
            tenant_id=deal.tenant_id,
            title=f"{TASK_TITLES.get(suggestion.action, 'Follow up')}: {deal.title}"[:200],
            notes=notes[:4000],
            due_at=now,
            assignee_email=deal.owner_email or (contact.owner_email if contact else None),
            priority=TaskPriority.HIGH
            if suggestion.action in ("email", "call")
            else TaskPriority.NORMAL,
            contact_id=deal.contact_id,
            deal_id=deal.id,
            created_by=actor[:200],
            created_at=now,
            updated_at=now,
        )
        self._session.add(task)
        await self._session.flush()
        suggestion.status = "used"
        suggestion.decided_at = now
        suggestion.decided_by = actor[:320]
        suggestion.task_id = task.id
        await self._session.flush()
        return suggestion, task

    async def dismiss(self, suggestion_id: uuid.UUID, actor: str) -> FollowUpSuggestion:
        """Dismiss a suggestion."""
        suggestion = await self._open(suggestion_id)
        suggestion.status = "dismissed"
        suggestion.decided_at = self._clock.now()
        suggestion.decided_by = actor[:320]
        await self._session.flush()
        return suggestion
