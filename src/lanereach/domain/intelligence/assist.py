"""AI assists for people at work: a conversation summary and a quote's cover note.

Both are drafts on screen: the summary is not stored, and the cover note fills the quote builder's
message box for a person to edit and send (sending still goes through the engine's checks).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.ai.schemas import QuoteCoverNote, ThreadSummary
from lanereach.ai.tasks.draft_quote_note import draft_quote_note
from lanereach.ai.tasks.runtime import AIRuntime
from lanereach.ai.tasks.suggest_follow_up import ConversationTurn
from lanereach.ai.tasks.summarize_thread import summarize_thread
from lanereach.core.clock import Clock
from lanereach.domain.contacts.models import Contact
from lanereach.domain.inbox.service import InboxService
from lanereach.domain.logistics import LINE_LABELS
from lanereach.domain.messages.models import MessageDirection
from lanereach.domain.quotes.service import QuoteService
from lanereach.domain.senders.models import ServiceLine


@dataclass(frozen=True)
class ConversationSummaryResult:
    """A summary and how many messages it read."""

    summary: ThreadSummary
    messages_read: int


async def summarize_conversation(
    session: AsyncSession, clock: Clock, runtime: AIRuntime, thread_id: str
) -> ConversationSummaryResult:
    """Summarise an inbox conversation (404 for an unknown thread).

    Raises:
        NotFoundError: For an unknown conversation.
        LLMError: When the model fails.
    """
    _summary, messages = await InboxService(session, clock).get_conversation(thread_id)
    turns = [
        ConversationTurn(
            direction="them" if message.direction is MessageDirection.INBOUND else "us",
            occurred_on=message.occurred_at.date().isoformat(),
            subject=message.subject,
            body=message.body or "(text no longer kept)",
        )
        for message in messages
    ]
    result = await summarize_thread(runtime, turns, input_ref=thread_id[:200])
    return ConversationSummaryResult(result.output, min(len(turns), 12))


def _service_label(value: str) -> str:
    try:
        return LINE_LABELS[ServiceLine(value)]
    except ValueError:
        return value


async def quote_facts(
    session: AsyncSession, quotes: QuoteService, quote_id: uuid.UUID
) -> dict[str, Any]:
    """Everything the cover note may mention, as plain data."""
    quote = await quotes.require_quote(quote_id)
    contact = await session.get(Contact, quote.contact_id)
    lines = await quotes.lines_of(quote.id)
    return {
        "contact_first_name": (contact.first_name if contact else None) or None,
        "language": ((contact.language if contact else None) or "en")[:2],
        "service": _service_label(quote.service_line),
        "mode": quote.mode,
        "incoterm": quote.incoterm,
        "origin": quote.origin,
        "destination": quote.destination,
        "currency": quote.currency,
        "total": f"{quote.total_amount:.2f}",
        "valid_until": quote.valid_until.isoformat(),
        "transit_days": quote.transit_days,
        "notes": quote.notes[:600],
        "lines": [
            {
                "description": line.description,
                "quantity": f"{line.quantity.normalize():f}",
                "unit": line.unit,
                "unit_price": f"{line.unit_price:.2f}",
                "amount": f"{line.amount:.2f}",
            }
            for line in lines
        ],
    }


async def write_cover_note(
    session: AsyncSession, quotes: QuoteService, runtime: AIRuntime, quote_id: uuid.UUID
) -> QuoteCoverNote:
    """Draft the message above a quote (404 for an unknown quote).

    Raises:
        NotFoundError: For an unknown quote.
        LLMError: When the model fails or keeps using numbers the quote does not contain.
    """
    facts = await quote_facts(session, quotes, quote_id)
    return (await draft_quote_note(runtime, facts, input_ref=str(quote_id))).output
