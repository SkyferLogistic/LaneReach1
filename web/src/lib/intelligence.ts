/** Lead score, digest and follow-up helpers for the console (pure, tested). */

export type Band = "hot" | "warm" | "cold";

/** Same bands as the server: hot ≥ 70, warm ≥ 45. */
export function scoreBand(score: number | null | undefined): Band | null {
  if (score === null || score === undefined) return null;
  return score >= 70 ? "hot" : score >= 45 ? "warm" : "cold";
}

export const BAND_LABELS: Record<Band, string> = { hot: "Hot", warm: "Warm", cold: "Cold" };

/** "+8", "−3" or "note" for a reason's points. */
export function formatPoints(points: number): string {
  if (points === 0) return "note";
  return points > 0 ? `+${points}` : `−${Math.abs(points)}`;
}

/** Deals by score, unscored last, then by title. */
export function sortByScore<T extends { score?: number | null; title: string }>(items: T[]): T[] {
  return [...items].sort((a, b) => (b.score ?? -1) - (a.score ?? -1) || a.title.localeCompare(b.title));
}

export const FOLLOW_UP_ACTIONS: Record<string, { label: string; icon: "mail" | "user" | "clock" | "x" }> = {
  email: { label: "Send a follow-up email", icon: "mail" },
  call: { label: "Call them", icon: "user" },
  wait: { label: "Wait, they asked for later", icon: "clock" },
  close_lost: { label: "Close as lost", icon: "x" },
};

export const HIGHLIGHT_KINDS: Record<string, { label: string; icon: "route" | "trending" | "clock" | "reply" | "quote" }> = {
  lanes: { label: "Lanes", icon: "route" },
  win_loss: { label: "Win / loss", icon: "trending" },
  speed: { label: "Speed", icon: "clock" },
  replies: { label: "Replies", icon: "reply" },
  quotes: { label: "Quotes", icon: "quote" },
};

/** "28 Sep – 4 Oct 2026" for a digest week. */
export function weekLabel(start: string, end: string): string {
  const parse = (value: string) => {
    const [year, month, day] = value.split("-").map(Number);
    return new Date(Date.UTC(year ?? 1970, (month ?? 1) - 1, day ?? 1));
  };
  const from = parse(start);
  const to = parse(end);
  const short = (date: Date) => date.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  return `${short(from)} – ${short(to)} ${to.getUTCFullYear()}`;
}

/** `42%` of a total, or an en dash when there is nothing to measure yet. */
export function percentText(part: number, total: number): string {
  return total > 0 ? `${Math.round((part * 100) / total)}%` : "–";
}

/** Width shares (summing to 100) for a split bar; empty when there is nothing to split. */
export function splitShares(values: number[]): number[] {
  const total = values.reduce((sum, value) => sum + value, 0);
  if (total <= 0) return [];
  return values.map((value) => (value * 100) / total);
}
