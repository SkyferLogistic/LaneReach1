/** Pure helpers of campaigns: schedule labels and step counts for the canvas. */

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export interface ScheduleLike {
  start_date?: string | null;
  end_date?: string | null;
  weekdays?: number[];
}

function shortDate(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return value;
  return new Date(year, month - 1, day).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** "Mon–Fri · from 1 Oct · until 30 Nov" */
export function scheduleLabel(schedule: ScheduleLike): string {
  const days = [...(schedule.weekdays ?? [1, 2, 3, 4, 5])].sort((a, b) => a - b);
  const contiguous = days.length > 2 && days.every((day, index) => index === 0 || day === (days[index - 1] ?? 0) + 1);
  const first = days[0];
  const last = days[days.length - 1];
  const dayText =
    days.length === 7
      ? "Every day"
      : contiguous && first !== undefined && last !== undefined
        ? `${DAY_NAMES[first - 1]}–${DAY_NAMES[last - 1]}`
        : days.map((day) => DAY_NAMES[day - 1]).join(", ");
  const parts = [dayText];
  if (schedule.start_date) parts.push(`from ${shortDate(schedule.start_date)}`);
  if (schedule.end_date) parts.push(`until ${shortDate(schedule.end_date)}`);
  return parts.join(" · ");
}

/** Enrollments per node, for the count bubbles on the canvas. */
export function stepCountMap(steps: { node_id: string; enrollments: number }[] | undefined): Map<string, number> {
  return new Map((steps ?? []).map((step) => [step.node_id, step.enrollments]));
}
