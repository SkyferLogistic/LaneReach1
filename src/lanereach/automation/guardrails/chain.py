"""The guardrail chain and `evaluate_send_guardrails`."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import structlog

from lanereach.automation.guardrails.base import (
    Guardrail,
    GuardrailContext,
    GuardrailDecision,
    GuardrailOutcome,
    OutboundEmail,
)
from lanereach.automation.guardrails.business_days import BusinessDaysGuardrail
from lanereach.automation.guardrails.campaign_schedule import CampaignScheduleGuardrail
from lanereach.automation.guardrails.company_freeze import CompanyFreezeGuardrail
from lanereach.automation.guardrails.company_rotation import CompanyRotationGuardrail
from lanereach.automation.guardrails.email_verification import EmailVerificationGuardrail
from lanereach.automation.guardrails.frequency_cap import FrequencyCapGuardrail
from lanereach.automation.guardrails.kill_switch import KillSwitchGuardrail
from lanereach.automation.guardrails.mailbox_capacity import MailboxCapacityGuardrail
from lanereach.automation.guardrails.mailbox_connected import MailboxConnectedGuardrail
from lanereach.automation.guardrails.sending_window import SendingWindowGuardrail
from lanereach.automation.guardrails.suppression import SuppressionGuardrail

logger = structlog.get_logger(__name__)

# Order matters (spec §6.6): pause first, then permanent blocks, then timing rules.
DEFAULT_CHAIN: tuple[Guardrail, ...] = (
    KillSwitchGuardrail(),
    SuppressionGuardrail(),
    EmailVerificationGuardrail(),
    CompanyFreezeGuardrail(),
    FrequencyCapGuardrail(),
    CompanyRotationGuardrail(),
    CampaignScheduleGuardrail(),
    BusinessDaysGuardrail(),
    SendingWindowGuardrail(),
    MailboxConnectedGuardrail(),
    MailboxCapacityGuardrail(),
)


@dataclass(frozen=True)
class SendVerdict:
    """The chain's final decision plus every individual decision taken."""

    outcome: GuardrailOutcome
    reason: str
    defer_until: datetime | None
    decisions: list[GuardrailDecision] = field(default_factory=list)

    @property
    def deciding_guardrail(self) -> str | None:
        """Name of the guardrail that deferred or blocked, if any."""
        for decision in self.decisions:
            if decision.outcome is not GuardrailOutcome.ALLOW:
                return decision.guardrail
        return None


async def evaluate_send_guardrails(
    context: GuardrailContext,
    email: OutboundEmail,
    chain: Sequence[Guardrail] = DEFAULT_CHAIN,
) -> SendVerdict:
    """Decide whether an email may be sent now.

    Guardrails run in order and the chain stops at the first `block` or `defer`: a deferred
    email is evaluated again in full when it wakes, so later rules see the new time.

    Args:
        context: Session, time and calendars.
        email: The email to judge.
        chain: Guardrails to apply (tests may pass a subset).

    Returns:
        The verdict with the trail of decisions (stored on the enrollment step).
    """
    decisions: list[GuardrailDecision] = []
    for guardrail in chain:
        if email.kind not in guardrail.applies_to:
            continue
        decision = await guardrail.evaluate(context, email)
        decisions.append(decision)
        if decision.outcome is not GuardrailOutcome.ALLOW:
            logger.info(
                "guardrail_stopped_email",
                guardrail=decision.guardrail,
                outcome=decision.outcome.value,
                contact_id=str(email.contact.id),
            )
            return SendVerdict(decision.outcome, decision.reason, decision.defer_until, decisions)
    return SendVerdict(GuardrailOutcome.ALLOW, "all guardrails passed", None, decisions)
