/**
 * Motion: animated freight routes (a plane on an air lane, a ship on a sea lane), count-up
 * numbers and skeleton placeholders. Everything honours `prefers-reduced-motion`.
 */
import { useEffect, useId, useRef, useState } from "react";
import type { CSSProperties } from "react";

export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const query = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!query) return undefined;
    const listener = () => setReduced(query.matches);
    query.addEventListener("change", listener);
    return () => query.removeEventListener("change", listener);
  }, []);
  return Boolean(reduced);
}

interface Port {
  name: string;
  x: number;
  y: number;
}

// Rough positions on an 800 × 300 equirectangular-ish canvas (North America → Europe → Gulf → Asia).
const PORTS: Port[] = [
  { name: "Toronto", x: 170, y: 92 },
  { name: "Houston", x: 150, y: 150 },
  { name: "Rotterdam", x: 392, y: 78 },
  { name: "Istanbul", x: 455, y: 110 },
  { name: "Jebel Ali", x: 522, y: 158 },
  { name: "Mumbai", x: 570, y: 175 },
  { name: "Colombo", x: 590, y: 212 },
  { name: "Shanghai", x: 690, y: 128 },
  { name: "Santos", x: 272, y: 250 },
];

function arc(from: Port, to: Port, lift: number): string {
  const midX = (from.x + to.x) / 2;
  const midY = Math.min(from.y, to.y) - lift;
  return `M${from.x},${from.y} Q${midX},${midY} ${to.x},${to.y}`;
}

const port = (name: string) => PORTS.find((item) => item.name === name) as Port;

const AIR_LANES = [
  arc(port("Toronto"), port("Istanbul"), 70),
  arc(port("Toronto"), port("Jebel Ali"), 95),
  arc(port("Houston"), port("Rotterdam"), 60),
];
const SEA_LANES = [
  `M${port("Houston").x},${port("Houston").y} C230,200 330,190 392,120 S470,150 522,158`,
  `M${port("Rotterdam").x},${port("Rotterdam").y} C430,150 520,230 590,212 S650,160 690,128`,
  `M${port("Santos").x},${port("Santos").y} C360,260 480,240 570,175`,
];

// Nose at +x: `animateMotion rotate="auto"` turns +x along the direction of travel.
const PLANE =
  "M8,0 L5,-1.1 L1.5,-1.1 L-2,-6 L-3.8,-6 L-1.6,-1.1 L-5.5,-1.1 L-7,-3.2 L-8.2,-3.2 L-7.2,0 " +
  "L-8.2,3.2 L-7,3.2 L-5.5,1.1 L-1.6,1.1 L-3.8,6 L-2,6 L1.5,1.1 L5,1.1 Z";
const SHIP = "M-8,1 L8,1 L6,4 L-6,4 Z M-5,1 L-5,-2 L2,-2 L2,-4 L4,-4 L4,1 Z";

/**
 * Decorative map of freight lanes with a plane and a ship travelling on them.
 *
 * @param density `full` for the login hero (labels, all lanes), `compact` for banners.
 */
export function RouteArt({
  density = "full",
  className = "route-art",
}: {
  density?: "full" | "compact";
  className?: string;
}) {
  const id = useId().replaceAll(":", "");
  const reduced = useReducedMotion();
  const air = density === "full" ? AIR_LANES : AIR_LANES.slice(0, 2);
  const sea = density === "full" ? SEA_LANES : SEA_LANES.slice(0, 2);
  return (
    // In the page flow the map is cropped to the ports; as a backdrop it covers the whole box.
    <svg
      className={className}
      viewBox={className.includes("inline") ? "110 30 620 240" : "0 0 800 300"}
      preserveAspectRatio="xMidYMid slice"
      aria-hidden
    >
      <defs>
        <linearGradient id="routeGradient" x1="0" x2="1">
          <stop offset="0" stopColor="#19c3b1" />
          <stop offset=".6" stopColor="#8fb0ff" />
          <stop offset="1" stopColor="#c7b8ff" />
        </linearGradient>
        <pattern id={`dots-${id}`} width="10" height="10" patternUnits="userSpaceOnUse">
          <circle cx="1.5" cy="1.5" r="1" fill="rgb(255 255 255 / 9%)" />
        </pattern>
      </defs>
      <rect width="800" height="300" fill={`url(#dots-${id})`} />
      {sea.map((d, index) => (
        <path key={`sea-${index}`} id={`sea-${id}-${index}`} d={d} className="route-path soft" />
      ))}
      {air.map((d, index) => (
        <path key={`air-${index}`} id={`air-${id}-${index}`} d={d} className="route-path" />
      ))}
      {PORTS.map((item, index) => (
        <g key={item.name}>
          <circle cx={item.x} cy={item.y} r={2.6} className="route-port" />
          <circle
            cx={item.x}
            cy={item.y}
            r={3}
            className="route-port-ring"
            style={{ animationDelay: `${(index * 0.37) % 2.6}s` }}
          />
          {density === "full" && (
            <text x={item.x + 6} y={item.y - 6} fontSize="9" fill="rgb(255 255 255 / 55%)">
              {item.name}
            </text>
          )}
        </g>
      ))}
      {!reduced &&
        air.map((_, index) => (
          <path key={`plane-${index}`} d={PLANE} className="vehicle air">
            <animateMotion dur={`${9 + index * 3}s`} begin={`${index * 2.2}s`} repeatCount="indefinite" rotate="auto">
              <mpath href={`#air-${id}-${index}`} />
            </animateMotion>
          </path>
        ))}
      {!reduced &&
        sea.map((_, index) => (
          <path key={`ship-${index}`} d={SHIP} className="vehicle ocean">
            <animateMotion dur={`${22 + index * 5}s`} begin={`${index * 3}s`} repeatCount="indefinite" rotate="auto">
              <mpath href={`#sea-${id}-${index}`} />
            </animateMotion>
          </path>
        ))}
    </svg>
  );
}

/** A small empty-state illustration: one lane with a ship waiting at port. */
export function EmptyArt() {
  const reduced = useReducedMotion();
  const id = useId().replaceAll(":", "");
  return (
    <svg className="empty-art" viewBox="0 0 180 64" aria-hidden>
      <path id={`empty-${id}`} d="M10,50 C50,10 130,10 170,50" fill="none" stroke="var(--border-strong)" strokeDasharray="3 5" strokeWidth="1.5" />
      <circle cx="10" cy="50" r="4" fill="var(--accent)" />
      <circle cx="170" cy="50" r="4" fill="var(--brand-1)" />
      <path d={PLANE} fill="var(--accent)">
        {!reduced && (
          <animateMotion dur="5s" repeatCount="indefinite" rotate="auto">
            <mpath href={`#empty-${id}`} />
          </animateMotion>
        )}
      </path>
    </svg>
  );
}

/** Animate a number from its previous value (or 0) to `value`. */
export function useCountUp(value: number, duration = 900): number {
  const reduced = useReducedMotion();
  const [shown, setShown] = useState(reduced ? value : 0);
  const from = useRef(0);
  useEffect(() => {
    if (reduced) {
      setShown(value);
      from.current = value;
      return undefined;
    }
    const start = performance.now();
    const origin = from.current;
    let frame = 0;
    const tick = (time: number) => {
      const progress = Math.min((time - start) / duration, 1);
      const eased = 1 - (1 - progress) ** 3;
      setShown(origin + (value - origin) * eased);
      if (progress < 1) frame = requestAnimationFrame(tick);
      else from.current = value;
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value, duration, reduced]);
  return shown;
}

const integer = new Intl.NumberFormat();

export function CountUp({
  value,
  format = (current) => integer.format(Math.round(current)),
}: {
  value: number;
  format?: (current: number) => string;
}) {
  const shown = useCountUp(value);
  return <>{format(shown)}</>;
}

export function Skeleton({ width = "100%", height = 14, style }: { width?: number | string; height?: number; style?: CSSProperties }) {
  return <span className="skeleton" style={{ display: "block", width, height, ...style }} />;
}

/** Placeholder rows while a table loads. */
export function SkeletonRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="stack" style={{ padding: 16 }} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="row nowrap" style={{ gap: 12 }}>
          <Skeleton width={36} height={36} style={{ borderRadius: 10 }} />
          <div className="stack tight" style={{ flex: 1 }}>
            <Skeleton width={`${60 - ((index * 13) % 25)}%`} />
            <Skeleton width={`${35 + ((index * 17) % 30)}%`} height={10} />
          </div>
        </div>
      ))}
    </div>
  );
}
