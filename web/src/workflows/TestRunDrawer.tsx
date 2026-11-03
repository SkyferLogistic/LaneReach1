/**
 * Test-run: simulate a contact through the workflow with the real engine, a simulated clock,
 * the mock LLM and the dry-run provider. Nothing is stored; the response shows each step
 * (with guardrail decisions), the emails that would be sent and the notifications.
 */
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { ContactPicker } from "../components/ContactPicker";
import { Badge, Button, Drawer, Empty, ErrorAlert, StatusBadge } from "../components/ui";
import { formatDateTime, humanize } from "../lib/format";

const BUCKETS = [
  "interested",
  "meeting_request",
  "rate_query",
  "service_query",
  "partnership_query",
  "referral",
  "wrong_office",
  "later",
  "not_interested",
  "complaint",
  "existing_partner",
  "unsubscribe",
  "out_of_office",
  "auto_reply",
  "unclear",
];

export function TestRunDrawer({
  workflowId,
  version,
  onClose,
}: {
  workflowId: string;
  version: number;
  onClose: () => void;
}) {
  const [contactId, setContactId] = useState<string | null>(null);
  const [bucket, setBucket] = useState("");
  const [confidence, setConfidence] = useState(0.95);
  const [startAt, setStartAt] = useState("");
  const run = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/automation/workflows/{workflow_id}/test-run", {
          params: { path: { workflow_id: workflowId } },
          body: {
            contact_id: contactId as string,
            version,
            start_at: startAt ? new Date(startAt).toISOString() : undefined,
            reply: bucket ? { bucket: bucket as Schemas["ReplyBucket"], confidence } : undefined,
          },
        }),
      ),
  });

  return (
    <Drawer
      title={`Test-run · version ${version}`}
      onClose={onClose}
      footer={
        <Button variant="primary" busy={run.isPending} disabled={!contactId} onClick={() => run.mutate()}>
          Run simulation
        </Button>
      }
    >
      <div className="stack">
        <p className="muted small" style={{ margin: 0 }}>
          The real engine runs on a simulated clock (waits pass instantly), with the mock AI and no
          email sent. Everything is rolled back afterwards.
        </p>
        <ContactPicker value={contactId} onChange={setContactId} />
        <div className="form-grid">
          <label className="field">
            <span>Simulated reply (optional)</span>
            <select className="select" value={bucket} onChange={(event) => setBucket(event.target.value)}>
              <option value="">No reply</option>
              {BUCKETS.map((option) => (
                <option key={option} value={option}>
                  {humanize(option)}
                </option>
              ))}
            </select>
            <span className="field-help">Feeds reply.* conditions and bucket branches.</span>
          </label>
          <label className="field">
            <span>Confidence</span>
            <input
              className="input"
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={confidence}
              disabled={!bucket}
              onChange={(event) => setConfidence(Number(event.target.value))}
            />
          </label>
          <label className="field">
            <span>Start at (optional)</span>
            <input
              className="input"
              type="datetime-local"
              value={startAt}
              onChange={(event) => setStartAt(event.target.value)}
            />
          </label>
        </div>
        <ErrorAlert error={run.error} />
        {run.data && <TestRunResult result={run.data} />}
      </div>
    </Drawer>
  );
}

function outcomeTone(result: string): string {
  if (result === "continue" || result === "stop") return "green";
  if (result === "wait") return "blue";
  if (result === "defer") return "amber";
  return "red";
}

function TestRunResult({ result }: { result: Schemas["TestRunResponse"] }) {
  return (
    <div className="stack">
      <hr />
      <div className="row">
        <h3>Result</h3>
        <StatusBadge value={result.status} />
        {result.truncated && <Badge tone="amber">stopped at the simulation limit</Badge>}
        {result.stop_reason && <span className="small muted">{result.stop_reason}</span>}
      </div>
      <div className="timeline">
        {result.steps.map((step) => (
          <div key={step.step_index} className="timeline-item">
            <span className={`timeline-dot ${outcomeTone(step.result)}`} />
            <div className="stack tight">
              <div className="row">
                <strong className="mono">{step.node_id}</strong>
                <span className="small muted">{humanize(step.node_type)}</span>
                <StatusBadge value={step.result} />
                {step.handle && step.handle !== "next" && <Badge tone="violet">→ {step.handle}</Badge>}
                <span className="small muted">{formatDateTime(step.at)}</span>
              </div>
              {step.detail && <div className="small">{step.detail}</div>}
              {step.wait_until && <div className="small muted">until {formatDateTime(step.wait_until)}</div>}
              {step.error && <div className="small" style={{ color: "var(--danger)" }}>{step.error}</div>}
              {step.guardrail_decisions.length > 0 && <GuardrailList decisions={step.guardrail_decisions} />}
            </div>
          </div>
        ))}
      </div>
      <h3>Emails ({result.emails.length})</h3>
      {result.emails.length === 0 && <Empty title="No email would be sent" />}
      {result.emails.map((email, index) => (
        <div key={index} className="email-preview stack tight">
          <div className="row small muted">
            <Badge>{email.kind}</Badge> {formatDateTime(email.at)} · {email.from_name} &lt;{email.from_email}&gt; →{" "}
            {email.to_email}
          </div>
          <strong>{email.subject}</strong>
          <pre>{email.body}</pre>
        </div>
      ))}
      {result.notifications.length > 0 && (
        <>
          <h3>Notifications ({result.notifications.length})</h3>
          {result.notifications.map((notification, index) => (
            <div key={index} className="email-preview small">
              <strong>{notification.subject}</strong> → {notification.recipient} ({notification.channel})
              <pre>{notification.body}</pre>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

export function GuardrailList({ decisions }: { decisions: Record<string, unknown>[] }) {
  return (
    <div className="chips">
      {decisions.map((decision, index) => {
        const outcome = String(decision.outcome ?? "");
        const tone = outcome === "allow" ? "green" : outcome === "defer" ? "amber" : "red";
        return (
          <span key={index} title={String(decision.reason ?? "")}>
            <Badge tone={tone}>
              {humanize(String(decision.guardrail ?? ""))}: {outcome}
            </Badge>
          </span>
        );
      })}
    </div>
  );
}
