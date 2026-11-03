"""FastAPI application factory.

Run locally with ``uvicorn lanereach.main:create_app --factory``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lanereach.ai.policies.router import router as ai_policies_router
from lanereach.ai.review.router import router as ai_review_router
from lanereach.ai.router import router as ai_router
from lanereach.ai.usage_router import router as ai_usage_router
from lanereach.assistant.router import router as assistant_router
from lanereach.auth.router import dev_router as auth_dev_router
from lanereach.auth.router import router as auth_router
from lanereach.automation.engine.runner import build_engine_runner
from lanereach.automation.enrollments.router import router as enrollments_router
from lanereach.automation.events.router import router as events_router
from lanereach.automation.nodes.router import router as automation_nodes_router
from lanereach.automation.workflows.router import router as workflows_router
from lanereach.core.config import AuthMode, Settings, get_settings
from lanereach.core.container import ServiceContainer, build_service_container
from lanereach.core.errors import ConfigurationError, register_exception_handlers
from lanereach.core.logging import configure_logging
from lanereach.core.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from lanereach.core.model_registry import load_all_models
from lanereach.domain.assets.router import router as public_assets_router
from lanereach.domain.audit.router import router as audit_router
from lanereach.domain.companies.router import router as companies_router
from lanereach.domain.contacts.router import router as contacts_router
from lanereach.domain.countries.router import router as countries_router
from lanereach.domain.crm.router import router as crm_router
from lanereach.domain.forms.router import router as forms_router
from lanereach.domain.inbox.router import router as inbox_router
from lanereach.domain.intelligence.router import router as intelligence_router
from lanereach.domain.leads.router import router as leads_router
from lanereach.domain.offices.router import router as offices_router
from lanereach.domain.pricing.router import router as pricing_router
from lanereach.domain.quotes.public_router import router as online_quotes_router
from lanereach.domain.quotes.router import router as quotes_router
from lanereach.domain.scripts.router import router as scripts_router
from lanereach.domain.senders.router import router as agents_router
from lanereach.domain.suppressions.router import router as suppressions_router
from lanereach.domain.templates.router import router as templates_router
from lanereach.health.router import router as health_router
from lanereach.integrations.documents.router import router as library_router
from lanereach.integrations.mailboxes.router import router as mailboxes_router
from lanereach.integrations.meetings.router import router as meetings_router
from lanereach.integrations.notifications.router import router as notifications_router
from lanereach.integrations.outbound.router import router as integrations_router
from lanereach.integrations.sending.router import router as sending_router
from lanereach.integrations.tracking.router import router as tracking_router
from lanereach.integrations.verification.router import router as verification_router
from lanereach.reporting.router import router as dashboard_router
from lanereach.tenants.router import router as settings_router
from lanereach.website.router import router as website_router

# Endpoints that accept files (Word/PDF scripts, banners, agent CSV) get the larger body limit.
UPLOAD_PATH_SUFFIXES = (
    "/scripts/upload",
    "/scripts/banners",
    "/scripts/brand-images",
    "/senders/import",
    "/contacts/import",
    "/signature-image",
)

OPENAPI_TAGS = [
    {"name": "Auth", "description": "Dev tokens, current principal and tenant API keys."},
    {
        "name": "Automation",
        "description": "Workflow node catalog, condition fields, workflows, versions, validation, "
        "publishing and test-runs.",
    },
    {
        "name": "AI",
        "description": "Reply classification, shipment extraction, drafting, localization "
        "and AI runs.",
    },
    {
        "name": "AI Review",
        "description": "AI output waiting for a person: approve (and send), edit or reject.",
    },
    {"name": "Webhooks", "description": "Signed events from sending providers."},
    {"name": "Test data", "description": "Dev/staging helpers such as simulated replies."},
    {"name": "Enrollments", "description": "Contacts going through workflows, step by step."},
    {"name": "Events", "description": "Events that start workflows (outbox pattern)."},
    {"name": "Companies & contacts", "description": "Prospects, their branches and contacts."},
    {"name": "Offices & senders", "description": "Offices and the mailboxes they send from."},
    {"name": "Templates", "description": "Email templates per key and language."},
    {
        "name": "Scripts",
        "description": "Word scripts from SharePoint: language matrix, uploads, sync, AI "
        "translations and previews.",
    },
    {
        "name": "Countries & languages",
        "description": "Which script language and destination phrase each country gets.",
    },
    {"name": "Public", "description": "Signed links loaded by recipients' mail clients."},
    {"name": "Suppressions & calendars", "description": "Do-not-email lists and working weeks."},
    {"name": "Dashboard", "description": "Sending, replies, leads, capacity and script coverage."},
    {"name": "Leads", "description": "Quote-ready leads for the pricing team."},
    {"name": "CRM", "description": "Pipelines, deals, tasks and notes."},
    {
        "name": "Inbox",
        "description": "Conversations of every agent mailbox: folders, read/done/snooze, "
        "assignees, labels and replying in the thread.",
    },
    {
        "name": "Integrations",
        "description": "Signed webhooks (Zapier, Make, n8n), Slack alerts, Calendly / Cal.com "
        "meetings and email verification.",
    },
    {"name": "Forms", "description": "Website quote forms: embed snippet and public submissions."},
    {"name": "Sending", "description": "Dry-run outbox and one-click unsubscribe."},
    {"name": "Notifications", "description": "Notifications to owners, pricing and Teams."},
    {"name": "Settings", "description": "Tenant kill switch and sending window."},
    {"name": "Health", "description": "Liveness and readiness probes."},
]

logger = structlog.get_logger(__name__)


def ensure_settings_allow_startup(settings: Settings) -> None:
    """Refuse to start with a configuration that is unsafe for its environment.

    `Settings` already validates this on load; the check is repeated here because settings can
    be built without validation (`model_construct`) and the rule must never be bypassed.

    Args:
        settings: The configuration the app is about to use.

    Raises:
        ConfigurationError: If dev auth is enabled in production.
    """
    if settings.is_production and settings.auth_mode is AuthMode.DEV:
        raise ConfigurationError("Refusing to start: AUTH_MODE=dev with ENVIRONMENT=production")


def create_app(
    settings: Settings | None = None, *, container: ServiceContainer | None = None
) -> FastAPI:
    """Build the LaneReach API application.

    Args:
        settings: Configuration; defaults to settings loaded from the environment.
        container: Pre-built service container (tests inject one with a `ManualClock`).
            When omitted, one is built from `settings` and closed on shutdown.

    Returns:
        The configured FastAPI application.
    """
    resolved_settings = container.settings if container else (settings or get_settings())
    ensure_settings_allow_startup(resolved_settings)
    # Every model must be registered before the first flush, or foreign keys to tables whose
    # module was never imported (for example `tenants`) cannot be resolved.
    load_all_models()
    configure_logging(level=resolved_settings.log_level, json_output=resolved_settings.log_json)
    resolved_container = container or build_service_container(resolved_settings)
    owns_container = container is None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "application_started",
            environment=resolved_settings.environment.value,
            auth_mode=resolved_settings.auth_mode.value,
        )
        yield
        if owns_container:
            await resolved_container.aclose()

    app = FastAPI(
        title="LaneReach API",
        version="0.1.0",
        description="AI + Automation backend for LaneReach. Authenticate with **Authorize** "
        "using an OIDC token, a dev token from `POST /api/v1/auth/dev-token` (dev mode), "
        "or a tenant API key.",
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    app.state.container = resolved_container
    app.state.engine_runner = build_engine_runner(resolved_container)

    register_exception_handlers(app)
    # Middleware added last runs first: request context wraps everything, including 413s.
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=resolved_settings.max_request_body_bytes,
        upload_max_bytes=resolved_settings.max_upload_bytes + 64 * 1024,  # multipart overhead
        upload_path_suffixes=UPLOAD_PATH_SUFFIXES,
    )
    if resolved_settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved_settings.cors_allowed_origins,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        )
    app.add_middleware(RequestContextMiddleware)

    prefix = resolved_settings.api_prefix
    app.include_router(health_router, prefix=prefix)
    app.include_router(website_router, prefix=prefix)
    app.include_router(auth_router, prefix=prefix)
    app.include_router(ai_router, prefix=prefix)
    app.include_router(ai_review_router, prefix=prefix)
    app.include_router(ai_policies_router, prefix=prefix)
    app.include_router(ai_usage_router, prefix=prefix)
    app.include_router(online_quotes_router, prefix=prefix)
    app.include_router(automation_nodes_router, prefix=prefix)
    app.include_router(workflows_router, prefix=prefix)
    app.include_router(enrollments_router, prefix=prefix)
    app.include_router(events_router, prefix=prefix)
    app.include_router(companies_router, prefix=prefix)
    app.include_router(contacts_router, prefix=prefix)
    app.include_router(agents_router, prefix=prefix)
    app.include_router(offices_router, prefix=prefix)
    app.include_router(templates_router, prefix=prefix)
    app.include_router(scripts_router, prefix=prefix)
    app.include_router(countries_router, prefix=prefix)
    app.include_router(public_assets_router, prefix=prefix)
    app.include_router(suppressions_router, prefix=prefix)
    app.include_router(leads_router, prefix=prefix)
    app.include_router(quotes_router, prefix=prefix)
    app.include_router(pricing_router, prefix=prefix)
    app.include_router(intelligence_router, prefix=prefix)
    app.include_router(crm_router, prefix=prefix)
    app.include_router(inbox_router, prefix=prefix)
    app.include_router(audit_router, prefix=prefix)
    app.include_router(tracking_router, prefix=prefix)
    app.include_router(integrations_router, prefix=prefix)
    app.include_router(meetings_router, prefix=prefix)
    app.include_router(verification_router, prefix=prefix)
    app.include_router(forms_router, prefix=prefix)
    app.include_router(dashboard_router, prefix=prefix)
    app.include_router(sending_router, prefix=prefix)
    app.include_router(mailboxes_router, prefix=prefix)
    app.include_router(assistant_router, prefix=prefix)
    app.include_router(notifications_router, prefix=prefix)
    app.include_router(settings_router, prefix=prefix)
    app.include_router(library_router, prefix=prefix)
    if resolved_settings.auth_mode is AuthMode.DEV:
        app.include_router(auth_dev_router, prefix=prefix)
    return app
