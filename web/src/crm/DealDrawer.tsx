/** A deal: stage stepper, details, timeline, tasks and notes. Used by the board and contacts. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys, useTasks } from "../api/queries";
import { useSession } from "../auth/session";
import { fireConfetti } from "../components/delight";
import { Icon } from "../components/icons";
import type { IconName } from "../components/icons";
import { SkeletonRows } from "../components/motion";
import { Badge, Button, Drawer, ErrorAlert, LinePill, StatusBadge, Tabs, useErrorToast, useToast } from "../components/ui";
import { formatDate, formatDateTime, formatRelative } from "../lib/format";
import { daysInStage, formatMoney } from "./board";
import { useConfirm, useDelete } from "../components/confirm";
import { ServiceLineOptions } from "../components/pickers";
import { LINE_ICONS, asServiceLine } from "../lib/freight";
import { DealAi, ScoreBadge } from "./DealAi";
import { DealPricing } from "./DealPricing";
import { QUOTES_KEY, QuoteBuilder, QuoteStatusBadge, useLogisticsCatalog, useStartQuote } from "./QuoteBuilder";

type Deal = Schemas["DealResponse"];
type Pipeline = Schemas["CrmPipelineResponse"];

const TIMELINE_ICONS: Record<string, IconName> = {
  "deal.created": "plus",
  "deal.stage_changed": "route",
  "deal.value_changed": "trending",
  "deal.won": "leads",
  "deal.lost": "x",
  "deal.reopened": "sync",
  "deal.priced_from_rates": "pricing",
  "deal.market_check": "external",
  note: "file",
  task: "clock",
  "task.done": "check",
  "email.outbound": "outbox",
  "email.inbound": "reply",
};

export function useInvalidateCrm() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: keys.crm });
    void queryClient.invalidateQueries({ queryKey: keys.leads });
  };
}

export function useMoveDeal() {
  const invalidate = useInvalidateCrm();
  const toast = useToast();
  const onError = useErrorToast();
  return useMutation({
    mutationFn: ({ deal, stageId, index }: { deal: Deal; stageId: string; index?: number; stageKind?: string }) =>
      unwrap(api.POST("/api/v1/crm/deals/{deal_id}/move", { params: { path: { deal_id: deal.id } }, body: { stage_id: stageId, index } })),
    onSuccess: (moved, variables) => {
      if (moved.status === "won" && variables.deal.status !== "won") {
        fireConfetti();
        toast(`Won: ${moved.title}`);
      }
      invalidate();
    },
    onError: (error) => {
      onError(error);
      invalidate();
    },
  });
}

export function DealDrawer({ deal, pipeline, onClose }: { deal: Deal; pipeline: Pipeline | undefined; onClose: () => void }) {
  const [tab, setTab] = useState<"timeline" | "ai" | "pricing" | "quotes" | "tasks" | "notes" | "details">("timeline");
  const { can } = useSession();
  const writer = can("crm:write");
  const move = useMoveDeal();
  const current = useQuery({
    queryKey: [...keys.deals, "one", deal.id],
    queryFn: () => unwrap(api.GET("/api/v1/crm/deals/{deal_id}", { params: { path: { deal_id: deal.id } } })),
    initialData: deal,
  });
  const data = current.data ?? deal;
  const stages = pipeline?.stages ?? [];
  const stage = stages.find((item) => item.id === data.stage_id);
  const [confirmDialog, ask] = useConfirm();
  const removeDeal = useDelete({
    run: () => unwrap(api.DELETE("/api/v1/crm/deals/{deal_id}", { params: { path: { deal_id: deal.id } } })),
    success: "Deal deleted.",
    invalidate: [keys.deals, keys.crm],
    onDone: onClose,
  });

  return (
    <Drawer
      wide
      footer={
        writer && (
          <>
            <Button
              variant="danger"
              busy={removeDeal.isPending}
              style={{ marginRight: "auto" }}
              onClick={() =>
                void ask({
                  title: `Delete "${data.title}"?`,
                  message: "Its stage history goes with it. Tasks, notes and meetings stay on the contact.",
                }).then((ok) => ok && removeDeal.mutate())
              }
            >
              <Icon name="trash" /> Delete deal
            </Button>
            {confirmDialog}
            <Button onClick={onClose}>Close</Button>
          </>
        )
      }
      title={
        <span className="row">
          {data.title} <StatusBadge value={data.status} />
        </span>
      }
      onClose={onClose}
    >
      <div className="stack">
        <div className="row between">
          <div className="row">
            <LinePill line={data.service_line} />
            {data.origin || data.destination ? (
              <span className="lane-chip">
                {data.origin ?? "?"} <Icon name={LINE_ICONS[asServiceLine(data.service_line)]} /> {data.destination ?? "?"}
              </span>
            ) : null}
            <strong style={{ fontFamily: "var(--display)", fontSize: 18 }}>{formatMoney(data.value_amount, data.currency, false)}</strong>
            <button type="button" className="score-link" onClick={() => setTab("ai")} title="Lead score: open the AI tab">
              <ScoreBadge score={data.score} />
            </button>
          </div>
          <span className="small muted">
            <Link to={`/contacts/${data.contact_id}`}>{data.contact_name}</Link>
            {data.company_name ? ` · ${data.company_name}` : ""} · {stage?.name ?? "stage"} for {daysInStage(data.stage_entered_at)} days
          </span>
        </div>
        <div className="stage-stepper" role="list" aria-label="Stages">
          {stages.map((item) => {
            const reached = stage ? item.position <= stage.position && item.kind === "open" && stage.kind === "open" : false;
            const active = item.id === data.stage_id;
            return (
              <button
                key={item.id}
                type="button"
                role="listitem"
                disabled={!writer || move.isPending || active}
                className={["step", item.kind, active ? "active" : "", reached ? "reached" : ""].filter(Boolean).join(" ")}
                onClick={() => move.mutate({ deal: data, stageId: item.id })}
                title={`${item.name} (${item.probability}%)`}
              >
                {item.kind === "won" && <Icon name="check" />}
                {item.kind === "lost" && <Icon name="x" />}
                {item.name}
              </button>
            );
          })}
        </div>
        <ErrorAlert error={move.error} />
        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            { id: "timeline", label: "Timeline" },
            { id: "ai", label: "AI" },
            { id: "pricing", label: "Pricing" },
            { id: "quotes", label: "Quotes" },
            { id: "tasks", label: "Tasks" },
            { id: "notes", label: "Notes" },
            { id: "details", label: "Details" },
          ]}
        />
        {tab === "timeline" && <DealTimeline dealId={data.id} />}
        {tab === "ai" && <DealAi dealId={data.id} canWrite={writer} />}
        {tab === "pricing" && <DealPricing dealId={data.id} canWrite={writer} />}
        {tab === "quotes" && <DealQuotes dealId={data.id} canWrite={writer} />}
        {tab === "tasks" && <DealTasks dealId={data.id} contactId={data.contact_id} canWrite={writer} />}
        {tab === "notes" && <NotesPanel dealId={data.id} contactId={data.contact_id} canWrite={writer} />}
        {tab === "details" && <DealDetails deal={data} canWrite={writer} />}
      </div>
    </Drawer>
  );
}

function DealTimeline({ dealId }: { dealId: string }) {
  const timeline = useQuery({
    queryKey: [...keys.deals, "timeline", dealId],
    queryFn: () => unwrap(api.GET("/api/v1/crm/deals/{deal_id}/timeline", { params: { path: { deal_id: dealId } } })),
  });
  if (timeline.isLoading) return <SkeletonRows rows={4} />;
  return (
    <div className="timeline">
      <ErrorAlert error={timeline.error} />
      {timeline.data?.map((entry, index) => (
        <div key={`${entry.kind}-${entry.ref_id ?? index}`} className="timeline-item">
          <span className={`timeline-dot ${entry.kind.startsWith("deal.won") ? "green" : entry.kind.startsWith("deal.lost") ? "red" : entry.kind.startsWith("email") ? "blue" : ""}`} />
          <div>
            <div className="row" style={{ gap: 6 }}>
              <Icon name={TIMELINE_ICONS[entry.kind] ?? "events"} style={{ width: 14, height: 14, color: "var(--text-3)" }} />
              <strong>{entry.title}</strong>
              {entry.detail && entry.kind.startsWith("email") && <Badge tone="violet">{entry.detail.replaceAll("_", " ")}</Badge>}
            </div>
            {entry.detail && !entry.kind.startsWith("email") && <div className="small" style={{ whiteSpace: "pre-wrap" }}>{entry.detail}</div>}
            <div className="small muted">
              {formatDateTime(entry.occurred_at)}
              {entry.actor ? ` · ${entry.actor}` : ""}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

export function TaskRow({ task, canWrite }: { task: Schemas["TaskResponse"]; canWrite: boolean }) {
  const invalidate = useInvalidateCrm();
  const onError = useErrorToast();
  const [leaving, setLeaving] = useState(false);
  const toggle = useMutation({
    mutationFn: () => unwrap(api.PATCH("/api/v1/crm/tasks/{task_id}", { params: { path: { task_id: task.id } }, body: { done: !task.done_at } })),
    onSuccess: () => {
      setLeaving(!task.done_at);
      window.setTimeout(invalidate, task.done_at ? 0 : 450);
    },
    onError,
  });
  const overdue = !task.done_at && task.due_at && new Date(task.due_at).getTime() < Date.now();
  return (
    <div className={["task-row", task.done_at ? "done" : "", leaving ? "leaving" : "", overdue ? "overdue" : ""].filter(Boolean).join(" ")}>
      <button type="button" className="task-check" disabled={!canWrite || toggle.isPending} aria-label={task.done_at ? "Reopen" : "Complete"} onClick={() => toggle.mutate()}>
        {task.done_at || leaving ? <Icon name="check" /> : null}
      </button>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div className="task-title">{task.title}</div>
        <div className="small muted">
          {task.due_at ? `${overdue ? "Overdue · " : "Due "}${formatRelative(task.due_at)}` : "No due date"}
          {task.assignee_email ? ` · ${task.assignee_email}` : ""}
          {task.priority === "high" && <Badge tone="red">high</Badge>}
        </div>
      </div>
    </div>
  );
}

function QuickTask({ dealId, contactId, onAdded }: { dealId?: string; contactId?: string; onAdded?: () => void }) {
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const invalidate = useInvalidateCrm();
  const add = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/crm/tasks", {
          body: { title: title.trim(), deal_id: dealId ?? null, contact_id: contactId ?? null, due_at: due ? new Date(`${due}T17:00:00`).toISOString() : null },
        }),
      ),
    onSuccess: () => {
      setTitle("");
      setDue("");
      invalidate();
      onAdded?.();
    },
  });
  return (
    <form
      className="row nowrap"
      onSubmit={(event) => {
        event.preventDefault();
        if (title.trim()) add.mutate();
      }}
    >
      <input className="input" placeholder="Add a task… (e.g. Send the Jebel Ali rates)" value={title} onChange={(event) => setTitle(event.target.value)} />
      <input className="input" type="date" style={{ width: 160 }} value={due} onChange={(event) => setDue(event.target.value)} aria-label="Due date" />
      <Button type="submit" busy={add.isPending} disabled={!title.trim()}>
        <Icon name="plus" /> Add
      </Button>
    </form>
  );
}

function DealTasks({ dealId, contactId, canWrite }: { dealId: string; contactId: string; canWrite: boolean }) {
  const tasks = useTasks({ deal_id: dealId, done: null });
  return (
    <div className="stack">
      {canWrite && <QuickTask dealId={dealId} contactId={contactId} />}
      {tasks.isLoading && <SkeletonRows rows={2} />}
      {tasks.data?.length === 0 && <div className="small muted">No tasks yet.</div>}
      {tasks.data?.map((task) => <TaskRow key={task.id} task={task} canWrite={canWrite} />)}
    </div>
  );
}

export function NotesPanel({ dealId, contactId, canWrite }: { dealId?: string; contactId?: string; canWrite: boolean }) {
  const [body, setBody] = useState("");
  const invalidate = useInvalidateCrm();
  const { session } = useSession();
  const onError = useErrorToast();
  const notes = useQuery({
    queryKey: [...keys.notes, dealId ?? contactId],
    queryFn: () => unwrap(api.GET("/api/v1/crm/notes", { params: { query: dealId ? { deal_id: dealId } : { contact_id: contactId } } })),
  });
  const add = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/crm/notes", { body: { body: body.trim(), deal_id: dealId ?? null, contact_id: contactId ?? null } })),
    onSuccess: () => {
      setBody("");
      invalidate();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/api/v1/crm/notes/{note_id}", { params: { path: { note_id: id } } })),
    onSuccess: invalidate,
    onError,
  });
  return (
    <div className="stack">
      {canWrite && (
        <div className="stack tight">
          <textarea className="textarea" placeholder="Write a note… (rates agreed, carrier preference, next step)" value={body} onChange={(event) => setBody(event.target.value)} />
          <div>
            <Button variant="primary" size="sm" disabled={!body.trim()} busy={add.isPending} onClick={() => add.mutate()}>
              Add note
            </Button>
          </div>
          <ErrorAlert error={add.error} />
        </div>
      )}
      {notes.data?.length === 0 && <div className="small muted">No notes yet.</div>}
      {notes.data?.map((note) => (
        <div key={note.id} className="note">
          <div style={{ whiteSpace: "pre-wrap" }}>{note.body}</div>
          <div className="row between small muted">
            <span>
              {note.author} · {formatRelative(note.created_at)}
            </span>
            {canWrite && (note.author === session?.subject || can(session, "admin:manage")) && (
              <button type="button" className="btn ghost sm" onClick={() => remove.mutate(note.id)}>
                Delete
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function can(session: { scopes: string[] } | null, scope: string): boolean {
  return Boolean(session?.scopes.includes(scope));
}

function DealDetails({ deal, canWrite }: { deal: Deal; canWrite: boolean }) {
  const [form, setForm] = useState({
    title: deal.title,
    value_amount: deal.value_amount === null ? "" : String(deal.value_amount),
    currency: deal.currency,
    service_line: deal.service_line,
    mode: deal.mode ?? "",
    origin: deal.origin ?? "",
    destination: deal.destination ?? "",
    commodity: deal.commodity ?? "",
    owner_email: deal.owner_email ?? "",
    expected_close_date: deal.expected_close_date ?? "",
    incoterm: deal.incoterm ?? "",
    cargo_ready_date: deal.cargo_ready_date ?? "",
    weight_kg: deal.weight_kg === null || deal.weight_kg === undefined ? "" : String(deal.weight_kg),
    volume_cbm: deal.volume_cbm === null || deal.volume_cbm === undefined ? "" : String(deal.volume_cbm),
    pieces: deal.pieces === null || deal.pieces === undefined ? "" : String(deal.pieces),
    equipment: deal.equipment ?? "",
  });
  const catalog = useLogisticsCatalog();
  const modes = catalog.data?.service_lines.find((item) => item.key === form.service_line)?.modes ?? [];
  const invalidate = useInvalidateCrm();
  const toast = useToast();
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PATCH("/api/v1/crm/deals/{deal_id}", {
          params: { path: { deal_id: deal.id } },
          body: {
            title: form.title.trim(),
            value_amount: form.value_amount === "" ? null : (Number(form.value_amount).toFixed(2) as never),
            currency: form.currency.toUpperCase(),
            service_line: form.service_line,
            mode: form.mode || null,
            origin: form.origin || null,
            destination: form.destination || null,
            commodity: form.commodity || null,
            owner_email: form.owner_email || null,
            expected_close_date: form.expected_close_date || null,
            incoterm: form.incoterm || null,
            cargo_ready_date: form.cargo_ready_date || null,
            weight_kg: form.weight_kg === "" ? null : (form.weight_kg as never),
            volume_cbm: form.volume_cbm === "" ? null : (form.volume_cbm as never),
            pieces: form.pieces === "" ? null : Number(form.pieces),
            equipment: form.equipment.trim().toUpperCase() || null,
          },
        }),
      ),
    onSuccess: () => {
      toast("Deal saved.");
      invalidate();
    },
  });
  const text = (name: keyof typeof form) => (event: { target: { value: string } }) => setForm((current) => ({ ...current, [name]: event.target.value }));
  return (
    <div className="stack">
      <div className="form-grid">
        <label className="field"><span>Title</span><input className="input" disabled={!canWrite} value={form.title} onChange={text("title")} /></label>
        <label className="field">
          <span>Value</span>
          <div className="row nowrap">
            <input className="input" type="number" min={0} step="0.01" disabled={!canWrite} value={form.value_amount} onChange={text("value_amount")} />
            <input className="input" style={{ width: 80 }} maxLength={3} disabled={!canWrite} value={form.currency} onChange={text("currency")} aria-label="Currency" />
          </div>
        </label>
        <label className="field">
          <span>Line</span>
          <select className="select" disabled={!canWrite} value={form.service_line} onChange={(event) => setForm((current) => ({ ...current, service_line: event.target.value as Deal["service_line"], mode: "" }))}>
            <ServiceLineOptions any="Any" current={form.service_line} />
          </select>
        </label>
        <label className="field">
          <span>Mode</span>
          <select className="select" disabled={!canWrite} value={form.mode} onChange={text("mode")}>
            <option value="">—</option>
            {modes.map((mode) => <option key={mode.key} value={mode.key}>{mode.label}</option>)}
            {form.mode && !modes.some((mode) => mode.key === form.mode) && <option value={form.mode}>{form.mode}</option>}
          </select>
        </label>
        <label className="field"><span>Origin</span><input className="input" disabled={!canWrite} value={form.origin} onChange={text("origin")} placeholder="YYZ CA" /></label>
        <label className="field"><span>Destination</span><input className="input" disabled={!canWrite} value={form.destination} onChange={text("destination")} placeholder="IST TR" /></label>
        <label className="field"><span>Commodity</span><input className="input" disabled={!canWrite} value={form.commodity} onChange={text("commodity")} /></label>
        <label className="field">
          <span>Incoterm</span>
          <select className="select" disabled={!canWrite} value={form.incoterm} onChange={text("incoterm")}>
            <option value="">—</option>
            {(catalog.data?.incoterms ?? []).map((term) => <option key={term} value={term}>{term}</option>)}
          </select>
        </label>
        <label className="field"><span>Cargo ready</span><input className="input" type="date" disabled={!canWrite} value={form.cargo_ready_date} onChange={text("cargo_ready_date")} /></label>
        <label className="field"><span>Weight (kg)</span><input className="input" type="number" min={0} step="any" disabled={!canWrite} value={form.weight_kg} onChange={text("weight_kg")} /></label>
        <label className="field"><span>Volume (cbm)</span><input className="input" type="number" min={0} step="any" disabled={!canWrite} value={form.volume_cbm} onChange={text("volume_cbm")} /></label>
        <label className="field"><span>Pieces / containers</span><input className="input" type="number" min={0} step={1} disabled={!canWrite} value={form.pieces} onChange={text("pieces")} /></label>
        <label className="field">
          <span>Equipment</span>
          <input className="input" list="equipment-options" maxLength={30} placeholder="40HC" disabled={!canWrite} value={form.equipment} onChange={text("equipment")} />
          <datalist id="equipment-options">
            {["20GP", "40GP", "40HC", "45HC", "20RF", "40RF", "20OT", "40FR", "FTL", "LTL"].map((code) => <option key={code} value={code} />)}
          </datalist>
        </label>
        <label className="field"><span>Owner</span><input className="input" type="email" disabled={!canWrite} value={form.owner_email} onChange={text("owner_email")} /></label>
        <label className="field"><span>Expected close</span><input className="input" type="date" disabled={!canWrite} value={form.expected_close_date} onChange={text("expected_close_date")} /></label>
      </div>
      <div className="small muted">
        Opened {formatDate(deal.created_at)}
        {deal.lead_id ? " from a quote request" : ""}
        {deal.won_at ? ` · won ${formatDate(deal.won_at)}` : ""}
        {deal.lost_at ? ` · lost ${formatDate(deal.lost_at)}${deal.lost_reason ? ` (${deal.lost_reason})` : ""}` : ""}
      </div>
      <ErrorAlert error={save.error} />
      {canWrite && (
        <div>
          <Button variant="primary" busy={save.isPending} disabled={!form.title.trim()} onClick={() => save.mutate()}>
            Save deal
          </Button>
        </div>
      )}
    </div>
  );
}

function DealQuotes({ dealId, canWrite }: { dealId: string; canWrite: boolean }) {
  const [open, setOpen] = useState<string | null>(null);
  const quotes = useQuery({
    queryKey: [...QUOTES_KEY, "deal", dealId],
    queryFn: () => unwrap(api.GET("/api/v1/crm/quotes", { params: { query: { deal_id: dealId } } })),
  });
  const start = useStartQuote((quote) => setOpen(quote.id));
  const items = quotes.data ?? [];
  return (
    <div className="stack">
      <ErrorAlert error={quotes.error} />
      {items.length === 0 && !quotes.isLoading ? (
        <div className="alert info small">No quotes yet. Build one with charge lines for the mode, preview it and send it in the customer&apos;s thread.</div>
      ) : (
        <div className="quote-list">
          {items.map((quote) => (
            <button key={quote.id} type="button" className="quote-row" onClick={() => setOpen(quote.id)}>
              <Icon name="quote" />
              <span className="grow">
                <strong>{quote.number}</strong> <span className="muted">{quote.title}</span>
              </span>
              <strong style={{ fontVariantNumeric: "tabular-nums" }}>
                {quote.total_amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} {quote.currency}
              </strong>
              <QuoteStatusBadge quote={quote} />
            </button>
          ))}
        </div>
      )}
      {canWrite && (
        <div>
          <Button variant="primary" busy={start.isPending} onClick={() => start.mutate(dealId)}>
            <Icon name="plus" /> New quote
          </Button>
        </div>
      )}
      {open && <QuoteBuilder quoteId={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

export { QuickTask };
