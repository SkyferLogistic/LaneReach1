"""Guardrail interfaces and shared helpers.

Guardrails live in the engine, never in workflows or prompts: nothing a workflow author or an
LLM produces can switch them off. Each guardrail returns `allow`, `defer` (try again at
`defer_until`) or `block` (never send this email).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.workflows.schedule import CampaignSchedule
from lanereach.domain.calendars.service import BusinessCalendar, CountryCalendarService
from lanereach.domain.companies.models import Company
from lanereach.domain.contacts.models import Contact
from lanereach.domain.offices.models import Office
from lanereach.domain.senders.models import Sender
from lanereach.tenants.models import Tenant


class GuardrailOutcome(StrEnum):
    """What a guardrail decided."""

    ALLOW = "allow"
    DEFER = "defer"
    BLOCK = "block"


class EmailKind(StrEnum):
    """Which rules apply to an email.

    * `cold` - the first outreach email of an enrollment (`send_email`): every guardrail applies.
    * `follow_up` - a later `send_email` of the same enrollment. It is sent in the thread of the
      first email, so like a thread reply it is exempt from the frequency cap and company
      rotation (otherwise a 5-business-day follow-up could never meet a 30-day cap).
    * `reply` - a workflow reply in an existing thread (`reply_in_thread`): exempt from the
      frequency cap and company rotation.
    * `approved_reply` — a reply a person approved in the review queue: only the kill switch,
      suppression, company freeze and mailbox capacity apply, because a human already chose to
      answer now.
    """

    COLD = "cold"
    FOLLOW_UP = "follow_up"
    REPLY = "reply"
    APPROVED_REPLY = "approved_reply"


ALL_KINDS = frozenset(EmailKind)
WORKFLOW_KINDS = frozenset({EmailKind.COLD, EmailKind.FOLLOW_UP, EmailKind.REPLY})
COLD_ONLY = frozenset({EmailKind.COLD})


@dataclass(frozen=True, slots=True)
class GuardrailDecision:
    """One guardrail's verdict."""

    guardrail: str
    outcome: GuardrailOutcome
    reason: str
    defer_until: datetime | None = None

    def to_json(self) -> dict[str, Any]:
        """Serialise for storage on the enrollment step.

        Returns:
            A JSON-compatible dict.
        """
        return {
            "guardrail": self.guardrail,
            "outcome": self.outcome.value,
            "reason": self.reason,
            "defer_until": self.defer_until.isoformat() if self.defer_until else None,
        }


@dataclass(frozen=True, slots=True)
class OutboundEmail:
    """The email about to be sent, with everything guardrails need to judge it."""

    kind: EmailKind
    tenant: Tenant
    contact: Contact
    company: Company | None
    office: Office
    sender: Sender | None
    schedule: CampaignSchedule | None = None


@dataclass
class GuardrailContext:
    """Shared inputs of one evaluation."""

    session: AsyncSession
    now: datetime
    calendars: CountryCalendarService
    frequency_cap_days: int
    kill_switch_recheck_minutes: int
    # Real sending needs a connected mailbox per agent; dry-run needs none.
    requires_connected_mailbox: bool = False

    async def recipient_calendar(self, email: OutboundEmail) -> BusinessCalendar:
        """Calendar of the recipient's country.

        Args:
            email: The email being judged.

        Returns:
            The calendar.
        """
        return await self.calendars.get_calendar(email.contact.country)

    async def recipient_zone(self, email: OutboundEmail) -> ZoneInfo:
        """The recipient's timezone: the contact's own, else their country's.

        Args:
            email: The email being judged.

        Returns:
            The timezone.
        """
        if email.contact.timezone:
            try:
                return ZoneInfo(email.contact.timezone)
            except (ZoneInfoNotFoundError, ValueError):
                pass
        return (await self.recipient_calendar(email)).timezone


class Guardrail(Protocol):
    """A single send rule."""

    name: str
    applies_to: frozenset[EmailKind]

    async def evaluate(self, context: GuardrailContext, email: OutboundEmail) -> GuardrailDecision:
        """Judge the email."""
        ...


def allow(name: str, reason: str = "ok") -> GuardrailDecision:
    """Build an `allow` decision.

    Args:
        name: Guardrail name.
        reason: Short explanation.

    Returns:
        The decision.
    """
    return GuardrailDecision(name, GuardrailOutcome.ALLOW, reason)


def window_bounds(tenant: Tenant) -> tuple[time, time]:
    """The tenant's sending window in recipient local time.

    Args:
        tenant: The sending tenant.

    Returns:
        Start (inclusive) and end (exclusive) times.
    """
    return tenant.sending_window_start, tenant.sending_window_end
