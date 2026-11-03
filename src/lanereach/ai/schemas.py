"""Vocabulary shared by AI tasks, the reply policy and workflow nodes.

The enums are shared with workflow node configs (for example `trigger_reply_classified` or
`branch_on_reply_bucket`) and condition fields; the output models validate every LLM response.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReplyBucket(StrEnum):
    """What an inbound reply means for outreach."""

    INTERESTED = "interested"
    MEETING_REQUEST = "meeting_request"
    RATE_QUERY = "rate_query"
    SERVICE_QUERY = "service_query"
    PARTNERSHIP_QUERY = "partnership_query"
    REFERRAL = "referral"
    WRONG_OFFICE = "wrong_office"
    LATER = "later"
    NOT_INTERESTED = "not_interested"
    COMPLAINT = "complaint"
    EXISTING_PARTNER = "existing_partner"
    UNSUBSCRIBE = "unsubscribe"
    OUT_OF_OFFICE = "out_of_office"
    AUTO_REPLY = "auto_reply"
    BOUNCE_HARD = "bounce_hard"
    BOUNCE_SOFT = "bounce_soft"
    BLOCKED = "blocked"
    UNCLEAR = "unclear"


class ShippingMode(StrEnum):
    """Transport mode of a shipment."""

    FCL = "FCL"
    LCL = "LCL"
    AIR = "AIR"
    ROAD = "ROAD"
    RAIL = "RAIL"
    COURIER = "COURIER"
    UNKNOWN = "UNKNOWN"


class ContainerType(StrEnum):
    """ISO container size/type codes used in quotes."""

    GP20 = "20GP"
    GP40 = "40GP"
    HC40 = "40HC"
    HC45 = "45HC"
    RF20 = "20RF"
    RF40 = "40RF"
    OTHER = "OTHER"


class ShipmentFrequency(StrEnum):
    """How often a shipment repeats."""

    ONE_OFF = "one_off"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    UNKNOWN = "unknown"


class Incoterm(StrEnum):
    """Incoterms 2020."""

    EXW = "EXW"
    FCA = "FCA"
    CPT = "CPT"
    CIP = "CIP"
    DAP = "DAP"
    DPU = "DPU"
    DDP = "DDP"
    FAS = "FAS"
    FOB = "FOB"
    CFR = "CFR"
    CIF = "CIF"


# --- AI task output models ---------------------------------------------------------------------
# Every LLM response is validated against one of these before anything uses it. Bounds are
# deliberately tight: an LLM cannot smuggle a 10 kB "summary" or an unknown bucket through.


class _AIOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReplyClassification(_AIOutput):
    """What an inbound reply means (output of `classify_reply`)."""

    bucket: ReplyBucket
    confidence: float = Field(ge=0, le=1)
    language: str = Field(pattern=r"^[a-z]{2}$", description="ISO 639-1 code of the reply.")
    summary: str = Field(max_length=400, description="One or two sentences, no personal data.")
    reasoning_short: str = Field(max_length=400)
    needs_human: bool


class Place(_AIOutput):
    """A shipment origin or destination."""

    city: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    port_code: str | None = Field(
        default=None,
        pattern=r"^[A-Z0-9]{3,5}$",
        description="UN/LOCODE (5 characters) for sea ports or IATA code (3) for airports.",
    )


class ShipmentDetails(_AIOutput):
    """Shipment facts found in a reply; every field is optional (output of extraction)."""

    origin: Place | None = None
    destination: Place | None = None
    mode: ShippingMode = ShippingMode.UNKNOWN
    container_type: ContainerType | None = None
    container_quantity: int | None = Field(default=None, ge=1, le=10_000)
    commodity: str | None = Field(default=None, max_length=200)
    weight_kg: float | None = Field(default=None, ge=0)
    volume_cbm: float | None = Field(default=None, ge=0)
    frequency: ShipmentFrequency = ShipmentFrequency.UNKNOWN
    incoterm: Incoterm | None = None
    cargo_ready_date: date | None = None
    is_hazardous: bool | None = None
    special_requirements: str | None = Field(default=None, max_length=500)
    missing_fields: list[str] = Field(default_factory=list, max_length=20)
    confidence: float = Field(ge=0, le=1)


class ReplyDraft(_AIOutput):
    """A reply drafted in the office's voice (output of `draft_reply`)."""

    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=10_000)
    language: str = Field(pattern=r"^[a-z]{2}$")
    tone: str = Field(max_length=40)
    used_template_key: str | None = Field(default=None, max_length=64)


class LocalizedTemplate(_AIOutput):
    """A template adapted to a language and region (output of `localize_template`)."""

    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20_000)
    language: str = Field(pattern=r"^[a-z]{2}$")
    notes_for_reviewer: str = Field(default="", max_length=1000)


class LaneMatch(_AIOutput):
    """The rate-sheet places a requested lane corresponds to (output of `match_lane`)."""

    origin: str | None = Field(default=None, max_length=200)
    destination: str | None = Field(default=None, max_length=200)
    confidence: float = Field(ge=0, le=1)
    reasoning_short: str = Field(default="", max_length=300)


class LeadAssessment(_AIOutput):
    """How keen a prospect sounds in their latest reply (output of `assess_lead`)."""

    intent: float = Field(ge=0, le=1, description="How likely they want to ship with us soon.")
    urgency: Literal["low", "normal", "high"]
    buying_signals: list[str] = Field(default_factory=list, max_length=5)
    concerns: list[str] = Field(default_factory=list, max_length=3)
    summary: str = Field(default="", max_length=240)

    @field_validator("buying_signals", "concerns")
    @classmethod
    def short_items(cls, items: list[str]) -> list[str]:
        """Each signal is a short phrase."""
        return [item.strip()[:80] for item in items if item.strip()]


DigestKind = Literal["lanes", "win_loss", "speed", "replies", "quotes"]


class DigestHighlight(_AIOutput):
    """One point of the weekly digest."""

    kind: DigestKind
    title: str = Field(min_length=1, max_length=90)
    detail: str = Field(min_length=1, max_length=260)


class DigestNarrative(_AIOutput):
    """The weekly digest written from the week's numbers (output of `write_digest`)."""

    headline: str = Field(min_length=1, max_length=140)
    summary: str = Field(min_length=1, max_length=700)
    highlights: list[DigestHighlight] = Field(default_factory=list, max_length=6)


class FollowUpProposal(_AIOutput):
    """A suggested next step for a quiet deal (output of `suggest_follow_up`)."""

    action: Literal["email", "call", "wait", "close_lost"]
    reason: str = Field(min_length=1, max_length=300)
    subject: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=1500)
    language: str = Field(pattern=r"^[a-z]{2}$")


class ThreadSummary(_AIOutput):
    """What a conversation is about (output of `summarize_thread`)."""

    summary: str = Field(min_length=1, max_length=500)
    customer_wants: list[str] = Field(default_factory=list, max_length=5)
    open_questions: list[str] = Field(default_factory=list, max_length=4)
    next_step: str = Field(min_length=1, max_length=240)
    sentiment: Literal["positive", "neutral", "negative"]
    language: str = Field(pattern=r"^[a-z]{2}$")

    @field_validator("customer_wants", "open_questions")
    @classmethod
    def short_points(cls, items: list[str]) -> list[str]:
        """Each point is one short line."""
        return [item.strip()[:160] for item in items if item.strip()]


class QuoteCoverNote(_AIOutput):
    """The message above a quote (output of `draft_quote_note`)."""

    intro: str = Field(min_length=1, max_length=1200)
    language: str = Field(pattern=r"^[a-z]{2}$")
