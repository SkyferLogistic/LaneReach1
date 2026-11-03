/**
 * Workflow graph <-> canvas conversion.
 *
 * The backend stores `{trigger, nodes, edges}` with optional canvas positions; the engine
 * ignores positions. Graphs created through the API or the seed have none, so the canvas lays
 * them out in layers (breadth-first from the trigger).
 */
import type { Edge, Node } from "@xyflow/react";
import type { Schemas } from "../api/client";

export type WorkflowGraph = Schemas["WorkflowGraph"];
export type GraphNode = Schemas["GraphNode"];
export type NodeType = Schemas["NodeTypeResponse"];

export interface FlowNodeData extends Record<string, unknown> {
  nodeType: string;
  config: Record<string, unknown>;
  isTrigger: boolean;
}

export type FlowNode = Node<FlowNodeData, "workflow">;

const COLUMN_GAP = 280;
const ROW_GAP = 150;

/** Output handles a node offers, given its config (some nodes derive them from config). */
export function handlesFor(type: NodeType | undefined, config: Record<string, unknown>): string[] {
  if (!type) return ["next"];
  if (type.node_type === "branch_on_reply_bucket") {
    const buckets = Array.isArray(config.buckets) ? (config.buckets as string[]) : [];
    return [...buckets, "other"];
  }
  if (type.node_type === "switch_on_field") {
    const cases = Array.isArray(config.cases)
      ? (config.cases as { handle?: string }[]).map((item) => item.handle ?? "").filter(Boolean)
      : [];
    return [...cases, "other"];
  }
  if (type.node_type === "ab_split") {
    const variants = Array.isArray(config.variants)
      ? (config.variants as { name?: string }[]).map((variant) => variant.name ?? "").filter(Boolean)
      : [];
    return variants;
  }
  return type.output_handles;
}

/** Breadth-first layering from the trigger; unreachable nodes go to a last column. */
export function layoutGraph(graph: WorkflowGraph): Map<string, { x: number; y: number }> {
  const children = new Map<string, string[]>();
  for (const edge of graph.edges ?? []) {
    const list = children.get(edge.source) ?? [];
    list.push(edge.target);
    children.set(edge.source, list);
  }
  const depth = new Map<string, number>([[graph.trigger.id, 0]]);
  const queue = [graph.trigger.id];
  while (queue.length) {
    const id = queue.shift() as string;
    for (const child of children.get(id) ?? []) {
      if (!depth.has(child)) {
        depth.set(child, (depth.get(id) ?? 0) + 1);
        queue.push(child);
      }
    }
  }
  const maxDepth = Math.max(0, ...depth.values());
  const rows = new Map<number, number>();
  const positions = new Map<string, { x: number; y: number }>();
  for (const node of [graph.trigger, ...(graph.nodes ?? [])]) {
    const level = depth.get(node.id) ?? maxDepth + 1;
    const row = rows.get(level) ?? 0;
    rows.set(level, row + 1);
    positions.set(node.id, { x: row * COLUMN_GAP, y: level * ROW_GAP });
  }
  // Centre each layer under the trigger.
  for (const [id, position] of positions) {
    const level = depth.get(id) ?? maxDepth + 1;
    const count = rows.get(level) ?? 1;
    position.x -= ((count - 1) * COLUMN_GAP) / 2;
  }
  return positions;
}

export function graphToFlow(graph: WorkflowGraph): { nodes: FlowNode[]; edges: Edge[] } {
  const needsLayout = [graph.trigger, ...(graph.nodes ?? [])].some((node) => !node.position);
  const layout = needsLayout ? layoutGraph(graph) : null;
  const toFlow = (node: GraphNode, isTrigger: boolean): FlowNode => ({
    id: node.id,
    type: "workflow",
    position: (layout?.get(node.id) ?? node.position ?? { x: 0, y: 0 }) as { x: number; y: number },
    data: { nodeType: node.type, config: (node.config ?? {}) as Record<string, unknown>, isTrigger },
    deletable: !isTrigger,
  });
  return {
    nodes: [toFlow(graph.trigger, true), ...(graph.nodes ?? []).map((node) => toFlow(node, false))],
    edges: (graph.edges ?? []).map((edge) => toFlowEdge(edge.source, edge.target, edge.handle ?? "next")),
  };
}

export function toFlowEdge(source: string, target: string, handle: string): Edge {
  return {
    id: `${source}:${handle}->${target}`,
    source,
    target,
    sourceHandle: handle,
    type: "insertable",
  };
}

export function flowToGraph(nodes: FlowNode[], edges: Edge[]): WorkflowGraph {
  const trigger = nodes.find((node) => node.data.isTrigger);
  if (!trigger) throw new Error("A workflow needs a trigger.");
  const toGraph = (node: FlowNode): GraphNode => ({
    id: node.id,
    type: node.data.nodeType,
    config: node.data.config,
    position: { x: Math.round(node.position.x), y: Math.round(node.position.y) },
  });
  return {
    trigger: toGraph(trigger),
    nodes: nodes.filter((node) => !node.data.isTrigger).map(toGraph),
    edges: edges.map((edge) => ({
      source: edge.source,
      target: edge.target,
      handle: edge.sourceHandle ?? "next",
    })),
  };
}

/** A readable, unique node ID such as `send_email_2`. */
export function nextNodeId(nodeType: string, existing: Iterable<string>): string {
  const taken = new Set(existing);
  const base = nodeType.replace(/^trigger_/, "").slice(0, 50);
  for (let index = 1; ; index += 1) {
    const candidate = `${base}_${index}`;
    if (!taken.has(candidate)) return candidate;
  }
}

/** Initial config from the schema's defaults (required fields stay empty until filled in). */
export function defaultConfig(schema: Record<string, unknown>): Record<string, unknown> {
  const properties = (schema.properties ?? {}) as Record<string, { default?: unknown }>;
  const config: Record<string, unknown> = {};
  for (const [name, property] of Object.entries(properties)) {
    if (property.default !== undefined && property.default !== null) config[name] = property.default;
  }
  return config;
}

/** One-line summary of a node's config shown on the canvas. */
export function summarizeConfig(
  nodeType: string,
  config: Record<string, unknown>,
  names: { offices: Map<string, string>; workflows?: Map<string, string> },
): string {
  const parts: string[] = [];
  const office = typeof config.office_id === "string" ? names.offices.get(config.office_id) : null;
  if (office) parts.push(office);
  if (config.template_key) parts.push(`template ${String(config.template_key)}`);
  if (config.days) parts.push(`${String(config.days)} business days`);
  if (config.tag) parts.push(`tag “${String(config.tag)}”`);
  if (Array.isArray(config.buckets)) parts.push((config.buckets as string[]).join(", "));
  if (config.stage) parts.push(`→ ${String(config.stage)}`);
  if (config.priority) parts.push(`${String(config.priority)} priority`);
  if (config.use_ai_draft) parts.push("AI draft");
  if (config.condition) parts.push("condition set");
  if (config.target_language) parts.push(`→ ${String(config.target_language)}`);
  if (config.channel) parts.push(String(config.channel));
  if (Array.isArray(config.statuses)) parts.push(`lead ${(config.statuses as string[]).join(" / ")}`);
  if (config.event_name) parts.push(`event “${String(config.event_name)}”`);
  if (config.service_line && config.service_line !== "any") parts.push(`${String(config.service_line)} agents`);
  if (nodeType === "switch_on_field" && config.field) {
    const count = Array.isArray(config.cases) ? config.cases.length : 0;
    parts.push(`${String(config.field)} · ${count} case${count === 1 ? "" : "s"}`);
  }
  if (nodeType === "enroll_in_workflow" && typeof config.workflow_id === "string") {
    parts.push(names.workflows?.get(config.workflow_id) ?? "another workflow");
  }
  if (nodeType === "ab_split" && Array.isArray(config.variants)) {
    parts.push(
      (config.variants as { name?: string; percentage?: number }[])
        .map((variant) => `${variant.name ?? "?"} ${variant.percentage ?? 0}%`)
        .join(" / "),
    );
  }
  return parts.join(" · ");
}

/** Colour family of an output handle: green for the "yes" side, red for the "no" side. */
export function handleTone(handle: string): "good" | "bad" | "neutral" {
  if (["true", "valid", "yes", "clicked", "opened", "won"].includes(handle)) return "good";
  if (["false", "invalid", "no", "no_engagement", "lost", "unknown"].includes(handle)) return "bad";
  return "neutral";
}

/** Readable label for an output handle (`next` has none). */
export function handleLabel(handle: string): string {
  return handle === "next" ? "" : handle.replace(/_/g, " ");
}

/** The first output of `source` with nothing connected, or null when all are used. */
export function firstFreeHandle(handles: string[], source: string, edges: Edge[]): string | null {
  const used = new Set(edges.filter((edge) => edge.source === source).map((edge) => edge.sourceHandle ?? "next"));
  return handles.find((handle) => !used.has(handle)) ?? null;
}

/** Whether an edge source -> target would close a loop (the engine refuses cycles). */
export function wouldCreateCycle(edges: Edge[], source: string, target: string): boolean {
  if (source === target) return true;
  const children = new Map<string, string[]>();
  for (const edge of edges) children.set(edge.source, [...(children.get(edge.source) ?? []), edge.target]);
  const seen = new Set<string>();
  const stack = [target];
  while (stack.length) {
    const id = stack.pop() as string;
    if (id === source) return true;
    if (seen.has(id)) continue;
    seen.add(id);
    stack.push(...(children.get(id) ?? []));
  }
  return false;
}

/** Why a connection is refused, or null when it is fine. */
export function connectionProblem(
  edges: Edge[],
  source: string,
  target: string,
  triggerId: string | undefined,
): string | null {
  if (source === target) return "A step cannot connect to itself.";
  if (target === triggerId) return "Nothing can connect into the trigger: it is where the workflow starts.";
  if (wouldCreateCycle(edges, source, target)) return "That would make a loop. Workflows run top to bottom, so loops are not allowed.";
  return null;
}

/** Connect `source`'s output to `target`; an output has one edge, so an old one is replaced. */
export function connectHandle(
  edges: Edge[],
  source: string,
  target: string,
  handle: string,
): { edges: Edge[]; replaced: Edge | null } {
  const replaced = edges.find((edge) => edge.source === source && (edge.sourceHandle ?? "next") === handle) ?? null;
  return {
    edges: [...edges.filter((edge) => edge !== replaced), toFlowEdge(source, target, handle)],
    replaced: replaced && replaced.target !== target ? replaced : null,
  };
}

/** Put a new node on an existing edge: source -> new (same output) -> old target (new's output). */
export function insertBetween(edges: Edge[], edge: Edge, newId: string, newHandle: string | null): Edge[] {
  const rest = edges.filter((item) => item.id !== edge.id);
  const into = toFlowEdge(edge.source, newId, edge.sourceHandle ?? "next");
  return newHandle ? [...rest, into, toFlowEdge(newId, edge.target, newHandle)] : [...rest, into];
}

/** Move `start` and everything below it down by `dy` (makes room for an inserted step). */
export function shiftDescendants(nodes: FlowNode[], edges: Edge[], start: string, dy: number): FlowNode[] {
  const below = new Set<string>();
  const stack = [start];
  while (stack.length) {
    const id = stack.pop() as string;
    if (below.has(id)) continue;
    below.add(id);
    for (const edge of edges) if (edge.source === id) stack.push(edge.target);
  }
  return nodes.map((node) => (below.has(node.id) ? { ...node, position: { x: node.position.x, y: node.position.y + dy } } : node));
}

/** Where a node added under output `index` of `count` goes, relative to its parent. */
export function childPosition(parent: { x: number; y: number }, index: number, count: number): { x: number; y: number } {
  return { x: parent.x + (index - (count - 1) / 2) * 260, y: parent.y + 170 };
}

export interface History<T> {
  push(state: T): void;
  undo(current: T): T | null;
  redo(current: T): T | null;
  canUndo(): boolean;
  canRedo(): boolean;
}

/** Undo/redo stacks of editor snapshots (a new change clears redo). */
export function createHistory<T>(limit = 50): History<T> {
  const past: T[] = [];
  let future: T[] = [];
  return {
    push(state) {
      past.push(state);
      if (past.length > limit) past.shift();
      future = [];
    },
    undo(current) {
      const previous = past.pop();
      if (previous === undefined) return null;
      future.push(current);
      return previous;
    },
    redo(current) {
      const next = future.pop();
      if (next === undefined) return null;
      past.push(current);
      return next;
    },
    canUndo: () => past.length > 0,
    canRedo: () => future.length > 0,
  };
}

/** The nearest spot at or right of `wanted` where a new step does not cover another one. */
export function findFreeSpot(wanted: { x: number; y: number }, nodes: FlowNode[], ignore?: string): { x: number; y: number } {
  const clashes = (x: number, y: number) =>
    nodes.some((node) => node.id !== ignore && Math.abs(node.position.x - x) < 240 && Math.abs(node.position.y - y) < 110);
  for (let step = 0; step < 20; step += 1) {
    // Try right, then left, alternating further out.
    const offset = (step % 2 === 1 ? 1 : -1) * Math.ceil(step / 2) * 260;
    if (!clashes(wanted.x + offset, wanted.y)) return { x: wanted.x + offset, y: wanted.y };
  }
  return { x: wanted.x, y: wanted.y + 130 };
}
