"""The data assistant: answers questions about the company's own LaneReach data with tools.

The model works in short steps: it picks one read-only tool (`agent_tools.py`), sees the result,
and either picks another or answers. Honesty is checked in code, not left to the prompt:

* at most three tool calls, then it must answer;
* every record it cites must be one a tool returned, and every number in the answer must appear
  in the tool results, the question or today's date (a count it did not get is refused, and the
  model gets one retry with the reason);
* tools only read; the assistant never sends, changes or deletes anything. Links in the answer
  open the records so a person can act.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

import structlog

from lanereach.ai.llm.client import AIBudgetReachedError, LLMError
from lanereach.ai.prompts.registry import wrap_untrusted
from lanereach.ai.tasks.runtime import AIRuntime, run_prompted_task
from lanereach.ai.tasks.write_digest import numbers_in
from lanereach.assistant.agent_tools import TOOL_SPECS, AgentTools, ToolRecord, ToolResult
from lanereach.assistant.schemas import AgentStep
from lanereach.assistant.service import ChatTurn, _history_text
from lanereach.core.clock import Clock

logger = structlog.get_logger(__name__)

TASK = "answer_data_question"
MAX_TOOL_CALLS = 3


@dataclass(frozen=True)
class AgentStepLog:
    """A tool the assistant used, shown with the answer ("Looked at: 5 deals")."""

    tool: str
    arguments: dict[str, object]
    summary: str


@dataclass
class DataAnswer:
    """The answer, the records it points to and how it was found."""

    answer: str
    records: list[ToolRecord]
    steps: list[AgentStepLog]
    mode: str  # "ai", "extractive" (no AI key), "partial" (AI stopped) or "budget"
    ai_run_ids: list[uuid.UUID] = field(default_factory=list)


def _call_key(output: AgentStep) -> str:
    """The same tool with the same arguments is one call (asking twice is refused)."""
    arguments = output.arguments.model_dump_json(exclude_none=True) if output.arguments else ""
    return f"{output.tool}:{arguments}"


def _tools_text(names: list[str]) -> str:
    if not names:
        return "(none: answer now with what you have)"
    return "\n".join(
        f"- {name}: {TOOL_SPECS[name].description} Arguments: {TOOL_SPECS[name].arguments}."
        for name in names
    )


class DataAssistant:
    """Answers questions about the company's data.

    Args:
        tools: The read-only tools.
        runtime: AI runtime (counts against the AI budget).
        clock: Time source.
        allowed_tools: Tools this person may use.
    """

    def __init__(
        self, tools: AgentTools, runtime: AIRuntime, clock: Clock, allowed_tools: list[str]
    ) -> None:
        self._tools = tools
        self._runtime = runtime
        self._clock = clock
        self._allowed = [name for name in TOOL_SPECS if name in allowed_tools]

    async def ask(self, question: str, history: list[ChatTurn]) -> DataAnswer:
        """Answer one question.

        Args:
            question: The person's question.
            history: Earlier turns of the conversation (the last six are used).

        Returns:
            The answer with the records it cites.
        """
        today = self._clock.now().date().isoformat()
        results: list[ToolResult] = []
        run_ids: list[uuid.UUID] = []
        calls: set[str] = set()
        for step in range(MAX_TOOL_CALLS + 1):
            offered = [] if step == MAX_TOOL_CALLS else self._allowed
            seen = {record.ref: record for result in results for record in result.records}
            evidence = json.dumps(
                [result.for_prompt() for result in results], ensure_ascii=False, default=str
            )
            allowed_numbers = numbers_in(f"{question} {evidence} {today}")

            def check(
                output: AgentStep,
                offered: list[str] = offered,
                seen: dict[str, ToolRecord] = seen,
                allowed_numbers: set[str] = allowed_numbers,
            ) -> None:
                if output.action == "use_tool":
                    if not offered:
                        raise ValueError("no more tools: answer now from <tool_results>")
                    if output.tool not in offered:
                        raise ValueError(f"tool must be one of {offered}")
                    if _call_key(output) in calls:
                        raise ValueError("you already have this result: use it or answer")
                    return
                if not (output.answer or "").strip():
                    raise ValueError("write the answer")
                unknown = [ref for ref in output.record_refs if ref not in seen]
                if unknown:
                    raise ValueError(f"record_refs must come from <tool_results>, not {unknown}")
                invented = numbers_in(output.answer or "") - allowed_numbers
                if invented:
                    raise ValueError(
                        "use only numbers that are in <tool_results> or the question; not found: "
                        + ", ".join(sorted(invented))
                    )

            try:
                result, run_id = await run_prompted_task(
                    self._runtime,
                    task=TASK,
                    values={
                        "today": today,
                        "tools": _tools_text(offered),
                        "results": wrap_untrusted(evidence, "tool_results"),
                        "history": wrap_untrusted(_history_text(history), "conversation"),
                        "question": wrap_untrusted(question.strip()[:2000], "question"),
                    },
                    output_model=AgentStep,
                    input_ref=None,
                    max_tokens=900,
                    validate=check,
                )
            except LLMError as exc:
                return self._stopped(exc, results, run_ids)
            run_ids.append(run_id)
            output = result.output
            if output.action == "answer":
                cited = [seen[ref] for ref in dict.fromkeys(output.record_refs)]
                mode = "extractive" if self._runtime.llm.provider_name == "mock" else "ai"
                return DataAnswer(
                    (output.answer or "").strip(), cited, self._steps(results), mode, run_ids
                )
            assert output.tool is not None  # noqa: S101 - checked above
            calls.add(_call_key(output))
            results.append(await self._tools.run(output.tool, output.arguments_or_empty()))
        # The last step offers no tool, so the loop always returns above.
        raise AssertionError("unreachable")  # pragma: no cover

    def _stopped(
        self, exc: LLMError, results: list[ToolResult], run_ids: list[uuid.UUID]
    ) -> DataAnswer:
        """What was found before the AI stopped (no unverified text)."""
        logger.warning("data_assistant_stopped", error=type(exc).__name__)
        records = [record for result in results for record in result.records][:8]
        if isinstance(exc, AIBudgetReachedError):
            text = "This month's AI budget is used up, so I cannot answer questions right now."
            mode = "budget"
        else:
            text = "I could not finish this answer right now."
            mode = "partial"
        if records:
            text += " These are the records I found so far:"
        return DataAnswer(text, records, self._steps(results), mode, run_ids)

    @staticmethod
    def _steps(results: list[ToolResult]) -> list[AgentStepLog]:
        return [AgentStepLog(result.tool, result.arguments, result.summary()) for result in results]
