/** Right-hand panel: selected node's settings, or the workflow's validation report. */
import type { Schemas } from "../api/client";
import { Badge, Button } from "../components/ui";
import type { JsonSchema } from "../forms/SchemaForm";
import { SchemaForm } from "../forms/SchemaForm";
import { humanize } from "../lib/format";
import type { FlowNode, NodeType } from "./graph";
import { defaultConfig } from "./graph";

type ValidationReport = Schemas["ValidationReport"];

export function Inspector({
  node,
  catalog,
  report,
  validating,
  readOnly,
  onConfigChange,
  onTriggerChange,
  onRename,
  onDelete,
  onSelectNode,
}: {
  node: FlowNode | null;
  catalog: Map<string, NodeType>;
  report: ValidationReport | null;
  validating: boolean;
  readOnly: boolean;
  onConfigChange: (id: string, config: Record<string, unknown>) => void;
  onTriggerChange: (nodeType: string, config: Record<string, unknown>) => void;
  onRename: (id: string, next: string) => void;
  onDelete: (id: string) => void;
  onSelectNode: (id: string) => void;
}) {
  const type = node ? catalog.get(node.data.nodeType) : undefined;
  const triggers = [...catalog.values()].filter((candidate) => candidate.category === "trigger");

  return (
    <aside className="inspector" aria-label="Inspector">
      {node ? (
        <>
          <div className="inspector-section">
            <div className="row between">
              <h2>{type?.display_name ?? node.data.nodeType}</h2>
              {type && <Badge tone={type.category === "trigger" ? "violet" : "blue"}>{type.category}</Badge>}
            </div>
            {type && <p className="small muted" style={{ marginBottom: 0 }}>{type.description}</p>}
          </div>
          <div className="inspector-section stack">
            {node.data.isTrigger ? (
              <label className="field">
                <span>Trigger</span>
                <select
                  className="select"
                  value={node.data.nodeType}
                  disabled={readOnly}
                  onChange={(event) => {
                    const next = catalog.get(event.target.value);
                    onTriggerChange(event.target.value, next ? defaultConfig(next.config_schema) : {});
                  }}
                >
                  {triggers.map((trigger) => (
                    <option key={trigger.node_type} value={trigger.node_type}>
                      {trigger.display_name}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <label className="field">
                <span>Node ID</span>
                <input
                  className="input mono"
                  defaultValue={node.id}
                  key={node.id}
                  disabled={readOnly}
                  pattern="[A-Za-z0-9_-]{1,64}"
                  onBlur={(event) => {
                    const next = event.target.value.trim();
                    if (next && next !== node.id && /^[A-Za-z0-9_-]{1,64}$/.test(next)) onRename(node.id, next);
                    else event.target.value = node.id;
                  }}
                />
                <span className="field-help">Shown in the enrollment history and validation messages.</span>
              </label>
            )}
            {type ? (
              <fieldset disabled={readOnly} style={{ border: 0, padding: 0 }}>
                <SchemaForm
                  schema={type.config_schema as JsonSchema}
                  value={node.data.config}
                  onChange={(config) => onConfigChange(node.id, config)}
                />
              </fieldset>
            ) : (
              <div className="alert error">This node type no longer exists; delete the node.</div>
            )}
            <NodeIssues report={report} nodeId={node.id} />
            {!node.data.isTrigger && !readOnly && (
              <div className="row">
                <Button variant="danger" size="sm" onClick={() => onDelete(node.id)}>
                  Delete node
                </Button>
              </div>
            )}
          </div>
        </>
      ) : (
        <div className="inspector-section stack">
          <div className="row between">
            <h2>Validation</h2>
            {validating ? (
              <span className="spinner" />
            ) : report ? (
              report.is_valid ? (
                <Badge tone="green">Ready to publish</Badge>
              ) : (
                <Badge tone="red">{(report.errors ?? []).length} to fix</Badge>
              )
            ) : null}
          </div>
          <p className="small muted" style={{ margin: 0 }}>
            Select a node to edit it. Problems are checked as you edit; publishing is refused until
            every error is fixed.
          </p>
          <div className="issue-list">
            {(report?.errors ?? []).map((issue, index) => (
              <div
                key={`e${index}`}
                className="issue error"
                onClick={() => issue.node_id && onSelectNode(issue.node_id)}
              >
                <strong>{issue.node_id ?? "workflow"}</strong> · {issue.message}
              </div>
            ))}
            {(report?.warnings ?? []).map((issue, index) => (
              <div
                key={`w${index}`}
                className="issue warning"
                onClick={() => issue.node_id && onSelectNode(issue.node_id)}
              >
                <strong>{issue.node_id ?? "workflow"}</strong> · {issue.message}
              </div>
            ))}
            {report && (report.errors ?? []).length === 0 && (report.warnings ?? []).length === 0 && (
              <div className="alert success">No problems found.</div>
            )}
          </div>
        </div>
      )}
    </aside>
  );
}

function NodeIssues({ report, nodeId }: { report: ValidationReport | null; nodeId: string }) {
  const issues = [
    ...(report?.errors ?? []).filter((issue) => issue.node_id === nodeId).map((issue) => ({ ...issue, level: "error" })),
    ...(report?.warnings ?? []).filter((issue) => issue.node_id === nodeId).map((issue) => ({ ...issue, level: "warning" })),
  ];
  if (!issues.length) return null;
  return (
    <div className="issue-list">
      {issues.map((issue, index) => (
        <div key={index} className={`issue ${issue.level}`}>
          {issue.handle ? `${humanize(issue.handle)}: ` : ""}
          {issue.message}
        </div>
      ))}
    </div>
  );
}
