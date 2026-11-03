/** Node palette: drag a node onto the canvas, or click to add it below the selection. */
import type { DragEvent } from "react";
import { Icon } from "../components/icons";
import type { NodeType } from "./graph";
import { nodeIcon } from "./WorkflowNodeView";

const GROUPS: { category: string; title: string }[] = [
  { category: "action", title: "Actions" },
  { category: "logic", title: "Logic" },
  { category: "ai", title: "AI" },
  { category: "protection", title: "Protection" },
];

export const DRAG_MIME = "application/lanereach-node";

export function Palette({
  catalog,
  onAdd,
  disabled,
}: {
  catalog: NodeType[];
  onAdd: (nodeType: string) => void;
  disabled: boolean;
}) {
  const onDragStart = (event: DragEvent, nodeType: string) => {
    event.dataTransfer.setData(DRAG_MIME, nodeType);
    event.dataTransfer.effectAllowed = "move";
  };
  return (
    <aside className="palette" aria-label="Node palette">
      <p className="small muted" style={{ marginTop: 0 }}>
        Drag onto the canvas, or click to add after the selected node.
      </p>
      {GROUPS.map((group) => (
        <div key={group.category} className="palette-group">
          <div className="nav-group-title">{group.title}</div>
          {catalog
            .filter((type) => type.category === group.category)
            .map((type) => (
              <button
                key={type.node_type}
                type="button"
                className="palette-item"
                draggable={!disabled}
                disabled={disabled}
                onDragStart={(event) => onDragStart(event, type.node_type)}
                onClick={() => onAdd(type.node_type)}
                title={type.description}
              >
                <span className={`node-icon cat-${type.category}`}>
                  <Icon name={nodeIcon(type.node_type, type.category)} />
                </span>
                <span style={{ minWidth: 0 }}>
                  {type.display_name}
                  <span className="desc">{type.description}</span>
                </span>
              </button>
            ))}
        </div>
      ))}
    </aside>
  );
}
