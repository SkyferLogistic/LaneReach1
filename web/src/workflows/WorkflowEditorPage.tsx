/**
 * Workflow editor: canvas (React Flow), palette, inspector with schema forms, live
 * validation, save (new version), publish, pause/resume, versions and test-run.
 */
import "@xyflow/react/dist/style.css";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import type { Connection, Edge, EdgeTypes, FinalConnectionState, NodeTypes } from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent } from "react";
import { Link, useBlocker, useNavigate, useParams } from "react-router-dom";
import { ApiError, api, describeError, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys, useNodeCatalog, useOffices } from "../api/queries";
import { useSession } from "../auth/session";
import { Badge, Button, Drawer, ErrorAlert, Loading, StatusBadge, useToast } from "../components/ui";
import { formatDateTime } from "../lib/format";
import type { FlowNode, WorkflowGraph } from "./graph";
import {
  childPosition,
  connectHandle,
  connectionProblem,
  createHistory,
  defaultConfig,
  findFreeSpot,
  firstFreeHandle,
  flowToGraph,
  graphToFlow,
  handlesFor,
  insertBetween,
  layoutGraph,
  nextNodeId,
  shiftDescendants,
  toFlowEdge,
} from "./graph";
import { EdgeContext, InsertableEdge, NodePicker } from "./CanvasParts";
import { Inspector } from "./Inspector";
import { DRAG_MIME, Palette } from "./Palette";
import { Icon } from "../components/icons";
import { stepCountMap } from "../lib/campaigns";
import { ResultsDrawer, useCampaignStats } from "./CampaignResults";
import { useConfirm, useDelete, usePermission } from "../components/confirm";
import { TestRunDrawer } from "./TestRunDrawer";
import { CanvasContext, WorkflowNodeView } from "./WorkflowNodeView";

const nodeTypes: NodeTypes = { workflow: WorkflowNodeView };
const edgeTypes: EdgeTypes = { insertable: InsertableEdge };

type Snapshot = { nodes: FlowNode[]; edges: Edge[] };
/** Where the step picker is and what the picked step connects to. */
type PickerTarget =
  | { kind: "after"; source: string; handle: string; screen: { x: number; y: number }; flow?: { x: number; y: number } }
  | { kind: "insert"; edgeId: string; screen: { x: number; y: number } };

type ValidationReport = Schemas["ValidationReport"];

export function WorkflowEditorPage() {
  const { workflowId = "" } = useParams();
  const workflow = useQuery({
    queryKey: keys.workflow(workflowId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/automation/workflows/{workflow_id}", {
          params: { path: { workflow_id: workflowId } },
        }),
      ),
  });
  const catalog = useNodeCatalog();
  if (workflow.isLoading || catalog.isLoading) return <Loading />;
  if (workflow.error || !workflow.data || !catalog.data) {
    return (
      <div className="content">
        <ErrorAlert error={workflow.error ?? catalog.error} />
      </div>
    );
  }
  return (
    <ReactFlowProvider>
      <Editor key={`${workflow.data.id}:${workflow.data.latest_version}`} workflow={workflow.data} catalog={catalog.data} />
    </ReactFlowProvider>
  );
}

function Editor({
  workflow,
  catalog,
}: {
  workflow: Schemas["WorkflowResponse"];
  catalog: Schemas["NodeTypeResponse"][];
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const { can } = useSession();
  const offices = useOffices();
  const flow = useReactFlow();
  const initial = useMemo(() => graphToFlow(workflow.latest.graph), [workflow.latest.graph]);
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>(initial.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initial.edges);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [name, setName] = useState(workflow.name);
  const [dirty, setDirty] = useState(false);
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [validating, setValidating] = useState(false);
  const [drawer, setDrawer] = useState<"test" | "versions" | "results" | null>(null);
  const stats = useCampaignStats(workflow.id, workflow.current_version !== null);
  const stepCounts = useMemo(() => stepCountMap(stats.data?.steps), [stats.data]);
  const [problem, setProblem] = useState<unknown>(null);
  const wrapper = useRef<HTMLDivElement>(null);
  const [picker, setPicker] = useState<PickerTarget | null>(null);
  const history = useRef(createHistory<Snapshot>(50));
  const [, setHistoryVersion] = useState(0);
  const current = useRef<Snapshot>({ nodes: initial.nodes, edges: initial.edges });
  current.current = { nodes, edges };
  const lastSnapshotAt = useRef(0);
  /** Remember the graph before a change (coalesced for rapid edits such as typing). */
  const snapshot = useCallback((coalesce = false) => {
    const now = Date.now();
    if (coalesce && now - lastSnapshotAt.current < 1500) return;
    lastSnapshotAt.current = now;
    history.current.push(current.current);
    setHistoryVersion((value) => value + 1);
  }, []);

  const readOnly = workflow.status === "archived" || !can("workflows:write");
  const catalogMap = useMemo(() => new Map(catalog.map((type) => [type.node_type, type])), [catalog]);
  const workflowList = useQuery({
    queryKey: [...keys.workflows, "picker"],
    queryFn: () => unwrap(api.GET("/api/v1/automation/workflows", { params: { query: { limit: 100 } } })),
  });
  const workflowNames = useMemo(
    () => new Map((workflowList.data?.items ?? []).map((item) => [item.id, item.name])),
    [workflowList.data],
  );
  const officeNames = useMemo(
    () => new Map((offices.data ?? []).map((office) => [office.id, office.name])),
    [offices.data],
  );
  const nodesWithErrors = useMemo(
    () => new Set((report?.errors ?? []).map((issue) => issue.node_id).filter(Boolean) as string[]),
    [report],
  );
  const graph = useMemo(() => flowToGraph(nodes, edges), [nodes, edges]);
  const selected = nodes.find((node) => node.id === selectedId) ?? null;

  // Warn before leaving with unsaved changes.
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    if (blocker.state === "blocked") {
      if (window.confirm("You have unsaved changes. Leave without saving?")) blocker.proceed();
      else blocker.reset();
    }
  }, [blocker]);

  // Live validation, debounced, on the graph as currently drawn.
  const graphKey = JSON.stringify({ trigger: graph.trigger, nodes: (graph.nodes ?? []).map(({ position: _p, ...rest }) => rest), edges: graph.edges });
  useEffect(() => {
    let cancelled = false;
    setValidating(true);
    const timer = window.setTimeout(async () => {
      try {
        const result = await unwrap(
          api.POST("/api/v1/automation/workflows/validate-graph", { body: { graph } }),
        );
        if (!cancelled) setReport(result);
      } catch (error) {
        if (!cancelled) setProblem(error);
      } finally {
        if (!cancelled) setValidating(false);
      }
    }, 500);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // graphKey captures every semantic change; positions alone do not need re-validation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphKey]);

  const markDirty = useCallback(() => setDirty(true), []);

  const updateNode = useCallback(
    (id: string, update: (node: FlowNode) => FlowNode) => {
      snapshot(true);
      setNodes((items) => items.map((node) => (node.id === id ? update(node) : node)));
      markDirty();
    },
    [setNodes, markDirty, snapshot],
  );

  const triggerId = nodes.find((node) => node.data.isTrigger)?.id;
  const outputsOf = useCallback(
    (node: FlowNode) => handlesFor(catalogMap.get(node.data.nodeType), node.data.config),
    [catalogMap],
  );

  /** Put a new step on an edge (source -> new -> old target), making room below. */
  const insertOnEdge = useCallback(
    (nodeType: string, edge: Edge) => {
      const type = catalogMap.get(nodeType);
      if (!type) return null;
      snapshot();
      const source = nodes.find((node) => node.id === edge.source);
      const targetNode = nodes.find((node) => node.id === edge.target);
      const id = nextNodeId(nodeType, nodes.map((node) => node.id));
      const outputs = handlesFor(type, defaultConfig(type.config_schema));
      const node: FlowNode = {
        id,
        type: "workflow",
        position: {
          x: targetNode?.position.x ?? source?.position.x ?? 0,
          y: targetNode ? targetNode.position.y : (source?.position.y ?? 0) + 170,
        },
        data: { nodeType, config: defaultConfig(type.config_schema), isTrigger: false },
      };
      // Make room: the old target and everything below it move down one row.
      setNodes([...shiftDescendants(nodes, edges, edge.target, 170).map((item) => ({ ...item, selected: false })), { ...node, selected: true }]);
      setEdges(insertBetween(edges, edge, id, outputs[0] ?? null));
      setSelectedId(id);
      markDirty();
      return id;
    },
    [catalogMap, nodes, edges, setNodes, setEdges, markDirty, snapshot],
  );

  /** Add a step; with `from` it is connected to that output (replacing nothing: outputs are free). */
  const addNode = useCallback(
    (nodeType: string, options: { position?: { x: number; y: number }; from?: { source: string; handle: string } } = {}) => {
      const type = catalogMap.get(nodeType);
      if (!type) return null;
      let from = options.from ?? null;
      if (!from && !options.position) {
        // Palette click: after the selected step, else after the last step with a free output.
        const anchors = selected ? [selected] : [...nodes].reverse();
        for (const anchor of anchors) {
          const handle = firstFreeHandle(outputsOf(anchor), anchor.id, edges);
          if (handle) {
            from = { source: anchor.id, handle };
            break;
          }
        }
        if (!from) {
          // Every output is used: insert after the selected step, or just before the final stop.
          const edge = selected
            ? edges.find((item) => item.source === selected.id)
            : [...edges].reverse().find((item) => nodes.find((node) => node.id === item.target)?.data.nodeType === "stop");
          if (edge) return insertOnEdge(nodeType, edge);
        }
      }
      snapshot();
      const id = nextNodeId(nodeType, nodes.map((node) => node.id));
      const parent = from ? nodes.find((node) => node.id === from?.source) : undefined;
      const outputs = parent ? outputsOf(parent) : [];
      const position = findFreeSpot(
        options.position ??
          (parent ? childPosition(parent.position, Math.max(0, outputs.indexOf(from?.handle ?? "next")), Math.max(1, outputs.length)) : { x: 0, y: 150 }),
        nodes,
      );
      const node: FlowNode = {
        id,
        type: "workflow",
        position,
        data: { nodeType, config: defaultConfig(type.config_schema), isTrigger: false },
      };
      setNodes((items) => [...items.map((item) => ({ ...item, selected: false })), { ...node, selected: true }]);
      if (from) setEdges((items) => connectHandle(items, from.source, id, from.handle).edges);
      setSelectedId(id);
      markDirty();
      return id;
    },
    [catalogMap, nodes, edges, selected, outputsOf, setNodes, setEdges, markDirty, snapshot, insertOnEdge],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      if (!connection.source || !connection.target) return;
      const reason = connectionProblem(edges, connection.source, connection.target, triggerId);
      if (reason) {
        toast(reason, "error");
        return;
      }
      snapshot();
      const handle = connection.sourceHandle ?? "next";
      // One edge per output: connecting a used output moves its edge (undo brings it back).
      const result = connectHandle(edges, connection.source, connection.target, handle);
      setEdges(result.edges);
      if (result.replaced) toast(`Moved the “${handle}” connection of ${connection.source}. Press Ctrl+Z to undo.`);
      markDirty();
    },
    [edges, triggerId, setEdges, markDirty, snapshot, toast],
  );

  const onConnectEnd = useCallback(
    (event: MouseEvent | TouchEvent, state: FinalConnectionState) => {
      if (readOnly || state.isValid || !state.fromNode || state.fromHandle?.type !== "source") return;
      if (state.toNode) {
        const reason = connectionProblem(edges, state.fromNode.id, state.toNode.id, triggerId);
        if (reason) toast(reason, "error");
        return;
      }
      // Dropped on empty canvas: offer the step picker right there.
      const point = "changedTouches" in event ? event.changedTouches[0] : event;
      if (!point) return;
      setPicker({
        kind: "after",
        source: state.fromNode.id,
        handle: state.fromHandle.id ?? "next",
        screen: { x: point.clientX, y: point.clientY },
        flow: flow.screenToFlowPosition({ x: point.clientX, y: point.clientY }),
      });
    },
    [readOnly, edges, triggerId, flow, toast],
  );

  const onReconnect = useCallback(
    (oldEdge: Edge, connection: Connection) => {
      if (!connection.source || !connection.target) return;
      const others = edges.filter((edge) => edge.id !== oldEdge.id);
      const reason = connectionProblem(others, connection.source, connection.target, triggerId);
      if (reason) {
        toast(reason, "error");
        return;
      }
      snapshot();
      setEdges(connectHandle(others, connection.source, connection.target, connection.sourceHandle ?? "next").edges);
      markDirty();
    },
    [edges, triggerId, setEdges, markDirty, snapshot, toast],
  );

  const pickStep = useCallback(
    (nodeType: string) => {
      const target = picker;
      setPicker(null);
      if (!target) return;
      if (target.kind === "after") {
        addNode(nodeType, {
          from: { source: target.source, handle: target.handle },
          position: target.flow ? { x: target.flow.x - 110, y: target.flow.y } : undefined,
        });
        return;
      }
      const edge = edges.find((item) => item.id === target.edgeId);
      if (edge) insertOnEdge(nodeType, edge);
    },
    [picker, addNode, edges, insertOnEdge],
  );

  const undo = useCallback(() => {
    const previous = history.current.undo(current.current);
    if (!previous) return;
    setNodes(previous.nodes);
    setEdges(previous.edges);
    setHistoryVersion((value) => value + 1);
    markDirty();
  }, [setNodes, setEdges, markDirty]);

  const redo = useCallback(() => {
    const next = history.current.redo(current.current);
    if (!next) return;
    setNodes(next.nodes);
    setEdges(next.edges);
    setHistoryVersion((value) => value + 1);
    markDirty();
  }, [setNodes, setEdges, markDirty]);

  useEffect(() => {
    if (readOnly) return undefined;
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && (target.closest("input, textarea, select, [contenteditable=true]") || target.isContentEditable)) return;
      if (!(event.ctrlKey || event.metaKey)) return;
      const key = event.key.toLowerCase();
      if (key === "z" && !event.shiftKey) {
        event.preventDefault();
        undo();
      } else if ((key === "z" && event.shiftKey) || key === "y") {
        event.preventDefault();
        redo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [readOnly, undo, redo]);

  const tidyUp = useCallback(() => {
    snapshot();
    const positions = layoutGraph(flowToGraph(nodes, edges));
    setNodes((items) => items.map((node) => ({ ...node, position: positions.get(node.id) ?? node.position })));
    markDirty();
    window.setTimeout(() => void flow.fitView({ padding: 0.25, duration: 300 }), 50);
  }, [nodes, edges, setNodes, markDirty, snapshot, flow]);

  const usedHandles = useMemo(() => new Set(edges.map((edge) => `${edge.source}:${edge.sourceHandle ?? "next"}`)), [edges]);
  const edgeActions = useMemo(
    () => ({
      readOnly,
      onInsert: (edgeId: string, screen: { x: number; y: number }) => setPicker({ kind: "insert", edgeId, screen }),
      onRemove: (edgeId: string) => {
        snapshot();
        setEdges((items) => items.filter((edge) => edge.id !== edgeId));
        markDirty();
      },
    }),
    [readOnly, setEdges, markDirty, snapshot],
  );

  const onDrop = useCallback(
    (event: DragEvent) => {
      event.preventDefault();
      const nodeType = event.dataTransfer.getData(DRAG_MIME);
      if (!nodeType || readOnly) return;
      addNode(nodeType, { position: flow.screenToFlowPosition({ x: event.clientX, y: event.clientY }) });
    },
    [addNode, flow, readOnly],
  );

  const renameNode = useCallback(
    (id: string, next: string) => {
      if (nodes.some((node) => node.id === next)) {
        toast(`A node called “${next}” already exists.`, "error");
        return;
      }
      snapshot();
      setNodes((items) => items.map((node) => (node.id === id ? { ...node, id: next } : node)));
      setEdges((items) =>
        items.map((edge) => {
          const source = edge.source === id ? next : edge.source;
          const target = edge.target === id ? next : edge.target;
          return toFlowEdge(source, target, edge.sourceHandle ?? "next");
        }),
      );
      setSelectedId(next);
      markDirty();
    },
    [nodes, setNodes, setEdges, markDirty, toast, snapshot],
  );

  const deleteNode = useCallback(
    (id: string) => {
      snapshot();
      setNodes((items) => items.filter((node) => node.id !== id));
      setEdges((items) => items.filter((edge) => edge.source !== id && edge.target !== id));
      setSelectedId(null);
      markDirty();
    },
    [setNodes, setEdges, markDirty, snapshot],
  );

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: keys.workflows });
  };

  const save = useMutation({
    mutationFn: (currentGraph: WorkflowGraph) =>
      unwrap(
        api.PUT("/api/v1/automation/workflows/{workflow_id}", {
          params: { path: { workflow_id: workflow.id } },
          body: { name, description: workflow.description, graph: currentGraph, base_version: workflow.latest_version },
        }),
      ),
  });

  const publish = useMutation({
    mutationFn: async () => {
      let version = workflow.latest_version;
      if (dirty) version = (await save.mutateAsync(graph)).latest_version;
      return unwrap(
        api.POST("/api/v1/automation/workflows/{workflow_id}/publish", {
          params: { path: { workflow_id: workflow.id } },
          body: { version },
        }),
      );
    },
    onSuccess: (published) => {
      setDirty(false);
      toast(`Published version ${published.current_version}. New enrollments use it.`);
      invalidate();
    },
    onError: (error) => setProblem(error),
  });

  const navigateTo = useNavigate();
  const [confirmDialog, ask] = useConfirm();
  const deletePermission = usePermission("workflows:publish", "admins and campaign managers");
  const removeWorkflow = useDelete({
    run: () => unwrap(api.DELETE("/api/v1/automation/workflows/{workflow_id}", { params: { path: { workflow_id: workflow.id } } })),
    success: `"${workflow.name}" deleted.`,
    invalidate: [keys.workflows],
    onDone: () => navigateTo("/workflows"),
  });
  const lifecycle = useMutation({
    mutationFn: (action: "pause" | "resume" | "archive") => {
      const options = { params: { path: { workflow_id: workflow.id } } };
      if (action === "pause") return unwrap(api.POST("/api/v1/automation/workflows/{workflow_id}/pause", options));
      if (action === "resume") return unwrap(api.POST("/api/v1/automation/workflows/{workflow_id}/resume", options));
      return unwrap(api.POST("/api/v1/automation/workflows/{workflow_id}/archive", options));
    },
    onSuccess: (_, action) => {
      toast(action === "pause" ? "Workflow paused." : action === "resume" ? "Workflow resumed." : "Workflow archived.");
      invalidate();
    },
    onError: (error) => setProblem(error),
  });

  const onSave = async () => {
    setProblem(null);
    try {
      const saved = await save.mutateAsync(graph);
      setDirty(false);
      toast(`Saved version ${saved.latest_version}.`);
      invalidate();
    } catch (error) {
      setProblem(error);
      if (error instanceof ApiError && error.status === 409) {
        toast("Someone else saved a newer version. Reload to see it.", "error");
      }
    }
  };

  const publishErrors =
    problem instanceof ApiError && problem.problem.errors?.length ? problem.problem.errors : null;

  return (
    <div className="content flush">
      <div className="editor-toolbar">
        <Link to="/workflows" className="btn ghost sm">
          ← Workflows
        </Link>
        <input
          className="input"
          style={{ width: 280, fontWeight: 600 }}
          value={name}
          disabled={readOnly}
          onChange={(event) => {
            setName(event.target.value);
            markDirty();
          }}
          aria-label="Workflow name"
        />
        <StatusBadge value={workflow.status} />
        {workflow.is_paused && <Badge tone="amber">paused</Badge>}
        <span className="small muted">
          v{workflow.latest_version}
          {workflow.current_version ? ` · live v${workflow.current_version}` : " · not published"}
          {dirty ? " · unsaved changes" : ""}
        </span>
        <span className="spacer" />
        {!readOnly && (
          <span className="row nowrap" style={{ gap: 4 }}>
            <Button size="sm" variant="ghost" title="Undo (Ctrl+Z)" aria-label="Undo" disabled={!history.current.canUndo()} onClick={undo}>
              <Icon name="undo" />
            </Button>
            <Button size="sm" variant="ghost" title="Redo (Ctrl+Shift+Z)" aria-label="Redo" disabled={!history.current.canRedo()} onClick={redo}>
              <Icon name="redo" />
            </Button>
            <Button size="sm" variant="ghost" title="Arrange the steps neatly" onClick={tidyUp}>
              <Icon name="layout" /> Tidy up
            </Button>
          </span>
        )}
        <Button size="sm" onClick={() => setDrawer("results")} title="Funnel, per-step numbers, links and schedule">
          <Icon name="trending" /> Results{workflow.schedule ? " · scheduled" : ""}
        </Button>
        <Button size="sm" onClick={() => setDrawer("versions")}>
          Versions
        </Button>
        <Button size="sm" disabled={dirty} title={dirty ? "Save first" : undefined} onClick={() => setDrawer("test")}>
          Test-run
        </Button>
        {workflow.status === "published" &&
          can("workflows:publish") &&
          (workflow.is_paused ? (
            <Button size="sm" busy={lifecycle.isPending} onClick={() => lifecycle.mutate("resume")}>
              Resume
            </Button>
          ) : (
            <Button size="sm" busy={lifecycle.isPending} onClick={() => lifecycle.mutate("pause")}>
              Pause
            </Button>
          ))}
        {workflow.status !== "archived" && can("workflows:publish") && (
          <Button
            size="sm"
            variant="danger"
            onClick={() => window.confirm("Archive this workflow? Open enrollments stop.") && lifecycle.mutate("archive")}
          >
            Archive
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          className={deletePermission.allowed ? undefined : "locked"}
          busy={removeWorkflow.isPending}
          title={deletePermission.allowed ? "Delete this workflow" : deletePermission.reason}
          onClick={() =>
            deletePermission.allowed
              ? void ask({
                  title: `Delete "${workflow.name}"?`,
                  message: "Versions and finished enrollments are deleted. Running enrollments must be archived or finish first.",
                }).then((ok) => ok && removeWorkflow.mutate())
              : deletePermission.explain()
          }
        >
          <Icon name="trash" /> Delete
        </Button>
        {!readOnly && (
          <Button size="sm" busy={save.isPending && !publish.isPending} disabled={!dirty} onClick={() => void onSave()}>
            Save
          </Button>
        )}
        {can("workflows:publish") && workflow.status !== "archived" && (
          <Button
            size="sm"
            variant="primary"
            busy={publish.isPending}
            disabled={Boolean(report && !report.is_valid)}
            title={report && !report.is_valid ? "Fix the validation errors first" : undefined}
            onClick={() => {
              setProblem(null);
              publish.mutate();
            }}
          >
            {dirty ? "Save & publish" : "Publish"}
          </Button>
        )}
      </div>
      {problem !== null && (
        <div style={{ padding: "8px 16px" }}>
          <div className="alert error">
            {describeError(problem).split("\n")[0]}
            {publishErrors && (
              <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                {publishErrors.map((error, index) => (
                  <li key={index}>
                    <strong>{String(error.location?.[error.location.length - 1] ?? "")}</strong> {error.message}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
      <CanvasContext.Provider
        value={{
          catalog: catalogMap,
          officeNames,
          workflowNames,
          nodesWithErrors,
          readOnly,
          stepCounts,
          usedHandles,
          onAddAfter: (source, handle, screen) => setPicker({ kind: "after", source, handle, screen }),
          onEditTrigger: () => {
            if (!triggerId) return;
            setSelectedId(triggerId);
            setNodes((items) => items.map((node) => ({ ...node, selected: node.id === triggerId })));
          },
        }}
      >
      <EdgeContext.Provider value={edgeActions}>
        <div className="editor">
          <Palette catalog={catalog} onAdd={(type) => void addNode(type)} disabled={readOnly} />
          <div className="canvas" ref={wrapper} onDragOver={(event) => event.preventDefault()} onDrop={onDrop}>
            <ReactFlow<FlowNode, Edge>
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={(changes) => {
                onNodesChange(changes);
                if (changes.some((change) => change.type === "position" && change.dragging === false)) markDirty();
                if (changes.some((change) => change.type === "remove")) markDirty();
              }}
              onEdgesChange={(changes) => {
                onEdgesChange(changes);
                if (changes.some((change) => change.type === "remove")) markDirty();
              }}
              edgeTypes={edgeTypes}
              onConnect={onConnect}
              onConnectEnd={onConnectEnd}
              onReconnect={onReconnect}
              edgesReconnectable={!readOnly}
              connectionRadius={40}
              isValidConnection={(connection) =>
                Boolean(connection.source && connection.target) &&
                connectionProblem(edges, connection.source, connection.target, triggerId) === null
              }
              onNodeDragStart={() => snapshot()}
              onBeforeDelete={async () => {
                snapshot();
                return true;
              }}
              onSelectionChange={({ nodes: picked }) => setSelectedId(picked[0]?.id ?? null)}
              onPaneClick={() => setSelectedId(null)}
              nodesDraggable={!readOnly}
              nodesConnectable={!readOnly}
              elementsSelectable
              deleteKeyCode={readOnly ? null : ["Backspace", "Delete"]}
              fitView
              fitViewOptions={{ padding: 0.25 }}
            >
              <Background gap={20} />
              <Controls />
              <MiniMap pannable zoomable position="top-right" />
            </ReactFlow>
            {!readOnly && nodes.length <= 2 && (
              <div className="canvas-hint">Click the + under a step, or drag from its dot to empty space, to add the next step.</div>
            )}
          </div>
          <Inspector
            node={selected}
            catalog={catalogMap}
            report={report}
            validating={validating}
            readOnly={readOnly}
            onConfigChange={(id, config) => updateNode(id, (node) => ({ ...node, data: { ...node.data, config } }))}
            onTriggerChange={(nodeType, config) => {
              const trigger = nodes.find((node) => node.data.isTrigger);
              if (trigger) updateNode(trigger.id, (node) => ({ ...node, data: { ...node.data, nodeType, config } }));
            }}
            onRename={renameNode}
            onDelete={deleteNode}
            onSelectNode={(id) => {
              setSelectedId(id);
              setNodes((current) => current.map((node) => ({ ...node, selected: node.id === id })));
            }}
          />
        </div>
      </EdgeContext.Provider>
      </CanvasContext.Provider>
      {picker && (
        <NodePicker
          catalog={catalog}
          at={picker.screen}
          title={picker.kind === "insert" ? "Insert a step" : picker.handle === "next" ? `Add a step after ${picker.source}` : `Add a step for “${picker.handle.replace(/_/g, " ")}”`}
          onPick={pickStep}
          onClose={() => setPicker(null)}
        />
      )}
      {drawer === "test" && (
        <TestRunDrawer workflowId={workflow.id} version={workflow.latest_version} onClose={() => setDrawer(null)} />
      )}
      {confirmDialog}
      {drawer === "results" && <ResultsDrawer workflow={workflow} onClose={() => setDrawer(null)} />}
      {drawer === "versions" && (
        <VersionsDrawer
          workflow={workflow}
          onClose={() => setDrawer(null)}
          onRestore={(restored) => {
            snapshot();
            const next = graphToFlow(restored);
            setNodes(next.nodes);
            setEdges(next.edges);
            markDirty();
            setDrawer(null);
            toast("Version loaded into the editor. Save to make it the latest version.");
          }}
        />
      )}
    </div>
  );
}

function VersionsDrawer({
  workflow,
  onClose,
  onRestore,
}: {
  workflow: Schemas["WorkflowResponse"];
  onClose: () => void;
  onRestore: (graph: WorkflowGraph) => void;
}) {
  const versions = useQuery({
    queryKey: [...keys.workflow(workflow.id), "versions"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/automation/workflows/{workflow_id}/versions", {
          params: { path: { workflow_id: workflow.id } },
        }),
      ),
  });
  const [loading, setLoading] = useState<number | null>(null);
  const restore = async (version: number) => {
    setLoading(version);
    try {
      const detail = await unwrap(
        api.GET("/api/v1/automation/workflows/{workflow_id}/versions/{version}", {
          params: { path: { workflow_id: workflow.id, version } },
        }),
      );
      onRestore(detail.graph);
    } finally {
      setLoading(null);
    }
  };
  return (
    <Drawer title="Versions" onClose={onClose}>
      <p className="small muted" style={{ marginTop: 0 }}>
        Versions are immutable. Enrollments stay on the version they started with; publishing
        changes what new enrollments use.
      </p>
      {versions.isLoading && <Loading />}
      <ErrorAlert error={versions.error} />
      <table className="table">
        <thead>
          <tr>
            <th>Version</th>
            <th>Saved</th>
            <th>By</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(versions.data ?? []).map((version) => (
            <tr key={version.id}>
              <td>
                v{version.version} {version.is_current && <Badge tone="green">live</Badge>}
                {version.published_at && !version.is_current && <Badge>published before</Badge>}
              </td>
              <td className="small">{formatDateTime(version.created_at)}</td>
              <td className="small">{version.created_by}</td>
              <td>
                <Button size="sm" busy={loading === version.version} onClick={() => void restore(version.version)}>
                  Open in editor
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Drawer>
  );
}
