"""Mock provider: fixtures, callables and heuristics. Used by every test and by dev without keys.

Resolution order for a call:

1. a fixture registered for `(task_name, input_hash)`;
2. a responder callable registered for the task (tests use this to simulate any model output,
   including malicious or invalid JSON);
3. the built-in heuristic for the task.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from lanereach.ai.llm.client import LLMMessage, ProviderResponse
from lanereach.ai.llm.providers.agent_heuristics import answer_data_question_heuristically
from lanereach.ai.llm.providers.assistant_heuristics import (
    answer_heuristically,
    draft_workflow_heuristically,
)
from lanereach.ai.llm.providers.heuristics import (
    classify_heuristically,
    draft_heuristically,
    extract_context,
    extract_email_text,
    extract_shipment_heuristically,
    localize_heuristically,
)
from lanereach.ai.llm.providers.insight_heuristics import (
    assess_lead_heuristically,
    draft_quote_note_heuristically,
    suggest_follow_up_heuristically,
    summarize_thread_heuristically,
    write_digest_heuristically,
)
from lanereach.ai.llm.providers.pricing_heuristics import match_lane_heuristically

type Responder = Callable[[str, Sequence[LLMMessage]], dict[str, Any] | str]


def input_hash(messages: Sequence[LLMMessage]) -> str:
    """Hash of the first user message (fixture key)."""
    first_user = next((message.content for message in messages if message.role == "user"), "")
    return hashlib.sha256(first_user.encode()).hexdigest()


_HEURISTICS: dict[str, Callable[[str], dict[str, Any]]] = {
    "classify_reply": lambda text: classify_heuristically(extract_email_text(text)),
    "extract_shipment_details": lambda text: extract_shipment_heuristically(
        extract_email_text(text)
    ),
    "draft_reply": lambda text: draft_heuristically(extract_context(text)),
    "localize_template": lambda text: localize_heuristically(extract_context(text)),
    "answer_help_question": answer_heuristically,
    "draft_workflow": draft_workflow_heuristically,
    "match_lane": match_lane_heuristically,
    "assess_lead": assess_lead_heuristically,
    "write_digest": write_digest_heuristically,
    "suggest_follow_up": suggest_follow_up_heuristically,
    "summarize_thread": summarize_thread_heuristically,
    "draft_quote_note": draft_quote_note_heuristically,
    "answer_data_question": answer_data_question_heuristically,
}


@dataclass
class MockLLMProvider:
    """Deterministic provider for tests and key-less development."""

    name: str = "mock"
    model: str = "mock-1"
    fixtures: dict[tuple[str, str], dict[str, Any] | str] = field(default_factory=dict)
    responders: dict[str, Responder] = field(default_factory=dict)
    calls: list[tuple[str, list[LLMMessage]]] = field(default_factory=list)

    def add_fixture(
        self, task_name: str, messages: Sequence[LLMMessage], output: dict[str, Any] | str
    ) -> None:
        """Register the output for an exact input."""
        self.fixtures[(task_name, input_hash(messages))] = output

    async def complete_json(
        self,
        *,
        task_name: str,
        system: str,
        messages: Sequence[LLMMessage],
        json_schema: dict[str, Any],
        max_tokens: int,
        temperature: float,
    ) -> ProviderResponse:
        """Return the fixture, responder or heuristic output as JSON text."""
        self.calls.append((task_name, list(messages)))
        key = (task_name, input_hash(messages))
        if key in self.fixtures:
            output: dict[str, Any] | str = self.fixtures[key]
        elif task_name in self.responders:
            output = self.responders[task_name](system, messages)
        elif task_name in _HEURISTICS:
            output = _HEURISTICS[task_name]("\n".join(message.content for message in messages))
        else:
            output = {}
        text = (
            output
            if isinstance(output, str)
            else json.dumps(output, ensure_ascii=False, default=str)
        )
        prompt_size = len(system) + sum(len(message.content) for message in messages)
        return ProviderResponse(
            text=text, model=self.model, tokens_in=prompt_size // 4, tokens_out=len(text) // 4
        )
