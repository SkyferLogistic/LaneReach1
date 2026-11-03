"""The services a running enrollment uses, bundled per unit of work."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.ai.llm.client import LLMClient
from lanereach.automation.engine.queue import TaskQueue
from lanereach.automation.guardrails.base import GuardrailContext
from lanereach.automation.nodes.registry import NodeRegistry
from lanereach.core.clock import Clock
from lanereach.core.config import Settings
from lanereach.domain.calendars.service import CountryCalendarService
from lanereach.integrations.notifications.notifier import Notifier, RecordingNotifier
from lanereach.integrations.notifications.teams import TeamsWebhookNotifier
from lanereach.integrations.sending.dry_run import DryRunSendingProvider
from lanereach.integrations.sending.provider import SendingProvider


@dataclass
class EngineServices:
    """Everything nodes and the executor need, bound to one tenant session.

    Attributes:
        session: Tenant-scoped session of the current unit of work.
        clock: Time source (a `ManualClock` during test-runs).
        settings: Process configuration.
        registry: Node types.
        sending_provider: Where email goes (dry-run in v1).
        notifier: Where notifications go.
        calendars: Country calendar resolver.
        task_queue: Where follow-up work is enqueued after commit; None during test-runs,
            which must not trigger anything outside the simulation.
        llm: LLM client for AI nodes (set from M2; None when AI is unavailable).
    """

    session: AsyncSession
    clock: Clock
    settings: Settings
    registry: NodeRegistry
    sending_provider: SendingProvider
    notifier: Notifier
    calendars: CountryCalendarService
    task_queue: TaskQueue | None
    llm: LLMClient | None = None

    def guardrail_context(self) -> GuardrailContext:
        """Build the guardrail context for the current moment.

        Returns:
            The context.
        """
        return GuardrailContext(
            session=self.session,
            now=self.clock.now(),
            calendars=self.calendars,
            frequency_cap_days=self.settings.frequency_cap_days,
            requires_connected_mailbox=self.settings.sending_provider == "smtp",
            kill_switch_recheck_minutes=self.settings.kill_switch_recheck_minutes,
        )


def build_engine_services(
    *,
    session: AsyncSession,
    clock: Clock,
    settings: Settings,
    registry: NodeRegistry,
    task_queue: TaskQueue | None,
    notifier: Notifier | None = None,
    teams_http_client: httpx.AsyncClient | None = None,
    llm: LLMClient | None = None,
) -> EngineServices:
    """Assemble engine services for a session.

    Args:
        session: Tenant-scoped session.
        clock: Time source.
        settings: Process configuration.
        registry: Node types.
        task_queue: Follow-up work destination (None to disable follow-ups).
        notifier: Override the notifier (tests).
        teams_http_client: HTTP client for Teams webhooks, when configured.
        llm: LLM client for AI nodes.

    Returns:
        The services.
    """
    recording = RecordingNotifier(session, clock)
    resolved_notifier: Notifier = notifier or recording
    if notifier is None and settings.teams_webhook_urls and teams_http_client is not None:
        resolved_notifier = TeamsWebhookNotifier(
            teams_http_client,
            {name: url.get_secret_value() for name, url in settings.teams_webhook_urls.items()},
            recording,
        )
    return EngineServices(
        session=session,
        clock=clock,
        settings=settings,
        registry=registry,
        sending_provider=build_sending_provider(settings, session, clock),
        notifier=resolved_notifier,
        calendars=CountryCalendarService(session),
        task_queue=task_queue,
        llm=llm,
    )


def build_sending_provider(
    settings: Settings, session: AsyncSession, clock: Clock
) -> SendingProvider:
    """The provider the deployment is configured for.

    `dry_run` (the default everywhere) only records email in the outbox; `smtp` queues it for
    delivery through each agent's own mailbox.

    Args:
        settings: Deployment settings.
        session: Tenant session.
        clock: Time source.

    Returns:
        The sending provider.
    """
    if settings.sending_provider == "smtp":
        from lanereach.integrations.sending.smtp import SmtpSendingProvider

        return SmtpSendingProvider(session, clock)
    return DryRunSendingProvider(session, clock)
