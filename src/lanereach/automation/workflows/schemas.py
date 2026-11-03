"""Request and response schemas for the workflow endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import AwareDatetime, ConfigDict, Field

from lanereach.ai.schemas import ReplyBucket
from lanereach.automation.workflows.graph import WorkflowGraph
from lanereach.automation.workflows.models import WorkflowStatus
from lanereach.automation.workflows.schedule import CampaignSchedule
from lanereach.core.schemas import ResponseModel, StrictRequestModel

_EXAMPLE_GRAPH: dict[str, Any] = {
    "trigger": {
        "id": "trigger",
        "type": "trigger_tag_added",
        "config": {"tag": "uae-importers"},
        "position": {"x": 0, "y": 0},
    },
    "nodes": [
        {
            "id": "send_intro",
            "type": "send_email",
            "config": {
                "office_id": "6f1c1f3e-3d7b-4c55-9b38-7c2f0f5d9a11",
                "template_key": "intro",
            },
            "position": {"x": 0, "y": 120},
        },
        {"id": "done", "type": "stop", "config": {}, "position": {"x": 0, "y": 240}},
    ],
    "edges": [
        {"source": "trigger", "target": "send_intro", "handle": "next"},
        {"source": "send_intro", "target": "done", "handle": "next"},
    ],
}


class WorkflowCreateRequest(StrictRequestModel):
    """A new workflow. The graph may be incomplete; it is validated when publishing."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "name": "UAE importers intro",
                    "description": "Intro email for UAE importers.",
                    "graph": _EXAMPLE_GRAPH,
                }
            ]
        },
    )

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    graph: WorkflowGraph


class WorkflowUpdateRequest(StrictRequestModel):
    """A new draft version of the graph, optionally renaming the workflow."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    graph: WorkflowGraph
    base_version: int | None = Field(
        default=None,
        ge=1,
        description="The version the editor started from. When set and another version was "
        "saved meanwhile, the update is refused with 409 instead of overwriting it.",
    )


class WorkflowPublishRequest(StrictRequestModel):
    """Which version to publish."""

    version: int | None = Field(
        default=None, ge=1, description="Version to publish; defaults to the latest."
    )


class GraphValidationRequest(StrictRequestModel):
    """A graph to validate without saving it."""

    graph: WorkflowGraph


class WorkflowVersionSummary(ResponseModel):
    """A version without its graph."""

    id: uuid.UUID
    workflow_id: uuid.UUID
    version: int
    created_by: str
    created_at: datetime
    published_at: datetime | None
    is_current: bool = False


class WorkflowVersionResponse(WorkflowVersionSummary):
    """A version with its graph."""

    graph: WorkflowGraph


class WorkflowSummaryResponse(ResponseModel):
    """A workflow as shown in lists."""

    id: uuid.UUID
    name: str
    description: str
    status: WorkflowStatus
    current_version: int | None = Field(description="Published version enrollments start on.")
    latest_version: int
    has_unpublished_changes: bool
    is_paused: bool
    paused_at: datetime | None
    schedule: CampaignSchedule | None = Field(
        default=None, description="Campaign dates and weekdays; empty sends whenever allowed."
    )
    created_by: str
    created_at: datetime
    updated_at: datetime


class WorkflowResponse(WorkflowSummaryResponse):
    """A workflow with its latest version (the one the editor opens)."""

    latest: WorkflowVersionResponse


class SimulatedReply(StrictRequestModel):
    """A classified reply to feed into the simulation (for `reply.*` conditions and branches)."""

    bucket: ReplyBucket
    confidence: float = Field(default=0.95, ge=0, le=1)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")
    summary: str | None = Field(default=None, max_length=500)
    needs_human: bool = False


class TestRunRequest(StrictRequestModel):
    """What to simulate."""

    contact_id: uuid.UUID
    version: int | None = Field(default=None, ge=1, description="Defaults to the latest version.")
    start_at: AwareDatetime | None = Field(
        default=None, description="Simulated start time (defaults to now)."
    )
    reply: SimulatedReply | None = None
    lead: dict[str, Any] | None = Field(default=None, description="Simulated shipment details.")
    variables: dict[str, Any] = Field(default_factory=dict)


class SimulatedStepResponse(ResponseModel):
    """One step of the simulated path."""

    step_index: int
    node_id: str
    node_type: str
    at: datetime
    result: str
    handle: str | None
    detail: str | None
    wait_until: datetime | None
    guardrail_decisions: list[dict[str, Any]]
    output: dict[str, Any] | None
    error: str | None


class SimulatedEmailResponse(ResponseModel):
    """An email the run would send."""

    at: datetime
    kind: str
    from_email: str
    from_name: str
    to_email: str
    subject: str
    body: str
    headers: dict[str, Any]


class SimulatedNotificationResponse(ResponseModel):
    """A notification the run would deliver."""

    at: datetime
    channel: str
    recipient: str
    subject: str
    body: str


class TestRunResponse(ResponseModel):
    """The simulated run."""

    status: str
    stop_reason: str | None
    started_at: datetime
    finished_at: datetime
    truncated: bool = Field(description="True when the simulation hit its step or time limit.")
    steps: list[SimulatedStepResponse]
    emails: list[SimulatedEmailResponse]
    notifications: list[SimulatedNotificationResponse]


class StepCountResponse(ResponseModel):
    """How many enrollments ran a node, and the branches they took."""

    node_id: str
    node_type: str
    enrollments: int
    handles: dict[str, int]


class LinkClicksResponse(ResponseModel):
    """Human clicks on one link."""

    url: str
    clicks: int
    contacts: int


class CampaignStatsResponse(ResponseModel):
    """The funnel of a workflow."""

    workflow_id: uuid.UUID
    enrolled: int
    by_status: dict[str, int]
    sent: int
    contacts_emailed: int
    tracked: int = Field(description="Emails sent with tracking (HTML with tracking on).")
    opened: int = Field(description="Tracked emails opened by a person (scanners excluded).")
    clicked: int
    replied: int = Field(description="Contacts who replied after being enrolled.")
    positive: int
    bounced: int
    unsubscribed: int
    leads: int
    deals: int
    won: int
    open_rate: float
    click_rate: float
    reply_rate: float
    steps: list[StepCountResponse]
    links: list[LinkClicksResponse]
