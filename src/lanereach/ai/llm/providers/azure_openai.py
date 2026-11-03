"""Azure OpenAI provider (chat completions with a strict JSON-schema response format)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import openai

from lanereach.ai.llm.client import (
    LLMMessage,
    ProviderPermanentError,
    ProviderRefusal,
    ProviderResponse,
    ProviderTransientError,
)


class AzureOpenAILLMProvider:
    """Calls an Azure OpenAI deployment.

    Args:
        endpoint: Resource endpoint, for example `https://<name>.openai.azure.com`.
        api_key: Resource key.
        api_version: API version.
        deployment: Deployment name (recorded as the model).
        timeout_seconds: Per-request timeout.
        client: Pre-built async client (tests inject a fake).
    """

    name = "azure_openai"

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        api_version: str,
        deployment: str,
        timeout_seconds: float,
        client: Any | None = None,
    ) -> None:
        self.model = deployment
        self._client = client or openai.AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            timeout=timeout_seconds,
            max_retries=0,
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
            "messages": [
                {"role": "system", "content": system},
                *({"role": message.role, "content": message.content} for message in messages),
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": task_name, "schema": json_schema, "strict": True},
            },
            "max_completion_tokens": max_tokens,
        }
        try:
            response = await self._client.chat.completions.create(**request)
        except (openai.APITimeoutError, openai.APIConnectionError) as exc:
            raise ProviderTransientError(type(exc).__name__) from exc
        except openai.RateLimitError as exc:
            raise ProviderTransientError("rate limited") from exc
        except openai.APIStatusError as exc:
            if exc.status_code >= 500:
                raise ProviderTransientError(f"HTTP {exc.status_code}") from exc
            raise ProviderPermanentError(f"HTTP {exc.status_code}") from exc

        choice = response.choices[0]
        if choice.finish_reason == "content_filter" or getattr(choice.message, "refusal", None):
            raise ProviderRefusal("declined (content filter)")
        usage = response.usage
        return ProviderResponse(
            text=choice.message.content or "",
            model=self.model,
            tokens_in=int(getattr(usage, "prompt_tokens", 0) or 0),
            tokens_out=int(getattr(usage, "completion_tokens", 0) or 0),
            stop_reason=choice.finish_reason,
        )
