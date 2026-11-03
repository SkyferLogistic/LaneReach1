"""Deterministic reply policy: what happens after a reply is classified (spec §5.5).

The LLM only classifies; this table decides. Two rules hold whatever the model says:

* a classification below `AI_REVIEW_CONFIDENCE_THRESHOLD`, or flagged `needs_human`, is always
  drafted for review (never auto-sent, never silently ignored when it is a human reply);
* `auto_send` is disabled for every bucket unless the tenant explicitly enables it per bucket.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from lanereach.ai.schemas import ReplyBucket, ReplyClassification


class ActionMode(StrEnum):
    """How the reply itself is answered."""

    AUTO_SEND = "auto_send"
    DRAFT_FOR_REVIEW = "draft_for_review"
    FORWARD_TO_OWNER = "forward_to_owner"
    NONE = "none"


class DraftSource(StrEnum):
    """Who writes the answer to a reply.

    `built_in` uses the company's message for that kind of reply (its `reply_<bucket>` template
    in the contact's language, else LaneReach's own text): no AI call, the same words every
    time. `ai` drafts with the model and falls back to the built-in message when the AI is not
    available (budget used up, provider down).
    """

    AI = "ai"
    BUILT_IN = "built_in"


class ProtectionAction(StrEnum):
    """Side effects protecting the prospect and the sender reputation."""

    STOP_SEQUENCE = "stop_sequence"
    REENROLL_LATER = "reenroll_later"
    REST = "rest"
    DO_NOT_CONTACT = "do_not_contact"
    FREEZE_COMPANY = "freeze_company"
    ALERT_OWNER = "alert_owner"
    SUPPRESS_CONTACT = "suppress_contact"
    FLAG_MAILBOX = "flag_mailbox"
    SOFT_BOUNCE_RETRY = "soft_bounce_retry"
    RESCHEDULE = "reschedule"


class Handover(StrEnum):
    """Who takes over the conversation."""

    OFFICE_OWNER = "office_owner"
    PRICING_LEAD = "pricing_lead"
    REFERRED_CONTACT = "referred_contact"
    REROUTE_OFFICE = "reroute_office"
    HUMAN_REVIEW = "human_review"


@dataclass(frozen=True)
class BucketPolicy:
    """Default treatment of one bucket."""

    action_mode: ActionMode
    protection_actions: tuple[ProtectionAction, ...] = ()
    handover: Handover | None = None


P = ProtectionAction
DEFAULT_POLICIES: dict[ReplyBucket, BucketPolicy] = {
    ReplyBucket.INTERESTED: BucketPolicy(
        ActionMode.DRAFT_FOR_REVIEW, (P.STOP_SEQUENCE,), Handover.OFFICE_OWNER
    ),
    ReplyBucket.MEETING_REQUEST: BucketPolicy(
        ActionMode.DRAFT_FOR_REVIEW, (P.STOP_SEQUENCE,), Handover.OFFICE_OWNER
    ),
    ReplyBucket.RATE_QUERY: BucketPolicy(
        ActionMode.DRAFT_FOR_REVIEW, (P.STOP_SEQUENCE,), Handover.PRICING_LEAD
    ),
    ReplyBucket.SERVICE_QUERY: BucketPolicy(
        ActionMode.DRAFT_FOR_REVIEW, (P.STOP_SEQUENCE,), Handover.PRICING_LEAD
    ),
    ReplyBucket.PARTNERSHIP_QUERY: BucketPolicy(
        ActionMode.FORWARD_TO_OWNER, (P.STOP_SEQUENCE,), Handover.OFFICE_OWNER
    ),
    ReplyBucket.REFERRAL: BucketPolicy(
        ActionMode.DRAFT_FOR_REVIEW, (P.STOP_SEQUENCE,), Handover.REFERRED_CONTACT
    ),
    ReplyBucket.WRONG_OFFICE: BucketPolicy(
        ActionMode.FORWARD_TO_OWNER, (P.STOP_SEQUENCE,), Handover.REROUTE_OFFICE
    ),
    ReplyBucket.LATER: BucketPolicy(ActionMode.NONE, (P.STOP_SEQUENCE, P.REENROLL_LATER)),
    ReplyBucket.NOT_INTERESTED: BucketPolicy(ActionMode.NONE, (P.STOP_SEQUENCE, P.REST)),
    ReplyBucket.COMPLAINT: BucketPolicy(
        ActionMode.NONE, (P.STOP_SEQUENCE, P.DO_NOT_CONTACT, P.FREEZE_COMPANY, P.ALERT_OWNER)
    ),
    ReplyBucket.EXISTING_PARTNER: BucketPolicy(ActionMode.NONE, (P.STOP_SEQUENCE, P.ALERT_OWNER)),
    ReplyBucket.UNSUBSCRIBE: BucketPolicy(ActionMode.NONE, (P.STOP_SEQUENCE, P.DO_NOT_CONTACT)),
    ReplyBucket.BOUNCE_HARD: BucketPolicy(ActionMode.NONE, (P.STOP_SEQUENCE, P.SUPPRESS_CONTACT)),
    ReplyBucket.BLOCKED: BucketPolicy(
        ActionMode.NONE, (P.STOP_SEQUENCE, P.SUPPRESS_CONTACT, P.FLAG_MAILBOX)
    ),
    ReplyBucket.BOUNCE_SOFT: BucketPolicy(ActionMode.NONE, (P.SOFT_BOUNCE_RETRY,)),
    ReplyBucket.OUT_OF_OFFICE: BucketPolicy(ActionMode.NONE, (P.RESCHEDULE,)),
    ReplyBucket.AUTO_REPLY: BucketPolicy(ActionMode.NONE),
    ReplyBucket.UNCLEAR: BucketPolicy(
        ActionMode.DRAFT_FOR_REVIEW, (P.STOP_SEQUENCE,), Handover.HUMAN_REVIEW
    ),
}

# Automatic messages: the person did not answer, so outreach is not stopped by "stop on reply".
AUTOMATIC_BUCKETS = frozenset(
    {
        ReplyBucket.OUT_OF_OFFICE,
        ReplyBucket.AUTO_REPLY,
        ReplyBucket.BOUNCE_SOFT,
    }
)


@dataclass(frozen=True)
class TenantPolicyOverride:
    """A tenant's override for one bucket."""

    action_mode: ActionMode | None = None
    auto_send_enabled: bool = False
    draft_source: DraftSource = DraftSource.AI


@dataclass(frozen=True)
class ReplyPolicyDecision:
    """What to do with a classified reply."""

    action_mode: ActionMode
    protection_actions: tuple[ProtectionAction, ...]
    handover: Handover | None
    reasons: tuple[str, ...] = field(default_factory=tuple)
    draft_source: DraftSource = DraftSource.AI


def decide_reply_policy(
    classification: ReplyClassification,
    tenant_policy: Mapping[ReplyBucket, TenantPolicyOverride],
    *,
    review_threshold: float,
) -> ReplyPolicyDecision:
    """Decide the action mode, protection actions and handover for a classification.

    Args:
        classification: Validated classification.
        tenant_policy: The tenant's overrides by bucket.
        review_threshold: `AI_REVIEW_CONFIDENCE_THRESHOLD`.

    Returns:
        The decision. `reasons` explains any adjustment (shown in the review queue).
    """
    default = DEFAULT_POLICIES[classification.bucket]
    override = tenant_policy.get(classification.bucket)
    action_mode = override.action_mode if override and override.action_mode else default.action_mode
    reasons: list[str] = []

    if action_mode is ActionMode.AUTO_SEND and not (override and override.auto_send_enabled):
        action_mode = ActionMode.DRAFT_FOR_REVIEW
        reasons.append("auto-send is not enabled for this bucket")

    handover = default.handover
    uncertain = classification.confidence < review_threshold or classification.needs_human
    if uncertain and classification.bucket not in AUTOMATIC_BUCKETS:
        if action_mode in (ActionMode.AUTO_SEND, ActionMode.NONE):
            action_mode = ActionMode.DRAFT_FOR_REVIEW
        handover = handover or Handover.HUMAN_REVIEW
        reasons.append(
            f"confidence {classification.confidence:.2f} below {review_threshold:.2f}"
            if classification.confidence < review_threshold
            else "the classifier asked for a human"
        )
    # Protection actions always apply: each of them only means *less* email, which is the safe
    # direction when a classification might be wrong.
    return ReplyPolicyDecision(
        action_mode=action_mode,
        protection_actions=default.protection_actions,
        handover=handover,
        reasons=tuple(reasons),
        draft_source=override.draft_source if override else DraftSource.AI,
    )
