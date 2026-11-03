/** Pure helpers for the SVG charts (no DOM): scales, line/area paths, donut arcs, deltas. */

export interface Point {
  x: number;
  y: number;
}

/** A round upper bound for an axis (0 → 1, 7 → 8, 43 → 50, 180 → 200). */
export function niceMax(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 2.5, 5, 10]) {
    if (value <= step * magnitude) return step * magnitude;
  }
  return 10 * magnitude;
}

/** Points of a series scaled into a `width` × `height` box (y grows downwards). */
export function scalePoints(values: number[], width: number, height: number, max?: number): Point[] {
  if (values.length === 0) return [];
  const top = max ?? niceMax(Math.max(...values));
  const step = values.length > 1 ? width / (values.length - 1) : 0;
  return values.map((value, index) => ({
    x: values.length > 1 ? index * step : width / 2,
    y: height - (Math.max(0, value) / top) * height,
  }));
}

const round = (value: number) => Math.round(value * 100) / 100;

/** A smooth SVG path through the points (monotone cubic, so it never overshoots below 0). */
export function linePath(points: Point[]): string {
  const [first] = points;
  if (!first) return "";
  // Pairs of consecutive points; each becomes one cubic segment.
  const pairs = points.slice(1).map((end, index) => [points[index] as Point, end] as const);
  const slopes = pairs.map(([start, end]) => {
    const dx = end.x - start.x;
    return dx === 0 ? 0 : (end.y - start.y) / dx;
  });
  const tangents = points.map((_, index) => {
    const before = slopes[index - 1];
    const after = slopes[index];
    if (before === undefined) return after ?? 0;
    if (after === undefined) return before;
    return before * after <= 0 ? 0 : (before + after) / 2;
  });
  let path = `M${round(first.x)},${round(first.y)}`;
  pairs.forEach(([start, end], index) => {
    const third = (end.x - start.x) / 3;
    const startTangent = tangents[index] ?? 0;
    const endTangent = tangents[index + 1] ?? 0;
    path +=
      ` C${round(start.x + third)},${round(start.y + startTangent * third)}` +
      ` ${round(end.x - third)},${round(end.y - endTangent * third)}` +
      ` ${round(end.x)},${round(end.y)}`;
  });
  return path;
}

/** The line path closed down to the baseline, for a filled area. */
export function areaPath(points: Point[], height: number): string {
  const first = points[0];
  const last = points[points.length - 1];
  if (!first || !last) return "";
  return `${linePath(points)} L${round(last.x)},${height} L${round(first.x)},${height} Z`;
}

export interface DonutSegment {
  key: string;
  value: number;
  share: number;
  path: string;
}

function polar(cx: number, cy: number, radius: number, angle: number): Point {
  // Angles start at 12 o'clock and run clockwise.
  const radians = (angle - 90) * (Math.PI / 180);
  return { x: cx + radius * Math.cos(radians), y: cy + radius * Math.sin(radians) };
}

/** Ring segments (SVG paths) for a donut chart; zero values are dropped. */
export function donutSegments(
  values: { key: string; value: number }[],
  size: number,
  thickness: number,
  gapDegrees = 1.5,
): DonutSegment[] {
  const positive = values.filter((item) => item.value > 0);
  const total = positive.reduce((sum, item) => sum + item.value, 0);
  if (total === 0) return [];
  const center = size / 2;
  const outer = size / 2;
  const inner = outer - thickness;
  let angle = 0;
  return positive.map((item) => {
    const sweep = (item.value / total) * 360;
    const gap = positive.length > 1 ? gapDegrees : 0;
    // A full circle cannot be drawn as one arc: stop just short of 360°.
    const start = angle + gap / 2;
    const end = angle + Math.min(sweep, 359.99) - gap / 2;
    angle += sweep;
    const large = end - start > 180 ? 1 : 0;
    const a = polar(center, center, outer, start);
    const b = polar(center, center, outer, end);
    const c = polar(center, center, inner, end);
    const d = polar(center, center, inner, start);
    const path =
      `M${round(a.x)},${round(a.y)} A${outer},${outer} 0 ${large} 1 ${round(b.x)},${round(b.y)}` +
      ` L${round(c.x)},${round(c.y)} A${inner},${inner} 0 ${large} 0 ${round(d.x)},${round(d.y)} Z`;
    return { key: item.key, value: item.value, share: item.value / total, path };
  });
}

export interface Change {
  direction: "up" | "down" | "flat";
  /** Relative change, e.g. 0.25 for +25 %; null when the previous period was 0. */
  ratio: number | null;
  label: string;
}

/** Change between two periods, worded for a delta chip. */
export function periodChange(current: number, previous: number): Change {
  if (current === previous) return { direction: "flat", ratio: 0, label: "no change" };
  const direction = current > previous ? "up" : "down";
  if (previous === 0) return { direction, ratio: null, label: `+${current} new` };
  const ratio = (current - previous) / previous;
  const sign = ratio > 0 ? "+" : "−";
  return { direction, ratio, label: `${sign}${Math.round(Math.abs(ratio) * 100)}%` };
}

/** Short day labels for the x axis: at most `count` evenly spread (always the first and last). */
export function axisTicks<T>(items: T[], count: number): number[] {
  if (items.length === 0) return [];
  if (items.length <= count) return items.map((_, index) => index);
  const ticks = new Set<number>();
  for (let index = 0; index < count; index += 1) {
    ticks.add(Math.round((index * (items.length - 1)) / (count - 1)));
  }
  return [...ticks];
}

/** Heat colour for a reply heatmap cell (0 → empty, max → full accent). */
export function heatOpacity(value: number, max: number): number {
  if (value <= 0 || max <= 0) return 0;
  return Math.round((0.18 + 0.82 * (value / max)) * 100) / 100;
}

/** Share of the previous stage kept by each funnel stage ("—" for the first). */
export function stageConversion(values: number[]): (number | null)[] {
  return values.map((value, index) => {
    if (index === 0) return null;
    const previous = values[index - 1] ?? 0;
    return previous > 0 ? value / previous : 0;
  });
}
