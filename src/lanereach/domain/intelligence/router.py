"""Sales-intelligence endpoints: lead scores, weekly digests, follow-up suggestions."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from pydantic import Field

from lanereach.ai.llm.client import AIBudgetReachedError, LLMError
from lanereach.ai.router import AIRateLimit, get_ai_runtime
from lanereach.ai.tasks.runtime import AIRuntime
from lanereach.auth.dependencies import IdempotencyGuardDependency, TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import LaneReachError, NotFoundError, ServiceUnavailableError
from lanereach.core.idempotency import run_idempotent
from lanereach.core.openapi import READ, WRITE
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.domain.crm.models import Deal
from lanereach.domain.crm.service import deal_labels
from lanereach.domain.intelligence.assist import summarize_conversation, write_cover_note
from lanereach.domain.intelligence.digest import DigestService
from lanereach.domain.intelligence.follow_ups import FollowUpService
from lanereach.domain.intelligence.models import FollowUpSuggestion, InsightDigest
from lanereach.domain.intelligence.quality import DraftOutcomes, measure_ai_quality
from lanereach.domain.intelligence.scoring import HOT, LeadScoringService
from lanereach.domain.quotes.service import QuoteService

router = APIRouter(tags=["Sales intelligence"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Writer = Annotated[Principal, Depends(require_scopes(Scope.CRM_WRITE))]
AIUser = Annotated[Principal, Depends(require_scopes(Scope.AI_RUN, Scope.CRM_WRITE))]
Runtime = Annotated[AIRuntime, Depends(get_ai_runtime)]


class ScoreReason(ResponseModel):
    """One reason of a score."""

    label: str
    points: int


class DealScoreResponse(ResponseModel):
    """A deal's lead score."""

    deal_id: uuid.UUID
    score: int | None
    band: Literal["hot", "warm", "cold"] | None
    reasons: list[ScoreReason]
    scored_at: datetime | None
    ai_intent: dict[str, Any] | None


class DigestResponse(ResponseModel):
    """A weekly digest."""

    id: uuid.UUID
    week_start: date
    week_end: date
    headline: str
    summary: str
    highlights: list[dict[str, Any]]
    stats: dict[str, Any]
    written_by: str
    updated_at: datetime


class DigestRequest(StrictRequestModel):
    """Which week to build (defaults to the current week so far)."""

    week_start: date | None = None


class FollowUpResponse(ResponseModel):
    """A follow-up suggestion with its deal."""

    id: uuid.UUID
    deal_id: uuid.UUID
    deal_title: str = ""
    lane: str | None = None
    contact_name: str | None = None
    company_name: str | None = None
    score: int | None = None
    days_quiet: int
    action: str
    reason: str
    subject: str
    body: str
    language: str
    status: str
    written_by: str = Field(default="ai", description="`ai` or `built_in` (no AI call).")
    task_id: uuid.UUID | None
    created_at: datetime


class UsedFollowUpResponse(ResponseModel):
    """A used suggestion and the task it became."""

    suggestion: FollowUpResponse
    task_id: uuid.UUID


def _band(score: int | None) -> Literal["hot", "warm", "cold"] | None:
    if score is None:
        return None
    return "hot" if score >= HOT else "warm" if score >= 45 else "cold"


def _score(deal: Deal) -> DealScoreResponse:
    return DealScoreResponse(
        deal_id=deal.id,
        score=deal.score,
        band=_band(deal.score),
        reasons=[ScoreReason(**reason) for reason in deal.score_reasons or []],
        scored_at=deal.scored_at,
        ai_intent=deal.ai_intent,
    )


async def _follow_ups(
    session: TenantSession, rows: list[FollowUpSuggestion]
) -> list[FollowUpResponse]:
    deals = {row.deal_id: await session.get(Deal, row.deal_id) for row in rows}
    labels = await deal_labels(session, [deal for deal in deals.values() if deal is not None])
    result = []
    for row in rows:
        response = FollowUpResponse.model_validate(row)
        deal = deals.get(row.deal_id)
        if deal is not None:
            response.deal_title = deal.title
            response.lane = (
                f"{deal.origin or '?'} → {deal.destination or '?'}"
                if deal.origin or deal.destination
                else None
            )
            response.score = deal.score
            label = labels.get(deal.id)
            if label is not None:
                response.contact_name = label.contact_name
                response.company_name = label.company_name
        result.append(response)
    return result


def _ai_failed(exc: LLMError) -> LaneReachError:
    # A spent monthly budget is not an outage: say so (429) instead of "try again in a minute".
    if isinstance(exc, AIBudgetReachedError):
        return exc
    return ServiceUnavailableError(
        "The AI could not answer right now. Please try again in a minute."
    )


# --- lead scores -----------------------------------------------------------------------------


@router.get(
    "/crm/deals/{deal_id}/score",
    response_model=DealScoreResponse,
    summary="A deal's lead score and why",
    responses=READ,
)
async def read_deal_score(
    deal_id: uuid.UUID, _principal: Reader, session: TenantSession
) -> DealScoreResponse:
    """The stored score."""
    deal = await session.get(Deal, deal_id)
    if deal is None:
        raise NotFoundError("Deal not found.")
    return _score(deal)


@router.post(
    "/crm/deals/{deal_id}/score",
    response_model=DealScoreResponse,
    summary="Rescore a deal, with the AI reading the latest reply",
    responses=WRITE,
    dependencies=[AIRateLimit],
)
async def rescore_deal(
    deal_id: uuid.UUID,
    _principal: AIUser,
    session: TenantSession,
    container: ContainerDependency,
    runtime: Runtime,
    idempotency: IdempotencyGuardDependency,
) -> DealScoreResponse | JSONResponse:
    """Read intent with AI (when there is a reply) and recompute the score."""

    async def rescore() -> DealScoreResponse:
        deal = await LeadScoringService(session, container.clock, runtime).score_deal(
            deal_id, read_reply=True
        )
        return _score(deal)

    return await run_idempotent(idempotency, status.HTTP_200_OK, rescore)


# --- digests ---------------------------------------------------------------------------------


def _digests(
    session: TenantSession, container: ContainerDependency, runtime: AIRuntime | None
) -> DigestService:
    return DigestService(
        session, container.clock, runtime, quiet_days=container.settings.quiet_deal_days
    )


@router.get(
    "/insights/digests",
    response_model=list[DigestResponse],
    summary="Weekly digests",
    responses=READ,
)
async def list_digests(
    _principal: Reader, session: TenantSession, container: ContainerDependency
) -> list[DigestResponse]:
    """Latest first (12 weeks)."""
    return [
        DigestResponse.model_validate(row)
        for row in await _digests(session, container, None).list_digests()
    ]


@router.get(
    "/insights/digests/{digest_id}",
    response_model=DigestResponse,
    summary="A weekly digest",
    responses=READ,
)
async def read_digest(
    digest_id: uuid.UUID, _principal: Reader, session: TenantSession
) -> DigestResponse:
    """One digest."""
    digest = await session.get(InsightDigest, digest_id)
    if digest is None:
        raise NotFoundError("Digest not found.")
    return DigestResponse.model_validate(digest)


@router.post(
    "/insights/digests",
    response_model=DigestResponse,
    summary="Build (or rebuild) a week's digest now",
    responses=WRITE,
    dependencies=[AIRateLimit],
)
async def build_digest(
    payload: DigestRequest,
    _principal: AIUser,
    session: TenantSession,
    container: ContainerDependency,
    runtime: Runtime,
    idempotency: IdempotencyGuardDependency,
) -> DigestResponse | JSONResponse:
    """The AI writes it from the week's numbers (rules when the AI cannot)."""

    async def build() -> DigestResponse:
        week = payload.week_start or container.clock.now().date()
        return DigestResponse.model_validate(
            await _digests(session, container, runtime).generate(week)
        )

    return await run_idempotent(idempotency, status.HTTP_200_OK, build)


# --- follow-ups ------------------------------------------------------------------------------


def _follow_up_service(
    session: TenantSession, container: ContainerDependency, runtime: AIRuntime | None
) -> FollowUpService:
    return FollowUpService(
        session, container.clock, runtime, quiet_days=container.settings.quiet_deal_days
    )


@router.get(
    "/crm/follow-ups",
    response_model=list[FollowUpResponse],
    summary="Follow-up suggestions for quiet deals",
    responses=READ,
)
async def list_follow_ups(
    _principal: Reader,
    session: TenantSession,
    container: ContainerDependency,
    status_filter: Annotated[
        Literal["open", "used", "dismissed"] | None, Query(alias="status")
    ] = "open",
    deal_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[FollowUpResponse]:
    """Newest first."""
    rows = list(await _follow_up_service(session, container, None).list_suggestions(status_filter))
    if deal_id is not None:
        rows = [row for row in rows if row.deal_id == deal_id]
    return await _follow_ups(session, rows)


@router.post(
    "/crm/deals/{deal_id}/follow-up",
    response_model=FollowUpResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ask the AI for a follow-up suggestion on a deal",
    responses=WRITE,
    dependencies=[AIRateLimit],
)
async def suggest_deal_follow_up(
    deal_id: uuid.UUID,
    _principal: AIUser,
    session: TenantSession,
    container: ContainerDependency,
    runtime: Runtime,
    idempotency: IdempotencyGuardDependency,
) -> FollowUpResponse | JSONResponse:
    """A suggested next step and draft (replaces the deal's open suggestion)."""

    async def suggest() -> FollowUpResponse:
        try:
            suggestion = await _follow_up_service(session, container, runtime).suggest(deal_id)
        except LLMError as exc:
            raise _ai_failed(exc) from exc
        [response] = await _follow_ups(session, [suggestion])
        return response

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, suggest)


@router.post(
    "/crm/follow-ups/{suggestion_id}/use",
    response_model=UsedFollowUpResponse,
    summary="Turn a suggestion into a task for the deal's owner",
    responses=WRITE,
)
async def use_follow_up(
    suggestion_id: uuid.UUID,
    principal: Writer,
    session: TenantSession,
    container: ContainerDependency,
    idempotency: IdempotencyGuardDependency,
) -> UsedFollowUpResponse | JSONResponse:
    """Creates a task with the draft in its notes; nothing is sent."""

    async def use() -> UsedFollowUpResponse:
        suggestion, task = await _follow_up_service(session, container, None).use(
            suggestion_id, principal.subject
        )
        [response] = await _follow_ups(session, [suggestion])
        return UsedFollowUpResponse(suggestion=response, task_id=task.id)

    return await run_idempotent(idempotency, status.HTTP_200_OK, use)


@router.post(
    "/crm/follow-ups/{suggestion_id}/dismiss",
    response_model=FollowUpResponse,
    summary="Dismiss a suggestion",
    responses=WRITE,
)
async def dismiss_follow_up(
    suggestion_id: uuid.UUID,
    principal: Writer,
    session: TenantSession,
    container: ContainerDependency,
    idempotency: IdempotencyGuardDependency,
) -> FollowUpResponse | JSONResponse:
    """Mark a suggestion as not useful."""

    async def dismiss() -> FollowUpResponse:
        suggestion = await _follow_up_service(session, container, None).dismiss(
            suggestion_id, principal.subject
        )
        [response] = await _follow_ups(session, [suggestion])
        return response

    return await run_idempotent(idempotency, status.HTTP_200_OK, dismiss)


# --- AI quality ------------------------------------------------------------------------------


class DraftOutcomesResponse(ResponseModel):
    """What people did with drafts of one writer."""

    sent_as_written: int
    edited: int
    rejected: int
    waiting: int
    unchanged_percent: int | None = Field(
        description="Share of decided drafts sent exactly as written (null before any decision)."
    )


class FollowUpOutcomesResponse(ResponseModel):
    """What people did with follow-up suggestions of one writer."""

    used: int
    dismissed: int
    open: int


class AIQualityResponse(ResponseModel):
    """The AI's track record, from what people did with its work."""

    days: int
    replies_sorted_by_rules: int
    replies_sorted_by_ai: int
    replies_sent_to_a_person: int
    ai_drafts: DraftOutcomesResponse
    built_in_drafts: DraftOutcomesResponse
    ai_follow_ups: FollowUpOutcomesResponse
    built_in_follow_ups: FollowUpOutcomesResponse
    won_average_score: int | None
    lost_average_score: int | None
    won_scored: int
    lost_scored: int
    ai_runs: int
    ai_runs_failed: int


def _outcomes(outcomes: DraftOutcomes) -> DraftOutcomesResponse:
    return DraftOutcomesResponse(
        sent_as_written=outcomes.sent_as_written,
        edited=outcomes.edited,
        rejected=outcomes.rejected,
        waiting=outcomes.waiting,
        unchanged_percent=outcomes.unchanged_percent,
    )


@router.get(
    "/insights/ai-quality",
    response_model=AIQualityResponse,
    summary="How well the AI is doing",
    description="From what people did with the AI's work over the period: replies sorted by "
    "rules, AI or a person; drafts sent as written, edited or rejected (AI and built-in); "
    "follow-ups used or dismissed; the average lead score of won and lost deals; failed AI "
    "runs. No AI call.",
    responses=READ,
)
async def read_ai_quality(
    _principal: Reader,
    session: TenantSession,
    container: ContainerDependency,
    days: Annotated[int, Query(ge=7, le=365)] = 30,
) -> AIQualityResponse:
    """The AI's track record."""
    quality = await measure_ai_quality(session, container.clock.now(), days)
    return AIQualityResponse(
        days=quality.days,
        replies_sorted_by_rules=quality.replies_by["rules"],
        replies_sorted_by_ai=quality.replies_by["ai"],
        replies_sent_to_a_person=quality.replies_by["person"],
        ai_drafts=_outcomes(quality.drafts["ai"]),
        built_in_drafts=_outcomes(quality.drafts["built_in"]),
        ai_follow_ups=FollowUpOutcomesResponse(**quality.follow_ups["ai"]),
        built_in_follow_ups=FollowUpOutcomesResponse(**quality.follow_ups["built_in"]),
        won_average_score=quality.won_average_score,
        lost_average_score=quality.lost_average_score,
        won_scored=quality.won_scored,
        lost_scored=quality.lost_scored,
        ai_runs=quality.ai_runs,
        ai_runs_failed=quality.ai_runs_failed,
    )


# --- assists ---------------------------------------------------------------------------------


class ThreadSummaryResponse(ResponseModel):
    """What a conversation is about (not stored)."""

    summary: str
    customer_wants: list[str]
    open_questions: list[str]
    next_step: str
    sentiment: Literal["positive", "neutral", "negative"]
    language: str
    messages_read: int


class CoverNoteResponse(ResponseModel):
    """A drafted message above a quote."""

    intro: str
    language: str


@router.post(
    "/inbox/conversation/summary",
    response_model=ThreadSummaryResponse,
    summary="Summarise a conversation with AI (nothing is stored)",
    responses=WRITE,
    dependencies=[AIRateLimit],
)
async def summarize_inbox_conversation(
    thread_id: Annotated[str, Query(min_length=1, max_length=300)],
    _principal: Annotated[Principal, Depends(require_scopes(Scope.AI_RUN))],
    session: TenantSession,
    container: ContainerDependency,
    runtime: Runtime,
) -> ThreadSummaryResponse:
    """Where it stands, what they want, open questions, next step."""
    try:
        result = await summarize_conversation(session, container.clock, runtime, thread_id)
    except LLMError as exc:
        raise _ai_failed(exc) from exc
    return ThreadSummaryResponse(**result.summary.model_dump(), messages_read=result.messages_read)


@router.post(
    "/crm/quotes/{quote_id}/cover-note",
    response_model=CoverNoteResponse,
    summary="Draft the message above a quote with AI",
    description="Uses only the quote's own facts (checked); the text fills the quote builder for "
    "a person to edit. Nothing is sent.",
    responses=WRITE,
    dependencies=[AIRateLimit],
)
async def draft_cover_note(
    quote_id: uuid.UUID,
    _principal: AIUser,
    session: TenantSession,
    container: ContainerDependency,
    runtime: Runtime,
) -> CoverNoteResponse:
    """A short cover note in the contact's language."""
    quotes = QuoteService(session, container.clock, container.task_queue)
    try:
        note = await write_cover_note(session, quotes, runtime, quote_id)
    except LLMError as exc:
        raise _ai_failed(exc) from exc
    return CoverNoteResponse(intro=note.intro, language=note.language)
