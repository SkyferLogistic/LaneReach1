"""Unified inbox: conversations of every agent mailbox, what needs an answer, replying in place.

A conversation is an email thread (`messages.thread_id`). Its summary is computed from the
messages; `conversation_states` only holds what people did (read, done, snoozed, assignee,
labels). Folders:

* **inbox** — has a reply from the contact, not done, not snoozed;
* **unread** — in the inbox with a reply newer than the last time someone read it;
* **awaiting** — in the inbox and the contact wrote last (nobody answered yet);
* **drafts** — an AI reply draft waits in the review queue;
* **snoozed**, **done**, **sent** (only our emails so far) and **all**.

Replies written here go out like approved review-queue replies: through the same guardrails
(kill switch, suppression, company freeze, mailbox capacity) and the `SendingProvider`, in the
thread, from the agent who owns it, with the agent's signature.
"""

from __future__ import annotations

import html
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import String, Uuid, and_, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.ai.review.models import ReviewItem, ReviewKind, ReviewStatus
from lanereach.automation.engine.email import (
    choose_sender,
    deliver_email,
    personalise_for_agent,
    thread_reference,
)
from lanereach.automation.engine.services import EngineServices
from lanereach.automation.guardrails.base import EmailKind, GuardrailOutcome
from lanereach.core.clock import Clock
from lanereach.core.errors import BusinessRuleViolationError, ConflictError, NotFoundError
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.companies.models import Company
from lanereach.domain.contacts.models import Contact
from lanereach.domain.inbox.models import ConversationState
from lanereach.domain.messages.models import Message, MessageDirection
from lanereach.domain.offices.models import Office
from lanereach.domain.senders.models import Sender
from lanereach.tenants.models import Tenant

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
PREVIEW_LENGTH = 160
MAX_LABELS = 10
MAX_LABEL_LENGTH = 40


class Folder(StrEnum):
    """Which conversations to list."""

    INBOX = "inbox"
    UNREAD = "unread"
    AWAITING = "awaiting"
    DRAFTS = "drafts"
    SNOOZED = "snoozed"
    DONE = "done"
    SENT = "sent"
    ALL = "all"


@dataclass(frozen=True, slots=True)
class InboxFilters:
    """Narrowing the list.

    Attributes:
        folder: Which folder.
        sender_id: Conversations of this agent mailbox.
        office_id: Conversations of this office.
        bucket: Conversations with a reply classified in this bucket.
        label: Conversations with this label.
        assignee_email: Conversations assigned to this person.
        search: Contact name/email or subject contains this text.
    """

    folder: Folder = Folder.INBOX
    sender_id: uuid.UUID | None = None
    office_id: uuid.UUID | None = None
    bucket: str | None = None
    label: str | None = None
    assignee_email: str | None = None
    search: str | None = None


@dataclass(slots=True)
class ConversationSummary:
    """One line of the inbox."""

    thread_id: str
    subject: str
    preview: str
    contact_id: uuid.UUID | None
    contact_name: str
    contact_email: str
    company_name: str | None
    country: str | None
    office_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    agent_name: str | None
    service_line: str | None
    last_message_at: datetime
    last_inbound_at: datetime | None
    last_outbound_at: datetime | None
    message_count: int
    inbound_count: int
    last_bucket: str | None
    last_direction: str
    unread: bool
    awaiting_reply: bool
    done: bool
    snoozed_until: datetime | None
    assignee_email: str | None
    pending_draft_id: uuid.UUID | None = None
    labels: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class InboxPage:
    """A page of conversations and the cursor of the next one."""

    items: list[ConversationSummary]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class FolderCounts:
    """Numbers shown next to the folders."""

    inbox: int
    unread: int
    awaiting: int
    drafts: int
    snoozed: int
    done: int


def _threads_subquery() -> Any:
    """One row per thread with its dates and counts."""
    inbound = Message.direction == MessageDirection.INBOUND
    outbound = Message.direction == MessageDirection.OUTBOUND
    return (
        select(
            Message.thread_id.label("thread_id"),
            func.max(Message.occurred_at).label("last_at"),
            func.max(Message.occurred_at).filter(inbound).label("last_in"),
            func.max(Message.occurred_at).filter(outbound).label("last_out"),
            func.count().label("messages"),
            func.count().filter(inbound).label("inbound"),
            # A thread has one contact; min() of the text form picks it inside the GROUP BY.
            func.min(cast(Message.contact_id, String)).label("contact_text"),
        )
        .group_by(Message.thread_id)
        .subquery("threads")
    )


def _pending_draft(thread_column: Any) -> Any:
    return (
        select(ReviewItem.id)
        .join(Message, Message.id == ReviewItem.message_id)
        .where(
            Message.thread_id == thread_column,
            ReviewItem.kind == ReviewKind.REPLY_DRAFT,
            ReviewItem.status == ReviewStatus.PENDING,
        )
        .exists()
    )


def _folder_condition(columns: Any, folder: Folder, now: datetime) -> Any:
    """SQL condition of a folder over the columns of `_filtered_threads`."""
    done = and_(columns.done_at.is_not(None), columns.done_at >= columns.last_at)
    snoozed = and_(columns.snoozed_until.is_not(None), columns.snoozed_until > now)
    in_inbox = and_(columns.inbound > 0, ~done, ~snoozed)
    if folder is Folder.INBOX:
        return in_inbox
    if folder is Folder.UNREAD:
        return and_(in_inbox, or_(columns.read_at.is_(None), columns.read_at < columns.last_in))
    if folder is Folder.AWAITING:
        return and_(in_inbox, columns.last_in > func.coalesce(columns.last_out, _EPOCH))
    if folder is Folder.DRAFTS:
        return and_(~done, _pending_draft(columns.thread_id))
    if folder is Folder.SNOOZED:
        return snoozed
    if folder is Folder.DONE:
        return done
    if folder is Folder.SENT:
        return columns.inbound == 0
    return literal(True)


def _encode_cursor(last_at: datetime, thread_id: str) -> str:
    return f"{last_at.isoformat()}|{thread_id}"


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        stamp, thread_id = cursor.split("|", 1)
        return datetime.fromisoformat(stamp), thread_id
    except ValueError as exc:
        raise BusinessRuleViolationError("Invalid cursor.") from exc


def _filtered_threads(filters: InboxFilters) -> Any:
    """Threads joined with their state, narrowed by the filters (not by folder), as a subquery."""
    threads = _threads_subquery()
    state = ConversationState
    statement = (
        select(
            threads.c.thread_id,
            threads.c.last_at,
            threads.c.last_in,
            threads.c.last_out,
            threads.c.messages,
            threads.c.inbound,
            state.read_at,
            state.done_at,
            state.snoozed_until,
            state.assignee_email,
            state.labels,
        )
        .select_from(threads)
        .outerjoin(state, state.thread_id == threads.c.thread_id)
    )

    def has_message(*conditions: Any) -> Any:
        return (
            select(Message.id).where(Message.thread_id == threads.c.thread_id, *conditions).exists()
        )

    if filters.sender_id is not None:
        statement = statement.where(has_message(Message.sender_id == filters.sender_id))
    if filters.office_id is not None:
        statement = statement.where(has_message(Message.office_id == filters.office_id))
    if filters.bucket:
        bucket = Message.processing_result["classification"]["bucket"].astext
        statement = statement.where(has_message(bucket == filters.bucket))
    if filters.label:
        statement = statement.where(state.labels.contains([filters.label]))
    if filters.assignee_email:
        statement = statement.where(
            func.lower(state.assignee_email) == filters.assignee_email.strip().lower()
        )
    if filters.search and filters.search.strip():
        like = f"%{filters.search.strip()}%"
        contact_matches = (
            select(Contact.id)
            .where(
                Contact.id == cast(threads.c.contact_text, Uuid()),
                or_(
                    Contact.email.ilike(like),
                    Contact.first_name.ilike(like),
                    Contact.last_name.ilike(like),
                ),
            )
            .exists()
        )
        statement = statement.where(or_(contact_matches, has_message(Message.subject.ilike(like))))
    return statement.subquery("conversations")


def _clean_labels(labels: Sequence[str]) -> list[str]:
    cleaned = list(
        dict.fromkeys(label.strip()[:MAX_LABEL_LENGTH] for label in labels if label.strip())
    )
    if len(cleaned) > MAX_LABELS:
        raise BusinessRuleViolationError(f"A conversation has at most {MAX_LABELS} labels.")
    return cleaned


def reply_html(body: str, signature: str) -> str:
    """A human-written plain-text reply as HTML, ending with the signature.

    Args:
        body: The reply as typed (escaped here).
        signature: Trusted signature HTML (the agent's or the office's picture).

    Returns:
        The HTML body.
    """
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body.strip()) if part.strip()]
    blocks = "".join(
        '<p style="margin:0 0 12px 0;">'
        + html.escape(part, quote=False).replace("\n", "<br>")
        + "</p>"
        for part in paragraphs
    )
    return (
        '<div style="font-family:Calibri,Arial,Helvetica,sans-serif;font-size:14px;'
        f'line-height:1.5;color:#1f2933;max-width:640px;">{blocks}{signature}</div>'
    )


class InboxService:
    """The unified inbox.

    Args:
        session: Tenant session.
        clock: Time source.
    """

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def list_conversations(
        self, filters: InboxFilters, *, limit: int = 30, cursor: str | None = None
    ) -> InboxPage:
        """A page of conversations, newest activity first.

        Args:
            filters: Folder and filters.
            limit: Page size.
            cursor: `next_cursor` of the previous page.

        Returns:
            The page.

        Raises:
            BusinessRuleViolationError: For a cursor this service did not issue.
        """
        now = self._clock.now()
        conversations = _filtered_threads(filters)
        statement = select(conversations).where(
            _folder_condition(conversations.c, filters.folder, now)
        )
        if cursor:
            last_at, thread_id = _decode_cursor(cursor)
            statement = statement.where(
                or_(
                    conversations.c.last_at < last_at,
                    and_(
                        conversations.c.last_at == last_at,
                        conversations.c.thread_id > thread_id,
                    ),
                )
            )
        rows = (
            await self._session.execute(
                statement.order_by(conversations.c.last_at.desc(), conversations.c.thread_id).limit(
                    limit + 1
                )
            )
        ).all()
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = (
            _encode_cursor(rows[-1].last_at, rows[-1].thread_id) if more and rows else None
        )
        return InboxPage(await self._summaries(rows, now), next_cursor)

    async def count_folders(self, filters: InboxFilters) -> FolderCounts:
        """How many conversations each folder holds, with the same filters.

        Args:
            filters: Filters (the folder is ignored).

        Returns:
            The counts.
        """
        now = self._clock.now()
        conversations = _filtered_threads(filters)
        folders = (
            Folder.INBOX,
            Folder.UNREAD,
            Folder.AWAITING,
            Folder.DRAFTS,
            Folder.SNOOZED,
            Folder.DONE,
        )
        counts = (
            await self._session.execute(
                select(
                    *(
                        func.count().filter(_folder_condition(conversations.c, folder, now))
                        for folder in folders
                    )
                ).select_from(conversations)
            )
        ).one()
        return FolderCounts(*(int(value or 0) for value in counts))

    async def get_conversation(self, thread_id: str) -> tuple[ConversationSummary, list[Message]]:
        """A conversation and its messages, oldest first.

        Args:
            thread_id: The thread.

        Returns:
            The summary and the messages.

        Raises:
            NotFoundError: If the thread has no messages in this tenant.
        """
        now = self._clock.now()
        conversations = _filtered_threads(InboxFilters(folder=Folder.ALL))
        row = (
            await self._session.execute(
                select(conversations).where(conversations.c.thread_id == thread_id)
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError("Conversation not found.")
        [summary] = await self._summaries([row], now)
        messages = (
            await self._session.scalars(
                select(Message).where(Message.thread_id == thread_id).order_by(Message.occurred_at)
            )
        ).all()
        return summary, list(messages)

    async def _summaries(self, rows: Sequence[Any], now: datetime) -> list[ConversationSummary]:
        thread_ids = [row.thread_id for row in rows]
        if not thread_ids:
            return []
        session = self._session
        by_thread: dict[str, list[Message]] = {}
        for message in (
            await session.scalars(
                select(Message)
                .where(Message.thread_id.in_(thread_ids))
                .order_by(Message.occurred_at)
            )
        ).all():
            by_thread.setdefault(message.thread_id, []).append(message)
        contact_ids = {m.contact_id for thread in by_thread.values() for m in thread}
        contacts = {
            contact.id: contact
            for contact in (
                await session.scalars(select(Contact).where(Contact.id.in_(contact_ids)))
            ).all()
        }
        company_ids = {c.company_id for c in contacts.values() if c.company_id}
        companies: dict[uuid.UUID, str] = {}
        if company_ids:
            companies = {
                company.id: company.name
                for company in (
                    await session.scalars(select(Company).where(Company.id.in_(company_ids)))
                ).all()
            }
        sender_ids = {m.sender_id for thread in by_thread.values() for m in thread if m.sender_id}
        senders: dict[uuid.UUID, Sender] = {}
        if sender_ids:
            senders = {
                sender.id: sender
                for sender in (
                    await session.scalars(select(Sender).where(Sender.id.in_(sender_ids)))
                ).all()
            }
        drafts: dict[str, uuid.UUID] = {
            thread_id: item_id
            for thread_id, item_id in (
                await session.execute(
                    select(Message.thread_id, ReviewItem.id)
                    .join(Message, Message.id == ReviewItem.message_id)
                    .where(
                        Message.thread_id.in_(thread_ids),
                        ReviewItem.kind == ReviewKind.REPLY_DRAFT,
                        ReviewItem.status == ReviewStatus.PENDING,
                    )
                    .order_by(ReviewItem.created_at)
                )
            ).all()
        }
        return [
            self._summary(
                row, by_thread.get(row.thread_id, []), contacts, companies, senders, drafts, now
            )
            for row in rows
        ]

    @staticmethod
    def _summary(
        row: Any,
        thread: list[Message],
        contacts: dict[uuid.UUID, Contact],
        companies: dict[uuid.UUID, str],
        senders: dict[uuid.UUID, Sender],
        drafts: dict[str, uuid.UUID],
        now: datetime,
    ) -> ConversationSummary:
        last = thread[-1] if thread else None
        contact = contacts.get(last.contact_id) if last else None
        agent_id = next((m.sender_id for m in reversed(thread) if m.sender_id), None)
        agent = senders.get(agent_id) if agent_id else None
        last_inbound = next(
            (m for m in reversed(thread) if m.direction is MessageDirection.INBOUND), None
        )
        result = (last_inbound.processing_result or {}) if last_inbound else {}
        classification = result.get("classification") or {}
        snoozed = row.snoozed_until if row.snoozed_until and row.snoozed_until > now else None
        return ConversationSummary(
            thread_id=row.thread_id,
            subject=last.subject if last else "",
            preview=" ".join((last.body or "").split())[:PREVIEW_LENGTH] if last else "",
            contact_id=contact.id if contact else None,
            contact_name=contact.display_name if contact else "",
            contact_email=contact.email if contact else "",
            company_name=(
                companies.get(contact.company_id) if contact and contact.company_id else None
            ),
            country=contact.country if contact else None,
            office_id=next((m.office_id for m in reversed(thread) if m.office_id), None),
            agent_id=agent_id,
            agent_name=agent.display_name if agent else None,
            service_line=agent.service_line.value if agent else None,
            last_message_at=row.last_at,
            last_inbound_at=row.last_in,
            last_outbound_at=row.last_out,
            message_count=int(row.messages),
            inbound_count=int(row.inbound),
            last_bucket=classification.get("bucket"),
            last_direction=last.direction.value if last else MessageDirection.OUTBOUND.value,
            unread=bool(row.last_in and (row.read_at is None or row.read_at < row.last_in)),
            awaiting_reply=bool(row.last_in and row.last_in > (row.last_out or _EPOCH)),
            done=bool(row.done_at and row.done_at >= row.last_at),
            snoozed_until=snoozed,
            assignee_email=row.assignee_email,
            pending_draft_id=drafts.get(row.thread_id),
            labels=list(row.labels or []),
        )

    async def _state(self, thread_id: str) -> ConversationState:
        exists = await self._session.scalar(
            select(Message.id).where(Message.thread_id == thread_id).limit(1)
        )
        if exists is None:
            raise NotFoundError("Conversation not found.")
        state = await self._session.scalar(
            select(ConversationState).where(ConversationState.thread_id == thread_id)
        )
        if state is None:
            now = self._clock.now()
            state = ConversationState(
                tenant_id=require_session_tenant_id(self._session),
                thread_id=thread_id,
                labels=[],
                created_at=now,
                updated_at=now,
            )
            self._session.add(state)
        return state

    async def update_conversation(
        self,
        thread_id: str,
        *,
        read: bool | None = None,
        done: bool | None = None,
        snoozed_until: datetime | None = None,
        unsnooze: bool = False,
        assignee_email: str | None = None,
        unassign: bool = False,
        labels: Sequence[str] | None = None,
    ) -> None:
        """Mark read/unread or done/reopened, snooze, assign or label a conversation.

        Args:
            thread_id: The thread.
            read: True marks read, False unread.
            done: True marks done (and ends a snooze), False reopens.
            snoozed_until: Hide it from the inbox until then.
            unsnooze: End the snooze now.
            assignee_email: Assign it to this person.
            unassign: Remove the assignee.
            labels: Replace the labels.

        Raises:
            NotFoundError: For an unknown thread.
            BusinessRuleViolationError: For a snooze time in the past or too many labels.
        """
        state = await self._state(thread_id)
        now = self._clock.now()
        if read is not None:
            state.read_at = now if read else None
        if done is not None:
            state.done_at = now if done else None
            if done:
                state.snoozed_until = None
        if snoozed_until is not None:
            if snoozed_until <= now:
                raise BusinessRuleViolationError("Snooze until a time in the future.")
            state.snoozed_until = snoozed_until
            # Snoozing reads it: it comes back as unread only if the contact writes again.
            state.read_at = now
        if unsnooze:
            state.snoozed_until = None
        if assignee_email is not None:
            state.assignee_email = assignee_email.strip().lower() or None
        if unassign:
            state.assignee_email = None
        if labels is not None:
            state.labels = _clean_labels(labels)
        state.updated_at = now
        await self._session.flush()

    async def list_labels(self) -> list[str]:
        """Labels in use, alphabetically (for the label picker).

        Returns:
            The labels.
        """
        labels = select(
            func.jsonb_array_elements_text(ConversationState.labels).label("label")
        ).subquery()
        rows: Sequence[Any] = (await self._session.scalars(select(labels.c.label).distinct())).all()
        return sorted((str(label) for label in rows), key=str.lower)

    async def send_reply(
        self,
        services: EngineServices,
        thread_id: str,
        *,
        body: str,
        request_key: str,
    ) -> Message:
        """Send a human-written reply in the thread, from the thread's agent, with a signature.

        Args:
            services: Engine services (provider, guardrails, clock, settings).
            thread_id: The conversation.
            body: Plain-text reply (the signature is added).
            request_key: Unique key of this send (the provider is idempotent on it).

        Returns:
            The sent message.

        Raises:
            NotFoundError: For an unknown thread or a deleted contact.
            BusinessRuleViolationError: When a guardrail blocks it or no office can send.
            ConflictError: When a guardrail defers it (for example the mailbox is at its cap).
        """
        session = self._session
        messages = (
            await session.scalars(
                select(Message).where(Message.thread_id == thread_id).order_by(Message.occurred_at)
            )
        ).all()
        if not messages:
            raise NotFoundError("Conversation not found.")
        last = messages[-1]
        contact = await session.get(Contact, last.contact_id)
        if contact is None:
            raise NotFoundError("The conversation's contact no longer exists.")
        office_id = next((m.office_id for m in reversed(messages) if m.office_id), None)
        office_id = office_id or contact.office_id
        office = await session.get(Office, office_id) if office_id else None
        if office is None:
            raise BusinessRuleViolationError("No office can send in this conversation.")
        thread = await thread_reference(session, thread_id, last.subject)
        assert thread is not None  # noqa: S101 - the thread has messages
        now = services.clock.now()
        agent_id = next(
            (
                m.sender_id
                for m in reversed(messages)
                if m.sender_id and m.direction is MessageDirection.OUTBOUND
            ),
            None,
        )
        # The agent who wrote keeps the thread; another agent of the office if it cannot send.
        sender = await choose_sender(session, office, now, agent_id)
        if sender is None:
            sender = await choose_sender(session, office, now, None)
        tenant = await session.get(Tenant, contact.tenant_id)
        assert tenant is not None  # noqa: S101 - own tenant
        company = await session.get(Company, contact.company_id) if contact.company_id else None
        text_body, html_body = body.strip(), None
        if isinstance(sender, Sender):
            extras = await personalise_for_agent(
                session,
                services.settings,
                tenant=tenant,
                sender=sender,
                template=None,
                country_code=contact.country,
                office=office,
            )
            text_body = f"{text_body}\n\n{extras.signature_text}"
            html_body = reply_html(body, extras.office_signature or extras.signature)
        result = await deliver_email(
            services,
            # A person wrote and sent it now: the same rules as an approved review-queue reply.
            kind=EmailKind.APPROVED_REPLY,
            tenant=tenant,
            contact=contact,
            company=company,
            office=office,
            sender=sender if isinstance(sender, Sender) else None,
            subject=thread.subject[:300],
            body=text_body,
            html_body=html_body,
            idempotency_key=f"inbox:{request_key}",
            thread=thread,
        )
        verdict = result.verdict
        if verdict.outcome is GuardrailOutcome.BLOCK:
            raise BusinessRuleViolationError(
                f"Not sent: {verdict.deciding_guardrail} - {verdict.reason}"
            )
        if verdict.outcome is GuardrailOutcome.DEFER:
            until = verdict.defer_until.isoformat() if verdict.defer_until else "later"
            raise ConflictError(
                f"Not sent yet: {verdict.deciding_guardrail} - {verdict.reason}. "
                f"Try again after {until}."
            )
        assert result.message is not None  # noqa: S101 - sent deliveries are recorded
        state = await self._state(thread_id)
        state.read_at = now
        state.updated_at = now
        await session.flush()
        return result.message
