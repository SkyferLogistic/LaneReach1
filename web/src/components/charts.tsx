/** SVG charts: sparkline, trend (area + line), donut and meter. Maths lives in lib/charts. */
import { useId, useMemo, useState } from "react";
import { areaPath, axisTicks, donutSegments, linePath, niceMax, scalePoints } from "../lib/charts";

export function Sparkline({
  values,
  color = "var(--accent)",
  width = 96,
  height = 28,
}: {
  values: number[];
  color?: string;
  width?: number;
  height?: number;
}) {
  const id = useId().replaceAll(":", "");
  const points = useMemo(() => scalePoints(values, width, height - 2), [values, width, height]);
  if (points.length < 2) return <svg width={width} height={height} aria-hidden />;
  return (
    <svg width={width} height={height} viewBox={`0 -1 ${width} ${height}`} aria-hidden className="chart">
      <defs>
        <linearGradient id={`spark-${id}`} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity=".28" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={areaPath(points, height - 2)} fill={`url(#spark-${id})`} />
      <path d={linePath(points)} fill="none" stroke={color} strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

export interface TrendSeries {
  key: string;
  label: string;
  color: string;
  values: number[];
  fill?: boolean;
}

/** Area/line chart for daily series with a hover read-out. */
export function TrendChart({
  labels,
  series,
  height = 220,
}: {
  labels: string[];
  series: TrendSeries[];
  height?: number;
}) {
  const id = useId().replaceAll(":", "");
  const [hover, setHover] = useState<number | null>(null);
  const width = 640;
  const pad = { top: 10, right: 8, bottom: 22, left: 30 };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const max = niceMax(Math.max(1, ...series.flatMap((item) => item.values)));
  const ticks = axisTicks(labels, labels.length > 14 ? 6 : labels.length);
  const gridValues = [0, max / 2, max];
  const step = labels.length > 1 ? innerWidth / (labels.length - 1) : 0;

  return (
    <div style={{ position: "relative" }}>
      <svg
        className="chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={series.map((item) => `${item.label}: ${item.values.reduce((a, b) => a + b, 0)}`).join(", ")}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          const x = ((event.clientX - box.left) / box.width) * width - pad.left;
          const index = step ? Math.round(x / step) : 0;
          setHover(Math.max(0, Math.min(labels.length - 1, index)));
        }}
      >
        <defs>
          {series.map((item) => (
            <linearGradient key={item.key} id={`trend-${id}-${item.key}`} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0" stopColor={item.color} stopOpacity=".3" />
              <stop offset="1" stopColor={item.color} stopOpacity="0" />
            </linearGradient>
          ))}
        </defs>
        <g transform={`translate(${pad.left},${pad.top})`}>
          {gridValues.map((value) => {
            const y = innerHeight - (value / max) * innerHeight;
            return (
              <g key={value}>
                <line x1={0} x2={innerWidth} y1={y} y2={y} className="grid-line" />
                <text x={-8} y={y + 3} textAnchor="end" className="axis-label">
                  {Math.round(value)}
                </text>
              </g>
            );
          })}
          {ticks.map((index) => (
            <text key={index} x={index * step} y={innerHeight + 16} textAnchor="middle" className="axis-label">
              {labels[index]}
            </text>
          ))}
          {series.map((item) => {
            const points = scalePoints(item.values, innerWidth, innerHeight, max);
            return (
              <g key={item.key}>
                {item.fill && <path d={areaPath(points, innerHeight)} fill={`url(#trend-${id}-${item.key})`} />}
                <path
                  d={linePath(points)}
                  fill="none"
                  stroke={item.color}
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  className="draw"
                  pathLength={2000}
                />
                {hover !== null && points[hover] && (
                  <circle cx={points[hover].x} cy={points[hover].y} r={4} fill="var(--surface)" stroke={item.color} strokeWidth="2" />
                )}
              </g>
            );
          })}
          {hover !== null && (
            <line x1={hover * step} x2={hover * step} y1={0} y2={innerHeight} stroke="var(--border-strong)" strokeDasharray="2 3" />
          )}
        </g>
      </svg>
      {hover !== null && (
        <div
          className="card small"
          style={{
            position: "absolute",
            top: 4,
            left: `min(calc(${((pad.left + hover * step) / width) * 100}% + 10px), calc(100% - 150px))`,
            padding: "6px 10px",
            pointerEvents: "none",
            boxShadow: "var(--shadow-md)",
          }}
        >
          <strong>{labels[hover]}</strong>
          {series.map((item) => (
            <div key={item.key} className="row nowrap" style={{ gap: 6 }}>
              <span className="dot" style={{ background: item.color }} />
              {item.label}: <strong>{item.values[hover]}</strong>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function Donut({
  values,
  colors,
  size = 150,
  thickness = 18,
  center,
  caption,
}: {
  values: { key: string; value: number }[];
  colors: Record<string, string>;
  size?: number;
  thickness?: number;
  center: string;
  caption: string;
}) {
  const segments = donutSegments(values, size, thickness);
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${center} ${caption}`}>
      <circle cx={size / 2} cy={size / 2} r={size / 2 - thickness / 2} fill="none" stroke="var(--surface-3)" strokeWidth={thickness} />
      {segments.map((segment) => (
        <path key={segment.key} d={segment.path} fill={colors[segment.key] ?? "var(--text-3)"} className="fade-in">
          <title>{`${segment.key.replaceAll("_", " ")}: ${segment.value}`}</title>
        </path>
      ))}
      <text x="50%" y="48%" textAnchor="middle" className="donut-center">
        {center}
      </text>
      <text x="50%" y="62%" textAnchor="middle" className="donut-sub">
        {caption}
      </text>
    </svg>
  );
}

export function Meter({ value, color = "var(--accent)" }: { value: number; color?: string }) {
  const width = `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
  return (
    <div className="meter" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value * 100)}>
      <span style={{ width, background: color }} />
    </div>
  );
}
