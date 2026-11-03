/** AI usage screen helpers: task names people understand, money and the budget meter. */

const TASK_LABELS: Record<string, string> = {
  classify_reply: "Sorting replies",
  extract_shipment_details: "Reading shipment details",
  assess_lead: "Reading buying intent",
  summarize_thread: "Conversation summaries",
  match_lane: "Matching rate lanes",
  draft_reply: "Reply drafts",
  suggest_follow_up: "Follow-up suggestions",
  write_digest: "Weekly digest",
  draft_quote_note: "Quote cover notes",
  draft_workflow: "Workflow drafts",
  answer_help_question: "Assistant answers",
  localize_template: "Translations",
};

export function taskLabel(task: string): string {
  return TASK_LABELS[task] ?? task.replace(/_/g, " ").replace(/^./, (first) => first.toUpperCase());
}

/** `$12.40`, `<$0.01` for a cost too small to show, `$0.00` for nothing. */
export function formatUsd(amount: number): string {
  if (amount > 0 && amount < 0.01) return "<$0.01";
  return `$${amount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export type MeterTone = "green" | "amber" | "red" | "gray";

/** Green under 80 % of the budget, amber from 80 %, red once AI is paused. */
export function budgetTone(percent: number | null | undefined): MeterTone {
  if (percent === null || percent === undefined) return "gray";
  if (percent >= 100) return "red";
  if (percent >= 80) return "amber";
  return "green";
}

/** A budget typed by a person: empty means no cap; anything else a non-negative amount. */
export function parseBudget(text: string): number | null | "invalid" {
  const cleaned = text.replace(/[$,\s]/g, "");
  if (!cleaned) return null;
  const value = Number(cleaned);
  return Number.isFinite(value) && value >= 0 && value <= 1_000_000 ? Math.round(value * 100) / 100 : "invalid";
}
