"""Reading rate sheets: Excel (.xlsx) and CSV files as pricing teams keep them.

Every worksheet tab is read. The header row is found by recognising column names (POL, POD,
40HC, validity...), so the team does not have to change its sheet; a saved mapping per sheet
overrides the guess. Two layouts are understood:

* long: one row per charge (`Origin | Destination | Equipment | Charge | Rate | Currency ...`);
* wide: one column per container size or air weight break (`20GP | 40GP | 40HC` or `+45 | +100`),
  each becoming its own rate.

Blank lane cells repeat the value above (merged cells), amounts accept currency symbols, thousands
separators and decimal commas, and dates accept the usual formats. A row that cannot be read is
reported with its tab and row number; the rest of the sheet still imports.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from lanereach.domain.logistics import line_for_mode
from lanereach.domain.pricing.places import place_code, place_key

MAX_ROWS = 20_000
MAX_ERRORS = 50
_HEADER_SCAN = 20

FIELD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "origin": (
        "origin",
        "pol",
        "port of loading",
        "loading port",
        "from",
        "origin port",
        "origin city",
        "departure",
        "aol",
        "origin airport",
        "place of receipt",
        "por",
    ),
    "destination": (
        "destination",
        "pod",
        "port of discharge",
        "discharge port",
        "to",
        "destination port",
        "final destination",
        "aod",
        "destination airport",
        "place of delivery",
        "fnd",
    ),
    "mode": (
        "mode",
        "service",
        "transport mode",
        "service type",
        "type of service",
        "shipment type",
    ),
    "equipment": (
        "equipment",
        "container",
        "container type",
        "size",
        "eq",
        "cntr",
        "equipment type",
        "container size",
    ),
    "carrier": (
        "carrier",
        "line",
        "shipping line",
        "airline",
        "vendor",
        "supplier",
        "agent",
        "partner",
    ),
    "amount": (
        "rate",
        "price",
        "amount",
        "all in",
        "all in rate",
        "total",
        "o f",
        "ocean freight",
        "freight",
        "buy",
        "buy rate",
        "cost",
        "sell",
        "sell rate",
        "tariff",
        "net rate",
    ),
    "currency": ("currency", "curr", "ccy", "cur"),
    "valid_from": (
        "valid from",
        "effective",
        "effective date",
        "from date",
        "start date",
        "validity from",
    ),
    "valid_to": (
        "valid to",
        "validity",
        "valid till",
        "valid until",
        "expiry",
        "expiry date",
        "expires",
        "end date",
        "validity to",
    ),
    "transit_days": ("transit", "transit time", "tt", "transit days", "t t"),
    "unit": ("unit", "per", "basis", "uom", "rate basis"),
    "charge": ("charge", "charge name", "description", "surcharge", "item", "charge description"),
    "minimum": ("min", "minimum", "min charge", "minimum charge"),
    "notes": ("remarks", "notes", "comment", "comments", "remark"),
}
FIELDS = tuple(FIELD_SYNONYMS)

_EQUIPMENT_HEADER = re.compile(r"^(20|40|45)\s*(?:'|ft|feet)?\s*(gp|dv|dc|std|hc|hq|rf|rh|ot|fr)?$")
_WEIGHT_BREAK = re.compile(r"^([+-])\s*(\d{2,4})\s*(?:kg|kgs)?$|^(\d{2,4})\s*(?:kg|kgs)$")
_EQUIPMENT_CODES = {
    None: "GP",
    "gp": "GP",
    "dv": "GP",
    "dc": "GP",
    "std": "GP",
    "hc": "HC",
    "hq": "HC",
    "rf": "RF",
    "rh": "RH",
    "ot": "OT",
    "fr": "FR",
}
_CURRENCY = re.compile(
    r"\b(USD|EUR|GBP|AED|SAR|INR|CNY|LKR|CAD|TRY|QAR|OMR|KWD|BHD|JPY|SGD|AUD|CHF)\b"
)
_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "CNY"}
_SURCHARGE = re.compile(
    r"\b(baf|bunker|fuel|fsc|caf|lss|ebs|pss|gri|war risk|security|ssc|surcharge|peak)\b"
)
_ORIGIN = re.compile(r"\b(origin|pickup|pick up|export|othc|thc origin|loading)\b")
_DESTINATION = re.compile(r"\b(destination|dthc|delivery|import|discharge)\b")
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%d-%b-%Y",
    "%d/%m/%y",
    "%m/%d/%Y",
    "%b %d %Y",
    "%d-%b-%y",
)
_FILL_DOWN = (
    "origin",
    "destination",
    "mode",
    "carrier",
    "valid_from",
    "valid_to",
    "currency",
    "transit_days",
)


@dataclass
class ParsedRate:
    """One rate read from a sheet (not yet stored)."""

    tab: str
    row_number: int
    service_line: str
    mode: str | None
    origin: str
    origin_code: str | None
    origin_key: str
    destination: str
    destination_code: str | None
    destination_key: str
    equipment: str | None
    carrier: str | None
    charge_type: str
    description: str
    unit: str
    amount: Decimal
    currency: str
    minimum: Decimal | None
    valid_from: date | None
    valid_to: date | None
    transit_days: int | None
    notes: str | None


@dataclass
class ParsedSheet:
    """Everything read from a file."""

    rates: list[ParsedRate] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    mapping: dict[str, dict[str, str]] = field(default_factory=dict)
    tabs: list[str] = field(default_factory=list)

    def error(self, tab: str, row: int, message: str) -> None:
        """Record a problem (only the first `MAX_ERRORS` are kept)."""
        if len(self.errors) < MAX_ERRORS:
            self.errors.append({"tab": tab, "row": row, "message": message})


class RateSheetUnreadableError(ValueError):
    """The file is not an Excel or CSV file LaneReach can read."""


def _header_text(value: Any) -> str:
    text = str(value or "").strip().lower().replace("\n", " ")
    return " ".join(re.sub(r"[^a-z0-9+\-' ]+", " ", text).split())


def read_tables(content: bytes, filename: str) -> list[tuple[str, list[list[Any]]]]:
    """The rows of every worksheet (or the CSV file as one tab).

    Raises:
        RateSheetUnreadableError: For files that are not .xlsx or .csv, or are damaged.
    """
    name = filename.lower()
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        import openpyxl

        try:
            workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as exc:
            raise RateSheetUnreadableError(
                "The Excel file cannot be read (is it a valid .xlsx?)."
            ) from exc
        tables = []
        for sheet in workbook.worksheets:
            rows: list[list[Any]] = []
            for row in sheet.iter_rows(values_only=True):
                rows.append(list(row))
                if len(rows) >= MAX_ROWS:
                    break
            tables.append((str(sheet.title), rows))
        workbook.close()
        return tables
    if name.endswith(".csv"):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        try:
            dialect: Any = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = [
            list(row)
            for row, _ in zip(csv.reader(io.StringIO(text), dialect), range(MAX_ROWS), strict=False)
        ]
        return [("CSV", rows)]
    raise RateSheetUnreadableError("Rate sheets must be .xlsx or .csv files.")


def _match_field(header: str) -> str | None:
    for name, synonyms in FIELD_SYNONYMS.items():
        if header in synonyms:
            return name
    return None


def _wide_column(header: str) -> tuple[str, str] | None:
    compact = header.replace(" ", "")
    container = _EQUIPMENT_HEADER.match(compact)
    if container:
        return f"{container.group(1)}{_EQUIPMENT_CODES[container.group(2)]}", "container"
    weight = _WEIGHT_BREAK.match(compact)
    if weight:
        sign = weight.group(1) or "+"
        kilos = weight.group(2) or weight.group(3)
        return f"{sign}{kilos}kg", "kg"
    return None


@dataclass
class _Layout:
    header_row: int
    columns: dict[str, int]
    wide: dict[int, tuple[str, str]]
    headers: list[str]
    labels: list[str]


def detect_layout(rows: list[list[Any]], override: dict[str, str] | None = None) -> _Layout | None:
    """Find the header row and what each column holds.

    Args:
        rows: The tab's rows.
        override: A saved mapping (field -> header text) that wins over the guess.

    Returns:
        The layout, or None when no row looks like a rate header.
    """
    for index, row in enumerate(rows[:_HEADER_SCAN]):
        headers = [_header_text(cell) for cell in row]
        columns: dict[str, int] = {}
        wide: dict[int, tuple[str, str]] = {}
        for position, header in enumerate(headers):
            if not header:
                continue
            matched = _match_field(header)
            if matched and matched not in columns:
                columns[matched] = position
                continue
            column = _wide_column(header)
            if column:
                wide[position] = column
        for name, header in (override or {}).items():
            wanted = _header_text(header)
            if name in FIELD_SYNONYMS and wanted in headers:
                columns[name] = headers.index(wanted)
        if "origin" in columns and "destination" in columns and ("amount" in columns or wide):
            labels = [" ".join(str(cell).split()) if cell is not None else "" for cell in row]
            return _Layout(index, columns, wide, headers, labels)
    return None


def parse_amount(value: Any) -> tuple[Decimal | None, str | None]:
    """An amount and the currency written with it, if any (`USD 1,250.00`, `€ 1.250,50`)."""
    if value is None or value == "":
        return None, None
    if isinstance(value, int | float | Decimal) and not isinstance(value, bool):
        return Decimal(str(value)).quantize(Decimal("0.01")), None
    text = str(value).strip()
    currency = None
    match = _CURRENCY.search(text.upper())
    if match:
        currency = match.group(1)
    for symbol, code in _SYMBOLS.items():
        if symbol in text:
            currency = currency or code
    digits = re.sub(r"[^0-9,.\-]", "", text)
    if not digits or digits in {"-", ".", ","}:
        return None, currency
    if "," in digits and "." in digits:
        # The last separator is the decimal one: 1.250,50 or 1,250.50.
        if digits.rfind(",") > digits.rfind("."):
            digits = digits.replace(".", "").replace(",", ".")
        else:
            digits = digits.replace(",", "")
    elif "," in digits:
        head, _, tail = digits.rpartition(",")
        digits = (
            f"{head.replace(',', '')}.{tail}" if len(tail) in (1, 2) else digits.replace(",", "")
        )
    try:
        return Decimal(digits).quantize(Decimal("0.01")), currency
    except InvalidOperation:
        return None, currency


def parse_date(value: Any) -> date | None:
    """A date from an Excel date cell or the usual written formats."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = " ".join(str(value).replace(",", " ").split())
    for pattern in _DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _integer(value: Any) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else None


def _unit(text: str, equipment: str | None, wide_unit: str | None) -> str:
    value = text.lower()
    if wide_unit:
        return wide_unit
    for words, unit in (
        (("container", "cntr", "box", "teu", "per unit", "equipment"), "container"),
        (("kg", "kilo"), "kg"),
        (("cbm", "w/m", "wm", "rt", "m3"), "cbm"),
        (("truck", "trailer"), "truck"),
        (("pallet", "plt"), "pallet"),
        (("wagon",), "wagon"),
        (("bl", "b/l", "awb", "shipment", "set", "per doc", "document"), "shipment"),
    ):
        if any(word in value for word in words):
            return unit
    if equipment and equipment[:2] in ("20", "40", "45"):
        return "container"
    if equipment and equipment.endswith("kg"):
        return "kg"
    return "shipment"


def _mode(text: str, equipment: str | None, unit: str) -> str | None:
    value = text.lower()
    sea = "lcl" if unit == "cbm" else "fcl"
    for word, mode in (
        ("lcl", "lcl"),
        ("fcl", "fcl"),
        ("reefer", sea),
        ("air", "air"),
        ("ftl", "ftl"),
        ("ltl", "ltl"),
        ("truck", "ftl"),
        ("road", "ftl"),
        ("rail", "rail_fcl"),
        ("courier", "courier"),
        ("express", "courier"),
        ("ocean", sea),
        ("sea", sea),
    ):
        if re.search(rf"\b{word}\b", value):
            return mode
    if unit == "container" or (equipment and equipment[:2] in ("20", "40", "45")):
        return "fcl"
    if unit == "kg":
        return "air"
    if unit == "cbm":
        return "lcl"
    if unit == "truck":
        return "ftl"
    return None


def _charge_type(text: str) -> str:
    value = text.lower()
    if _SURCHARGE.search(value):
        return "surcharge"
    if "customs" in value or "clearance" in value:
        return "customs"
    if "insurance" in value:
        return "insurance"
    if _DESTINATION.search(value):
        return "destination"
    if _ORIGIN.search(value):
        return "origin"
    return "freight"


def _default_description(mode: str | None) -> str:
    return {
        "air": "Air freight",
        "lcl": "Ocean freight (LCL)",
        "fcl": "Ocean freight",
        "ftl": "Road freight",
        "ltl": "Road freight (LTL)",
        "rail_fcl": "Rail freight",
        "courier": "Courier",
    }.get(mode or "", "Freight")


def parse_rate_sheet(
    content: bytes,
    filename: str,
    *,
    default_currency: str = "USD",
    mapping: dict[str, dict[str, str]] | None = None,
) -> ParsedSheet:
    """Read every rate of a file.

    Args:
        content: The file bytes.
        filename: Its name (`.xlsx` or `.csv` decides the reader).
        default_currency: Used when neither a column nor the amount says which currency.
        mapping: Saved overrides per tab (field -> header text).

    Returns:
        The rates, the problems found and the mapping that was used.

    Raises:
        RateSheetUnreadableError: When the file cannot be read at all.
    """
    result = ParsedSheet()
    for tab, rows in read_tables(content, filename):
        result.tabs.append(tab)
        layout = detect_layout(rows, (mapping or {}).get(tab))
        if layout is None:
            if any(any(cell not in (None, "") for cell in row) for row in rows[:_HEADER_SCAN]):
                result.error(
                    tab, 0, "No header row with origin, destination and a rate column was found."
                )
            continue
        result.mapping[tab] = {
            name: layout.labels[position] for name, position in layout.columns.items()
        }
        for position, (equipment, _unit_name) in layout.wide.items():
            result.mapping[tab][f"rate:{equipment}"] = layout.labels[position]
        previous: dict[str, Any] = {}
        for offset, row in enumerate(rows[layout.header_row + 1 :], start=layout.header_row + 2):
            values = {
                name: (row[index] if index < len(row) else None)
                for name, index in layout.columns.items()
            }
            if all(value in (None, "") for value in row):
                continue
            for name in _FILL_DOWN:
                if values.get(name) in (None, "") and name in previous:
                    values[name] = previous[name]
            previous.update(
                {name: value for name, value in values.items() if value not in (None, "")}
            )
            _read_row(result, tab, offset, row, values, layout, default_currency)
    return result


def _read_row(
    result: ParsedSheet,
    tab: str,
    row_number: int,
    row: list[Any],
    values: dict[str, Any],
    layout: _Layout,
    default_currency: str,
) -> None:
    origin = str(values.get("origin") or "").strip()
    destination = str(values.get("destination") or "").strip()
    if not origin or not destination:
        result.error(tab, row_number, "Origin or destination is empty.")
        return
    prices: list[tuple[Any, str | None, str | None]] = []
    if "amount" in layout.columns:
        prices.append(
            (values.get("amount"), str(values.get("equipment") or "").strip() or None, None)
        )
    for position, (column_equipment, column_unit) in layout.wide.items():
        cell = row[position] if position < len(row) else None
        if cell not in (None, ""):
            prices.append((cell, column_equipment, column_unit))
    column_currency = str(values.get("currency") or "").strip().upper()[:3] or None
    charge_text = str(values.get("charge") or "").strip()
    minimum, _ = parse_amount(values.get("minimum"))
    found = False
    for raw_amount, equipment, wide_unit in prices:
        amount, written_currency = parse_amount(raw_amount)
        if amount is None:
            if raw_amount not in (None, ""):
                result.error(tab, row_number, f"Cannot read the rate {str(raw_amount)[:30]!r}.")
            continue
        found = True
        equipment_code = normalise_equipment(equipment)
        unit = _unit(str(values.get("unit") or ""), equipment_code, wide_unit)
        mode = _mode(str(values.get("mode") or ""), equipment_code, unit)
        result.rates.append(
            ParsedRate(
                tab=tab[:100],
                row_number=row_number,
                service_line=line_for_mode(mode).value,
                mode=mode,
                origin=origin[:200],
                origin_code=place_code(origin),
                origin_key=place_key(origin)[:200],
                destination=destination[:200],
                destination_code=place_code(destination),
                destination_key=place_key(destination)[:200],
                equipment=equipment_code,
                carrier=(str(values.get("carrier") or "").strip()[:120] or None),
                charge_type=_charge_type(charge_text) if charge_text else "freight",
                description=(charge_text or _default_description(mode))[:200],
                unit=unit,
                amount=amount,
                currency=(column_currency or written_currency or default_currency).upper(),
                minimum=minimum,
                valid_from=parse_date(values.get("valid_from")),
                valid_to=parse_date(values.get("valid_to")),
                transit_days=_integer(values.get("transit_days")),
                notes=(str(values.get("notes") or "").strip()[:500] or None),
            )
        )
    if not found and not any(error["row"] == row_number for error in result.errors):
        result.error(tab, row_number, "No rate on this row.")


def normalise_equipment(text: str | None) -> str | None:
    """A container code as rates store it (`40 HQ` -> `40HC`); other codes upper-cased."""
    if not text:
        return None
    if text.endswith("kg"):
        return text
    column = _wide_column(_header_text(text))
    if column:
        return column[0]
    value = text.strip().upper()
    return value[:30] or None
