"""Assistant and help endpoints: ask a question, draft a workflow, read the help articles."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import Field

from lanereach.ai.router import AIRateLimit, get_ai_runtime
from lanereach.ai.tasks.runtime import AIRuntime
from lanereach.assistant.agent import DataAnswer, DataAssistant
from lanereach.assistant.agent_tools import TOOL_SPECS, AgentTools
from lanereach.assistant.embeddings import build_embeddings
from lanereach.assistant.index import sync_help_index
from lanereach.assistant.knowledge import find_article, load_articles, node_reference
from lanereach.assistant.service import AssistantService, ChatTurn
from lanereach.auth.dependencies import IdempotencyGuardDependency, TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import NotFoundError
from lanereach.core.idempotency import run_idempotent
from lanereach.core.openapi import READ, WRITE
from lanereach.core.schemas import ResponseModel, StrictRequestModel

router = APIRouter(tags=["Assistant"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Builder = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_WRITE))]
Admin = Annotated[Principal, Depends(require_scopes(Scope.ADMIN_MANAGE))]


class ChatTurnRequest(StrictRequestModel):
    """An earlier message of the conversation."""

    role: Literal["user", "assistant"]
    text: str = Field(max_length=4000)


class AskRequest(StrictRequestModel):
    """A question about LaneReach."""

    question: str = Field(min_length=2, max_length=2000)
    history: list[ChatTurnRequest] = Field(default_factory=list, max_length=12)


class SourceResponse(ResponseModel):
    """A help section the answer came from."""

    id: str
    slug: str
    title: str
    heading: str
    url: str
    excerpt: str


class AskResponse(ResponseModel):
    """The assistant's answer."""

    answer: str
    supported: bool = Field(description="False when the help does not say LaneReach does this.")
    sources: list[SourceResponse]
    mode: str = Field(
        description="`ai`; `extractive` (no AI key: quoted passages); `sources_only` (no "
        "verified answer, closest sections); `not_found`."
    )
    search: str = Field(description="`hybrid` (vectors and text) or `full_text`.")


class DraftRequest(StrictRequestModel):
    """What the workflow should do, in your words."""

    description: str = Field(min_length=10, max_length=3000)


class DraftStepResponse(ResponseModel):
    """A step of the drafted workflow."""

    id: str
    type: str
    name: str


class DraftResponse(ResponseModel):
    """The drafted workflow, saved as a draft."""

    workflow_id: uuid.UUID
    name: str
    summary: str
    steps: list[DraftStepResponse]
    assumptions: list[str]
    problems: list[str] = Field(description="Validation errors and warnings to fix on the canvas.")
    url: str


class ArticleSummaryResponse(ResponseModel):
    """A help article in the list."""

    slug: str
    title: str
    category: str
    summary: str


class ArticleResponse(ArticleSummaryResponse):
    """A help article with its Markdown body."""

    body: str


class ReindexResponse(ResponseModel):
    """What the help index sync changed."""

    added: int
    updated: int
    removed: int
    embedded: int
    search: str


def get_assistant(
    session: TenantSession,
    container: ContainerDependency,
    runtime: Annotated[AIRuntime, Depends(get_ai_runtime)],
) -> AssistantService:
    """Assistant service for the request."""
    return AssistantService(
        session,
        container.clock,
        container.node_registry,
        runtime,
        build_embeddings(container.settings),
        max_cold_emails=container.settings.max_cold_emails_per_sequence,
    )


Assistant = Annotated[AssistantService, Depends(get_assistant)]


class DataRecordResponse(ResponseModel):
    """A record the answer points to (opens in the console)."""

    ref: str
    kind: Literal["deal", "quote", "contact", "rate", "conversation", "help"]
    label: str
    url: str


class DataStepResponse(ResponseModel):
    """A lookup the assistant made."""

    tool: str
    summary: str


class DataAnswerResponse(ResponseModel):
    """An answer about the company's own data."""

    answer: str
    mode: Literal["ai", "extractive", "partial", "budget"]
    records: list[DataRecordResponse]
    steps: list[DataStepResponse]


def get_data_assistant(
    session: TenantSession,
    container: ContainerDependency,
    runtime: Annotated[AIRuntime, Depends(get_ai_runtime)],
) -> DataAssistant:
    """Data assistant for the request (read-only tools in the caller's tenant)."""
    tools = AgentTools(
        session,
        container.clock,
        container.node_registry,
        build_embeddings(container.settings),
        quiet_days=container.settings.quiet_deal_days,
    )
    return DataAssistant(tools, runtime, container.clock, list(TOOL_SPECS))


def _data_response(result: DataAnswer) -> DataAnswerResponse:
    return DataAnswerResponse(
        answer=result.answer,
        mode=result.mode,
        records=[
            DataRecordResponse(
                ref=record.ref,
                kind=record.kind,
                label=record.label,
                url=record.url,
            )
            for record in result.records
        ],
        steps=[DataStepResponse(tool=step.tool, summary=step.summary) for step in result.steps],
    )


@router.post(
    "/assistant/ask-data",
    response_model=DataAnswerResponse,
    summary="Ask about your own data",
    description="Looks up the company's deals, quotes, contacts, rate sheets, inbox and "
    "activity with read-only tools (at most three lookups), then answers with links to the "
    "records. Every number in the answer comes from the lookups. It cannot change anything.",
    responses=WRITE,
    dependencies=[AIRateLimit],
)
async def ask_about_data(
    payload: AskRequest,
    _principal: Reader,
    assistant: Annotated[DataAssistant, Depends(get_data_assistant)],
    idempotency: IdempotencyGuardDependency,
) -> DataAnswerResponse | JSONResponse:
    """Answer a question about the company's data."""

    async def answer() -> DataAnswerResponse:
        result = await assistant.ask(
            payload.question, [ChatTurn(turn.role, turn.text) for turn in payload.history]
        )
        return _data_response(result)

    return await run_idempotent(idempotency, status.HTTP_200_OK, answer)


@router.post(
    "/assistant/ask",
    response_model=AskResponse,
    summary="Ask the LaneReach assistant",
    description="Answers only from LaneReach's help articles and the live list of workflow "
    "steps, with the sources. It says when LaneReach does not do something. It cannot change "
    "anything.",
    responses=WRITE,
    dependencies=[AIRateLimit],
)
async def ask_assistant(
    payload: AskRequest,
    _principal: Reader,
    assistant: Assistant,
    idempotency: IdempotencyGuardDependency,
) -> AskResponse | JSONResponse:
    """Answer a question."""

    async def answer() -> AskResponse:
        result = await assistant.ask(
            payload.question, [ChatTurn(turn.role, turn.text) for turn in payload.history]
        )
        return AskResponse(
            answer=result.answer,
            supported=result.supported,
            sources=[
                SourceResponse(
                    id=hit.key,
                    slug=hit.slug,
                    title=hit.title,
                    heading=hit.heading,
                    url=hit.url,
                    excerpt=hit.content[:280],
                )
                for hit in result.sources
            ],
            mode=result.mode,
            search=result.search,
        )

    return await run_idempotent(idempotency, status.HTTP_200_OK, answer)


@router.post(
    "/assistant/draft-workflow",
    response_model=DraftResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Draft a workflow from a description",
    description="The draft is checked by the workflow validator and saved as a DRAFT: it is "
    "never published, and nobody is enrolled or emailed. Review it on the canvas.",
    responses=WRITE,
    dependencies=[AIRateLimit],
)
async def draft_workflow(
    payload: DraftRequest,
    principal: Builder,
    assistant: Assistant,
    idempotency: IdempotencyGuardDependency,
) -> DraftResponse | JSONResponse:
    """Draft and save a workflow (needs `workflows:write`)."""

    async def draft() -> DraftResponse:
        result = await assistant.draft_workflow(principal, payload.description)
        return DraftResponse(
            workflow_id=result.workflow.id,
            name=result.workflow.name,
            summary=result.summary,
            steps=[
                DraftStepResponse(id=step_id, type=kind, name=name)
                for step_id, kind, name in result.steps
            ],
            assumptions=result.assumptions,
            problems=result.problems,
            url=f"/workflows/{result.workflow.id}",
        )

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, draft)


@router.get(
    "/help/articles",
    response_model=list[ArticleSummaryResponse],
    summary="Help articles",
    responses=READ,
)
async def list_help_articles(
    _principal: Reader, container: ContainerDependency
) -> list[ArticleSummaryResponse]:
    """Every help article, the generated steps reference included."""
    articles = [*load_articles(), node_reference(container.node_registry)]
    return [
        ArticleSummaryResponse(
            slug=item.slug, title=item.title, category=item.category, summary=item.summary
        )
        for item in articles
    ]


@router.get(
    "/help/articles/{slug}",
    response_model=ArticleResponse,
    summary="One help article",
    responses=READ,
)
async def read_help_article(
    slug: str, _principal: Reader, container: ContainerDependency
) -> ArticleResponse:
    """An article with its Markdown body."""
    article = find_article(slug, container.node_registry)
    if article is None:
        raise NotFoundError("Help article not found.")
    return ArticleResponse(
        slug=article.slug,
        title=article.title,
        category=article.category,
        summary=article.summary,
        body=article.body,
    )


@router.post(
    "/assistant/reindex",
    response_model=ReindexResponse,
    summary="Rebuild the help search index",
    responses=WRITE,
)
async def reindex_help(
    _principal: Admin,
    session: TenantSession,
    container: ContainerDependency,
    idempotency: IdempotencyGuardDependency,
) -> ReindexResponse | JSONResponse:
    """Refresh the tenant's help index now (admins); it is also refreshed daily."""
    embeddings = build_embeddings(container.settings)

    async def reindex() -> ReindexResponse:
        run = await sync_help_index(session, container.clock, container.node_registry, embeddings)
        return ReindexResponse(
            added=run.added,
            updated=run.updated,
            removed=run.removed,
            embedded=run.embedded,
            search="hybrid" if embeddings is not None else "full_text",
        )

    return await run_idempotent(idempotency, status.HTTP_200_OK, reindex)
