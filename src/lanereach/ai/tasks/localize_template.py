"""`localize_template`: translate a template without breaking its placeholders."""

from __future__ import annotations

import json
from collections import Counter

from lanereach.ai.prompts.registry import wrap_untrusted
from lanereach.ai.schemas import LocalizedTemplate
from lanereach.ai.tasks.runtime import AIRuntime, TaskResult, run_prompted_task
from lanereach.domain.templates.rendering import PLACEHOLDER

TASK = "localize_template"


def placeholder_tokens(text: str) -> Counter[str]:
    """Every placeholder token exactly as written (including fallbacks), with counts.

    Args:
        text: Template text.

    Returns:
        Token multiset.
    """
    return Counter(match.group(0) for match in PLACEHOLDER.finditer(text))


def check_placeholders_preserved(subject: str, body: str, localized: LocalizedTemplate) -> None:
    """Reject a localization that added, removed or altered placeholders.

    Args:
        subject: Original subject.
        body: Original body.
        localized: Model output.

    Raises:
        ValueError: With the differences, so the model can correct itself.
    """
    expected = placeholder_tokens(subject) + placeholder_tokens(body)
    actual = placeholder_tokens(localized.subject) + placeholder_tokens(localized.body)
    if expected != actual:
        missing = sorted((expected - actual).elements())
        extra = sorted((actual - expected).elements())
        raise ValueError(
            f"placeholders changed: missing {missing or 'none'}, unexpected {extra or 'none'}; "
            "copy every placeholder exactly"
        )


async def localize_template(
    runtime: AIRuntime,
    *,
    subject: str,
    body: str,
    target_language: str,
    region: str | None,
    input_ref: str | None = None,
) -> TaskResult[LocalizedTemplate]:
    """Localize a template; placeholders must survive exactly.

    Args:
        runtime: AI runtime.
        subject: Template subject.
        body: Template body.
        target_language: ISO 639-1 target.
        region: ISO 3166-1 target region, if any.
        input_ref: Template ID for the AI run.

    Returns:
        The localized template and the AI run ID.

    Raises:
        LLMError: If the model fails, including twice breaking placeholders.
    """

    def validate(output: LocalizedTemplate) -> None:
        check_placeholders_preserved(subject, body, output)
        if output.language != target_language:
            raise ValueError(f"language must be '{target_language}'")

    content = json.dumps(
        {"subject": subject, "body": body, "target_language": target_language}, ensure_ascii=False
    )
    result, run_id = await run_prompted_task(
        runtime,
        task=TASK,
        values={
            "target_language": target_language,
            "region": region or "not specified",
            "template_content": wrap_untrusted(content, "template_content"),
        },
        output_model=LocalizedTemplate,
        input_ref=input_ref,
        max_tokens=4096,
        validate=validate,
    )
    return TaskResult(result.output, run_id)
