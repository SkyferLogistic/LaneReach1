"""Read-only tools of the data assistant: deals, quotes, contacts, rates, inbox, activity, help.

Each tool runs in the caller's tenant session (row-level security keeps other companies out),
returns at most ten small records, each with a `ref` the answer may cite (`deal:<id>`), and a
`count` of everything that matched. No tool returns an email body or changes anything.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.ai.review.models import ReviewItem, ReviewStatus
from lanereach.assistant.embeddings import EmbeddingsProvider
from lanereach.assistant.index import ensure_help_index
from lanereach.assistant.schemas import AgentArguments
from lanereach.assistant.search import search_help
from lanereach.automation.nodes.registry import NodeRegistry
from lanereach.core.clock import Clock
from lanereach.domain.companies.models import Company
from lanereach.domain.contacts.models import Contact
from lanereach.domain.crm.models import Deal, DealStatus, PipelineStage
from lanereach.domain.inbox.service import Folder, InboxFilters, InboxService
from lanereach.domain.intelligence.follow_ups import find_quiet_deals
from lanereach.domain.messages.models import Message, MessageDirection
from lanereach.domain.pricing.matching import ShipmentQuery, find_rate_options
from lanereach.domain.quotes.models import Quote, QuoteStatus

MAX_ITEMS = 10
DEFAULT_ITEMS = 8


@dataclass(frozen=True)
class ToolSpec:
    """What the model is told about a tool."""

    description: str
    arguments: str


TOOL_SPECS: dict[str, ToolSpec] = {
    "find_deals": ToolSpec(
        "Deals in the pipeline, hottest first: title, stage, lead score, lane, value, company, "
        "days since the last change.",
        "query (words in the title, company, contact or commodity), status (open|won|lost, "
        "default open), origin, destination, min_score (0-100; 70 = hot), quiet_days (no "
        "activity for at least this many days), limit",
    ),
    "find_quotes": ToolSpec(
        "Quotes: number, status, total, valid until, lane, contact.",
        "query (number or title words), status (draft|sent|accepted|rejected|expired), origin, "
        "destination, within_days (sent quotes that expire within this many days), limit",
    ),
    "find_contacts": ToolSpec(
        "Contacts: name, email, company, stage, country, language.",
        "query (name, email or company), limit",
    ),
    "find_rates": ToolSpec(
        "The best rate options from the company's own rate sheets for a lane (cheapest first).",
        "origin, destination (city, port or UN/LOCODE), mode (ocean|air|road|rail), equipment "
        "(for example 40HC)",
    ),
    "inbox_waiting": ToolSpec(
        "Inbox conversations waiting for our answer, oldest first.",
        "limit",
    ),
    "activity_summary": ToolSpec(
        "Counts for the last days: new deals, won, lost, quotes sent, accepted, rejected, "
        "replies received, open deals, items waiting in the review queue.",
        "within_days (default 7, at most 90)",
    ),
    "search_help": ToolSpec(
        "How LaneReach works: the help sections matching a question about using LaneReach.",
        "query",
    ),
}


@dataclass(frozen=True)
class ToolRecord:
    """A record an answer can point to."""

    ref: str
    kind: str
    label: str
    url: str


@dataclass
class ToolResult:
    """What one tool call returned."""

    tool: str
    arguments: dict[str, Any]
    count: int
    items: list[dict[str, Any]]
    records: list[ToolRecord] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        """`5 deals`, `no quotes`: for the steps shown with the answer."""
        noun = {
            "find_deals": "deal",
            "find_quotes": "quote",
            "find_contacts": "contact",
            "find_rates": "rate option",
            "inbox_waiting": "conversation",
            "search_help": "help section",
        }.get(self.tool)
        if noun is None:
            return "activity counts"
        if self.count == 0:
            return f"no {noun}s"
        return f"{self.count} {noun}{'' if self.count == 1 else 's'}"

    def for_prompt(self) -> dict[str, Any]:
        """The result as the model sees it."""
        data: dict[str, Any] = {"tool": self.tool, "arguments": self.arguments}
        if self.totals:
            data["counts"] = self.totals
        else:
            data["count"] = self.count
            data["items"] = self.items
        return data


def _limit(args: AgentArguments) -> int:
    return max(1, min(args.limit or DEFAULT_ITEMS, MAX_ITEMS))


def _money(amount: Decimal | None, currency: str) -> str | None:
    return None if amount is None else f"{amount:.2f} {currency}"


def _lane(origin: str | None, destination: str | None) -> str | None:
    return f"{origin or '?'} -> {destination or '?'}" if origin or destination else None


def _like(text: str) -> str:
    cleaned = text.strip().replace("%", "").replace("_", " ")[:80]
    return f"%{cleaned}%"


class AgentTools:
    """The tools, bound to a tenant session.

    Args:
        session: Tenant session.
        clock: Time source.
        registry: Node registry (help search indexes the step reference).
        embeddings: Embeddings provider for help search, or None.
        quiet_days: The company's "quiet deal" threshold, the default for `quiet_days`.
    """

    def __init__(
        self,
        session: AsyncSession,
        clock: Clock,
        registry: NodeRegistry,
        embeddings: EmbeddingsProvider | None,
        *,
        quiet_days: int,
    ) -> None:
        self._session = session
        self._clock = clock
        self._registry = registry
        self._embeddings = embeddings
        self._quiet_days = quiet_days

    async def run(self, tool: str, args: AgentArguments) -> ToolResult:
        """Run one tool.

        Raises:
            ValueError: For an unknown tool.
        """
        handlers = {
            "find_deals": self.find_deals,
            "find_quotes": self.find_quotes,
            "find_contacts": self.find_contacts,
            "find_rates": self.find_rates,
            "inbox_waiting": self.inbox_waiting,
            "activity_summary": self.activity_summary,
            "search_help": self.search_help,
        }
        if tool not in handlers:
            raise ValueError(f"unknown tool {tool}")
        result = await handlers[tool](args)
        result.arguments = args.model_dump(exclude_none=True)
        return result

    async def find_deals(self, args: AgentArguments) -> ToolResult:
        """Deals by status, lane, score and quietness."""
        now = self._clock.now()
        status = args.status if args.status in {"open", "won", "lost"} else "open"
        statement = (
            select(Deal, PipelineStage.name, Contact, Company.name)
            .join(PipelineStage, PipelineStage.id == Deal.stage_id)
            .join(Contact, Contact.id == Deal.contact_id)
            .outerjoin(Company, Company.id == Deal.company_id)
            .where(Deal.status == DealStatus(status))
        )
        if args.query:
            pattern = _like(args.query)
            statement = statement.where(
                or_(
                    Deal.title.ilike(pattern),
                    Deal.commodity.ilike(pattern),
                    Company.name.ilike(pattern),
                    Contact.first_name.ilike(pattern),
                    Contact.last_name.ilike(pattern),
                    Contact.email.ilike(pattern),
                )
            )
        if args.origin:
            statement = statement.where(Deal.origin.ilike(_like(args.origin)))
        if args.destination:
            statement = statement.where(Deal.destination.ilike(_like(args.destination)))
        if args.min_score is not None:
            statement = statement.where(Deal.score >= args.min_score)
        quiet: dict[uuid.UUID, int] = {}
        if args.quiet_days is not None:
            quiet = {
                item.deal.id: item.days_quiet
                for item in await find_quiet_deals(self._session, now, args.quiet_days)
            }
            statement = statement.where(Deal.id.in_(list(quiet) or [uuid.uuid4()]))
        count = int(
            await self._session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        rows = (
            await self._session.execute(
                statement.order_by(Deal.score.desc().nulls_last(), Deal.updated_at.desc()).limit(
                    _limit(args)
                )
            )
        ).all()
        items, records = [], []
        for deal, stage, contact, company in rows:
            ref = f"deal:{deal.id}"
            items.append(
                {
                    "ref": ref,
                    "title": deal.title,
                    "stage": stage,
                    "status": deal.status.value,
                    "score": deal.score,
                    "lane": _lane(deal.origin, deal.destination),
                    "mode": deal.mode,
                    "value": _money(deal.value_amount, deal.currency),
                    "company": company,
                    "contact": contact.display_name,
                    "days_since_change": (now - deal.updated_at).days,
                    "days_quiet": quiet.get(deal.id),
                    "owner": deal.owner_email,
                }
            )
            records.append(ToolRecord(ref, "deal", deal.title, f"/pipeline?deal={deal.id}"))
        return ToolResult("find_deals", {}, count, items, records)

    async def find_quotes(self, args: AgentArguments) -> ToolResult:
        """Quotes by status, lane and expiry."""
        today = self._clock.now().date()
        statement = select(Quote, Contact).join(Contact, Contact.id == Quote.contact_id)
        if args.status in {status.value for status in QuoteStatus}:
            statement = statement.where(Quote.status == QuoteStatus(args.status))
        if args.query:
            pattern = _like(args.query)
            statement = statement.where(
                or_(Quote.number.ilike(pattern), Quote.title.ilike(pattern))
            )
        if args.origin:
            statement = statement.where(Quote.origin.ilike(_like(args.origin)))
        if args.destination:
            statement = statement.where(Quote.destination.ilike(_like(args.destination)))
        if args.within_days is not None:
            statement = statement.where(
                Quote.status == QuoteStatus.SENT,
                Quote.valid_until >= today,
                Quote.valid_until <= today + timedelta(days=max(0, args.within_days)),
            )
        count = int(
            await self._session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        rows = (
            await self._session.execute(
                statement.order_by(Quote.updated_at.desc()).limit(_limit(args))
            )
        ).all()
        items, records = [], []
        for quote, contact in rows:
            ref = f"quote:{quote.id}"
            items.append(
                {
                    "ref": ref,
                    "number": quote.number,
                    "title": quote.title,
                    "status": quote.status.value,
                    "total": _money(quote.total_amount, quote.currency),
                    "valid_until": quote.valid_until.isoformat(),
                    "lane": _lane(quote.origin, quote.destination),
                    "contact": contact.display_name,
                    "sent_on": quote.sent_at.date().isoformat() if quote.sent_at else None,
                }
            )
            records.append(
                ToolRecord(
                    ref, "quote", f"{quote.number} {quote.title}", f"/quotes?quote={quote.id}"
                )
            )
        return ToolResult("find_quotes", {}, count, items, records)

    async def find_contacts(self, args: AgentArguments) -> ToolResult:
        """Contacts by name, email or company."""
        statement = select(Contact, Company.name).outerjoin(
            Company, Company.id == Contact.company_id
        )
        if args.query:
            pattern = _like(args.query)
            statement = statement.where(
                or_(
                    Contact.first_name.ilike(pattern),
                    Contact.last_name.ilike(pattern),
                    Contact.email.ilike(pattern),
                    Company.name.ilike(pattern),
                )
            )
        count = int(
            await self._session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        rows = (
            await self._session.execute(
                statement.order_by(Contact.updated_at.desc()).limit(_limit(args))
            )
        ).all()
        items, records = [], []
        for contact, company in rows:
            ref = f"contact:{contact.id}"
            items.append(
                {
                    "ref": ref,
                    "name": contact.display_name,
                    "email": contact.email,
                    "company": company,
                    "stage": contact.stage.value,
                    "country": contact.country,
                    "language": contact.language,
                }
            )
            records.append(
                ToolRecord(ref, "contact", contact.display_name, f"/contacts/{contact.id}")
            )
        return ToolResult("find_contacts", {}, count, items, records)

    async def find_rates(self, args: AgentArguments) -> ToolResult:
        """Rate options for a lane from the company's rate sheets."""
        options = await find_rate_options(
            self._session,
            ShipmentQuery(
                origin=args.origin,
                destination=args.destination,
                mode=args.mode,
                equipment=args.equipment,
                on_date=self._clock.now().date(),
            ),
            limit=min(_limit(args), 5),
        )
        items, records = [], []
        for option in options:
            ref = f"rate:{option.sheet_id}:{option.tab}"[:120]
            items.append(
                {
                    "ref": ref,
                    "sheet": f"{option.sheet_name} / {option.tab}",
                    "carrier": option.carrier,
                    "lane": _lane(option.origin, option.destination),
                    "equipment": option.equipment,
                    "total": _money(option.total, option.currency),
                    "valid_to": option.valid_to.isoformat() if option.valid_to else None,
                    "transit_days": option.transit_days,
                }
            )
            records.append(
                ToolRecord(ref, "rate", f"{option.sheet_name} / {option.tab}", "/pricing")
            )
        return ToolResult("find_rates", {}, len(options), items, records)

    async def inbox_waiting(self, args: AgentArguments) -> ToolResult:
        """Conversations waiting for our answer."""
        page = await InboxService(self._session, self._clock).list_conversations(
            InboxFilters(folder=Folder.AWAITING), limit=MAX_ITEMS * 3
        )
        now = self._clock.now()
        waiting = sorted(page.items, key=lambda item: item.last_inbound_at or item.last_message_at)
        items, records = [], []
        for item in waiting[: _limit(args)]:
            ref = f"thread:{item.thread_id}"
            since = item.last_inbound_at or item.last_message_at
            items.append(
                {
                    "ref": ref,
                    "subject": item.subject,
                    "contact": item.contact_name,
                    "company": item.company_name,
                    "kind_of_reply": item.last_bucket,
                    "days_waiting": (now - since).days,
                }
            )
            records.append(
                ToolRecord(
                    ref,
                    "conversation",
                    item.subject or item.contact_name,
                    f"/inbox?thread={item.thread_id}",
                )
            )
        return ToolResult("inbox_waiting", {}, len(page.items), items, records)

    async def activity_summary(self, args: AgentArguments) -> ToolResult:
        """Counts of what happened in the last days."""
        days = max(1, min(args.within_days or 7, 90))
        since = self._clock.now() - timedelta(days=days)

        async def count(statement: Select[Any]) -> int:
            return int(await self._session.scalar(statement) or 0)

        totals = {
            "days": days,
            "new_deals": await count(
                select(func.count()).select_from(Deal).where(Deal.created_at >= since)
            ),
            "deals_won": await count(
                select(func.count()).select_from(Deal).where(Deal.won_at >= since)
            ),
            "deals_lost": await count(
                select(func.count()).select_from(Deal).where(Deal.lost_at >= since)
            ),
            "open_deals": await count(
                select(func.count()).select_from(Deal).where(Deal.status == DealStatus.OPEN)
            ),
            "quotes_sent": await count(
                select(func.count()).select_from(Quote).where(Quote.sent_at >= since)
            ),
            "quotes_accepted": await count(
                select(func.count())
                .select_from(Quote)
                .where(Quote.status == QuoteStatus.ACCEPTED, Quote.decided_at >= since)
            ),
            "quotes_rejected": await count(
                select(func.count())
                .select_from(Quote)
                .where(Quote.status == QuoteStatus.REJECTED, Quote.decided_at >= since)
            ),
            "replies_received": await count(
                select(func.count())
                .select_from(Message)
                .where(Message.direction == MessageDirection.INBOUND, Message.occurred_at >= since)
            ),
            "waiting_in_review_queue": await count(
                select(func.count())
                .select_from(ReviewItem)
                .where(ReviewItem.status == ReviewStatus.PENDING)
            ),
        }
        return ToolResult("activity_summary", {}, 1, [], [], totals)

    async def search_help(self, args: AgentArguments) -> ToolResult:
        """Help sections about using LaneReach."""
        query = (args.query or "").strip()
        if not query:
            return ToolResult("search_help", {}, 0, [])
        await ensure_help_index(self._session, self._clock, self._registry, self._embeddings)
        found = await search_help(self._session, query, self._embeddings)
        items, records = [], []
        for hit in found.hits[:4]:
            ref = f"help:{hit.key}"
            items.append(
                {"ref": ref, "title": hit.title, "heading": hit.heading, "text": hit.content[:700]}
            )
            records.append(ToolRecord(ref, "help", f"{hit.title} > {hit.heading}", hit.url))
        return ToolResult("search_help", {}, len(items), items, records)


def today_for_prompt(day: date) -> str:
    """Today's date as the model sees it (also allowed in answers)."""
    return day.isoformat()
