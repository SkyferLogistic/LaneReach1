"""`assess_lead`: how keen a prospect sounds in their latest reply (one input of lead scoring)."""

from __future__ import annotations

from lanereach.ai.prompts.registry import wrap_untrusted
from lanereach.ai.schemas import LeadAssessment
from lanereach.ai.tasks.runtime import AIRuntime, TaskResult, run_prompted_task

TASK = "assess_lead"
MAX_BODY = 3000


async def assess_lead(
    runtime: AIRuntime,
    *,
    subject: str,
    body: str,
    deal_context: str,
    input_ref: str | None = None,
) -> TaskResult[LeadAssessment]:
    """Read buying intent from a reply.

    Args:
        runtime: AI runtime.
        subject: Subject of the reply.
        body: The reply (untrusted).
        deal_context: One line about the deal (lane, mode, stage); no personal data.
        input_ref: The message the run is about.

    Returns:
        The assessment and its AI run ID.

    Raises:
        LLMError: When the model call fails.
    """
    result, run_id = await run_prompted_task(
        runtime,
        task=TASK,
        values={
            "deal_context": deal_context[:300],
            "email_content": wrap_untrusted(f"Subject: {subject}\n\n{body[:MAX_BODY]}"),
        },
        output_model=LeadAssessment,
        input_ref=input_ref,
        max_tokens=600,
    )
    return TaskResult(result.output, run_id)
