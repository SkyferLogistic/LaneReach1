/**
 * AI insights: the weekly digest (the AI's summary of the week's numbers, with the numbers behind
 * it), the hottest open deals by lead score, follow-up suggestions for quiet deals, and the AI's
 * track record (measured from what people did with its work).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, describeError, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys } from "../api/queries";
import { useSession } from "../auth/session";
import { Icon } from "../components/icons";
import { Page } from "../components/Layout";
import { SkeletonRows } from "../components/motion";
import { Badge, Button, Card, Empty, ErrorAlert, PageHeader, useToast } from "../components/ui";
import { FOLLOW_UPS_KEY, FollowUpCard, ScoreBadge } from "../crm/DealAi";
import { formatRelative } from "../lib/format";
import { HIGHLIGHT_KINDS, percentText, sortByScore, splitShares, weekLabel } from "../lib/intelligence";

type Digest = Schemas["DigestResponse"];
const DIGESTS_KEY = ["insights", "digests"] as const;

interface DigestStats {
  quote_requests?: { this_week: number; previous_4_week_average: number };
  lanes_heating_up?: { lane: string; this_week: number; previous_4_week_average: number }[];
  win_loss_by_lane?: { lane: string; won: number; lost: number; win_rate_percent: number }[];
  slow_offices?: { office: string; deals_waiting: number; average_days_waiting: number }[];
  replies?: { total: number };
  quotes?: { sent: number; accepted: number; rejected: number };
  waiting_for_quote?: { deals: number; oldest_days: number };
  quiet_deals?: number;
}

export function InsightsPage() {
  const { can } = useSession();
  const aiAllowed = can("crm:write") && can("ai:run");
  const queryClient = useQueryClient();
  const toast = useToast();
  const digests = useQuery({ queryKey: DIGESTS_KEY, queryFn: () => unwrap(api.GET("/api/v1/insights/digests")) });
  const [selected, setSelected] = useState<string | null>(null);
  const build = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/insights/digests", { body: {} })),
    onSuccess: (digest) => {
      toast(digest.written_by === "ai" ? "Digest written by AI from this week's numbers." : "Digest written from this week's numbers.");
      setSelected(digest.id);
      void queryClient.invalidateQueries({ queryKey: DIGESTS_KEY });
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  const list = digests.data ?? [];
  const digest = list.find((item) => item.id === selected) ?? list[0];
  return (
    <Page>
      <PageHeader
        eyebrow={
          <>
            <Icon name="sparkles" /> AI
          </>
        }
        title="AI insights"
        description="Your week summarised by AI from LaneReach's own numbers, the hottest deals by lead score, and follow-ups for deals that went quiet."
        actions={
          aiAllowed && (
            <Button variant="primary" busy={build.isPending} onClick={() => build.mutate()}>
              <Icon name="sparkles" /> Write this week&apos;s digest
            </Button>
          )
        }
      />
      <ErrorAlert error={digests.error} />
      <div className="insights-grid">
        <div className="stack">
          {digests.isLoading ? (
            <Card>
              <SkeletonRows rows={5} />
            </Card>
          ) : digest ? (
            <DigestView digest={digest} weeks={list} onWeek={setSelected} />
          ) : (
            <Card>
              <Empty art="chart" title="No digest yet">
                A digest is written every Monday for the week before. {aiAllowed ? "Write one now for this week so far." : ""}
              </Empty>
            </Card>
          )}
        </div>
        <div className="stack">
          <HotDeals />
          <QuietDeals />
          <AIQualityCard />
        </div>
      </div>
    </Page>
  );
}

function DigestView({ digest, weeks, onWeek }: { digest: Digest; weeks: Digest[]; onWeek: (id: string) => void }) {
  const stats = digest.stats as DigestStats;
  const heating = stats.lanes_heating_up ?? [];
  const winLoss = stats.win_loss_by_lane ?? [];
  const slow = stats.slow_offices ?? [];
  const tiles: { label: string; value: number | string; hint?: string }[] = [
    { label: "Quote requests", value: stats.quote_requests?.this_week ?? 0, hint: `avg ${stats.quote_requests?.previous_4_week_average ?? 0} / week before` },
    { label: "Replies read", value: stats.replies?.total ?? 0 },
    { label: "Quotes sent", value: stats.quotes?.sent ?? 0, hint: `${stats.quotes?.accepted ?? 0} accepted · ${stats.quotes?.rejected ?? 0} rejected` },
    { label: "Waiting for a quote", value: stats.waiting_for_quote?.deals ?? 0, hint: stats.waiting_for_quote?.deals ? `oldest ${stats.waiting_for_quote.oldest_days} days` : undefined },
    { label: "Quiet deals", value: stats.quiet_deals ?? 0 },
  ];
  const maxHeat = Math.max(1, ...heating.map((lane) => lane.this_week));
  return (
    <>
      <section className="digest-hero">
        <div className="digest-glow" aria-hidden />
        <div className="row between">
          <span className="digest-week">
            <Icon name="clock" /> {weekLabel(digest.week_start, digest.week_end)}
          </span>
          <div className="row" style={{ gap: 6 }}>
            <Badge tone={digest.written_by === "ai" ? "violet" : "gray"}>{digest.written_by === "ai" ? "Written by AI" : "Written by rules"}</Badge>
            {weeks.length > 1 && (
              <select className="select" value={digest.id} onChange={(event) => onWeek(event.target.value)} aria-label="Week" style={{ width: "auto" }}>
                {weeks.map((week) => (
                  <option key={week.id} value={week.id}>
                    {weekLabel(week.week_start, week.week_end)}
                  </option>
                ))}
              </select>
            )}
          </div>
        </div>
        <h2>{digest.headline}</h2>
        <p>{digest.summary}</p>
        <div className="small muted">Updated {formatRelative(digest.updated_at)} · every number comes from LaneReach; the AI may not add any.</div>
      </section>
      <div className="digest-tiles">
        {tiles.map((tile) => (
          <div key={tile.label} className="digest-tile">
            <strong>{tile.value}</strong>
            <span>{tile.label}</span>
            {tile.hint && <small>{tile.hint}</small>}
          </div>
        ))}
      </div>
      {digest.highlights.length > 0 && (
        <div className="digest-highlights">
          {digest.highlights.map((item, index) => {
            const kind = HIGHLIGHT_KINDS[String(item.kind)] ?? { label: String(item.kind), icon: "trending" as const };
            return (
              <article key={index} className={`digest-highlight ${String(item.kind)}`} style={{ animationDelay: `${index * 70}ms` }}>
                <span className="digest-highlight-icon">
                  <Icon name={kind.icon} />
                </span>
                <div>
                  <small>{kind.label}</small>
                  <strong>{String(item.title)}</strong>
                  <p>{String(item.detail)}</p>
                </div>
              </article>
            );
          })}
        </div>
      )}
      <div className="digest-columns">
        <Card title="Lanes heating up">
          {heating.length === 0 ? (
            <p className="small muted">No lane had more requests than usual.</p>
          ) : (
            <ul className="heat-list">
              {heating.map((lane) => (
                <li key={lane.lane}>
                  <span className="truncate">{lane.lane}</span>
                  <span className="heat-bar">
                    <i style={{ width: `${(lane.this_week / maxHeat) * 100}%` }} />
                  </span>
                  <b>{lane.this_week}</b>
                  <small className="muted">avg {lane.previous_4_week_average}</small>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Win / loss by lane (90 days)">
          {winLoss.length === 0 ? (
            <p className="small muted">No deals won or lost yet.</p>
          ) : (
            <ul className="heat-list">
              {winLoss.map((lane) => (
                <li key={lane.lane}>
                  <span className="truncate">{lane.lane}</span>
                  <span className="winloss-bar" title={`${lane.won} won, ${lane.lost} lost`}>
                    <i className="won" style={{ flex: lane.won }} />
                    <i className="lost" style={{ flex: lane.lost }} />
                  </span>
                  <b>{lane.win_rate_percent}%</b>
                  <small className="muted">
                    {lane.won}/{lane.won + lane.lost}
                  </small>
                </li>
              ))}
            </ul>
          )}
        </Card>
        {slow.length > 0 && (
          <Card title="Waiting for a quote, by office">
            <ul className="heat-list">
              {slow.map((office) => (
                <li key={office.office}>
                  <span className="truncate">{office.office}</span>
                  <span />
                  <b>{office.deals_waiting}</b>
                  <small className="muted">{office.average_days_waiting} days avg</small>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>
    </>
  );
}

function AIQualityCard() {
  const quality = useQuery({ queryKey: ["insights", "ai-quality"], queryFn: () => unwrap(api.GET("/api/v1/insights/ai-quality")) });
  const data = quality.data;
  return (
    <Card
      title={
        <h2 className="row" style={{ gap: 6 }}>
          <Icon name="shield" /> AI quality · 30 days
        </h2>
      }
    >
      <ErrorAlert error={quality.error} />
      {!data ? (
        <SkeletonRows rows={4} />
      ) : (
        <div className="ai-quality">
          <ReplySplit data={data} />
          <QualityLine
            label="AI drafts sent as written"
            value={data.ai_drafts.unchanged_percent === null ? "–" : `${data.ai_drafts.unchanged_percent}%`}
            hint={`${data.ai_drafts.edited} edited · ${data.ai_drafts.rejected} rejected · ${data.ai_drafts.waiting} waiting`}
          />
          {data.built_in_drafts.sent_as_written + data.built_in_drafts.edited + data.built_in_drafts.rejected + data.built_in_drafts.waiting > 0 && (
            <QualityLine
              label="Built-in messages sent as written"
              value={data.built_in_drafts.unchanged_percent === null ? "–" : `${data.built_in_drafts.unchanged_percent}%`}
              hint={`${data.built_in_drafts.edited} edited · ${data.built_in_drafts.rejected} rejected`}
            />
          )}
          <QualityLine
            label="Follow-up suggestions used"
            value={percentText(data.ai_follow_ups.used + data.built_in_follow_ups.used, data.ai_follow_ups.used + data.ai_follow_ups.dismissed + data.built_in_follow_ups.used + data.built_in_follow_ups.dismissed)}
            hint={`${data.ai_follow_ups.dismissed + data.built_in_follow_ups.dismissed} dismissed · ${data.ai_follow_ups.open + data.built_in_follow_ups.open} open`}
          />
          <QualityLine
            label="Lead score: won vs lost deals"
            value={`${data.won_average_score ?? "–"} vs ${data.lost_average_score ?? "–"}`}
            hint={`${data.won_scored} won · ${data.lost_scored} lost scored. A useful score is higher for won deals.`}
          />
          <QualityLine label="AI runs that failed" value={percentText(data.ai_runs_failed, data.ai_runs)} hint={`${data.ai_runs_failed} of ${data.ai_runs} runs`} />
        </div>
      )}
    </Card>
  );
}

function ReplySplit({ data }: { data: Schemas["AIQualityResponse"] }) {
  const parts = [
    { label: "Rules", value: data.replies_sorted_by_rules, tone: "rules" },
    { label: "AI", value: data.replies_sorted_by_ai, tone: "ai" },
    { label: "A person", value: data.replies_sent_to_a_person, tone: "person" },
  ];
  const shares = splitShares(parts.map((part) => part.value));
  const total = parts.reduce((sum, part) => sum + part.value, 0);
  return (
    <div className="quality-line">
      <div className="row between">
        <span className="small muted">Replies sorted by</span>
        <strong>{total} replies</strong>
      </div>
      {shares.length > 0 && (
        <div className="split-bar" aria-hidden>
          {parts.map((part, index) => (
            <span key={part.label} className={part.tone} style={{ width: `${shares[index] ?? 0}%` }} />
          ))}
        </div>
      )}
      <div className="small muted">{parts.map((part) => `${part.label} ${part.value}`).join(" · ")}</div>
    </div>
  );
}

function QualityLine({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="quality-line">
      <div className="row between">
        <span className="small muted">{label}</span>
        <strong>{value}</strong>
      </div>
      <div className="small muted">{hint}</div>
    </div>
  );
}

function HotDeals() {
  const deals = useQuery({
    queryKey: [...keys.deals, "open-scored"],
    queryFn: () => unwrap(api.GET("/api/v1/crm/deals", { params: { query: { status: "open" } } })),
  });
  const hot = sortByScore((deals.data ?? []).filter((deal) => deal.score !== null && deal.score !== undefined)).slice(0, 6);
  return (
    <Card
      title={
        <h2 className="row" style={{ gap: 6 }}>
          <Icon name="sparkles" /> Hottest deals
        </h2>
      }
      actions={
        <Link to="/pipeline" className="small">
          Pipeline
        </Link>
      }
    >
      {deals.isLoading && <SkeletonRows rows={3} />}
      {!deals.isLoading && hot.length === 0 && <p className="small muted">Deals are scored as rate requests come in and every night.</p>}
      <ul className="hot-list">
        {hot.map((deal) => (
          <li key={deal.id}>
            <ScoreBadge score={deal.score} />
            <Link to={`/pipeline?deal=${deal.id}`} className="grow truncate">
              <strong>{deal.company_name ?? deal.contact_name ?? deal.title}</strong>
              <small className="muted">
                {deal.origin ?? "?"} → {deal.destination ?? "?"}
              </small>
            </Link>
            <span className="small muted">{(deal.score_reasons?.[0] as { label?: string } | undefined)?.label ?? ""}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function QuietDeals() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const followUps = useQuery({
    queryKey: [...FOLLOW_UPS_KEY, "open"],
    queryFn: () => unwrap(api.GET("/api/v1/crm/follow-ups", { params: { query: { status: "open" } } })),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: FOLLOW_UPS_KEY });
    void queryClient.invalidateQueries({ queryKey: keys.tasks });
  };
  const items = followUps.data ?? [];
  return (
    <Card
      title={
        <h2 className="row" style={{ gap: 6 }}>
          <Icon name="reply" /> Follow-ups for quiet deals {items.length > 0 && <Badge tone="amber">{items.length}</Badge>}
        </h2>
      }
    >
      <ErrorAlert error={followUps.error} />
      {followUps.isLoading && <SkeletonRows rows={2} />}
      {!followUps.isLoading && items.length === 0 && (
        <p className="small muted">No quiet deals need a nudge right now. Suggestions appear every morning for deals without activity.</p>
      )}
      <div className="stack">
        {items.map((item) => (
          <FollowUpCard key={item.id} suggestion={item} canWrite={can("crm:write")} onDone={refresh} showDeal />
        ))}
      </div>
    </Card>
  );
}
