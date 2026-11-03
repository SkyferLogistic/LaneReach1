"""`summarize_thread`: where a conversation stands, what the prospect wants, what to do next."""

from __future__ import annotations

from collections.abc import Sequence

from lanereach.ai.prompts.registry import wrap_untrusted
from lanereach.ai.schemas import ThreadSummary
from lanereach.ai.tasks.runtime import AIRuntime, TaskResult, run_prompted_task
from lanereach.ai.tasks.suggest_follow_up import ConversationTurn

TASK = "summarize_thread"
MAX_TURNS = 12
MAX_MESSAGE = 1200


async def summarize_thread(
    runtime: AIRuntime, conversation: Sequence[ConversationTurn], *, input_ref: str | None = None
) -> TaskResult[ThreadSummary]:
    """Summarise the latest messages of a conversation (oldest first, untrusted).

    Raises:
        LLMError: When the model call fails.
    """
    turns = list(conversation)[-MAX_TURNS:]
    text = "\n\n".join(
        f"[{turn.direction} · {turn.occurred_on}] Subject: {turn.subject}\n"
        f"{turn.body[:MAX_MESSAGE]}"
        for turn in turns
    )
    result, run_id = await run_prompted_task(
        runtime,
        task=TASK,
        values={"conversation": wrap_untrusted(text or "(no messages)", "conversation")},
        output_model=ThreadSummary,
        input_ref=input_ref,
        max_tokens=800,
    )
    return TaskResult(result.output, run_id)
