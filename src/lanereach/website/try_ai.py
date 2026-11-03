"""Try the reply AI on the public website: classify a pasted reply and pull out the shipment.

Runs the same tasks as the reply pipeline (rules first, then `classify_reply`, then
`extract_shipment_details` for replies that ask for freight), with the same untrusted-content
wrapping and schema validation. Nothing the visitor pastes is stored or logged: the AI runs go to a
recorder that discards them, and no contact, deal or email is created. What LaneReach *would* do
next is described, never done.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

import structlog

from lanereach.ai.rules.reply_rules import ReplyMessage
from lanereach.ai.schemas import ReplyBucket, ReplyClassification, ShipmentDetails
from lanereach.ai.tasks.classify_reply import ReplyContext, classify_reply
from lanereach.ai.tasks.extract_shipment_details import extract_shipment_details
from lanereach.ai.tasks.runtime import AIRuntime

logger = structlog.get_logger(__name__)

# Replies worth reading for shipment details.
SHIPMENT_BUCKETS = frozenset(
    {
        ReplyBucket.RATE_QUERY,
        ReplyBucket.INTERESTED,
        ReplyBucket.SERVICE_QUERY,
        ReplyBucket.MEETING_REQUEST,
        ReplyBucket.REFERRAL,
    }
)

# What LaneReach does with each kind of reply under the default reply policies (ai/policy.py);
# each company can change them. Kept in step by a test.
NEXT_STEPS: dict[ReplyBucket, list[str]] = {
    ReplyBucket.RATE_QUERY: [
        "Hand over to pricing: a quote request and a deal with the shipment filled in",
        "Draft a reply in your office's voice, waiting for review",
        "Stop the follow-up sequence",
    ],
    ReplyBucket.SERVICE_QUERY: [
        "Hand over to pricing as a quote request",
        "Draft a reply, waiting for review",
        "Stop the follow-up sequence",
    ],
    ReplyBucket.INTERESTED: [
        "Hand over to the office owner",
        "Draft a reply, waiting for review",
        "Stop the follow-up sequence",
    ],
    ReplyBucket.MEETING_REQUEST: [
        "Hand over to the office owner",
        "Draft a reply, waiting for review",
        "Stop the follow-up sequence",
    ],
    ReplyBucket.PARTNERSHIP_QUERY: ["Forward to the office owner", "Stop the follow-up sequence"],
    ReplyBucket.REFERRAL: [
        "Hand over the referred contact",
        "Draft a reply, waiting for review",
        "Stop the follow-up sequence",
    ],
    ReplyBucket.WRONG_OFFICE: ["Re-route to the right office", "Stop the follow-up sequence"],
    ReplyBucket.LATER: ["Stop the sequence now", "Enroll the contact again later"],
    ReplyBucket.NOT_INTERESTED: ["Stop the sequence", "Rest the contact"],
    ReplyBucket.COMPLAINT: [
        "Stop the sequence",
        "Do not contact again",
        "Freeze outreach to the whole company",
        "Alert the office owner",
    ],
    ReplyBucket.EXISTING_PARTNER: ["Stop the sequence", "Alert the office owner"],
    ReplyBucket.UNSUBSCRIBE: ["Stop the sequence", "Do not contact again, everywhere"],
    ReplyBucket.BOUNCE_HARD: ["Stop the sequence", "Suppress the address"],
    ReplyBucket.BLOCKED: ["Stop the sequence", "Suppress the address", "Flag the agent's mailbox"],
    ReplyBucket.BOUNCE_SOFT: ["Retry later"],
    ReplyBucket.OUT_OF_OFFICE: ["Reschedule the next email after the return date"],
    ReplyBucket.AUTO_REPLY: ["Ignore it and keep the sequence going"],
    ReplyBucket.UNCLEAR: [
        "Send to the review queue for a person",
        "Draft a reply, waiting for review",
        "Stop the follow-up sequence",
    ],
}


class DiscardingRecorder:
    """An AI run recorder that keeps nothing (website visitors' text is not stored)."""

    async def record_run(self, **_fields: Any) -> uuid.UUID:
        """Return a throwaway run id."""
        return uuid.uuid4()


@dataclass(frozen=True)
class TryAiResult:
    """What the AI made of a pasted reply."""

    classification: ReplyClassification
    source: str
    shipment: ShipmentDetails | None
    next_steps: list[str]


async def try_ai_on_reply(
    runtime: AIRuntime, *, subject: str, body: str, today: date
) -> TryAiResult:
    """Classify a reply and, for freight requests, extract the shipment.

    Args:
        runtime: AI runtime (with a discarding recorder).
        subject: Subject line (may be empty).
        body: The reply text (untrusted).
        today: Used to read dates without a year.

    Returns:
        The classification, the shipment when relevant and the steps LaneReach would take.

    Raises:
        LLMError: When the model cannot be reached or keeps answering invalid output.
    """
    message = ReplyMessage(subject=subject, body=body, received_on=today)
    outcome = await classify_reply(runtime, message, ReplyContext())
    bucket = outcome.classification.bucket
    shipment = None
    if bucket in SHIPMENT_BUCKETS:
        shipment = (await extract_shipment_details(runtime, message)).output
    logger.info("website_ai_demo", bucket=bucket.value, source=outcome.source, characters=len(body))
    return TryAiResult(
        classification=outcome.classification,
        source=outcome.source,
        shipment=shipment,
        next_steps=NEXT_STEPS[bucket],
    )
