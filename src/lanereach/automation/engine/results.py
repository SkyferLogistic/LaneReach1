"""What a node tells the executor to do next."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Continue:
    """Follow the edge leaving the node through `handle` immediately."""

    handle: str = "next"


@dataclass(frozen=True, slots=True)
class Wait:
    """Pause the enrollment until `until`.

    With a `handle` the enrollment then follows that output (a wait node). With `handle=None`
    the same node runs again when the time comes (a guardrail deferred the email).
    """

    until: datetime
    reason: str
    handle: str | None = "next"


@dataclass(frozen=True, slots=True)
class Stop:
    """End the enrollment.

    `completed=True` means the workflow reached its end; `False` means it was stopped early
    (for example a guardrail blocked the email), which is recorded as `stopped`.
    """

    reason: str
    completed: bool = True


@dataclass(frozen=True, slots=True)
class Fail:
    """The node failed. Retryable failures back off and retry; others fail the enrollment."""

    error: str
    retryable: bool


NodeResult = Continue | Wait | Stop | Fail
