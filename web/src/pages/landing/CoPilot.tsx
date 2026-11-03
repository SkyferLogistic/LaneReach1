/**
 * "AI co-pilot": the three AI features being built now (lead scoring, the weekly digest and
 * follow-ups for quiet deals), each played as a short scripted example. The tabs advance on their
 * own with a progress bar, pause on hover or focus (or with the pause button) and stay still under
 * reduced motion. Every visual is example data and says so.
 */
import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Icon } from "../../components/icons";
import type { IconName } from "../../components/icons";
import { CountUp, useReducedMotion } from "../../components/motion";
import {
  DIGEST_EXAMPLE,
  LEAD_SCORE_EXAMPLE,
  QUIET_DEAL_EXAMPLE,
  aiTaskStatus,
  isQuietDeal,
  scoreBand,
  scoreLead,
  sparklinePath,
  weekTrend,
} from "../../lib/landing";
import { useSeen, useTicker } from "./hooks";

const TICK_MS = 100;
/** Under reduced motion every panel shows its final frame. */
const FINAL = 100_000;

interface Panel {
  id: "assess_lead" | "write_digest" | "suggest_follow_up";
  tab: string;
  icon: IconName;
  title: string;
  text: string;
  points: string[];
  /** Ticks the panel plays before the carousel moves on. */
  length: number;
}

const PANELS: Panel[] = [
  {
    id: "assess_lead",
    tab: "AI lead scoring",
    icon: "leads",
    title: "Know which deal to work first",
    text: "Every deal gets a score from 0 to 100, built from what the reply actually says, with the reasons in plain words so your team can see why.",
    points: ["Intent read by AI from the reply", "How complete the shipment details are", "How fast they replied, engagement and company history"],
    length: 95,
  },
  {
    id: "write_digest",
    tab: "Weekly AI digest",
    icon: "trending",
    title: "Your week, summed up in a few sentences",
    text: "The AI reads the week's numbers and writes a short summary: lanes heating up, win and loss by lane, and offices that answer slowly.",
    points: ["Lanes heating up or cooling down", "Win and loss by lane", "Offices replying slowly against your target"],
    length: 120,
  },
  {
    id: "suggest_follow_up",
    tab: "Quiet-deal follow-ups",
    icon: "clock",
    title: "No deal goes quiet unnoticed",
    text: "When a deal has had no activity for the days you set, the AI suggests the next step and drafts a follow-up. A person reviews it before anything is sent.",
    points: ["You choose after how many days a deal is quiet", "A suggested next step, in plain words", "A drafted follow-up waiting for review"],
    length: 110,
  },
];

export function CoPilot({ tasks }: { tasks: string[] }) {
  const reduced = useReducedMotion();
  const root = useRef<HTMLDivElement>(null);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const seen = useSeen(root);
  const [index, setIndex] = useState(0);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [stopped, setStopped] = useState(false);
  const paused = hovered || focused || stopped;
  const [tick, setTick] = useTicker(seen && !paused, TICK_MS);
  const panel = PANELS[index] ?? PANELS[0];
  const shownTick = reduced ? FINAL : tick;

  useEffect(() => {
    if (!reduced && panel && tick >= panel.length) {
      setIndex((current) => (current + 1) % PANELS.length);
      setTick(0);
    }
  }, [tick, panel, reduced, setTick]);

  if (!panel) return null;
  const choose = (next: number) => {
    setIndex(next);
    setTick(0);
  };
  const onTabKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    const moves: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
    let next: number | null = null;
    if (event.key in moves) next = (index + (moves[event.key] ?? 0) + PANELS.length) % PANELS.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = PANELS.length - 1;
    if (next === null) return;
    event.preventDefault();
    choose(next);
    tabs.current[next]?.focus();
  };
  const status = aiTaskStatus(panel.id, tasks);

  return (
    <div
      ref={root}
      className="copilot glow-border"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocus={() => setFocused(true)}
      onBlur={(event) => !event.currentTarget.contains(event.relatedTarget as Node | null) && setFocused(false)}
    >
      <div className="copilot-tabs" role="tablist" aria-label="AI co-pilot features">
        {PANELS.map((item, position) => (
          <button
            key={item.id}
            ref={(element) => {
              tabs.current[position] = element;
            }}
            type="button"
            role="tab"
            id={`copilot-tab-${item.id}`}
            aria-selected={position === index}
            aria-controls="copilot-panel"
            tabIndex={position === index ? 0 : -1}
            className={position === index ? "on" : ""}
            onClick={() => choose(position)}
            onKeyDown={onTabKey}
          >
            <Icon name={item.icon} />
            <span>{item.tab}</span>
            <i className="copilot-progress" style={{ transform: `scaleX(${position === index ? (reduced ? 1 : Math.min(1, tick / item.length)) : 0})` }} />
          </button>
        ))}
        {!reduced && (
          <button type="button" className="copilot-pause" onClick={() => setStopped(!stopped)} aria-label={stopped ? "Play the examples" : "Pause the examples"}>
            <Icon name={stopped ? "play" : "pause"} />
          </button>
        )}
      </div>
      <div className="copilot-body" role="tabpanel" id="copilot-panel" aria-labelledby={`copilot-tab-${panel.id}`} key={panel.id}>
        <div className="copilot-copy">
          <div className="copilot-badges">
            <span className={status === "live" ? "status-badge live" : "status-badge"}>{status === "live" ? "Live" : "Coming soon"}</span>
            <span className="example-badge">Example data</span>
          </div>
          <h3>{panel.title}</h3>
          <p>{panel.text}</p>
          <ul>
            {panel.points.map((point) => (
              <li key={point}>
                <Icon name="check" /> {point}
              </li>
            ))}
          </ul>
        </div>
        <div className="copilot-stage">
          <span className="example-corner">Example</span>
          {panel.id === "assess_lead" && <LeadScore tick={shownTick} />}
          {panel.id === "write_digest" && <Digest tick={shownTick} />}
          {panel.id === "suggest_follow_up" && <QuietDeal tick={shownTick} />}
        </div>
      </div>
    </div>
  );
}

const RING = 2 * Math.PI * 52;

/** A deal card, a score ring that climbs as the reasons arrive one by one. */
function LeadScore({ tick }: { tick: number }) {
  const example = LEAD_SCORE_EXAMPLE;
  const shown = Math.max(0, Math.min(example.reasons.length, Math.floor((tick - 8) / 9) + 1));
  const reasons = example.reasons.slice(0, shown);
  const score = scoreLead(example.base, reasons);
  const band = scoreBand(score);
  return (
    <div className="score">
      <div className="score-deal">
        <span className="demo-avatar">{example.contact.slice(0, 2).toUpperCase()}</span>
        <div>
          <strong>{example.company}</strong>
          <small>
            {example.lane} · {example.load}
          </small>
        </div>
      </div>
      <div className="score-main">
        <svg viewBox="0 0 120 120" className={`score-ring ${band.tone}`}>
          <defs>
            <linearGradient id="score-gradient" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stopColor="#8fd3ff" />
              <stop offset=".5" stopColor="#b69cff" />
              <stop offset="1" stopColor="#6ff0d2" />
            </linearGradient>
          </defs>
          <circle cx="60" cy="60" r="52" className="track" />
          <circle cx="60" cy="60" r="52" className="value" strokeDasharray={RING} strokeDashoffset={RING * (1 - score / 100)} />
        </svg>
        <div className="score-number">
          <strong>
            <CountUp value={score} />
          </strong>
          <span className={`score-band ${band.tone}`}>{shown === example.reasons.length ? band.label : "Scoring…"}</span>
        </div>
      </div>
      <ul className="score-reasons">
        {reasons.map((reason) => (
          <li key={reason.text} className={reason.points < 0 ? "down" : "up"}>
            <b>{reason.points > 0 ? `+${reason.points}` : `−${Math.abs(reason.points)}`}</b>
            <span>{reason.text}</span>
            <small>{reason.signal}</small>
          </li>
        ))}
      </ul>
    </div>
  );
}

const MODE_ICON: Record<string, IconName> = { ocean: "ship", air: "plane", road: "truck", rail: "train" };

/** The week's lanes appear with their sparklines, then the AI writes the headline. */
function Digest({ tick }: { tick: number }) {
  const example = DIGEST_EXAMPLE;
  const typeStart = 16;
  const typed = Math.max(0, Math.min(example.headline.length, (tick - typeStart) * 4));
  const slow = example.slowOffice;
  return (
    <div className="digest">
      <div className="digest-head">
        <Icon name="sparkles" />
        <div>
          <strong>Weekly digest · {example.week}</strong>
          <small>Written by AI from this week&apos;s numbers</small>
        </div>
      </div>
      <p className="digest-headline">
        {tick < typeStart ? <span className="digest-wait">Reading the week&apos;s numbers…</span> : example.headline.slice(0, typed)}
        {tick >= typeStart && typed < example.headline.length && <span className="demo-caret" />}
      </p>
      <ul className="digest-lanes">
        {example.lanes.map((lane, position) => {
          const trend = weekTrend(lane.weeks);
          const on = tick >= 2 + position * 3;
          return (
            <li key={lane.lane} className={on ? "in" : ""}>
              <Icon name={MODE_ICON[lane.mode] ?? "route"} />
              <strong>{lane.lane}</strong>
              <svg viewBox="0 0 90 26" className={trend >= 0 ? "spark up" : "spark down"} aria-hidden>
                <path d={sparklinePath(lane.weeks, 90, 26, 3)} pathLength={1} />
              </svg>
              <span className={trend >= 0 ? "trend up" : "trend down"}>
                {trend >= 0 ? "+" : "−"}
                {Math.abs(trend)}%
              </span>
              <small>
                {lane.won}/{lane.quoted} won
              </small>
            </li>
          );
        })}
      </ul>
      <div className={tick >= 11 ? "digest-slow in" : "digest-slow"}>
        <Icon name="clock" />
        <span>
          <b>{slow.office} desk</b> · {slow.hours} h to first reply
        </span>
        <i style={{ ["--fill" as string]: `${Math.min(100, (slow.target / slow.hours) * 100)}%` }} />
        <small>target {slow.target} h</small>
      </div>
    </div>
  );
}

/** A quoted deal counts up its quiet days, turns amber, then the AI suggestion and draft slide in. */
function QuietDeal({ tick }: { tick: number }) {
  const example = QUIET_DEAL_EXAMPLE;
  const days = Math.min(example.shownDays, Math.floor(tick / 5));
  const quiet = isQuietDeal(days, example.quietAfterDays);
  const suggest = tick >= 38;
  const draftStart = 48;
  const typed = Math.max(0, Math.min(example.draft.length, (tick - draftStart) * 4));
  return (
    <div className="quiet">
      <div className={quiet ? "quiet-deal is-quiet" : "quiet-deal"}>
        <div className="quiet-top">
          <span className="demo-avatar">{example.contact.slice(0, 2).toUpperCase()}</span>
          <div>
            <strong>{example.company}</strong>
            <small>
              {example.lane} · {example.load}
            </small>
          </div>
          <span className="quiet-stage">{example.stage}</span>
        </div>
        <div className="quiet-timer">
          <Icon name="clock" />
          <span>
            <b>{days}</b> {days === 1 ? "day" : "days"} without activity
          </span>
          <i style={{ ["--fill" as string]: `${Math.min(100, (days / example.quietAfterDays) * 100)}%` }} />
          {quiet && <em>Quiet</em>}
        </div>
      </div>
      {suggest && (
        <div className="quiet-suggest">
          <small>
            <Icon name="sparkles" /> Suggested next step
          </small>
          <p>{example.nextStep}</p>
        </div>
      )}
      {typed > 0 && (
        <div className="demo-reply quiet-draft">
          <small>
            <Icon name="reply" /> Draft follow-up · waits for a person
          </small>
          <p>
            {example.draft.slice(0, typed)}
            {typed < example.draft.length && <span className="demo-caret" />}
          </p>
          {typed >= example.draft.length && (
            <div className="quiet-actions" aria-hidden>
              <span>Review &amp; send</span>
              <span>Dismiss</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
