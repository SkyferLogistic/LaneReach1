"""The JSON graph a workflow version stores.

```json
{"trigger": {"id": "t", "type": "trigger_tag_added", "config": {"tag": "uae"}},
 "nodes": [{"id": "n1", "type": "send_email", "config": {...}, "position": {"x": 0, "y": 120}}],
 "edges": [{"source": "t", "target": "n1", "handle": "next"}]}
```

This module only checks the *shape*. Drafts may be saved while semantically invalid (the canvas
saves work in progress); `validate_workflow_graph` reports what must be fixed before publishing.
`position` is kept for the canvas and ignored by the engine.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

MAX_GRAPH_NODES = 200
MAX_GRAPH_EDGES = 500

NodeId = Annotated[
    str,
    Field(
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
        description="Node ID, unique within the graph.",
        examples=["send_intro"],
    ),
]


class _GraphModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GraphPosition(_GraphModel):
    """Canvas coordinates (ignored by the engine)."""

    x: float
    y: float


class GraphNode(_GraphModel):
    """A node (or the trigger) placed on the canvas."""

    id: NodeId
    type: str = Field(max_length=64, examples=["send_email"])
    config: dict[str, Any] = Field(default_factory=dict)
    position: GraphPosition | None = None


class GraphEdge(_GraphModel):
    """A connection from a node's output handle to another node."""

    source: NodeId
    target: NodeId
    handle: str = Field(default="next", max_length=64)


class WorkflowGraph(_GraphModel):
    """Trigger, nodes and edges of one workflow version."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "trigger": {"id": "trigger", "type": "trigger_manual_enroll", "config": {}},
                    "nodes": [{"id": "done", "type": "stop", "config": {}}],
                    "edges": [{"source": "trigger", "target": "done", "handle": "next"}],
                }
            ]
        },
    )

    trigger: GraphNode
    nodes: list[GraphNode] = Field(default_factory=list, max_length=MAX_GRAPH_NODES)
    edges: list[GraphEdge] = Field(default_factory=list, max_length=MAX_GRAPH_EDGES)

    def all_nodes(self) -> list[GraphNode]:
        """Return the trigger followed by the other nodes.

        Returns:
            Every node in the graph.
        """
        return [self.trigger, *self.nodes]
