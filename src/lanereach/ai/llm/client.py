"""Provider-agnostic structured LLM calls.

`LLMClient.generate_structured` is the only way AI tasks talk to a model:

1. the provider is asked for JSON matching the output model's schema (native structured output
   where the provider supports it);
2. the response is **always** validated against the Pydantic model (plus an optional task
   validator, for example "placeholders preserved"). On failure the request is retried once with
   the validation error appended; a second failure raises `LLMOutputInvalidError`;
3. transient provider errors (timeouts, 429, 5xx) are retried with exponential backoff;
4. a circuit breaker per provider fails fast after repeated failures, so a provider outage turns
   into quick "send to human review" decisions instead of piling up slow requests.

Model name, provider and timeouts come from settings; nothing here is provider-specific.
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

import structlog
from pydantic import BaseModel, ValidationError

from lanereach.core.clock import Clock
from lanereach.core.errors import LaneReachError

logger = structlog.get_logger(__name__)

Role = Literal["user", "assistant"]


@dataclass(frozen=True)
class LLMMessage:
    """One conversation turn sent to the model."""

    role: Role
    content: str


@dataclass(frozen=True)
class ProviderResponse:
    """Raw provider output before validation."""

    text: str
    model: str
    tokens_in: int
    tokens_out: int
    stop_reason: str | None = None


@dataclass(frozen=True)
class StructuredResult[OutputT: BaseModel]:
    """A validated model response with usage metadata (recorded on the AI run)."""

    output: OutputT
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    attempts: int


class LLMError(LaneReachError):
    """Base class of LLM failures."""


class LLMUnavailableError(LLMError):
    """The provider is failing, unconfigured or the circuit is open. Callers route to humans."""

    status_code = 503
    problem_type = "llm-unavailable"
    title = "AI provider unavailable"


class AIBudgetReachedError(LLMError):
    """The tenant's monthly AI budget is spent: no model call is made until it is raised.

    The engine treats it like any AI failure (built-in messages and rules take over, people
    review); a person asking for an AI feature gets a 429 explaining why.
    """

    status_code = 429
    problem_type = "ai-budget-reached"
    title = "Monthly AI budget used up"


class LLMOutputInvalidError(LLMError):
    """The model returned output that failed validation twice."""

    status_code = 502
    problem_type = "llm-output-invalid"
    title = "AI returned invalid output"


class LLMRefusalError(LLMError):
    """The model declined the request (safety refusal)."""

    status_code = 502
    problem_type = "llm-refused"
    title = "AI declined the request"


class ProviderTransientError(Exception):
    """A retryable provider failure (timeout, rate limit, 5xx, connection)."""


class ProviderPermanentError(Exception):
    """A non-retryable provider failure (bad request, auth, unknown model)."""


class ProviderRefusal(Exception):  # noqa: N818 - a refusal is an outcome, not a bug
    """The provider reported a refusal stop reason."""


class LLMProvider(Protocol):
    """Adapter to one model API."""

    name: str
    model: str

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
        """Return the model's JSON text for the schema.

        Raises:
            ProviderTransientError: For retryable failures.
            ProviderPermanentError: For non-retryable failures.
            ProviderRefusal: When the model declines.
        """
        ...


class LLMClient(Protocol):
    """What AI tasks depend on."""

    @property
    def provider_name(self) -> str:
        """Provider name recorded on AI runs."""
        ...

    @property
    def model(self) -> str:
        """Model name recorded on AI runs."""
        ...

    async def generate_structured[OutputT: BaseModel](
        self,
        task_name: str,
        prompt_version: str,
        system: str,
        messages: Sequence[LLMMessage],
        output_model: type[OutputT],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        validate: Callable[[OutputT], None] | None = None,
    ) -> StructuredResult[OutputT]:
        """Call the model and return a validated instance of `output_model`."""
        ...


_UNSUPPORTED_SCHEMA_KEYS = {
    "default",
    "title",
    "examples",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "multipleOf",
}


def to_strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Convert a Pydantic model into the strict JSON Schema dialect providers accept.

    Every object forbids extra keys and lists all its properties as required (optional fields
    stay nullable), and numeric/length constraints some providers reject are removed. Those
    constraints are still enforced: the Pydantic model validates every response.

    Args:
        model: The output model.

    Returns:
        A JSON Schema dict.
    """
    schema = copy.deepcopy(model.model_json_schema())

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            cleaned = {
                key: clean(value)
                for key, value in node.items()
                if key not in _UNSUPPORTED_SCHEMA_KEYS
            }
            if cleaned.get("type") == "object" and "properties" in cleaned:
                cleaned["additionalProperties"] = False
                cleaned["required"] = list(cleaned["properties"])
            return cleaned
        if isinstance(node, list):
            return [clean(item) for item in node]
        return node

    result: dict[str, Any] = clean(schema)
    return result


@dataclass
class CircuitBreaker:
    """Opens after `failure_threshold` consecutive failures; half-opens after `reset_after`."""

    clock: Clock
    failure_threshold: int = 5
    reset_after: timedelta = timedelta(seconds=60)
    _failures: int = 0
    _opened_at: datetime | None = field(default=None)

    def allow_request(self) -> bool:
        """Whether a request may be attempted now."""
        if self._opened_at is None:
            return True
        # Half-open: let one request through after the cool-down.
        return self.clock.now() - self._opened_at >= self.reset_after

    def record_success(self) -> None:
        """Close the circuit."""
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        """Count a failure; open (or re-open) the circuit at the threshold."""
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = self.clock.now()


type Sleeper = Callable[[float], Awaitable[None]]


class StructuredLLMClient:
    """`LLMClient` implementation over any `LLMProvider`.

    Args:
        provider: The model adapter.
        clock: Time source for the circuit breaker.
        max_transient_retries: Retries of timeouts/429/5xx per attempt.
        backoff_base_seconds: First backoff delay (doubles each retry).
        breaker: Circuit breaker (defaults to 5 failures / 60 s).
        sleep: Async sleep (tests pass a no-op).
    """

    def __init__(
        self,
        provider: LLMProvider,
        clock: Clock,
        *,
        max_transient_retries: int = 2,
        backoff_base_seconds: float = 0.5,
        breaker: CircuitBreaker | None = None,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._provider = provider
        self._max_transient_retries = max_transient_retries
        self._backoff_base = backoff_base_seconds
        self._breaker = breaker or CircuitBreaker(clock)
        self._sleep = sleep

    @property
    def provider_name(self) -> str:
        """Provider name recorded on AI runs."""
        return self._provider.name

    @property
    def model(self) -> str:
        """Model name recorded on AI runs."""
        return self._provider.model

    async def generate_structured[OutputT: BaseModel](
        self,
        task_name: str,
        prompt_version: str,
        system: str,
        messages: Sequence[LLMMessage],
        output_model: type[OutputT],
        *,
        max_tokens: int,
        temperature: float = 0.0,
        validate: Callable[[OutputT], None] | None = None,
    ) -> StructuredResult[OutputT]:
        """Call the model and return a validated `output_model` instance.

        Args:
            task_name: Task identifier (logging, mock fixtures).
            prompt_version: Prompt version (logging).
            system: System prompt.
            messages: Conversation, untrusted content already wrapped in delimiters.
            output_model: Pydantic model the response must satisfy.
            max_tokens: Output token cap.
            temperature: Sampling temperature (ignored by providers that do not support it).
            validate: Extra task-level check; raise `ValueError` to reject the output.

        Returns:
            The validated output and usage.

        Raises:
            LLMUnavailableError: If the provider is unavailable or the circuit is open.
            LLMOutputInvalidError: If the output fails validation twice.
            LLMRefusalError: If the model declines.
        """
        schema = to_strict_json_schema(output_model)
        conversation = list(messages)
        started = time.perf_counter()
        tokens_in = tokens_out = 0
        attempts = 0
        last_error = ""
        for validation_attempt in range(2):
            response = await self._call_with_retries(
                task_name, system, conversation, schema, max_tokens, temperature
            )
            attempts += 1
            tokens_in += response.tokens_in
            tokens_out += response.tokens_out
            try:
                output = output_model.model_validate(json.loads(_strip_code_fence(response.text)))
                if validate is not None:
                    validate(output)
            except (ValueError, ValidationError) as exc:
                last_error = _describe_validation_error(exc)
                logger.warning(
                    "llm_output_invalid",
                    task=task_name,
                    prompt_version=prompt_version,
                    attempt=validation_attempt + 1,
                )
                conversation = [
                    *conversation,
                    LLMMessage("assistant", response.text[:4000]),
                    LLMMessage(
                        "user",
                        "Your previous answer was rejected by validation: "
                        f"{last_error}\nReturn only corrected JSON that matches the schema.",
                    ),
                ]
                continue
            return StructuredResult(
                output=output,
                provider=self._provider.name,
                model=response.model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=int((time.perf_counter() - started) * 1000),
                attempts=attempts,
            )
        raise LLMOutputInvalidError(f"{task_name}: output invalid after retry ({last_error[:300]})")

    async def _call_with_retries(
        self,
        task_name: str,
        system: str,
        messages: Sequence[LLMMessage],
        schema: dict[str, Any],
        max_tokens: int,
        temperature: float,
    ) -> ProviderResponse:
        for retry in range(self._max_transient_retries + 1):
            if not self._breaker.allow_request():
                raise LLMUnavailableError(f"{self._provider.name} circuit is open")
            try:
                response = await self._provider.complete_json(
                    task_name=task_name,
                    system=system,
                    messages=messages,
                    json_schema=schema,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            except ProviderRefusal as exc:
                self._breaker.record_success()  # the provider works; it declined this input
                raise LLMRefusalError(str(exc) or "The model declined the request.") from exc
            except ProviderPermanentError as exc:
                self._breaker.record_failure()
                raise LLMUnavailableError(f"{self._provider.name}: {exc}") from exc
            except ProviderTransientError as exc:
                self._breaker.record_failure()
                if retry == self._max_transient_retries:
                    raise LLMUnavailableError(f"{self._provider.name}: {exc}") from exc
                await self._sleep(self._backoff_base * (2**retry))
                continue
            self._breaker.record_success()
            return response
        raise AssertionError("unreachable")  # pragma: no cover


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def _describe_validation_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'root'}: {error['msg']}"
            for error in exc.errors()[:10]
        )
    return str(exc)
