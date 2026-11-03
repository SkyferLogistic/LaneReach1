"""Quotes: build, send in the contact's thread, accept or reject, expire.

A quote belongs to a deal. Drafts can be edited; a sent quote is final (duplicate it to offer
new prices) so what the customer received never changes behind their back. Sending goes
through `deliver_email` like a human reply (kill switch, suppression, company freeze and
mailbox capacity apply), in the latest thread with the contact when there is one, from the
thread's agent, with the agent's signature. Status changes publish `quote.status_changed`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import structlog
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.engine.email import (
    choose_sender,
    deliver_email,
    find_reply_thread,
    personalise_for_agent,
)
from lanereach.automation.engine.queue import TaskQueue
from lanereach.automation.engine.services import EngineServices
from lanereach.automation.events.models import EventType
from lanereach.automation.events.service import publish_event
from lanereach.automation.guardrails.base import EmailKind, GuardrailOutcome
from lanereach.core.clock import Clock
from lanereach.core.container import ServiceContainer
from lanereach.core.errors import BusinessRuleViolationError, ConflictError, NotFoundError
from lanereach.core.tenancy import (
    open_system_session,
    open_tenant_session,
    require_session_tenant_id,
)
from lanereach.domain.companies.models import Company
from lanereach.domain.contacts.models import Contact
from lanereach.domain.crm.models import Deal, DealStatus, PipelineStage, StageKind
from lanereach.domain.crm.service import DealService, publish_idle_deals
from lanereach.domain.messages.models import Message
from lanereach.domain.offices.models import Office
from lanereach.domain.quotes.document import render_quote_html, render_quote_text
from lanereach.domain.quotes.models import ChargeType, Quote, QuoteLine, QuoteStatus
from lanereach.domain.senders.models import Sender
from lanereach.tenants.branding import load_branding
from lanereach.tenants.models import Tenant

logger = structlog.get_logger(__name__)

CENT = Decimal("0.01")
MAX_LINES = 40
DEFAULT_VALIDITY_DAYS = 14
# Days before expiry when `quote.expiring` is published.
REMINDER_DAYS = (1, 2, 3, 5, 7)


@dataclass(frozen=True, slots=True)
class LineInput:
    """One charge as typed in the builder."""

    charge_type: ChargeType
    description: str
    quantity: Decimal
    unit: str
    unit_price: Decimal


@dataclass(slots=True)
class QuoteInput:
    """A quote's editable content."""

    title: str | None = None
    currency: str = "USD"
    valid_until: date | None = None
    transit_days: int | None = None
    incoterm: str | None = None
    service_line: str | None = None
    mode: str | None = None
    origin: str | None = None
    destination: str | None = None
    notes: str = ""
    lines: list[LineInput] = field(default_factory=list)


def line_amount(quantity: Decimal, unit_price: Decimal) -> Decimal:
    """Quantity x unit price, rounded half-up to cents (how invoices round)."""
    return (quantity * unit_price).quantize(CENT, rounding=ROUND_HALF_UP)


def quote_total(lines: Sequence[LineInput]) -> Decimal:
    """Sum of the rounded line amounts."""
    return sum((line_amount(line.quantity, line.unit_price) for line in lines), Decimal("0.00"))


class QuoteService:
    """Quotes of the tenant.

    Args:
        session: Tenant session.
        clock: Time source.
        task_queue: Where event processing is enqueued.
    """

    def __init__(self, session: AsyncSession, clock: Clock, task_queue: TaskQueue | None) -> None:
        self._session = session
        self._clock = clock
        self._task_queue = task_queue

    async def require_quote(self, quote_id: uuid.UUID) -> Quote:
        """A quote, or 404."""
        quote = await self._session.get(Quote, quote_id)
        if quote is None:
            raise NotFoundError("Quote not found.")
        return quote

    async def lines_of(self, quote_id: uuid.UUID) -> list[QuoteLine]:
        """Charge lines in order."""
        return list(
            (
                await self._session.scalars(
                    select(QuoteLine)
                    .where(QuoteLine.quote_id == quote_id)
                    .order_by(QuoteLine.position)
                )
            ).all()
        )

    async def list_quotes(
        self,
        *,
        status: QuoteStatus | None = None,
        deal_id: uuid.UUID | None = None,
        contact_id: uuid.UUID | None = None,
        expiring_within_days: int | None = None,
        limit: int = 200,
    ) -> list[Quote]:
        """Quotes, newest first (soonest to expire first with `expiring_within_days`)."""
        statement = select(Quote)
        if status is not None:
            statement = statement.where(Quote.status == status)
        if deal_id is not None:
            statement = statement.where(Quote.deal_id == deal_id)
        if contact_id is not None:
            statement = statement.where(Quote.contact_id == contact_id)
        if expiring_within_days is not None:
            today = self._clock.now().date()
            statement = statement.where(
                Quote.status == QuoteStatus.SENT,
                Quote.valid_until <= today + timedelta(days=expiring_within_days),
            ).order_by(Quote.valid_until)
        else:
            statement = statement.order_by(Quote.created_at.desc())
        return list((await self._session.scalars(statement.limit(limit))).all())

    async def _next_number(self) -> str:
        year = self._clock.now().year
        prefix = f"Q-{year}-"
        latest = await self._session.scalar(
            select(func.max(Quote.number)).where(Quote.number.startswith(prefix))
        )
        sequence = int(latest.rsplit("-", 1)[1]) + 1 if latest else 1
        return f"{prefix}{sequence:04d}"

    def _check(self, data: QuoteInput) -> None:
        if len(data.lines) > MAX_LINES:
            raise BusinessRuleViolationError(f"A quote has at most {MAX_LINES} lines.")
        if data.valid_until and data.valid_until < self._clock.now().date():
            raise BusinessRuleViolationError("The validity date is in the past.")

    async def _replace_lines(self, quote: Quote, lines: Sequence[LineInput]) -> None:
        await self._session.execute(delete(QuoteLine).where(QuoteLine.quote_id == quote.id))
        now = self._clock.now()
        for position, line in enumerate(lines):
            self._session.add(
                QuoteLine(
                    tenant_id=quote.tenant_id,
                    quote_id=quote.id,
                    position=position,
                    charge_type=line.charge_type,
                    description=line.description.strip()[:200],
                    quantity=line.quantity,
                    unit=line.unit.strip()[:20] or "shipment",
                    unit_price=line.unit_price.quantize(CENT),
                    amount=line_amount(line.quantity, line.unit_price),
                    created_at=now,
                )
            )
        quote.total_amount = quote_total(lines)

    async def create_quote(self, deal_id: uuid.UUID, actor: str, data: QuoteInput) -> Quote:
        """Start a draft for a deal (its lane and cargo are copied as defaults).

        Raises:
            NotFoundError: For an unknown deal.
            BusinessRuleViolationError: For too many lines or a past validity date.
        """
        deal = await self._session.get(Deal, deal_id)
        if deal is None:
            raise NotFoundError("Deal not found.")
        self._check(data)
        now = self._clock.now()
        quote = Quote(
            tenant_id=require_session_tenant_id(self._session),
            deal_id=deal.id,
            contact_id=deal.contact_id,
            number=await self._next_number(),
            title=(data.title or deal.title)[:200],
            service_line=data.service_line or deal.service_line.value,
            mode=data.mode or deal.mode,
            incoterm=data.incoterm or deal.incoterm,
            origin=data.origin or deal.origin,
            destination=data.destination or deal.destination,
            currency=data.currency or deal.currency,
            valid_until=data.valid_until or (now + timedelta(days=DEFAULT_VALIDITY_DAYS)).date(),
            transit_days=data.transit_days,
            notes=data.notes.strip()[:4000],
            status=QuoteStatus.DRAFT,
            total_amount=Decimal("0.00"),
            created_by=actor,
            created_at=now,
            updated_at=now,
        )
        self._session.add(quote)
        await self._session.flush()
        await self._replace_lines(quote, data.lines)
        await self._session.flush()
        return quote

    async def update_quote(self, quote_id: uuid.UUID, data: QuoteInput) -> Quote:
        """Change a draft.

        Raises:
            NotFoundError: If missing.
            ConflictError: If it was already sent (duplicate it instead).
        """
        quote = await self.require_quote(quote_id)
        if quote.status is not QuoteStatus.DRAFT:
            raise ConflictError(
                f"{quote.number} was sent; duplicate it to offer new prices so the customer's "
                "copy never changes."
            )
        self._check(data)
        for name in ("title", "service_line", "mode", "incoterm", "origin", "destination"):
            value = getattr(data, name)
            if value is not None:
                setattr(quote, name, value[:200] if name == "title" else value)
        quote.currency = data.currency or quote.currency
        quote.valid_until = data.valid_until or quote.valid_until
        quote.transit_days = data.transit_days
        quote.notes = data.notes.strip()[:4000]
        await self._replace_lines(quote, data.lines)
        quote.updated_at = self._clock.now()
        await self._session.flush()
        return quote

    async def duplicate_quote(self, quote_id: uuid.UUID, actor: str) -> Quote:
        """A new draft with the same content and a fresh number and validity."""
        source = await self.require_quote(quote_id)
        lines = await self.lines_of(source.id)
        return await self.create_quote(
            source.deal_id,
            actor,
            QuoteInput(
                title=source.title,
                currency=source.currency,
                transit_days=source.transit_days,
                incoterm=source.incoterm,
                service_line=source.service_line,
                mode=source.mode,
                origin=source.origin,
                destination=source.destination,
                notes=source.notes,
                lines=[
                    LineInput(
                        line.charge_type,
                        line.description,
                        line.quantity,
                        line.unit,
                        line.unit_price,
                    )
                    for line in lines
                ],
            ),
        )

    async def delete_quote(self, quote_id: uuid.UUID) -> None:
        """Delete a quote and its lines."""
        await self._session.delete(await self.require_quote(quote_id))
        await self._session.flush()

    async def _publish(self, quote: Quote, previous: QuoteStatus) -> None:
        now = self._clock.now()
        await publish_event(
            self._session,
            self._clock,
            event_type=EventType.QUOTE_STATUS_CHANGED,
            contact_id=quote.contact_id,
            payload={
                "quote_id": str(quote.id),
                "deal_id": str(quote.deal_id),
                "number": quote.number,
                "status": quote.status.value,
                "previous": previous.value,
                "total": str(quote.total_amount),
                "currency": quote.currency,
            },
            idempotency_key=f"quote:{quote.id}:{previous.value}->{quote.status.value}:{now.isoformat()}",
            task_queue=self._task_queue,
        )

    async def decide_quote(
        self, quote_id: uuid.UUID, actor: str, *, accepted: bool, note: str | None
    ) -> Quote:
        """Record the customer's answer; an accepted quote sets the deal's value when empty.

        Raises:
            NotFoundError: If missing.
            ConflictError: If it is not a sent (or expired) quote.
        """
        quote = await self.require_quote(quote_id)
        if quote.status not in (QuoteStatus.SENT, QuoteStatus.EXPIRED):
            raise ConflictError("Only a sent quote can be accepted or rejected.")
        previous = quote.status
        now = self._clock.now()
        quote.status = QuoteStatus.ACCEPTED if accepted else QuoteStatus.REJECTED
        quote.decided_at = now
        quote.decision_note = (note or "").strip()[:500] or None
        quote.updated_at = now
        if accepted:
            deal = await self._session.get(Deal, quote.deal_id)
            if deal is not None and deal.value_amount is None:
                await DealService(self._session, self._clock, self._task_queue).update_deal(
                    deal.id, actor, {"value_amount": quote.total_amount, "currency": quote.currency}
                )
        await self._publish(quote, previous)
        await self._session.flush()
        return quote

    async def expire_due_quotes(self) -> int:
        """Mark sent quotes past their validity as expired (daily)."""
        today = self._clock.now().date()
        due = (
            await self._session.scalars(
                select(Quote).where(Quote.status == QuoteStatus.SENT, Quote.valid_until < today)
            )
        ).all()
        for quote in due:
            quote.status = QuoteStatus.EXPIRED
            quote.updated_at = self._clock.now()
            await self._publish(quote, QuoteStatus.SENT)
        await self._session.flush()
        return len(due)

    async def publish_expiry_reminders(self) -> int:
        """Publish `quote.expiring` for sent quotes 1, 2, 3, 5 or 7 days before they expire.

        Runs hourly; the idempotency key (quote and days left) publishes each reminder once.

        Returns:
            Reminders published or already published this run.
        """
        today = self._clock.now().date()
        due = (
            await self._session.scalars(
                select(Quote).where(
                    Quote.status == QuoteStatus.SENT,
                    Quote.valid_until >= today,
                    Quote.valid_until <= today + timedelta(days=max(REMINDER_DAYS)),
                )
            )
        ).all()
        published = 0
        for quote in due:
            days_left = (quote.valid_until - today).days
            if days_left not in REMINDER_DAYS:
                continue
            await publish_event(
                self._session,
                self._clock,
                event_type=EventType.QUOTE_EXPIRING,
                contact_id=quote.contact_id,
                payload={
                    "quote_id": str(quote.id),
                    "deal_id": str(quote.deal_id),
                    "number": quote.number,
                    "days_left": days_left,
                    "valid_until": quote.valid_until.isoformat(),
                },
                idempotency_key=f"quote.expiring:{quote.id}:{quote.valid_until}:{days_left}",
                task_queue=self._task_queue,
            )
            published += 1
        return published

    async def send_quote(
        self,
        services: EngineServices,
        quote_id: uuid.UUID,
        *,
        intro: str | None,
        request_key: str,
        actor: str,
    ) -> Quote:
        """Email the quote to the deal's contact and mark it sent.

        Raises:
            NotFoundError: If the quote or its contact is missing.
            ConflictError: If it was already sent, or a guardrail defers it.
            BusinessRuleViolationError: For an empty quote, no office, or a blocking guardrail.
        """
        quote = await self.require_quote(quote_id)
        if quote.status is not QuoteStatus.DRAFT:
            raise ConflictError(f"{quote.number} was already sent.")
        lines = await self.lines_of(quote.id)
        if not lines:
            raise BusinessRuleViolationError("Add at least one charge before sending.")
        session = self._session
        contact = await session.get(Contact, quote.contact_id)
        if contact is None:
            raise NotFoundError("The quote's contact no longer exists.")
        deal = await session.get(Deal, quote.deal_id)
        office_id = (deal.office_id if deal else None) or contact.office_id
        office = await session.get(Office, office_id) if office_id else None
        if office is None:
            raise BusinessRuleViolationError("The contact has no office to send from.")
        tenant = await session.get(Tenant, contact.tenant_id)
        assert tenant is not None  # noqa: S101 - own tenant
        company = await session.get(Company, contact.company_id) if contact.company_id else None
        now = services.clock.now()
        thread = await find_reply_thread(session, contact)
        agent_id = None
        if thread is not None:
            agent_id = await session.scalar(
                select(Message.sender_id)
                .where(Message.thread_id == thread.thread_id, Message.sender_id.is_not(None))
                .order_by(Message.occurred_at.desc())
                .limit(1)
            )
        sender = await choose_sender(session, office, now, agent_id) or await choose_sender(
            session, office, now, None
        )
        branding = load_branding(tenant.branding)
        company_name = branding.company_name or tenant.name
        # Imported here: the online page builds on this service.
        from lanereach.domain.quotes.online import quote_link

        online_url = quote_link(services.settings, tenant.id, quote.id)
        signature, signature_text = "", ""
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
            signature, signature_text = (
                extras.office_signature or extras.signature,
                extras.signature_text,
            )
        html_body = render_quote_html(
            quote,
            lines,
            branding=branding,
            company_name=company_name,
            contact_name=contact.first_name or contact.display_name,
            customer_company=company.name if company else None,
            intro=intro,
            signature=signature,
            online_url=online_url,
        )
        text_body = render_quote_text(
            quote, lines, contact_name=contact.first_name or "", intro=intro, online_url=online_url
        )
        if signature_text:
            text_body = f"{text_body}\n\n{signature_text}"
        subject = thread.subject if thread else f"Quotation {quote.number}: {quote.title}"
        result = await deliver_email(
            services,
            # A person decided to send this offer now: the rules of an approved reply.
            kind=EmailKind.APPROVED_REPLY,
            tenant=tenant,
            contact=contact,
            company=company,
            office=office,
            sender=sender if isinstance(sender, Sender) else None,
            subject=subject[:300],
            body=text_body,
            html_body=html_body,
            idempotency_key=f"quote:{quote.id}:{request_key}",
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
        quote.status = QuoteStatus.SENT
        quote.sent_at = now
        quote.sent_message_id = result.message.message_id
        quote.updated_at = now
        await self._advance_deal_to_quoted(deal, actor)
        await self._publish(quote, QuoteStatus.DRAFT)
        await session.flush()
        logger.info("quote_sent", quote_id=str(quote.id))
        return quote

    async def _advance_deal_to_quoted(self, deal: Deal | None, actor: str) -> None:
        """Move an open deal to its pipeline's "Quoted" stage when it is still before it."""
        if deal is None or deal.status is not DealStatus.OPEN:
            return
        stages = (
            await self._session.scalars(
                select(PipelineStage)
                .where(PipelineStage.pipeline_id == deal.pipeline_id)
                .order_by(PipelineStage.position)
            )
        ).all()
        current = next((stage for stage in stages if stage.id == deal.stage_id), None)
        quoted = next(
            (
                stage
                for stage in stages
                if stage.kind is StageKind.OPEN and stage.name.casefold() == "quoted"
            ),
            None,
        )
        if current is None or quoted is None or quoted.position <= current.position:
            return
        await DealService(self._session, self._clock, self._task_queue).move_deal(
            deal.id, actor, stage_id=quoted.id
        )


async def expire_quotes_in_all_tenants(container: ServiceContainer) -> dict[str, Any]:
    """Hourly time-based work in every tenant (Celery beat).

    Expires overdue quotes, publishes "quote expiring soon" reminders and "deal idle" events.

    Args:
        container: Application container.

    Returns:
        Tenants processed, quotes expired, reminders, idle deals and tenants that failed.
    """
    async with open_system_session(container.session_factory) as system:
        tenant_ids = list(
            (await system.scalars(text("SELECT tenant_id FROM maintenance_list_tenants()"))).all()
        )
    expired, reminders, idle, errors = 0, 0, 0, 0
    for tenant_id in tenant_ids:
        try:
            async with open_tenant_session(container.session_factory, tenant_id) as session:
                quotes = QuoteService(session, container.clock, container.task_queue)
                expired += await quotes.expire_due_quotes()
                reminders += await quotes.publish_expiry_reminders()
                idle += await publish_idle_deals(session, container.clock, container.task_queue)
        except Exception:
            logger.exception("quote_expiry_failed", tenant_id=str(tenant_id))
            errors += 1
    return {
        "tenants": len(tenant_ids),
        "expired": expired,
        "reminders": reminders,
        "idle_deals": idle,
        "errors": errors,
    }
