/** Dashboard: live numbers, trends, reply mix, air/ocean capacity, script coverage and work to do. */
import { useQuery } from "@tanstack/react-query";
import { Suspense, lazy, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys, useBranding, useCountries, useCrmSummary, useDashboardStats, useInsights, useMailboxHealth, useOffices, useSendingSettings } from "../api/queries";
import { useSession } from "../auth/session";
import { Donut, Meter, Sparkline, TrendChart } from "../components/charts";
import { Reveal } from "../components/delight";
import { HealthPill } from "../components/HealthPill";
import { ActivityFeed, FunnelCard, LaunchChecklist, ReplyHeatmap } from "../components/insights";
import type { LaneResult } from "../components/LaneMap";
import { Icon } from "../components/icons";
import type { IconName } from "../components/icons";
import { Page } from "../components/Layout";
import { CountUp, RouteArt, Skeleton } from "../components/motion";
import { BucketBadge, Card, Empty, ErrorAlert, LinePill, Segmented, StatusBadge } from "../components/ui";
import { periodChange } from "../lib/charts";
import { formatRelative, percent } from "../lib/format";
import { LINE_ICONS, asServiceLine, countryFlag } from "../lib/freight";

type Period = 7 | 30 | 90;

// The map brings d3-geo and the world shapes: load it only when the dashboard needs it.
const LaneMap = lazy(() => import("../components/LaneMap").then((module) => ({ default: module.LaneMap })));

export const BUCKET_COLORS: Record<string, string> = {
  interested: "#12b886",
  meeting_request: "#0ca678",
  rate_query: "#7b5cff",
  service_query: "#9775fa",
  partnership_query: "#4c6ef5",
  referral: "#339af0",
  wrong_office: "#f59f00",
  later: "#22b8cf",
  not_interested: "#adb5bd",
  complaint: "#e03131",
  existing_partner: "#5c7cfa",
  unsubscribe: "#f03e3e",
  out_of_office: "#3bc9db",
  auto_reply: "#868e96",
  bounce_hard: "#c92a2a",
  bounce_soft: "#fd7e14",
  blocked: "#a61e4d",
  unclear: "#fab005",
};

const LINE_COLORS: Record<string, string> = {
  air: "var(--air)",
  ocean: "var(--ocean)",
  road: "var(--road)",
  rail: "var(--rail)",
  courier: "var(--courier)",
  customs: "var(--customs)",
  warehousing: "var(--warehousing)",
  project: "var(--project)",
  any: "var(--accent)",
};

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

function dayLabel(iso: string, days: Period): string {
  const date = new Date(`${iso}T00:00:00Z`);
  return new Intl.DateTimeFormat(undefined, days === 7 ? { weekday: "short", timeZone: "UTC" } : { day: "numeric", month: "short", timeZone: "UTC" }).format(date);
}

function Kpi({
  label,
  icon,
  value,
  previous,
  series,
  color,
  hint,
  loading,
}: {
  label: string;
  icon: IconName;
  value: number;
  previous: number;
  series: number[];
  color: string;
  hint?: ReactNode;
  loading: boolean;
}) {
  const change = periodChange(value, previous);
  return (
    <div className="card stat lift">
      <div className="stat-label">
        <Icon name={icon} style={{ color }} /> {label}
      </div>
      <div className="stat-value">{loading ? <Skeleton width={70} height={28} /> : <CountUp value={value} />}</div>
      <div className="stat-foot">
        <span className={`delta ${change.direction}`} title="Compared with the previous period">
          {change.direction === "up" ? "▲" : change.direction === "down" ? "▼" : "•"} {change.label}
        </span>
        <Sparkline values={series} color={color} />
      </div>
      {hint && <div className="small muted">{hint}</div>}
    </div>
  );
}

function Tile({ to, icon, label, value, tone }: { to: string; icon: IconName; label: string; value: number; tone: string }) {
  return (
    <Link to={to} className="card lift" style={{ padding: 12, color: "inherit", textDecoration: "none", display: "flex", gap: 10, alignItems: "center" }}>
      <span className="node-icon" style={{ background: tone, width: 32, height: 32, borderRadius: 10 }}>
        <Icon name={icon} />
      </span>
      <span>
        <strong style={{ fontFamily: "var(--display)", fontSize: 20 }}>
          <CountUp value={value} />
        </strong>
        <div className="small muted">{label}</div>
      </span>
    </Link>
  );
}

function BusinessByLine({ lines, days }: { lines: Schemas["LineSummaryResponse"][]; days: Period }) {
  const busiest = Math.max(1, ...lines.map((line) => line.open_value + line.won_value_in_period));
  const deals = lines.reduce((sum, line) => sum + line.open_deals, 0);
  return (
    <Card title="Business by service" actions={<Link to="/quotes">Quotes</Link>}>
      <div className="grid" style={{ gridTemplateColumns: "auto 1fr", gap: 24, alignItems: "center" }}>
        <Donut
          size={120}
          thickness={14}
          values={lines.map((line) => ({ key: line.service_line, value: line.open_deals + line.won_in_period }))}
          colors={LINE_COLORS}
          center={String(deals)}
          caption="open deals"
        />
        <div className="stack">
          {lines.map((line) => (
            <div key={line.service_line} className="stack tight">
              <div className="row between">
                <LinePill line={line.service_line} />
                <span className="small">
                  <strong>{line.open_deals}</strong> open · {Math.round(line.open_value).toLocaleString()} pipeline · <strong>{line.won_in_period}</strong> won ({Math.round(line.won_value_in_period).toLocaleString()}) in {days} days
                </span>
              </div>
              <Meter value={(line.open_value + line.won_value_in_period) / busiest} color={LINE_COLORS[line.service_line]} />
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

function CapacityCard({ capacity }: { capacity: Schemas["LineCapacityResponse"][] }) {
  return (
    <Card title="Today's capacity" actions={<Link to="/agents">Agents</Link>}>
      {capacity.length === 0 && <Empty title="No active agents">Add agents for the lines you sell to start sending.</Empty>}
      <div className="stack">
        {capacity.map((line) => (
          <div key={line.service_line} className="stack tight">
            <div className="row between">
              <LinePill line={line.service_line} />
              <span className="small">
                <strong>{line.sent_today}</strong> / {line.daily_cap} emails · {line.agents} agent{line.agents === 1 ? "" : "s"}
              </span>
            </div>
            <Meter value={line.used} color={LINE_COLORS[line.service_line]} />
          </div>
        ))}
      </div>
    </Card>
  );
}

function ScriptsCard({ scripts }: { scripts: Schemas["ScriptCoverageResponse"] }) {
  const needed = scripts.languages_needed.length;
  const covered = scripts.languages_covered.length;
  return (
    <Card title="Script coverage" actions={<Link to="/scripts">Scripts</Link>}>
      <div className="coverage-ring">
        <Donut
          size={96}
          thickness={11}
          values={[
            { key: "covered", value: covered },
            { key: "missing", value: Math.max(needed - covered, 0) },
          ]}
          colors={{ covered: "var(--success)", missing: "var(--surface-3)" }}
          center={`${covered}/${needed}`}
          caption="languages"
        />
        <div className="stack tight small">
          <span><strong>{scripts.active_versions}</strong> live script versions</span>
          <span><strong>{scripts.ai_drafts}</strong> AI drafts to approve</span>
          {scripts.errors > 0 && <span style={{ color: "var(--danger)" }}><strong>{scripts.errors}</strong> failed imports</span>}
        </div>
      </div>
      {scripts.held_countries.length > 0 && (
        <div className="alert warning small" style={{ marginTop: 12 }}>
          Held until their script exists: {scripts.held_countries.map((code) => `${countryFlag(code)} ${code}`).join("  ")}
        </div>
      )}
    </Card>
  );
}

function ReplyMix({ buckets, replies }: { buckets: Record<string, number>; replies: number }) {
  const entries = Object.entries(buckets);
  return (
    <Card title="Reply mix" actions={<Link to="/reviews">Review queue</Link>}>
      {entries.length === 0 ? (
        <Empty title="No replies in this period">Classified replies show up here by type.</Empty>
      ) : (
        <div className="donut-wrap">
          <Donut
            values={entries.map(([key, value]) => ({ key, value }))}
            colors={BUCKET_COLORS}
            center={String(replies)}
            caption="replies"
          />
          <div className="bucket-list">
            {entries.slice(0, 7).map(([bucket, count]) => (
              <div key={bucket}>
                <i className="dot" style={{ background: BUCKET_COLORS[bucket] ?? "var(--text-3)" }} />
                <span>{bucket.replaceAll("_", " ")}</span>
                <strong>{count}</strong>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function LanesCard({ insights, days }: { insights: Schemas["InsightsResponse"] | undefined; days: Period }) {
  const countries = useCountries();
  const branding = useBranding();
  const offices = useOffices();
  const names = new Map((countries.data ?? []).map((country) => [country.country, country.name]));
  // Lanes start where the sales desk is: the branding setting, else the first office's country.
  const origins = branding.data?.lane_origin_countries?.length
    ? branding.data.lane_origin_countries
    : offices.data?.[0]?.country
      ? [offices.data[0].country]
      : [];
  const originLabel = origins.length ? origins.map((code) => names.get(code) ?? code).join(" / ") : undefined;
  // Every enabled destination gets a lane; the ones with traffic are drawn thicker and move.
  const results = new Map((insights?.countries ?? []).map((row) => [row.country, row]));
  const lanes: LaneResult[] = [
    ...(countries.data ?? [])
      .filter((country) => country.is_enabled && !origins.includes(country.country))
      .map((country) => results.get(country.country) ?? { country: country.country, sent: 0, replies: 0, leads: 0, reply_rate: 0 }),
    ...(insights?.countries ?? []).filter((row) => !names.has(row.country) && !origins.includes(row.country)),
  ].map((lane) => ({ ...lane, name: names.get(lane.country) }));
  const top = [...lanes].filter((lane) => lane.sent > 0).sort((a, b) => b.sent - a.sent).slice(0, 7);
  const busiest = Math.max(1, ...top.map((lane) => lane.sent));
  return (
    <div className="grid dash" style={{ marginBottom: 16 }}>
      <Card
        title={
          <span className="row">
            <h2>Lane map</h2>
            <span className="small muted">{lanes.length} destinations · {days} days</span>
          </span>
        }
        actions={
          <div className="legend">
            <span><i style={{ background: "#7f8ca5" }} /> No replies</span>
            <span><i style={{ background: "#3cc8ea" }} /> &lt; 5%</span>
            <span><i style={{ background: "#19c3b1" }} /> 5–15%</span>
            <span><i style={{ background: "#3fd49b" }} /> ≥ 15%</span>
          </div>
        }
        flush
      >
        <Suspense fallback={<Skeleton height={360} />}>
          <LaneMap lanes={lanes} origin={origins[0]} originLabel={originLabel} />
        </Suspense>
      </Card>
      <Card title="Top lanes" actions={<Link to="/countries">Countries</Link>}>
        {!insights ? (
          <Skeleton height={180} />
        ) : top.length === 0 ? (
          <Empty title="No emails in this period">Lanes light up on the map as scripts go out.</Empty>
        ) : (
          <div className="lane-list">
            {top.map((lane) => (
              <div key={lane.country} className="stack tight">
                <div className="lane-row">
                  <span style={{ fontSize: 18 }}>{countryFlag(lane.country)}</span>
                  <span className="truncate">{lane.name ?? lane.country}</span>
                  <span className="small">
                    <strong>{lane.sent}</strong> sent · {lane.replies} repl{lane.replies === 1 ? "y" : "ies"}
                  </span>
                </div>
                <Meter value={lane.sent / busiest} color="var(--accent)" />
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

export function DashboardPage() {
  const [days, setDays] = useState<Period>(7);
  const { session } = useSession();
  const settings = useSendingSettings();
  const stats = useDashboardStats(days);
  const insights = useInsights(days);
  const crm = useCrmSummary(days);
  const reviews = useQuery({
    queryKey: [...keys.reviews, "dashboard"],
    queryFn: () => unwrap(api.GET("/api/v1/ai/reviews", { params: { query: { status: "pending", limit: 5 } } })),
  });
  const leads = useQuery({
    queryKey: [...keys.leads, "dashboard"],
    queryFn: () => unwrap(api.GET("/api/v1/leads", { params: { query: { status: "new", limit: 5 } } })),
  });
  const data = stats.data;
  const loading = !data;
  const series = data?.series ?? [];
  const name = (session?.subject ?? "").split("@")[0]?.split(/[._-]/)[0] ?? "";
  const sentToday = data?.capacity.reduce((sum, line) => sum + line.sent_today, 0) ?? 0;

  return (
    <Page>
      <section className="hero">
        <RouteArt density="compact" />
        <div className="hero-content">
          <div>
            <div className="eyebrow" style={{ color: "#8fe9dc" }}>
              <Icon name="route" /> {session?.tenantName}
            </div>
            <h1>
              {greeting()}
              {name ? `, ${name.charAt(0).toUpperCase()}${name.slice(1)}` : ""}.
            </h1>
            <p>Replies waiting for a person, leads for pricing, and every lane your agents are working.</p>
            <div className="hero-pills">
              <span className="hero-pill">
                {settings.data?.sending_paused ? <Icon name="pause" /> : <span className="dot live" />}
                {settings.data?.sending_paused ? "Sending paused" : "Sending on"}
              </span>
              <span className="hero-pill">
                <Icon name="outbox" /> {sentToday} sent today
              </span>
              {data && data.pipeline.pending_reviews > 0 && (
                <Link to="/reviews" className="hero-pill" style={{ textDecoration: "none" }}>
                  <Icon name="review" /> {data.pipeline.pending_reviews} to review
                </Link>
              )}
            </div>
          </div>
          <Segmented<Period>
            label="Period"
            value={days}
            onChange={setDays}
            options={[
              { id: 7, label: "7 days" },
              { id: 30, label: "30 days" },
              { id: 90, label: "90 days" },
            ]}
          />
        </div>
      </section>

      {settings.data?.sending_paused && (
        <div className="alert error" style={{ marginBottom: 16 }}>
          Sending is paused for the whole tenant (kill switch). Emails are deferred until you resume in <Link to="/settings">Settings</Link>.
        </div>
      )}
      <ErrorAlert error={stats.error} />

      <div className="grid stats" style={{ marginBottom: 16 }}>
        <Kpi label="Emails sent" icon="outbox" color="var(--accent)" loading={loading} value={data?.totals.sent ?? 0} previous={data?.previous.sent ?? 0} series={series.map((point) => point.sent)} hint={data && data.totals.tracked ? `${percent(data.totals.open_rate)} opened · ${percent(data.totals.click_rate)} clicked` : undefined} />
        <Kpi label="Replies" icon="reply" color="var(--brand-1)" loading={loading} value={data?.totals.replies ?? 0} previous={data?.previous.replies ?? 0} series={series.map((point) => point.replies)} hint={data ? `${percent(data.totals.reply_rate)} reply rate` : undefined} />
        <Kpi label="Pricing leads" icon="leads" color="var(--violet)" loading={loading} value={data?.totals.leads ?? 0} previous={data?.previous.leads ?? 0} series={series.map((point) => point.leads)} />
        <Kpi label="Bounces" icon="alert" color="var(--danger)" loading={loading} value={data?.totals.bounces ?? 0} previous={data?.previous.bounces ?? 0} series={[]} />
      </div>

      <div className="grid dash" style={{ marginBottom: 16 }}>
        <Card
          title="Sent, replies and leads"
          actions={
            <div className="legend">
              <span><i style={{ background: "var(--accent)" }} /> Sent</span>
              <span><i style={{ background: "var(--brand-1)" }} /> Replies</span>
              <span><i style={{ background: "var(--violet)" }} /> Leads</span>
            </div>
          }
        >
          {loading ? (
            <Skeleton height={220} />
          ) : (
            <TrendChart
              key={days}
              labels={series.map((point) => dayLabel(point.day, days))}
              series={[
                { key: "sent", label: "Sent", color: "var(--accent)", values: series.map((point) => point.sent), fill: true },
                { key: "replies", label: "Replies", color: "var(--brand-1)", values: series.map((point) => point.replies), fill: true },
                { key: "leads", label: "Leads", color: "var(--violet)", values: series.map((point) => point.leads) },
              ]}
            />
          )}
        </Card>
        {data ? <ReplyMix buckets={data.reply_buckets} replies={data.totals.replies} /> : <Card title="Reply mix"><Skeleton height={150} /></Card>}
      </div>

      <Reveal>
        <LanesCard insights={insights.data} days={days} />
      </Reveal>

      {crm.data?.by_line && crm.data.by_line.length > 0 && (
        <Reveal>
          <BusinessByLine lines={crm.data.by_line} days={days} />
        </Reveal>
      )}

      <Reveal>
        <div className="grid two" style={{ marginBottom: 16, alignItems: "start" }}>
          <FunnelCard funnel={insights.data?.funnel} days={days} />
          <ReplyHeatmap insights={insights.data} />
        </div>
      </Reveal>

      <Reveal>
      <div className="grid three" style={{ marginBottom: 16 }}>
        {data ? <CapacityCard capacity={data.capacity} /> : <Card title="Today's capacity"><Skeleton height={90} /></Card>}
        <Card title="Work in progress">
          {data ? (
            <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <Tile to="/reviews" icon="review" label="Replies to review" value={data.pipeline.pending_reviews} tone="var(--warning)" />
              <Tile to="/leads" icon="leads" label="New leads" value={data.pipeline.new_leads} tone="var(--violet)" />
              <Tile to="/enrollments" icon="play" label="Running" value={data.pipeline.running} tone="var(--accent)" />
              <Tile to="/enrollments" icon="wait" label="Waiting" value={data.pipeline.waiting} tone="var(--info)" />
              <Tile to="/pipeline" icon="trending" label="Open deals" value={crm.data?.open_deals ?? 0} tone="var(--brand-1)" />
              <Tile to="/tasks" icon="clock" label="Tasks due today" value={crm.data?.tasks_due_today ?? 0} tone={crm.data?.tasks_overdue ? "var(--danger)" : "var(--success)"} />
            </div>
          ) : (
            <Skeleton height={120} />
          )}
        </Card>
        {data ? <ScriptsCard scripts={data.scripts} /> : <Card title="Script coverage"><Skeleton height={96} /></Card>}
      </div>
      </Reveal>

      <Reveal>
        <div className="grid dash" style={{ marginBottom: 16, alignItems: "start" }}>
          <ActivityFeed />
          <LaunchChecklist publishedWorkflows={data?.pipeline.published_workflows} />
        </div>
      </Reveal>

      <Reveal>
      <div className="grid three">
        <MailboxHealthCard />
        <Card title="Busiest agents" actions={<span className="small muted">{days} days</span>}>
          {data && data.top_agents.length === 0 && <Empty title="No emails in this period" />}
          <div className="stack">
            {data?.top_agents.map((agent) => (
              <div key={agent.sender_id} className="stack tight">
                <div className="row between small">
                  <span className="row" style={{ gap: 6 }}>
                    <Icon name={agent.service_line === "any" ? "mail" : LINE_ICONS[asServiceLine(agent.service_line)]} style={{ color: LINE_COLORS[agent.service_line], width: 15 }} />
                    {agent.name}
                  </span>
                  <strong>{agent.sent}</strong>
                </div>
                <Meter value={agent.sent / Math.max(...data.top_agents.map((item) => item.sent), 1)} color={LINE_COLORS[agent.service_line]} />
              </div>
            ))}
          </div>
        </Card>
        <Card title="Waiting for review" actions={<Link to="/reviews">All</Link>} flush>
          {reviews.data?.items.length === 0 && <Empty title="Nothing to review" />}
          <table className="table">
            <tbody>
              {reviews.data?.items.map((item) => {
                const classification = (item.payload.classification ?? {}) as { bucket?: string };
                return (
                  <tr key={item.id}>
                    <td>
                      <Link to={`/reviews?item=${item.id}`}>{String(item.payload.contact_email ?? "Reply")}</Link>
                      <div className="small muted truncate" style={{ maxWidth: 240 }}>{item.reason}</div>
                    </td>
                    <td><BucketBadge bucket={classification.bucket} /></td>
                    <td className="small muted nowrap">{formatRelative(item.created_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
        <Card title="New leads" actions={<Link to="/leads">All</Link>} flush>
          {leads.data?.items.length === 0 && <Empty title="No new leads" />}
          <table className="table">
            <tbody>
              {leads.data?.items.map((lead) => (
                <tr key={lead.id}>
                  <td>
                    <Link to={`/contacts/${lead.contact_id}`}>{lead.summary ?? "Lead"}</Link>
                  </td>
                  <td><StatusBadge value={lead.priority} /></td>
                  <td className="small muted nowrap">{formatRelative(lead.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
      </Reveal>
    </Page>
  );
}

function MailboxHealthCard() {
  const health = useMailboxHealth();
  const rows = health.data ?? [];
  const attention = rows.filter((row) => row.status === "at_risk" || row.status === "watch");
  return (
    <Card title="Mailbox health" actions={<Link to="/agents" className="small">Agents</Link>}>
      {health.isLoading && <Skeleton height={90} />}
      {!health.isLoading && rows.length === 0 && <Empty title="No agent mailboxes yet" />}
      {rows.length > 0 && attention.length === 0 && (
        <div className="alert success small">All {rows.length} mailboxes are healthy (bounces and complaints, 30 days).</div>
      )}
      <div className="stack">
        {attention.slice(0, 5).map((row) => (
          <div key={row.sender_id} className="row between small">
            <span className="truncate">
              <strong>{row.display_name}</strong>
              <span className="muted"> · {row.reasons[0] ?? "Check the numbers"}</span>
            </span>
            <HealthPill health={row} />
          </div>
        ))}
      </div>
    </Card>
  );
}
