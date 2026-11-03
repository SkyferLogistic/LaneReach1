/**
 * Small animated freight scenes (inline SVG, theme colours, no image files): used by empty
 * states, the login and the mode strip. Motion stops under `prefers-reduced-motion`.
 */
import { useId } from "react";
import { useReducedMotion } from "./motion";

export type SceneKind = "plane" | "ship" | "truck" | "train" | "warehouse" | "parcel" | "customs" | "inbox" | "chart";

const PLANE = "M8,0 L5,-1.1 L1.5,-1.1 L-2,-6 L-3.8,-6 L-1.6,-1.1 L-5.5,-1.1 L-7,-3.2 L-8.2,-3.2 L-7.2,0 L-8.2,3.2 L-7,3.2 L-5.5,1.1 L-1.6,1.1 L-3.8,6 L-2,6 L1.5,1.1 L5,1.1 Z";

function Truck({ x = 0, y = 0 }: { x?: number; y?: number }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <rect x="0" y="0" width="34" height="18" rx="2" fill="var(--accent)" />
      <rect x="4" y="4" width="26" height="3" rx="1" fill="rgb(255 255 255 / 35%)" />
      <path d="M34 6h9l6 6v6H34z" fill="var(--brand-1, #19c3b1)" />
      <rect x="37" y="8" width="6" height="4" rx="1" fill="rgb(255 255 255 / 70%)" />
      <circle cx="9" cy="20" r="3.5" fill="var(--text)" />
      <circle cx="40" cy="20" r="3.5" fill="var(--text)" />
    </g>
  );
}

function Train({ x = 0, y = 0 }: { x?: number; y?: number }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      {[0, 30, 60].map((offset, index) => (
        <g key={offset} transform={`translate(${offset} 0)`}>
          <rect x="0" y="0" width="27" height="15" rx="2" fill={index === 2 ? "var(--violet)" : "var(--ocean)"} />
          <rect x="3" y="3" width="21" height="4" rx="1" fill="rgb(255 255 255 / 30%)" />
          <circle cx="6" cy="17" r="2.6" fill="var(--text)" />
          <circle cx="21" cy="17" r="2.6" fill="var(--text)" />
        </g>
      ))}
    </g>
  );
}

function Ship({ x = 0, y = 0 }: { x?: number; y?: number }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <path d="M0 14h58l-7 10H6z" fill="var(--ocean)" />
      {[6, 18, 30].map((left, index) => (
        <rect key={left} x={left} y={4} width="11" height="10" rx="1" fill={["var(--air)", "var(--accent)", "var(--violet)"][index]} />
      ))}
      <rect x="44" y="0" width="7" height="14" rx="1" fill="var(--text-2)" />
    </g>
  );
}

/** One small scene; `kind` picks the subject. */
export function FreightScene({ kind = "plane", className = "freight-scene" }: { kind?: SceneKind; className?: string }) {
  const id = useId().replaceAll(":", "");
  const reduced = useReducedMotion();
  const move = reduced ? "" : "scene-move";
  return (
    <svg className={className} viewBox="0 0 180 80" aria-hidden>
      <defs>
        <linearGradient id={`sky-${id}`} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor="var(--accent-soft)" />
          <stop offset="1" stopColor="transparent" />
        </linearGradient>
      </defs>
      <rect x="0" y="0" width="180" height="80" rx="14" fill={`url(#sky-${id})`} />
      {kind === "plane" && (
        <>
          <path id={`arc-${id}`} d="M14,62 C60,8 120,8 166,62" fill="none" stroke="var(--border-strong)" strokeDasharray="3 5" strokeWidth="1.5" />
          <circle cx="14" cy="62" r="4" fill="var(--accent)" />
          <circle cx="166" cy="62" r="4" fill="var(--air)" />
          <path d={PLANE} fill="var(--air)" transform="scale(1.3)">
            {!reduced && (
              <animateMotion dur="5s" repeatCount="indefinite" rotate="auto">
                <mpath href={`#arc-${id}`} />
              </animateMotion>
            )}
          </path>
        </>
      )}
      {kind === "ship" && (
        <>
          <path className={reduced ? "" : "scene-wave"} d="M0 62 q10 -5 20 0 t20 0 t20 0 t20 0 t20 0 t20 0 t20 0 t20 0 t20 0 t20 0" fill="none" stroke="var(--ocean)" strokeWidth="2" opacity=".5" />
          <g className={move}><Ship x={20} y={36} /></g>
        </>
      )}
      {kind === "truck" && (
        <>
          <rect x="0" y="62" width="180" height="4" fill="var(--surface-3)" />
          <path d="M0 64h180" stroke="var(--border-strong)" strokeDasharray="10 8" strokeWidth="1.5" className={reduced ? "" : "scene-road"} />
          <g className={move}><Truck x={20} y={38} /></g>
        </>
      )}
      {kind === "train" && (
        <>
          <path d="M0 64h180M0 68h180" stroke="var(--border-strong)" strokeWidth="1.5" />
          <path d="M0 66h180" stroke="var(--text-3)" strokeDasharray="2 6" strokeWidth="4" />
          <g className={move}><Train x={10} y={44} /></g>
        </>
      )}
      {kind === "warehouse" && (
        <>
          <path d="M40 66V34l50-18 50 18v32z" fill="var(--surface-3)" stroke="var(--border-strong)" />
          <rect x="70" y="44" width="40" height="22" fill="var(--surface-2)" stroke="var(--border-strong)" />
          {[0, 1, 2].map((row) => (
            <rect key={row} className={reduced ? "" : "scene-stack"} style={{ animationDelay: `${row * 0.4}s` }} x={76 + row * 10} y={54 - row * 6} width="10" height="8" rx="1" fill={["var(--air)", "var(--accent)", "var(--ocean)"][row]} />
          ))}
        </>
      )}
      {kind === "parcel" && (
        <g className={reduced ? "" : "scene-bounce"}>
          <path d="M70 34l20-10 20 10v22l-20 10-20-10z" fill="var(--air-soft)" stroke="var(--air)" />
          <path d="M70 34l20 10 20-10M90 44v22" fill="none" stroke="var(--air)" />
        </g>
      )}
      {kind === "customs" && (
        <>
          <rect x="54" y="22" width="72" height="46" rx="4" fill="var(--surface)" stroke="var(--border-strong)" />
          {[32, 40, 48].map((row) => <rect key={row} x="62" y={row} width="40" height="3" rx="1.5" fill="var(--surface-3)" />)}
          <g className={reduced ? "" : "scene-stamp"}>
            <circle cx="112" cy="54" r="11" fill="none" stroke="var(--success)" strokeWidth="2.5" />
            <path d="M106 54l4 4 8-8" fill="none" stroke="var(--success)" strokeWidth="2.5" />
          </g>
        </>
      )}
      {kind === "inbox" && (
        <>
          <path d="M50 44l10-20h60l10 20v20H50z" fill="var(--surface)" stroke="var(--border-strong)" />
          <path d="M50 44h24l4 8h24l4-8h24" fill="none" stroke="var(--border-strong)" />
          <g className={reduced ? "" : "scene-drop"}>
            <rect x="74" y="12" width="32" height="20" rx="2" fill="var(--accent-soft)" stroke="var(--accent)" />
            <path d="M74 14l16 10 16-10" fill="none" stroke="var(--accent)" />
          </g>
        </>
      )}
      {kind === "chart" && (
        <>
          {[18, 30, 24, 40, 34, 48].map((height, index) => (
            <rect key={index} className={reduced ? "" : "scene-grow"} style={{ animationDelay: `${index * 0.08}s` }} x={42 + index * 17} y={66 - height} width="11" height={height} rx="2" fill={index === 5 ? "var(--accent)" : "var(--accent-soft)"} />
          ))}
        </>
      )}
    </svg>
  );
}

/** The four transport modes moving side by side (login and dashboard banners). */
export function ModeStrip() {
  const reduced = useReducedMotion();
  return (
    <div className={reduced ? "mode-strip" : "mode-strip moving"} aria-hidden>
      {(["plane", "ship", "truck", "train"] as const).map((kind) => (
        <FreightScene key={kind} kind={kind} className="freight-scene tile" />
      ))}
    </div>
  );
}
