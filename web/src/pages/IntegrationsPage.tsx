/**
 * Integrations: signed webhooks (Zapier, Make, n8n), Slack alerts, Calendly and Cal.com meetings,
 * email verification, plus the operator-level ones (mailboxes, Teams, AI) for completeness.
 * Secrets are write-only: after saving, only a hint such as `hooks.slack.com/…` is shown.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, describeError, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { useOffices } from "../api/queries";
import { useSession } from "../auth/session";
import { Icon } from "../components/icons";
import type { IconName } from "../components/icons";
import { Page } from "../components/Layout";
import { SkeletonRows } from "../components/motion";
import { Badge, Button, Drawer, Empty, ErrorAlert, PageHeader, Switch, useToast } from "../components/ui";
import { formatDateTime, formatRelative, humanize } from "../lib/format";

type Overview = Schemas["IntegrationsOverviewResponse"];
type Connection = Schemas["ConnectionResponse"];
type Webhook = Schemas["WebhookSubscriptionResponse"];
type Kind = Schemas["IntegrationKind"];
type Panel = "webhooks" | Kind;

const INTEGRATIONS_KEY = ["integrations"] as const;

interface CardSpec {
  id: Panel;
  name: string;
  icon: IconName;
  blurb: string;
}

const CARDS: CardSpec[] = [
  { id: "webhooks", name: "Webhooks · Zapier, Make, n8n", icon: "sync", blurb: "Send new quote requests, won deals, replies, meetings and more to any system, signed." },
  { id: "slack", name: "Slack", icon: "bell", blurb: "Alerts in a channel: positive replies, new quote requests, deals won, meetings, forms." },
  { id: "calendly", name: "Calendly", icon: "clock", blurb: "Bookings land on the contact and their deal; workflows can start on a meeting." },
  { id: "cal_com", name: "Cal.com", icon: "clock", blurb: "Same as Calendly, for Cal.com booking pages." },
  { id: "millionverifier", name: "MillionVerifier", icon: "shield", blurb: "Verify addresses before outreach; invalid ones are never emailed. Offline rules until connected." },
];

function useOverview() {
  return useQuery({ queryKey: INTEGRATIONS_KEY, queryFn: () => unwrap(api.GET("/api/v1/integrations")) });
}

function statusOf(card: CardSpec, overview: Overview): { label: string; tone: "green" | "amber" | "gray" | "red" } {
  if (card.id === "webhooks") {
    if (overview.webhooks.some((item) => !item.is_active && item.disabled_reason)) return { label: "Needs attention", tone: "red" };
    return overview.webhooks.length ? { label: `${overview.webhooks.length} connected`, tone: "green" } : { label: "Available", tone: "gray" };
  }
  const mine = overview.connections.filter((item) => item.kind === card.id);
  if (mine.some((item) => item.last_error)) return { label: "Needs attention", tone: "red" };
  return mine.length ? { label: mine.length > 1 ? `${mine.length} connected` : "Connected", tone: "green" } : { label: "Available", tone: "gray" };
}

export function IntegrationsPage() {
  const overview = useOverview();
  const { can } = useSession();
  const [panel, setPanel] = useState<Panel | null>(null);
  if (!can("admin:manage")) {
    return (
      <Page>
        <PageHeader title="Integrations" />
        <Empty title="Admins only">Ask an admin to connect Slack, webhooks, calendars or verification.</Empty>
      </Page>
    );
  }
  return (
    <Page>
      <PageHeader
        eyebrow={<><Icon name="sync" /> Connect</>}
        title="Integrations"
        description="Connect LaneReach to the tools your team already uses. Secrets are stored encrypted and never shown again after saving."
      />
      <ErrorAlert error={overview.error} />
      {overview.isLoading && <SkeletonRows rows={4} />}
      {overview.data && (
        <>
          <div className="grid three integration-grid">
            {CARDS.map((card) => {
              const status = statusOf(card, overview.data);
              return (
                <button key={card.id} type="button" className="card integration-card" onClick={() => setPanel(card.id)}>
                  <div className="row between">
                    <span className="integration-icon"><Icon name={card.icon} /></span>
                    <Badge tone={status.tone}>{status.label}</Badge>
                  </div>
                  <strong>{card.name}</strong>
                  <span className="small muted">{card.blurb}</span>
                </button>
              );
            })}
            <Link to="/forms" className="card integration-card plain">
              <div className="row between">
                <span className="integration-icon"><Icon name="file" /></span>
                <Badge tone="blue">Built in</Badge>
              </div>
              <strong>Website quote forms</strong>
              <span className="small muted">A "Request a quote" form for your website: new contacts, tags and workflows.</span>
            </Link>
          </div>
          <h3 className="section-title" style={{ marginTop: 24 }}>Set up by your LaneReach operator</h3>
          <div className="grid three">
            {overview.data.environment.map((item) => (
              <div key={item.key} className="card integration-card static">
                <div className="row between">
                  <strong>{item.name}</strong>
                  <Badge tone={item.status === "connected" ? "green" : item.status === "available" ? "gray" : "amber"}>{humanize(item.status)}</Badge>
                </div>
                <span className="small muted">{item.detail}</span>
              </div>
            ))}
          </div>
        </>
      )}
      {panel && overview.data && (
        <Drawer wide title={CARDS.find((card) => card.id === panel)?.name ?? "Integration"} onClose={() => setPanel(null)}>
          {panel === "webhooks" ? <WebhooksPanel overview={overview.data} /> : <ConnectionsPanel kind={panel} overview={overview.data} />}
        </Drawer>
      )}
    </Page>
  );
}

function useInvalidate() {
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: INTEGRATIONS_KEY });
}

function Deliveries({ targetId }: { targetId: string }) {
  const invalidate = useInvalidate();
  const toast = useToast();
  const deliveries = useQuery({
    queryKey: [...INTEGRATIONS_KEY, "deliveries", targetId],
    queryFn: () => unwrap(api.GET("/api/v1/integrations/deliveries", { params: { query: { target_id: targetId, limit: 20 } } })),
  });
  const queryClient = useQueryClient();
  const retry = useMutation({
    mutationFn: (id: string) => unwrap(api.POST("/api/v1/integrations/deliveries/{delivery_id}/retry", { params: { path: { delivery_id: id } } })),
    onSuccess: () => {
      toast("Queued again: it goes out in the next delivery run.");
      void queryClient.invalidateQueries({ queryKey: [...INTEGRATIONS_KEY, "deliveries", targetId] });
      invalidate();
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  if (!deliveries.data?.length) return <div className="small muted">No deliveries yet.</div>;
  return (
    <ul className="delivery-list">
      {deliveries.data.map((item) => (
        <li key={item.id}>
          <Badge tone={item.status === "delivered" ? "green" : item.status === "dead" ? "red" : "amber"}>{item.status}</Badge>
          <code>{item.event_type}</code>
          <span className="small muted" title={formatDateTime(item.created_at)}>{formatRelative(item.created_at)}</span>
          <span className="small muted truncate">
            {item.last_error ?? (item.last_status_code ? `HTTP ${item.last_status_code}` : "")}
            {item.status === "pending" && item.next_attempt_at ? ` · next try ${formatRelative(item.next_attempt_at)}` : ""}
          </span>
          {item.status === "dead" && (
            <Button size="sm" variant="ghost" busy={retry.isPending} onClick={() => retry.mutate(item.id)}>Retry</Button>
          )}
        </li>
      ))}
    </ul>
  );
}

function SecretOnce({ secret, onDone }: { secret: string; onDone: () => void }) {
  const toast = useToast();
  return (
    <div className="alert success stack">
      <strong>Copy the signing secret now: it is not shown again.</strong>
      <code className="secret-box">{secret}</code>
      <div className="row" style={{ gap: 8 }}>
        <Button size="sm" onClick={() => void navigator.clipboard.writeText(secret).then(() => toast("Copied."))}>Copy</Button>
        <Button size="sm" variant="ghost" onClick={onDone}>Done</Button>
      </div>
      <span className="small">
        Check each call: <code>X-LaneReach-Signature: t=&lt;unix&gt;,v1=&lt;hex&gt;</code> where hex is HMAC-SHA256 of <code>"&lt;t&gt;.&lt;body&gt;"</code> with this secret. Reject old timestamps.
      </span>
    </div>
  );
}

function EventPicker({ events, value, onChange }: { events: string[]; value: string[]; onChange: (value: string[]) => void }) {
  return (
    <div className="chips" role="group" aria-label="Events">
      {events.map((name) => (
        <button
          key={name}
          type="button"
          className={value.includes(name) ? "chip-toggle on" : "chip-toggle"}
          aria-pressed={value.includes(name)}
          onClick={() => onChange(value.includes(name) ? value.filter((item) => item !== name) : [...value, name])}
        >
          {name}
        </button>
      ))}
    </div>
  );
}

function WebhooksPanel({ overview }: { overview: Overview }) {
  const invalidate = useInvalidate();
  const [name, setName] = useState("Zapier: new quote requests");
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>(["lead.created", "deal.won"]);
  const [secret, setSecret] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/integrations/webhooks", { body: { name, url, event_types: events } })),
    onSuccess: (created) => {
      setSecret(created.signing_secret);
      setUrl("");
      invalidate();
    },
  });
  return (
    <div className="stack">
      <p className="small muted" style={{ margin: 0 }}>
        Each event is POSTed as JSON to your HTTPS endpoint (a Zapier "Catch Hook", a Make or n8n webhook, or your own). Failed calls are retried after 1, 5, 30 and 120 minutes; a webhook failing five deliveries in a row is switched off. Private and internal addresses are refused.
      </p>
      {secret && <SecretOnce secret={secret} onDone={() => setSecret(null)} />}
      {overview.webhooks.length === 0 && !secret && <Empty title="No webhooks yet" />}
      {overview.webhooks.map((webhook) => <WebhookRow key={webhook.id} webhook={webhook} events={overview.webhook_events} onSecret={setSecret} />)}
      <form
        className="card stack"
        style={{ padding: 16 }}
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate();
        }}
      >
        <h3 style={{ margin: 0 }}>Add a webhook</h3>
        <label className="field"><span>Name</span><input className="input" value={name} onChange={(event) => setName(event.target.value)} required maxLength={100} /></label>
        <label className="field"><span>HTTPS URL</span><input className="input" type="url" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://hooks.zapier.com/hooks/catch/…" required /></label>
        <span className="small muted">Events</span>
        <EventPicker events={overview.webhook_events} value={events} onChange={setEvents} />
        <ErrorAlert error={create.error} />
        <div><Button type="submit" variant="primary" busy={create.isPending} disabled={!url || events.length === 0}>Add webhook</Button></div>
      </form>
    </div>
  );
}

function WebhookRow({ webhook, events, onSecret }: { webhook: Webhook; events: string[]; onSecret: (secret: string) => void }) {
  const invalidate = useInvalidate();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<string[] | null>(null);
  const path = { params: { path: { webhook_id: webhook.id } } };
  const update = useMutation({
    mutationFn: (body: Schemas["WebhookUpdateRequest"]) => unwrap(api.PATCH("/api/v1/integrations/webhooks/{webhook_id}", { ...path, body })),
    onSuccess: () => {
      setEditing(null);
      invalidate();
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  const test = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/integrations/webhooks/{webhook_id}/test", path)),
    onSuccess: (delivery) => {
      toast(delivery.status === "delivered" ? "Ping delivered." : `Ping failed: ${delivery.last_error ?? "no answer"}`, delivery.status === "delivered" ? undefined : "error");
      invalidate();
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  const rotate = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/integrations/webhooks/{webhook_id}/rotate-secret", path)),
    onSuccess: (result) => onSecret(result.signing_secret),
  });
  const remove = useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/integrations/webhooks/{webhook_id}", path)),
    onSuccess: invalidate,
  });
  return (
    <div className="card integration-row">
      <div className="row between" style={{ gap: 8, flexWrap: "wrap" }}>
        <div style={{ minWidth: 0 }}>
          <strong>{webhook.name}</strong>
          <div className="small muted">{webhook.url_hint} · {webhook.last_delivery_at ? `last call ${formatRelative(webhook.last_delivery_at)}` : "not called yet"}</div>
        </div>
        <Switch label="Active" checked={webhook.is_active} onChange={(value) => update.mutate({ is_active: value })} />
      </div>
      {webhook.disabled_reason && <div className="alert error small">{webhook.disabled_reason}</div>}
      {editing ? (
        <div className="stack">
          <EventPicker events={events} value={editing} onChange={setEditing} />
          <div className="row" style={{ gap: 6 }}>
            <Button size="sm" variant="primary" busy={update.isPending} disabled={editing.length === 0} onClick={() => update.mutate({ event_types: editing })}>Save events</Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
          </div>
        </div>
      ) : (
        <div className="chips">{webhook.event_types.map((name) => <Badge key={name}>{name}</Badge>)}</div>
      )}
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        <Button size="sm" busy={test.isPending} onClick={() => test.mutate()}><Icon name="play" /> Send test</Button>
        <Button size="sm" variant="ghost" onClick={() => setEditing(webhook.event_types)}>Events</Button>
        <Button size="sm" variant="ghost" onClick={() => setOpen((value) => !value)}>{open ? "Hide deliveries" : "Deliveries"}</Button>
        <Button size="sm" variant="ghost" busy={rotate.isPending} onClick={() => window.confirm("Replace the signing secret? The old one stops working now.") && rotate.mutate()}>New secret</Button>
        <Button size="sm" variant="danger" busy={remove.isPending} onClick={() => window.confirm(`Delete "${webhook.name}"?`) && remove.mutate()}>Delete</Button>
      </div>
      {open && <Deliveries targetId={webhook.id} />}
    </div>
  );
}

const SETUP: Partial<Record<Kind, ReactNode>> = {
  slack: (
    <ol className="small setup-steps">
      <li>In Slack, create an app → Incoming Webhooks → Add New Webhook to Workspace, pick the channel.</li>
      <li>Paste the webhook URL (https://hooks.slack.com/services/…) below and choose the alerts.</li>
    </ol>
  ),
  calendly: (
    <ol className="small setup-steps">
      <li>Save the connection with a signing key of your choice (16+ characters).</li>
      <li>Create a Calendly webhook subscription (API: POST /webhook_subscriptions) for <code>invitee.created</code> and <code>invitee.canceled</code> with the URL shown here and the same signing key.</li>
    </ol>
  ),
  cal_com: (
    <ol className="small setup-steps">
      <li>In Cal.com: Settings → Developer → Webhooks → New, events Booking created, rescheduled and cancelled.</li>
      <li>Use the URL shown here and the same secret you save below.</li>
    </ol>
  ),
  millionverifier: (
    <ol className="small setup-steps">
      <li>Copy the API key from your MillionVerifier account and save it below.</li>
      <li>Verify contacts from here, or per contact. Invalid addresses are blocked by the email verification guardrail.</li>
    </ol>
  ),
};

function ConnectionsPanel({ kind, overview }: { kind: Kind; overview: Overview }) {
  const invalidate = useInvalidate();
  const toast = useToast();
  const offices = useOffices();
  const mine = overview.connections.filter((item) => item.kind === kind);
  const [name, setName] = useState(kind === "slack" ? "#pricing" : humanize(kind));
  const [secret, setSecret] = useState("");
  const [alerts, setAlerts] = useState<string[]>(["positive_reply", "new_lead", "deal_won"]);
  const [officeId, setOfficeId] = useState("");
  const meetings = kind === "calendly" || kind === "cal_com";
  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/integrations/connections", {
          body: {
            kind,
            name,
            secret,
            config: kind === "slack" ? { alerts } : meetings ? { office_id: officeId || null } : {},
          },
        }),
      ),
    onSuccess: () => {
      setSecret("");
      toast("Connected.");
      invalidate();
    },
  });
  const verifyAll = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/contacts/verify-emails", { body: { limit: 500 } })),
    onSuccess: (result) =>
      toast(
        `Checked ${result.checked} contacts with ${result.verifier}: ${Object.entries(result.by_status).map(([status, count]) => `${count} ${status}`).join(", ") || "nothing new"}.`,
      ),
    onError: (error) => toast(describeError(error), "error"),
  });
  const canAdd = kind !== "millionverifier" || mine.length === 0;
  return (
    <div className="stack">
      {SETUP[kind]}
      {mine.map((connection) => <ConnectionRow key={connection.id} connection={connection} alertsCatalog={overview.slack_alerts} />)}
      {kind === "millionverifier" && (
        <div className="card row between" style={{ padding: 14, gap: 12, flexWrap: "wrap" }}>
          <span className="small">
            {mine.length ? "Verifying with MillionVerifier." : "Not connected: offline rules check syntax, disposable domains and role mailboxes."}
          </span>
          <Button size="sm" busy={verifyAll.isPending} onClick={() => verifyAll.mutate()}><Icon name="shield" /> Verify unverified contacts</Button>
        </div>
      )}
      {canAdd && (
        <form
          className="card stack"
          style={{ padding: 16 }}
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <h3 style={{ margin: 0 }}>{mine.length ? "Add another" : "Connect"}</h3>
          <label className="field"><span>Name</span><input className="input" value={name} onChange={(event) => setName(event.target.value)} required maxLength={100} /></label>
          <label className="field">
            <span>{kind === "slack" ? "Incoming-webhook URL" : kind === "millionverifier" ? "API key" : "Signing key"}</span>
            <input className="input" type="password" autoComplete="off" value={secret} onChange={(event) => setSecret(event.target.value)} required minLength={8} />
          </label>
          {kind === "slack" && (
            <>
              <span className="small muted">Alerts</span>
              <div className="chips">
                {Object.entries(overview.slack_alerts).map(([key, label]) => (
                  <button key={key} type="button" className={alerts.includes(key) ? "chip-toggle on" : "chip-toggle"} onClick={() => setAlerts(alerts.includes(key) ? alerts.filter((item) => item !== key) : [...alerts, key])}>
                    {label}
                  </button>
                ))}
              </div>
            </>
          )}
          {meetings && (
            <label className="field">
              <span>Office of new contacts</span>
              <select className="select" value={officeId} onChange={(event) => setOfficeId(event.target.value)}>
                <option value="">No office</option>
                {(offices.data ?? []).map((office) => <option key={office.id} value={office.id}>{office.name}</option>)}
              </select>
            </label>
          )}
          <ErrorAlert error={create.error} />
          <div><Button type="submit" variant="primary" busy={create.isPending} disabled={secret.length < 8}>Save</Button></div>
        </form>
      )}
    </div>
  );
}

function ConnectionRow({ connection, alertsCatalog }: { connection: Connection; alertsCatalog: Record<string, string> }) {
  const invalidate = useInvalidate();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const path = { params: { path: { connection_id: connection.id } } };
  const update = useMutation({
    mutationFn: (body: Schemas["ConnectionUpdateRequest"]) => unwrap(api.PATCH("/api/v1/integrations/connections/{connection_id}", { ...path, body })),
    onSuccess: invalidate,
    onError: (error) => toast(describeError(error), "error"),
  });
  const test = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/integrations/connections/{connection_id}/test", path)),
    onSuccess: (delivery) => {
      toast(delivery.status === "delivered" ? "Test alert posted." : `Slack refused it: ${delivery.last_error ?? "no answer"}`, delivery.status === "delivered" ? undefined : "error");
      invalidate();
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  const remove = useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/integrations/connections/{connection_id}", path)),
    onSuccess: invalidate,
  });
  const alerts = (connection.config.alerts as string[] | undefined) ?? [];
  return (
    <div className="card integration-row">
      <div className="row between" style={{ gap: 8 }}>
        <div style={{ minWidth: 0 }}>
          <strong>{connection.name}</strong>
          <div className="small muted">
            {connection.secret_hint ?? "secret saved"} · {connection.last_used_at ? `used ${formatRelative(connection.last_used_at)}` : "not used yet"}
          </div>
        </div>
        <Switch label="Active" checked={connection.is_active} onChange={(value) => update.mutate({ is_active: value })} />
      </div>
      {connection.last_error && <div className="alert error small">{connection.last_error}</div>}
      {connection.webhook_url && (
        <div className="stack tight">
          <span className="small muted">Webhook URL for {humanize(connection.kind)}</span>
          <div className="row" style={{ gap: 6 }}>
            <code className="secret-box truncate">{connection.webhook_url}</code>
            <Button size="sm" onClick={() => void navigator.clipboard.writeText(connection.webhook_url ?? "").then(() => toast("Copied."))}>Copy</Button>
          </div>
        </div>
      )}
      {alerts.length > 0 && <div className="chips">{alerts.map((key) => <Badge key={key}>{alertsCatalog[key] ?? key}</Badge>)}</div>}
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        {connection.kind === "slack" && (
          <>
            <Button size="sm" busy={test.isPending} onClick={() => test.mutate()}><Icon name="play" /> Send test</Button>
            <Button size="sm" variant="ghost" onClick={() => setOpen((value) => !value)}>{open ? "Hide deliveries" : "Deliveries"}</Button>
          </>
        )}
        <Button size="sm" variant="danger" busy={remove.isPending} onClick={() => window.confirm(`Disconnect "${connection.name}"?`) && remove.mutate()}>Disconnect</Button>
      </div>
      {open && <Deliveries targetId={connection.id} />}
    </div>
  );
}
