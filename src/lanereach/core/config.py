"""Application settings loaded from environment variables.

Every setting is documented in `.env.example`. Secrets are typed as `SecretStr` so they never
appear in `repr()` or logs. The `Settings` validator enforces environment-level safety rules
(for example, dev tokens are refused in production) so an unsafe configuration fails at startup
instead of at the first request.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_UNSUBSCRIBE_SECRET = "change-me-unsubscribe-secret"  # noqa: S105 - refused in staging/prod


class Environment(StrEnum):
    """Deployment environment the process runs in."""

    LOCAL = "local"
    TEST = "test"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class AuthMode(StrEnum):
    """How bearer JWTs are validated.

    `oidc` validates RS256 tokens from an external identity provider. `dev` issues and validates
    locally signed HS256 tokens and exists only so the API can be exercised without an IdP.
    """

    OIDC = "oidc"
    DEV = "dev"


class Settings(BaseSettings):
    """Typed process configuration.

    Attributes are read from environment variables of the same name (case-insensitive) and,
    for local convenience, from a `.env` file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Runtime ---------------------------------------------------------------------------
    environment: Environment = Environment.LOCAL
    service_name: str = "lanereach-api"
    log_level: str = "INFO"
    log_json: bool = True
    api_prefix: str = "/api/v1"
    cors_allowed_origins: list[str] = Field(default_factory=list)
    max_request_body_bytes: int = Field(default=1_048_576, gt=0)
    problem_type_base_uri: str = "urn:lanereach:problem:"

    # --- Database --------------------------------------------------------------------------
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://lanereach_app:lanereach_app@localhost:15432/lanereach"
    )
    database_owner_url: SecretStr = SecretStr(
        "postgresql+asyncpg://lanereach_owner:lanereach_owner@localhost:15432/lanereach"
    )
    database_pool_size: int = Field(default=10, gt=0)
    database_echo: bool = False

    # --- Redis / Celery --------------------------------------------------------------------
    redis_url: SecretStr = SecretStr("redis://localhost:16379/0")
    celery_broker_url: SecretStr = SecretStr("redis://localhost:16379/1")

    # --- Auth ------------------------------------------------------------------------------
    auth_mode: AuthMode = AuthMode.DEV
    dev_token_secret: SecretStr = SecretStr("change-me-dev-only-secret-at-least-32-bytes")
    dev_token_issuer: str = "lanereach-dev"  # noqa: S105 - issuer name, not a secret
    dev_token_max_lifetime_seconds: int = Field(default=86_400, gt=0)
    jwt_audience: str = "api://lanereach"
    jwt_leeway_seconds: int = Field(default=30, ge=0)
    oidc_tenant_claim: str = "tid"
    oidc_roles_claim: str = "roles"
    oidc_jwks_cache_ttl_seconds: int = Field(default=3_600, gt=0)
    oidc_jwks_min_refresh_interval_seconds: int = Field(default=60, ge=0)
    oidc_http_timeout_seconds: float = Field(default=5.0, gt=0)
    # Console sign-in (Authorization Code + PKCE), e.g. Microsoft Entra ID:
    # authority https://login.microsoftonline.com/organizations/v2.0, the SPA app's client id,
    # and the API scope the token is issued for (its audience is JWT_AUDIENCE).
    oidc_authority: str = ""
    oidc_client_id: str = ""
    oidc_scopes: str = "openid profile email api://lanereach/access_as_user"
    oidc_provider_name: str = "Microsoft"
    api_key_last_used_write_interval_seconds: int = Field(default=60, ge=0)

    # --- Webhooks --------------------------------------------------------------------------
    webhook_signing_secrets: dict[str, SecretStr] = Field(default_factory=dict)
    webhook_signature_tolerance_seconds: int = Field(default=300, gt=0)

    # --- Idempotency -----------------------------------------------------------------------
    idempotency_key_ttl_seconds: int = Field(default=86_400, gt=0)

    # --- Automation ----------------------------------------------------------------------
    max_cold_emails_per_sequence: int = Field(default=3, ge=1, le=10)
    max_steps_per_tick: int = Field(default=20, ge=1, le=200)
    engine_batch_size: int = Field(default=100, ge=1)
    engine_dispatch_interval_seconds: float = Field(default=30, gt=0)
    # "celery" hands work to the worker; "inline" runs it in-process right after commit
    # (tests, and single-process demos without a worker).
    task_queue: str = Field(default="celery", pattern="^(celery|inline)$")

    # --- Guardrails ------------------------------------------------------------------------
    frequency_cap_days: int = Field(default=30, ge=1)
    kill_switch_recheck_minutes: int = Field(default=15, ge=1)

    # --- Sending and notifications ---------------------------------------------------------
    # `dry_run` records email in the outbox only; `smtp` sends through each agent's mailbox.
    sending_provider: str = Field(default="dry_run", pattern="^(dry_run|smtp)$")
    outbox_delivery_interval_seconds: int = Field(default=15, gt=0)
    mailbox_poll_interval_seconds: int = Field(default=60, gt=0)
    mail_timeout_seconds: int = Field(default=20, gt=0, le=120)
    public_base_url: str = "http://127.0.0.1:8080"
    # --- Scripts library (SharePoint, or a local folder in development) ---------------------
    local_documents_path: str = "var/documents"
    sharepoint_client_secrets: dict[str, SecretStr] = Field(default_factory=dict)
    graph_base_url: str = "https://graph.microsoft.com/v1.0"
    graph_login_url: str = "https://login.microsoftonline.com"
    script_sync_interval_seconds: int = Field(default=300, gt=0)
    max_upload_bytes: int = Field(default=10_485_760, gt=0)
    unsubscribe_signing_secret: SecretStr = SecretStr(DEFAULT_UNSUBSCRIBE_SECRET)
    teams_webhook_urls: dict[str, SecretStr] = Field(default_factory=dict)
    # --- Integrations (webhooks, Slack, meetings, verification, forms) -------------------
    # Fernet key for integration secrets; generate with
    # `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
    integrations_encryption_key: SecretStr | None = None
    # Development only: allow http:// and private addresses for webhook receivers.
    integrations_allow_private_urls: bool = False
    integration_delivery_interval_seconds: int = Field(default=30, gt=0)
    console_base_url: str = "http://localhost:8081"
    form_submissions_per_email_per_hour: int = Field(default=5, ge=1)
    form_submissions_per_form_per_minute: int = Field(default=30, ge=1)
    # --- Public website (landing page) -------------------------------------------------------
    # Public key of the web form that receives "Contact us" (create it under Website forms in the
    # operator's own tenant, so enquiries land in its CRM); without it the page shows the email.
    public_contact_form_key: str | None = Field(default=None, max_length=64)
    public_contact_email: str | None = Field(default=None, max_length=320)
    # "Try it with your own email" on the website runs the real reply AI for anonymous visitors.
    # Off by default (every try is a paid LLM call); limits are per minute, per visitor and overall.
    public_ai_demo: bool = False
    public_ai_demo_per_ip_per_minute: int = Field(default=3, ge=1)
    public_ai_demo_per_minute: int = Field(default=20, ge=1)
    # Requests per minute per visitor to the online quote pages (public links).
    quote_online_per_ip_per_minute: int = Field(default=30, ge=1)
    # --- Sales intelligence ------------------------------------------------------------------
    # Days without activity after which an open deal counts as quiet and gets a follow-up idea.
    quiet_deal_days: int = Field(default=5, ge=1, le=90)
    # AI follow-up suggestions written per tenant per daily run (each is an LLM call).
    follow_up_suggestions_per_run: int = Field(default=20, ge=0, le=500)
    pricing_notification_recipient: str = "pricing-team"

    # --- LLM ------------------------------------------------------------------------------
    llm_provider: str = Field(default="mock", pattern="^(mock|anthropic|azure_openai)$")
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=60, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_effort: str = Field(default="low", pattern="^(low|medium|high|xhigh|max)$")
    llm_circuit_breaker_failures: int = Field(default=5, ge=1)
    llm_circuit_breaker_reset_seconds: int = Field(default=60, ge=1)
    llm_price_input_per_mtok: float = Field(default=0.0, ge=0)
    llm_price_output_per_mtok: float = Field(default=0.0, ge=0)
    # Optional cheaper model for the reading tasks (sorting replies, pulling out shipment facts,
    # lead reading, summaries, lane matching): see `lanereach.ai.llm.tiers.LIGHT_TASKS`. Empty =
    # every task uses LLM_MODEL. Its prices cost the runs it makes.
    llm_model_light: str = ""
    llm_light_price_input_per_mtok: float = Field(default=0.0, ge=0)
    llm_light_price_output_per_mtok: float = Field(default=0.0, ge=0)
    anthropic_api_key: SecretStr | None = None
    anthropic_refusal_fallback: bool = True
    azure_openai_endpoint: str = ""
    azure_openai_api_key: SecretStr | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_deployment: str = ""
    azure_openai_deployment_light: str = ""
    # --- Assistant search: `none` = PostgreSQL full-text only, `azure_openai` = vectors from the
    # embeddings deployment below, `mock` = word hashing for tests and key-less development.
    embeddings_provider: str = Field(default="none", pattern="^(none|mock|azure_openai)$")
    azure_openai_embedding_deployment: str = ""

    # --- AI behaviour ----------------------------------------------------------------------
    ai_review_confidence_threshold: float = Field(default=0.80, ge=0, le=1)
    ai_rate_limit_per_tenant_per_minute: int = Field(default=300, ge=0)
    data_retention_days: int = Field(default=365, ge=30)
    retention_purge_interval_seconds: int = Field(default=86_400, gt=0)
    ai_rate_limit_per_caller_per_minute: int = Field(default=60, ge=0)
    later_reenroll_days: int = Field(default=60, ge=1)
    not_interested_rest_days: int = Field(default=180, ge=1)
    out_of_office_default_days: int = Field(default=7, ge=1)
    soft_bounce_retry_hours: int = Field(default=24, ge=1)
    soft_bounce_limit: int = Field(default=3, ge=1)
    soft_bounce_window_days: int = Field(default=30, ge=1)
    eval_min_bucket_accuracy: float = Field(default=0.80, ge=0, le=1)
    eval_min_extraction_accuracy: float = Field(default=0.70, ge=0, le=1)

    @model_validator(mode="after")
    def _enforce_environment_safety(self) -> Self:
        """Refuse configurations that are unsafe for the selected environment.

        Raises:
            ValueError: If dev auth is enabled in production, the dev token secret is too
                short to be a meaningful HMAC key while dev auth is enabled, or staging or
                production still uses the default unsubscribe signing secret.
        """
        if self.environment is Environment.PRODUCTION and self.auth_mode is AuthMode.DEV:
            raise ValueError("AUTH_MODE=dev is not allowed when ENVIRONMENT=production")
        if self.auth_mode is AuthMode.DEV and len(self.dev_token_secret.get_secret_value()) < 32:
            raise ValueError("DEV_TOKEN_SECRET must be at least 32 characters")
        unsubscribe_secret = self.unsubscribe_signing_secret.get_secret_value()
        if self.environment in (Environment.STAGING, Environment.PRODUCTION) and (
            unsubscribe_secret == DEFAULT_UNSUBSCRIBE_SECRET or len(unsubscribe_secret) < 32
        ):
            # A known or short secret lets anyone forge unsubscribe links for any contact.
            raise ValueError("UNSUBSCRIBE_SIGNING_SECRET must be set (32+ characters) here")
        if self.environment in (Environment.STAGING, Environment.PRODUCTION):
            if self.integrations_encryption_key is None:
                # Without a key, integration secrets would be encrypted with a derived key.
                raise ValueError("INTEGRATIONS_ENCRYPTION_KEY must be set here")
            if self.integrations_allow_private_urls:
                raise ValueError("INTEGRATIONS_ALLOW_PRIVATE_URLS is for development only")
        return self

    @property
    def is_production(self) -> bool:
        """Whether the process runs in production."""
        return self.environment is Environment.PRODUCTION

    @property
    def exposes_test_data_endpoints(self) -> bool:
        """Whether dev/staging-only inspection endpoints (for example `outbox`) are mounted."""
        return self.environment is not Environment.PRODUCTION


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, loaded once from the environment.

    Returns:
        The cached `Settings` instance.
    """
    return Settings()
