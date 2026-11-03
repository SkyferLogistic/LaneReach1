"""Online quotes: the customer opens the quote from its email and accepts or declines it.

The quote email carries a link with a signed token (the tenant and the quote under an HMAC, like
the tracking and unsubscribe links), so the public page needs no login and no lookup across
companies: an altered or made-up token is simply not found. Accepting records the customer's
answer exactly like a person recording it (`QuoteService.decide_quote`), so the deal's value,
the quote events and the workflows that react to them all follow, and the deal's owner is told.
A quote past its validity can no longer be accepted online (prices may have changed).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lanereach.automation.engine.queue import TaskQueue
from lanereach.core.clock import Clock
from lanereach.core.config import Settings
from lanereach.core.errors import ConflictError, NotFoundError
from lanereach.core.tenancy import open_tenant_session
from lanereach.domain.contacts.models import Contact
from lanereach.domain.crm.models import Deal
from lanereach.domain.quotes.models import Quote, QuoteLine, QuoteStatus
from lanereach.domain.quotes.service import QuoteService
from lanereach.integrations.notifications.notifier import NotificationMessage, RecordingNotifier
from lanereach.tenants.branding import load_branding
from lanereach.tenants.models import Tenant

_SIGNATURE_BYTES = 16
_IDS = 32


def _key(settings: Settings) -> bytes:
    # One secret to manage (the unsubscribe secret), a separate key per purpose.
    secret = settings.unsubscribe_signing_secret.get_secret_value().encode()
    return hmac.new(secret, b"lanereach:quote-online", hashlib.sha256).digest()


def create_quote_token(settings: Settings, tenant_id: uuid.UUID, quote_id: uuid.UUID) -> str:
    """A URL-safe signed token naming one quote of one tenant."""
    payload = tenant_id.bytes + quote_id.bytes
    signature = hmac.new(_key(settings), payload, hashlib.sha256).digest()[:_SIGNATURE_BYTES]
    return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")


def read_quote_token(settings: Settings, token: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    """The tenant and quote of a valid token, or None for anything else."""
    if len(token) > 100:
        return None
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, TypeError):
        return None
    if len(raw) != _IDS + _SIGNATURE_BYTES:
        return None
    payload, signature = raw[:_IDS], raw[_IDS:]
    expected = hmac.new(_key(settings), payload, hashlib.sha256).digest()[:_SIGNATURE_BYTES]
    if not hmac.compare_digest(signature, expected):
        return None
    return uuid.UUID(bytes=payload[:16]), uuid.UUID(bytes=payload[16:])


def quote_link(settings: Settings, tenant_id: uuid.UUID, quote_id: uuid.UUID) -> str:
    """The page where the customer views and accepts the quote."""
    token = create_quote_token(settings, tenant_id, quote_id)
    return f"{settings.console_base_url.rstrip('/')}/q/{token}"


@dataclass(frozen=True)
class OnlineQuote:
    """What the customer sees."""

    quote: Quote
    lines: list[QuoteLine]
    company_name: str
    primary_color: str
    contact_first_name: str | None
    expired: bool

    @property
    def can_decide(self) -> bool:
        """Whether the Accept and Decline buttons are shown."""
        return self.quote.status is QuoteStatus.SENT and not self.expired


class OnlineQuoteService:
    """Show a quote to its customer and record their answer.

    Args:
        session_factory: Factory bound to the application role.
        clock: Time source.
        settings: Signing secret.
        task_queue: Where quote events are enqueued.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock,
        settings: Settings,
        task_queue: TaskQueue | None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._settings = settings
        self._task_queue = task_queue

    def _ids(self, token: str) -> tuple[uuid.UUID, uuid.UUID]:
        ids = read_quote_token(self._settings, token)
        if ids is None:
            raise NotFoundError("Quote not found.")
        return ids

    async def view(self, token: str) -> OnlineQuote:
        """The quote behind a link.

        Raises:
            NotFoundError: For an invalid token, or a quote that was deleted or never sent.
        """
        tenant_id, quote_id = self._ids(token)
        async with open_tenant_session(self._session_factory, tenant_id) as session:
            return await self._load(session, tenant_id, quote_id)

    async def decide(
        self, token: str, *, accepted: bool, name: str | None, note: str | None
    ) -> OnlineQuote:
        """Record the customer's answer (the same answer twice changes nothing).

        Raises:
            NotFoundError: For an invalid token or an unknown quote.
            ConflictError: When the quote was already answered the other way, or expired.
        """
        tenant_id, quote_id = self._ids(token)
        async with open_tenant_session(self._session_factory, tenant_id) as session:
            current = await self._load(session, tenant_id, quote_id)
            wanted = QuoteStatus.ACCEPTED if accepted else QuoteStatus.REJECTED
            if current.quote.status is wanted:
                return current
            if current.quote.status in (QuoteStatus.ACCEPTED, QuoteStatus.REJECTED):
                raise ConflictError(f"This quote was already {current.quote.status.value}.")
            if current.expired:
                raise ConflictError(
                    f"This quote expired on {current.quote.valid_until:%d %b %Y}. "
                    "Please reply to the email for an updated offer."
                )
            who = (name or "").strip()[:120] or "the customer"
            text = (note or "").strip()[:450]
            recorded = f"Online by {who}" + (f": {text}" if text else "")
            quote = await QuoteService(session, self._clock, self._task_queue).decide_quote(
                quote_id, f"customer online ({who})"[:200], accepted=accepted, note=recorded
            )
            deal = await session.get(Deal, quote.deal_id)
            verb = "accepted" if accepted else "declined"
            await RecordingNotifier(session, self._clock).notify(
                NotificationMessage(
                    channel="quote",
                    recipient=(deal.owner_email if deal else None) or "team",
                    subject=f"{quote.number} {verb} online"[:300],
                    body=f"{who} {verb} {quote.number} ({quote.title})."
                    + (f" Note: {text}" if text else ""),
                    payload={
                        "quote_id": str(quote.id),
                        "deal_id": str(quote.deal_id),
                        "status": quote.status.value,
                    },
                )
            )
            return await self._load(session, tenant_id, quote_id)

    async def _load(
        self, session: AsyncSession, tenant_id: uuid.UUID, quote_id: uuid.UUID
    ) -> OnlineQuote:
        quote = await session.get(Quote, quote_id)
        if quote is None or quote.status is QuoteStatus.DRAFT:
            raise NotFoundError("Quote not found.")
        quotes = QuoteService(session, self._clock, self._task_queue)
        tenant = await session.get(Tenant, tenant_id)
        branding = load_branding(tenant.branding if tenant else {})
        contact = await session.get(Contact, quote.contact_id)
        today = self._clock.now().date()
        return OnlineQuote(
            quote=quote,
            lines=await quotes.lines_of(quote.id),
            company_name=branding.company_name or (tenant.name if tenant else ""),
            primary_color=branding.primary_color,
            contact_first_name=contact.first_name if contact else None,
            expired=quote.status is QuoteStatus.EXPIRED or quote.valid_until < today,
        )
