/** Freight-domain display helpers: flags, service lines, agent initials, script cell states. */

export type ServiceLine = "air" | "ocean" | "road" | "rail" | "courier" | "customs" | "warehousing" | "project" | "any";

/** The lines a forwarder can sell, in menu order (`any` = agents/scripts for every line). */
export const SERVICE_LINES: Exclude<ServiceLine, "any">[] = ["air", "ocean", "road", "rail", "courier", "customs", "warehousing", "project"];

export const LINE_ICONS: Record<ServiceLine, "plane" | "ship" | "truck" | "train" | "parcel" | "stamp" | "warehouse" | "crane" | "route"> = {
  air: "plane",
  ocean: "ship",
  road: "truck",
  rail: "train",
  courier: "parcel",
  customs: "stamp",
  warehousing: "warehouse",
  project: "crane",
  any: "route",
};

/** Narrow a string from the API to a known line (`any` for unknown values). */
export function asServiceLine(value: string | null | undefined): ServiceLine {
  return (SERVICE_LINES as string[]).includes(value ?? "") ? (value as ServiceLine) : "any";
}

/** Emoji flag of an ISO 3166 alpha-2 code ("TR" → 🇹🇷); empty for anything else. */
export function countryFlag(code: string | null | undefined): string {
  if (!code || !/^[A-Za-z]{2}$/.test(code)) return "";
  const base = 0x1f1e6 - "A".charCodeAt(0);
  return String.fromCodePoint(...[...code.toUpperCase()].map((letter) => base + letter.charCodeAt(0)));
}

/** "Molly Joseph" → "MJ", "aisha" → "AI". */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const first = words[0];
  const last = words[words.length - 1];
  if (!first || !last) return "?";
  if (words.length === 1) return first.slice(0, 2).toUpperCase();
  return (first.charAt(0) + last.charAt(0)).toUpperCase();
}

export const SERVICE_LINE_LABELS: Record<ServiceLine, string> = {
  air: "Air freight",
  ocean: "Ocean freight",
  road: "Road freight",
  rail: "Rail freight",
  courier: "Courier & express",
  customs: "Customs brokerage",
  warehousing: "Warehousing",
  project: "Project cargo",
  any: "Any line",
};

export type CellState = "active" | "ai_draft" | "error" | "inactive" | "missing" | "held";

/** What a script × language cell shows. `strict` = the language's countries never fall back. */
export function scriptCellState(
  version: { status: string } | undefined,
  strict: boolean,
): CellState {
  if (!version) return strict ? "held" : "missing";
  if (version.status === "active" || version.status === "ai_draft" || version.status === "error") {
    return version.status;
  }
  return "inactive";
}

export const CELL_LABELS: Record<CellState, string> = {
  active: "Live — used for sending",
  ai_draft: "AI draft — waiting for approval",
  error: "Import failed — fix the Word file",
  inactive: "Removed from the library",
  missing: "No script — countries fall back to another language",
  held: "No script — these countries are held until one exists",
};

/** Share of languages that have a live script, per script. */
export function scriptCoverage(
  versions: Record<string, { status: string }>,
  languages: string[],
): number {
  if (languages.length === 0) return 0;
  const live = languages.filter((code) => versions[code]?.status === "active").length;
  return live / languages.length;
}
