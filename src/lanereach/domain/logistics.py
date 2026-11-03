"""The logistics catalogue: service lines, transport modes, Incoterms and quote charge presets.

One place for what a forwarder sells, so agents, scripts, deals, quotes and the console use the
same words. `ServiceLine` itself lives with the senders (agents belong to a line).
"""

from __future__ import annotations

from dataclasses import dataclass

from lanereach.domain.senders.models import ServiceLine


@dataclass(frozen=True, slots=True)
class ModeOption:
    """A transport mode or service type within a line."""

    key: str
    label: str


@dataclass(frozen=True, slots=True)
class ChargePreset:
    """A typical quote line for a line/mode (the builder offers these)."""

    charge_type: str
    description: str
    unit: str


LINE_LABELS: dict[ServiceLine, str] = {
    ServiceLine.AIR: "Air freight",
    ServiceLine.OCEAN: "Ocean freight",
    ServiceLine.ROAD: "Road freight",
    ServiceLine.RAIL: "Rail freight",
    ServiceLine.COURIER: "Courier & express",
    ServiceLine.CUSTOMS: "Customs brokerage",
    ServiceLine.WAREHOUSING: "Warehousing",
    ServiceLine.PROJECT: "Project cargo",
    ServiceLine.ANY: "All services",
}

MODES: dict[ServiceLine, tuple[ModeOption, ...]] = {
    ServiceLine.AIR: (
        ModeOption("air", "Air general cargo"),
        ModeOption("air_express", "Air express"),
        ModeOption("air_charter", "Charter"),
    ),
    ServiceLine.OCEAN: (
        ModeOption("fcl", "FCL (full container)"),
        ModeOption("lcl", "LCL (groupage)"),
        ModeOption("roro", "Ro-Ro"),
        ModeOption("breakbulk", "Breakbulk"),
    ),
    ServiceLine.ROAD: (
        ModeOption("ftl", "FTL (full truck)"),
        ModeOption("ltl", "LTL (part load)"),
        ModeOption("reefer_truck", "Reefer truck"),
    ),
    ServiceLine.RAIL: (
        ModeOption("rail_fcl", "Rail container"),
        ModeOption("rail_wagon", "Wagon load"),
        ModeOption("block_train", "Block train"),
    ),
    ServiceLine.COURIER: (
        ModeOption("courier", "Door-to-door express"),
        ModeOption("courier_economy", "Economy parcel"),
    ),
    ServiceLine.CUSTOMS: (
        ModeOption("import_clearance", "Import clearance"),
        ModeOption("export_clearance", "Export clearance"),
        ModeOption("transit", "Transit (T1)"),
    ),
    ServiceLine.WAREHOUSING: (
        ModeOption("storage", "Storage"),
        ModeOption("fulfilment", "Pick, pack & fulfilment"),
        ModeOption("cross_dock", "Cross-dock"),
    ),
    ServiceLine.PROJECT: (
        ModeOption("heavy_lift", "Heavy lift"),
        ModeOption("oog", "Out of gauge"),
        ModeOption("turnkey", "Turnkey project"),
    ),
}

INCOTERMS = ("EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP")

UNITS = (
    "kg",
    "cbm",
    "container",
    "truck",
    "wagon",
    "pallet",
    "shipment",
    "hour",
    "day",
    "week",
    "document",
    "piece",
)

_PRESETS: dict[ServiceLine, tuple[ChargePreset, ...]] = {
    ServiceLine.AIR: (
        ChargePreset("freight", "Air freight", "kg"),
        ChargePreset("surcharge", "Fuel surcharge", "kg"),
        ChargePreset("surcharge", "Security surcharge", "kg"),
        ChargePreset("origin", "Origin handling", "shipment"),
        ChargePreset("origin", "Air waybill fee", "document"),
        ChargePreset("destination", "Destination handling", "shipment"),
    ),
    ServiceLine.OCEAN: (
        ChargePreset("freight", "Ocean freight", "container"),
        ChargePreset("surcharge", "Bunker adjustment (BAF)", "container"),
        ChargePreset("origin", "Terminal handling at origin", "container"),
        ChargePreset("destination", "Terminal handling at destination", "container"),
        ChargePreset("origin", "Bill of lading", "document"),
        ChargePreset("customs", "Export customs clearance", "shipment"),
    ),
    ServiceLine.ROAD: (
        ChargePreset("freight", "Road freight", "truck"),
        ChargePreset("surcharge", "Fuel surcharge", "truck"),
        ChargePreset("other", "Waiting time", "hour"),
        ChargePreset("other", "Tolls", "shipment"),
    ),
    ServiceLine.RAIL: (
        ChargePreset("freight", "Rail freight", "container"),
        ChargePreset("origin", "Terminal handling", "container"),
        ChargePreset("destination", "Last-mile delivery", "container"),
    ),
    ServiceLine.COURIER: (
        ChargePreset("freight", "Express delivery", "kg"),
        ChargePreset("surcharge", "Fuel surcharge", "shipment"),
        ChargePreset("customs", "Duties & taxes (at cost)", "shipment"),
    ),
    ServiceLine.CUSTOMS: (
        ChargePreset("customs", "Customs clearance", "shipment"),
        ChargePreset("customs", "Additional HS code lines", "piece"),
        ChargePreset("customs", "Duties & taxes (at cost)", "shipment"),
        ChargePreset("other", "Documentation", "document"),
    ),
    ServiceLine.WAREHOUSING: (
        ChargePreset("storage", "Storage", "pallet"),
        ChargePreset("storage", "Handling in", "pallet"),
        ChargePreset("storage", "Handling out", "pallet"),
        ChargePreset("other", "Pick & pack", "piece"),
    ),
    ServiceLine.PROJECT: (
        ChargePreset("freight", "Heavy-lift transport", "shipment"),
        ChargePreset("other", "Permits and escorts", "shipment"),
        ChargePreset("insurance", "Cargo insurance", "shipment"),
    ),
}


def charge_presets(line: ServiceLine) -> tuple[ChargePreset, ...]:
    """Typical charges of a line (air's for `any`)."""
    return _PRESETS.get(line, _PRESETS[ServiceLine.AIR])


def line_for_mode(mode: str | None) -> ServiceLine:
    """The service line an extracted or typed mode belongs to.

    Args:
        mode: For example `FCL`, `air`, `road`, `courier`.

    Returns:
        The line, `any` when unknown.
    """
    value = (mode or "").strip().lower()
    if value in {"air", "air_express", "air_charter"}:
        return ServiceLine.AIR
    if value in {"ocean", "sea", "fcl", "lcl", "roro", "breakbulk"}:
        return ServiceLine.OCEAN
    for line, options in MODES.items():
        if value == line.value or value in {option.key for option in options}:
            return line
    return ServiceLine.ANY


ALL_MODE_KEYS = frozenset(option.key for options in MODES.values() for option in options) | {
    "ocean",
    "sea",
    "road",
    "rail",
}
