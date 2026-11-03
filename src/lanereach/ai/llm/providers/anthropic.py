"""Anthropic (Claude) provider using the official `anthropic` SDK.

Structured output uses `output_config.format` (JSON schema); the response is still validated
by `StructuredLLMClient`. Sampling parameters are not sent: current Claude models reject them,
and `output_config.effort` is the cost/quality lever instead. With `refusal_fallback` enabled the
server-side fallback (beta) re-runs a refused request on a fallback model inside the same call;
a final `refusal` stop reason is surfaced as `ProviderRefusal` so the task routes to a human.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import anthropic

from lanereach.ai.llm.client import (
    LLMMessage,
    ProviderPermanentError,
    ProviderRefusal,
    ProviderResponse,
    ProviderTransientError,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicLLMProvider:
    """Calls the Claude Messages API.

    Args:
        api_key: Anthropic API key (None to let the SDK resolve credentials from the environment).
        model: Model ID from settings (for example `claude-opus-5`).
        timeout_seconds: Per-request timeout.
        effort: `output_config.effort` (`low` suits classification/extraction).
        refusal_fallback: Enable the server-side refusal fallback.
        client: Pre-built async client (tests inject a fake).
    """

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        timeout_seconds: float,
        effort: str,
        refusal_fallback: bool,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self._effort = effort
        self._refusal_fallback = refusal_fallback
        # Retries are owned by StructuredLLMClient (backoff + circuit breaker), not the SDK.
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout_seconds, max_retries=0
        )

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
        """Request JSON matching `json_schema`."""
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [
                {"role": message.role, "content": message.content} for message in messages
            ],
            "output_config": {
                "effort": self._effort,
                "format": {"type": "json_schema", "schema": json_schema},
            },
        }
        try:
            if self._refusal_fallback:
                response = await self._client.beta.messages.create(
                    **request, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = await self._client.messages.create(**request)
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
            raise ProviderTransientError(type(exc).__name__) from exc
        except anthropic.RateLimitError as exc:
            raise ProviderTransientError("rate limited") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise ProviderTransientError(f"HTTP {exc.status_code}") from exc
            raise ProviderPermanentError(f"HTTP {exc.status_code}: {exc.message}") from exc

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise ProviderRefusal(f"declined ({category or 'unspecified'})")
        text = "".join(block.text for block in response.content if block.type == "text")
        usage = response.usage
        return ProviderResponse(
            text=text,
            model=str(response.model),
            tokens_in=int(usage.input_tokens or 0),
            tokens_out=int(usage.output_tokens or 0),
            stop_reason=response.stop_reason,
        )
