/** Insight cards: funnel, reply heatmap, live activity feed and the launch checklist. */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys, useActivity, useBranding, useCountries, useLibrarySettings, useScriptMatrix, useSenders } from "../api/queries";
import { countryFlag } from "../lib/freight";
import { formatRelative, percent } from "../lib/format";
import { heatOpacity, stageConversion } from "../lib/charts";
import { launchSteps } from "../lib/launch";
import { Donut } from "./charts";
import { Icon } from "./icons";
import type { IconName } from "./icons";
import { CountUp, SkeletonRows } from "./motion";
import { BucketBadge, Card, Empty } from "./ui";

const STAGES: { key: keyof Schemas["FunnelResponse"]; label: string; icon: IconName; color: string }[] = [
  { key: "contacted", label: "Contacted", icon: "outbox", color: "var(--accent)" },
  { key: "replied", label: "Replied", icon: "reply", color: "var(--info)" },
  { key: "interested", label: "Interested", icon: "sparkles", color: "var(--brand-1)" },
  { key: "leads", label: "Pricing leads", icon: "leads", color: "var(--violet)" },
  { key: "won", label: "Won", icon: "check", color: "var(--success)" },
];

export function FunnelCard({ funnel, days }: { funnel: Schemas["FunnelResponse"] | undefined; days: number }) {
  const values = STAGES.map((stage) => funnel?.[stage.key] ?? 0);
  const top = Math.max(values[0] ?? 0, 1);
  const conversions = stageConversion(values);
  return (
    <Card title="Outreach funnel" actions={<span className="small muted">contacts · {days} days</span>}>
      {!funnel ? (
        <SkeletonRows rows={4} />
      ) : (
        <div className="funnel">
          {STAGES.map((stage, index) => {
            const value = values[index] ?? 0;
            const conversion = conversions[index];
            return (
              <div key={stage.key} className="funnel-row">
                <span className="funnel-label">
                  <Icon name={stage.icon} style={{ color: stage.color }} /> {stage.label}
                </span>
                <div className="funnel-track">
                  <span
                    className="funnel-bar"
                    style={{ width: `${Math.max((value / top) * 100, value ? 4 : 0)}%`, background: stage.color, transitionDelay: `${index * 90}ms` }}
                  />
                </div>
                <strong className="funnel-value">
                  <CountUp value={value} />
                </strong>
                <span className="funnel-conv small muted">{conversion === null || conversion === undefined ? "" : percent(conversion)}</span>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function ReplyHeatmap({ insights }: { insights: Schemas["InsightsResponse"] | undefined }) {
  const cells = new Map((insights?.heatmap ?? []).map((cell) => [`${cell.weekday}-${cell.hour}`, cell.replies]));
  const max = Math.max(0, ...(insights?.heatmap ?? []).map((cell) => cell.replies));
  const best = insights?.best_slot;
  return (
    <Card
      title="When replies arrive"
      actions={
        best ? (
          <span className="badge green">
            <Icon name="clock" /> Best: {WEEKDAYS[best.weekday - 1]} {String(best.hour).padStart(2, "0")}:00
          </span>
        ) : undefined
      }
    >
      {!insights ? (
        <SkeletonRows rows={3} />
      ) : max === 0 ? (
        <Empty title="No replies yet">The heatmap fills in as replies arrive; it shows the sending office's local time.</Empty>
      ) : (
        <div className="stack tight">
          <div className="heatmap">
            <span />
            {Array.from({ length: 24 }, (_, hour) => (
              <span key={hour} className="hour">{hour % 3 === 0 ? hour : ""}</span>
            ))}
            {WEEKDAYS.map((day, index) => (
              <div key={day} style={{ display: "contents" }}>
                <span className="day">{day}</span>
                {Array.from({ length: 24 }, (_, hour) => {
                  const value = cells.get(`${index + 1}-${hour}`) ?? 0;
                  return (
                    <span
                      key={hour}
                      className="heat"
                      title={`${day} ${String(hour).padStart(2, "0")}:00 — ${value} repl${value === 1 ? "y" : "ies"}`}
                      style={{ "--heat": heatOpacity(value, max), animationDelay: `${(index * 24 + hour) * 4}ms` } as React.CSSProperties}
                    />
                  );
                })}
              </div>
            ))}
          </div>
          <span className="small muted">Sending office's local time. Schedule follow-ups just before the busy slots.</span>
        </div>
      )}
    </Card>
  );
}

const ACTIVITY: Record<Schemas["ActivityItemResponse"]["kind"], { icon: IconName; tone: string }> = {
  sent: { icon: "outbox", tone: "var(--accent)" },
  reply: { icon: "reply", tone: "var(--brand-1)" },
  bounce: { icon: "alert", tone: "var(--danger)" },
  lead: { icon: "leads", tone: "var(--violet)" },
};

function describe(item: Schemas["ActivityItemResponse"]) {
  const who = item.contact_name || item.contact_email || "a contact";
  const flag = countryFlag(item.country);
  switch (item.kind) {
    case "sent":
      return (
        <>
          <strong>{item.agent_name ?? "An agent"}</strong> emailed <strong>{who}</strong> {flag}
        </>
      );
    case "reply":
      return (
        <>
          <strong>{who}</strong> {flag} replied {item.bucket && <BucketBadge bucket={item.bucket} />}
        </>
      );
    case "bounce":
      return (
        <>
          Email to <strong>{who}</strong> {flag} bounced
        </>
      );
    default:
      return (
        <>
          New lead from <strong>{who}</strong> {flag}
        </>
      );
  }
}

export function ActivityFeed() {
  const activity = useActivity(14);
  // Items not seen on the previous poll slide in.
  const seen = useRef<Set<string> | null>(null);
  const fresh = new Set<string>();
  if (activity.data && seen.current) {
    for (const item of activity.data) if (!seen.current.has(item.id)) fresh.add(item.id);
  }
  useEffect(() => {
    if (activity.data) seen.current = new Set(activity.data.map((item) => item.id));
  }, [activity.data]);
  return (
    <Card
      title={
        <span className="row">
          <h2>Live activity</h2> <span className="dot live" title="Updates every 15 seconds" />
        </span>
      }
      flush
    >
      {activity.isLoading && <SkeletonRows rows={5} />}
      {activity.data?.length === 0 && <Empty title="Nothing yet">Emails, replies and leads show up here as they happen.</Empty>}
      <ul className="feed">
        {activity.data?.map((item) => {
          const style = ACTIVITY[item.kind];
          return (
            <li key={`${item.kind}-${item.id}`} className={fresh.has(item.id) ? "feed-item feed-new" : "feed-item"}>
              <span className="feed-icon" style={{ background: style.tone }}>
                <Icon name={style.icon} />
              </span>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="feed-text">
                  {item.contact_id ? <Link to={`/contacts/${item.contact_id}`} className="plain">{describe(item)}</Link> : describe(item)}
                </div>
                <div className="small muted truncate">{item.title}</div>
              </div>
              <span className="small muted nowrap">{formatRelative(item.occurred_at)}</span>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

export function LaunchChecklist({ publishedWorkflows }: { publishedWorkflows: number | undefined }) {
  const library = useLibrarySettings();
  const agents = useSenders();
  const matrix = useScriptMatrix();
  const branding = useBranding();
  const countries = useCountries();
  const assets = useQuery({ queryKey: keys.assets, queryFn: () => unwrap(api.GET("/api/v1/scripts/assets")) });
  const steps = launchSteps({
    library: library.data,
    agents: agents.data,
    matrix: matrix.data,
    banners: assets.data?.filter((asset) => asset.kind === "banner").length,
    branding: branding.data,
    publishedWorkflows,
    countries: countries.data?.length,
  });
  const done = steps.filter((step) => step.done).length;
  const complete = done === steps.length;
  return (
    <Card
      title={
        <span className="row">
          <h2>{complete ? "Ready for launch" : "Launch checklist"}</h2>
          <span className="small muted">{done} of {steps.length} done</span>
        </span>
      }
    >
      <div className="launch">
        <Donut
          size={104}
          thickness={12}
          values={[
            { key: "done", value: done },
            { key: "left", value: steps.length - done },
          ]}
          colors={{ done: "var(--success)", left: "var(--surface-3)" }}
          center={`${Math.round((done / steps.length) * 100)}%`}
          caption="ready"
        />
        <ol className="launch-steps">
          {steps.map((step) => (
            <li key={step.id} className={step.done ? "done" : ""}>
              <span className="launch-check">{step.done ? <Icon name="check" /> : null}</span>
              <div style={{ minWidth: 0 }}>
                <Link to={step.to} className="plain">
                  <strong>{step.title}</strong>
                </Link>
                <div className="small muted">{step.detail}</div>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </Card>
  );
}
