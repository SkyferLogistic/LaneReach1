"""Model tiers: a cheaper model for the tasks that read, the main model for the ones that write.

Reading a reply (its kind, the shipment facts, buying intent, a summary, the matching rate lane)
is a short, structured job that small models do well; writing in the office's voice (reply
drafts, follow-ups, the digest, workflows, help answers) keeps the main model. With a light model
configured, the reading tasks make up most AI calls and most of the saving.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from lanereach.ai.llm.client import LLMMessage, LLMProvider, ProviderResponse
from lanereach.core.config import Settings

LIGHT_TASKS = frozenset(
    {
        "classify_reply",
        "extract_shipment_details",
        "assess_lead",
        "summarize_thread",
        "match_lane",
    }
)


def light_model_configured(settings: Settings) -> bool:
    """Whether a light model is set for the configured provider."""
    if settings.llm_provider == "anthropic":
        return bool(settings.llm_model_light)
    if settings.llm_provider == "azure_openai":
        return bool(settings.azure_openai_deployment_light)
    return False


def uses_light_model(settings: Settings, task: str) -> bool:
    """Whether `task` runs on the light model (and is priced at its rates)."""
    return task in LIGHT_TASKS and light_model_configured(settings)


class TieredLLMProvider:
    """Sends the light tasks to one provider and everything else to another.

    Args:
        main: Provider for the writing tasks (and any task not listed as light).
        light: Provider for `LIGHT_TASKS`.
    """

    def __init__(self, main: LLMProvider, light: LLMProvider) -> None:
        self._main = main
        self._light = light
        self.name = main.name
        self.model = main.model

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
        """Route the call by task."""
        provider = self._light if task_name in LIGHT_TASKS else self._main
        return await provider.complete_json(
            task_name=task_name,
            system=system,
            messages=messages,
            json_schema=json_schema,
            max_tokens=max_tokens,
            temperature=temperature,
        )
