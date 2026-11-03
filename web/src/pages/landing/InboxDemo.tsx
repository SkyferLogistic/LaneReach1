/**
 * The "watch the AI work" widget: a reply lands in the inbox, a scan line reads it, the
 * classification and the shipment fields appear (their words light up in the email), then the
 * steps LaneReach takes run one by one. Scripted example data, labelled as such.
 *
 * The email arrives whole, the way a real reply does, and the AI starts reading it at once: typing
 * it out letter by letter left visitors looking at an empty panel for seconds.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../../components/icons";
import { useReducedMotion } from "../../components/motion";
import { DEMO_SCENARIOS, highlightSources, scenarioAt } from "../../lib/landing";
import type { DemoScenario } from "../../lib/landing";

const TICK_MS = 100;
// Paced so a visitor can follow each step: reading ~1.2 s, then a field every 0.6 s, a step every 0.8 s.
const SCAN_TICKS = 12;
const FIELD_TICKS = 6;
const STEP_TICKS = 8;
const REPLY_CHARS_PER_TICK = 6;
/** How long the finished result stays up before the next example (time to read it). */
const HOLD_TICKS = 60;

interface Timeline {
  scanning: boolean;
  classified: boolean;
  fields: number;
  steps: number;
  outcome: boolean;
  reply: number;
  done: boolean;
}

function phases(scenario: DemoScenario) {
  const fieldsStart = SCAN_TICKS + 1;
  const fieldsEnd = fieldsStart + scenario.fields.length * FIELD_TICKS;
  const stepsEnd = fieldsEnd + scenario.steps.length * STEP_TICKS;
  const replyLength = scenario.reply?.length ?? 0;
  const replyEnd = stepsEnd + 1 + Math.ceil(replyLength / REPLY_CHARS_PER_TICK);
  return { fieldsStart, fieldsEnd, stepsEnd, replyLength, replyEnd, total: replyEnd + HOLD_TICKS };
}

/** What is visible after `tick` ticks of a scenario. */
function timeline(scenario: DemoScenario, tick: number): Timeline {
  const { fieldsStart, fieldsEnd, stepsEnd, replyLength, total } = phases(scenario);
  return {
    scanning: tick < SCAN_TICKS,
    classified: tick >= SCAN_TICKS,
    fields: Math.max(0, Math.min(scenario.fields.length, Math.floor((tick - fieldsStart) / FIELD_TICKS) + 1)),
    steps: Math.max(0, Math.min(scenario.steps.length, Math.floor((tick - fieldsEnd) / STEP_TICKS) + 1)),
    outcome: tick >= stepsEnd,
    reply: Math.max(0, Math.min(replyLength, (tick - stepsEnd - 1) * REPLY_CHARS_PER_TICK)),
    done: tick >= total,
  };
}

const FINAL = 100_000;

export function InboxDemo() {
  const reduced = useReducedMotion();
  const [index, setIndex] = useState(0);
  const [tick, setTick] = useState(0);
  // Bumped on every (re)start so the tab's progress bar starts over with the example.
  const [run, setRun] = useState(0);
  // Hovering holds the finished example on screen; it never pauses one that is still playing (a
  // click on a tab leaves the pointer here, and the example must still run).
  const [hovered, setHovered] = useState(false);
  const [visible, setVisible] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const scenario = scenarioAt(index);
  const state = useMemo(() => timeline(scenario, reduced ? FINAL : tick), [scenario, tick, reduced]);

  // Start playing only once the widget is on screen.
  useEffect(() => {
    if (!root.current || typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return undefined;
    }
    const observer = new IntersectionObserver((entries) => entries.some((entry) => entry.isIntersecting) && setVisible(true), { threshold: 0.25 });
    observer.observe(root.current);
    return () => observer.disconnect();
  }, []);

  const { replyEnd, total } = phases(scenario);
  const holding = hovered && tick >= replyEnd;
  useEffect(() => {
    if (reduced || !visible) return undefined;
    const timer = window.setInterval(() => setTick((current) => (hovered && current >= replyEnd ? current : current + 1)), TICK_MS);
    return () => window.clearInterval(timer);
  }, [reduced, visible, hovered, replyEnd]);

  useEffect(() => {
    if (state.done && !reduced) {
      setIndex((current) => (current + 1) % DEMO_SCENARIOS.length);
      setTick(0);
      setRun((current) => current + 1);
    }
  }, [state.done, reduced]);

  const restart = (next: number) => {
    setIndex(next);
    setTick(0);
    setRun((current) => current + 1);
  };
  const shownFields = scenario.fields.slice(0, state.fields);
  const parts = highlightSources(scenario.body, shownFields.map((field) => field.source));
  const progressMs = total * TICK_MS;

  return (
    <div className="inbox-demo" ref={root} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
      <div className="demo-tabs" role="tablist" aria-label="Example replies">
        {DEMO_SCENARIOS.map((item, position) => (
          <button key={item.id} type="button" role="tab" aria-selected={position === index} className={position === index ? "on" : ""} onClick={() => restart(position)}>
            {item.tab}
            {position === index && !reduced && (
              <span key={run} className="demo-tab-progress" style={{ animationDuration: `${progressMs}ms`, animationPlayState: holding || !visible ? "paused" : "running" }} />
            )}
          </button>
        ))}
        <span className="demo-badge">Example · scripted</span>
        <button type="button" className="demo-replay" onClick={() => restart(index)} aria-label="Replay">
          <Icon name="undo" />
        </button>
      </div>
      <div className="demo-grid">
        <div className="demo-mail">
          <div className="demo-mail-head">
            <span className="demo-avatar">{scenario.from.slice(0, 2).toUpperCase()}</span>
            <div>
              <strong>
                {scenario.from} · {scenario.company}
              </strong>
              <small>{scenario.subject}</small>
            </div>
            <span className="demo-lang">{scenario.language}</span>
          </div>
          <p className="demo-body" dir="auto">
            {parts.map((part, position) =>
              part.mark ? (
                <mark key={position} className="demo-mark">
                  {part.text}
                </mark>
              ) : (
                <span key={position}>{part.text}</span>
              ),
            )}
          </p>
          {state.scanning && visible && !reduced && <span key={run} className="demo-scan" />}
        </div>
        <div className="demo-ai">
          <div className={state.classified ? "demo-class in" : "demo-class"}>
            <Icon name="sparkles" />
            <div className="grow">
              <small>Classified as</small>
              <strong>{scenario.bucket}</strong>
            </div>
            <div className="demo-confidence">
              <span style={{ width: state.classified ? `${scenario.confidence * 100}%` : "0%" }} />
              <small>{Math.round(scenario.confidence * 100)}%</small>
            </div>
          </div>
          {scenario.fields.length > 0 && (
            <div className="demo-fields">
              {scenario.fields.map((field, position) => (
                <div key={field.label} className={position < state.fields ? "demo-field in" : "demo-field"}>
                  <small>{field.label}</small>
                  <strong>{field.value}</strong>
                </div>
              ))}
            </div>
          )}
          <ol className="demo-steps">
            {scenario.steps.map((step, position) => (
              <li key={step} className={position < state.steps ? "done" : position === state.steps && state.classified ? "doing" : ""}>
                <span className="demo-step-dot">{position < state.steps ? <Icon name="check" /> : null}</span>
                {step}
              </li>
            ))}
          </ol>
          <div className={state.outcome ? `demo-outcome ${scenario.outcomeTone} in` : `demo-outcome ${scenario.outcomeTone}`}>{scenario.outcome}</div>
          {scenario.reply && state.reply > 0 && (
            <div className="demo-reply">
              <small>
                <Icon name="reply" /> Draft reply, waiting for review
              </small>
              <p>
                {scenario.reply.slice(0, state.reply)}
                {state.reply < scenario.reply.length && <span className="demo-caret" />}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
