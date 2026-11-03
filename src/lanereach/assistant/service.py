"""The assistant: answers questions from the help and drafts workflows (saved as drafts only).

Honesty is built in, not left to the model:

* Answers are generated only from retrieved help sections; with none found, no model is called
  and the answer says so. Cited sources are checked against what was retrieved.
* A drafted workflow must pass the same validator as the canvas (the model gets one retry with
  the errors); it is saved as a draft and never published, enrolled or run by the assistant.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.ai.llm.client import LLMError
from lanereach.ai.prompts.registry import wrap_untrusted
from lanereach.ai.tasks.runtime import AIRuntime, run_prompted_task
from lanereach.assistant.embeddings import EmbeddingsProvider
from lanereach.assistant.index import ensure_help_index
from lanereach.assistant.schemas import DraftedWorkflow, HelpAnswer
from lanereach.assistant.search import SearchHit, search_help
from lanereach.auth.principal import Principal
from lanereach.automation.nodes.registry import NodeRegistry
from lanereach.automation.workflows.graph import WorkflowGraph
from lanereach.automation.workflows.models import Workflow, WorkflowStatus
from lanereach.automation.workflows.references import check_workflow_references
from lanereach.automation.workflows.service import WorkflowService
from lanereach.automation.workflows.validator import validate_workflow_graph
from lanereach.core.clock import Clock
from lanereach.domain.offices.models import Office
from lanereach.domain.templates.models import Template

logger = structlog.get_logger(__name__)

MAX_HISTORY = 6
NOT_FOUND = (
    "I could not find this in the LaneReach help, so LaneReach may not do it. Try other words, or "
    "browse the Help center. If you expected LaneReach to do this, ask your administrator."
)
UNVERIFIED = (
    "I could not give a verified answer to this. The help sections below are the closest matches "
    "to your question."
)


@dataclass(frozen=True)
class ChatTurn:
    """An earlier message of the conversation."""

    role: str  # "user" or "assistant"
    text: str


@dataclass(frozen=True)
class AskResult:
    """An answer with its sources."""

    answer: str
    supported: bool
    sources: list[SearchHit]
    mode: str  # "ai", "extractive" (no AI key) or "not_found"
    search: str  # "hybrid" or "full_text"
    ai_run_id: uuid.UUID | None = None


@dataclass(frozen=True)
class DraftResult:
    """A drafted workflow saved as a draft."""

    workflow: Workflow
    summary: str
    steps: list[tuple[str, str, str]]  # (id, type, display name)
    assumptions: list[str]
    problems: list[str] = field(default_factory=list)


def _history_text(history: list[ChatTurn]) -> str:
    turns = history[-MAX_HISTORY:]
    return "\n".join(f"{turn.role}: {turn.text.strip()[:1000]}" for turn in turns) or "(none)"


class AssistantService:
    """Ask questions and draft workflows.

    Args:
        session: Tenant session.
        clock: Time source.
        registry: Node registry.
        runtime: AI runtime (LLM client and run recorder).
        embeddings: Embeddings provider, or None for full-text search only.
        max_cold_emails: The workflow validator's cold-email limit.
    """

    def __init__(
        self,
        session: AsyncSession,
        clock: Clock,
        registry: NodeRegistry,
        runtime: AIRuntime,
        embeddings: EmbeddingsProvider | None,
        *,
        max_cold_emails: int,
    ) -> None:
        self._session = session
        self._clock = clock
        self._registry = registry
        self._runtime = runtime
        self._embeddings = embeddings
        self._max_cold_emails = max_cold_emails

    async def ask(self, question: str, history: list[ChatTurn]) -> AskResult:
        """Answer a question about LaneReach from its help articles.

        Args:
            question: The person's question.
            history: Earlier turns (the last six are used).

        Returns:
            The answer, whether LaneReach does it, and the sections it came from.
        """
        await ensure_help_index(self._session, self._clock, self._registry, self._embeddings)
        # Follow-up questions ("and for ocean?") are searched with the previous question.
        previous = next((turn.text for turn in reversed(history) if turn.role == "user"), "")
        found = await search_help(self._session, f"{question} {previous}".strip(), self._embeddings)
        if not found.hits:
            return AskResult(NOT_FOUND, False, [], "not_found", found.mode)
        by_id = {hit.key: hit for hit in found.hits}
        articles = [
            {
                "id": hit.key,
                "slug": hit.slug,
                "title": hit.title,
                "heading": hit.heading,
                "text": hit.content,
            }
            for hit in found.hits
        ]

        def check(output: HelpAnswer) -> None:
            unknown = [source for source in output.source_ids if source not in by_id]
            if unknown:
                raise ValueError(f"source_ids must be IDs from <help_articles>, not {unknown}")

        try:
            result, run_id = await run_prompted_task(
                self._runtime,
                task="answer_help_question",
                values={
                    "articles": json.dumps(articles, ensure_ascii=False, indent=1),
                    "history": wrap_untrusted(_history_text(history), "conversation"),
                    "question": wrap_untrusted(question.strip()[:2000], "question"),
                },
                output_model=HelpAnswer,
                input_ref=None,
                max_tokens=900,
                validate=check,
            )
        except LLMError:
            # No verified answer (model down, or it cited sections it was not given): show the
            # matching sections instead of an unverified answer.
            return AskResult(UNVERIFIED, False, found.hits[:3], "sources_only", found.mode)
        answer = result.output
        cited = [by_id[source] for source in dict.fromkeys(answer.source_ids)]
        mode = "extractive" if self._runtime.llm.provider_name == "mock" else "ai"
        return AskResult(
            answer.answer, answer.supported, cited or found.hits[:1], mode, found.mode, run_id
        )

    # --- Drafting workflows -----------------------------------------------------------------

    def _catalog(self) -> list[dict[str, Any]]:
        nodes = []
        for node_class in self._registry.all_nodes():
            schema = node_class.config_model.model_json_schema()
            fields = {
                name: {
                    key: spec[key]
                    for key in ("type", "enum", "description", "default", "items", "anyOf")
                    if key in spec
                }
                for name, spec in (schema.get("properties") or {}).items()
            }
            nodes.append(
                {
                    "type": node_class.node_type,
                    "category": node_class.category.value,
                    "name": node_class.display_name,
                    "description": node_class.description,
                    "outputs": "dynamic (from config)"
                    if node_class.has_dynamic_handles
                    else list(node_class.output_handles),
                    "config": fields,
                    "required": schema.get("required", []),
                }
            )
        return nodes

    async def _workspace(self) -> dict[str, Any]:
        offices = (
            await self._session.execute(select(Office.id, Office.name).order_by(Office.name))
        ).all()
        templates = (
            await self._session.execute(
                select(Template.key, func.min(Template.name))
                .group_by(Template.key)
                .order_by(Template.key)
                .limit(60)
            )
        ).all()
        workflows = (
            await self._session.execute(
                select(Workflow.id, Workflow.name)
                .where(Workflow.status == WorkflowStatus.PUBLISHED)
                .limit(50)
            )
        ).all()
        return {
            "offices": [{"id": str(office_id), "name": name} for office_id, name in offices],
            "templates": [{"key": key, "name": name} for key, name in templates],
            "published_workflows": [
                {"id": str(item_id), "name": name} for item_id, name in workflows
            ],
        }

    def _graph(self, draft: DraftedWorkflow) -> WorkflowGraph:
        def node(step: Any) -> dict[str, Any]:
            try:
                config = json.loads(step.config_json or "{}")
            except ValueError as exc:
                raise ValueError(f"config_json of {step.id} is not JSON") from exc
            if not isinstance(config, dict):
                raise ValueError(f"config_json of {step.id} must be a JSON object")
            return {"id": step.id, "type": step.type, "config": config}

        return WorkflowGraph.model_validate(
            {
                "trigger": node(draft.trigger),
                "nodes": [node(step) for step in draft.steps],
                "edges": [edge.model_dump() for edge in draft.edges],
            }
        )

    async def draft_workflow(self, principal: Principal, description: str) -> DraftResult:
        """Draft a workflow from a description and save it as a draft.

        Args:
            principal: The person asking (recorded as the creator).
            description: What the workflow should do.

        Returns:
            The saved draft, its steps and the model's assumptions.

        Raises:
            LLMError: When the model cannot produce a valid workflow (after one retry).
        """
        registry, limit = self._registry, self._max_cold_emails

        def check(output: DraftedWorkflow) -> None:
            graph = self._graph(output)
            report = validate_workflow_graph(graph, registry=registry, max_cold_emails=limit)
            if report.errors:
                raise ValueError(
                    "The workflow is not valid: "
                    + "; ".join(
                        f"{issue.node_id or 'workflow'}: {issue.message}"
                        for issue in report.errors[:12]
                    )
                )

        result, _run_id = await run_prompted_task(
            self._runtime,
            task="draft_workflow",
            values={
                "catalog": json.dumps(self._catalog(), ensure_ascii=False),
                "workspace": json.dumps(await self._workspace(), ensure_ascii=False),
                "description": wrap_untrusted(description.strip()[:3000], "description"),
            },
            output_model=DraftedWorkflow,
            input_ref=None,
            max_tokens=4000,
            validate=check,
        )
        draft = result.output
        graph = self._graph(draft)
        report = validate_workflow_graph(graph, registry=registry, max_cold_emails=limit)
        report = await check_workflow_references(self._session, graph, registry, report)
        name = await self._unique_name(draft.name)
        service = WorkflowService(self._session, self._clock, registry, max_cold_emails=limit)
        created = await service.create_workflow(
            principal,
            name=name,
            description=(
                f"{draft.summary}\n\nDrafted by the LaneReach assistant from: {description.strip()}"
            )[:2000],
            graph=graph,
        )
        steps = [
            (
                step.id,
                step.type,
                registry.get(step.type).display_name if step.type in registry else step.type,
            )
            for step in [draft.trigger, *draft.steps]
        ]
        problems = [issue.message for issue in [*report.errors, *report.warnings]]
        logger.info(
            "assistant_workflow_drafted", workflow_id=str(created.workflow.id), steps=len(steps)
        )
        return DraftResult(
            created.workflow, draft.summary, steps, list(draft.assumptions), problems
        )

    async def _unique_name(self, wanted: str) -> str:
        base = (wanted.strip() or "Drafted workflow")[:80]
        names = set(
            (
                await self._session.scalars(
                    select(Workflow.name).where(Workflow.name.startswith(base))
                )
            ).all()
        )
        if f"{base} (draft)" not in names:
            return f"{base} (draft)"
        for index in range(2, 100):
            candidate = f"{base} (draft {index})"
            if candidate not in names:
                return candidate
        return f"{base} ({uuid.uuid4().hex[:6]})"
