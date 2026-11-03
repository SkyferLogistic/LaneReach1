/** A node on the workflow canvas: icon, name, ID, config summary and handles. */
import { createContext, memo, useContext } from "react";
import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import type { FlowNode, NodeType } from "./graph";
import { handleLabel, handleTone, handlesFor, summarizeConfig } from "./graph";
import { Icon } from "../components/icons";
import type { IconName } from "../components/icons";

const NODE_ICONS: Record<string, IconName> = {
  trigger_tag_added: "tag",
  trigger_contact_created: "contacts",
  trigger_reply_classified: "reply",
  trigger_scheduled_date: "clock",
  trigger_manual_enroll: "play",
  trigger_lead_status_changed: "leads",
  trigger_custom_event: "key",
  switch_on_field: "branch",
  enroll_in_workflow: "workflow",
  send_email: "mail",
  reply_in_thread: "reply",
  add_tag: "tag",
  remove_tag: "tag",
  create_pricing_lead: "leads",
  notify_office_owner: "bell",
  notify_teams_channel: "bell",
  assign_owner: "agents",
  change_contact_stage: "trending",
  if_condition: "branch",
  branch_on_reply_bucket: "branch",
  ab_split: "branch",
  wait_business_days: "wait",
  wait_until_date: "clock",
  stop: "stop",
  suppress_contact: "shield",
  freeze_company: "pause",
  pause_workflow_for_company: "pause",
  trigger_deal_stage_changed: "trending",
  trigger_meeting_booked: "clock",
  trigger_form_submitted: "file",
  trigger_email_opened: "eye",
  trigger_email_clicked: "external",
  trigger_contact_stage_changed: "contacts",
  wait_for_engagement: "eye",
  wait_until_weekday: "clock",
  branch_on_email_status: "shield",
  verify_email: "shield",
  send_to_webhook: "sync",
  notify_slack: "bell",
  add_note: "file",
  update_deal: "trending",
  create_deal: "trending",
  move_deal_stage: "trending",
  create_task: "check",
  trigger_quote_status_changed: "quote",
  trigger_reply_received: "reply",
  trigger_lead_created: "leads",
  trigger_meeting_canceled: "clock",
  trigger_email_bounced: "alert",
  trigger_quote_expiring: "quote",
  trigger_deal_idle: "wait",
  create_quote_draft: "quote",
  update_contact_field: "contacts",
  assign_round_robin: "agents",
};

const CATEGORY_ICONS: Record<string, IconName> = {
  trigger: "events",
  action: "outbox",
  logic: "branch",
  ai: "sparkles",
  protection: "shield",
};

/** The icon for a node type (AI nodes all get the sparkles). */
export function nodeIcon(nodeType: string, category: string | undefined): IconName {
  if (nodeType.startsWith("ai_")) return "sparkles";
  return NODE_ICONS[nodeType] ?? CATEGORY_ICONS[category ?? "action"] ?? "outbox";
}

export interface CanvasContextValue {
  catalog: Map<string, NodeType>;
  officeNames: Map<string, string>;
  workflowNames?: Map<string, string>;
  nodesWithErrors: Set<string>;
  readOnly: boolean;
  /** Enrollments that ran each node (live campaigns), shown as a bubble. */
  stepCounts?: Map<string, number>;
  /** Outputs already connected, as `node:handle`. */
  usedHandles?: Set<string>;
  /** "+" under a free output: open the step picker for it (screen position of the button). */
  onAddAfter?: (nodeId: string, handle: string, screen: { x: number; y: number }) => void;
  /** "Change trigger" on the trigger node. */
  onEditTrigger?: () => void;
}

export const CanvasContext = createContext<CanvasContextValue>({
  catalog: new Map(),
  officeNames: new Map(),
  nodesWithErrors: new Set(),
  readOnly: false,
});

function WorkflowNodeViewInner({ id, data, selected }: NodeProps<FlowNode>) {
  const { catalog, officeNames, workflowNames, nodesWithErrors, stepCounts, usedHandles, onAddAfter, onEditTrigger, readOnly } =
    useContext(CanvasContext);
  const count = stepCounts?.get(id);
  const type = catalog.get(data.nodeType);
  const handles = handlesFor(type, data.config);
  const summary = summarizeConfig(data.nodeType, data.config, { offices: officeNames, workflows: workflowNames });
  const classes = ["flow-node", selected ? "selected" : "", nodesWithErrors.has(id) ? "has-error" : "", handles.length ? "has-outputs" : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <div className={classes}>
      {!data.isTrigger && <Handle type="target" position={Position.Top} />}
      {count ? (
        <span className="flow-node-count" title={`${count} enrollments ran this step`}>
          {count}
        </span>
      ) : null}
      <div className="flow-node-head">
        <span className={`node-icon cat-${type?.category ?? "action"}`}>
          <Icon name={nodeIcon(data.nodeType, type?.category)} />
        </span>
        <span className="truncate">{type?.display_name ?? data.nodeType}</span>
      </div>
      <div className="flow-node-body">
        <div className="flow-node-id">{id}</div>
        {summary && <div className="small">{summary}</div>}
        {!type && <div className="small" style={{ color: "var(--danger)" }}>Unknown node type</div>}
        {data.isTrigger && !readOnly && onEditTrigger && (
          <button type="button" className="chip-toggle nodrag" style={{ marginTop: 6 }} onClick={onEditTrigger}>
            Change trigger
          </button>
        )}
      </div>
      {handles.map((handle, index) => {
        const left = `${((index + 1) / (handles.length + 1)) * 100}%`;
        const free = !usedHandles?.has(`${id}:${handle}`);
        const tone = handleTone(handle);
        return (
          <div key={handle}>
            <Handle id={handle} type="source" position={Position.Bottom} className={`flow-handle tone-${tone}`} style={{ left }} title={handleLabel(handle) || "next step"} />
            {handleLabel(handle) && (
              <span className={`flow-handle-label tone-${tone}`} style={{ left }}>
                {handleLabel(handle)}
              </span>
            )}
            {free && !readOnly && onAddAfter && (
              <button
                type="button"
                className="flow-add nodrag nopan"
                style={{ left }}
                title={`Add a step after ${handleLabel(handle) || "this"}`}
                aria-label={`Add a step after ${id}${handle === "next" ? "" : ` (${handleLabel(handle)})`}`}
                onClick={(event) => {
                  event.stopPropagation();
                  const box = event.currentTarget.getBoundingClientRect();
                  onAddAfter(id, handle, { x: box.left + box.width / 2, y: box.bottom });
                }}
              >
                <Icon name="plus" />
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

export const WorkflowNodeView = memo(WorkflowNodeViewInner);
