/**
 * Agents: the mailboxes of every service line that send the scripts, each with a branded signature.
 *
 * A send-email step picks an agent of its service line (fewest emails today) and the same agent
 * keeps the whole conversation with a contact.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys, useMailboxHealth, useOfferedLines, useOffices, useSenders } from "../api/queries";
import { useSession } from "../auth/session";
import { EmailFrame } from "../components/EmailFrame";
import { HealthPill } from "../components/HealthPill";
import { AgentMailboxPanel, MailboxStatusPill } from "./AgentMailbox";
import { ImageDrop } from "../components/ImageDrop";
import { Icon } from "../components/icons";
import { Page } from "../components/Layout";
import { SkeletonRows } from "../components/motion";
import { Badge, Button, Drawer, Empty, ErrorAlert, LinePill, Loading, Modal, PageHeader, Switch, useToast } from "../components/ui";
import { percent } from "../lib/format";
import { LINE_ICONS, SERVICE_LINES, SERVICE_LINE_LABELS, initials } from "../lib/freight";
import type { ServiceLine } from "../lib/freight";
import { useConfirm, useDelete, usePermission } from "../components/confirm";
import { ServiceLineOptions } from "../components/pickers";

type Agent = Schemas["SenderResponse"];

const SIGNATURE_MODES: { id: Schemas["SignatureMode"]; label: string; help: string }[] = [
  { id: "generated", label: "Generated", help: "Built from the agent's details and the tenant branding (logo, tagline, offices, badges)." },
  { id: "custom_html", label: "Custom HTML", help: "Paste the signature HTML; unsafe tags and scripts are removed." },
  { id: "image", label: "Image", help: "A signature picture uploaded as an email image." },
];

export function AgentsPage() {
  const [params, setParams] = useSearchParams();
  const senders = useSenders();
  const offices = useOffices();
  const { can } = useSession();
  const admin = can("admin:manage");
  const [importing, setImporting] = useState(params.get("import") === "1");
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState<Agent | null>(null);
  const [search, setSearch] = useState("");

  useEffect(() => {
    if (params.has("import")) setParams({}, { replace: true });
  }, [params, setParams]);

  const officeNames = useMemo(() => new Map((offices.data ?? []).map((office) => [office.id, office.name])), [offices.data]);
  const agents = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (senders.data ?? []).filter(
      (agent) =>
        !needle ||
        agent.display_name.toLowerCase().includes(needle) ||
        agent.email.toLowerCase().includes(needle) ||
        (officeNames.get(agent.office_id) ?? "").toLowerCase().includes(needle),
    );
  }, [senders.data, search, officeNames]);
  const byLine = (line: ServiceLine) => agents.filter((agent) => agent.service_line === line);
  const offered = useOfferedLines();
  const general = byLine("any");

  return (
    <Page>
      <PageHeader
        eyebrow={<><Icon name="agents" /> {senders.data?.length ?? 0} agents</>}
        title="Agents"
        description="Agents for every service line (air, ocean, road, rail and more) with their branded signatures. Emails rotate between the agents of the right line; a contact keeps the same agent for the whole thread."
        actions={
          <>
            <input className="input" style={{ width: 220 }} placeholder="Find an agent or office…" value={search} onChange={(event) => setSearch(event.target.value)} aria-label="Find an agent" />
            {admin && (
              <>
                <Button onClick={() => setAdding(true)}>
                  <Icon name="plus" /> Add agent
                </Button>
                <Button variant="primary" onClick={() => setImporting(true)}>
                  <Icon name="upload" /> Import CSV
                </Button>
              </>
            )}
          </>
        }
      />
      {senders.isLoading && <SkeletonRows rows={4} />}
      <ErrorAlert error={senders.error} />
      {senders.data && (
        <div className="stack loose">
          <div className="board">
            {SERVICE_LINES.filter((line) => offered.includes(line) || byLine(line).length > 0).map((line) => (
              <AgentColumn key={line} line={line} title={SERVICE_LINE_LABELS[line]} agents={byLine(line)} officeNames={officeNames} onOpen={setOpen} />
            ))}
          </div>
          {general.length > 0 && (
            <AgentColumn line="any" title="Not assigned to a line" agents={general} officeNames={officeNames} onOpen={setOpen} />
          )}
        </div>
      )}
      {importing && <ImportModal onClose={() => setImporting(false)} />}
      {adding && <AddAgentModal onClose={() => setAdding(false)} />}
      {open && <AgentDrawer agent={open} officeName={officeNames.get(open.office_id)} canEdit={admin} onClose={() => setOpen(null)} />}
    </Page>
  );
}

function AgentColumn({
  line,
  title,
  agents,
  officeNames,
  onOpen,
}: {
  line: ServiceLine;
  title: string;
  agents: Agent[];
  officeNames: Map<string, string>;
  onOpen: (agent: Agent) => void;
}) {
  const health = useMailboxHealth();
  const healthById = new Map((health.data ?? []).map((row) => [row.sender_id, row]));
  const active = agents.filter((agent) => agent.is_active && !agent.flagged_at);
  const capacity = active.reduce((sum, agent) => sum + agent.daily_cap, 0);
  return (
    <section className={`board-col ${line}`}>
      <div className="board-head">
        <h2>
          {line !== "any" && <Icon name={LINE_ICONS[line]} />}
          {title}
        </h2>
        <span className="small muted">
          {active.length} active · {capacity} emails/day
        </span>
      </div>
      {agents.length === 0 ? (
        <Empty title="No agents yet">Import the CSV or add an agent with this line.</Empty>
      ) : (
        <div className="agent-grid">
          {agents.map((agent) => (
            <button key={agent.id} type="button" className={agent.is_active ? "agent-card" : "agent-card off"} onClick={() => onOpen(agent)}>
              <div className="row nowrap" style={{ gap: 10 }}>
                <span className={`avatar ${line}`}>{initials(agent.display_name)}</span>
                <div style={{ minWidth: 0 }}>
                  <strong className="truncate" style={{ display: "block" }}>{agent.display_name}</strong>
                  <span className="small muted truncate" style={{ display: "block" }}>{agent.job_title ?? "No title yet"}</span>
                </div>
              </div>
              <div className="small muted truncate">{agent.email}</div>
              <div className="row" style={{ gap: 6 }}>
                <Badge>{officeNames.get(agent.office_id) ?? "Office"}</Badge>
                <Badge tone="blue">{agent.daily_cap}/day</Badge>
                <HealthPill health={healthById.get(agent.id)} />
                {agent.mailbox_status && <MailboxStatusPill status={agent.mailbox_status} />}
                {agent.flagged_at && <Badge tone="red">flagged</Badge>}
                {!agent.is_active && <Badge>inactive</Badge>}
              </div>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function AgentDrawer({ agent, officeName, canEdit, onClose }: { agent: Agent; officeName?: string; canEdit: boolean; onClose: () => void }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [line, setLine] = useState<ServiceLine>(agent.service_line);
  const [title, setTitle] = useState(agent.job_title ?? "");
  const [phone, setPhone] = useState(agent.phone ?? "");
  const [mobile, setMobile] = useState(agent.mobile ?? "");
  const [picture, setPicture] = useState<File | null>(null);
  const [cap, setCap] = useState(agent.daily_cap);
  const [confirmDialog, ask] = useConfirm();
  const adminPermission = usePermission("admin:manage", "admins");
  const removeAgent = useDelete({
    run: () => unwrap(api.DELETE("/api/v1/senders/{sender_id}", { params: { path: { sender_id: agent.id } } })),
    success: `${agent.display_name} deleted.`,
    invalidate: [keys.senders],
    onDone: onClose,
  });
  const [active, setActive] = useState(agent.is_active);
  const [mode, setMode] = useState<Schemas["SignatureMode"]>(agent.signature_mode);
  const [html, setHtml] = useState(agent.signature_html ?? "");
  const signature = useQuery({
    queryKey: [...keys.senders, agent.id, "signature", agent.updated_at],
    queryFn: () => unwrap(api.GET("/api/v1/senders/{sender_id}/signature", { params: { path: { sender_id: agent.id } } })),
  });
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PATCH("/api/v1/senders/{sender_id}", {
          params: { path: { sender_id: agent.id } },
          body: {
            service_line: line,
            job_title: title.trim() || null,
            phone: phone.trim() || null,
            mobile: mobile.trim() || null,
            daily_cap: cap,
            is_active: active,
            // Image mode needs a picture; without one the agent keeps the generated signature.
            signature_mode: mode === "image" && !agent.signature_asset_id ? agent.signature_mode : mode,
            signature_html: mode === "custom_html" ? html : agent.signature_html,
          },
        }),
      ),
    onSuccess: () => {
      toast(`${agent.display_name} saved.`);
      void queryClient.invalidateQueries({ queryKey: keys.senders });
      onClose();
    },
  });
  const uploadPicture = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return unwrap(api.POST("/api/v1/senders/{sender_id}/signature-image", { params: { path: { sender_id: agent.id } }, body: form as never }));
    },
    onSuccess: () => {
      toast("Signature picture saved: this agent now signs with it.");
      setPicture(null);
      setMode("image");
      void queryClient.invalidateQueries({ queryKey: keys.senders });
      void queryClient.invalidateQueries({ queryKey: [...keys.senders, agent.id, "signature"] });
    },
  });
  const clearFlag = useMutation({
    mutationFn: () => unwrap(api.PATCH("/api/v1/senders/{sender_id}", { params: { path: { sender_id: agent.id } }, body: { clear_flag: true } })),
    onSuccess: () => {
      toast("Flag cleared: the mailbox sends again.");
      void queryClient.invalidateQueries({ queryKey: keys.senders });
      onClose();
    },
  });

  return (
    <Drawer
      wide
      title={
        <span className="row">
          <span className={`avatar ${agent.service_line}`} style={{ width: 30, height: 30, borderRadius: 9, fontSize: 12 }}>{initials(agent.display_name)}</span>
          {agent.display_name} <LinePill line={agent.service_line} />
        </span>
      }
      onClose={onClose}
      footer={
        <>
          <Button
            variant="danger"
            className={canEdit ? undefined : "locked"}
            busy={removeAgent.isPending}
            style={{ marginRight: "auto" }}
            title={canEdit ? `Delete ${agent.display_name}` : adminPermission.reason}
            onClick={() =>
              canEdit
                ? void ask({
                    title: `Delete ${agent.display_name}?`,
                    message: "Only agents who never sent an email can be deleted. An agent with history is switched off instead, so their threads and reports keep their name.",
                  }).then((ok) => ok && removeAgent.mutate())
                : adminPermission.explain()
            }
          >
            <Icon name="trash" /> Delete
          </Button>
          {confirmDialog}
          {!canEdit && <Button onClick={onClose}>Close</Button>}
          {canEdit && (
          <>
            {agent.flagged_at && (
              <Button busy={clearFlag.isPending} onClick={() => clearFlag.mutate()}>
                Clear flag
              </Button>
            )}
            <Button onClick={onClose}>Cancel</Button>
            <Button variant="primary" busy={save.isPending} onClick={() => save.mutate()}>
              Save agent
            </Button>
          </>
          )}
        </>
      }
    >
      <div className="grid stack-narrow" style={{ gridTemplateColumns: "minmax(0, 1.5fr) minmax(260px, 1fr)", alignItems: "start" }}>
        <div className="stack">
          <h3>Signature, as recipients see it</h3>
          {signature.isLoading && <Loading />}
          <ErrorAlert error={signature.error} />
          {signature.data && <EmailFrame html={signature.data.html} minHeight={180} />}
          {agent.flagged_at && (
            <div className="alert error small">
              Flagged {agent.flag_reason ? `(${agent.flag_reason})` : ""}: this mailbox does not send until the flag is cleared.
            </div>
          )}
          <MailboxHealthPanel senderId={agent.id} />
          <AgentMailboxPanel senderId={agent.id} agentEmail={agent.email} canEdit={canEdit} />
          <p className="small muted" style={{ margin: 0 }}>
            The generated signature uses the tenant branding (Settings → Branding) and brand images in the library (<code>Brand/logo.png</code>, <code>Brand/badge-*.png</code>).
          </p>
        </div>
        <div className="stack">
          {!canEdit && <div className="alert info small">{adminPermission.reason.replace("do this", "edit or delete agents")}</div>}
          <div className="small muted">
            {agent.email} · {officeName ?? "Office"}
          </div>
          <label className="field">
            <span>Service line</span>
            <select className="select" disabled={!canEdit} value={line} onChange={(event) => setLine(event.target.value as ServiceLine)}>
              <ServiceLineOptions any="Any (every line)" current={line} />
            </select>
          </label>
          <label className="field">
            <span>Job title</span>
            <input className="input" disabled={!canEdit} value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Marketing Executive" />
          </label>
          <label className="field">
            <span>Phone</span>
            <input className="input" disabled={!canEdit} value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="905-677-5000 Ex 332" />
            <span className="field-help">Add the extension after "Ex"; it is dialled after a pause.</span>
          </label>
          <label className="field">
            <span>Mobile / WhatsApp</span>
            <input className="input" disabled={!canEdit} value={mobile} onChange={(event) => setMobile(event.target.value)} placeholder="647-295-8454" />
          </label>
          <label className="field">
            <span>Daily cap</span>
            <input className="input" type="number" min={1} max={1000} disabled={!canEdit} value={cap} onChange={(event) => setCap(Number(event.target.value) || 1)} />
          </label>
          <label className="checkbox">
            <Switch checked={active} onChange={setActive} label="Active" disabled={!canEdit} /> Active (sends emails)
          </label>
          <fieldset>
            <legend>Signature</legend>
            <div className="stack tight">
              {SIGNATURE_MODES.map((option) => (
                <label key={option.id} className="checkbox" style={{ alignItems: "flex-start" }}>
                  <input type="radio" name="signature-mode" disabled={!canEdit} checked={mode === option.id} onChange={() => setMode(option.id)} />
                  <span>
                    {option.label}
                    <span className="field-help" style={{ display: "block" }}>{option.help}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          {mode === "image" && canEdit && (
            <div className="stack tight">
              <ImageDrop file={picture} onFile={setPicture} />
              {picture && (
                <Button variant="primary" size="sm" busy={uploadPicture.isPending} onClick={() => uploadPicture.mutate(picture)}>
                  <Icon name="upload" /> Use this picture
                </Button>
              )}
              {!agent.signature_asset_id && !picture && <span className="field-help">Upload a picture to sign with it (for example a designed signature exported as PNG).</span>}
              <ErrorAlert error={uploadPicture.error} />
            </div>
          )}
          {mode === "custom_html" && (
            <label className="field">
              <span>Signature HTML</span>
              <textarea className="textarea code" disabled={!canEdit} value={html} onChange={(event) => setHtml(event.target.value)} />
            </label>
          )}
          <ErrorAlert error={save.error ?? clearFlag.error} />
        </div>
      </div>
    </Drawer>
  );
}

const CSV_EXAMPLE = `name,email,office,service_line,title,phone,mobile,daily_cap
Molly Example,molly@forwarder.example,USA/Canada,air,Marketing Executive,905-677-5000 Ex 331,647-295-8454,50
Oscar Example,oscar@forwarder.example,USA/Canada,ocean,Ocean Sales,905-677-5000 Ex 332,,50`;

function ImportModal({ onClose }: { onClose: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const run = useMutation({
    mutationFn: () => {
      const form = new FormData();
      if (file) form.append("file", file);
      return unwrap(api.POST("/api/v1/senders/import", { body: form as never }));
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: keys.senders }),
  });
  const template = `data:text/csv;charset=utf-8,${encodeURIComponent(CSV_EXAMPLE)}`;
  return (
    <Modal
      title="Import agents from CSV"
      onClose={onClose}
      footer={
        run.data ? (
          <Button variant="primary" onClick={onClose}>Done</Button>
        ) : (
          <>
            <Button onClick={onClose}>Cancel</Button>
            <Button variant="primary" disabled={!file} busy={run.isPending} onClick={() => run.mutate()}>
              <Icon name="upload" /> Import
            </Button>
          </>
        )
      }
    >
      <div className="stack">
        <p className="small muted" style={{ margin: 0 }}>
          One row per agent. Existing agents (same email) are updated; offices are matched by name. Columns:
          <code> name, email, office, service_line</code> (air/ocean) and optionally <code>title, phone, mobile, daily_cap</code>.
        </p>
        <div
          className="dropzone"
          role="button"
          tabIndex={0}
          onClick={() => input.current?.click()}
          onKeyDown={(event) => event.key === "Enter" && input.current?.click()}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            const dropped = event.dataTransfer.files[0];
            if (dropped) setFile(dropped);
          }}
        >
          <Icon name={file ? "file" : "upload"} />
          <div>{file ? <strong>{file.name}</strong> : <><strong>Drop the CSV</strong> or click to choose</>}</div>
          <input ref={input} type="file" accept=".csv,text/csv" hidden onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
        </div>
        <a className="small" href={template} download="agents-template.csv">
          Download a template
        </a>
        <ErrorAlert error={run.error} />
        {run.data && (
          <div className={run.data.errors.length ? "alert warning" : "alert success"}>
            {run.data.created} created, {run.data.updated} updated.
            {run.data.errors.length > 0 && (
              <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                {run.data.errors.map((error, index) => (
                  <li key={index}>{String(error)}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}

function AddAgentModal({ onClose }: { onClose: () => void }) {
  const offices = useOffices();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [office, setOffice] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [line, setLine] = useState<ServiceLine>("air");
  const [title, setTitle] = useState("");
  const [phone, setPhone] = useState("");
  const [mobile, setMobile] = useState("");
  const [cap, setCap] = useState(50);
  const officeId = office || offices.data?.[0]?.id || "";
  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/senders", {
          body: {
            office_id: officeId,
            display_name: name.trim(),
            email: email.trim(),
            service_line: line,
            job_title: title.trim() || null,
            phone: phone.trim() || null,
            mobile: mobile.trim() || null,
            daily_cap: cap,
          },
        }),
      ),
    onSuccess: (agent) => {
      toast(`${agent.display_name} added.`);
      void queryClient.invalidateQueries({ queryKey: keys.senders });
      onClose();
    },
  });
  return (
    <Modal
      title="Add an agent"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={create.isPending} disabled={!officeId || !name.trim() || !email.trim()} onClick={() => create.mutate()}>
            Add agent
          </Button>
        </>
      }
    >
      <div className="form-grid">
        <label className="field">
          <span>Office</span>
          <select className="select" value={officeId} onChange={(event) => setOffice(event.target.value)}>
            {(offices.data ?? []).map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Service line</span>
          <select className="select" value={line} onChange={(event) => setLine(event.target.value as ServiceLine)}>
            <ServiceLineOptions any="Any" current={line} />
          </select>
        </label>
        <label className="field">
          <span>Name</span>
          <input className="input" value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        <label className="field">
          <span>Email (mailbox)</span>
          <input className="input" type="email" value={email} onChange={(event) => setEmail(event.target.value)} />
        </label>
        <label className="field">
          <span>Job title</span>
          <input className="input" value={title} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <label className="field">
          <span>Phone</span>
          <input className="input" value={phone} onChange={(event) => setPhone(event.target.value)} />
        </label>
        <label className="field">
          <span>Mobile / WhatsApp</span>
          <input className="input" value={mobile} onChange={(event) => setMobile(event.target.value)} />
        </label>
        <label className="field">
          <span>Daily cap</span>
          <input className="input" type="number" min={1} value={cap} onChange={(event) => setCap(Number(event.target.value) || 1)} />
        </label>
      </div>
      <div style={{ marginTop: 12 }}>
        <ErrorAlert error={create.error} />
      </div>
    </Modal>
  );
}

function MailboxHealthPanel({ senderId }: { senderId: string }) {
  const health = useMailboxHealth();
  const row = health.data?.find((item) => item.sender_id === senderId);
  if (!row) return null;
  return (
    <div className="card" style={{ padding: 14 }}>
      <div className="row between">
        <h3 style={{ margin: 0 }}>Mailbox health</h3>
        <HealthPill health={row} />
      </div>
      <div className="grid four" style={{ marginTop: 10, gap: 10 }}>
        <div><span className="small muted">Sent 30 d</span><strong style={{ display: "block" }}>{row.sent_30d}</strong></div>
        <div><span className="small muted">Bounces</span><strong style={{ display: "block" }}>{percent(row.bounce_rate)}</strong></div>
        <div><span className="small muted">Opens</span><strong style={{ display: "block" }}>{row.tracked_30d ? percent(row.open_rate) : "—"}</strong></div>
        <div><span className="small muted">Replies</span><strong style={{ display: "block" }}>{percent(row.reply_rate)}</strong></div>
      </div>
      <div className="small muted" style={{ marginTop: 8 }}>
        Today {row.used_today}/{row.daily_cap} · {row.complaints_30d} complaint(s) in 30 days
      </div>
      {row.reasons.length > 0 && (
        <ul className="small" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
          {row.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
