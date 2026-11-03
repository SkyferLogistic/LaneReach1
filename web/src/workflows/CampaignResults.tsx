/**
 * Campaign results and schedule: the funnel of a workflow (enrolled → emailed → opened →
 * clicked → replied → quote requests → deals → won), per-step counts, top links, and the
 * dates and weekdays the campaign may send on.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, describeError, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys } from "../api/queries";
import { useSession } from "../auth/session";
import { Icon } from "../components/icons";
import type { IconName } from "../components/icons";
import { CountUp, SkeletonRows } from "../components/motion";
import { Badge, Button, Drawer, Empty, ErrorAlert, useToast } from "../components/ui";
import { humanize, percent } from "../lib/format";

type Stats = Schemas["CampaignStatsResponse"];
type Workflow = Schemas["WorkflowSummaryResponse"];
type Schedule = Schemas["CampaignSchedule"];

export function useCampaignStats(workflowId: string, enabled = true) {
  return useQuery({
    queryKey: [...keys.workflow(workflowId), "stats"],
    queryFn: () =>
      unwrap(api.GET("/api/v1/automation/workflows/{workflow_id}/stats", { params: { path: { workflow_id: workflowId } } })),
    enabled,
    staleTime: 30_000,
  });
}

/** Compact numbers for the campaign list. */
export function CampaignNumbers({ workflowId, live }: { workflowId: string; live: boolean }) {
  const stats = useCampaignStats(workflowId, live);
  if (!live) return <span className="muted">—</span>;
  if (!stats.data) return <span className="muted small">…</span>;
  const data = stats.data;
  if (data.sent === 0) return <span className="small muted">{data.enrolled ? `${data.enrolled} enrolled` : "Not started"}</span>;
  return (
    <span className="campaign-numbers small">
      <span title="Emails sent">
        <Icon name="outbox" /> {data.sent}
      </span>
      {data.tracked > 0 && (
        <>
          <span title="Opened (people, not scanners)">
            <Icon name="eye" /> {percent(data.open_rate)}
          </span>
          <span title="Clicked">
            <Icon name="external" /> {percent(data.click_rate)}
          </span>
        </>
      )}
      <span title="Contacts who replied">
        <Icon name="reply" /> {data.replied}
      </span>
      {data.leads > 0 && (
        <span title="Quote requests">
          <Icon name="leads" /> {data.leads}
        </span>
      )}
    </span>
  );
}

interface FunnelStage {
  label: string;
  icon: IconName;
  value: number;
  color: string;
}

function funnel(stats: Stats): FunnelStage[] {
  return [
    { label: "Enrolled", icon: "enrollments", value: stats.enrolled, color: "var(--accent)" },
    { label: "Emailed", icon: "outbox", value: stats.contacts_emailed, color: "var(--info)" },
    { label: "Opened", icon: "eye", value: stats.opened, color: "var(--ocean)" },
    { label: "Clicked", icon: "external", value: stats.clicked, color: "var(--violet)" },
    { label: "Replied", icon: "reply", value: stats.replied, color: "var(--success)" },
    { label: "Positive", icon: "sparkles", value: stats.positive, color: "var(--success)" },
    { label: "Quote requests", icon: "leads", value: stats.leads, color: "var(--air)" },
    { label: "Deals won", icon: "trending", value: stats.won, color: "var(--air)" },
  ];
}

export function ResultsDrawer({ workflow, onClose }: { workflow: Workflow; onClose: () => void }) {
  const stats = useCampaignStats(workflow.id);
  const { can } = useSession();
  return (
    <Drawer title={`Results · ${workflow.name}`} onClose={onClose} wide>
      <div className="stack">
        <ErrorAlert error={stats.error} />
        {stats.isLoading && <SkeletonRows rows={6} />}
        {stats.data && <ResultsBody stats={stats.data} />}
        <ScheduleEditor workflow={workflow} canEdit={can("workflows:publish") && workflow.status !== "archived"} />
      </div>
    </Drawer>
  );
}

function ResultsBody({ stats }: { stats: Stats }) {
  const stages = funnel(stats);
  const top = Math.max(1, ...stages.map((stage) => stage.value));
  return (
    <>
      <div className="grid four">
        <Kpi label="Emails sent" value={stats.sent} />
        <Kpi label="Open rate" value={stats.tracked ? percent(stats.open_rate) : "—"} hint={stats.tracked ? `${stats.opened} of ${stats.tracked} tracked` : "No tracked emails"} />
        <Kpi label="Click rate" value={stats.tracked ? percent(stats.click_rate) : "—"} hint={`${stats.clicked} clicked`} />
        <Kpi label="Reply rate" value={percent(stats.reply_rate)} hint={`${stats.replied} contacts, ${stats.positive} positive`} />
      </div>
      <section>
        <h3 className="section-title">Funnel</h3>
        <div className="funnel">
          {stages.map((stage, index) => (
            <div key={stage.label} className="funnel-row">
              <span className="funnel-label">
                <Icon name={stage.icon} style={{ color: stage.color }} /> {stage.label}
              </span>
              <div className="funnel-track">
                <span className="funnel-bar" style={{ width: `${Math.max((stage.value / top) * 100, stage.value ? 4 : 0)}%`, background: stage.color, animationDelay: `${index * 70}ms` }} />
              </div>
              <strong className="funnel-value">
                <CountUp value={stage.value} />
              </strong>
              <span className="funnel-conv small muted">
                {index > 0 && (stages[index - 1]?.value ?? 0) > 0 ? percent(stage.value / (stages[index - 1]?.value ?? 1)) : ""}
              </span>
            </div>
          ))}
        </div>
        <div className="chips" style={{ marginTop: 10 }}>
          {Object.entries(stats.by_status)
            .filter(([, count]) => count > 0)
            .map(([status, count]) => (
              <Badge key={status}>
                {humanize(status)}: {count}
              </Badge>
            ))}
          {stats.bounced > 0 && <Badge tone="red">Bounced: {stats.bounced}</Badge>}
          {stats.unsubscribed > 0 && <Badge tone="amber">Unsubscribed: {stats.unsubscribed}</Badge>}
        </div>
      </section>
      <section>
        <h3 className="section-title">Steps</h3>
        {stats.steps.length === 0 ? (
          <Empty title="No steps ran yet" />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Step</th>
                  <th>Enrollments</th>
                  <th>Branches taken</th>
                </tr>
              </thead>
              <tbody>
                {stats.steps.map((step) => (
                  <tr key={step.node_id}>
                    <td>
                      <strong>{step.node_id}</strong>
                      <div className="small muted">{humanize(step.node_type)}</div>
                    </td>
                    <td>{step.enrollments}</td>
                    <td>
                      <div className="chips">
                        {Object.entries(step.handles)
                          .filter(([handle]) => handle !== "next")
                          .map(([handle, count]) => (
                            <Badge key={handle}>
                              {humanize(handle)}: {count}
                            </Badge>
                          ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {stats.links.length > 0 && (
        <section>
          <h3 className="section-title">Top links</h3>
          <ul className="link-list">
            {stats.links.map((link) => (
              <li key={link.url}>
                <a href={link.url} target="_blank" rel="noreferrer noopener" className="truncate">
                  {link.url}
                </a>
                <span className="small muted">
                  {link.clicks} clicks · {link.contacts} contacts
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function Kpi({ label, value, hint }: { label: string; value: number | string; hint?: string }) {
  return (
    <div className="card kpi-mini">
      <span className="small muted">{label}</span>
      <strong>{typeof value === "number" ? <CountUp value={value} /> : value}</strong>
      {hint && <span className="small muted">{hint}</span>}
    </div>
  );
}

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function ScheduleEditor({ workflow, canEdit }: { workflow: Workflow; canEdit: boolean }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const current = workflow.schedule ?? null;
  const [start, setStart] = useState(current?.start_date ?? "");
  const [end, setEnd] = useState(current?.end_date ?? "");
  const [weekdays, setWeekdays] = useState<number[]>(current?.weekdays ?? [1, 2, 3, 4, 5]);
  const save = useMutation({
    mutationFn: (schedule: Schedule | null) =>
      unwrap(
        api.PUT("/api/v1/automation/workflows/{workflow_id}/schedule", {
          params: { path: { workflow_id: workflow.id } },
          body: schedule as never,
        }),
      ),
    onSuccess: (next) => {
      toast(next.schedule ? "Campaign schedule saved." : "Schedule cleared: the campaign sends whenever the guardrails allow.");
      void queryClient.invalidateQueries({ queryKey: keys.workflows });
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  const toggle = (day: number) =>
    setWeekdays((days) => (days.includes(day) ? days.filter((item) => item !== day) : [...days, day].sort()));
  return (
    <section className="card" style={{ padding: 16 }}>
      <div className="row between" style={{ flexWrap: "wrap", gap: 8 }}>
        <h3 className="section-title" style={{ margin: 0 }}>
          <Icon name="clock" /> Schedule
        </h3>
        {current ? <Badge tone="blue">Scheduled</Badge> : <Badge>Always on</Badge>}
      </div>
      <p className="small muted">
        Emails go out only between these dates, on these weekdays (recipient's local day), and still inside the sending window
        and business days. After the end date the campaign's emails are blocked and its enrollments stop. Replies to contacts who
        answered are never held.
      </p>
      <div className="form-grid">
        <label className="field">
          <span>Start</span>
          <input className="input" type="date" value={start} disabled={!canEdit} onChange={(event) => setStart(event.target.value)} />
        </label>
        <label className="field">
          <span>End</span>
          <input className="input" type="date" value={end} min={start || undefined} disabled={!canEdit} onChange={(event) => setEnd(event.target.value)} />
        </label>
      </div>
      <div className="chips" role="group" aria-label="Weekdays" style={{ margin: "10px 0" }}>
        {DAYS.map((label, index) => (
          <button
            key={label}
            type="button"
            className={weekdays.includes(index + 1) ? "chip-toggle on" : "chip-toggle"}
            aria-pressed={weekdays.includes(index + 1)}
            disabled={!canEdit}
            onClick={() => toggle(index + 1)}
          >
            {label}
          </button>
        ))}
      </div>
      {canEdit && (
        <div className="row" style={{ gap: 8 }}>
          <Button
            variant="primary"
            size="sm"
            busy={save.isPending}
            disabled={weekdays.length === 0}
            onClick={() => save.mutate({ start_date: start || null, end_date: end || null, weekdays })}
          >
            Save schedule
          </Button>
          {current && (
            <Button size="sm" variant="ghost" busy={save.isPending} onClick={() => save.mutate(null)}>
              Clear
            </Button>
          )}
        </div>
      )}
    </section>
  );
}
