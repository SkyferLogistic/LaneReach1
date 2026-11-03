"""Builds the configured LLM client."""

from __future__ import annotations

from datetime import timedelta

from lanereach.ai.llm.client import CircuitBreaker, LLMProvider, StructuredLLMClient
from lanereach.ai.llm.providers.anthropic import AnthropicLLMProvider
from lanereach.ai.llm.providers.azure_openai import AzureOpenAILLMProvider
from lanereach.ai.llm.providers.mock import MockLLMProvider
from lanereach.ai.llm.tiers import TieredLLMProvider, light_model_configured
from lanereach.core.clock import Clock
from lanereach.core.config import Settings
from lanereach.core.errors import ConfigurationError


def build_llm_provider(settings: Settings) -> LLMProvider:
    """Create the provider named by `LLM_PROVIDER` (tiered when a light model is set).

    Args:
        settings: Process configuration.

    Returns:
        The provider.

    Raises:
        ConfigurationError: If a real provider lacks its model or credentials.
    """
    main = _build_single_provider(settings, light=False)
    if light_model_configured(settings):
        return TieredLLMProvider(main, _build_single_provider(settings, light=True))
    return main


def _build_single_provider(settings: Settings, *, light: bool) -> LLMProvider:
    if settings.llm_provider == "anthropic":
        if not settings.llm_model:
            raise ConfigurationError("LLM_MODEL is required for the anthropic provider")
        return AnthropicLLMProvider(
            api_key=settings.anthropic_api_key.get_secret_value()
            if settings.anthropic_api_key
            else None,
            model=settings.llm_model_light if light else settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            effort=settings.llm_effort,
            refusal_fallback=settings.anthropic_refusal_fallback,
        )
    if settings.llm_provider == "azure_openai":
        if not (
            settings.azure_openai_endpoint
            and settings.azure_openai_api_key
            and settings.azure_openai_deployment
        ):
            raise ConfigurationError(
                "AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY and AZURE_OPENAI_DEPLOYMENT "
                "are required"
            )
        return AzureOpenAILLMProvider(
            endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key.get_secret_value(),
            api_version=settings.azure_openai_api_version,
            deployment=settings.azure_openai_deployment_light
            if light
            else settings.azure_openai_deployment,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    return MockLLMProvider()


def build_llm_client(
    settings: Settings, clock: Clock, provider: LLMProvider | None = None
) -> StructuredLLMClient:
    """Create the structured client over the configured (or given) provider.

    Args:
        settings: Process configuration.
        clock: Time source for the circuit breaker.
        provider: Override the provider (tests).

    Returns:
        The client.
    """
    return StructuredLLMClient(
        provider or build_llm_provider(settings),
        clock,
        max_transient_retries=settings.llm_max_retries,
        breaker=CircuitBreaker(
            clock,
            failure_threshold=settings.llm_circuit_breaker_failures,
            reset_after=timedelta(seconds=settings.llm_circuit_breaker_reset_seconds),
        ),
    )
