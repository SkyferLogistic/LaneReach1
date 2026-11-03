/** Pure helpers of the unified inbox: snooze presets, keyboard moves, previews. */

export type Folder = "inbox" | "unread" | "awaiting" | "drafts" | "snoozed" | "done" | "sent" | "all";

export const FOLDERS: { id: Folder; label: string; hint: string }[] = [
  { id: "inbox", label: "Inbox", hint: "Contacts who replied" },
  { id: "unread", label: "Unread", hint: "New replies nobody has read" },
  { id: "awaiting", label: "Awaiting reply", hint: "The contact wrote last" },
  { id: "drafts", label: "AI drafts", hint: "A drafted reply waits in the review queue" },
  { id: "snoozed", label: "Snoozed", hint: "Hidden until later" },
  { id: "done", label: "Done", hint: "Back in the inbox when the contact writes again" },
  { id: "sent", label: "Sent", hint: "No reply yet" },
  { id: "all", label: "All", hint: "Every conversation" },
];

export function isFolder(value: string | null): value is Folder {
  return FOLDERS.some((folder) => folder.id === value);
}

export interface SnoozeOption {
  id: string;
  label: string;
  until: Date;
}

function at(day: Date, hours: number): Date {
  const result = new Date(day);
  result.setHours(hours, 0, 0, 0);
  return result;
}

/** Later today (+3 h), tomorrow 09:00, next Monday 09:00 and in a week, in the viewer's time. */
export function snoozeOptions(now: Date): SnoozeOption[] {
  const later = new Date(now.getTime() + 3 * 3_600_000);
  const tomorrow = at(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1), 9);
  const daysToMonday = ((8 - now.getDay()) % 7) || 7;
  const monday = at(new Date(now.getFullYear(), now.getMonth(), now.getDate() + daysToMonday), 9);
  const week = at(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 7), 9);
  return [
    { id: "later", label: "Later today", until: later },
    { id: "tomorrow", label: "Tomorrow morning", until: tomorrow },
    { id: "monday", label: "Next Monday", until: monday },
    { id: "week", label: "In a week", until: week },
  ];
}

/** The id `delta` places away from `current` (first item when nothing is selected). */
export function neighbour(ids: string[], current: string | null, delta: number): string | null {
  if (ids.length === 0) return null;
  const index = current === null ? -1 : ids.indexOf(current);
  if (index === -1) return ids[0] ?? null;
  const next = Math.min(ids.length - 1, Math.max(0, index + delta));
  return ids[next] ?? null;
}

/**
 * The text of an email without the quoted history below it, so a thread reads like a chat.
 * Lines starting with ">" and everything after "On … wrote:" / "From:" headers are dropped.
 */
export function withoutQuotedText(body: string): { text: string; quoted: boolean } {
  const lines = body.replace(/\r\n/g, "\n").split("\n");
  const kept: string[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (/^On .+wrote:$/i.test(trimmed) || /^-{2,}\s*Original Message\s*-{2,}$/i.test(trimmed) || /^From:\s.+/i.test(trimmed) && kept.length > 0) {
      return { text: kept.join("\n").trimEnd(), quoted: true };
    }
    if (trimmed.startsWith(">")) continue;
    kept.push(line);
  }
  const text = kept.join("\n").trimEnd();
  return { text, quoted: text.length < body.trimEnd().length };
}

