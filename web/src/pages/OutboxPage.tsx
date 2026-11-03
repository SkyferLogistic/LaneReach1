/** Outbox: the sent log. Every email with its delivery status, search, filters and conversation. */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { LoadMore, usePaged } from "../api/paging";
import { keys, useSenders } from "../api/queries";
import { useDebounced } from "../components/ContactPicker";
import { EmailFrame, textToHtml } from "../components/EmailFrame";
import { Icon } from "../components/icons";
import { Page } from "../components/Layout";
import { SkeletonRows } from "../components/motion";
import { Badge, Card, Drawer, Empty, ErrorAlert, Json, PageHeader, Tabs } from "../components/ui";
import { formatDateTime, formatRelative } from "../lib/format";

type Email = Schemas["OutboxMessageResponse"];
type StatusTab = "all" | "sent" | "queued" | "dry_run" | "failed";

const STATUS_LABELS: Record<string, string> = { sent: "Sent", queued: "Queued", dry_run: "Dry run", failed: "Failed" };
const STATUS_TONES: Record<string, "green" | "gray" | "red" | "blue"> = { sent: "green", queued: "blue", dry_run: "gray", failed: "red" };
const KINDS = ["cold", "follow_up", "reply"];

export function StatusPill({ status }: { status: string }) {
  return <Badge tone={STATUS_TONES[status] ?? "gray"}>{STATUS_LABELS[status] ?? status}</Badge>;
}

export function OutboxPage() {
  const [open, setOpen] = useState<Email | null>(null);
  const [tab, setTab] = useState<StatusTab>("all");
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("");
  const [sender, setSender] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const q = useDebounced(search.trim());
  const senders = useSenders();
  const filters = {
    q: q || undefined,
    kind: kind || undefined,
    sender_id: sender || undefined,
    sent_from: from || undefined,
    sent_to: to || undefined,
  };
  const counts = useQuery({
    queryKey: [...keys.outbox, "counts", filters],
    queryFn: () => unwrap(api.GET("/api/v1/outbox/counts", { params: { query: filters } })),
  });
  const page = usePaged([...keys.outbox, "list", tab, filters], (cursor) =>
    unwrap(
      api.GET("/api/v1/outbox", {
        params: { query: { ...filters, status: tab === "all" ? undefined : tab, cursor, limit: 50 } },
      }),
    ),
  );
  const filtered = Boolean(q || kind || sender || from || to);
  const dryRun = (counts.data?.dry_run ?? 0) > 0 && (counts.data?.sent ?? 0) === 0;
  return (
    <Page>
      <PageHeader
        eyebrow={<><Icon name="outbox" /> Delivery</>}
        title="Outbox"
        description="The sent log: every email LaneReach sent or recorded, with its delivery status and the whole conversation."
      />
      {dryRun && (
        <div className="alert info small" style={{ marginBottom: 12 }}>
          <strong>Dry-run mode:</strong> these emails were recorded, not delivered. Connect agent mailboxes to send for real.
        </div>
      )}
      <Tabs<StatusTab>
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "all", label: `All (${counts.data?.all ?? "…"})` },
          { id: "sent", label: `Sent (${counts.data?.sent ?? "…"})` },
          { id: "queued", label: `Queued (${counts.data?.queued ?? "…"})` },
          { id: "dry_run", label: `Dry run (${counts.data?.dry_run ?? "…"})` },
          {
            id: "failed",
            label: <span style={{ color: counts.data?.failed ? "var(--danger)" : undefined }}>Failed ({counts.data?.failed ?? "…"})</span>,
          },
        ]}
      />
      <div className="filter-bar">
        <label className="search-field">
          <Icon name="search" />
          <input className="input" value={search} placeholder="Search subject or address…" onChange={(event) => setSearch(event.target.value)} aria-label="Search the outbox" />
        </label>
        <select className="select" value={kind} onChange={(event) => setKind(event.target.value)} aria-label="Kind">
          <option value="">Every kind</option>
          {KINDS.map((item) => (
            <option key={item} value={item}>
              {item.replace(/_/g, " ")}
            </option>
          ))}
        </select>
        <select className="select" value={sender} onChange={(event) => setSender(event.target.value)} aria-label="Agent">
          <option value="">Every agent</option>
          {(senders.data ?? []).map((agent) => (
            <option key={agent.id} value={agent.id}>
              {agent.display_name}
            </option>
          ))}
        </select>
        <input className="input" type="date" value={from} onChange={(event) => setFrom(event.target.value)} aria-label="From date" />
        <input className="input" type="date" value={to} onChange={(event) => setTo(event.target.value)} aria-label="To date" />
        {filtered && (
          <button
            type="button"
            className="btn ghost sm"
            onClick={() => {
              setSearch("");
              setKind("");
              setSender("");
              setFrom("");
              setTo("");
            }}
          >
            Clear filters
          </button>
        )}
      </div>
      <Card flush>
        {page.isLoading && <SkeletonRows rows={5} />}
        <ErrorAlert error={page.error ?? counts.error} />
        {!page.isLoading && page.items.length === 0 && (
          <Empty art="ship" title={filtered || tab !== "all" ? "No email matches" : "No emails yet"}>
            {filtered || tab !== "all" ? "Try another search or filter." : "Emails appear here as workflows, replies and quotes send them."}
          </Empty>
        )}
        {page.items.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Subject</th>
                  <th>From</th>
                  <th>To</th>
                  <th>Kind</th>
                  <th>Status</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((email) => (
                  <tr key={email.id} className="clickable" onClick={() => setOpen(email)}>
                    <td className="truncate" style={{ maxWidth: 340 }}>
                      <strong>{email.subject}</strong> {email.html_body && <Badge tone="violet">HTML</Badge>}
                    </td>
                    <td className="small">
                      {email.from_name}
                      <div className="muted">{email.from_email}</div>
                    </td>
                    <td className="small">
                      {email.to_name ?? ""}
                      <div className="muted">{email.to_email}</div>
                    </td>
                    <td>
                      <Badge tone={email.kind === "cold" ? "blue" : email.kind.includes("reply") ? "green" : "cyan"}>{email.kind.replace(/_/g, " ")}</Badge>
                    </td>
                    <td>
                      <StatusPill status={email.status} />
                    </td>
                    <td className="small muted" title={formatDateTime(email.created_at)}>
                      {formatRelative(email.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <LoadMore {...page} />
      </Card>
      {open && <OutboxDrawer email={open} onClose={() => setOpen(null)} />}
    </Page>
  );
}

function OutboxDrawer({ email, onClose }: { email: Email; onClose: () => void }) {
  const [view, setView] = useState<"email" | "thread" | "text" | "headers">(email.html_body ? "email" : "thread");
  const purged = !email.body;
  const thread = useQuery({
    queryKey: [...keys.outbox, email.id, "thread"],
    queryFn: () => unwrap(api.GET("/api/v1/outbox/{message_id}/thread", { params: { path: { message_id: email.id } } })),
    enabled: view === "thread",
  });
  return (
    <Drawer
      wide
      title={
        <span className="row">
          {email.subject} <StatusPill status={email.status} />
        </span>
      }
      onClose={onClose}
    >
      <div className="stack">
        <div className="small muted">
          {formatDateTime(email.created_at)} · {email.from_name} &lt;{email.from_email}&gt; → {email.to_email}
          {email.sent_at ? ` · accepted by the mailbox ${formatDateTime(email.sent_at)}` : ""}
          {email.contact_id && (
            <>
              {" · "}
              <Link to={`/contacts/${email.contact_id}`}>Open contact</Link>
            </>
          )}
          {email.enrollment_id && (
            <>
              {" · "}
              <Link to={`/enrollments/${email.enrollment_id}`}>Workflow step and guardrails</Link>
            </>
          )}
        </div>
        {email.status === "failed" && email.error && <div className="alert error small">The mailbox refused this email: {email.error}</div>}
        {email.status === "dry_run" && <div className="alert info small">Recorded in dry-run mode: this email was not delivered.</div>}
        {email.status === "queued" && (
          <div className="alert info small">
            Waiting for delivery through the agent's mailbox{email.attempts ? ` (attempt ${email.attempts + 1}; last problem: ${email.error ?? "unknown"})` : ""}.
          </div>
        )}
        <Tabs
          value={view}
          onChange={setView}
          tabs={[
            { id: "email", label: "As received" },
            { id: "thread", label: "Conversation" },
            { id: "text", label: "Plain text" },
            { id: "headers", label: "Headers" },
          ]}
        />
        {view === "email" && (
          <EmailFrame
            html={email.html_body ?? textToHtml(purged ? "(body purged by the retention policy)" : email.body)}
            subject={email.subject}
            from={`${email.from_name} <${email.from_email}>`}
            to={email.to_email}
          />
        )}
        {view === "thread" && (
          <div className="thread-list">
            {thread.isLoading && <SkeletonRows rows={3} />}
            <ErrorAlert error={thread.error} />
            {(thread.data ?? []).map((message) => (
              <div key={message.id} className={`thread-item ${message.direction}`}>
                <div className="row between small">
                  <strong>{message.direction === "inbound" ? message.from_email : `→ ${message.to_email}`}</strong>
                  <span className="muted">
                    {message.kind.replace(/_/g, " ")} · {formatDateTime(message.occurred_at)}
                  </span>
                </div>
                <div className="thread-body">{message.body ?? "(body purged by the retention policy)"}</div>
              </div>
            ))}
            {thread.data && thread.data.length === 0 && <div className="small muted">No other messages in this conversation yet.</div>}
          </div>
        )}
        {view === "text" && <pre className="email-preview">{email.body || "(body purged by the retention policy)"}</pre>}
        {view === "headers" && <Json value={email.headers} />}
      </div>
    </Drawer>
  );
}
