/** Pricing helpers: lane links (same rules as the server), freight maths, validity and comparisons. */

export const LINK_PLACEHOLDERS = ["origin", "destination", "mode", "equipment", "date"] as const;
export type LinkPlaceholder = (typeof LINK_PLACEHOLDERS)[number];

export interface LaneValues {
  origin?: string | null;
  destination?: string | null;
  mode?: string | null;
  equipment?: string | null;
  date?: string | null;
}

const PLACEHOLDER = /\{([a-z_]+)\}/g;

/** Problems with a lane link template, or null when it is usable (https, no login, known placeholders). */
export function linkTemplateProblem(template: string): string | null {
  const value = template.trim();
  if (!value) return null;
  const unknown = [...value.matchAll(PLACEHOLDER)].map((match) => match[1]).filter((name) => !LINK_PLACEHOLDERS.includes(name as LinkPlaceholder));
  if (unknown.length) return `Unknown placeholder ${unknown.map((name) => `{${name}}`).join(", ")}`;
  let url: URL;
  try {
    url = new URL(value.replace(PLACEHOLDER, "x"));
  } catch {
    return "Not a valid link";
  }
  if (url.protocol !== "https:") return "Links must start with https://";
  if (url.username || url.password) return "Links cannot contain a user name or password";
  return null;
}

/** A template with the lane filled in, every value URL-encoded (like the server). */
export function fillLaneTemplate(template: string, lane: LaneValues): string {
  return template.replace(PLACEHOLDER, (whole, name: string) => {
    if (!LINK_PLACEHOLDERS.includes(name as LinkPlaceholder)) return whole;
    return encodeURIComponent(lane[name as LinkPlaceholder] ?? "");
  });
}

/** Air chargeable weight: the larger of actual kg and volume × 167 (IATA 1:6000). */
export function chargeableWeight(weightKg: number | null | undefined, volumeCbm: number | null | undefined): number {
  const actual = weightKg ?? 0;
  const volumetric = (volumeCbm ?? 0) * 167;
  return Math.round(Math.max(actual, volumetric) * 1000) / 1000;
}

export type Validity = "expired" | "soon" | "valid" | "open";

/** How a rate's validity looks today: expired, ending within `soonDays`, valid, or open-ended. */
export function validityOf(validTo: string | null | undefined, today: Date = new Date(), soonDays = 7): Validity {
  if (!validTo) return "open";
  const [year, month, day] = validTo.split("-").map(Number);
  if (!year || !month || !day) return "open";
  const end = Date.UTC(year, month - 1, day);
  const now = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  const days = Math.round((end - now) / 86_400_000);
  if (days < 0) return "expired";
  return days <= soonDays ? "soon" : "valid";
}

export interface PricePoint {
  amount: number;
  currency: string;
}

/** The cheapest price per currency (prices in different currencies are never compared). */
export function cheapestByCurrency<T extends PricePoint>(items: T[]): Map<string, T> {
  const best = new Map<string, T>();
  for (const item of items) {
    const current = best.get(item.currency);
    if (!current || item.amount < current.amount) best.set(item.currency, item);
  }
  return best;
}

/** "−4.2 %" style difference of a market price against the sheet price (same currency only). */
export function priceDifference(market: number, sheet: number): string {
  if (!sheet) return "";
  const change = ((market - sheet) / sheet) * 100;
  const rounded = Math.round(change * 10) / 10;
  if (rounded === 0) return "same as sheet";
  return `${rounded > 0 ? "+" : "−"}${Math.abs(rounded).toFixed(1)} % vs sheet`;
}

/** Fields a sheet column can be mapped to, with labels for the mapping editor. */
export const FIELD_LABELS: Record<string, string> = {
  origin: "Origin (POL / from)",
  destination: "Destination (POD / to)",
  mode: "Mode",
  equipment: "Equipment / container",
  carrier: "Carrier / line / airline",
  amount: "Rate / price",
  currency: "Currency",
  valid_from: "Valid from",
  valid_to: "Valid to",
  transit_days: "Transit time",
  unit: "Unit / per",
  charge: "Charge / description",
  minimum: "Minimum charge",
  notes: "Notes / remarks",
};
