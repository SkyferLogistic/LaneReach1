"""Pricing: importing rate sheets, rate sources, market checks and rate-priced quotes.

Rate sheets come from the library's `Rates/` folder (SharePoint/OneDrive, re-imported when the
file's etag changes) or from an upload. A re-import replaces the sheet's rates in the same
transaction, so pricing never sees half a sheet. A file that cannot be read keeps the previous
rates (a half-saved workbook should not wipe out the price list) and is marked failed.

Rate sources are the websites the team checks. LaneReach keeps an https link template per site
(placeholders filled with the deal's lane, URL-encoded); it never stores a password and never
logs in or scrapes. A market check is a rate a person saw there, recorded on the deal.

Matching is deterministic (`matching.py`); the AI only maps an unusual spelling of a lane onto
the places present in the sheets (`match_lane`), and a quote is only ever drafted, never sent.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlsplit

import structlog
from sqlalchemy import Select, delete, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.ai.llm.client import LLMError
from lanereach.ai.tasks.match_lane import match_lane
from lanereach.ai.tasks.runtime import AIRuntime
from lanereach.core.clock import Clock
from lanereach.core.errors import BusinessRuleViolationError, ConflictError, NotFoundError
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.crm.models import Deal, DealEvent
from lanereach.domain.logistics import UNITS
from lanereach.domain.pricing.importer import (
    FIELDS,
    ParsedSheet,
    RateSheetUnreadableError,
    parse_rate_sheet,
)
from lanereach.domain.pricing.matching import (
    RateOption,
    ShipmentQuery,
    describe_sources,
    find_rate_options,
    lane_condition,
    price_quote_lines,
    shipment_query_for_deal,
)
from lanereach.domain.pricing.models import MarketCheck, RateEntry, RateSheet, RateSource
from lanereach.domain.pricing.places import place_code, place_key
from lanereach.domain.quotes.models import Quote, QuoteStatus
from lanereach.domain.quotes.service import DEFAULT_VALIDITY_DAYS, QuoteInput, QuoteService

logger = structlog.get_logger(__name__)

SHEET_SOURCE_LIBRARY = "library"
SHEET_SOURCE_UPLOAD = "upload"
PLACEHOLDERS = ("origin", "destination", "mode", "equipment", "date")
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
AI_LANE_CONFIDENCE = 0.6


# --- rate sheets -----------------------------------------------------------------------------


@dataclass
class RateSyncSummary:
    """What a library sync did to the rate sheets."""

    imported: int = 0
    unchanged: int = 0
    failed: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)


def _status(parsed: ParsedSheet) -> str:
    if not parsed.rates:
        return "failed"
    return "partial" if parsed.errors else "ok"


class RateSheetService:
    """Imports and manages a tenant's rate sheets.

    Args:
        session: Tenant session.
        clock: Time source.
    """

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def list_sheets(self) -> Sequence[RateSheet]:
        """Every sheet, by name."""
        return (await self._session.scalars(select(RateSheet).order_by(RateSheet.name))).all()

    async def require_sheet(self, sheet_id: uuid.UUID) -> RateSheet:
        """A sheet or 404."""
        sheet = await self._session.get(RateSheet, sheet_id)
        if sheet is None:
            raise NotFoundError("Rate sheet not found.")
        return sheet

    async def import_sheet(
        self,
        *,
        name: str,
        filename: str,
        content: bytes,
        source: str,
        library_path: str | None = None,
        etag: str | None = None,
        default_currency: str | None = None,
    ) -> RateSheet:
        """Create or re-import a sheet by name, replacing its rates.

        Args:
            name: Sheet name (unique per tenant; the library path under `Rates/`).
            filename: Original file name (`.xlsx` or `.csv`).
            content: File bytes.
            source: `library` or `upload`.
            library_path: Path in the library, for library sheets.
            etag: Library version, for change detection.
            default_currency: Currency for rows that do not say (kept from before when None).

        Returns:
            The sheet (status `failed` when nothing could be read).
        """
        existing = await self._session.scalar(select(RateSheet).where(RateSheet.name == name))
        now = self._clock.now()
        currency = (default_currency or (existing.default_currency if existing else "USD")).upper()
        if not _CURRENCY.match(currency):
            raise BusinessRuleViolationError("The default currency must be a 3-letter code.")
        sheet = existing or RateSheet(
            tenant_id=require_session_tenant_id(self._session),
            name=name[:300],
            created_at=now,
            row_count=0,
            errors=[],
            column_mapping={},
            tabs=[],
            status="failed",
        )
        sheet.source = source
        sheet.library_path = library_path
        sheet.etag = etag
        sheet.default_currency = currency
        previous = (await self._content(existing), existing.file_hash) if existing else None
        sheet.content = content
        sheet.file_hash = hashlib.sha256(content).hexdigest()
        sheet.imported_at = now
        sheet.updated_at = now
        if existing is None:
            self._session.add(sheet)
            await self._session.flush()
        readable = await self._apply(
            sheet, filename, content, sheet.column_mapping.get("override") or {}
        )
        if not readable and previous is not None:
            # Keep the file the kept rates came from, so a mapping change still works.
            sheet.content, sheet.file_hash = previous
            await self._session.flush()
        return sheet

    async def _content(self, sheet: RateSheet) -> bytes:
        """The stored file (a deferred column, loaded only when needed)."""
        content = await self._session.scalar(
            select(RateSheet.content).where(RateSheet.id == sheet.id)
        )
        return content or b""

    async def _apply(
        self, sheet: RateSheet, filename: str, content: bytes, override: dict[str, Any]
    ) -> bool:
        try:
            parsed = parse_rate_sheet(
                content, filename, default_currency=sheet.default_currency, mapping=override
            )
        except RateSheetUnreadableError as exc:
            sheet.status = "failed"
            message = str(exc)
            if sheet.row_count:
                message += f" The previous {sheet.row_count} rates are kept."
            sheet.errors = [{"tab": "", "row": 0, "message": message}]
            await self._session.flush()
            return False
        await self._session.execute(delete(RateEntry).where(RateEntry.sheet_id == sheet.id))
        if parsed.rates:
            await self._session.execute(
                insert(RateEntry),
                [
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": sheet.tenant_id,
                        "sheet_id": sheet.id,
                        **{key: getattr(rate, key) for key in _ENTRY_FIELDS},
                    }
                    for rate in parsed.rates
                ],
            )
        sheet.status = _status(parsed)
        sheet.row_count = len(parsed.rates)
        sheet.errors = parsed.errors
        sheet.tabs = parsed.tabs
        sheet.column_mapping = {"detected": parsed.mapping, "override": override}
        await self._session.flush()
        logger.info(
            "rate_sheet_imported",
            rate_sheet_id=str(sheet.id),
            rates=sheet.row_count,
            errors=len(parsed.errors),
        )
        return True

    async def update_mapping(
        self,
        sheet_id: uuid.UUID,
        override: dict[str, dict[str, str]],
        default_currency: str | None = None,
    ) -> RateSheet:
        """Save which column holds which field (per tab) and re-import the stored file.

        Raises:
            NotFoundError: For an unknown sheet.
            BusinessRuleViolationError: For unknown fields or a bad currency.
        """
        sheet = await self.require_sheet(sheet_id)
        for tab, columns in override.items():
            unknown = sorted(set(columns) - set(FIELDS))
            if unknown:
                raise BusinessRuleViolationError(
                    f"Unknown fields for tab {tab!r}: {', '.join(unknown)}."
                )
        if default_currency:
            if not _CURRENCY.match(default_currency.upper()):
                raise BusinessRuleViolationError("The default currency must be a 3-letter code.")
            sheet.default_currency = default_currency.upper()
        clean = {
            tab[:100]: {name: header[:100] for name, header in columns.items() if header.strip()}
            for tab, columns in override.items()
        }
        sheet.updated_at = self._clock.now()
        content = await self._content(sheet)
        await self._apply(sheet, _filename_of(sheet, content), content, clean)
        return sheet

    async def reimport_sheet(self, sheet_id: uuid.UUID) -> RateSheet:
        """Read the stored file again (after an importer improvement or a mapping change)."""
        sheet = await self.require_sheet(sheet_id)
        sheet.imported_at = sheet.updated_at = self._clock.now()
        content = await self._content(sheet)
        await self._apply(
            sheet, _filename_of(sheet, content), content, sheet.column_mapping.get("override") or {}
        )
        return sheet

    async def delete_sheet(self, sheet_id: uuid.UUID) -> None:
        """Delete a sheet and its rates.

        Raises:
            NotFoundError: For an unknown sheet.
            ConflictError: For a library sheet (delete the file in the library instead, or the
                next sync would bring it back).
        """
        sheet = await self.require_sheet(sheet_id)
        if sheet.source == SHEET_SOURCE_LIBRARY:
            raise ConflictError(
                "This sheet comes from the library's Rates folder; remove the file there and it "
                "disappears at the next sync."
            )
        await self._session.delete(sheet)
        await self._session.flush()

    async def preview_rows(self, sheet_id: uuid.UUID, limit: int = 8) -> dict[str, list[list[str]]]:
        """The first rows of every tab, as text, for the column-mapping editor."""
        from lanereach.domain.pricing.importer import read_tables

        sheet = await self.require_sheet(sheet_id)
        try:
            content = await self._content(sheet)
            tables = read_tables(content, _filename_of(sheet, content))
        except RateSheetUnreadableError:
            return {}
        return {
            tab: [
                ["" if cell is None else str(cell)[:60] for cell in row[:30]]
                for row in rows[:limit]
            ]
            for tab, rows in tables
        }

    async def sync_from_library(
        self, store: Any, items: Sequence[Any], *, remove_missing: bool
    ) -> RateSyncSummary:
        """Import the library's changed rate sheets and drop the ones that were removed.

        Args:
            store: The tenant's document store (`download(item)`).
            items: The library files under `Rates/` (`DocumentItem`s).
            remove_missing: Delete library sheets whose file is gone. False when the library
                listing came back empty, which is more likely an unreachable library than
                every file deleted.

        Returns:
            What changed.
        """
        from lanereach.domain.scripts.paths import RateSheetFile, parse_library_path
        from lanereach.integrations.documents.store import DocumentStoreError

        summary = RateSyncSummary()
        library = {
            sheet.library_path: sheet
            for sheet in (
                await self._session.scalars(
                    select(RateSheet).where(RateSheet.source == SHEET_SOURCE_LIBRARY)
                )
            ).all()
        }
        seen: set[str] = set()
        for item in items:
            parsed = parse_library_path(item.path)
            if not isinstance(parsed, RateSheetFile):
                continue
            seen.add(item.path)
            existing = library.get(item.path)
            if existing is not None and existing.etag == item.etag and existing.status != "failed":
                summary.unchanged += 1
                continue
            try:
                content = await store.download(item)
            except DocumentStoreError as exc:
                summary.failed += 1
                summary.errors.append(f"{item.path}: {exc}")
                continue
            sheet = await self.import_sheet(
                name=parsed.name,
                filename=item.name,
                content=content,
                source=SHEET_SOURCE_LIBRARY,
                library_path=item.path,
                etag=item.etag,
            )
            if sheet.status == "failed":
                summary.failed += 1
                summary.errors.extend(
                    f"{item.path}: {error['message']}" for error in sheet.errors[:1]
                )
            else:
                summary.imported += 1
        if remove_missing:
            for path, sheet in library.items():
                if path not in seen:
                    await self._session.delete(sheet)
                    summary.removed += 1
        await self._session.flush()
        return summary


_ENTRY_FIELDS = (
    "tab",
    "row_number",
    "service_line",
    "mode",
    "origin",
    "origin_code",
    "origin_key",
    "destination",
    "destination_code",
    "destination_key",
    "equipment",
    "carrier",
    "charge_type",
    "description",
    "unit",
    "amount",
    "currency",
    "minimum",
    "valid_from",
    "valid_to",
    "transit_days",
    "notes",
)


def _filename_of(sheet: RateSheet, content: bytes) -> str:
    """Name with the right extension for the parser (`.csv` when the file is not a zip)."""
    path = sheet.library_path or sheet.name
    if path.lower().endswith((".xlsx", ".xlsm", ".csv")):
        return path
    return f"{sheet.name}.xlsx" if content[:2] == b"PK" else f"{sheet.name}.csv"


# --- searching rates -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RateFilter:
    """Rates tab filters."""

    lane: str | None = None
    origin: str | None = None
    destination: str | None = None
    service_line: str | None = None
    equipment: str | None = None
    sheet_id: uuid.UUID | None = None
    valid_on: date | None = None
    include_expired: bool = False


def filter_rates[StatementT: Select[*tuple[Any, ...]]](
    statement: StatementT, rate_filter: RateFilter
) -> StatementT:
    """Apply the filters to a rate query."""
    if rate_filter.origin:
        key, code = place_key(rate_filter.origin), place_code(rate_filter.origin)
        statement = statement.where(
            or_(RateEntry.origin_key == key, RateEntry.origin_code == code)
            if code
            else or_(RateEntry.origin_key == key, RateEntry.origin.ilike(f"%{rate_filter.origin}%"))
        )
    if rate_filter.destination:
        key, code = place_key(rate_filter.destination), place_code(rate_filter.destination)
        statement = statement.where(
            or_(RateEntry.destination_key == key, RateEntry.destination_code == code)
            if code
            else or_(
                RateEntry.destination_key == key,
                RateEntry.destination.ilike(f"%{rate_filter.destination}%"),
            )
        )
    if rate_filter.lane:
        like = f"%{rate_filter.lane.strip()}%"
        statement = statement.where(
            or_(
                RateEntry.origin.ilike(like),
                RateEntry.destination.ilike(like),
                RateEntry.carrier.ilike(like),
                RateEntry.origin_code == rate_filter.lane.strip().upper(),
                RateEntry.destination_code == rate_filter.lane.strip().upper(),
            )
        )
    if rate_filter.service_line:
        statement = statement.where(RateEntry.service_line == rate_filter.service_line)
    if rate_filter.equipment:
        statement = statement.where(RateEntry.equipment == rate_filter.equipment.upper())
    if rate_filter.sheet_id:
        statement = statement.where(RateEntry.sheet_id == rate_filter.sheet_id)
    if not rate_filter.include_expired and rate_filter.valid_on:
        statement = statement.where(
            or_(RateEntry.valid_to.is_(None), RateEntry.valid_to >= rate_filter.valid_on)
        )
    return statement


async def search_rates(
    session: AsyncSession, rate_filter: RateFilter, *, limit: int, offset: int
) -> tuple[list[tuple[RateEntry, str]], int]:
    """A page of rates (cheapest first per lane) and the total count.

    Returns:
        (rate, sheet name) pairs and the number of matching rates.
    """
    total = await session.scalar(filter_rates(select(func.count(RateEntry.id)), rate_filter)) or 0
    rows = (
        await session.execute(
            filter_rates(
                select(RateEntry, RateSheet.name).join(
                    RateSheet, RateSheet.id == RateEntry.sheet_id
                ),
                rate_filter,
            )
            .order_by(
                RateEntry.origin, RateEntry.destination, RateEntry.equipment, RateEntry.amount
            )
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [(rate, name) for rate, name in rows], int(total)


async def sheet_places(session: AsyncSession) -> tuple[list[str], list[str]]:
    """Distinct origins and destinations written in the tenant's sheets."""
    origins = (
        await session.scalars(select(RateEntry.origin).distinct().order_by(RateEntry.origin))
    ).all()
    destinations = (
        await session.scalars(
            select(RateEntry.destination).distinct().order_by(RateEntry.destination)
        )
    ).all()
    return list(origins), list(destinations)


async def lane_history(
    session: AsyncSession, origin: str, destination: str, equipment: str | None = None
) -> list[dict[str, Any]]:
    """Freight prices of a lane over time: sheet rates and market checks recorded on deals.

    Returns:
        Points (`kind`, `label`, `observed_on`, `amount`, `currency`, `unit`, `equipment`),
        oldest first.
    """
    query = ShipmentQuery(origin=origin, destination=destination)
    statement = (
        select(RateEntry, RateSheet.name, RateSheet.imported_at)
        .join(RateSheet, RateSheet.id == RateEntry.sheet_id)
        .where(lane_condition(query), RateEntry.charge_type == "freight")
        .limit(500)
    )
    if equipment:
        statement = statement.where(RateEntry.equipment == equipment.upper())
    points: list[dict[str, Any]] = []
    for rate, name, imported_at in (await session.execute(statement)).all():
        points.append(
            {
                "kind": "sheet",
                "label": f"{name} ▸ {rate.carrier}" if rate.carrier else name,
                "observed_on": rate.valid_from or imported_at.date(),
                "amount": rate.amount,
                "currency": rate.currency,
                "unit": rate.unit,
                "equipment": rate.equipment,
            }
        )
    origin_key, destination_key = place_key(origin), place_key(destination)
    checks = await session.execute(
        select(MarketCheck, Deal.origin, Deal.destination)
        .join(Deal, Deal.id == MarketCheck.deal_id)
        .order_by(MarketCheck.created_at.desc())
        .limit(1000)
    )
    for check, deal_origin, deal_destination in checks.all():
        if place_key(deal_origin or "") != origin_key:
            continue
        if place_key(deal_destination or "") != destination_key:
            continue
        if equipment and check.equipment and check.equipment != equipment.upper():
            continue
        points.append(
            {
                "kind": "market",
                "label": check.source_name,
                "observed_on": check.created_at.date(),
                "amount": check.amount,
                "currency": check.currency,
                "unit": check.unit,
                "equipment": check.equipment,
            }
        )
    points.sort(key=lambda point: point["observed_on"])
    return points


# --- rate sources (websites) -----------------------------------------------------------------


def validate_lane_template(template: str | None) -> str | None:
    """Check a lane link template: https, no credentials, only known placeholders.

    Raises:
        BusinessRuleViolationError: When the template is unsafe or unknown placeholders are used.
    """
    if template is None or not template.strip():
        return None
    value = template.strip()
    unknown = sorted(set(_PLACEHOLDER.findall(value)) - set(PLACEHOLDERS))
    if unknown:
        known = ", ".join("{" + name + "}" for name in PLACEHOLDERS)
        raise BusinessRuleViolationError(
            f"Unknown placeholders: {', '.join(unknown)} (use {known})."
        )
    _check_https(_PLACEHOLDER.sub("x", value))
    return value


def _check_https(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise BusinessRuleViolationError("Links must start with https:// and name a website.")
    if parts.username or parts.password:
        raise BusinessRuleViolationError(
            "Links cannot contain a user name or password; LaneReach never stores website logins."
        )


def render_lane_link(template: str, query: ShipmentQuery) -> str:
    """A source's link for a lane, every value URL-encoded."""
    values = {
        "origin": query.origin or "",
        "destination": query.destination or "",
        "mode": query.mode or "",
        "equipment": query.equipment or "",
        "date": query.on_date.isoformat() if query.on_date else "",
    }
    return _PLACEHOLDER.sub(lambda match: quote(values.get(match.group(1), ""), safe=""), template)


@dataclass(frozen=True, slots=True)
class RateSourceInput:
    """A website the team checks."""

    name: str
    website: str
    lane_url_template: str | None = None
    service_lines: list[str] = field(default_factory=list)
    is_active: bool = True
    notes: str | None = None


class RateSourceService:
    """The websites pricing checks.

    Args:
        session: Tenant session.
        clock: Time source.
    """

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def list_sources(self, *, active_only: bool = False) -> Sequence[RateSource]:
        """Sources by name."""
        statement = select(RateSource).order_by(RateSource.name)
        if active_only:
            statement = statement.where(RateSource.is_active.is_(True))
        return (await self._session.scalars(statement)).all()

    async def require_source(self, source_id: uuid.UUID) -> RateSource:
        """A source or 404."""
        source = await self._session.get(RateSource, source_id)
        if source is None:
            raise NotFoundError("Rate source not found.")
        return source

    async def save_source(
        self, data: RateSourceInput, source_id: uuid.UUID | None = None
    ) -> RateSource:
        """Create or change a source.

        Raises:
            BusinessRuleViolationError: For unsafe links.
            ConflictError: When another source has the name.
        """
        _check_https(data.website.strip())
        template = validate_lane_template(data.lane_url_template)
        name = " ".join(data.name.split())[:120]
        clash = await self._session.scalar(
            select(RateSource.id).where(func.lower(RateSource.name) == name.lower())
        )
        if clash is not None and clash != source_id:
            raise ConflictError(f"A rate source named {name!r} already exists.")
        now = self._clock.now()
        if source_id is None:
            source = RateSource(tenant_id=require_session_tenant_id(self._session), created_at=now)
            self._session.add(source)
        else:
            source = await self.require_source(source_id)
        source.name = name
        source.website = data.website.strip()[:500]
        source.lane_url_template = template
        source.service_lines = sorted(set(data.service_lines))
        source.is_active = data.is_active
        source.notes = (data.notes or "").strip()[:500] or None
        source.updated_at = now
        await self._session.flush()
        return source

    async def delete_source(self, source_id: uuid.UUID) -> None:
        """Delete a source (market checks keep its name)."""
        await self._session.delete(await self.require_source(source_id))
        await self._session.flush()


# --- market checks ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MarketCheckInput:
    """A rate seen on a website."""

    amount: Decimal
    currency: str
    unit: str
    source_id: uuid.UUID | None = None
    source_name: str | None = None
    equipment: str | None = None
    valid_until: date | None = None
    transit_days: int | None = None
    note: str | None = None


class MarketCheckService:
    """Rates people saw on websites, per deal.

    Args:
        session: Tenant session.
        clock: Time source.
    """

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def list_checks(self, deal_id: uuid.UUID) -> Sequence[MarketCheck]:
        """A deal's checks, cheapest first."""
        await _require_deal(self._session, deal_id)
        return (
            await self._session.scalars(
                select(MarketCheck)
                .where(MarketCheck.deal_id == deal_id)
                .order_by(MarketCheck.currency, MarketCheck.amount)
            )
        ).all()

    async def record_check(
        self, deal_id: uuid.UUID, actor: str, data: MarketCheckInput
    ) -> MarketCheck:
        """Record a rate seen on a website.

        Raises:
            NotFoundError: For an unknown deal or source.
            BusinessRuleViolationError: Without a source.
        """
        deal = await _require_deal(self._session, deal_id)
        if data.unit not in UNITS:
            raise BusinessRuleViolationError(f"Unknown unit {data.unit!r}.")
        source_name = (data.source_name or "").strip()
        if data.source_id is not None:
            source_name = (
                await RateSourceService(self._session, self._clock).require_source(data.source_id)
            ).name
        if not source_name:
            raise BusinessRuleViolationError("Say which website the rate comes from.")
        now = self._clock.now()
        check = MarketCheck(
            tenant_id=deal.tenant_id,
            deal_id=deal.id,
            source_id=data.source_id,
            source_name=source_name[:120],
            amount=data.amount,
            currency=data.currency.upper(),
            unit=data.unit,
            equipment=(data.equipment or "").strip().upper()[:30] or None,
            valid_until=data.valid_until,
            transit_days=data.transit_days,
            note=(data.note or "").strip()[:500] or None,
            checked_by=actor[:320],
            created_at=now,
        )
        self._session.add(check)
        self._session.add(
            DealEvent(
                tenant_id=deal.tenant_id,
                deal_id=deal.id,
                kind="market_check",
                data={
                    "source": check.source_name,
                    "amount": str(check.amount),
                    "currency": check.currency,
                    "unit": check.unit,
                },
                actor=actor[:200],
                created_at=now,
            )
        )
        await self._session.flush()
        return check

    async def delete_check(self, check_id: uuid.UUID) -> None:
        """Delete a check."""
        check = await self._session.get(MarketCheck, check_id)
        if check is None:
            raise NotFoundError("Market check not found.")
        await self._session.delete(check)
        await self._session.flush()


async def _require_deal(session: AsyncSession, deal_id: uuid.UUID) -> Deal:
    deal = await session.get(Deal, deal_id)
    if deal is None:
        raise NotFoundError("Deal not found.")
    return deal


# --- pricing a deal --------------------------------------------------------------------------

type Lane = tuple[str, str]


@dataclass
class LaneSuggestion:
    """How the AI mapped an unusual lane spelling onto the sheets."""

    origin: str | None
    destination: str | None
    confidence: float
    ai_run_id: uuid.UUID

    @property
    def usable(self) -> bool:
        """Both places found, confidently enough to price from."""
        return self.lane() is not None

    def lane(self) -> Lane | None:
        """The suggested lane when it is usable."""
        if self.origin and self.destination and self.confidence >= AI_LANE_CONFIDENCE:
            return (self.origin, self.destination)
        return None


@dataclass
class DealPricing:
    """Everything the deal's Pricing tab shows."""

    query: ShipmentQuery
    options: list[RateOption]
    lane: LaneSuggestion | None = None


async def find_deal_rates(
    session: AsyncSession, deal: Deal, today: date, *, lane: Lane | None = None
) -> DealPricing:
    """Rate options for a deal (deterministic).

    Args:
        session: Tenant session.
        deal: The deal.
        today: Validity date when the deal has no cargo-ready date.
        lane: Sheet origin and destination to use instead of the deal's spelling (a person's
            or the AI's choice from the sheet places).

    Returns:
        The query used and the ranked options.
    """
    query = await shipment_query_for_deal(session, deal, today)
    if lane is not None:
        query = replace(query, origin=lane[0], destination=lane[1])
    options = await find_rate_options(session, query)
    if lane is not None:
        for option in options:
            option.reasons.insert(0, f"Lane chosen as {lane[0]} → {lane[1]}")
    return DealPricing(query, options)


async def suggest_deal_lane(
    session: AsyncSession, deal: Deal, runtime: AIRuntime
) -> LaneSuggestion | None:
    """Ask the AI which sheet places the deal's origin and destination are.

    The answer is limited to places present in the sheets (validated), so the AI can point at
    an existing lane but never invent one or a price.

    Returns:
        The suggestion, or None without a lane, without sheets, or when the AI call fails.
    """
    if not (deal.origin and deal.destination):
        return None
    origins, destinations = await sheet_places(session)
    if not origins or not destinations:
        return None
    try:
        result = await match_lane(
            runtime,
            origin=deal.origin,
            destination=deal.destination,
            origins=origins,
            destinations=destinations,
            input_ref=str(deal.id),
        )
    except LLMError as exc:
        logger.warning("match_lane_failed", deal_id=str(deal.id), error=type(exc).__name__)
        return None
    match = result.output
    return LaneSuggestion(match.origin, match.destination, match.confidence, result.ai_run_id)


@dataclass
class PricedQuote:
    """A quote drafted or filled from rates."""

    quote: Quote
    option: RateOption
    sources: list[str]
    needs_attention: list[str]


class PricingService:
    """Drafts and fills quotes from the rate sheets (never sends them).

    Args:
        session: Tenant session.
        clock: Time source.
        quotes: Quote service.
        runtime: AI runtime used when the deal's lane spelling matches no sheet (workflows);
            None keeps pricing deterministic (the API asks the AI only on request).
    """

    def __init__(
        self,
        session: AsyncSession,
        clock: Clock,
        quotes: QuoteService,
        runtime: AIRuntime | None = None,
    ) -> None:
        self._session = session
        self._clock = clock
        self._quotes = quotes
        self._runtime = runtime

    async def deal_pricing(self, deal_id: uuid.UUID, lane: Lane | None = None) -> DealPricing:
        """Rate options for a deal (404 for an unknown deal)."""
        deal = await _require_deal(self._session, deal_id)
        return await find_deal_rates(self._session, deal, self._clock.now().date(), lane=lane)

    async def suggest_lane(self, deal_id: uuid.UUID) -> DealPricing:
        """Let the AI map the deal's lane onto the sheets, and the options for that lane.

        Raises:
            NotFoundError: For an unknown deal.
            BusinessRuleViolationError: When no AI model is configured.
        """
        if self._runtime is None:
            raise BusinessRuleViolationError("No AI model is configured.")
        deal = await _require_deal(self._session, deal_id)
        today = self._clock.now().date()
        suggestion = await suggest_deal_lane(self._session, deal, self._runtime)
        lane = suggestion.lane() if suggestion else None
        pricing = await find_deal_rates(self._session, deal, today, lane=lane)
        pricing.lane = suggestion
        return pricing

    async def _choose(
        self, deal: Deal, option_index: int, lane: Lane | None
    ) -> tuple[DealPricing, RateOption]:
        today = self._clock.now().date()
        pricing = await find_deal_rates(self._session, deal, today, lane=lane)
        if not pricing.options and lane is None and self._runtime is not None:
            suggestion = await suggest_deal_lane(self._session, deal, self._runtime)
            suggested = suggestion.lane() if suggestion else None
            if suggested is not None:
                pricing = await find_deal_rates(self._session, deal, today, lane=suggested)
                pricing.lane = suggestion
        if not pricing.options:
            raise BusinessRuleViolationError(
                "No rate sheet covers this lane yet; price it by hand or add the lane to a sheet."
            )
        if option_index >= len(pricing.options):
            raise BusinessRuleViolationError("That rate option is no longer available.")
        return pricing, pricing.options[option_index]

    async def draft_quote_from_rates(
        self,
        deal_id: uuid.UUID,
        actor: str,
        *,
        option_index: int = 0,
        markup_percent: Decimal = Decimal(0),
        validity_days: int | None = None,
        notes: str = "",
        lane: Lane | None = None,
    ) -> PricedQuote:
        """Create a draft quote priced from a rate option.

        Raises:
            NotFoundError: For an unknown deal.
            BusinessRuleViolationError: When no rate covers the lane.
        """
        from datetime import timedelta

        deal = await _require_deal(self._session, deal_id)
        pricing, option = await self._choose(deal, option_index, lane)
        today = self._clock.now().date()
        valid_until = today + timedelta(days=validity_days or DEFAULT_VALIDITY_DAYS)
        if option.valid_to and option.valid_to < valid_until:
            # Never offer a price for longer than the rate behind it is valid.
            valid_until = max(option.valid_to, today)
        quote = await self._quotes.create_quote(
            deal.id,
            actor,
            QuoteInput(
                currency=option.currency,
                valid_until=valid_until,
                transit_days=option.transit_days,
                notes=notes,
                lines=price_quote_lines(option, pricing.query, markup_percent=markup_percent),
            ),
        )
        return self._record(deal, quote, option, actor)

    async def fill_quote_from_rates(
        self,
        quote_id: uuid.UUID,
        actor: str,
        *,
        option_index: int = 0,
        markup_percent: Decimal = Decimal(0),
        lane: Lane | None = None,
    ) -> PricedQuote:
        """Replace a draft's lines with a rate option's prices.

        Raises:
            NotFoundError: For an unknown quote.
            ConflictError: When the quote was already sent.
            BusinessRuleViolationError: When no rate covers the lane.
        """
        quote = await self._quotes.require_quote(quote_id)
        if quote.status is not QuoteStatus.DRAFT:
            raise ConflictError(f"{quote.number} was sent; duplicate it to change prices.")
        deal = await _require_deal(self._session, quote.deal_id)
        pricing, option = await self._choose(deal, option_index, lane)
        valid_until = quote.valid_until
        if option.valid_to and option.valid_to < valid_until:
            valid_until = max(option.valid_to, self._clock.now().date())
        updated = await self._quotes.update_quote(
            quote.id,
            QuoteInput(
                title=quote.title,
                currency=option.currency,
                valid_until=valid_until,
                transit_days=option.transit_days or quote.transit_days,
                incoterm=quote.incoterm,
                service_line=quote.service_line,
                mode=quote.mode,
                origin=quote.origin,
                destination=quote.destination,
                notes=quote.notes,
                lines=price_quote_lines(option, pricing.query, markup_percent=markup_percent),
            ),
        )
        return self._record(deal, updated, option, actor)

    def _record(self, deal: Deal, quote: Quote, option: RateOption, actor: str) -> PricedQuote:
        sources = describe_sources(option)
        # Where the prices came from goes to the deal history (internal), not on the quote the
        # customer receives.
        self._session.add(
            DealEvent(
                tenant_id=deal.tenant_id,
                deal_id=deal.id,
                kind="priced_from_rates",
                data={
                    "quote": quote.number,
                    "sheet": option.sheet_name,
                    "sources": sources[:20],
                    "needs_attention": option.other_currency[:20],
                },
                actor=actor[:200],
                created_at=self._clock.now(),
            )
        )
        return PricedQuote(quote, option, sources, list(option.other_currency))
