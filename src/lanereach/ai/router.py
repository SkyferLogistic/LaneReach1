"""AI endpoints: run AI tasks directly (testing, integrations) and inspect AI runs."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, EmailStr, Field

from lanereach.ai.controls import AIBudgetGuard
from lanereach.ai.pipeline import process_inbound_reply
from lanereach.ai.rules.reply_rules import ReplyMessage
from lanereach.ai.runs.models import AIRunStatus
from lanereach.ai.runs.service import AIRunRecorder, AIRunRepository
from lanereach.ai.schemas import (
    LocalizedTemplate,
    ReplyBucket,
    ReplyClassification,
    ReplyDraft,
    ShipmentDetails,
)
from lanereach.ai.tasks.classify_reply import ReplyContext, classify_reply
from lanereach.ai.tasks.draft_reply import DraftContext, draft_reply
from lanereach.ai.tasks.extract_shipment_details import extract_shipment_details
from lanereach.ai.tasks.localize_template import localize_template, placeholder_tokens
from lanereach.ai.tasks.runtime import AIRuntime
from lanereach.auth.dependencies import (
    CurrentPrincipal,
    IdempotencyGuardDependency,
    TenantSession,
    require_scopes,
)
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.automation.engine.dependencies import EngineServicesDependency
from lanereach.automation.engine.email import choose_sender, find_template
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import BusinessRuleViolationError, NotFoundError
from lanereach.core.idempotency import run_idempotent
from lanereach.core.languages import LANGUAGE_VARIANT_PATTERN, split_language_variant
from lanereach.core.openapi import problem_responses
from lanereach.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from lanereach.core.rate_limit import RedisRateLimiter, enforce_rate_limits
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.domain.contacts.models import Contact
from lanereach.domain.offices.models import Office
from lanereach.domain.templates.models import Template

router = APIRouter(prefix="/ai", tags=["AI"])

Runner = Annotated[Principal, Depends(require_scopes(Scope.AI_RUN))]
_ERRORS = problem_responses(401, 403, 404, 422, 429, 502, 503)


def get_ai_runtime(principal: CurrentPrincipal, container: ContainerDependency) -> AIRuntime:
    """AI runtime for the caller's tenant.

    Args:
        principal: The caller (tenant for AI run records).
        container: The service container.

    Returns:
        The runtime.
    """
    return AIRuntime(
        llm=container.llm,
        recorder=AIRunRecorder(
            container.session_factory, principal.tenant_id, container.clock, container.settings
        ),
        settings=container.settings,
        budget=AIBudgetGuard.for_tenant(
            container.session_factory, principal.tenant_id, container.clock
        ),
    )


Runtime = Annotated[AIRuntime, Depends(get_ai_runtime)]


async def limit_ai_requests(principal: CurrentPrincipal, container: ContainerDependency) -> None:
    """Count the request against the tenant's and the caller's AI budgets.

    Args:
        principal: The caller.
        container: The service container.

    Raises:
        RateLimitedError: 429 with `Retry-After` when a budget is used up.
    """
    settings = container.settings
    await enforce_rate_limits(
        RedisRateLimiter(container.redis),
        [
            (f"ai:tenant:{principal.tenant_id}", settings.ai_rate_limit_per_tenant_per_minute),
            (
                f"ai:caller:{principal.tenant_id}:{principal.subject}",
                settings.ai_rate_limit_per_caller_per_minute,
            ),
        ],
        now=container.clock.now(),
    )


AIRateLimit = Depends(limit_ai_requests)


class ReplyInput(StrictRequestModel):
    """An email reply to analyse (not stored)."""

    subject: str = Field(default="", max_length=998)
    body: str = Field(min_length=1, max_length=50_000)
    from_email: EmailStr | None = None
    headers: dict[str, str] = Field(default_factory=dict, max_length=50)
    received_at: AwareDatetime | None = None

    def to_message(self, today: date) -> ReplyMessage:
        """Convert to the rules/AI input."""
        return ReplyMessage(
            subject=self.subject,
            body=self.body,
            received_on=self.received_at.date() if self.received_at else today,
            from_email=str(self.from_email or ""),
            headers=self.headers,
        )


class ClassifyReplyRequest(ReplyInput):
    """A reply to classify."""

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "subject": "Re: Shipping to Gulf Importers",
                    "body": "Please quote 2x40HC Chennai to Rotterdam, monthly, FOB.",
                }
            ]
        }
    }

    office_name: str = Field(default="our office", max_length=200)
    contact_country: str = Field(default="unknown", max_length=50)
    use_rules: bool = True


class ClassifyReplyResponse(ResponseModel):
    """Classification with its source (rules or LLM)."""

    classification: ReplyClassification
    source: Literal["rules", "llm"]
    rule: str | None
    return_date: date | None
    ai_run_id: uuid.UUID


class ExtractShipmentResponse(ResponseModel):
    """Extracted shipment details."""

    shipment: ShipmentDetails
    ai_run_id: uuid.UUID


class DraftReplyRequest(ReplyInput):
    """A reply to answer, and who answers."""

    office_id: uuid.UUID
    contact_id: uuid.UUID | None = None
    sender_id: uuid.UUID | None = None
    bucket: ReplyBucket = ReplyBucket.INTERESTED
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")
    template_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    tone: Literal["friendly", "formal", "concise"] = "friendly"


class DraftReplyResponse(ResponseModel):
    """A draft reply (not sent)."""

    draft: ReplyDraft
    ai_run_id: uuid.UUID


class LocalizeTemplateRequest(StrictRequestModel):
    """A template (stored or inline) to localize."""

    template_id: uuid.UUID | None = None
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=20_000)
    target_language: str = Field(pattern=LANGUAGE_VARIANT_PATTERN, examples=["fr", "pt-br"])
    region: str | None = Field(default=None, pattern=r"^[A-Z]{2}$", examples=["FR"])


class LocalizeTemplateResponse(ResponseModel):
    """The localized template (not stored)."""

    localized: LocalizedTemplate
    placeholders: list[str]
    ai_run_id: uuid.UUID


class AIRunResponse(ResponseModel):
    """An AI run."""

    id: uuid.UUID
    task: str
    prompt_version: str
    provider: str
    model: str
    input_ref: str | None
    input_hash: str
    output_json: dict[str, Any] | None
    confidence: float | None
    status: AIRunStatus
    error: str | None
    latency_ms: int
    tokens_in: int
    tokens_out: int
    attempts: int
    estimated_cost: float
    created_at: datetime


@router.post(
    "/replies/classify",
    dependencies=[AIRateLimit],
    response_model=ClassifyReplyResponse,
    summary="Classify a reply",
    description="Rules first (bounces, out-of-office, unsubscribe...), the LLM otherwise. "
    "The email is untrusted input; nothing is stored except the AI run.",
    responses=_ERRORS,
)
async def classify_reply_endpoint(
    payload: ClassifyReplyRequest,
    _principal: Runner,
    runtime: Runtime,
    container: ContainerDependency,
) -> ClassifyReplyResponse:
    """Classify a reply."""
    outcome = await classify_reply(
        runtime,
        payload.to_message(container.clock.now().date()),
        ReplyContext(office_name=payload.office_name, contact_country=payload.contact_country),
        use_rules=payload.use_rules,
    )
    return ClassifyReplyResponse(
        classification=outcome.classification,
        source=outcome.source,
        rule=outcome.rule.rule if outcome.rule else None,
        return_date=outcome.rule.return_date if outcome.rule else None,
        ai_run_id=outcome.ai_run_id,
    )


@router.post(
    "/replies/extract-shipment",
    dependencies=[AIRateLimit],
    response_model=ExtractShipmentResponse,
    summary="Extract shipment details from a reply",
    responses=_ERRORS,
)
async def extract_shipment_endpoint(
    payload: ReplyInput, _principal: Runner, runtime: Runtime, container: ContainerDependency
) -> ExtractShipmentResponse:
    """Extract shipment details."""
    result = await extract_shipment_details(
        runtime, payload.to_message(container.clock.now().date())
    )
    return ExtractShipmentResponse(shipment=result.output, ai_run_id=result.ai_run_id)


@router.post(
    "/replies/draft",
    dependencies=[AIRateLimit],
    response_model=DraftReplyResponse,
    summary="Draft a reply in the office's voice",
    description="Returns a draft only; sending happens through the review queue.",
    responses=_ERRORS,
)
async def draft_reply_endpoint(
    payload: DraftReplyRequest,
    _principal: Runner,
    runtime: Runtime,
    session: TenantSession,
    container: ContainerDependency,
) -> DraftReplyResponse:
    """Draft a reply."""
    office = await session.get(Office, payload.office_id)
    if office is None:
        raise NotFoundError("Office not found.")
    contact = await session.get(Contact, payload.contact_id) if payload.contact_id else None
    if payload.contact_id and contact is None:
        raise NotFoundError("Contact not found.")
    sender = await choose_sender(session, office, container.clock.now(), payload.sender_id)
    language = (
        payload.language or (contact.language if contact else None) or office.default_language
    )
    template = (
        await find_template(
            session, payload.template_key, [language, office.default_language, "en"]
        )
        if payload.template_key
        else None
    )
    result = await draft_reply(
        runtime,
        payload.to_message(container.clock.now().date()),
        DraftContext(
            office_name=office.name,
            office_signature=office.signature,
            sender_name=sender.display_name if sender else office.owner_name,
            contact_first_name=contact.first_name if contact else None,
            language=language,
            bucket=payload.bucket,
            tone=payload.tone,
            template_key=payload.template_key,
            template_body=template.body if template else None,
        ),
    )
    return DraftReplyResponse(draft=result.output, ai_run_id=result.ai_run_id)


@router.post(
    "/templates/localize",
    dependencies=[AIRateLimit],
    response_model=LocalizeTemplateResponse,
    summary="Localize a template (placeholders preserved)",
    description="Pass a stored `template_id` or an inline subject/body. The result is not "
    "stored; save it as a new template language version if it is good.",
    responses=_ERRORS,
)
async def localize_template_endpoint(
    payload: LocalizeTemplateRequest, _principal: Runner, runtime: Runtime, session: TenantSession
) -> LocalizeTemplateResponse:
    """Localize a template."""
    if payload.template_id is not None:
        template = await session.get(Template, payload.template_id)
        if template is None:
            raise NotFoundError("Template not found.")
        subject, body = template.subject, template.body
    elif payload.subject and payload.body:
        subject, body = payload.subject, payload.body
    else:
        raise BusinessRuleViolationError("Give template_id, or subject and body.")
    result = await localize_template(
        runtime,
        subject=subject,
        body=body,
        target_language=split_language_variant(payload.target_language)[0],
        region=payload.region or split_language_variant(payload.target_language)[1],
        input_ref=str(payload.template_id) if payload.template_id else None,
    )
    tokens = placeholder_tokens(result.output.subject) + placeholder_tokens(result.output.body)
    return LocalizeTemplateResponse(
        localized=result.output, placeholders=sorted(tokens), ai_run_id=result.ai_run_id
    )


@router.get(
    "/runs",
    response_model=Page[AIRunResponse],
    summary="List AI runs",
    responses=problem_responses(401, 403),
)
async def list_ai_runs(
    _principal: Runner,
    session: TenantSession,
    task: Annotated[str | None, Query(max_length=64)] = None,
    status: Annotated[AIRunStatus | None, Query()] = None,
    input_ref: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[AIRunResponse]:
    """List AI runs newest first."""
    rows, next_cursor = await AIRunRepository(session).list_runs(
        task=task, status=status, input_ref=input_ref, limit=limit, cursor=cursor
    )
    return Page(items=[AIRunResponse.model_validate(row) for row in rows], next_cursor=next_cursor)


@router.get(
    "/runs/{run_id}",
    response_model=AIRunResponse,
    summary="Get an AI run",
    responses=problem_responses(401, 403, 404),
)
async def read_ai_run(
    run_id: uuid.UUID, _principal: Runner, session: TenantSession
) -> AIRunResponse:
    """Get an AI run."""
    run = await AIRunRepository(session).get(run_id)
    if run is None:
        raise NotFoundError("AI run not found.")
    return AIRunResponse.model_validate(run)


class ReplyProcessingResponse(ResponseModel):
    """What the reply pipeline did with a stored inbound message."""

    message_id: uuid.UUID
    contact_id: uuid.UUID
    classification: ReplyClassification
    source: str
    policy: dict[str, Any]
    actions_taken: list[str]
    shipment: ShipmentDetails | None
    review_item_id: uuid.UUID | None
    lead_id: uuid.UUID | None
    sent_message_id: str | None
    event_id: uuid.UUID | None
    already_processed: bool


@router.post(
    "/replies/{message_id}/process",
    dependencies=[AIRateLimit],
    response_model=ReplyProcessingResponse,
    summary="Run the full reply pipeline on a stored inbound message",
    description="Rules, classification, extraction, policy, protection actions, handover, "
    "draft or review item, and the `reply.classified` event. Idempotent per message: a "
    "processed message returns its stored result (`already_processed: true`).",
    responses=problem_responses(401, 403, 404, 422),
)
async def process_reply_endpoint(
    message_id: uuid.UUID,
    _principal: Runner,
    services: EngineServicesDependency,
    idempotency: IdempotencyGuardDependency,
) -> ReplyProcessingResponse | JSONResponse:
    """Process a stored inbound message."""

    async def process() -> ReplyProcessingResponse:
        result = await process_inbound_reply(services, message_id)
        return ReplyProcessingResponse.model_validate(result.to_json())

    return await run_idempotent(idempotency, status.HTTP_200_OK, process)
