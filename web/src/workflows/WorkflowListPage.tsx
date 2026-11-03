/** Workflow list and creation (blank or from a starter). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { keys, useOffices, useScriptMatrix } from "../api/queries";
import { useSession } from "../auth/session";
import { Page } from "../components/Layout";
import { Icon } from "../components/icons";
import { Badge, Button, Card, Empty, ErrorAlert, Loading, Modal, PageHeader, StatusBadge } from "../components/ui";
import { scheduleLabel } from "../lib/campaigns";
import { formatRelative } from "../lib/format";
import { CampaignNumbers } from "./CampaignResults";
import { useConfirm, useDelete, usePermission } from "../components/confirm";
import type { WorkflowGraph } from "./graph";

interface Starter {
  id: string;
  title: string;
  description: string;
  needsOffice: boolean;
  build: (officeId: string, scripts: ScriptKeys) => WorkflowGraph;
}

/** The first script of each service line, used to prefill the script sequences. */
export interface ScriptKeys {
  air?: string;
  ocean?: string;
}

function scriptSequence(line: "air" | "ocean", officeId: string, scriptKey: string | undefined): WorkflowGraph {
  const send = (id: string, key: string) => ({
    id,
    type: "send_email",
    config: { office_id: officeId, template_key: key, service_line: line },
  });
  return {
    trigger: { id: "trigger", type: "trigger_tag_added", config: { tag: `${line}-intro` } },
    nodes: [
      send("send_script", scriptKey ?? `${line}_script`),
      { id: "wait_4", type: "wait_business_days", config: { days: 4 } },
      send("send_follow_up", "follow_up_1"),
      { id: "wait_6", type: "wait_business_days", config: { days: 6 } },
      { id: "tag_done", type: "add_tag", config: { tag: `${line}-sequence-done` } },
      { id: "done", type: "stop", config: {} },
    ],
    edges: [
      { source: "trigger", target: "send_script", handle: "next" },
      { source: "send_script", target: "wait_4", handle: "next" },
      { source: "wait_4", target: "send_follow_up", handle: "next" },
      { source: "send_follow_up", target: "wait_6", handle: "next" },
      { source: "wait_6", target: "tag_done", handle: "next" },
      { source: "tag_done", target: "done", handle: "next" },
    ],
  };
}

const GULF = ["AE", "SA", "QA", "KW", "BH", "OM"];
const EUROPE = ["FR", "DE", "ES", "IT", "NL", "BE", "PL", "PT", "GB", "IE", "CH"];

const STARTERS: Starter[] = [
  {
    id: "blank",
    title: "Blank",
    description: "Manual enrollment and a stop. Build from scratch.",
    needsOffice: false,
    build: () => ({
      trigger: { id: "trigger", type: "trigger_manual_enroll", config: {} },
      nodes: [{ id: "done", type: "stop", config: {} }],
      edges: [{ source: "trigger", target: "done", handle: "next" }],
    }),
  },
  {
    id: "intro",
    title: "Intro sequence",
    description: "Tag a contact → intro email → wait 5 business days → follow-up → stop.",
    needsOffice: true,
    build: (officeId) => ({
      trigger: { id: "trigger", type: "trigger_tag_added", config: { tag: "intro" } },
      nodes: [
        { id: "send_intro", type: "send_email", config: { office_id: officeId, template_key: "intro" } },
        { id: "wait_5", type: "wait_business_days", config: { days: 5 } },
        { id: "send_follow_up", type: "send_email", config: { office_id: officeId, template_key: "follow_up_1" } },
        { id: "done", type: "stop", config: {} },
      ],
      edges: [
        { source: "trigger", target: "send_intro", handle: "next" },
        { source: "send_intro", target: "wait_5", handle: "next" },
        { source: "wait_5", target: "send_follow_up", handle: "next" },
        { source: "send_follow_up", target: "done", handle: "next" },
      ],
    }),
  },
  {
    id: "air_script",
    title: "Air script sequence",
    description: "Tag air-intro → air script in the contact's language (air agents rotate) → wait 4 days → follow-up → done.",
    needsOffice: true,
    build: (officeId, scripts) => scriptSequence("air", officeId, scripts.air),
  },
  {
    id: "ocean_script",
    title: "Ocean script sequence",
    description: "Tag ocean-intro → ocean script (ocean agents rotate, same agent for the thread) → wait → follow-up.",
    needsOffice: true,
    build: (officeId, scripts) => scriptSequence("ocean", officeId, scripts.ocean),
  },
  {
    id: "regions",
    title: "Route by region",
    description: "Switch on the contact's country: Gulf, Europe and the rest each get their own email.",
    needsOffice: true,
    build: (officeId) => ({
      trigger: { id: "trigger", type: "trigger_tag_added", config: { tag: "outreach" } },
      nodes: [
        {
          id: "region",
          type: "switch_on_field",
          config: {
            field: "contact.country",
            cases: [
              { handle: "gulf", values: GULF },
              { handle: "europe", values: EUROPE },
            ],
          },
        },
        { id: "send_gulf", type: "send_email", config: { office_id: officeId, template_key: "intro" } },
        { id: "send_europe", type: "send_email", config: { office_id: officeId, template_key: "intro" } },
        { id: "send_other", type: "send_email", config: { office_id: officeId, template_key: "intro" } },
        { id: "done", type: "stop", config: {} },
      ],
      edges: [
        { source: "trigger", target: "region", handle: "next" },
        { source: "region", target: "send_gulf", handle: "gulf" },
        { source: "region", target: "send_europe", handle: "europe" },
        { source: "region", target: "send_other", handle: "other" },
        { source: "send_gulf", target: "done", handle: "next" },
        { source: "send_europe", target: "done", handle: "next" },
        { source: "send_other", target: "done", handle: "next" },
      ],
    }),
  },
  {
    id: "won",
    title: "Won deal → customer",
    description: "A deal reaches Won on the pipeline → tag customer → stage customer → tell the office owner.",
    needsOffice: false,
    build: () => ({
      trigger: { id: "trigger", type: "trigger_deal_stage_changed", config: { kinds: ["won"] } },
      nodes: [
        { id: "tag", type: "add_tag", config: { tag: "customer" } },
        { id: "stage", type: "change_contact_stage", config: { stage: "customer" } },
        { id: "tell_owner", type: "notify_office_owner", config: { message: "Deal won with {{contact.full_name}} ({{company.name}})." } },
        { id: "done", type: "stop", config: {} },
      ],
      edges: [
        { source: "trigger", target: "tag", handle: "next" },
        { source: "tag", target: "stage", handle: "next" },
        { source: "stage", target: "tell_owner", handle: "next" },
        { source: "tell_owner", target: "done", handle: "next" },
      ],
    }),
  },
  {
    id: "lost",
    title: "Lost deal → try again later",
    description: "A deal is lost → wait about three months → task to call → friendly follow-up from the same office.",
    needsOffice: true,
    build: (officeId) => ({
      trigger: { id: "trigger", type: "trigger_deal_stage_changed", config: { kinds: ["lost"] } },
      nodes: [
        { id: "wait", type: "wait_business_days", config: { days: 60 } },
        { id: "call", type: "create_task", config: { title: "Call {{contact.full_name}} again about new lanes", assignee: "deal_owner", due_in_business_days: 1 } },
        { id: "follow_up", type: "send_email", config: { office_id: officeId, template_key: "follow_up_1" } },
        { id: "done", type: "stop", config: {} },
      ],
      edges: [
        { source: "trigger", target: "wait", handle: "next" },
        { source: "wait", target: "call", handle: "next" },
        { source: "call", target: "follow_up", handle: "next" },
        { source: "follow_up", target: "done", handle: "next" },
      ],
    }),
  },
  {
    id: "quote_expiring",
    title: "Quote expiring → chase",
    description: "3 days before a sent quote expires → friendly reminder in the thread → call task for the owner.",
    needsOffice: true,
    build: (officeId) => ({
      trigger: { id: "trigger", type: "trigger_quote_expiring", config: { days_before: 3 } },
      nodes: [
        { id: "reminder", type: "send_email", config: { office_id: officeId, template_key: "follow_up_1" } },
        { id: "call", type: "create_task", config: { title: "Call {{contact.full_name}} before the quote expires", assignee: "deal_owner", due_in_business_days: 1 } },
        { id: "done", type: "stop", config: {} },
      ],
      edges: [
        { source: "trigger", target: "reminder", handle: "next" },
        { source: "reminder", target: "call", handle: "next" },
        { source: "call", target: "done", handle: "next" },
      ],
    }),
  },
  {
    id: "deal_idle",
    title: "Stale deal nudge",
    description: "A deal sits 7 days in the same stage → task for the owner → note on the deal.",
    needsOffice: false,
    build: () => ({
      trigger: { id: "trigger", type: "trigger_deal_idle", config: { days: 7 } },
      nodes: [
        { id: "task", type: "create_task", config: { title: "Move {{company.name}} forward: no change for a week", assignee: "deal_owner", due_in_business_days: 0, priority: "high" } },
        { id: "note", type: "add_note", config: { body: "Reminder sent to the owner: the deal has not moved for 7 days." } },
        { id: "done", type: "stop", config: {} },
      ],
      edges: [
        { source: "trigger", target: "task", handle: "next" },
        { source: "task", target: "note", handle: "next" },
        { source: "note", target: "done", handle: "next" },
      ],
    }),
  },
  {
    id: "bounce",
    title: "Bounce handling",
    description: "An email hard-bounces → tell the office owner → task to find another contact at the company.",
    needsOffice: false,
    build: () => ({
      trigger: { id: "trigger", type: "trigger_email_bounced", config: { bounce_types: ["hard"] } },
      nodes: [
        { id: "notify", type: "notify_office_owner", config: { message: "{{contact.email}} bounced. Find another contact at {{company.name}}." } },
        { id: "task", type: "create_task", config: { title: "Find a new contact at {{company.name}} (bounce)", assignee: "office_owner", due_in_business_days: 2 } },
        { id: "done", type: "stop", config: {} },
      ],
      edges: [
        { source: "trigger", target: "task", handle: "next" },
        { source: "task", target: "notify", handle: "next" },
        { source: "notify", target: "done", handle: "next" },
      ],
    }),
  },
  {
    id: "lead_quote",
    title: "New quote request → draft quote",
    description: "A quote request arrives → draft quote with the usual charges of its mode → pricing task for the owner.",
    needsOffice: false,
    build: () => ({
      trigger: { id: "trigger", type: "trigger_lead_created", config: {} },
      nodes: [
        { id: "quote", type: "create_quote_draft", config: { validity_days: 14 } },
        { id: "task", type: "create_task", config: { title: "Price the quote for {{company.name}}", assignee: "deal_owner", due_in_business_days: 1, priority: "high" } },
        { id: "done", type: "stop", config: {} },
      ],
      edges: [
        { source: "trigger", target: "quote", handle: "next" },
        { source: "quote", target: "task", handle: "next" },
        { source: "task", target: "done", handle: "next" },
      ],
    }),
  },
  {
    id: "web_quote",
    title: "Website quote request",
    description: "Your website or CRM sends a quote_requested event → pricing lead (high) → tell the owner.",
    needsOffice: false,
    build: () => ({
      trigger: { id: "trigger", type: "trigger_custom_event", config: { event_name: "quote_requested" } },
      nodes: [
        { id: "lead", type: "create_pricing_lead", config: { priority: "high" } },
        { id: "tell_owner", type: "notify_office_owner", config: { message: "Website quote request from {{contact.full_name}}." } },
        { id: "done", type: "stop", config: {} },
      ],
      edges: [
        { source: "trigger", target: "lead", handle: "next" },
        { source: "lead", target: "tell_owner", handle: "next" },
        { source: "tell_owner", target: "done", handle: "next" },
      ],
    }),
  },
  {
    id: "interested",
    title: "Answer interested replies",
    description: "Reply classified as interested → AI draft (review queue) → reply in thread when approved.",
    needsOffice: false,
    build: () => ({
      trigger: {
        id: "trigger",
        type: "trigger_reply_classified",
        config: { buckets: ["interested", "meeting_request"] },
      },
      nodes: [
        { id: "draft", type: "ai_draft_reply", config: { tone: "friendly" } },
        { id: "answer", type: "reply_in_thread", config: { use_ai_draft: true } },
        { id: "done", type: "stop", config: {} },
      ],
      edges: [
        { source: "trigger", target: "draft", handle: "next" },
        { source: "draft", target: "answer", handle: "next" },
        { source: "answer", target: "done", handle: "next" },
      ],
    }),
  },
  {
    id: "rate",
    title: "Rate-query handover",
    description: "Rate or service question → extract shipment → tell the owner → pricing lead.",
    needsOffice: false,
    build: () => ({
      trigger: {
        id: "trigger",
        type: "trigger_reply_classified",
        config: { buckets: ["rate_query", "service_query"] },
      },
      nodes: [
        { id: "extract", type: "ai_extract_shipment_details", config: {} },
        {
          id: "tell_owner",
          type: "notify_office_owner",
          config: { message: "Rate request from {{contact.full_name}}: {{reply.summary}}" },
        },
        { id: "lead", type: "create_pricing_lead", config: { priority: "high" } },
      ],
      edges: [
        { source: "trigger", target: "extract", handle: "next" },
        { source: "extract", target: "tell_owner", handle: "next" },
        { source: "tell_owner", target: "lead", handle: "next" },
      ],
    }),
  },
];

export function WorkflowListPage() {
  const [status, setStatus] = useState("");
  const [params] = useSearchParams();
  // `?new=1` (from the command palette) opens the dialog straight away.
  const [creating, setCreating] = useState(params.get("new") === "1");
  const { can } = useSession();
  const navigate = useNavigate();
  const [confirmDialog, ask] = useConfirm();
  const deletePermission = usePermission("workflows:publish", "admins and campaign managers");
  const remove = useDelete<string>({
    run: (id) => unwrap(api.DELETE("/api/v1/automation/workflows/{workflow_id}", { params: { path: { workflow_id: id } } })),
    success: "Campaign deleted.",
    invalidate: [keys.workflows],
  });
  const askDelete = async (workflow: { id: string; name: string; status: string }) => {
    const ok = await ask({
      title: `Delete "${workflow.name}"?`,
      message:
        workflow.status === "published"
          ? "Its versions and finished enrollments are deleted too. A campaign with running enrollments must be archived first."
          : "Its versions and any finished test enrollments are deleted. This cannot be undone.",
    });
    if (!ok) return;
    remove.mutate(workflow.id);
  };
  const workflows = useQuery({
    queryKey: [...keys.workflows, status],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/automation/workflows", {
          params: { query: { status: (status || undefined) as never, limit: 100 } },
        }),
      ),
  });

  return (
    <Page>
      <PageHeader
        title="Workflows"
        description="Workflows that reach out to contacts: triggers start them, each step runs through the engine, every email passes the guardrails. Results show opens, clicks, replies and quote requests."
        actions={
          <>
            <select className="select" style={{ width: 160 }} value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="">All statuses</option>
              <option value="draft">Draft</option>
              <option value="published">Published</option>
              <option value="archived">Archived</option>
            </select>
            {can("workflows:write") && (
              <Button variant="primary" onClick={() => setCreating(true)}>
                New workflow
              </Button>
            )}
          </>
        }
      />
      <Card flush>
        {workflows.isLoading && <Loading />}
        <ErrorAlert error={workflows.error} />
        {workflows.data && workflows.data.items.length === 0 && (
          <Empty art="plane" title="No workflows yet">Create one from a starter to see the canvas.</Empty>
        )}
        {workflows.data && workflows.data.items.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Status</th>
                  <th>Results</th>
                  <th>Version</th>
                  <th>Updated</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {workflows.data.items.map((workflow) => (
                  <tr key={workflow.id} className="clickable" onClick={() => navigate(`/workflows/${workflow.id}`)}>
                    <td>
                      <strong>{workflow.name}</strong>
                      {workflow.description && <div className="small muted truncate" style={{ maxWidth: 480 }}>{workflow.description}</div>}
                    </td>
                    <td>
                      <div className="row">
                        <StatusBadge value={workflow.status} />
                        {workflow.is_paused && <Badge tone="amber">paused</Badge>}
                        {workflow.has_unpublished_changes && workflow.status === "published" && (
                          <Badge tone="cyan">unpublished changes</Badge>
                        )}
                      </div>
                    </td>
                    <td>
                      <CampaignNumbers workflowId={workflow.id} live={workflow.current_version !== null} />
                      {workflow.schedule && (
                        <div className="small muted" style={{ marginTop: 2 }}>
                          <Icon name="clock" /> {scheduleLabel(workflow.schedule)}
                        </div>
                      )}
                    </td>
                    <td>
                      {workflow.current_version ? `v${workflow.current_version}` : "—"}
                      {workflow.latest_version !== workflow.current_version && <span className="small muted"> (v{workflow.latest_version} draft)</span>}
                    </td>
                    <td className="small muted">{formatRelative(workflow.updated_at)}</td>
                    <td>
                      <span className="row-actions always">
                        <button
                          type="button"
                          className={`icon-button danger${deletePermission.allowed ? "" : " locked"}`}
                          title={deletePermission.allowed ? `Delete ${workflow.name}` : deletePermission.reason}
                          aria-label={`Delete ${workflow.name}`}
                          onClick={(event) => {
                            event.stopPropagation();
                            if (deletePermission.allowed) void askDelete(workflow);
                            else deletePermission.explain();
                          }}
                        >
                          <Icon name="trash" />
                        </button>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {creating && <NewWorkflowModal onClose={() => setCreating(false)} />}
      {confirmDialog}
    </Page>
  );
}

function NewWorkflowModal({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [starterId, setStarterId] = useState("intro");
  const offices = useOffices();
  const [officeId, setOfficeId] = useState("");
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const starter = STARTERS.find((candidate) => candidate.id === starterId) ?? STARTERS[0]!;
  const office = officeId || offices.data?.[0]?.id || "";
  const matrix = useScriptMatrix();
  // The first live script of each line prefills the script sequences.
  const scriptKeys: ScriptKeys = {
    air: matrix.data?.scripts.find((script) => script.service_line === "air")?.key,
    ocean: matrix.data?.scripts.find((script) => script.service_line === "ocean")?.key,
  };
  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/automation/workflows", {
          body: { name, description, graph: starter.build(office, scriptKeys) },
        }),
      ),
    onSuccess: (workflow) => {
      void queryClient.invalidateQueries({ queryKey: keys.workflows });
      navigate(`/workflows/${workflow.id}`);
    },
  });
  return (
    <Modal
      title="New workflow"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            busy={create.isPending}
            disabled={!name.trim() || (starter.needsOffice && !office)}
            onClick={() => create.mutate()}
          >
            Create and open
          </Button>
        </>
      }
    >
      <div className="stack">
        <label className="field">
          <span>Name</span>
          <input className="input" autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="UAE importers intro" />
        </label>
        <label className="field">
          <span>Description</span>
          <input className="input" value={description} onChange={(event) => setDescription(event.target.value)} />
        </label>
        <div className="field">
          <span>Start from</span>
          <div className="stack tight">
            {STARTERS.map((candidate) => (
              <label key={candidate.id} className="checkbox" style={{ alignItems: "flex-start" }}>
                <input
                  type="radio"
                  name="starter"
                  value={candidate.id}
                  aria-label={candidate.title}
                  checked={candidate.id === starterId}
                  onChange={() => setStarterId(candidate.id)}
                />
                <span>
                  <strong>{candidate.title}</strong>
                  <span className="field-help" style={{ display: "block" }}>{candidate.description}</span>
                </span>
              </label>
            ))}
          </div>
        </div>
        {starter.needsOffice && (
          <label className="field">
            <span>Office that sends</span>
            <select className="select" value={office} onChange={(event) => setOfficeId(event.target.value)}>
              {(offices.data ?? []).map((candidate) => (
                <option key={candidate.id} value={candidate.id}>
                  {candidate.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <ErrorAlert error={create.error} />
      </div>
    </Modal>
  );
}
