"""`write_digest`: the weekly digest written from the week's numbers.

The model may only use numbers that are in the statistics it was given: every number in its
headline, summary and highlights is checked against them, and an answer with an unknown number is
rejected (the model retries once, then the caller falls back to the rule-written digest).
"""

from __future__ import annotations

import json
import re
from typing import Any

from lanereach.ai.prompts.registry import wrap_untrusted
from lanereach.ai.schemas import DigestNarrative
from lanereach.ai.tasks.runtime import AIRuntime, TaskResult, run_prompted_task

TASK = "write_digest"
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def numbers_in(text: str) -> set[str]:
    """The numbers written in a text, normalised (`1,250.5` -> `1250.5`, `40%` -> `40`)."""
    found = set()
    for token in _NUMBER.findall(text):
        plain = token.replace(",", "")
        found.add(plain.rstrip("0").rstrip(".") if "." in plain else plain)
    return found


def unknown_numbers(narrative: DigestNarrative, stats_text: str) -> set[str]:
    """Numbers the narrative uses that are not in the statistics."""
    texts = [narrative.headline, narrative.summary]
    texts += [part for item in narrative.highlights for part in (item.title, item.detail)]
    used = set().union(*(numbers_in(text) for text in texts))
    return used - numbers_in(stats_text)


async def write_digest(
    runtime: AIRuntime, stats: dict[str, Any], *, input_ref: str | None = None
) -> TaskResult[DigestNarrative]:
    """Write the digest.

    Args:
        runtime: AI runtime.
        stats: The week's statistics (lane and office names come from emails: untrusted).
        input_ref: The digest the run is about.

    Returns:
        The narrative and its AI run ID.

    Raises:
        LLMError: When the call fails or the model keeps using numbers that are not in the stats.
    """
    stats_text = json.dumps(stats, ensure_ascii=False, default=str)

    def only_known_numbers(output: DigestNarrative) -> None:
        unknown = unknown_numbers(output, stats_text)
        if unknown:
            raise ValueError(
                "use only numbers from <digest_stats>; not found: " + ", ".join(sorted(unknown))
            )

    result, run_id = await run_prompted_task(
        runtime,
        task=TASK,
        values={"digest_stats": wrap_untrusted(stats_text, "digest_stats")},
        output_model=DigestNarrative,
        input_ref=input_ref,
        max_tokens=1500,
        validate=only_known_numbers,
    )
    return TaskResult(result.output, run_id)
