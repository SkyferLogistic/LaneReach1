"""`suggest_follow_up`: the next step for a deal that went quiet, with a draft for a person."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from lanereach.ai.prompts.registry import wrap_untrusted
from lanereach.ai.schemas import FollowUpProposal
from lanereach.ai.tasks.runtime import AIRuntime, TaskResult, run_prompted_task

TASK = "suggest_follow_up"
MAX_MESSAGE = 700
_LINK = re.compile(r"https?://|www\.", re.IGNORECASE)


@dataclass(frozen=True)
class ConversationTurn:
    """One message of the deal's conversation."""

    direction: str  # "us" or "them"
    occurred_on: str
    subject: str
    body: str


def _no_links(output: FollowUpProposal) -> None:
    if _LINK.search(output.body) or _LINK.search(output.subject):
        raise ValueError("the draft must not contain links")
    if output.action == "email" and not output.body.strip():
        raise ValueError("an email suggestion needs a body")


async def suggest_follow_up(
    runtime: AIRuntime,
    *,
    deal_context: str,
    conversation: Sequence[ConversationTurn],
    input_ref: str | None = None,
) -> TaskResult[FollowUpProposal]:
    """Suggest a next step and draft it.

    Args:
        runtime: AI runtime.
        deal_context: Lane, mode, stage, days quiet, quote status (no personal data).
        conversation: The latest messages, oldest first (untrusted).
        input_ref: The deal the run is about.

    Returns:
        The proposal and its AI run ID.

    Raises:
        LLMError: When the model call fails or keeps producing invalid drafts.
    """
    text = (
        "\n\n".join(
            f"[{turn.direction} · {turn.occurred_on}] Subject: {turn.subject}\n"
            f"{turn.body[:MAX_MESSAGE]}"
            for turn in conversation
        )
        or "(no messages yet)"
    )
    result, run_id = await run_prompted_task(
        runtime,
        task=TASK,
        values={
            "deal_context": deal_context[:400],
            "conversation": wrap_untrusted(text, "conversation"),
        },
        output_model=FollowUpProposal,
        input_ref=input_ref,
        max_tokens=900,
        validate=_no_links,
    )
    return TaskResult(result.output, run_id)
