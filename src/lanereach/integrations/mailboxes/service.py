"""Agent mailboxes: connect and test, deliver queued email over SMTP, read replies over IMAP.

Rules:

* A mailbox password is encrypted at rest and only decrypted in memory to talk to the server.
* Server hosts are checked like webhook URLs: private and internal addresses are refused unless
  the deployment allows them for development (a local test mail server).
* Plain, unencrypted connections (`none`) exist for development only.
* The poller only reads messages that arrived after the mailbox was connected, opens them
  read-only (they stay unread for the agent) and keeps only answers to LaneReach's own emails.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.engine.queue import TaskQueue
from lanereach.core.clock import Clock
from lanereach.core.config import Settings
from lanereach.core.container import ServiceContainer
from lanereach.core.errors import BusinessRuleViolationError, NotFoundError
from lanereach.core.secrets import SecretBox, SecretDecryptionError, build_secret_box
from lanereach.core.tenancy import (
    open_system_session,
    open_tenant_session,
    require_session_tenant_id,
)
from lanereach.domain.senders.models import Sender
from lanereach.integrations.mailboxes.mime import build_outbound_mime, parse_inbound_mail
from lanereach.integrations.mailboxes.models import (
    MailboxPreset,
    MailboxStatus,
    MailSecurity,
    SenderMailbox,
)
from lanereach.integrations.mailboxes.transport import MailGateway, MailServer, MailServerError
from lanereach.integrations.outbound.url_guard import UnsafeUrlError, check_url_is_public
from lanereach.integrations.sending.models import OutboxMessage, OutboxStatus
from lanereach.integrations.sending.provider import ProviderEvent, ProviderEventType
from lanereach.integrations.sending.webhooks import (
    enqueue_inbound_processing,
    ingest_provider_event,
)

logger = structlog.get_logger(__name__)

IMAP_PROVIDER = "imap"
MAX_ATTEMPTS = 5
# Minutes before the next attempt after a temporary failure (then the email is marked failed).
RETRY_MINUTES = (1, 5, 15, 60, 180)
DELIVERY_BATCH = 50
POLL_BATCH = 50


@dataclass(slots=True)
class MailboxInput:
    """A mailbox connection as entered by an admin (`password` None keeps the saved one)."""

    preset: MailboxPreset
    smtp_host: str
    smtp_port: int
    smtp_security: MailSecurity
    imap_host: str
    imap_port: int
    imap_security: MailSecurity
    username: str
    password: str | None


def mail_servers(
    mailbox: SenderMailbox, box: SecretBox, settings: Settings
) -> tuple[MailServer, MailServer]:
    """SMTP and IMAP connection details with the decrypted password.

    Raises:
        SecretDecryptionError: If the password was encrypted with another key.
    """
    password = box.decrypt(mailbox.password_encrypted)
    timeout = float(settings.mail_timeout_seconds)
    return (
        MailServer(
            mailbox.smtp_host,
            mailbox.smtp_port,
            mailbox.smtp_security,
            mailbox.username,
            password,
            timeout,
        ),
        MailServer(
            mailbox.imap_host,
            mailbox.imap_port,
            mailbox.imap_security,
            mailbox.username,
            password,
            timeout,
        ),
    )


class MailboxService:
    """Connect, test and disconnect agent mailboxes.

    Args:
        session: Tenant session.
        clock: Time source.
        settings: Deployment settings (private hosts, timeouts).
        gateway: Mail servers (a fake in tests).
    """

    def __init__(
        self, session: AsyncSession, clock: Clock, settings: Settings, gateway: MailGateway
    ) -> None:
        self._session = session
        self._clock = clock
        self._settings = settings
        self._gateway = gateway
        self._box = build_secret_box(settings)

    async def _sender(self, sender_id: uuid.UUID) -> Sender:
        sender = await self._session.get(Sender, sender_id)
        if sender is None:
            raise NotFoundError("Agent not found.")
        return sender

    async def get_mailbox(self, sender_id: uuid.UUID) -> SenderMailbox | None:
        """The agent's mailbox connection, if any.

        Raises:
            NotFoundError: For an unknown agent.
        """
        await self._sender(sender_id)
        return await self._session.scalar(
            select(SenderMailbox).where(SenderMailbox.sender_id == sender_id)
        )

    async def _check_host(self, host: str, port: int, security: MailSecurity) -> None:
        allow_private = self._settings.integrations_allow_private_urls
        if security is MailSecurity.NONE and not allow_private:
            raise BusinessRuleViolationError(
                "Unencrypted mail connections are only allowed in development."
            )
        try:
            await check_url_is_public(f"https://{host}:{port}", allow_private=allow_private)
        except UnsafeUrlError as exc:
            raise BusinessRuleViolationError(f"{host}: {exc}") from None

    async def save_mailbox(self, sender_id: uuid.UUID, data: MailboxInput) -> SenderMailbox:
        """Connect (or change) the agent's mailbox; it must be tested before it sends.

        Raises:
            NotFoundError: For an unknown agent.
            BusinessRuleViolationError: For an unsafe host, a plain connection outside
                development, or a new connection without a password.
        """
        await self._sender(sender_id)
        await self._check_host(data.smtp_host, data.smtp_port, data.smtp_security)
        await self._check_host(data.imap_host, data.imap_port, data.imap_security)
        mailbox = await self.get_mailbox(sender_id)
        now = self._clock.now()
        if mailbox is None:
            if not data.password:
                raise BusinessRuleViolationError("Enter the mailbox password (or app password).")
            mailbox = SenderMailbox(
                tenant_id=require_session_tenant_id(self._session),
                sender_id=sender_id,
                password_encrypted=self._box.encrypt(data.password),
                created_at=now,
            )
            self._session.add(mailbox)
        elif data.password:
            mailbox.password_encrypted = self._box.encrypt(data.password)
        changed_login = mailbox.imap_host != data.imap_host or mailbox.username != data.username
        mailbox.preset = data.preset.value
        mailbox.smtp_host = data.smtp_host.strip().lower()
        mailbox.smtp_port = data.smtp_port
        mailbox.smtp_security = data.smtp_security
        mailbox.imap_host = data.imap_host.strip().lower()
        mailbox.imap_port = data.imap_port
        mailbox.imap_security = data.imap_security
        mailbox.username = data.username.strip()
        mailbox.status = MailboxStatus.UNVERIFIED
        mailbox.last_error = None
        mailbox.verified_at = None
        if changed_login:
            # Another inbox: start reading from its current end again.
            mailbox.uid_validity = None
            mailbox.last_seen_uid = None
        mailbox.updated_at = now
        await self._session.flush()
        return mailbox

    async def test_mailbox(self, sender_id: uuid.UUID) -> SenderMailbox:
        """Log in to SMTP and IMAP (nothing is sent or read) and record the result.

        Raises:
            NotFoundError: For an unknown agent or when no mailbox is connected.
        """
        mailbox = await self.get_mailbox(sender_id)
        if mailbox is None:
            raise NotFoundError("Connect the mailbox first.")
        now = self._clock.now()
        try:
            smtp, imap = mail_servers(mailbox, self._box, self._settings)
            await self._gateway.check_smtp(smtp)
            await self._gateway.check_imap(imap)
        except SecretDecryptionError:
            mailbox.status = MailboxStatus.ERROR
            mailbox.last_error = (
                "The saved password cannot be read (the encryption key changed). Enter it again."
            )
        except MailServerError as exc:
            mailbox.status = MailboxStatus.ERROR
            mailbox.last_error = str(exc)[:500]
        else:
            mailbox.status = MailboxStatus.VERIFIED
            mailbox.last_error = None
            mailbox.verified_at = now
        mailbox.updated_at = now
        await self._session.flush()
        logger.info("mailbox_tested", sender_id=str(sender_id), status=mailbox.status.value)
        return mailbox

    async def delete_mailbox(self, sender_id: uuid.UUID) -> None:
        """Disconnect the mailbox (the agent stops sending in SMTP mode).

        Raises:
            NotFoundError: For an unknown agent or when no mailbox is connected.
        """
        mailbox = await self.get_mailbox(sender_id)
        if mailbox is None:
            raise NotFoundError("No mailbox is connected.")
        await self._session.delete(mailbox)


async def mailbox_statuses(
    session: AsyncSession, sender_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Mailbox status per agent (agents without a mailbox are absent).

    Args:
        session: Tenant session.
        sender_ids: Agents.

    Returns:
        `verified`, `unverified` or `error` by agent id.
    """
    if not sender_ids:
        return {}
    rows = await session.execute(
        select(SenderMailbox.sender_id, SenderMailbox.status).where(
            SenderMailbox.sender_id.in_(sender_ids)
        )
    )
    return {sender_id: status.value for sender_id, status in rows.all()}


# --- Delivery (SMTP) -----------------------------------------------------------------------


@dataclass(slots=True)
class DeliveryRun:
    """What one delivery run did in a tenant."""

    sent: int = 0
    retried: int = 0
    failed: int = 0


async def deliver_queued_emails(
    session: AsyncSession, clock: Clock, settings: Settings, gateway: MailGateway
) -> DeliveryRun:
    """Send the tenant's due queued emails through their agents' mailboxes.

    Rows are locked (`SKIP LOCKED`) so two workers never send the same email. A temporary failure
    retries after 1, 5, 15, 60 and 180 minutes; a permanent one (refused login or recipient) or
    the fifth failure marks the email `failed` with the reason.

    Args:
        session: Tenant session.
        clock: Time source.
        settings: Settings (timeouts, encryption key).
        gateway: Mail servers.

    Returns:
        Counts of sent, retried and failed emails.
    """
    now = clock.now()
    box = build_secret_box(settings)
    run = DeliveryRun()
    rows = (
        await session.scalars(
            select(OutboxMessage)
            .where(
                OutboxMessage.status == OutboxStatus.QUEUED,
                (OutboxMessage.next_attempt_at.is_(None)) | (OutboxMessage.next_attempt_at <= now),
            )
            .order_by(OutboxMessage.created_at)
            .limit(DELIVERY_BATCH)
            .with_for_update(skip_locked=True)
        )
    ).all()
    mailboxes: dict[uuid.UUID, SenderMailbox | None] = {}
    for row in rows:
        mailbox = None
        if row.sender_id is not None:
            if row.sender_id not in mailboxes:
                mailboxes[row.sender_id] = await session.scalar(
                    select(SenderMailbox).where(SenderMailbox.sender_id == row.sender_id)
                )
            mailbox = mailboxes[row.sender_id]
        row.attempts += 1
        try:
            if mailbox is None or mailbox.status is not MailboxStatus.VERIFIED:
                raise MailServerError(
                    "The agent's mailbox is not connected or not verified.", permanent=False
                )
            smtp, _ = mail_servers(mailbox, box, settings)
            data = build_outbound_mime(row).as_bytes()
            await gateway.send(smtp, row.from_email, [row.to_email], data)
        except SecretDecryptionError:
            _fail(
                row,
                "The mailbox password cannot be read (encryption key changed).",
                now,
                run,
                final=True,
            )
        except MailServerError as exc:
            final = exc.permanent or row.attempts >= MAX_ATTEMPTS
            _fail(row, str(exc), now, run, final=final)
            if mailbox is not None and exc.permanent and "login" in str(exc):
                mailbox.status = MailboxStatus.ERROR
                mailbox.last_error = str(exc)[:500]
                mailbox.updated_at = now
        else:
            row.status = OutboxStatus.SENT
            row.sent_at = clock.now()
            row.error = None
            row.next_attempt_at = None
            run.sent += 1
    if rows:
        logger.info("outbox_delivery_run", sent=run.sent, retried=run.retried, failed=run.failed)
    return run


def _fail(row: OutboxMessage, reason: str, now: datetime, run: DeliveryRun, *, final: bool) -> None:
    row.error = reason[:500]
    if final:
        row.status = OutboxStatus.FAILED
        row.next_attempt_at = None
        run.failed += 1
        logger.warning("outbox_delivery_failed", outbox_id=str(row.id), attempts=row.attempts)
    else:
        delay = RETRY_MINUTES[min(row.attempts, len(RETRY_MINUTES)) - 1]
        row.next_attempt_at = now + timedelta(minutes=delay)
        run.retried += 1


# --- Replies (IMAP) ------------------------------------------------------------------------


@dataclass(slots=True)
class PollRun:
    """What one polling run did in a tenant."""

    mailboxes: int = 0
    stored: int = 0
    skipped: int = 0
    errors: int = 0


def inbound_event(
    mailbox: SenderMailbox, uid_validity: int, uid: int, raw: bytes, now: datetime
) -> ProviderEvent | None:
    """The provider event for a fetched message (None for the agent's own sent copies).

    Args:
        mailbox: The mailbox it came from.
        uid_validity: The inbox's UIDVALIDITY.
        uid: The message UID.
        raw: The message bytes.
        now: When it was read.

    Returns:
        A reply or bounce event.
    """
    mail = parse_inbound_mail(raw)
    event_id = f"imap:{mailbox.id}:{uid_validity}:{uid}"
    if mail.kind == "bounce":
        data: dict[str, Any] = {
            "message_id": mail.message_id,
            "original_message_id": mail.original_message_id,
            "recipient": mail.recipient,
            "bounce_type": mail.bounce_type,
            "diagnostic": mail.diagnostic,
            "subject": mail.subject,
        }
        return ProviderEvent(ProviderEventType.BOUNCE, event_id, now, data)
    if mail.from_email == mailbox.username.lower():
        return None
    return ProviderEvent(
        ProviderEventType.REPLY,
        event_id,
        now,
        {
            "message_id": mail.message_id,
            "in_reply_to": mail.in_reply_to,
            "from_email": mail.from_email,
            "to_email": mail.to_email,
            "subject": mail.subject,
            "body": mail.body,
        },
    )


async def poll_mailboxes(
    session: AsyncSession,
    clock: Clock,
    settings: Settings,
    gateway: MailGateway,
    task_queue: TaskQueue | None,
) -> PollRun:
    """Read new replies and bounces from every verified mailbox of the tenant.

    The first poll of a mailbox only remembers where its inbox ends (no old mail is read).
    Messages that answer no LaneReach email are skipped and not stored.

    Args:
        session: Tenant session.
        clock: Time source.
        settings: Settings.
        gateway: Mail servers.
        task_queue: Where reply processing is enqueued after commit.

    Returns:
        Counts per run.
    """
    box = build_secret_box(settings)
    run = PollRun()
    mailboxes = (
        await session.scalars(
            select(SenderMailbox)
            .where(SenderMailbox.status == MailboxStatus.VERIFIED)
            .with_for_update(skip_locked=True)
        )
    ).all()
    stored: list[uuid.UUID] = []
    for mailbox in mailboxes:
        run.mailboxes += 1
        now = clock.now()
        try:
            _, imap = mail_servers(mailbox, box, settings)
            fetched = await gateway.fetch_new(
                imap, mailbox.uid_validity, mailbox.last_seen_uid, POLL_BATCH
            )
        except (MailServerError, SecretDecryptionError) as exc:
            run.errors += 1
            mailbox.last_error = str(exc)[:500]
            if isinstance(exc, SecretDecryptionError) or getattr(exc, "permanent", False):
                mailbox.status = MailboxStatus.ERROR
            mailbox.last_polled_at = now
            continue
        for uid, raw in fetched.messages:
            event = inbound_event(mailbox, fetched.uid_validity, uid, raw, now)
            if event is None:
                run.skipped += 1
                continue
            try:
                async with session.begin_nested():
                    result = await ingest_provider_event(
                        session, clock, provider=IMAP_PROVIDER, event=event
                    )
            except Exception:
                # An unreadable message must not stop the inbox: skip it, never retry it forever.
                logger.exception(
                    "mailbox_message_ingest_failed", mailbox_id=str(mailbox.id), uid=uid
                )
                run.skipped += 1
                continue
            if result.message_id is not None and result.skipped_reason is None:
                stored.append(result.message_id)
                run.stored += 1
            else:
                run.skipped += 1
        mailbox.uid_validity = fetched.uid_validity
        mailbox.last_seen_uid = fetched.max_uid
        mailbox.last_polled_at = now
        mailbox.last_error = None
    if task_queue is not None:
        enqueue_inbound_processing(session, task_queue, stored)
    return run


# --- Every tenant (Celery beat) ------------------------------------------------------------


async def _tenant_ids(container: ServiceContainer) -> list[uuid.UUID]:
    async with open_system_session(container.session_factory) as system:
        return list(
            (await system.scalars(text("SELECT tenant_id FROM maintenance_list_tenants()"))).all()
        )


async def deliver_outbox_in_all_tenants(container: ServiceContainer) -> dict[str, int]:
    """Deliver due queued emails in every tenant (Celery beat)."""
    totals = {"sent": 0, "retried": 0, "failed": 0, "errors": 0}
    for tenant_id in await _tenant_ids(container):
        try:
            async with open_tenant_session(container.session_factory, tenant_id) as session:
                run = await deliver_queued_emails(
                    session, container.clock, container.settings, container.mail
                )
        except Exception:
            logger.exception("outbox_delivery_tenant_failed", tenant_id=str(tenant_id))
            totals["errors"] += 1
            continue
        totals["sent"] += run.sent
        totals["retried"] += run.retried
        totals["failed"] += run.failed
    return totals


async def poll_mailboxes_in_all_tenants(container: ServiceContainer) -> dict[str, int]:
    """Read new replies from every tenant's verified mailboxes (Celery beat)."""
    totals = {"mailboxes": 0, "stored": 0, "skipped": 0, "errors": 0}
    for tenant_id in await _tenant_ids(container):
        try:
            async with open_tenant_session(container.session_factory, tenant_id) as session:
                run = await poll_mailboxes(
                    session,
                    container.clock,
                    container.settings,
                    container.mail,
                    container.task_queue,
                )
        except Exception:
            logger.exception("mailbox_poll_tenant_failed", tenant_id=str(tenant_id))
            totals["errors"] += 1
            continue
        totals["mailboxes"] += run.mailboxes
        totals["stored"] += run.stored
        totals["skipped"] += run.skipped
        totals["errors"] += run.errors
    return totals
