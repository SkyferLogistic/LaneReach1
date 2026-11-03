"""`match_lane`: map a customer's spelling of a lane onto the places in the rate sheets.

Used only when the deterministic match (codes and normalised names) finds nothing. The model
may only answer with places that exist in the tenant's sheets; anything else is rejected by
validation, so it can never invent a lane or a price.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from lanereach.ai.prompts.registry import wrap_untrusted
from lanereach.ai.schemas import LaneMatch
from lanereach.ai.tasks.runtime import AIRuntime, TaskResult, run_prompted_task

TASK = "match_lane"
MAX_PLACES = 300


async def match_lane(
    runtime: AIRuntime,
    *,
    origin: str,
    destination: str,
    origins: Sequence[str],
    destinations: Sequence[str],
    input_ref: str | None = None,
) -> TaskResult[LaneMatch]:
    """Pick the sheet places that are the requested origin and destination.

    Args:
        runtime: AI runtime.
        origin: Requested origin as written (untrusted).
        destination: Requested destination as written (untrusted).
        origins: Origins present in the tenant's rate sheets.
        destinations: Destinations present in the tenant's rate sheets.
        input_ref: The deal the run is about.

    Returns:
        The validated match and its AI run ID.

    Raises:
        LLMError: When the call fails or the model keeps answering with unknown places.
    """
    allowed_origins = list(dict.fromkeys(origins))[:MAX_PLACES]
    allowed_destinations = list(dict.fromkeys(destinations))[:MAX_PLACES]

    def only_listed(output: LaneMatch) -> None:
        if output.origin is not None and output.origin not in allowed_origins:
            raise ValueError("origin must be one of the listed rate-sheet origins or null")
        if output.destination is not None and output.destination not in allowed_destinations:
            raise ValueError(
                "destination must be one of the listed rate-sheet destinations or null"
            )

    lane = json.dumps(
        {"origin": origin[:200], "destination": destination[:200]}, ensure_ascii=False
    )
    places = json.dumps(
        {"origins": allowed_origins, "destinations": allowed_destinations}, ensure_ascii=False
    )
    result, run_id = await run_prompted_task(
        runtime,
        task=TASK,
        values={
            "shipment_lane": wrap_untrusted(lane, "shipment_lane"),
            "rate_sheet_places": wrap_untrusted(places, "rate_sheet_places"),
        },
        output_model=LaneMatch,
        input_ref=input_ref,
        max_tokens=400,
        validate=only_listed,
    )
    return TaskResult(result.output, run_id)
