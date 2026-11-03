"""`classify_reply`: what does an inbound reply mean?"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from lanereach.ai.llm.providers.heuristics import detect_language
from lanereach.ai.prompts.registry import wrap_untrusted
from lanereach.ai.rules.reply_rules import ReplyMessage, RuleClassification, classify_reply_by_rules
from lanereach.ai.runs.models import AIRunStatus
from lanereach.ai.runs.service import hash_prompt_input
from lanereach.ai.schemas import ReplyClassification
from lanereach.ai.tasks.runtime import AIRuntime, run_prompted_task

TASK = "classify_reply"


@dataclass(frozen=True)
class ReplyContext:
    """What the classifier may know about the outreach being answered."""

    office_name: str = "our office"
    contact_country: str = "unknown"
    input_ref: str | None = None


@dataclass(frozen=True)
class ClassificationOutcome:
    """The classification, where it came from and the AI run."""

    classification: ReplyClassification
    source: Literal["rules", "llm"]
    rule: RuleClassification | None
    ai_run_id: uuid.UUID


async def classify_reply(
    runtime: AIRuntime,
    message: ReplyMessage,
    context: ReplyContext,
    *,
    use_rules: bool = True,
) -> ClassificationOutcome:
    """Classify a reply: deterministic rules first, the LLM only when no rule matches.

    Below `AI_REVIEW_CONFIDENCE_THRESHOLD` the result is marked `needs_human` regardless of what
    the model said: the threshold is policy, not something the model decides.

    Args:
        runtime: AI runtime.
        message: The reply.
        context: Outreach context.
        use_rules: Run the rules pre-classifier first.

    Returns:
        The outcome.

    Raises:
        LLMError: If the model call fails.
    """
    if use_rules:
        rule = classify_reply_by_rules(message)
        if rule is not None:
            classification = ReplyClassification(
                bucket=rule.bucket,
                confidence=1.0,
                language=detect_language(f"{message.subject}\n{message.body}"),
                summary=rule.rule[:400],
                reasoning_short=f"Rule: {rule.rule}"[:400],
                needs_human=False,
            )
            run_id = await runtime.recorder.record_run(
                task=TASK,
                prompt_version="rules",
                provider="rules",
                model="reply_rules",
                input_ref=context.input_ref,
                input_hash=hash_prompt_input(message.subject, message.body),
                status=AIRunStatus.RULE_MATCHED,
                output=classification.model_dump(mode="json"),
                confidence=1.0,
            )
            return ClassificationOutcome(classification, "rules", rule, run_id)

    result, run_id = await run_prompted_task(
        runtime,
        task=TASK,
        values={
            "office_name": context.office_name,
            "contact_country": context.contact_country,
            "email_content": wrap_untrusted(f"Subject: {message.subject}\n\n{message.body}"),
        },
        output_model=ReplyClassification,
        input_ref=context.input_ref,
        max_tokens=1024,
    )
    classification = result.output
    if classification.confidence < runtime.settings.ai_review_confidence_threshold:
        classification = classification.model_copy(update={"needs_human": True})
    return ClassificationOutcome(classification, "llm", None, run_id)
