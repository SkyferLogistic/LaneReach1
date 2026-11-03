"""Public quote page endpoints: the customer views a quote from its email and answers online."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from pydantic import Field

from lanereach.core.dependencies import ContainerDependency
from lanereach.core.openapi import problem_responses
from lanereach.core.rate_limit import RedisRateLimiter, enforce_rate_limits
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.domain.quotes.document import mode_label
from lanereach.domain.quotes.online import OnlineQuote, OnlineQuoteService
from lanereach.website.router import visitor_address

router = APIRouter(prefix="/public/quotes", tags=["Public"])

Token = Annotated[str, Path(min_length=20, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")]


class OnlineQuoteLineResponse(ResponseModel):
    """A charge of the quote."""

    description: str
    quantity: float
    unit: str
    unit_price: float
    amount: float


class OnlineQuoteResponse(ResponseModel):
    """A quote as its customer sees it."""

    number: str
    title: str
    status: str
    company_name: str
    primary_color: str
    contact_first_name: str | None
    service: str
    origin: str | None
    destination: str | None
    incoterm: str | None
    transit_days: int | None
    currency: str
    total_amount: float
    valid_until: date
    notes: str
    lines: list[OnlineQuoteLineResponse]
    expired: bool
    can_decide: bool
    decided_at: datetime | None


class OnlineDecisionRequest(StrictRequestModel):
    """The customer's answer."""

    accepted: bool
    name: str | None = Field(default=None, max_length=120, description="Who answers.")
    note: str | None = Field(default=None, max_length=450, description="Anything to add.")


def get_online_quotes(container: ContainerDependency) -> OnlineQuoteService:
    """Online quote service."""
    return OnlineQuoteService(
        container.session_factory, container.clock, container.settings, container.task_queue
    )


Service = Annotated[OnlineQuoteService, Depends(get_online_quotes)]


async def limit_visitor(request: Request, container: ContainerDependency) -> None:
    """Rate limit per visitor address (the links are public)."""
    await enforce_rate_limits(
        RedisRateLimiter(container.redis),
        [
            (
                f"quote-online:{visitor_address(request)}",
                container.settings.quote_online_per_ip_per_minute,
            )
        ],
        now=container.clock.now(),
    )


def _to_response(online: OnlineQuote) -> OnlineQuoteResponse:
    quote = online.quote
    return OnlineQuoteResponse(
        number=quote.number,
        title=quote.title,
        status=quote.status.value,
        company_name=online.company_name,
        primary_color=online.primary_color,
        contact_first_name=online.contact_first_name,
        service=mode_label(quote),
        origin=quote.origin,
        destination=quote.destination,
        incoterm=quote.incoterm,
        transit_days=quote.transit_days,
        currency=quote.currency,
        total_amount=float(quote.total_amount),
        valid_until=quote.valid_until,
        notes=quote.notes,
        lines=[
            OnlineQuoteLineResponse(
                description=line.description,
                quantity=float(line.quantity),
                unit=line.unit,
                unit_price=float(line.unit_price),
                amount=float(line.amount),
            )
            for line in online.lines
        ],
        expired=online.expired,
        can_decide=online.can_decide,
        decided_at=quote.decided_at,
    )


@router.get(
    "/{token}",
    response_model=OnlineQuoteResponse,
    summary="A quote from its email link (public)",
    description="The token in the link names the quote; an invalid token is a 404.",
    responses=problem_responses(404, 429),
    dependencies=[Depends(limit_visitor)],
)
async def read_online_quote(token: Token, service: Service) -> OnlineQuoteResponse:
    """The quote as its customer sees it."""
    return _to_response(await service.view(token))


@router.post(
    "/{token}/decision",
    response_model=OnlineQuoteResponse,
    summary="Accept or decline a quote online (public)",
    description="Records the customer's answer like a person recording it: the deal's value, "
    "quote events and workflows follow, and the deal's owner is notified. The same answer "
    "twice changes nothing (safe to retry); the other answer afterwards, or accepting an "
    "expired quote, is a 409.",
    responses=problem_responses(404, 409, 422, 429),
    dependencies=[Depends(limit_visitor)],
)
async def decide_online_quote(
    token: Token, payload: OnlineDecisionRequest, service: Service
) -> OnlineQuoteResponse:
    """Record the customer's answer."""
    return _to_response(
        await service.decide(token, accepted=payload.accepted, name=payload.name, note=payload.note)
    )
