"""Retrieval over the company's own history: how did we answer similar emails before?

Before the AI drafts an answer, LaneReach looks for earlier replies of the same kind (the same
bucket) whose words are closest to the new one, and the answer the office actually sent in that
thread. Up to two of these go into the prompt as examples of the company's tone and facts, so
drafts sound like the team and repeat what it really says about its services.

Lexical retrieval (PostgreSQL full-text search with the `simple` configuration, so it works in
every language) keeps this free: no embeddings, no extra model call. Only the tenant's own
messages are searched (row-level security), bodies purged by retention are skipped, and excerpts
are short.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from lanereach.domain.messages.models import Message, MessageDirection, MessageKind

LOOK_BACK = timedelta(days=365)
MAX_KEYWORDS = 16
EMAIL_EXCERPT = 500
ANSWER_EXCERPT = 900
_WORD = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
# Words that say nothing about what a reply is about (greetings, sign-offs, filler).
_STOP = frozenset(
    [
        "the",
        "and",
        "for",
        "you",
        "your",
        "with",
        "this",
        "that",
        "have",
        "from",
        "are",
        "our",
        "can",
        "will",
        "please",
        "thanks",
        "thank",
        "regards",
        "best",
        "dear",
        "hello",
        "hi",
        "kind",
        "would",
        "could",
        "should",
        "about",
        "there",
        "here",
        "what",
        "when",
        "which",
        "also",
        "just",
        "been",
        "were",
        "they",
        "them",
        "their",
        "into",
        "more",
        "some",
        "need",
        "want",
        "like",
    ]
)


@dataclass(frozen=True)
class PastAnswer:
    """An earlier email of the same kind and the answer the office sent."""

    email: str
    answer: str
    answered_on: str


def keywords(text: str) -> list[str]:
    """The distinct content words of `text`, in order, at most `MAX_KEYWORDS`."""
    seen: dict[str, None] = {}
    for word in _WORD.findall(text.lower()):
        if word not in _STOP:
            seen.setdefault(word, None)
        if len(seen) >= MAX_KEYWORDS:
            break
    return list(seen)


async def find_past_answers(
    session: AsyncSession,
    *,
    bucket: str,
    text: str,
    now: datetime,
    exclude_message_id: uuid.UUID | None = None,
    limit: int = 2,
) -> list[PastAnswer]:
    """The office's answers to the earlier emails most like `text` (same kind of reply).

    Args:
        session: Tenant session.
        bucket: Kind of reply the new email is.
        text: Subject and body of the new email.
        now: Current time (only the last year is searched).
        exclude_message_id: The new email itself.
        limit: How many to return.

    Returns:
        Up to `limit` past answers, the closest first (empty when nothing matches).
    """
    words = keywords(text)
    if not words:
        return []
    inbound = aliased(Message)
    document = func.to_tsvector(
        "simple", func.coalesce(inbound.subject, "") + " " + func.coalesce(inbound.body, "")
    )
    query = func.to_tsquery("simple", " | ".join(words))
    rank = func.ts_rank(document, query)
    answer_id = (
        select(Message.id)
        .where(
            Message.thread_id == inbound.thread_id,
            Message.direction == MessageDirection.OUTBOUND,
            Message.kind == MessageKind.REPLY,
            Message.occurred_at > inbound.occurred_at,
            Message.body.is_not(None),
        )
        .order_by(Message.occurred_at)
        .limit(1)
        .correlate(inbound)
        .scalar_subquery()
    )
    statement = (
        select(inbound, answer_id.label("answer_id"))
        .where(
            inbound.direction == MessageDirection.INBOUND,
            inbound.body.is_not(None),
            inbound.occurred_at >= now - LOOK_BACK,
            inbound.processing_result["classification"]["bucket"].astext == bucket,
            document.op("@@")(query),
        )
        .order_by(rank.desc(), inbound.occurred_at.desc())
        .limit(limit * 4)
    )
    if exclude_message_id is not None:
        statement = statement.where(inbound.id != exclude_message_id)
    found: list[PastAnswer] = []
    for email, reply_id in (await session.execute(statement)).all():
        # An email nobody answered yet has no answer id.
        reply = await session.get(Message, reply_id) if reply_id else None
        if reply is None or not reply.body:
            continue
        found.append(
            PastAnswer(
                email=f"Subject: {email.subject}\n{(email.body or '')[:EMAIL_EXCERPT]}",
                answer=reply.body[:ANSWER_EXCERPT],
                answered_on=reply.occurred_at.date().isoformat(),
            )
        )
        if len(found) >= limit:
            break
    return found


def past_answers_text(answers: list[PastAnswer]) -> str:
    """The examples as the model sees them."""
    if not answers:
        return "(none)"
    return "\n\n".join(
        f"[example {index} · answered {answer.answered_on}]\nTheir email:\n{answer.email}\n"
        f"Our answer:\n{answer.answer}"
        for index, answer in enumerate(answers, start=1)
    )
