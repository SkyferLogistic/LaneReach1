/** Mailbox health as a small coloured pill (score and status). */
import type { Schemas } from "../api/client";

type Health = Schemas["MailboxHealthResponse"];

const LABELS: Record<string, string> = {
  healthy: "Healthy",
  watch: "Watch",
  at_risk: "At risk",
  paused: "Off",
};

export function HealthPill({ health }: { health: Health | undefined }) {
  if (!health) return null;
  return (
    <span className={`health-pill ${health.status}`} title={health.reasons.join(" · ") || "No issues in the last 30 days"}>
      {LABELS[health.status] ?? health.status} · {health.score}
    </span>
  );
}
