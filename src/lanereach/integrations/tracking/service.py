"""Recording opens and clicks: bot filtering, counters, events and waking engagement waits."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.engine.queue import TaskQueue
from lanereach.automation.enrollments.models import Enrollment, EnrollmentStatus
from lanereach.automation.events.models import EventType
from lanereach.automation.events.service import publish_event
from lanereach.core.clock import Clock
from lanereach.core.tenancy import require_session_tenant_id, run_after_commit
from lanereach.domain.messages.models import Message
from lanereach.integrations.tracking.models import EmailEvent, EmailEventKind
from lanereach.integrations.tracking.tokens import (
    ENGAGEMENT_WAIT_PREFIX,
    TrackingKind,
    TrackingTarget,
)

logger = structlog.get_logger(__name__)

# Mail security gateways and link checkers fetch every link and image of an email. They are
# recorded but never counted. Apple Mail Privacy Protection loads images through a proxy that
# looks like a normal client; its opens cannot be told apart and count as opens.
_BOT_AGENTS = re.compile(
    r"bot|crawl|spider|scan|python|curl|wget|go-http-client|java/|okhttp|libwww|headless|"
    r"phantom|barracuda|mimecast|proofpoint|symantec|forcepoint|trendmicro|sophos|safelinks|"
    r"linkcheck|facebookexternalhit|preview",
    re.IGNORECASE,
)
# Scanners act within seconds of delivery; people do not.
_TOO_FAST = {TrackingKind.OPEN: timedelta(seconds=5), TrackingKind.CLICK: timedelta(seconds=10)}


def classify_client(user_agent: str | None) -> str:
    """A short, non-identifying name of the client (stored instead of the full user agent).

    Args:
        user_agent: The request's User-Agent header.

    Returns:
        For example `outlook`, `gmail-proxy`, `apple-mail`, `browser`, `bot` or `unknown`.
    """
    agent = (user_agent or "").lower()
    if not agent:
        return "unknown"
    if _BOT_AGENTS.search(agent):
        return "bot"
    if "googleimageproxy" in agent:
        return "gmail-proxy"
    if "outlook" in agent or "microsoft office" in agent:
        return "outlook"
    if "thunderbird" in agent:
        return "thunderbird"
    if "applewebkit" in agent and "safari" not in agent:
        # Apple Mail (and other WebKit mail apps) do not claim to be Safari.
        return "apple-mail"
    return "browser" if "mozilla" in agent else "unknown"


def is_suspected_bot(
    kind: TrackingKind, user_agent: str | None, sent_at: datetime, now: datetime
) -> bool:
    """Whether an open or click looks automated.

    Args:
        kind: Open or click.
        user_agent: The request's User-Agent header.
        sent_at: When the email was sent.
        now: When the pixel or link was loaded.

    Returns:
        True for scanners, prefetchers and anything within seconds of sending.
    """
    if not user_agent or _BOT_AGENTS.search(user_agent):
        return True
    return now - sent_at < _TOO_FAST[kind]


async def record_email_engagement(
    session: AsyncSession,
    clock: Clock,
    target: TrackingTarget,
    *,
    user_agent: str | None,
    task_queue: TaskQueue | None,
) -> Message | None:
    """Record an open or click of a tracked email.

    The first human open (or click) of a message publishes `email.opened` (or
    `email.clicked`) and wakes an enrollment waiting in `wait_for_engagement`. A click also
    counts as an open when the pixel was blocked.

    Args:
        session: Tenant session of the token's tenant.
        clock: Time source.
        target: The verified token.
        user_agent: The request's User-Agent header (only a client class is stored).
        task_queue: Where to enqueue event processing and the enrollment wake-up.

    Returns:
        The message, or None when no message has the token's tracking id.
    """
    message = await session.scalar(select(Message).where(Message.tracking_id == target.tracking_id))
    if message is None:
        return None
    now = clock.now()
    bot = is_suspected_bot(target.kind, user_agent, message.occurred_at, now)
    click = target.kind is TrackingKind.CLICK
    session.add(
        EmailEvent(
            tenant_id=require_session_tenant_id(session),
            message_id=message.id,
            kind=EmailEventKind.CLICKED if click else EmailEventKind.OPENED,
            url=target.url[:1500] if target.url else None,
            occurred_at=now,
            suspected_bot=bot,
            client=classify_client(user_agent),
            created_at=now,
        )
    )
    if bot:
        await session.flush()
        return message
    first_open = message.first_opened_at is None
    first_click = click and message.first_clicked_at is None
    if click:
        message.click_count += 1
        message.first_clicked_at = message.first_clicked_at or now
    else:
        message.open_count += 1
    message.first_opened_at = message.first_opened_at or now
    payload = {
        "message_id": message.message_id,
        "thread_id": message.thread_id,
        "subject": message.subject,
        "enrollment_id": str(message.enrollment_id) if message.enrollment_id else None,
    }
    if first_open:
        await publish_event(
            session,
            clock,
            event_type=EventType.EMAIL_OPENED,
            contact_id=message.contact_id,
            payload=payload,
            idempotency_key=f"email.opened:{message.id}",
            task_queue=task_queue,
        )
    if first_click:
        await publish_event(
            session,
            clock,
            event_type=EventType.EMAIL_CLICKED,
            contact_id=message.contact_id,
            payload={**payload, "url": target.url},
            idempotency_key=f"email.clicked:{message.id}",
            task_queue=task_queue,
        )
    if first_open or first_click:
        await _wake_engagement_wait(session, message, now, task_queue)
    await session.flush()
    return message


async def _wake_engagement_wait(
    session: AsyncSession, message: Message, now: datetime, task_queue: TaskQueue | None
) -> None:
    if message.enrollment_id is None:
        return
    enrollment = await session.get(Enrollment, message.enrollment_id)
    if enrollment is None or enrollment.status is not EnrollmentStatus.WAITING:
        return
    if f"{ENGAGEMENT_WAIT_PREFIX}{enrollment.current_node_id}" not in enrollment.variables:
        return
    enrollment.next_run_at = now
    enrollment.updated_at = now
    logger.info("engagement_wait_woken", enrollment_id=str(enrollment.id))
    if task_queue is not None:
        tenant_id, enrollment_id = enrollment.tenant_id, enrollment.id

        async def wake() -> None:
            await task_queue.enqueue_advance(tenant_id, enrollment_id)

        run_after_commit(session, wake)
