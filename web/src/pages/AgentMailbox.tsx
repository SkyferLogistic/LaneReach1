/**
 * An agent's mailbox: connect SMTP (sending) and IMAP (reading replies), test the login, see
 * the last check. Admins edit; everyone else sees the status. The password is never shown.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys } from "../api/queries";
import { useConfirm } from "../components/confirm";
import { Icon } from "../components/icons";
import { Badge, Button, ErrorAlert, useToast } from "../components/ui";
import { formatDateTime, formatRelative } from "../lib/format";

type Security = Schemas["MailSecurity"];
type Preset = Schemas["MailboxPresetResponse"];

const STATUS_TONES: Record<string, "green" | "amber" | "red"> = { verified: "green", unverified: "amber", error: "red" };
const STATUS_LABELS: Record<string, string> = { verified: "Connected", unverified: "Not tested", error: "Problem" };

export function MailboxStatusPill({ status }: { status: string | null | undefined }) {
  if (!status) return <Badge tone="gray">No mailbox</Badge>;
  return <Badge tone={STATUS_TONES[status] ?? "gray"}>{STATUS_LABELS[status] ?? status}</Badge>;
}

interface Form {
  preset: Schemas["MailboxPreset"];
  smtp_host: string;
  smtp_port: string;
  smtp_security: Security;
  imap_host: string;
  imap_port: string;
  imap_security: Security;
  username: string;
  password: string;
}

function formFrom(mailbox: Schemas["MailboxResponse"] | null | undefined, agentEmail: string): Form {
  return {
    preset: (mailbox?.preset as Schemas["MailboxPreset"]) ?? "microsoft_365",
    smtp_host: mailbox?.smtp_host ?? "smtp.office365.com",
    smtp_port: String(mailbox?.smtp_port ?? 587),
    smtp_security: mailbox?.smtp_security ?? "starttls",
    imap_host: mailbox?.imap_host ?? "outlook.office365.com",
    imap_port: String(mailbox?.imap_port ?? 993),
    imap_security: mailbox?.imap_security ?? "ssl",
    username: mailbox?.username ?? agentEmail,
    password: "",
  };
}

export function AgentMailboxPanel({ senderId, agentEmail, canEdit }: { senderId: string; agentEmail: string; canEdit: boolean }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [confirmDialog, ask] = useConfirm();
  const state = useQuery({
    queryKey: [...keys.senders, senderId, "mailbox"],
    queryFn: () => unwrap(api.GET("/api/v1/senders/{sender_id}/mailbox", { params: { path: { sender_id: senderId } } })),
  });
  const presets = useQuery({
    queryKey: ["mailbox-presets"],
    queryFn: () => unwrap(api.GET("/api/v1/mailbox-presets")),
    staleTime: Infinity,
    enabled: canEdit,
  });
  const mailbox = state.data?.mailbox ?? null;
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<Form>(() => formFrom(null, agentEmail));
  useEffect(() => {
    if (state.data) setForm(formFrom(state.data.mailbox, agentEmail));
  }, [state.data, agentEmail]);
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: [...keys.senders, senderId, "mailbox"] });
    void queryClient.invalidateQueries({ queryKey: keys.senders });
  };
  const saveAndTest = useMutation({
    mutationFn: async () => {
      await unwrap(
        api.PUT("/api/v1/senders/{sender_id}/mailbox", {
          params: { path: { sender_id: senderId } },
          body: {
            preset: form.preset,
            smtp_host: form.smtp_host.trim(),
            smtp_port: Number(form.smtp_port),
            smtp_security: form.smtp_security,
            imap_host: form.imap_host.trim(),
            imap_port: Number(form.imap_port),
            imap_security: form.imap_security,
            username: form.username.trim(),
            password: form.password ? form.password : null,
          },
        }),
      );
      return unwrap(api.POST("/api/v1/senders/{sender_id}/mailbox/test", { params: { path: { sender_id: senderId } } }));
    },
    onSuccess: (tested) => {
      setForm((current) => ({ ...current, password: "" }));
      refresh();
      if (tested.status === "verified") {
        setEditing(false);
        toast("Mailbox connected: SMTP and IMAP logins work.");
      } else {
        toast("Saved, but the login test failed. See the details below.", "error");
      }
    },
  });
  const retest = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/senders/{sender_id}/mailbox/test", { params: { path: { sender_id: senderId } } })),
    onSuccess: (tested) => {
      refresh();
      toast(tested.status === "verified" ? "The mailbox works." : "The login test failed.", tested.status === "verified" ? "success" : "error");
    },
  });
  const disconnect = useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/senders/{sender_id}/mailbox", { params: { path: { sender_id: senderId } } })),
    onSuccess: () => {
      refresh();
      toast("Mailbox disconnected; the saved password was deleted.");
    },
  });
  const choosePreset = (key: Schemas["MailboxPreset"]) => {
    const preset = presets.data?.find((item) => item.key === key);
    setForm((current) =>
      preset
        ? {
            ...current,
            preset: key,
            smtp_host: preset.smtp_host || current.smtp_host,
            smtp_port: String(preset.smtp_port),
            smtp_security: preset.smtp_security,
            imap_host: preset.imap_host || current.imap_host,
            imap_port: String(preset.imap_port),
            imap_security: preset.imap_security,
          }
        : { ...current, preset: key },
    );
  };
  const set = (name: keyof Form) => (event: { target: { value: string } }) => setForm((current) => ({ ...current, [name]: event.target.value }));
  const help: Preset | undefined = presets.data?.find((item) => item.key === form.preset);
  const dryRun = state.data?.sending_provider !== "smtp";

  return (
    <section className="card mailbox-panel">
      <div className="card-header">
        <h2 className="row">
          <Icon name="mail" /> Mailbox
        </h2>
        <MailboxStatusPill status={mailbox?.status} />
      </div>
      <div className="card-body stack">
        <ErrorAlert error={state.error} />
        {dryRun && (
          <div className="alert info small">
            This workspace is in <strong>dry-run mode</strong>: emails are recorded in the Outbox, not sent. You can connect and test mailboxes now;
            they send for real once the administrator switches sending to SMTP.
          </div>
        )}
        {mailbox && !editing && (
          <div className="stack tight small">
            <div>
              <strong>{mailbox.username}</strong> · sends via {mailbox.smtp_host}:{mailbox.smtp_port} ({mailbox.smtp_security}), reads replies from {mailbox.imap_host}:
              {mailbox.imap_port} ({mailbox.imap_security})
            </div>
            <div className="muted">
              {mailbox.verified_at ? `Login tested ${formatDateTime(mailbox.verified_at)}` : "Login not tested yet"}
              {mailbox.last_polled_at ? ` · replies checked ${formatRelative(mailbox.last_polled_at)}` : ""}
            </div>
            {mailbox.last_error && <div className="alert error small">{mailbox.last_error}</div>}
          </div>
        )}
        {!mailbox && !editing && (
          <p className="small muted" style={{ margin: 0 }}>
            Connect this agent's mailbox so LaneReach sends from it (SMTP) and reads the replies (IMAP). Replies stay unread in the mailbox; only answers to
            LaneReach emails are picked up.
          </p>
        )}
        {canEdit && editing && (
          <form
            className="stack"
            onSubmit={(event) => {
              event.preventDefault();
              saveAndTest.mutate();
            }}
          >
            <label className="field">
              <span>Email provider</span>
              <select className="select" value={form.preset} onChange={(event) => choosePreset(event.target.value as Schemas["MailboxPreset"])}>
                {(presets.data ?? []).map((preset) => (
                  <option key={preset.key} value={preset.key}>
                    {preset.label}
                  </option>
                ))}
              </select>
            </label>
            {help?.help && <p className="field-help" style={{ margin: 0 }}>{help.help}</p>}
            <div className="form-grid">
              <label className="field">
                <span>SMTP server</span>
                <input className="input" required value={form.smtp_host} onChange={set("smtp_host")} />
              </label>
              <label className="field">
                <span>Port</span>
                <input className="input" type="number" min={1} max={65535} required value={form.smtp_port} onChange={set("smtp_port")} />
              </label>
              <label className="field">
                <span>Security</span>
                <SecuritySelect value={form.smtp_security} onChange={(value) => setForm((current) => ({ ...current, smtp_security: value }))} />
              </label>
              <label className="field">
                <span>IMAP server</span>
                <input className="input" required value={form.imap_host} onChange={set("imap_host")} />
              </label>
              <label className="field">
                <span>Port</span>
                <input className="input" type="number" min={1} max={65535} required value={form.imap_port} onChange={set("imap_port")} />
              </label>
              <label className="field">
                <span>Security</span>
                <SecuritySelect value={form.imap_security} onChange={(value) => setForm((current) => ({ ...current, imap_security: value }))} />
              </label>
              <label className="field">
                <span>Username</span>
                <input className="input" required autoComplete="off" value={form.username} onChange={set("username")} />
              </label>
              <label className="field">
                <span>Password or app password</span>
                <input
                  className="input"
                  type="password"
                  autoComplete="new-password"
                  required={!mailbox}
                  placeholder={mailbox ? "Saved (leave empty to keep it)" : ""}
                  value={form.password}
                  onChange={set("password")}
                />
              </label>
            </div>
            <p className="field-help" style={{ margin: 0 }}>
              The password is encrypted and never shown again. Testing only logs in: nothing is sent or read.
            </p>
            <ErrorAlert error={saveAndTest.error} />
            <div className="row">
              <Button variant="primary" type="submit" busy={saveAndTest.isPending}>
                Save and test
              </Button>
              <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
                Cancel
              </Button>
            </div>
          </form>
        )}
        {canEdit && !editing && (
          <div className="row">
            <Button onClick={() => setEditing(true)}>{mailbox ? "Change settings" : "Connect mailbox"}</Button>
            {mailbox && (
              <Button busy={retest.isPending} onClick={() => retest.mutate()}>
                Test again
              </Button>
            )}
            {mailbox && (
              <Button
                variant="ghost"
                busy={disconnect.isPending}
                onClick={() =>
                  void ask({ title: "Disconnect this mailbox?", message: "The saved password is deleted. In SMTP mode the agent stops sending until a mailbox is connected again." }).then(
                    (ok) => ok && disconnect.mutate(),
                  )
                }
              >
                Disconnect
              </Button>
            )}
          </div>
        )}
        <ErrorAlert error={retest.error ?? disconnect.error} />
        {confirmDialog}
      </div>
    </section>
  );
}

function SecuritySelect({ value, onChange }: { value: Security; onChange: (value: Security) => void }) {
  return (
    <select className="select" value={value} onChange={(event) => onChange(event.target.value as Security)}>
      <option value="ssl">SSL/TLS</option>
      <option value="starttls">STARTTLS</option>
      <option value="none">None (development only)</option>
    </select>
  );
}
