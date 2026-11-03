/**
 * Canvas helpers that make building easy: a searchable step picker (opened from "+" buttons,
 * from edges and when a connection is dropped on empty canvas) and edges with "+" / "×".
 */
import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath } from "@xyflow/react";
import type { EdgeProps } from "@xyflow/react";
import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../components/icons";
import { rankItems } from "../lib/search";
import type { NodeType } from "./graph";
import { handleTone } from "./graph";
import { nodeIcon } from "./WorkflowNodeView";

const GROUPS: { category: string; title: string }[] = [
  { category: "action", title: "Actions" },
  { category: "logic", title: "Logic & waits" },
  { category: "ai", title: "AI" },
  { category: "protection", title: "Protection" },
];

/** Steps people add most, shown first when the search is empty. */
const SUGGESTED = ["send_email", "wait_business_days", "if_condition", "add_tag", "create_task", "notify_office_owner", "stop"];

export function NodePicker({
  catalog,
  at,
  title,
  onPick,
  onClose,
}: {
  catalog: NodeType[];
  at: { x: number; y: number };
  title: string;
  onPick: (nodeType: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const steps = useMemo(() => catalog.filter((type) => type.category !== "trigger"), [catalog]);
  const results = useMemo(() => {
    if (!query.trim()) {
      const suggested = SUGGESTED.map((key) => steps.find((type) => type.node_type === key)).filter(Boolean) as NodeType[];
      return suggested;
    }
    return rankItems(
      query,
      steps.map((type) => ({ ...type, label: type.display_name, keywords: [type.node_type.replace(/_/g, " "), type.description, type.category] })),
    ).slice(0, 12);
  }, [steps, query]);
  useEffect(() => {
    input.current?.focus();
  }, []);
  useEffect(() => setActive(0), [query]);
  const pick = (type: NodeType | undefined) => type && onPick(type.node_type);
  return (
    <>
      <div className="picker-backdrop" onMouseDown={onClose} />
      <div
        className="node-picker"
        style={{ left: at.x, top: at.y }}
        role="dialog"
        aria-label={title}
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setActive((index) => Math.min(index + 1, results.length - 1));
          }
          if (event.key === "ArrowUp") {
            event.preventDefault();
            setActive((index) => Math.max(index - 1, 0));
          }
          if (event.key === "Enter") pick(results[active]);
        }}
      >
        <div className="node-picker-head">
          <strong>{title}</strong>
          <button type="button" className="icon-button" aria-label="Close" onClick={onClose}>
            <Icon name="x" />
          </button>
        </div>
        <div className="palette-search">
          <Icon name="search" />
          <input ref={input} value={query} placeholder="Search steps: email, wait, tag, quote…" onChange={(event) => setQuery(event.target.value)} aria-label="Search steps" />
        </div>
        <div className="node-picker-list">
          {!query.trim() && <div className="nav-group-title">Suggested</div>}
          {results.map((type, index) => (
            <PickerOption key={type.node_type} type={type} active={index === active} onPick={() => pick(type)} />
          ))}
          {results.length === 0 && <div className="small muted" style={{ padding: 10 }}>No step matches “{query}”.</div>}
          {!query.trim() &&
            GROUPS.map((group) => (
              <div key={group.category}>
                <div className="nav-group-title">{group.title}</div>
                {steps
                  .filter((type) => type.category === group.category)
                  .map((type) => (
                    <PickerOption key={type.node_type} type={type} active={false} onPick={() => pick(type)} />
                  ))}
              </div>
            ))}
        </div>
      </div>
    </>
  );
}

function PickerOption({ type, active, onPick }: { type: NodeType; active: boolean; onPick: () => void }) {
  return (
    <button type="button" className={`picker-option${active ? " on" : ""}`} onClick={onPick} title={type.description}>
      <span className={`node-icon cat-${type.category}`}>
        <Icon name={nodeIcon(type.node_type, type.category)} />
      </span>
      <span style={{ minWidth: 0 }}>
        {type.display_name}
        <span className="desc">{type.description}</span>
      </span>
    </button>
  );
}

export interface EdgeActions {
  readOnly: boolean;
  onInsert: (edgeId: string, screen: { x: number; y: number }) => void;
  onRemove: (edgeId: string) => void;
}

export const EdgeContext = createContext<EdgeActions>({ readOnly: true, onInsert: () => undefined, onRemove: () => undefined });

/** An edge coloured by its output (green yes, red no), with "+" to insert a step and "×" to remove it. */
export function InsertableEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, sourceHandleId, selected, markerEnd, style }: EdgeProps) {
  const { readOnly, onInsert, onRemove } = useContext(EdgeContext);
  const [path, labelX, labelY] = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, borderRadius: 14 });
  const handle = sourceHandleId ?? "next";
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} className={`tone-${handleTone(handle)}${selected ? " selected" : ""}`} />
      <EdgeLabelRenderer>
        <div className={`edge-tools nodrag nopan${selected ? " on" : ""}`} style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}>
          {!readOnly && (
            <>
              <button
                type="button"
                className="edge-button"
                title="Insert a step here"
                aria-label="Insert a step here"
                onClick={(event) => {
                  const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
                  onInsert(id, { x: box.left + box.width / 2, y: box.bottom });
                }}
              >
                <Icon name="plus" />
              </button>
              <button type="button" className="edge-button danger" title="Remove this connection" aria-label="Remove this connection" onClick={() => onRemove(id)}>
                <Icon name="x" />
              </button>
            </>
          )}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}
