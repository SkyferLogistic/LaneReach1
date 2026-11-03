"""Shared plumbing of AI tasks: prompt rendering, the LLM call and run recording."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from lanereach.ai.llm.client import LLMClient, LLMError, LLMMessage, StructuredResult
from lanereach.ai.prompts.registry import RenderedPrompt, get_prompt
from lanereach.ai.runs.models import AIRunStatus
from lanereach.ai.runs.service import RunRecorder, hash_prompt_input
from lanereach.core.config import Settings


class BudgetCheck(Protocol):
    """Stops AI calls when the tenant's monthly AI budget is spent (`AIBudgetGuard`)."""

    async def ensure_available(self) -> None:
        """Raise `AIBudgetReachedError` when the budget is used up."""
        ...


@dataclass
class AIRuntime:
    """Everything an AI task needs.

    Attributes:
        llm: Structured LLM client.
        recorder: Writes AI runs.
        settings: Thresholds and token limits.
        budget: The tenant's monthly AI budget, checked before every model call (None for
            calls that are not billed to a tenant, such as the website demo).
    """

    llm: LLMClient
    recorder: RunRecorder
    settings: Settings
    budget: BudgetCheck | None = None


@dataclass(frozen=True)
class TaskResult[OutputT: BaseModel]:
    """A task's validated output and the AI run that produced it."""

    output: OutputT
    ai_run_id: uuid.UUID


async def run_prompted_task[OutputT: BaseModel](
    runtime: AIRuntime,
    *,
    task: str,
    values: dict[str, str],
    output_model: type[OutputT],
    input_ref: str | None,
    max_tokens: int,
    validate: Callable[[OutputT], None] | None = None,
    prompt_version: str = "latest",
) -> tuple[StructuredResult[OutputT], uuid.UUID]:
    """Render the task's prompt, call the model and record the run.

    Args:
        runtime: AI runtime.
        task: Task name (prompt file prefix).
        values: Prompt placeholder values (untrusted ones already wrapped).
        output_model: Output schema.
        input_ref: Message or template ID the run is about.
        max_tokens: Output cap.
        validate: Extra output validation (raise `ValueError` to reject).
        prompt_version: Prompt version to use.

    Returns:
        The structured result and the AI run ID.

    Raises:
        AIBudgetReachedError: When the tenant's monthly AI budget is used up (no call is made).
        LLMError: When the call fails (the failure is recorded first).
    """
    if runtime.budget is not None:
        await runtime.budget.ensure_available()
    prompt: RenderedPrompt = get_prompt(task, prompt_version).render(**values)
    input_hash = hash_prompt_input(prompt.system, prompt.user)
    llm = runtime.llm
    try:
        result = await llm.generate_structured(
            task,
            prompt.version,
            prompt.system,
            [LLMMessage("user", prompt.user)],
            output_model,
            max_tokens=max_tokens,
            validate=validate,
        )
    except LLMError as exc:
        await runtime.recorder.record_run(
            task=task,
            prompt_version=prompt.version,
            provider=llm.provider_name,
            model=llm.model,
            input_ref=input_ref,
            input_hash=input_hash,
            status=AIRunStatus.FAILED,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    run_id = await runtime.recorder.record_run(
        task=task,
        prompt_version=prompt.version,
        provider=result.provider,
        model=result.model,
        input_ref=input_ref,
        input_hash=input_hash,
        status=AIRunStatus.SUCCEEDED,
        output=result.output.model_dump(mode="json"),
        confidence=getattr(result.output, "confidence", None),
        latency_ms=result.latency_ms,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        attempts=result.attempts,
    )
    return result, run_id
