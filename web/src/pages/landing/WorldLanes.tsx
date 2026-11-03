/**
 * Hero map: the world as a dot matrix with freight lanes arcing between ports. Each lane draws
 * itself, a packet travels along it and a card names the lane (example lanes, not customer data).
 */
import { geoNaturalEarth1 } from "d3-geo";
import { useEffect, useMemo, useState } from "react";
import { useReducedMotion } from "../../components/motion";
import { HERO_LANES, arcPath } from "../../lib/landing";
import { WORLD_DOTS } from "./worldDots";

// The land dots in worldDots.ts are generated for this size and projection (scripts/world-dots.mjs).
const WIDTH = 960;
const HEIGHT = 470;
// Theme tokens (styles.css): deeper shades in the light theme. SVG attributes cannot read var(), so they go in style.
const MODE_COLORS = { ocean: "var(--s-mode-ocean)", air: "var(--s-mode-air)", road: "var(--s-mode-road)", rail: "var(--s-mode-rail)" } as const;

function projector() {
  return geoNaturalEarth1()
    .scale(178)
    .translate([WIDTH / 2, HEIGHT / 2 + 30]);
}

export function WorldLanes() {
  const reduced = useReducedMotion();
  const [active, setActive] = useState(0);
  const lanes = useMemo(() => {
    const projection = projector();
    return HERO_LANES.map((lane) => {
      const a = projection(lane.fromAt) ?? [0, 0];
      const b = projection(lane.toAt) ?? [0, 0];
      return { ...lane, a, b, path: arcPath(a, b, 0.24) };
    });
  }, []);

  useEffect(() => {
    if (reduced) return undefined;
    const timer = window.setInterval(() => setActive((current) => (current + 1) % lanes.length), 2600);
    return () => window.clearInterval(timer);
  }, [lanes.length, reduced]);

  const current = lanes[active];
  const mid: [number, number] = current ? midpoint(current.a, current.b) : [0, 0];
  return (
    <div className="world-lanes" aria-hidden>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="world-svg">
        <defs>
          <radialGradient id="world-glow" cx="50%" cy="45%" r="60%">
            <stop offset="0" stopColor="rgb(90 169 255 / 22%)" />
            <stop offset="1" stopColor="rgb(90 169 255 / 0%)" />
          </radialGradient>
          {lanes.map((lane, index) => (
            <linearGradient key={lane.from} id={`lane-${index}`} gradientUnits="userSpaceOnUse" x1={lane.a[0]} y1={lane.a[1]} x2={lane.b[0]} y2={lane.b[1]}>
              <stop offset="0" style={{ stopColor: MODE_COLORS[lane.mode] }} stopOpacity="0.1" />
              <stop offset="0.5" style={{ stopColor: MODE_COLORS[lane.mode] }} stopOpacity="0.95" />
              <stop offset="1" style={{ stopColor: "var(--s-strong)" }} stopOpacity="0.9" />
            </linearGradient>
          ))}
        </defs>
        <rect width={WIDTH} height={HEIGHT} fill="url(#world-glow)" />
        <path d={WORLD_DOTS} className="world-dots" />
        {lanes.map((lane, index) => (
          <g key={lane.from} className={index === active ? "lane on" : "lane"} style={{ animationDelay: `${index * 0.35}s` }}>
            <path d={lane.path} stroke={`url(#lane-${index})`} className="lane-path" pathLength={1} />
            {!reduced && (
              <circle r={3.2} style={{ fill: MODE_COLORS[lane.mode] }} className="lane-packet">
                <animateMotion dur={`${3.2 + (index % 3) * 0.7}s`} repeatCount="indefinite" path={lane.path} begin={`${index * 0.45}s`} />
              </circle>
            )}
            <circle cx={lane.a[0]} cy={lane.a[1]} r={3} className="port" />
            <circle cx={lane.b[0]} cy={lane.b[1]} r={3} className="port" />
            <circle cx={lane.b[0]} cy={lane.b[1]} r={3} className="port-ring" style={{ animationDelay: `${index * 0.4}s` }} />
          </g>
        ))}
      </svg>
      {current && (
        <div
          key={active}
          className="lane-card"
          style={{ left: `${(mid[0] / WIDTH) * 100}%`, top: `${(mid[1] / HEIGHT) * 100}%`, ["--lane-color" as string]: MODE_COLORS[current.mode] }}
        >
          <span className="lane-dot" />
          <strong>
            {current.from} → {current.to}
          </strong>
          <small>{current.label}</small>
        </div>
      )}
    </div>
  );
}

function midpoint(a: [number, number], b: [number, number]): [number, number] {
  const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
  return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2 - length * 0.16];
}
