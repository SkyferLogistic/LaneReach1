"""Mock heuristic for `match_lane`: known codes first, then the closest spelling (difflib)."""

from __future__ import annotations

import difflib
import json
import re
from typing import Any

from lanereach.domain.pricing.places import normalise_place, place_code

_LANE = re.compile(r"<shipment_lane>\s*(.*?)\s*</shipment_lane>", re.DOTALL)
_PLACES = re.compile(r"<rate_sheet_places>\s*(.*?)\s*</rate_sheet_places>", re.DOTALL)
_CUTOFF = 0.75


def _block(pattern: re.Pattern[str], prompt: str) -> dict[str, Any]:
    match = pattern.search(prompt)
    try:
        value = json.loads(match.group(1)) if match else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def closest_place(text: str, candidates: list[str]) -> str | None:
    """The candidate that is the same place as `text`, or None.

    Args:
        text: The requested place.
        candidates: Places written in the rate sheets.

    Returns:
        A candidate copied exactly, or None when nothing is close enough.
    """
    if not text or not candidates:
        return None
    code = place_code(text)
    if code:
        for candidate in candidates:
            if place_code(candidate) == code:
                return candidate
    wanted = normalise_place(text)
    names = {normalise_place(candidate): candidate for candidate in candidates}
    for name, candidate in names.items():
        if name and (name in wanted or wanted in name):
            return candidate
    close = difflib.get_close_matches(wanted, list(names), n=1, cutoff=_CUTOFF)
    return names[close[0]] if close else None


def match_lane_heuristically(prompt: str) -> dict[str, Any]:
    """Match the lane in a `match_lane` prompt against its listed places."""
    lane = _block(_LANE, prompt)
    places = _block(_PLACES, prompt)
    origin = closest_place(str(lane.get("origin") or ""), list(places.get("origins") or []))
    destination = closest_place(
        str(lane.get("destination") or ""), list(places.get("destinations") or [])
    )
    found = sum(value is not None for value in (origin, destination))
    return {
        "origin": origin,
        "destination": destination,
        "confidence": {0: 0.0, 1: 0.4, 2: 0.8}[found],
        "reasoning_short": "Closest spelling among the rate-sheet places.",
    }
