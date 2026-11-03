"""What the assistant's AI tasks must return (validated before anything is used)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HelpAnswer(_Output):
    """An answer to a question about LaneReach (output of `answer_help_question`)."""

    answer: str = Field(min_length=1, max_length=4000, description="Plain text, may use - lists.")
    source_ids: list[str] = Field(max_length=6, description="IDs of the help sections used.")
    supported: bool = Field(description="False when the help does not say LaneReach does this.")
    confidence: float = Field(ge=0, le=1)


class DraftStep(_Output):
    """A trigger or step of a drafted workflow."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    type: str = Field(max_length=64)
    config_json: str = Field(max_length=4000, description="The step's settings as a JSON object.")


class DraftEdge(_Output):
    """A connection from a step's output to the next step."""

    source: str = Field(max_length=64)
    target: str = Field(max_length=64)
    handle: str = Field(max_length=64, description="The output name, `next` for most steps.")


class DraftedWorkflow(_Output):
    """A workflow drafted from a description (output of `draft_workflow`)."""

    name: str = Field(min_length=3, max_length=100)
    summary: str = Field(max_length=600)
    trigger: DraftStep
    steps: list[DraftStep] = Field(min_length=1, max_length=25)
    edges: list[DraftEdge] = Field(min_length=1, max_length=60)
    assumptions: list[str] = Field(max_length=8, description="What you assumed; the person checks.")


AgentTool = Literal[
    "find_deals",
    "find_quotes",
    "find_contacts",
    "find_rates",
    "inbox_waiting",
    "activity_summary",
    "search_help",
]


class AgentArguments(_Output):
    """Filters for a data-assistant tool (each tool uses some of them)."""

    query: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, max_length=20)
    origin: str | None = Field(default=None, max_length=120)
    destination: str | None = Field(default=None, max_length=120)
    mode: str | None = Field(default=None, max_length=20)
    equipment: str | None = Field(default=None, max_length=30)
    min_score: int | None = Field(default=None, ge=0, le=100)
    quiet_days: int | None = Field(default=None, ge=0, le=365)
    within_days: int | None = Field(default=None, ge=0, le=365)
    limit: int | None = Field(default=None, ge=1, le=10)


class AgentStep(_Output):
    """One step of the data assistant (output of `answer_data_question`)."""

    action: Literal["use_tool", "answer"]
    tool: AgentTool | None = None
    arguments: AgentArguments | None = None
    answer: str | None = Field(default=None, max_length=3000)
    record_refs: list[str] = Field(default_factory=list, max_length=10)

    def arguments_or_empty(self) -> AgentArguments:
        """The tool arguments (none given = no filters)."""
        return self.arguments or AgentArguments()
