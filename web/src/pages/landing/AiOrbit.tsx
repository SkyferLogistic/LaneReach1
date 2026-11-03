/**
 * "AI command center": every AI skill orbits a glowing core. Skills in the running version circle on
 * the outer ring; the ones being built now sit on the inner ring, marked "Coming soon". Hover, focus
 * or the arrow keys pick a skill and the panel below says what it does. The rings stop turning while
 * you point at them, and stay still under reduced motion.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Icon } from "../../components/icons";
import type { IconName } from "../../components/icons";
import { useReducedMotion } from "../../components/motion";
import { orbitPosition, orbitSkills } from "../../lib/landing";
import type { OrbitSkill } from "../../lib/landing";
import { useSeen } from "./hooks";

const SKILL_ICONS: Record<string, IconName> = {
  classify_reply: "mail",
  extract_shipment_details: "parcel",
  draft_reply: "reply",
  localize_template: "translate",
  match_lane: "route",
  answer_help_question: "help",
  draft_workflow: "workflow",
  assess_lead: "leads",
  write_digest: "trending",
  suggest_follow_up: "clock",
};

/** Ring radius in percent of the stage, per ring (outer, inner). */
const RADIUS = [41, 24] as const;
/** Half the core's width (27%), in the same percent units. */
const CORE_RADIUS = 13.5;

export function AiOrbit({ tasks }: { tasks: string[] }) {
  const reduced = useReducedMotion();
  const root = useRef<HTMLDivElement>(null);
  const nodes = useRef<(HTMLButtonElement | null)[]>([]);
  const seen = useSeen(root);
  const skills = useMemo(() => orbitSkills(tasks), [tasks]);
  const [active, setActive] = useState(0);
  const [engaged, setEngaged] = useState(false);
  const rings: [OrbitSkill[], OrbitSkill[]] = [skills.filter((skill) => skill.ring === 0), skills.filter((skill) => skill.ring === 1)];
  const current = skills[Math.min(active, skills.length - 1)];
  const live = rings[0].length;

  // Walk through the skills on its own until the visitor takes over.
  useEffect(() => {
    if (reduced || engaged || !seen || skills.length < 2) return undefined;
    const timer = window.setInterval(() => setActive((index) => (index + 1) % skills.length), 2800);
    return () => window.clearInterval(timer);
  }, [reduced, engaged, seen, skills.length]);

  const onKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    const moves: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
    let next: number | null = null;
    if (event.key in moves) next = (active + (moves[event.key] ?? 0) + skills.length) % skills.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = skills.length - 1;
    if (next === null) return;
    event.preventDefault();
    setActive(next);
    nodes.current[next]?.focus();
  };

  if (!current) return null;
  return (
    <div
      ref={root}
      className={engaged || reduced ? "orbit held" : "orbit"}
      onPointerLeave={() => setEngaged(false)}
      onBlur={(event) => !event.currentTarget.contains(event.relatedTarget as Node | null) && setEngaged(false)}
    >
      <div className="orbit-stage" onPointerEnter={() => setEngaged(true)}>
        <div className="orbit-glow" aria-hidden />
        {rings.map((ring, ringIndex) =>
          ring.length === 0 ? null : (
            <div key={ringIndex} className={`orbit-ring ${ringIndex === 0 ? "outer" : "inner"}${ring.includes(current) ? " has-active" : ""}`}>
              <svg viewBox="0 0 100 100" className="orbit-lines" aria-hidden>
                <circle cx="50" cy="50" r={RADIUS[ringIndex as 0 | 1]} className="orbit-path" />
                {ring.map((skill, position) => {
                  if (skill.id !== current.id) return null;
                  const radius = RADIUS[ringIndex as 0 | 1];
                  const [x, y] = orbitPosition(position, ring.length, radius);
                  // The beam starts at the edge of the core, not under it.
                  const from = CORE_RADIUS / radius;
                  return <line key={skill.id} x1={50 + (x - 50) * from} y1={50 + (y - 50) * from} x2={x} y2={y} className="orbit-beam" pathLength={1} />;
                })}
              </svg>
              <ul>
                {ring.map((skill, position) => {
                  const index = skills.indexOf(skill);
                  const [x, y] = orbitPosition(position, ring.length, RADIUS[ringIndex as 0 | 1]);
                  const on = skill.id === current.id;
                  return (
                    <li key={skill.id} className={on ? "on" : undefined} style={{ left: `${x}%`, top: `${y}%` }}>
                      <button
                        ref={(element) => {
                          nodes.current[index] = element;
                        }}
                        type="button"
                        className={["orbit-node", on ? "on" : "", skill.live ? "" : "soon"].filter(Boolean).join(" ")}
                        aria-pressed={on}
                        onFocus={() => {
                          setEngaged(true);
                          setActive(index);
                        }}
                        onPointerEnter={() => setActive(index)}
                        onClick={() => setActive(index)}
                        onKeyDown={onKey}
                      >
                        <span className="orbit-upright">
                          <span className="orbit-dot">
                            <Icon name={SKILL_ICONS[skill.id] ?? "sparkles"} />
                          </span>
                          <span className="orbit-label">{skill.title}</span>
                          <span className="sr-only">
                            {skill.live ? "" : "Coming soon. "}
                            {skill.detail}
                          </span>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ),
        )}
        <div className="orbit-core" aria-hidden>
          <Icon name="sparkles" />
          <strong>LaneReach AI</strong>
          <small>
            {live} live{rings[1].length ? ` · ${rings[1].length} coming` : ""}
          </small>
        </div>
      </div>
      <div className="orbit-detail" aria-hidden key={current.id}>
        <span className="orbit-detail-icon">
          <Icon name={SKILL_ICONS[current.id] ?? "sparkles"} />
        </span>
        <div>
          <strong>
            {current.title}
            <span className={current.live ? "status-badge live" : "status-badge"}>{current.live ? "Live" : "Coming soon"}</span>
          </strong>
          <p>{current.detail || "An AI skill in the running version of LaneReach."}</p>
        </div>
      </div>
      <p className="orbit-hint">Point at a skill, or use Tab and the arrow keys.</p>
    </div>
  );
}
