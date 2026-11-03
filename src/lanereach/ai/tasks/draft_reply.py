"""`draft_reply`: a reply in the office's voice and the prospect's language.

The office's own answers to similar earlier emails (`lanereach.ai.history`) go in as
examples of its tone and facts, never as content to copy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from lanereach.ai.history import PastAnswer, past_answers_text
from lanereach.ai.prompts.registry import wrap_untrusted
from lanereach.ai.rules.reply_rules import ReplyMessage
from lanereach.ai.schemas import ReplyBucket, ReplyDraft
from lanereach.ai.tasks.runtime import AIRuntime, TaskResult, run_prompted_task

TASK = "draft_reply"


@dataclass(frozen=True)
class DraftContext:
    """Who is writing to whom, and how."""

    office_name: str
    office_signature: str
    sender_name: str
    contact_first_name: str | None
    language: str
    bucket: ReplyBucket
    tone: str = "friendly"
    template_key: str | None = None
    template_body: str | None = None
    past_answers: list[PastAnswer] = field(default_factory=list)


async def draft_reply(
    runtime: AIRuntime,
    message: ReplyMessage,
    context: DraftContext,
    *,
    input_ref: str | None = None,
) -> TaskResult[ReplyDraft]:
    """Draft a reply to `message`.

    Args:
        runtime: AI runtime.
        message: The prospect's reply.
        context: Office, sender, language and optional template.
        input_ref: Message ID for the AI run.

    Returns:
        The draft and the AI run ID.

    Raises:
        LLMError: If the model call fails.
    """
    draft_context = {
        "office_name": context.office_name,
        "office_signature": context.office_signature,
        "sender_name": context.sender_name,
        "contact_first_name": context.contact_first_name,
        "language": context.language,
        "bucket": context.bucket.value,
        "tone": context.tone,
        "template_key": context.template_key,
        "template_body": context.template_body,
        "original_subject": message.subject,
    }
    result, run_id = await run_prompted_task(
        runtime,
        task=TASK,
        values={
            "draft_context": wrap_untrusted(
                json.dumps(draft_context, ensure_ascii=False), "draft_context"
            ),
            "past_answers": wrap_untrusted(past_answers_text(context.past_answers), "past_answers"),
            "email_content": wrap_untrusted(f"Subject: {message.subject}\n\n{message.body}"),
        },
        output_model=ReplyDraft,
        input_ref=input_ref,
        max_tokens=2048,
    )
    return TaskResult(result.output, run_id)
