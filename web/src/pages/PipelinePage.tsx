/**
 * The pipeline board: every deal as a card in its stage. Drag a card to another column (or
 * another place in the same column) to move it; dropping on Won celebrates.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys, useCrmSummary, useDeals, usePipelines } from "../api/queries";
import { useSession } from "../auth/session";
import { ContactPicker } from "../components/ContactPicker";
import { Icon } from "../components/icons";
import { Page } from "../components/Layout";
import { CountUp, SkeletonRows } from "../components/motion";
import { Button, ErrorAlert, Modal, PageHeader, useToast } from "../components/ui";
import { buildColumns, daysInStage, dropIndex, formatMoney, moveLocally } from "../crm/board";
import { ScoreBadge } from "../crm/DealAi";
import { DealDrawer, useMoveDeal } from "../crm/DealDrawer";
import { initials } from "../lib/freight";
import { ServiceLineOptions } from "../components/pickers";
import { LINE_ICONS, asServiceLine } from "../lib/freight";
import type { ServiceLine } from "../lib/freight";

type Deal = Schemas["DealResponse"];
type Pipeline = Schemas["CrmPipelineResponse"];
type LineFilter = "all" | ServiceLine;

export function PipelinePage() {
  const pipelines = usePipelines();
  const [params, setParams] = useSearchParams();
  const { session, can } = useSession();
  const writer = can("crm:write");
  const [line, setLine] = useState<LineFilter>("all");
  const [mine, setMine] = useState(false);
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(params.get("new") === "1");
  const [editingStages, setEditingStages] = useState(false);
  const pipelineId = params.get("pipeline") ?? pipelines.data?.find((item) => item.is_default)?.id ?? pipelines.data?.[0]?.id ?? "";
  const pipeline = pipelines.data?.find((item) => item.id === pipelineId);
  const query = {
    pipeline_id: pipelineId || undefined,
    service_line: line === "all" ? undefined : line,
    owner_email: mine ? session?.subject : undefined,
    search: search.trim() || undefined,
  };
  const deals = useDeals(query);
  const summary = useCrmSummary(30);
  const [openId, setOpenId] = useState<string | null>(params.get("deal"));
  const open = deals.data?.find((deal) => deal.id === openId);

  return (
    <Page>
      <PageHeader
        eyebrow={<><Icon name="leads" /> CRM</>}
        title="Pipeline"
        description="Every quote request becomes a deal. Drag cards through the stages; won and lost close the deal and start the workflows listening to them."
        actions={
          <>
            {(pipelines.data?.length ?? 0) > 1 && (
              <select className="select" style={{ width: 200 }} value={pipelineId} onChange={(event) => setParams({ pipeline: event.target.value })} aria-label="Pipeline">
                {pipelines.data?.map((item) => (
                  <option key={item.id} value={item.id}>{item.name}</option>
                ))}
              </select>
            )}
            {can("admin:manage") && (
              <Button onClick={() => setEditingStages(true)} disabled={!pipeline}>
                <Icon name="settings" /> Stages
              </Button>
            )}
            {writer && (
              <Button variant="primary" onClick={() => setCreating(true)} disabled={!pipeline}>
                <Icon name="plus" /> New deal
              </Button>
            )}
          </>
        }
      />
      <div className="grid stats" style={{ marginBottom: 16 }}>
        <div className="card stat lift">
          <div className="stat-label"><Icon name="leads" /> Open deals</div>
          <div className="stat-value"><CountUp value={summary.data?.open_deals ?? 0} /></div>
        </div>
        <div className="card stat lift">
          <div className="stat-label"><Icon name="trending" /> Open pipeline</div>
          <div className="stat-value">{formatMoney(summary.data?.open_value ?? 0)}</div>
          <div className="small muted">{formatMoney(summary.data?.weighted_value ?? 0)} weighted by stage</div>
        </div>
        <div className="card stat lift">
          <div className="stat-label"><Icon name="check" /> Won (30 days)</div>
          <div className="stat-value"><CountUp value={summary.data?.won_in_period ?? 0} /></div>
          <div className="small muted">{formatMoney(summary.data?.won_value_in_period ?? 0)}</div>
        </div>
        <div className="card stat lift">
          <div className="stat-label"><Icon name="clock" /> Tasks due today</div>
          <div className="stat-value"><CountUp value={summary.data?.tasks_due_today ?? 0} /></div>
          <div className="small muted" style={{ color: summary.data?.tasks_overdue ? "var(--danger)" : undefined }}>{summary.data?.tasks_overdue ?? 0} overdue</div>
        </div>
      </div>
      <div className="row" style={{ marginBottom: 12 }}>
        <select className="select" style={{ maxWidth: 190 }} aria-label="Service line" value={line} onChange={(event) => setLine(event.target.value as LineFilter)}>
          <option value="all">All services</option>
          <ServiceLineOptions current={line} />
        </select>
        <label className="checkbox small">
          <input type="checkbox" checked={mine} onChange={(event) => setMine(event.target.checked)} /> Only my deals
        </label>
        <input className="input" style={{ width: 240 }} placeholder="Find a deal, lane or port…" value={search} onChange={(event) => setSearch(event.target.value)} aria-label="Find a deal" />
      </div>
      {(pipelines.isLoading || deals.isLoading) && <SkeletonRows rows={4} />}
      <ErrorAlert error={pipelines.error ?? deals.error} />
      {pipeline && deals.data && <Board pipeline={pipeline} deals={deals.data} query={query} canWrite={writer} onOpen={setOpenId} />}
      {open && <DealDrawer deal={open} pipeline={pipeline} onClose={() => setOpenId(null)} />}
      {creating && pipeline && <NewDealModal pipeline={pipeline} onClose={() => setCreating(false)} onCreated={(deal) => setOpenId(deal.id)} />}
      {editingStages && pipeline && <StagesModal pipeline={pipeline} onClose={() => setEditingStages(false)} />}
    </Page>
  );
}

function Board({
  pipeline,
  deals,
  query,
  canWrite,
  onOpen,
}: {
  pipeline: Pipeline;
  deals: Deal[];
  query: object;
  canWrite: boolean;
  onOpen: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const move = useMoveDeal();
  const columns = buildColumns(pipeline.stages, deals);
  const [dragging, setDragging] = useState<string | null>(null);
  const [target, setTarget] = useState<{ stageId: string; index: number } | null>(null);
  const columnRefs = useRef(new Map<string, HTMLDivElement>());

  const indexFor = (stageId: string, pointerY: number) => {
    const element = columnRefs.current.get(stageId);
    const cards = [...(element?.querySelectorAll<HTMLElement>("[data-deal-id]") ?? [])].map((card) => {
      const box = card.getBoundingClientRect();
      return { id: card.dataset.dealId ?? "", top: box.top, height: box.height };
    });
    return dropIndex(cards, pointerY, dragging ?? "");
  };

  const drop = (event: DragEvent, stageId: string) => {
    event.preventDefault();
    const dealId = event.dataTransfer.getData("text/lanereach-deal") || dragging;
    setDragging(null);
    setTarget(null);
    const deal = deals.find((item) => item.id === dealId);
    if (!deal || !canWrite) return;
    const index = indexFor(stageId, event.clientY);
    // Optimistic: the card jumps to its new place right away.
    queryClient.setQueryData<Deal[]>([...keys.deals, query], (current) => (current ? moveLocally(current, deal.id, stageId, index) : current));
    move.mutate({ deal, stageId, index });
  };

  return (
    <div className="board-scroll">
      {columns.map((column) => (
        <div
          key={column.stage.id}
          className={["kanban-col", column.stage.kind, target?.stageId === column.stage.id ? "drop-target" : ""].filter(Boolean).join(" ")}
          onDragOver={(event) => {
            if (!dragging) return;
            event.preventDefault();
            const index = indexFor(column.stage.id, event.clientY);
            if (target?.stageId !== column.stage.id || target.index !== index) setTarget({ stageId: column.stage.id, index });
          }}
          onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setTarget(null);
          }}
          onDrop={(event) => drop(event, column.stage.id)}
        >
          <div className="kanban-head">
            <div className="row between nowrap">
              <strong className="truncate">{column.stage.name}</strong>
              <span className="kanban-count">{column.count}</span>
            </div>
            <div className="small muted">
              {formatMoney(column.value)}
              {column.stage.kind === "open" ? ` · ${column.stage.probability}%` : ""}
            </div>
            <div className="kanban-bar" style={{ width: `${column.stage.kind === "open" ? column.stage.probability : 100}%` }} />
          </div>
          <div className="kanban-cards" ref={(element) => { if (element) columnRefs.current.set(column.stage.id, element); }}>
            {column.deals.map((deal, index) => (
              <div key={deal.id}>
                {target?.stageId === column.stage.id && target.index === index && dragging !== deal.id && <div className="drop-slot" />}
                <DealCard
                  deal={deal}
                  dragging={dragging === deal.id}
                  draggable={canWrite}
                  onDragStart={(event) => {
                    event.dataTransfer.setData("text/lanereach-deal", deal.id);
                    event.dataTransfer.effectAllowed = "move";
                    setDragging(deal.id);
                  }}
                  onDragEnd={() => {
                    setDragging(null);
                    setTarget(null);
                  }}
                  onOpen={() => onOpen(deal.id)}
                />
              </div>
            ))}
            {target?.stageId === column.stage.id && target.index >= column.deals.filter((deal) => deal.id !== dragging).length && <div className="drop-slot" />}
            {column.deals.length === 0 && !target && <div className="kanban-empty">Drop deals here</div>}
          </div>
        </div>
      ))}
    </div>
  );
}

function DealCard({
  deal,
  dragging,
  draggable,
  onDragStart,
  onDragEnd,
  onOpen,
}: {
  deal: Deal;
  dragging: boolean;
  draggable: boolean;
  onDragStart: (event: DragEvent) => void;
  onDragEnd: () => void;
  onOpen: () => void;
}) {
  const days = daysInStage(deal.stage_entered_at);
  return (
    <div
      className={dragging ? "deal-card dragging" : "deal-card"}
      data-deal-id={deal.id}
      draggable={draggable}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onClick={onOpen}
      onKeyDown={(event) => (event.key === "Enter" || event.key === " ") && onOpen()}
      role="button"
      tabIndex={0}
      aria-label={`${deal.title}, ${formatMoney(deal.value_amount, deal.currency, false)}`}
    >
      <div className="row between nowrap" style={{ gap: 6 }}>
        <strong className="deal-title">{deal.title}</strong>
        {deal.owner_email && (
          <span className={`avatar ${deal.service_line}`} style={{ width: 24, height: 24, borderRadius: 8, fontSize: 10 }} title={deal.owner_email}>
            {initials(deal.owner_email.split("@")[0]?.replace(/[._-]/g, " ") ?? "?")}
          </span>
        )}
      </div>
      <div className="row between nowrap" style={{ gap: 6 }}>
        <span className="small muted truncate">{deal.company_name ?? deal.contact_name}</span>
        <ScoreBadge score={deal.score} />
      </div>
      {(deal.origin || deal.destination) && (
        <span className="lane-chip small">
          {deal.origin ?? "?"} <Icon name={LINE_ICONS[asServiceLine(deal.service_line)]} /> {deal.destination ?? "?"}
        </span>
      )}
      <div className="row between small">
        <strong>{formatMoney(deal.value_amount, deal.currency)}</strong>
        <span className={days > 14 && deal.status === "open" ? "stale" : "muted"} title="Days in this stage">
          <Icon name="clock" style={{ width: 12, height: 12, verticalAlign: "-2px" }} /> {days}d
        </span>
      </div>
    </div>
  );
}

function NewDealModal({ pipeline, onClose, onCreated }: { pipeline: Pipeline; onClose: () => void; onCreated: (deal: Deal) => void }) {
  const [contactId, setContactId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [value, setValue] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [line, setLine] = useState<ServiceLine>("any");
  const [origin, setOrigin] = useState("");
  const [destination, setDestination] = useState("");
  const [stageId, setStageId] = useState(pipeline.stages[0]?.id ?? "");
  const queryClient = useQueryClient();
  const toast = useToast();
  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/crm/deals", {
          body: {
            contact_id: contactId ?? "",
            title: title.trim(),
            pipeline_id: pipeline.id,
            stage_id: stageId || null,
            value_amount: value === "" ? null : (Number(value).toFixed(2) as never),
            currency,
            service_line: line,
            origin: origin || null,
            destination: destination || null,
          },
        }),
      ),
    onSuccess: (deal) => {
      toast(`Deal created: ${deal.title}`);
      void queryClient.invalidateQueries({ queryKey: keys.crm });
      onClose();
      onCreated(deal);
    },
  });
  return (
    <Modal
      title="New deal"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={create.isPending} disabled={!contactId || !title.trim()} onClick={() => create.mutate()}>
            Create deal
          </Button>
        </>
      }
    >
      <div className="stack">
        <ContactPicker value={contactId} onChange={setContactId} />
        <label className="field"><span>Title</span><input className="input" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="IST Cargo: YYZ → IST monthly air" /></label>
        <div className="form-grid">
          <label className="field">
            <span>Value</span>
            <div className="row nowrap">
              <input className="input" type="number" min={0} step="0.01" value={value} onChange={(event) => setValue(event.target.value)} />
              <input className="input" style={{ width: 80 }} maxLength={3} value={currency} onChange={(event) => setCurrency(event.target.value.toUpperCase())} aria-label="Currency" />
            </div>
          </label>
          <label className="field">
            <span>Line</span>
            <select className="select" value={line} onChange={(event) => setLine(event.target.value as ServiceLine)}>
              <ServiceLineOptions any="Any" current={line} />
            </select>
          </label>
          <label className="field"><span>Origin</span><input className="input" value={origin} onChange={(event) => setOrigin(event.target.value)} placeholder="YYZ" /></label>
          <label className="field"><span>Destination</span><input className="input" value={destination} onChange={(event) => setDestination(event.target.value)} placeholder="IST" /></label>
          <label className="field">
            <span>Stage</span>
            <select className="select" value={stageId} onChange={(event) => setStageId(event.target.value)}>
              {pipeline.stages.map((stage) => (
                <option key={stage.id} value={stage.id}>{stage.name}</option>
              ))}
            </select>
          </label>
        </div>
        <ErrorAlert error={create.error} />
      </div>
    </Modal>
  );
}

interface StageDraft {
  key: string;
  id?: string;
  name: string;
  kind: "open" | "won" | "lost";
  probability: number;
}

function StagesModal({ pipeline, onClose }: { pipeline: Pipeline; onClose: () => void }) {
  const [name, setName] = useState(pipeline.name);
  const [stages, setStages] = useState<StageDraft[]>(pipeline.stages.map((stage) => ({ key: stage.id, id: stage.id, name: stage.name, kind: stage.kind, probability: stage.probability })));
  const queryClient = useQueryClient();
  const toast = useToast();
  useEffect(() => setName(pipeline.name), [pipeline.name]);
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/api/v1/crm/pipelines/{pipeline_id}", {
          params: { path: { pipeline_id: pipeline.id } },
          body: { name: name.trim(), stages: stages.map(({ id, name: stageName, kind, probability }) => ({ id: id ?? null, name: stageName.trim(), kind, probability })) },
        }),
      ),
    onSuccess: () => {
      toast("Stages saved.");
      void queryClient.invalidateQueries({ queryKey: keys.crm });
      onClose();
    },
  });
  const update = (key: string, change: Partial<StageDraft>) => setStages((current) => current.map((stage) => (stage.key === key ? { ...stage, ...change } : stage)));
  const shift = (index: number, by: number) =>
    setStages((current) => {
      const next = [...current];
      const [item] = next.splice(index, 1);
      if (item) next.splice(Math.max(0, Math.min(next.length, index + by)), 0, item);
      return next;
    });
  const valid = useMemo(
    () => name.trim() && stages.every((stage) => stage.name.trim()) && stages.some((stage) => stage.kind === "won") && stages.some((stage) => stage.kind === "lost"),
    [name, stages],
  );
  return (
    <Modal
      title="Pipeline stages"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={save.isPending} disabled={!valid} onClick={() => save.mutate()}>
            Save stages
          </Button>
        </>
      }
    >
      <div className="stack">
        <label className="field"><span>Pipeline name</span><input className="input" value={name} onChange={(event) => setName(event.target.value)} /></label>
        {stages.map((stage, index) => (
          <div key={stage.key} className="stage-edit">
            <div className="stack tight" style={{ gap: 2 }}>
              <button type="button" className="btn ghost sm icon" disabled={index === 0} onClick={() => shift(index, -1)} aria-label="Move up">▲</button>
              <button type="button" className="btn ghost sm icon" disabled={index === stages.length - 1} onClick={() => shift(index, 1)} aria-label="Move down">▼</button>
            </div>
            <input className="input" value={stage.name} onChange={(event) => update(stage.key, { name: event.target.value })} aria-label="Stage name" />
            <select className="select" style={{ width: 110 }} value={stage.kind} onChange={(event) => update(stage.key, { kind: event.target.value as StageDraft["kind"] })} aria-label="Kind">
              <option value="open">Open</option>
              <option value="won">Won</option>
              <option value="lost">Lost</option>
            </select>
            <input className="input" type="number" min={0} max={100} style={{ width: 80 }} value={stage.probability} onChange={(event) => update(stage.key, { probability: Number(event.target.value) || 0 })} aria-label="Win probability %" />
            <button type="button" className="btn ghost sm icon" disabled={stages.length <= 2} onClick={() => setStages((current) => current.filter((item) => item.key !== stage.key))} aria-label="Remove stage">✕</button>
          </div>
        ))}
        <div>
          <Button size="sm" disabled={stages.length >= 12} onClick={() => setStages((current) => [...current.slice(0, -2), { key: crypto.randomUUID(), name: "New stage", kind: "open", probability: 40 }, ...current.slice(-2)])}>
            <Icon name="plus" /> Add stage
          </Button>
        </div>
        <span className="field-help">Probability weights the pipeline value. A stage with deals cannot be removed; move its deals first.</span>
        <ErrorAlert error={save.error} />
      </div>
    </Modal>
  );
}
