"""Public landing-page data: what the product does, counted from the code, and how to reach us.

Unauthenticated and tenant-free. The numbers on the landing page come from here (registered
workflow steps, send checks, AI tasks, service lines), so the page never claims more than the
running version does. "Contact us" posts to an ordinary web form of the operator's tenant
(`PUBLIC_CONTACT_FORM_KEY`), so enquiries arrive as contacts in its own CRM.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from lanereach.ai.llm.client import LLMError
from lanereach.ai.prompts.registry import load_prompts
from lanereach.ai.tasks.runtime import AIRuntime
from lanereach.automation.guardrails.chain import DEFAULT_CHAIN
from lanereach.automation.nodes.base import NodeCategory
from lanereach.automation.nodes.registry import load_node_catalog
from lanereach.core.config import AuthMode
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import NotFoundError, ServiceUnavailableError
from lanereach.core.rate_limit import RedisRateLimiter, enforce_rate_limits
from lanereach.core.schemas import StrictRequestModel
from lanereach.domain.logistics import LINE_LABELS
from lanereach.domain.senders.models import ServiceLine
from lanereach.website.try_ai import DiscardingRecorder, try_ai_on_reply

router = APIRouter(prefix="/site", tags=["Website"])


class StepCounts(BaseModel):
    """Workflow steps by palette group."""

    total: int
    triggers: int
    actions: int
    logic: int
    ai: int
    protection: int


class ContactSettings(BaseModel):
    """Where "Contact us" goes."""

    form_key: str | None
    email: str | None


class SiteFactsResponse(BaseModel):
    """What the running version of LaneReach does."""

    service_lines: list[str]
    workflow_steps: StepCounts
    send_checks: list[str]
    ai_tasks: list[str]
    sign_in: Literal["microsoft", "development"]
    contact: ContactSettings
    ai_demo: bool


@router.get(
    "",
    response_model=SiteFactsResponse,
    summary="Product facts and contact settings for the public website (no sign-in)",
)
async def read_site_facts(container: ContainerDependency) -> SiteFactsResponse:
    """Counted from the registered steps, guardrails and AI prompts of this version."""
    nodes = load_node_catalog().all_nodes()

    def count(category: NodeCategory) -> int:
        return sum(1 for node in nodes if node.category is category)

    settings = container.settings
    return SiteFactsResponse(
        service_lines=[label for line, label in LINE_LABELS.items() if line is not ServiceLine.ANY],
        workflow_steps=StepCounts(
            total=len(nodes),
            triggers=count(NodeCategory.TRIGGER),
            actions=count(NodeCategory.ACTION),
            logic=count(NodeCategory.LOGIC),
            ai=count(NodeCategory.AI),
            protection=count(NodeCategory.PROTECTION),
        ),
        send_checks=[guardrail.name for guardrail in DEFAULT_CHAIN],
        ai_tasks=sorted({task for task, _version in load_prompts()}),
        sign_in="microsoft" if settings.auth_mode is AuthMode.OIDC else "development",
        contact=ContactSettings(
            form_key=settings.public_contact_form_key, email=settings.public_contact_email
        ),
        ai_demo=settings.public_ai_demo,
    )


class TryAiRequest(StrictRequestModel):
    """A reply a visitor wants the AI to read (not stored)."""

    subject: str = Field(default="", max_length=200)
    text: str = Field(min_length=10, max_length=2000)


class TryAiResponse(BaseModel):
    """What LaneReach's AI made of the reply."""

    bucket: str
    confidence: float
    language: str
    summary: str
    needs_human: bool
    source: Literal["rules", "llm"]
    shipment: dict[str, Any] | None
    next_steps: list[str]


def visitor_address(request: Request) -> str:
    """The visitor's address: the hop nginx appended to X-Forwarded-For, else the peer.

    Only used for rate limiting; the overall per-minute limit bounds cost even if the header
    is forged by someone calling the API directly.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    if hops:
        return hops[-1][:64]
    return request.client.host if request.client else "unknown"


@router.post(
    "/try-ai",
    response_model=TryAiResponse,
    summary="Let the reply AI read a pasted email (public, rate limited, nothing stored)",
    description="Classifies the reply like the reply pipeline does and, for freight requests, "
    "extracts the shipment. Available when `PUBLIC_AI_DEMO` is on.",
)
async def try_ai(
    payload: TryAiRequest, request: Request, container: ContainerDependency
) -> TryAiResponse:
    """Run the reply AI on a visitor's example (no contact, deal or email is created)."""
    settings = container.settings
    if not settings.public_ai_demo:
        raise NotFoundError("The AI demo is not available.")
    await enforce_rate_limits(
        RedisRateLimiter(container.redis),
        [
            (
                f"site-ai:visitor:{visitor_address(request)}",
                settings.public_ai_demo_per_ip_per_minute,
            ),
            ("site-ai:all:website", settings.public_ai_demo_per_minute),
        ],
        now=container.clock.now(),
    )
    runtime = AIRuntime(llm=container.llm, recorder=DiscardingRecorder(), settings=settings)
    try:
        result = await try_ai_on_reply(
            runtime,
            subject=payload.subject.strip(),
            body=payload.text.strip(),
            today=container.clock.now().date(),
        )
    except LLMError as exc:
        raise ServiceUnavailableError(
            "The AI is busy right now. Please try again in a minute."
        ) from exc
    classification = result.classification
    return TryAiResponse(
        bucket=classification.bucket.value,
        confidence=classification.confidence,
        language=classification.language,
        summary=classification.summary,
        needs_human=classification.needs_human,
        source="rules" if result.source == "rules" else "llm",
        shipment=result.shipment.model_dump(mode="json") if result.shipment else None,
        next_steps=result.next_steps,
    )
