"""Finding the sheet rates for a shipment and turning them into quote lines.

Everything here is deterministic: the same deal and sheets always give the same rates. Rates
match on the lane (place codes, else normalised names), a compatible mode and equipment, and
validity on the cargo-ready date. Rows of one sheet tab for the same lane, carrier and validity
form a *rate option* (freight plus its surcharges); options are ranked by how exactly the lane
matched, how recent the sheet is and the all-in price.

Quantities follow freight practice: containers x count, air on chargeable weight
(max of actual kg and cbm x 167), LCL on weight/measure (max of cbm and tonnes). A rate in another
currency than the option's freight is not converted (LaneReach has no exchange rates); its
line is left at 0 and listed as needing attention.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.domain.crm.models import Deal
from lanereach.domain.leads.models import Lead
from lanereach.domain.logistics import line_for_mode
from lanereach.domain.pricing.importer import normalise_equipment
from lanereach.domain.pricing.models import RateEntry, RateSheet
from lanereach.domain.pricing.places import place_code, place_key
from lanereach.domain.quotes.models import ChargeType
from lanereach.domain.quotes.service import LineInput

AIR_VOLUME_FACTOR = Decimal(167)  # kg per cbm (IATA 1:6000)
MAX_OPTIONS = 5
MAX_CANDIDATES = 2000
_BREAK = re.compile(r"^([+-])(\d+)kg$")
_THOUSAND = Decimal(1000)


@dataclass(frozen=True, slots=True)
class ShipmentQuery:
    """What is being priced."""

    origin: str | None
    destination: str | None
    mode: str | None = None
    equipment: str | None = None
    container_quantity: int | None = None
    weight_kg: Decimal | None = None
    volume_cbm: Decimal | None = None
    on_date: date | None = None

    @property
    def service_line(self) -> str:
        """The service line of the mode (`any` when unknown)."""
        return line_for_mode(self.mode).value


async def shipment_query_for_deal(session: AsyncSession, deal: Deal, today: date) -> ShipmentQuery:
    """The pricing question a deal asks (equipment from the lead's extracted shipment).

    Args:
        session: Tenant session.
        deal: The deal.
        today: Used when the deal has no cargo-ready date.

    Returns:
        The query.
    """
    shipment: dict[str, Any] = {}
    if deal.lead_id is not None:
        lead = await session.get(Lead, deal.lead_id)
        shipment = (lead.shipment if lead else None) or {}
    extracted = shipment.get("container_type")
    equipment = deal.equipment or (str(extracted) if extracted and extracted != "OTHER" else None)
    return ShipmentQuery(
        origin=deal.origin,
        destination=deal.destination,
        mode=deal.mode,
        equipment=normalise_equipment(equipment),
        container_quantity=deal.pieces if deal.pieces and deal.pieces > 0 else None,
        weight_kg=deal.weight_kg,
        volume_cbm=deal.volume_cbm,
        on_date=max(deal.cargo_ready_date, today) if deal.cargo_ready_date else today,
    )


def chargeable_weight(weight_kg: Decimal | None, volume_cbm: Decimal | None) -> Decimal:
    """Air chargeable weight: the larger of actual and volumetric weight (1 cbm = 167 kg)."""
    actual = weight_kg or Decimal(0)
    volumetric = (volume_cbm or Decimal(0)) * AIR_VOLUME_FACTOR
    return max(actual, volumetric).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def weight_measure(weight_kg: Decimal | None, volume_cbm: Decimal | None) -> Decimal:
    """LCL revenue tonnes: the larger of cbm and metric tonnes (minimum 1)."""
    tonnes = (weight_kg or Decimal(0)) / _THOUSAND
    return max(volume_cbm or Decimal(0), tonnes, Decimal(1)).quantize(Decimal("0.001"))


def _same_family(query_mode: str | None, rate_mode: str | None) -> bool:
    if not query_mode or not rate_mode:
        return True
    if query_mode == rate_mode:
        return True
    pairs = {
        frozenset({"ocean", "fcl"}),
        frozenset({"ocean", "lcl"}),
        frozenset({"sea", "fcl"}),
        frozenset({"sea", "lcl"}),
        frozenset({"road", "ftl"}),
        frozenset({"road", "ltl"}),
        frozenset({"rail", "rail_fcl"}),
        frozenset({"air_express", "air"}),
    }
    return frozenset({query_mode, rate_mode}) in pairs


def _break_floor(equipment: str | None) -> int | None:
    match = _BREAK.match(equipment or "")
    if not match:
        return None
    return 0 if match.group(1) == "-" else int(match.group(2))


@dataclass
class RateOption:
    """Freight plus surcharges from one sheet tab for one lane, carrier and validity."""

    sheet_id: uuid.UUID
    sheet_name: str
    tab: str
    carrier: str | None
    origin: str
    destination: str
    mode: str | None
    equipment: str | None
    currency: str
    valid_to: date | None
    transit_days: int | None
    rates: list[RateEntry]
    exact_lane: bool
    imported_at: Any
    reasons: list[str] = field(default_factory=list)
    total: Decimal = Decimal("0.00")
    other_currency: list[str] = field(default_factory=list)


def quantity_for(rate: RateEntry, query: ShipmentQuery) -> Decimal:
    """How many units of a rate the shipment uses."""
    if rate.unit == "container":
        return Decimal(query.container_quantity or 1)
    if rate.unit == "kg":
        if (
            rate.mode in ("air", "air_express", "courier")
            or _break_floor(rate.equipment) is not None
        ):
            return chargeable_weight(query.weight_kg, query.volume_cbm) or Decimal(1)
        return query.weight_kg or Decimal(1)
    if rate.unit == "cbm":
        return weight_measure(query.weight_kg, query.volume_cbm)
    return Decimal(1)


def line_amount_for(rate: RateEntry, query: ShipmentQuery) -> tuple[Decimal, Decimal, str, bool]:
    """Quantity, unit price and unit of a rate, applying its minimum charge.

    Returns:
        (quantity, unit price, unit, whether the minimum applied).
    """
    quantity = quantity_for(rate, query)
    amount = quantity * rate.amount
    if rate.minimum is not None and amount < rate.minimum:
        return Decimal(1), rate.minimum, "shipment", True
    return quantity, rate.amount, rate.unit, False


def lane_condition(query: ShipmentQuery) -> Any:
    """SQL condition: the rate's origin and destination are the query's (code or name)."""
    origin_code, destination_code = (
        place_code(query.origin or ""),
        place_code(query.destination or ""),
    )
    origin_key, destination_key = place_key(query.origin or ""), place_key(query.destination or "")
    origin = or_(
        RateEntry.origin_key == origin_key,
        *([RateEntry.origin_code == origin_code] if origin_code else []),
    )
    destination = or_(
        RateEntry.destination_key == destination_key,
        *([RateEntry.destination_code == destination_code] if destination_code else []),
    )
    return and_(origin, destination)


def _applies(rate: RateEntry, query: ShipmentQuery) -> str | None:
    """Why a lane-matching rate does not apply to the shipment, or None when it does."""
    on = query.on_date
    if on is not None and rate.valid_to is not None and rate.valid_to < on:
        return "expired"
    if on is not None and rate.valid_from is not None and rate.valid_from > on:
        return "not yet valid"
    line = query.service_line
    if line != "any" and rate.service_line not in (line, "any"):
        return "other service line"
    if not _same_family(query.mode, rate.mode):
        return "other mode"
    if (
        query.equipment
        and rate.unit == "container"
        and rate.equipment
        and rate.equipment != query.equipment
    ):
        return "other equipment"
    return None


async def find_rate_options(
    session: AsyncSession, query: ShipmentQuery, *, limit: int = MAX_OPTIONS
) -> list[RateOption]:
    """The best rate options for a shipment, best first.

    Args:
        session: Tenant session (RLS keeps other tenants' sheets out).
        query: The shipment.
        limit: How many options to return.

    Returns:
        Ranked options with the reasons each one matched (empty when no sheet covers the lane).
    """
    if not (query.origin and query.destination):
        return []
    rows = (
        await session.execute(
            select(RateEntry, RateSheet.name, RateSheet.imported_at)
            .join(RateSheet, RateSheet.id == RateEntry.sheet_id)
            .where(lane_condition(query))
            .order_by(RateEntry.sheet_id, RateEntry.tab, RateEntry.row_number)
            .limit(MAX_CANDIDATES)
        )
    ).all()
    return rank_rate_options(
        [(rate, name, imported_at) for rate, name, imported_at in rows], query, limit=limit
    )


def rank_rate_options(
    rows: Sequence[tuple[RateEntry, str, Any]], query: ShipmentQuery, *, limit: int = MAX_OPTIONS
) -> list[RateOption]:
    """Group lane-matching rates into options and rank them (pure, for tests and the API)."""
    origin_code, destination_code = (
        place_code(query.origin or ""),
        place_code(query.destination or ""),
    )
    groups: dict[tuple[Any, ...], RateOption] = {}
    for rate, sheet_name, imported_at in rows:
        if _applies(rate, query) is not None:
            continue
        key = (
            rate.sheet_id,
            rate.tab,
            rate.carrier or "",
            rate.origin_key,
            rate.destination_key,
            rate.mode or "",
            rate.valid_to,
        )
        option = groups.get(key)
        if option is None:
            exact = bool(
                origin_code
                and destination_code
                and rate.origin_code == origin_code
                and rate.destination_code == destination_code
            )
            option = groups[key] = RateOption(
                sheet_id=rate.sheet_id,
                sheet_name=sheet_name,
                tab=rate.tab,
                carrier=rate.carrier,
                origin=rate.origin,
                destination=rate.destination,
                mode=rate.mode,
                equipment=None,
                currency=rate.currency,
                valid_to=rate.valid_to,
                transit_days=rate.transit_days,
                rates=[],
                exact_lane=exact,
                imported_at=imported_at,
            )
        option.rates.append(rate)
    options = [
        finished
        for group in groups.values()
        for part in _split_by_size(group, query)
        if (finished := _finish(part, query)) is not None
    ]
    if query.equipment:
        options.sort(
            key=lambda option: (
                not option.exact_lane,
                -_timestamp(option.imported_at),
                option.total,
            )
        )
    else:
        # Without equipment, sizes are not comparable: group them, cheapest first per size.
        options.sort(
            key=lambda option: (not option.exact_lane, option.equipment or "", option.total)
        )
    return options[:limit]


def _split_by_size(option: RateOption, query: ShipmentQuery) -> list[RateOption]:
    """One option per container size when the shipment does not say which (never 20GP + 40HC)."""
    sizes = sorted(
        {rate.equipment for rate in option.rates if rate.unit == "container" and rate.equipment}
    )
    if query.equipment or len(sizes) < 2:
        return [option]
    parts = []
    for size in sizes:
        rates = [
            rate
            for rate in option.rates
            if rate.unit != "container" or not rate.equipment or rate.equipment == size
        ]
        part = replace(option, rates=rates, equipment=size, reasons=[], other_currency=[])
        part.reasons.append(f"No equipment on the deal: {size} shown")
        parts.append(part)
    return parts


def _timestamp(value: Any) -> float:
    return value.timestamp() if hasattr(value, "timestamp") else 0.0


def _finish(option: RateOption, query: ShipmentQuery) -> RateOption | None:
    """Pick one weight break / equipment per option, total it and explain the match."""
    rates = option.rates
    # Air weight breaks: keep the highest break the chargeable weight reaches.
    breaks = [rate for rate in rates if _break_floor(rate.equipment) is not None]
    if breaks:
        weight = chargeable_weight(query.weight_kg, query.volume_cbm)
        reached = [rate for rate in breaks if Decimal(_break_floor(rate.equipment) or 0) <= weight]
        chosen = max(reached or breaks, key=lambda rate: _break_floor(rate.equipment) or 0)
        rates = [rate for rate in rates if rate not in breaks or rate is chosen]
        option.equipment = chosen.equipment
    sizes = {rate.equipment for rate in rates if rate.unit == "container" and rate.equipment}
    if len(sizes) == 1 and option.equipment is None:
        option.equipment = next(iter(sizes))
    freight = [rate for rate in rates if rate.charge_type == "freight"]
    if not freight:
        return None
    option.rates = rates
    option.currency = freight[0].currency
    option.transit_days = next((rate.transit_days for rate in freight if rate.transit_days), None)
    total = Decimal("0.00")
    for rate in rates:
        quantity, unit_price, _unit, _minimum = line_amount_for(rate, query)
        if rate.currency != option.currency:
            option.other_currency.append(
                f"{rate.description}: {rate.amount} {rate.currency} per {rate.unit}"
            )
            continue
        total += (quantity * unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    option.total = total
    option.reasons.insert(0, "Same port/airport codes" if option.exact_lane else "Same place names")
    if query.mode and option.mode:
        option.reasons.append(f"Mode {option.mode}")
    if query.equipment and option.equipment == query.equipment:
        option.reasons.append(f"Equipment {option.equipment}")
    if option.valid_to:
        option.reasons.append(f"Valid until {option.valid_to.isoformat()}")
    return option


_CHARGE_TYPES = {item.value for item in ChargeType}


def price_quote_lines(
    option: RateOption, query: ShipmentQuery, *, markup_percent: Decimal = Decimal(0)
) -> list[LineInput]:
    """Quote lines for an option, in its freight currency.

    Args:
        option: The chosen option.
        query: The shipment (quantities).
        markup_percent: Added to every price (0 when the sheet already holds selling rates).

    Returns:
        One line per rate; lines in another currency are left at 0 for pricing to convert.
    """
    factor = Decimal(1) + markup_percent / Decimal(100)
    lines = []
    for rate in option.rates:
        quantity, unit_price, unit, minimum = line_amount_for(rate, query)
        same_currency = rate.currency == option.currency
        description = rate.description
        if rate.equipment and rate.unit == "container" and rate.equipment not in description:
            description = f"{description} {rate.equipment}"
        if minimum:
            description = f"{description} (minimum charge)"
        lines.append(
            LineInput(
                charge_type=ChargeType(
                    rate.charge_type if rate.charge_type in _CHARGE_TYPES else "other"
                ),
                description=description[:200],
                quantity=quantity,
                unit=unit,
                unit_price=(unit_price * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if same_currency
                else Decimal("0.00"),
            )
        )
    return lines


def describe_sources(option: RateOption) -> list[str]:
    """Where each line came from (`sheet ▸ tab ▸ row 12`): for the deal history, not the quote."""
    return [f"{option.sheet_name} ▸ {rate.tab} ▸ row {rate.row_number}" for rate in option.rates]
