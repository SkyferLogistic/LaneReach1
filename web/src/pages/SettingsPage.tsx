/** Tenant settings: kill switch, window and tracking, AI usage, reply policy, branding, library, API keys, calendars, audit log. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys, useSendingSettings, useServiceSettings } from "../api/queries";
import { Icon } from "../components/icons";
import { LINE_ICONS, SERVICE_LINES, SERVICE_LINE_LABELS } from "../lib/freight";
import { useSession } from "../auth/session";
import { Page } from "../components/Layout";
import { Badge, Button, Card, ErrorAlert, Loading, Modal, PageHeader, Switch, Tabs, useErrorToast, useToast } from "../components/ui";
import { formatDateTime, humanize } from "../lib/format";
import { AISettings } from "./AISettings";
import { AuditLog } from "./AuditLog";
import { BrandingSettings, LibrarySettings, WindowGrid } from "./SettingsLibrary";

type Tab = "sending" | "services" | "ai" | "policies" | "branding" | "library" | "keys" | "calendars" | "audit";

export function SettingsPage() {
  const [params] = useSearchParams();
  const requested = params.get("tab") as Tab | null;
  const [tab, setTab] = useState<Tab>(requested ?? "sending");
  return (
    <Page>
      <PageHeader title="Settings" description="Controls that apply to the whole tenant." />
      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "sending", label: "Sending" },
          { id: "services", label: "Services" },
          { id: "ai", label: "AI usage" },
          { id: "policies", label: "Reply policy" },
          { id: "branding", label: "Branding" },
          { id: "library", label: "Scripts library" },
          { id: "keys", label: "API keys" },
          { id: "calendars", label: "Country calendars" },
          { id: "audit", label: "Audit log" },
        ]}
      />
      {tab === "sending" && <SendingSettings />}
      {tab === "services" && <ServiceSettings />}
      {tab === "ai" && <AISettings onOpenPolicies={() => setTab("policies")} />}
      {tab === "policies" && <ReplyPolicies />}
      {tab === "branding" && <BrandingSettings />}
      {tab === "library" && <LibrarySettings />}
      {tab === "keys" && <ApiKeys />}
      {tab === "calendars" && <Calendars />}
      {tab === "audit" && <AuditLog />}
    </Page>
  );
}

function ServiceSettings() {
  const settings = useServiceSettings();
  const { can } = useSession();
  const admin = can("admin:manage");
  const queryClient = useQueryClient();
  const toast = useToast();
  const [chosen, setChosen] = useState<string[] | null>(null);
  const selected = chosen ?? settings.data?.service_lines ?? [];
  const save = useMutation({
    mutationFn: () =>
      unwrap(api.PUT("/api/v1/settings/services", { body: { service_lines: selected as Schemas["ServiceLine"][] } })),
    onSuccess: (saved) => {
      queryClient.setQueryData(["settings", "services"], saved);
      setChosen(null);
      toast("Services saved. Menus and filters now show only these lines.");
    },
  });
  const toggle = (line: string) =>
    setChosen(selected.includes(line) ? selected.filter((item) => item !== line) : [...selected, line]);
  return (
    <Card title="Services you sell">
      <p className="muted small" style={{ marginTop: 0 }}>
        LaneReach covers every freight line. Switch off the ones your company does not sell to keep menus, filters and
        boards focused. Existing agents, deals and quotes on a hidden line keep working.
      </p>
      {settings.isLoading ? (
        <Loading />
      ) : (
        <div className="service-grid">
          {SERVICE_LINES.map((line) => {
            const on = selected.includes(line);
            return (
              <button key={line} type="button" className={`service-tile ${line}${on ? " on" : ""}`} aria-pressed={on} disabled={!admin} onClick={() => toggle(line)}>
                <Icon name={LINE_ICONS[line]} />
                <strong>{SERVICE_LINE_LABELS[line]}</strong>
                <span className="small">{on ? "Offered" : "Hidden"}</span>
              </button>
            );
          })}
        </div>
      )}
      <ErrorAlert error={settings.error ?? save.error} />
      {admin && (
        <div className="row" style={{ marginTop: 12 }}>
          <Button variant="primary" busy={save.isPending} disabled={chosen === null || selected.length === 0} onClick={() => save.mutate()}>
            Save services
          </Button>
          {chosen !== null && <Button variant="ghost" onClick={() => setChosen(null)}>Undo changes</Button>}
          {selected.length === 0 && <span className="small muted">Choose at least one line.</span>}
        </div>
      )}
    </Card>
  );
}

function SendingSettings() {
  const settings = useSendingSettings();
  const { can } = useSession();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [start, setStart] = useState<string | null>(null);
  const [end, setEnd] = useState<string | null>(null);
  const update = useMutation({
    mutationFn: (body: Schemas["SendingSettingsUpdateRequest"]) => unwrap(api.PATCH("/api/v1/settings/sending", { body })),
    onSuccess: (next) => {
      toast(next.sending_paused ? "Sending paused for the whole tenant." : "Settings saved.");
      void queryClient.invalidateQueries({ queryKey: keys.settings });
    },
  });
  if (!settings.data) return <Loading />;
  const data = settings.data;
  const admin = can("admin:manage");
  return (
    <div className="grid two" style={{ alignItems: "start" }}>
      <Card title="Kill switch">
        <div className="stack">
          {data.sending_paused ? (
            <div className="alert error">Paused since {formatDateTime(data.sending_paused_at)}. Every email is deferred.</div>
          ) : (
            <div className="alert success">Sending is on.</div>
          )}
          <p className="small muted" style={{ margin: 0 }}>
            Pausing defers every email of every workflow (nothing is lost). After resuming, deferred emails go out in each recipient's next sending window.
          </p>
          {admin && (
            <div>
              <Button
                variant={data.sending_paused ? "success" : "danger"}
                busy={update.isPending}
                onClick={() => update.mutate({ sending_paused: !data.sending_paused })}
              >
                {data.sending_paused ? "Resume sending" : "Pause all sending"}
              </Button>
            </div>
          )}
        </div>
      </Card>
      <Card title="Sending window">
        <div className="stack">
          <p className="small muted" style={{ margin: 0 }}>In the recipient's local time, on their country's business days.</p>
          <div className="row">
            <label className="field">
              <span>From</span>
              <input className="input" type="time" disabled={!admin} value={start ?? data.sending_window_start.slice(0, 5)} onChange={(event) => setStart(event.target.value)} />
            </label>
            <label className="field">
              <span>To</span>
              <input className="input" type="time" disabled={!admin} value={end ?? data.sending_window_end.slice(0, 5)} onChange={(event) => setEnd(event.target.value)} />
            </label>
          </div>
          {admin && (
            <div>
              <Button
                disabled={start === null && end === null}
                busy={update.isPending}
                onClick={() =>
                  update.mutate({
                    sending_window_start: start ?? undefined,
                    sending_window_end: end ?? undefined,
                  })
                }
              >
                Save window
              </Button>
            </div>
          )}
          <ErrorAlert error={update.error} />
          <WindowGrid start={start ?? data.sending_window_start.slice(0, 5)} end={end ?? data.sending_window_end.slice(0, 5)} />
        </div>
      </Card>
      <Card title="Open and click tracking">
        <div className="stack">
          <p className="small muted" style={{ margin: 0 }}>
            HTML emails carry an invisible pixel and links that pass through LaneReach, so campaigns can branch on
            opens and clicks and show results. Plain-text emails are never tracked. Mail scanners are filtered out;
            Apple Mail may preload images and count as an open.
          </p>
          <label className="row between nowrap" style={{ gap: 12, alignItems: "center" }}>
            <span>
              <strong>Track opens</strong>
              <span className="small muted" style={{ display: "block" }}>A 1×1 image at the end of the email.</span>
            </span>
            <Switch label="Track opens" checked={data.track_opens} disabled={!admin || update.isPending} onChange={(value) => update.mutate({ track_opens: value })} />
          </label>
          <label className="row between nowrap" style={{ gap: 12, alignItems: "center" }}>
            <span>
              <strong>Track clicks</strong>
              <span className="small muted" style={{ display: "block" }}>Links to other sites; unsubscribe, email and phone links stay direct.</span>
            </span>
            <Switch label="Track clicks" checked={data.track_clicks} disabled={!admin || update.isPending} onChange={(value) => update.mutate({ track_clicks: value })} />
          </label>
        </div>
      </Card>
    </div>
  );
}

const MODES = ["draft_for_review", "auto_send", "forward_to_owner"];

function ReplyPolicies() {
  const policies = useQuery({ queryKey: keys.policies, queryFn: () => unwrap(api.GET("/api/v1/ai/reply-policies")) });
  const { can } = useSession();
  const queryClient = useQueryClient();
  const onError = useErrorToast();
  const toast = useToast();
  const update = useMutation({
    mutationFn: ({ bucket, body }: { bucket: Schemas["ReplyBucket"]; body: Schemas["ReplyPolicyUpdateRequest"] }) =>
      unwrap(api.PUT("/api/v1/ai/reply-policies/{bucket}", { params: { path: { bucket } }, body })),
    onSuccess: (policy) => {
      const writer = policy.draft_source === "built_in" ? ", built-in message" : "";
      toast(`${humanize(policy.bucket)}: ${humanize(policy.action_mode)}${policy.auto_send_enabled ? " (enabled)" : ""}${writer}.`);
      void queryClient.invalidateQueries({ queryKey: keys.policies });
    },
    onError,
  });
  const admin = can("admin:manage");
  return (
    <Card title="How each kind of reply is handled" flush>
      <div className="card-body" style={{ paddingBottom: 0 }}>
        <p className="small muted" style={{ marginTop: 0 }}>
          The AI only classifies; this table decides. Uncertain classifications always go to review. Protective buckets (bounces, complaints, unsubscribes…) are fixed. “Written by” picks an AI draft or your built-in message (template “reply_&lt;bucket&gt;”, no AI call).
        </p>
      </div>
      {policies.isLoading && <Loading />}
      <ErrorAlert error={policies.error} />
      <table className="table">
        <thead>
          <tr>
            <th>Bucket</th>
            <th>Answer</th>
            <th>Written by</th>
            <th>Protection actions</th>
            <th>Handover</th>
          </tr>
        </thead>
        <tbody>
          {policies.data?.map((policy) => (
            <tr key={policy.bucket}>
              <td><strong>{humanize(policy.bucket)}</strong>{policy.overridden && <div><Badge tone="cyan">customized</Badge></div>}</td>
              <td>
                {policy.configurable && admin ? (
                  <select
                    className="select"
                    value={policy.action_mode === "auto_send" && policy.auto_send_enabled ? "auto_send" : policy.action_mode}
                    onChange={(event) => {
                      const mode = event.target.value;
                      if (mode === "auto_send" && !window.confirm(`Send AI replies to confident “${humanize(policy.bucket)}” replies without review?`)) return;
                      update.mutate({
                        bucket: policy.bucket,
                        body:
                          mode === policy.default_action_mode
                            ? { draft_source: policy.draft_source }
                            : { action_mode: mode as Schemas["ActionMode"], auto_send_enabled: mode === "auto_send", draft_source: policy.draft_source },
                      });
                    }}
                  >
                    {MODES.map((mode) => (
                      <option key={mode} value={mode}>
                        {humanize(mode)}{mode === policy.default_action_mode ? " (default)" : ""}
                      </option>
                    ))}
                  </select>
                ) : (
                  <Badge tone={policy.action_mode === "none" ? "gray" : "blue"}>{humanize(policy.action_mode)}</Badge>
                )}
              </td>
              <td>
                {writesAnswer(policy) && policy.configurable && admin ? (
                  <select
                    className="select"
                    aria-label={`Who writes the answer to ${humanize(policy.bucket)} replies`}
                    value={policy.draft_source}
                    onChange={(event) =>
                      update.mutate({
                        bucket: policy.bucket,
                        body: {
                          ...(policy.action_mode === policy.default_action_mode
                            ? {}
                            : { action_mode: policy.action_mode, auto_send_enabled: policy.auto_send_enabled }),
                          draft_source: event.target.value as Schemas["DraftSource"],
                        },
                      })
                    }
                  >
                    <option value="ai">AI draft</option>
                    <option value="built_in">Built-in message</option>
                  </select>
                ) : writesAnswer(policy) ? (
                  <Badge tone={policy.draft_source === "built_in" ? "cyan" : "violet"}>{policy.draft_source === "built_in" ? "Built-in message" : "AI draft"}</Badge>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
              <td className="small">{policy.protection_actions.map(humanize).join(", ") || "—"}</td>
              <td className="small">{policy.handover ? humanize(policy.handover) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

/** Only answers that are drafted (for review or auto-send) have a writer. */
function writesAnswer(policy: Schemas["ReplyPolicyResponse"]): boolean {
  return policy.action_mode === "draft_for_review" || policy.action_mode === "auto_send";
}

const SCOPES = ["ai:run", "ai:review", "workflows:read", "workflows:write", "workflows:publish", "enrollments:write", "events:write", "admin:manage"];

function ApiKeys() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const onError = useErrorToast();
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const apiKeys = useQuery({
    queryKey: ["api-keys"],
    queryFn: () => unwrap(api.GET("/api/v1/auth/api-keys", { params: { query: { limit: 100 } } })),
    enabled: can("admin:manage"),
  });
  const revoke = useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/api/v1/auth/api-keys/{api_key_id}", { params: { path: { api_key_id: id } } })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["api-keys"] }),
    onError,
  });
  if (!can("admin:manage")) return <div className="alert info">Only admins manage API keys.</div>;
  return (
    <Card title="API keys" actions={<Button variant="primary" onClick={() => setCreating(true)}>New key</Button>} flush>
      {secret && (
        <div className="card-body">
          <div className="alert warning">
            Copy the key now; it is shown only once:
            <pre className="mono" style={{ marginTop: 6 }}>{secret}</pre>
          </div>
        </div>
      )}
      {apiKeys.isLoading && <Loading />}
      <table className="table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Prefix</th>
            <th>Scopes</th>
            <th>Last used</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {apiKeys.data?.items.map((key) => (
            <tr key={key.id}>
              <td>{key.name} <Badge>{key.environment}</Badge></td>
              <td className="mono small">{key.key_prefix}…</td>
              <td className="small">{key.scopes.join(", ")}</td>
              <td className="small">{key.last_used_at ? formatDateTime(key.last_used_at) : "never"}</td>
              <td>
                {key.revoked_at ? (
                  <Badge tone="red">revoked</Badge>
                ) : (
                  <Button size="sm" variant="danger" onClick={() => window.confirm(`Revoke ${key.name}?`) && revoke.mutate(key.id)}>
                    Revoke
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {creating && (
        <NewKeyModal
          onClose={() => setCreating(false)}
          onCreated={(value) => {
            setSecret(value);
            setCreating(false);
            void queryClient.invalidateQueries({ queryKey: ["api-keys"] });
          }}
        />
      )}
    </Card>
  );
}

function NewKeyModal({ onClose, onCreated }: { onClose: () => void; onCreated: (secret: string) => void }) {
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["events:write", "workflows:read"]);
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/auth/api-keys", { body: { name, scopes: scopes as Schemas["Scope"][] } })),
    onSuccess: (created) => onCreated(created.secret ?? ""),
  });
  return (
    <Modal
      title="New API key"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={create.isPending} disabled={!name || scopes.length === 0} onClick={() => create.mutate()}>
            Create
          </Button>
        </>
      }
    >
      <div className="stack">
        <label className="field"><span>Name</span><input className="input" value={name} placeholder="CRM sync" onChange={(event) => setName(event.target.value)} /></label>
        <div className="field">
          <span>Scopes</span>
          <div className="chips">
            {SCOPES.map((scope) => (
              <button
                key={scope}
                type="button"
                className={scopes.includes(scope) ? "chip-toggle on" : "chip-toggle"}
                onClick={() => setScopes((current) => (current.includes(scope) ? current.filter((value) => value !== scope) : [...current, scope]))}
              >
                {scope}
              </button>
            ))}
          </div>
        </div>
        <ErrorAlert error={create.error} />
      </div>
    </Modal>
  );
}

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function Calendars() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const onError = useErrorToast();
  const calendars = useQuery({ queryKey: ["country-calendars"], queryFn: () => unwrap(api.GET("/api/v1/country-calendars")) });
  const [editing, setEditing] = useState<Schemas["CountryCalendarResponse"] | null>(null);
  const [weekend, setWeekend] = useState<number[]>([]);
  const [holidays, setHolidays] = useState("");
  const save = useMutation({
    mutationFn: (calendar: Schemas["CountryCalendarResponse"]) =>
      unwrap(
        api.PUT("/api/v1/country-calendars/{country}", {
          params: { path: { country: calendar.country } },
          body: {
            timezone: calendar.timezone,
            weekend_days: weekend,
            extra_holidays: holidays.split(/[\s,]+/).filter(Boolean),
            extra_workdays: calendar.extra_workdays,
          },
        }),
      ),
    onSuccess: () => {
      setEditing(null);
      void queryClient.invalidateQueries({ queryKey: ["country-calendars"] });
    },
    onError,
  });
  return (
    <Card title="Working weeks by country" flush>
      <div className="card-body" style={{ paddingBottom: 0 }}>
        <p className="small muted" style={{ marginTop: 0 }}>Waits count business days in the recipient's country; public holidays come from a built-in calendar.</p>
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>Country</th>
            <th>Timezone</th>
            <th>Weekend</th>
            <th>Extra holidays</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {calendars.data?.map((calendar) => (
            <tr key={calendar.country}>
              <td><strong>{calendar.country}</strong> {!calendar.is_default && <Badge tone="cyan">custom</Badge>}</td>
              <td className="small">{calendar.timezone}</td>
              <td className="small">{calendar.weekend_days.map((day) => WEEKDAYS[day - 1]).join(", ")}</td>
              <td className="small">{calendar.extra_holidays.join(", ") || "—"}</td>
              <td>
                {can("admin:manage") && (
                  <Button
                    size="sm"
                    onClick={() => {
                      setEditing(calendar);
                      setWeekend(calendar.weekend_days);
                      setHolidays(calendar.extra_holidays.join(", "));
                    }}
                  >
                    Edit
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {editing && (
        <Modal
          title={`Calendar for ${editing.country}`}
          onClose={() => setEditing(null)}
          footer={
            <>
              <Button onClick={() => setEditing(null)}>Cancel</Button>
              <Button variant="primary" busy={save.isPending} onClick={() => save.mutate(editing)}>Save</Button>
            </>
          }
        >
          <div className="stack">
            <div className="field">
              <span>Weekend days</span>
              <div className="chips">
                {WEEKDAYS.map((label, index) => (
                  <button
                    key={label}
                    type="button"
                    className={weekend.includes(index + 1) ? "chip-toggle on" : "chip-toggle"}
                    onClick={() => setWeekend((current) => (current.includes(index + 1) ? current.filter((day) => day !== index + 1) : [...current, index + 1].sort()))}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <label className="field">
              <span>Extra holidays (YYYY-MM-DD, comma separated)</span>
              <input className="input" value={holidays} onChange={(event) => setHolidays(event.target.value)} placeholder="2026-12-02, 2026-12-03" />
            </label>
          </div>
        </Modal>
      )}
    </Card>
  );
}
