"""Semantic validation of workflow graphs (spec §6.3).

`validate_workflow_graph` returns every problem at once, each naming the node it concerns, so
the canvas can highlight them. Errors block publishing; warnings do not.

Rules (errors):

* exactly one trigger, of a trigger node type;
* every node type is registered and every config validates against the node's schema;
* node IDs are unique; edges connect existing nodes through handles that exist on the source;
  each handle leads to at most one node; nothing points back into the trigger;
* no orphan nodes (everything is reachable from the trigger);
* the graph is acyclic (v1). Cycles are reported and the remaining rules still run on the graph
  without the edges that close them, so every problem is listed at once;
* every path ends in `stop` or a terminal action: every output handle of a non-terminal node
  must be connected;
* at most `MAX_COLD_EMAILS_PER_SEQUENCE` `send_email` nodes on any path;
* every `send_email` after the first on a path is preceded by a wait since the previous send.

Warnings flag things that are allowed but probably unintended, such as `reply.*` conditions
on a path where no reply has been classified.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, ValidationError, computed_field

from lanereach.automation.conditions import (
    AllCondition,
    AnyCondition,
    Comparison,
    NotCondition,
    iter_condition_fields,
)
from lanereach.automation.nodes.base import BaseNode, NodeCategory, NodeConfig
from lanereach.automation.nodes.registry import NodeRegistry
from lanereach.automation.workflows.graph import GraphNode, WorkflowGraph

type NodeClass = type[BaseNode[Any]]

REPLY_TRIGGER = "trigger_reply_classified"
CLASSIFY_NODE = "ai_classify_reply"
EXTRACT_NODE = "ai_extract_shipment_details"


class ValidationCode(StrEnum):
    """Stable identifiers of validation findings (the canvas maps them to help texts)."""

    UNKNOWN_NODE_TYPE = "unknown_node_type"
    INVALID_TRIGGER_TYPE = "invalid_trigger_type"
    MULTIPLE_TRIGGERS = "multiple_triggers"
    INVALID_CONFIG = "invalid_config"
    DUPLICATE_NODE_ID = "duplicate_node_id"
    UNKNOWN_EDGE_SOURCE = "unknown_edge_source"
    UNKNOWN_EDGE_TARGET = "unknown_edge_target"
    EDGE_INTO_TRIGGER = "edge_into_trigger"
    UNKNOWN_HANDLE = "unknown_handle"
    HANDLE_HAS_MULTIPLE_TARGETS = "handle_has_multiple_targets"
    ORPHAN_NODE = "orphan_node"
    CYCLE_DETECTED = "cycle_detected"
    TRIGGER_NOT_CONNECTED = "trigger_not_connected"
    UNCONNECTED_HANDLE = "unconnected_handle"
    TOO_MANY_EMAILS = "too_many_emails"
    SEND_WITHOUT_WAIT = "send_without_wait"
    UNKNOWN_OFFICE = "unknown_office"
    UNKNOWN_SENDER = "unknown_sender"
    UNKNOWN_TEMPLATE = "unknown_template"
    UNKNOWN_WORKFLOW = "unknown_workflow"
    WORKFLOW_NOT_RUNNING = "workflow_not_running"
    REPLY_FIELDS_WITHOUT_REPLY = "reply_fields_without_reply"
    LEAD_FIELDS_WITHOUT_EXTRACTION = "lead_fields_without_extraction"
    OFFICE_WITHOUT_SENDER = "office_without_sender"


class ValidationIssue(BaseModel):
    """One finding."""

    code: ValidationCode
    message: str
    node_id: str | None = Field(default=None, description="Node the finding is about.")
    handle: str | None = Field(default=None, description="Output handle, when relevant.")


class ValidationReport(BaseModel):
    """All findings for a graph."""

    errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_valid(self) -> bool:
        """True when there are no errors (warnings allowed)."""
        return not self.errors


class _Checker:
    def __init__(self, graph: WorkflowGraph, registry: NodeRegistry, max_cold_emails: int):
        self.graph = graph
        self.registry = registry
        self.max_cold_emails = max_cold_emails
        self.report = ValidationReport()
        self.nodes: dict[str, GraphNode] = {}
        self.classes: dict[str, NodeClass] = {}
        self.configs: dict[str, NodeConfig] = {}
        self.successors: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self.predecessors: dict[str, list[str]] = defaultdict(list)
        self.back_edges: set[tuple[str, str]] = set()

    def error(self, code: ValidationCode, message: str, node_id: str | None, **extra: Any) -> None:
        self.report.errors.append(
            ValidationIssue(code=code, message=message, node_id=node_id, **extra)
        )

    def warn(self, code: ValidationCode, message: str, node_id: str | None) -> None:
        self.report.warnings.append(ValidationIssue(code=code, message=message, node_id=node_id))

    # --- structure -----------------------------------------------------------------------

    def check_nodes(self) -> None:
        for node in self.graph.all_nodes():
            if node.id in self.nodes:
                self.error(
                    ValidationCode.DUPLICATE_NODE_ID, f"Node ID '{node.id}' is used twice.", node.id
                )
                continue
            self.nodes[node.id] = node
            node_class = self.registry.find(node.type)
            if node_class is None:
                self.error(
                    ValidationCode.UNKNOWN_NODE_TYPE, f"Unknown node type '{node.type}'.", node.id
                )
                continue
            is_trigger = node.id == self.graph.trigger.id
            if is_trigger and node_class.category is not NodeCategory.TRIGGER:
                self.error(
                    ValidationCode.INVALID_TRIGGER_TYPE,
                    f"'{node.type}' is not a trigger; the workflow must start with a trigger.",
                    node.id,
                )
                continue
            if not is_trigger and node_class.category is NodeCategory.TRIGGER:
                self.error(
                    ValidationCode.MULTIPLE_TRIGGERS,
                    "A workflow has exactly one trigger; remove this extra trigger node.",
                    node.id,
                )
                continue
            self.classes[node.id] = node_class
            try:
                self.configs[node.id] = node_class.parse_config(node.config)
            except ValidationError as exc:
                for problem in exc.errors():
                    location = ".".join(str(part) for part in problem["loc"]) or "config"
                    self.error(
                        ValidationCode.INVALID_CONFIG,
                        f"{location}: {problem['msg']}",
                        node.id,
                    )

    def handles_of(self, node_id: str) -> tuple[str, ...] | None:
        """Known output handles of a node, or None if its type/config is invalid."""
        node_class = self.classes.get(node_id)
        config = self.configs.get(node_id)
        if node_class is None or config is None:
            return None
        return node_class.handles_for(config)

    def check_edges(self) -> None:
        taken: set[tuple[str, str]] = set()
        trigger_id = self.graph.trigger.id
        for edge in self.graph.edges:
            if edge.source not in self.nodes:
                self.error(
                    ValidationCode.UNKNOWN_EDGE_SOURCE,
                    f"Edge starts at unknown node '{edge.source}'.",
                    edge.source,
                )
                continue
            if edge.target not in self.nodes:
                self.error(
                    ValidationCode.UNKNOWN_EDGE_TARGET,
                    f"Edge from '{edge.source}' points to unknown node '{edge.target}'.",
                    edge.source,
                    handle=edge.handle,
                )
                continue
            if edge.target == trigger_id:
                self.error(
                    ValidationCode.EDGE_INTO_TRIGGER,
                    "Edges cannot point back into the trigger.",
                    edge.source,
                    handle=edge.handle,
                )
                continue
            handles = self.handles_of(edge.source)
            if handles is not None and edge.handle not in handles:
                available = ", ".join(handles) or "none"
                self.error(
                    ValidationCode.UNKNOWN_HANDLE,
                    f"'{self.nodes[edge.source].type}' has no output '{edge.handle}' "
                    f"(available: {available}).",
                    edge.source,
                    handle=edge.handle,
                )
                continue
            if (edge.source, edge.handle) in taken:
                self.error(
                    ValidationCode.HANDLE_HAS_MULTIPLE_TARGETS,
                    f"Output '{edge.handle}' is connected more than once; each output leads to "
                    "exactly one node.",
                    edge.source,
                    handle=edge.handle,
                )
                continue
            taken.add((edge.source, edge.handle))
            self.successors[edge.source].append((edge.handle, edge.target))
            self.predecessors[edge.target].append(edge.source)

    def reachable(self) -> set[str]:
        seen = {self.graph.trigger.id}
        stack = [self.graph.trigger.id]
        while stack:
            for _, target in self.successors[stack.pop()]:
                if target not in seen:
                    seen.add(target)
                    stack.append(target)
        return seen

    def check_orphans(self, reachable: set[str]) -> None:
        for node_id in self.nodes:
            if node_id not in reachable:
                self.error(
                    ValidationCode.ORPHAN_NODE,
                    "Node is not reachable from the trigger.",
                    node_id,
                )

    def find_cycles(self) -> list[list[str]]:
        """Depth-first search recording every back edge and the cycle it closes.

        Back edges are remembered so the path rules can still run on the rest of the graph:
        a user then sees every problem at once instead of fixing the cycle first.
        """
        white, grey, black = 0, 1, 2
        color = dict.fromkeys(self.nodes, white)
        path: list[str] = []
        cycles: list[list[str]] = []
        trigger_id = self.graph.trigger.id
        ordered = [trigger_id, *(node_id for node_id in self.nodes if node_id != trigger_id)]

        def visit(node_id: str) -> None:
            color[node_id] = grey
            path.append(node_id)
            for _, target in self.successors[node_id]:
                if color[target] == grey:
                    self.back_edges.add((node_id, target))
                    cycles.append(path[path.index(target) :])
                elif color[target] == white:
                    visit(target)
            path.pop()
            color[node_id] = black

        for node_id in ordered:
            if color.get(node_id) == white:
                visit(node_id)
        return cycles

    def dag_predecessors(self, node_id: str) -> list[str]:
        """Predecessors of a node, ignoring edges that close a cycle."""
        return [
            pred for pred in self.predecessors[node_id] if (pred, node_id) not in self.back_edges
        ]

    def dag_successors(self, node_id: str) -> list[str]:
        """Successors of a node, ignoring edges that close a cycle."""
        return [
            target
            for _, target in self.successors[node_id]
            if (node_id, target) not in self.back_edges
        ]

    def check_termination(self, reachable: set[str]) -> None:
        trigger_id = self.graph.trigger.id
        for node_id in self.nodes:
            if node_id not in reachable:
                continue
            handles = self.handles_of(node_id)
            if handles is None:
                continue
            connected = {handle for handle, _ in self.successors[node_id]}
            if node_id == trigger_id:
                if "next" not in connected:
                    self.error(
                        ValidationCode.TRIGGER_NOT_CONNECTED,
                        "Connect the trigger to the first step.",
                        node_id,
                    )
                continue
            if self.classes[node_id].is_terminal:
                continue
            for handle in handles:
                if handle not in connected:
                    self.error(
                        ValidationCode.UNCONNECTED_HANDLE,
                        f"Output '{handle}' is not connected. Every path must end in 'stop' or "
                        "a terminal action.",
                        node_id,
                        handle=handle,
                    )

    # --- path rules (on the graph without cycle-closing edges) --------------------------------

    def topological_order(self, reachable: set[str]) -> list[str]:
        indegree = {
            node_id: sum(1 for pred in self.dag_predecessors(node_id) if pred in reachable)
            for node_id in reachable
        }
        ready = [self.graph.trigger.id]
        order: list[str] = []
        while ready:
            node_id = ready.pop()
            order.append(node_id)
            for target in self.dag_successors(node_id):
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        return order

    def check_sends(self, order: list[str]) -> None:
        # Longest-path send count, and the set of "since last send" states reachable per node:
        # "none" (no send yet), "waited" (sent, then waited) or "pending" (sent, no wait since).
        sends_out: dict[str, int] = {}
        states_out: dict[str, set[str]] = {}
        for node_id in order:
            predecessors = [pred for pred in self.dag_predecessors(node_id) if pred in sends_out]
            sends_in = max((sends_out[pred] for pred in predecessors), default=0)
            states_in: set[str] = set().union(*(states_out[pred] for pred in predecessors))
            states_in = states_in or {"none"}
            node_class = self.classes.get(node_id)
            is_send = bool(node_class and node_class.is_cold_send)
            is_wait = bool(node_class and node_class.is_wait)

            if is_send:
                sends_out[node_id] = sends_in + 1
                if sends_out[node_id] > self.max_cold_emails:
                    self.error(
                        ValidationCode.TOO_MANY_EMAILS,
                        f"This path sends {sends_out[node_id]} cold emails; the maximum per "
                        f"sequence is {self.max_cold_emails}.",
                        node_id,
                    )
                if "pending" in states_in:
                    self.error(
                        ValidationCode.SEND_WITHOUT_WAIT,
                        "Add a wait between the previous email and this one.",
                        node_id,
                    )
                states_out[node_id] = {"pending"}
            else:
                sends_out[node_id] = sends_in
                states_out[node_id] = (
                    {"waited" if state == "pending" else state for state in states_in}
                    if is_wait
                    else states_in
                )

    def check_context_warnings(self, order: list[str]) -> None:
        # A path "has reply context" once a reply has been classified on it (reply trigger or an
        # ai_classify_reply node); likewise for shipment extraction and lead.* fields.
        has_reply: dict[str, bool] = {}
        has_lead: dict[str, bool] = {}
        for node_id in order:
            node = self.nodes[node_id]
            predecessors = [pred for pred in self.dag_predecessors(node_id) if pred in has_reply]
            reply_in = all(has_reply[pred] for pred in predecessors) if predecessors else False
            lead_in = all(has_lead[pred] for pred in predecessors) if predecessors else False
            scopes = {field.split(".", 1)[0] for field in self._condition_fields(node_id)}

            # The reply trigger's own filter may use reply.* fields.
            reply_available = reply_in or node.type == REPLY_TRIGGER
            uses_reply = "reply" in scopes or node.type == "branch_on_reply_bucket"
            if uses_reply and not reply_available:
                self.warn(
                    ValidationCode.REPLY_FIELDS_WITHOUT_REPLY,
                    "Uses the reply, but no reply is classified on every path to this node "
                    f"(use '{REPLY_TRIGGER}' or add '{CLASSIFY_NODE}' before it).",
                    node_id,
                )
            if ("lead" in scopes or node.type == "create_pricing_lead") and not lead_in:
                self.warn(
                    ValidationCode.LEAD_FIELDS_WITHOUT_EXTRACTION,
                    f"No '{EXTRACT_NODE}' runs before this node on every path, so shipment "
                    "details may be empty.",
                    node_id,
                )
            has_reply[node_id] = reply_in or node.type in {REPLY_TRIGGER, CLASSIFY_NODE}
            has_lead[node_id] = lead_in or node.type == EXTRACT_NODE

    def _condition_fields(self, node_id: str) -> Iterable[str]:
        condition = getattr(self.configs.get(node_id), "condition", None)
        if isinstance(condition, Comparison | AllCondition | AnyCondition | NotCondition):
            return iter_condition_fields(condition)
        return []


def validate_workflow_graph(
    graph: WorkflowGraph, *, registry: NodeRegistry, max_cold_emails: int
) -> ValidationReport:
    """Check a workflow graph against every publishing rule.

    Args:
        graph: The graph to check (already shape-validated).
        registry: Node types the workflow may use.
        max_cold_emails: `MAX_COLD_EMAILS_PER_SEQUENCE`.

    Returns:
        Errors and warnings, each naming the node concerned.
    """
    checker = _Checker(graph, registry, max_cold_emails)
    checker.check_nodes()
    checker.check_edges()
    reachable = checker.reachable()
    checker.check_orphans(reachable)
    for cycle in checker.find_cycles():
        checker.error(
            ValidationCode.CYCLE_DETECTED,
            "Workflows cannot loop back (cycle: " + " → ".join([*cycle, cycle[0]]) + ").",
            cycle[0],
        )
    checker.check_termination(reachable)
    order = checker.topological_order(reachable)
    checker.check_sends(order)
    checker.check_context_warnings(order)
    return checker.report


__all__ = [
    "ValidationCode",
    "ValidationIssue",
    "ValidationReport",
    "validate_workflow_graph",
]
