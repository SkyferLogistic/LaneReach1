/** Saved list filters per viewer (browser storage; a convenience, never required). */

export interface SavedView {
  name: string;
  search: string;
  tag: string;
  stage: string;
}

const PREFIX = "lanereach.views.";

function isView(value: unknown): value is SavedView {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return ["name", "search", "tag", "stage"].every((key) => typeof item[key] === "string");
}

/** The saved views of a list (empty when storage is unavailable or damaged). */
export function loadSavedViews(list: string, storage: Pick<Storage, "getItem"> | undefined = globalThis.localStorage): SavedView[] {
  try {
    const parsed: unknown = JSON.parse(storage?.getItem(PREFIX + list) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter(isView).slice(0, 20) : [];
  } catch {
    return [];
  }
}

/** Store the views of a list; silently ignored when storage is blocked. */
export function saveViews(list: string, views: SavedView[], storage: Pick<Storage, "setItem"> | undefined = globalThis.localStorage): void {
  try {
    storage?.setItem(PREFIX + list, JSON.stringify(views.slice(0, 20)));
  } catch {
    // Private windows can refuse storage: views are a convenience.
  }
}
