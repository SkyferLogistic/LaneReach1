"""`draft_quote_note`: the short message above a quote, using only the quote's own facts.

Every number in the text must appear in the quote facts (total, lines, validity, transit), and
links are refused, so the note can never promise a price or date the quote does not contain.
"""

from __future__ import annotations

import json
import re
from typing import Any

from lanereach.ai.prompts.registry import wrap_untrusted
from lanereach.ai.schemas import QuoteCoverNote
from lanereach.ai.tasks.runtime import AIRuntime, TaskResult, run_prompted_task
from lanereach.ai.tasks.write_digest import numbers_in

TASK = "draft_quote_note"
_LINK = re.compile(r"https?://|www\.", re.IGNORECASE)


async def draft_quote_note(
    runtime: AIRuntime, facts: dict[str, Any], *, input_ref: str | None = None
) -> TaskResult[QuoteCoverNote]:
    """Write the cover note.

    Args:
        runtime: AI runtime.
        facts: The quote (lane, mode, lines, total, currency, validity, transit, notes, language).
        input_ref: The quote the run is about.

    Returns:
        The note and its AI run ID.

    Raises:
        LLMError: When the call fails or the model keeps using numbers not in the quote.
    """
    facts_text = json.dumps(facts, ensure_ascii=False, default=str)
    allowed = numbers_in(facts_text)

    def only_quote_facts(output: QuoteCoverNote) -> None:
        if _LINK.search(output.intro):
            raise ValueError("the note must not contain links")
        unknown = numbers_in(output.intro) - allowed
        if unknown:
            raise ValueError(
                "use only numbers from <quote_facts>; not found: " + ", ".join(sorted(unknown))
            )

    result, run_id = await run_prompted_task(
        runtime,
        task=TASK,
        values={"quote_facts": wrap_untrusted(facts_text, "quote_facts")},
        output_model=QuoteCoverNote,
        input_ref=input_ref,
        max_tokens=700,
        validate=only_quote_facts,
    )
    return TaskResult(result.output, run_id)
