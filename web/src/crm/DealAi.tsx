/**
 * The deal's AI tab and score badge: the lead score with every reason (the AI's reading of the
 * latest reply is one of them), rescore, and a follow-up suggestion to use as a task or dismiss.
 * Nothing here sends anything.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, describeError, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys } from "../api/queries";
import { useSession } from "../auth/session";
import { Icon } from "../components/icons";
import { SkeletonRows } from "../components/motion";
import { Badge, Button, ErrorAlert, useToast } from "../components/ui";
import { formatRelative } from "../lib/format";
import { BAND_LABELS, FOLLOW_UP_ACTIONS, formatPoints, scoreBand } from "../lib/intelligence";

type FollowUp = Schemas["FollowUpResponse"];

export const FOLLOW_UPS_KEY = [...keys.crm, "follow-ups"] as const;

export function ScoreBadge({ score }: { score: number | null | undefined }) {
  const band = scoreBand(score);
  if (!band || score === null || score === undefined) return null;
  return (
    <span className={`score-badge ${band}`} title={`Lead score ${score} (${BAND_LABELS[band]})`}>
      {band === "hot" && <Icon name="sparkles" />}
      {score}
    </span>
  );
}

export function ScoreRing({ score, size = 96 }: { score: number | null | undefined; size?: number }) {
  const band = scoreBand(score) ?? "cold";
  const value = score ?? 0;
  const radius = 40;
  const circumference = 2 * Math.PI * radius;
  return (
    <svg viewBox="0 0 100 100" width={size} height={size} className={`score-ring ${band}`} role="img" aria-label={`Lead score ${score ?? "not scored"}`}>
      <circle cx="50" cy="50" r={radius} className="track" />
      <circle
        cx="50"
        cy="50"
        r={radius}
        className="value"
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - value / 100)}
        transform="rotate(-90 50 50)"
      />
      <text x="50" y="56" textAnchor="middle">
        {score ?? "–"}
      </text>
    </svg>
  );
}

export function DealAi({ dealId, canWrite }: { dealId: string; canWrite: boolean }) {
  const { can } = useSession();
  const aiAllowed = canWrite && can("ai:run");
  const queryClient = useQueryClient();
  const toast = useToast();
  const score = useQuery({
    queryKey: [...keys.deals, "score", dealId],
    queryFn: () => unwrap(api.GET("/api/v1/crm/deals/{deal_id}/score", { params: { path: { deal_id: dealId } } })),
  });
  const followUps = useQuery({
    queryKey: [...FOLLOW_UPS_KEY, "deal", dealId],
    queryFn: () => unwrap(api.GET("/api/v1/crm/follow-ups", { params: { query: { deal_id: dealId, status: "open" } } })),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: keys.deals });
    void queryClient.invalidateQueries({ queryKey: FOLLOW_UPS_KEY });
  };
  const rescore = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/crm/deals/{deal_id}/score", { params: { path: { deal_id: dealId } } })),
    onSuccess: (result) => {
      toast(`Lead score ${result.score ?? "–"}.`);
      refresh();
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  const suggest = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/crm/deals/{deal_id}/follow-up", { params: { path: { deal_id: dealId } } })),
    onSuccess: () => refresh(),
    onError: (error) => toast(describeError(error), "error"),
  });

  const data = score.data;
  const intent = data?.ai_intent as { intent?: number; urgency?: string; summary?: string; signals?: string[]; read_at?: string } | null | undefined;
  const open = followUps.data?.[0];
  return (
    <div className="stack">
      <ErrorAlert error={score.error ?? followUps.error} />
      {score.isLoading ? (
        <SkeletonRows rows={3} />
      ) : (
        <section className="ai-score">
          <ScoreRing score={data?.score} />
          <div className="grow" style={{ minWidth: 0 }}>
            <div className="row between">
              <div>
                <span className="eyebrow">Lead score</span>
                <h3 style={{ margin: "2px 0 0" }}>
                  {data?.band ? `${BAND_LABELS[data.band]} lead` : "Not scored yet"}
                </h3>
              </div>
              {aiAllowed && (
                <Button size="sm" busy={rescore.isPending} onClick={() => rescore.mutate()} title="The AI reads the latest reply again, then every signal is recounted">
                  <Icon name="sparkles" /> Rescore with AI
                </Button>
              )}
            </div>
            {intent?.summary && (
              <p className="small muted" style={{ margin: "6px 0 0" }}>
                <Icon name="sparkles" style={{ width: 12, height: 12, verticalAlign: "-2px" }} /> AI on the latest reply: intent{" "}
                {Math.round((intent.intent ?? 0) * 100)}%, urgency {intent.urgency}
                {intent.read_at ? ` · ${formatRelative(intent.read_at)}` : ""}
              </p>
            )}
            <ul className="score-reasons">
              {(data?.reasons ?? []).map((reason) => (
                <li key={reason.label} className={reason.points > 0 ? "up" : reason.points < 0 ? "down" : "neutral"}>
                  <b>{formatPoints(reason.points)}</b>
                  <span>{reason.label}</span>
                </li>
              ))}
            </ul>
            {data?.scored_at && <div className="small muted">Scored {formatRelative(data.scored_at)}. Scores order your work list; they never send anything.</div>}
          </div>
        </section>
      )}

      <section className="ai-follow-up">
        <div className="row between">
          <h3 className="section-title" style={{ margin: 0 }}>
            <Icon name="reply" /> Follow-up
          </h3>
          {aiAllowed && (
            <Button size="sm" busy={suggest.isPending} onClick={() => suggest.mutate()}>
              <Icon name="sparkles" /> {open ? "Suggest again" : "Suggest a follow-up"}
            </Button>
          )}
        </div>
        {followUps.isLoading ? (
          <SkeletonRows rows={2} />
        ) : open ? (
          <FollowUpCard suggestion={open} canWrite={canWrite} onDone={refresh} />
        ) : (
          <p className="small muted">
            Deals that go quiet get a suggested next step and a drafted message every morning. You can also ask for one now.
          </p>
        )}
      </section>
    </div>
  );
}

export function FollowUpCard({ suggestion, canWrite, onDone, showDeal = false }: { suggestion: FollowUp; canWrite: boolean; onDone: () => void; showDeal?: boolean }) {
  const toast = useToast();
  const [copied, setCopied] = useState(false);
  const action = FOLLOW_UP_ACTIONS[suggestion.action] ?? { label: suggestion.action, icon: "mail" as const };
  const use = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/crm/follow-ups/{suggestion_id}/use", { params: { path: { suggestion_id: suggestion.id } } })),
    onSuccess: () => {
      toast("Task created with the draft. Review it before sending.");
      onDone();
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  const dismiss = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/crm/follow-ups/{suggestion_id}/dismiss", { params: { path: { suggestion_id: suggestion.id } } })),
    onSuccess: onDone,
    onError: (error) => toast(describeError(error), "error"),
  });
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(`${suggestion.subject}\n\n${suggestion.body}`);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast("Copying is not allowed in this browser.", "error");
    }
  };
  return (
    <article className={`follow-up-card ${suggestion.action}`}>
      <header>
        <span className="follow-up-icon">
          <Icon name={action.icon} />
        </span>
        <div className="grow" style={{ minWidth: 0 }}>
          {showDeal && (
            <div className="small muted truncate">
              {suggestion.company_name ?? suggestion.contact_name} · {suggestion.lane ?? suggestion.deal_title}
            </div>
          )}
          <strong>{action.label}</strong>
          {suggestion.written_by === "built_in" && (
            <span className="small muted"> · built-in message</span>
          )}
          <div className="small muted">{suggestion.reason}</div>
        </div>
        <Badge tone="amber">{suggestion.days_quiet}d quiet</Badge>
      </header>
      {suggestion.body && (
        <div className="follow-up-draft" dir="auto">
          <div className="small muted">
            Draft · {suggestion.language.toUpperCase()} · review before sending
          </div>
          {suggestion.subject && <strong>{suggestion.subject}</strong>}
          <p>{suggestion.body}</p>
        </div>
      )}
      {canWrite && (
        <footer className="row">
          <Button size="sm" variant="primary" busy={use.isPending} onClick={() => use.mutate()}>
            <Icon name="check" /> Make it a task
          </Button>
          {suggestion.body && (
            <Button size="sm" onClick={() => void copy()}>
              <Icon name="file" /> {copied ? "Copied" : "Copy draft"}
            </Button>
          )}
          <Button size="sm" variant="ghost" busy={dismiss.isPending} onClick={() => dismiss.mutate()}>
            Dismiss
          </Button>
        </footer>
      )}
    </article>
  );
}
