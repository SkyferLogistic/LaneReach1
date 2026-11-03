/**
 * Lane map: great-circle lanes from the North America desk to every destination country, sized by
 * emails sent and coloured by reply rate, with traffic flowing along the busy ones.
 */
import { useQuery } from "@tanstack/react-query";
import { geoGraticule10, geoNaturalEarth1, geoPath } from "d3-geo";
import { useId, useMemo, useState } from "react";
import { ORIGIN, countryPoint, laneColor, laneWidth, loadWorld } from "../lib/geo";
import type { LonLat } from "../lib/geo";
import { countryFlag } from "../lib/freight";
import { percent } from "../lib/format";
import { useReducedMotion } from "./motion";

export interface LaneResult {
  country: string;
  name?: string;
  sent: number;
  replies: number;
  leads: number;
  reply_rate: number;
}

const WIDTH = 960;
const HEIGHT = 470;
const MAX_MOVING = 12;

export function LaneMap({
  lanes,
  origin: originCode,
  originLabel,
  height = 360,
}: {
  lanes: LaneResult[];
  /** ISO code of the country the lanes start from. */
  origin?: string;
  originLabel?: string;
  height?: number;
}) {
  const world = useQuery({ queryKey: ["world-110m"], queryFn: loadWorld, staleTime: Infinity });
  const reduced = useReducedMotion();
  const id = useId().replaceAll(":", "");
  const [hover, setHover] = useState<string | null>(null);

  const projection = useMemo(
    () => geoNaturalEarth1().rotate([-10, 0]).fitExtent([[6, 6], [WIDTH - 6, HEIGHT - 6]], { type: "Sphere" }),
    [],
  );
  const path = useMemo(() => geoPath(projection), [projection]);

  const start: LonLat = (world.data && originCode ? countryPoint(world.data, originCode) : null) ?? ORIGIN;
  const drawn = useMemo(() => {
    if (!world.data) return [];
    const busiest = Math.max(1, ...lanes.map((lane) => lane.sent));
    return lanes
      .map((lane) => {
        const point = countryPoint(world.data, lane.country);
        if (!point) return null;
        const line = { type: "LineString" as const, coordinates: [start, point] as LonLat[] };
        const end = projection(point);
        return {
          ...lane,
          point,
          end,
          d: path(line) ?? "",
          width: laneWidth(lane.sent, busiest),
          color: laneColor(lane.reply_rate, lane.sent),
        };
      })
      .filter((lane): lane is NonNullable<typeof lane> => Boolean(lane?.d && lane.end));
  }, [world.data, lanes, path, projection, start[0], start[1]]);

  const codes = useMemo(
    () => new Map([...(world.data?.byCode ?? new Map()).entries()].map(([code, feature]) => [feature, code] as const)),
    [world.data],
  );
  const planned = new Set(lanes.map((lane) => lane.country));
  const active = new Set(lanes.filter((lane) => lane.sent > 0).map((lane) => lane.country));
  const origin = projection(start);
  const hovered = drawn.find((lane) => lane.country === hover);
  const moving = [...drawn].filter((lane) => lane.sent > 0).sort((a, b) => b.sent - a.sent).slice(0, MAX_MOVING);

  return (
    <div className="lane-map" style={{ position: "relative" }}>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} style={{ width: "100%", height, display: "block" }} role="img" aria-label="Map of outreach lanes by destination country">
        <defs>
          <radialGradient id={`ocean-${id}`} cx="50%" cy="45%" r="70%">
            <stop offset="0" stopColor="var(--accent)" stopOpacity=".10" />
            <stop offset="1" stopColor="var(--accent)" stopOpacity=".02" />
          </radialGradient>
          <filter id={`glow-${id}`} x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="2.5" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        <path d={path({ type: "Sphere" }) ?? ""} fill={`url(#ocean-${id})`} stroke="var(--border)" />
        <path d={path(geoGraticule10()) ?? ""} fill="none" stroke="var(--border)" strokeOpacity=".5" strokeWidth=".5" />
        {world.data?.countries.map((feature, index) => {
          const code = codes.get(feature);
          const on = code ? active.has(code) : false;
          return (
            <path
              key={String(feature.id ?? index)}
              d={path(feature) ?? ""}
              className={on ? "map-country on" : code && planned.has(code) ? "map-country planned" : "map-country"}
              opacity={code === hover ? 1 : undefined}
            />
          );
        })}
        {drawn.map((lane) => (
          <path
            key={lane.country}
            id={`lane-${id}-${lane.country}`}
            d={lane.d}
            fill="none"
            stroke={lane.sent > 0 ? lane.color : "var(--text-3)"}
            strokeWidth={lane.country === hover ? lane.width + 1.5 : lane.width}
            strokeLinecap="round"
            className={lane.sent > 0 ? "lane-flow" : "lane-idle"}
            opacity={hover && hover !== lane.country ? 0.2 : lane.sent > 0 ? 0.95 : 0.45}
          />
        ))}
        {!reduced &&
          moving.map((lane, index) => (
            <circle key={`dot-${lane.country}`} r={2.6 + lane.width / 3} fill="#fff" filter={`url(#glow-${id})`}>
              <animateMotion dur={`${4 + (index % 5)}s`} begin={`${(index * 0.7) % 3}s`} repeatCount="indefinite">
                <mpath href={`#lane-${id}-${lane.country}`} />
              </animateMotion>
            </circle>
          ))}
        {drawn.map((lane) => {
          const [x, y] = lane.end ?? [0, 0];
          const radius = lane.sent > 0 ? 3 + lane.width : 2.5;
          return (
            <g
              key={`end-${lane.country}`}
              onMouseEnter={() => setHover(lane.country)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(lane.country)}
              onBlur={() => setHover(null)}
              tabIndex={0}
              role="button"
              aria-label={`${lane.name ?? lane.country}: ${lane.sent} sent, ${lane.replies} replies, ${lane.leads} leads`}
              style={{ cursor: "pointer", outline: "none" }}
            >
              <circle cx={x} cy={y} r={radius + 6} fill="transparent" />
              {lane.replies > 0 && <circle cx={x} cy={y} r={radius} className="map-ping" stroke={lane.color} />}
              <circle cx={x} cy={y} r={radius} fill={lane.sent > 0 ? lane.color : "var(--text-3)"} stroke="var(--surface)" strokeWidth="1.5" />
            </g>
          );
        })}
        {origin && (
          <g>
            <circle cx={origin[0]} cy={origin[1]} r={7} className="map-ping" stroke="var(--air)" />
            <circle cx={origin[0]} cy={origin[1]} r={6} fill="var(--air)" stroke="var(--surface)" strokeWidth="2" />
            <text x={origin[0] - 10} y={origin[1] - 12} textAnchor="end" className="map-label">
              {originLabel ?? "Origin"}
            </text>
          </g>
        )}
      </svg>
      {!world.data && <div className="skeleton" style={{ position: "absolute", inset: 0, opacity: 0.4 }} />}
      {hovered?.end && (
        <div
          className="card small map-tip"
          style={{ left: `${(hovered.end[0] / WIDTH) * 100}%`, top: `${(hovered.end[1] / HEIGHT) * 100}%` }}
        >
          <strong>
            {countryFlag(hovered.country)} {hovered.name ?? hovered.country}
          </strong>
          <div>{hovered.sent} sent · {hovered.replies} replies · {hovered.leads} leads</div>
          <div className="muted">{hovered.sent ? `${percent(hovered.reply_rate)} reply rate` : "No emails yet on this lane"}</div>
        </div>
      )}
    </div>
  );
}
