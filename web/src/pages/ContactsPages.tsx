/**
 * Contacts: search, create, tag (tags start workflows), enroll, simulate replies, and the
 * full email history with what the reply pipeline decided.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, describeError, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { LoadMore, usePaged } from "../api/paging";
import { keys, useOffices, usePublishedWorkflows } from "../api/queries";
import { useSession } from "../auth/session";
import { useDebounced } from "../components/ContactPicker";
import { Icon } from "../components/icons";
import { ContactCrmCard } from "../crm/ContactCrmCard";
import { CountrySelect, LanguageSelect, TimeZoneSelect } from "../components/pickers";
import { Page } from "../components/Layout";
import {
  Badge,
  BucketBadge,
  Button,
  Card,
  Empty,
  ErrorAlert,
  KeyValues,
  Loading,
  Modal,
  PageHeader,
  StatusBadge,
  useErrorToast,
  useToast,
} from "../components/ui";
import { formatDateTime, formatRelative, humanize, shortId } from "../lib/format";
import { countryFlag } from "../lib/freight";
import { useConfirm, useDelete } from "../components/confirm";
import { loadSavedViews, saveViews } from "../lib/savedViews";
import type { SavedView } from "../lib/savedViews";

const STAGES = ["new", "engaged", "qualified", "customer", "partner", "not_interested", "do_not_contact"];

export function ContactsPage() {
  const [search, setSearch] = useState("");
  const [tag, setTag] = useState("");
  const [stage, setStage] = useState("");
  const [listParams] = useSearchParams();
  const [creating, setCreating] = useState(listParams.get("new") === "1");
  const [importing, setImporting] = useState(false);
  const q = useDebounced(search);
  const navigate = useNavigate();
  const { can } = useSession();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkTag, setBulkTag] = useState("");
  const [views, setViews] = useState<SavedView[]>(() => loadSavedViews("contacts"));
  const [confirmDialog, ask] = useConfirm();
  const toggle = (id: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const refreshContacts = () => void queryClient.invalidateQueries({ queryKey: keys.contacts });
  const bulk = useMutation({
    mutationFn: async (action: "tag" | "verify" | "erase") => {
      const ids = [...selected];
      if (action === "verify") {
        const result = await unwrap(api.POST("/api/v1/contacts/verify-emails", { body: { contact_ids: ids } }));
        return `Checked ${result.checked}: ${Object.entries(result.by_status).map(([status, count]) => `${count} ${status.replace("_", " ")}`).join(", ")}.`;
      }
      for (const id of ids) {
        if (action === "tag") {
          await unwrap(api.POST("/api/v1/contacts/{contact_id}/tags", { params: { path: { contact_id: id } }, body: { tag: bulkTag.trim() } }));
        } else {
          await unwrap(api.DELETE("/api/v1/contacts/{contact_id}", { params: { path: { contact_id: id } } }));
        }
      }
      return action === "tag" ? `Tagged ${ids.length} contacts “${bulkTag.trim()}”.` : `Erased ${ids.length} contacts.`;
    },
    onSuccess: (message) => {
      toast(message);
      setSelected(new Set());
      setBulkTag("");
      refreshContacts();
    },
    onError: (error) => {
      toast(describeError(error), "error");
      refreshContacts();
    },
  });
  const applyView = (view: SavedView) => {
    setSearch(view.search);
    setTag(view.tag);
    setStage(view.stage);
  };
  const saveView = () => {
    const name = window.prompt("Name this view", tag || stage || search || "My view")?.trim();
    if (!name) return;
    const next = [...views.filter((view) => view.name !== name), { name, search, tag, stage }];
    setViews(next);
    saveViews("contacts", next);
  };
  const removeView = (name: string) => {
    const next = views.filter((view) => view.name !== name);
    setViews(next);
    saveViews("contacts", next);
  };
  const page = usePaged([...keys.contacts, "list", q, tag, stage], (cursor) =>
    unwrap(
      api.GET("/api/v1/contacts", {
        params: {
          query: { q: q || undefined, tag: tag || undefined, stage: (stage || undefined) as never, cursor, limit: 50 },
        },
      }),
    ),
  );
  return (
    <Page>
      <PageHeader
        title="Contacts"
        description="Adding a tag publishes an event: workflows with a matching tag trigger start for the contact."
        actions={
          can("workflows:write") && (
            <>
              <Button onClick={() => setImporting(true)}>
                <Icon name="upload" /> Import CSV
              </Button>
              <Button variant="primary" onClick={() => setCreating(true)}>
                New contact
              </Button>
            </>
          )
        }
      />
      <div className="row" style={{ marginBottom: 12 }}>
        <input className="input" style={{ maxWidth: 320 }} placeholder="Search name, email, domain…" value={search} onChange={(event) => setSearch(event.target.value)} />
        <input className="input" style={{ maxWidth: 160 }} placeholder="Tag" value={tag} onChange={(event) => setTag(event.target.value.trim())} />
        <select className="select" style={{ maxWidth: 180 }} value={stage} onChange={(event) => setStage(event.target.value)}>
          <option value="">All stages</option>
          {STAGES.map((value) => (
            <option key={value} value={value}>
              {humanize(value)}
            </option>
          ))}
        </select>
        <Button size="sm" variant="ghost" onClick={saveView} disabled={!search && !tag && !stage} title="Save these filters as a view">
          <Icon name="filter" /> Save view
        </Button>
      </div>
      {views.length > 0 && (
        <div className="chips" style={{ marginBottom: 12 }}>
          {views.map((view) => (
            <span key={view.name} className="saved-view">
              <button type="button" className="chip-toggle" onClick={() => applyView(view)}>{view.name}</button>
              <button type="button" className="icon-button" aria-label={`Remove view ${view.name}`} onClick={() => removeView(view.name)}>
                <Icon name="x" />
              </button>
            </span>
          ))}
        </div>
      )}
      {selected.size > 0 && (
        <div className="bulk-bar" role="region" aria-label="Bulk actions">
          <strong>{selected.size} selected</strong>
          {can("workflows:write") && (
            <>
              <input className="input" style={{ maxWidth: 180 }} placeholder="Tag to add" value={bulkTag} onChange={(event) => setBulkTag(event.target.value)} />
              <Button size="sm" busy={bulk.isPending} disabled={!bulkTag.trim()} onClick={() => bulk.mutate("tag")}>
                <Icon name="tag" /> Add tag
              </Button>
              <Button size="sm" busy={bulk.isPending} onClick={() => bulk.mutate("verify")}>
                <Icon name="shield" /> Verify emails
              </Button>
            </>
          )}
          {can("admin:manage") && (
            <Button
              size="sm"
              variant="danger"
              busy={bulk.isPending}
              onClick={() =>
                void ask({
                  title: `Erase ${selected.size} contacts?`,
                  message: "Their emails, enrollments, quote requests, deals and meetings are deleted for good. Their addresses stay suppressed so they are never emailed again.",
                  confirmLabel: "Erase",
                  typeToConfirm: "erase",
                }).then((ok) => ok && bulk.mutate("erase"))
              }
            >
              <Icon name="trash" /> Erase
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>Clear</Button>
        </div>
      )}
      <Card flush>
        {page.isLoading && <Loading />}
        <ErrorAlert error={page.error} />
        {!page.isLoading && page.items.length === 0 && (
          <Empty title="No contacts match" art="parcel">
            Import a CSV, add a website form or change the filters.
          </Empty>
        )}
        {page.items.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: 36 }}>
                    <input
                      type="checkbox"
                      aria-label="Select all on this page"
                      checked={page.items.length > 0 && page.items.every((contact) => selected.has(contact.id))}
                      onChange={(event) =>
                        setSelected(event.target.checked ? new Set(page.items.map((contact) => contact.id)) : new Set())
                      }
                    />
                  </th>
                  <th>Name</th>
                  <th>Email</th>
                  <th>Country</th>
                  <th>Stage</th>
                  <th>Tags</th>
                  <th>Added</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((contact) => (
                  <tr key={contact.id} className={selected.has(contact.id) ? "clickable selected" : "clickable"} onClick={() => navigate(`/contacts/${contact.id}`)}>
                    <td onClick={(event) => event.stopPropagation()}>
                      <input type="checkbox" aria-label={`Select ${contact.display_name}`} checked={selected.has(contact.id)} onChange={() => toggle(contact.id)} />
                    </td>
                    <td>
                      <strong>{contact.display_name}</strong>
                      {contact.job_title && <div className="small muted">{contact.job_title}</div>}
                    </td>
                    <td className="small">
                      {contact.email} <EmailStatusBadge status={contact.email_status ?? "unknown"} />
                    </td>
                    <td className="nowrap">{contact.country ? `${countryFlag(contact.country)} ${contact.country}` : "—"}</td>
                    <td><StatusBadge value={contact.stage} /></td>
                    <td>
                      <div className="chips">
                        {contact.tags.map((value) => (
                          <span key={value} className="tag">{value}</span>
                        ))}
                      </div>
                    </td>
                    <td className="small muted">{formatRelative(contact.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <LoadMore {...page} />
      </Card>
      {creating && <ContactFormModal onClose={() => setCreating(false)} />}
      {importing && <ContactImportModal onClose={() => setImporting(false)} />}
      {confirmDialog}
    </Page>
  );
}

function ContactFormModal({ onClose, contact }: { onClose: () => void; contact?: Schemas["ContactResponse"] }) {
  const offices = useOffices();
  const companies = useQuery({
    queryKey: [...keys.companies, "picker"],
    queryFn: () => unwrap(api.GET("/api/v1/companies", { params: { query: { limit: 200 } } })),
  });
  const [form, setForm] = useState({
    email: contact?.email ?? "",
    first_name: contact?.first_name ?? "",
    last_name: contact?.last_name ?? "",
    job_title: contact?.job_title ?? "",
    country: contact?.country ?? "",
    city: contact?.city ?? "",
    language: contact?.language ?? "",
    timezone: contact?.timezone ?? "",
    company_id: contact?.company_id ?? "",
    office_id: contact?.office_id ?? "",
    stage: contact?.stage ?? "new",
    tags: "",
  });
  const toast = useToast();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const set = (name: keyof typeof form) => (event: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [name]: event.target.value }));
  const optional = (value: string) => value.trim() || null;
  const save = useMutation({
    mutationFn: () => {
      const common = {
        first_name: optional(form.first_name),
        last_name: optional(form.last_name),
        job_title: optional(form.job_title),
        country: optional(form.country.toUpperCase()),
        city: optional(form.city),
        language: optional(form.language.toLowerCase()),
        timezone: optional(form.timezone),
        company_id: optional(form.company_id),
        office_id: optional(form.office_id),
        stage: form.stage as Schemas["ContactStage"],
      };
      if (contact) {
        return unwrap(api.PATCH("/api/v1/contacts/{contact_id}", { params: { path: { contact_id: contact.id } }, body: common }));
      }
      return unwrap(
        api.POST("/api/v1/contacts", {
          body: {
            ...common,
            email: form.email,
            tags: form.tags.split(",").map((value) => value.trim()).filter(Boolean),
          },
        }),
      );
    },
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: keys.contacts });
      toast(contact ? "Contact updated." : "Contact created.");
      onClose();
      if (!contact) navigate(`/contacts/${saved.id}`);
    },
  });
  return (
    <Modal
      title={contact ? "Edit contact" : "New contact"}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={save.isPending} disabled={!form.email} onClick={() => save.mutate()}>
            Save
          </Button>
        </>
      }
    >
      <div className="stack">
        <div className="form-grid">
          <label className="field">
            <span>Email *</span>
            <input className="input" value={form.email} disabled={Boolean(contact)} onChange={set("email")} />
          </label>
          <label className="field">
            <span>First name</span>
            <input className="input" value={form.first_name} onChange={set("first_name")} />
          </label>
          <label className="field">
            <span>Last name</span>
            <input className="input" value={form.last_name} onChange={set("last_name")} />
          </label>
          <label className="field">
            <span>Job title</span>
            <input className="input" value={form.job_title} onChange={set("job_title")} />
          </label>
          <label className="field">
            <span>Country</span>
            <CountrySelect value={form.country} onChange={(value) => setForm((current) => ({ ...current, country: value }))} placeholder="Not set" />
          </label>
          <label className="field">
            <span>City</span>
            <input className="input" value={form.city} onChange={set("city")} />
          </label>
          <label className="field">
            <span>Language</span>
            <LanguageSelect value={form.language} onChange={(value) => setForm((current) => ({ ...current, language: value }))} emptyLabel="Not set (office default)" />
          </label>
          <label className="field">
            <span>Timezone</span>
            <TimeZoneSelect value={form.timezone} onChange={(value) => setForm((current) => ({ ...current, timezone: value }))} emptyLabel="The country's time zone" />
          </label>
          <label className="field">
            <span>Company</span>
            <select className="select" value={form.company_id} onChange={set("company_id")}>
              <option value="">—</option>
              {(companies.data?.items ?? []).map((company) => (
                <option key={company.id} value={company.id}>
                  {company.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Office</span>
            <select className="select" value={form.office_id} onChange={set("office_id")}>
              <option value="">—</option>
              {(offices.data ?? []).map((office) => (
                <option key={office.id} value={office.id}>
                  {office.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Stage</span>
            <select className="select" value={form.stage} onChange={set("stage")}>
              {STAGES.map((value) => (
                <option key={value} value={value}>
                  {humanize(value)}
                </option>
              ))}
            </select>
          </label>
          {!contact && (
            <label className="field">
              <span>Tags</span>
              <input className="input" value={form.tags} placeholder="intro, uae-importers" onChange={set("tags")} />
              <span className="field-help">Tags can start workflows right away.</span>
            </label>
          )}
        </div>
        <ErrorAlert error={save.error} />
      </div>
    </Modal>
  );
}

export function ContactDetailPage() {
  const { contactId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  const onError = useErrorToast();
  const { can } = useSession();
  const [editing, setEditing] = useState(false);
  const [enrolling, setEnrolling] = useState(false);
  const [simulating, setSimulating] = useState(false);
  const [newTag, setNewTag] = useState("");
  const contact = useQuery({
    queryKey: [...keys.contacts, contactId],
    queryFn: () => unwrap(api.GET("/api/v1/contacts/{contact_id}", { params: { path: { contact_id: contactId } } })),
  });
  const messages = useQuery({
    queryKey: [...keys.contacts, contactId, "messages"],
    queryFn: () =>
      unwrap(api.GET("/api/v1/contacts/{contact_id}/messages", { params: { path: { contact_id: contactId } } })),
    refetchInterval: 15_000,
  });
  const enrollments = useQuery({
    queryKey: [...keys.enrollments, "contact", contactId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/automation/enrollments", { params: { query: { contact_id: contactId, limit: 50 } } })),
    refetchInterval: 15_000,
  });
  const offices = useOffices();
  const company = useQuery({
    queryKey: [...keys.companies, contact.data?.company_id],
    enabled: Boolean(contact.data?.company_id),
    queryFn: () =>
      unwrap(api.GET("/api/v1/companies/{company_id}", { params: { path: { company_id: contact.data!.company_id! } } })),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: keys.contacts });
    void queryClient.invalidateQueries({ queryKey: keys.enrollments });
  };
  const addTag = useMutation({
    mutationFn: (tag: string) =>
      unwrap(api.POST("/api/v1/contacts/{contact_id}/tags", { params: { path: { contact_id: contactId } }, body: { tag } })),
    onSuccess: (_, tag) => {
      setNewTag("");
      toast(`Tagged “${tag}”. Matching workflows start for this contact.`);
      refresh();
    },
    onError,
  });
  const removeTag = useMutation({
    mutationFn: (tag: string) =>
      unwrap(api.DELETE("/api/v1/contacts/{contact_id}/tags/{tag}", { params: { path: { contact_id: contactId, tag } } })),
    onSuccess: refresh,
    onError,
  });

  const [eraseDialog, askErase] = useConfirm();
  const erase = useDelete({
    run: () => unwrap(api.DELETE("/api/v1/contacts/{contact_id}", { params: { path: { contact_id: contactId } } })),
    success: "Contact erased. The address stays suppressed.",
    invalidate: [keys.contacts],
    onDone: () => navigate("/contacts"),
  });
  const verify = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/contacts/{contact_id}/verify-email", { params: { path: { contact_id: contactId } } })),
    onSuccess: (result) => {
      toast(`${result.email}: ${result.email_status.replace("_", " ")} (${result.reason}).`, result.email_status === "invalid" ? "error" : undefined);
      refresh();
    },
    onError,
  });

  if (contact.isLoading) return <Loading />;
  if (!contact.data) return <Page><ErrorAlert error={contact.error} /></Page>;
  const data = contact.data;
  const office = offices.data?.find((candidate) => candidate.id === data.office_id);

  return (
    <Page>
      <PageHeader
        title={
          <span className="row">
            {data.display_name} <StatusBadge value={data.stage} /> <EmailStatusBadge status={data.email_status ?? "unknown"} />
          </span>
        }
        description={`${data.email}${data.job_title ? ` · ${data.job_title}` : ""}`}
        actions={
          <>
            {can("admin:manage") && (
              <Button
                variant="danger"
                busy={erase.isPending}
                onClick={() =>
                  void askErase({
                    title: `Erase ${data.display_name}?`,
                    message: "Deletes the contact with their emails, enrollments, quote requests, deals, meetings, notes and every copy of their data. The address stays suppressed so a later import never emails them. This cannot be undone.",
                    confirmLabel: "Erase contact",
                    typeToConfirm: data.email,
                  }).then((ok) => ok && erase.mutate())
                }
              >
                <Icon name="trash" /> Erase
              </Button>
            )}
            {eraseDialog}
            {can("workflows:write") && (
              <Button busy={verify.isPending} onClick={() => verify.mutate()} title="Check that the address can receive email">
                Verify email
              </Button>
            )}
            {can("events:write") && (
              <Button onClick={() => setSimulating(true)} title="Dev/staging: store a reply, bounce or complaint and run the pipeline">
                Simulate reply
              </Button>
            )}
            {can("enrollments:write") && (
              <Button onClick={() => setEnrolling(true)}>Enroll in workflow</Button>
            )}
            {can("workflows:write") && (
              <Button variant="primary" onClick={() => setEditing(true)}>
                Edit
              </Button>
            )}
          </>
        }
      />
      <div className="grid two" style={{ alignItems: "start" }}>
        <div className="stack">
          <Card title="Details">
            <KeyValues
              items={[
                ["Company", company.data ? <Link to={`/companies/${company.data.id}`}>{company.data.name}</Link> : null],
                ["Office", office?.name],
                ["Country", data.country],
                ["City", data.city],
                ["Language", data.language],
                ["Timezone", data.timezone],
                ["Owner", data.owner_email],
                ["Added", formatDateTime(data.created_at)],
              ]}
            />
          </Card>
          <ContactCrmCard contactId={data.id} />
          <Card title="Tags">
            <div className="stack">
              <div className="chips">
                {data.tags.length === 0 && <span className="muted small">No tags</span>}
                {data.tags.map((tag) => (
                  <span key={tag} className="tag">
                    {tag}
                    {can("workflows:write") && (
                      <button type="button" className="btn ghost sm icon" aria-label={`Remove ${tag}`} onClick={() => removeTag.mutate(tag)}>
                        ✕
                      </button>
                    )}
                  </span>
                ))}
              </div>
              {can("workflows:write") && (
                <form
                  className="row"
                  onSubmit={(event) => {
                    event.preventDefault();
                    if (newTag.trim()) addTag.mutate(newTag.trim().toLowerCase());
                  }}
                >
                  <input className="input" style={{ maxWidth: 220 }} placeholder="Add a tag (e.g. intro)" value={newTag} onChange={(event) => setNewTag(event.target.value)} />
                  <Button type="submit" busy={addTag.isPending} disabled={!newTag.trim()}>
                    Add tag
                  </Button>
                </form>
              )}
            </div>
          </Card>
          <Card title="Enrollments" flush>
            {enrollments.data?.items.length === 0 && <Empty title="Not enrolled anywhere" />}
            <table className="table">
              <tbody>
                {enrollments.data?.items.map((enrollment) => (
                  <tr key={enrollment.id}>
                    <td>
                      <Link to={`/enrollments/${enrollment.id}`} className="mono small">{shortId(enrollment.id)}</Link>{" "}
                      <Link to={`/workflows/${enrollment.workflow_id}`} className="small">workflow</Link>
                    </td>
                    <td><StatusBadge value={enrollment.status} /></td>
                    <td className="small muted">{enrollment.stop_reason ?? enrollment.current_node_id ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
        <Card title="Email history">
          {messages.isLoading && <Loading />}
          {messages.data?.length === 0 && <Empty title="No emails yet" />}
          <div className="timeline">
            {messages.data?.map((message) => (
              <MessageItem key={message.id} message={message} />
            ))}
          </div>
        </Card>
      </div>
      {editing && <ContactFormModal contact={data} onClose={() => setEditing(false)} />}
      {enrolling && <EnrollModal contactId={data.id} onClose={() => setEnrolling(false)} />}
      {simulating && <SimulateReplyModal contact={data} onClose={() => setSimulating(false)} />}
    </Page>
  );
}

function MessageItem({ message }: { message: Schemas["MessageResponse"] }) {
  const inbound = message.direction === "inbound";
  const result = (message.processing_result ?? {}) as {
    classification?: { bucket?: string; confidence?: number; summary?: string };
    actions_taken?: string[];
    review_item_id?: string | null;
    lead_id?: string | null;
    source?: string;
  };
  return (
    <div className="timeline-item">
      <span className={`timeline-dot ${inbound ? "amber" : "blue"}`} />
      <div className="stack tight">
        <div className="row">
          <Badge tone={inbound ? "amber" : "blue"}>{inbound ? "received" : "sent"}</Badge>
          <Badge>{message.kind}</Badge>
          <span className="small muted">{formatDateTime(message.occurred_at)}</span>
        </div>
        <strong>{message.subject}</strong>
        <div className="small muted">
          {message.from_email} → {message.to_email}
        </div>
        {message.body && (
          <details>
            <summary className="small muted">Body</summary>
            <pre className="email-preview">{message.body}</pre>
          </details>
        )}
        {inbound && result.classification && (
          <div className="email-preview stack tight">
            <div className="row">
              <BucketBadge bucket={result.classification.bucket} />
              <span className="small muted">
                {Math.round((result.classification.confidence ?? 0) * 100)}% · {result.source}
              </span>
              {result.review_item_id && <Link to={`/reviews?item=${result.review_item_id}`} className="small">review item</Link>}
              {result.lead_id && <Link to="/leads" className="small">lead</Link>}
            </div>
            {result.classification.summary && <div className="small">{result.classification.summary}</div>}
            {result.actions_taken && result.actions_taken.length > 0 && (
              <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
                {result.actions_taken.map((action) => (
                  <li key={action}>{action}</li>
                ))}
              </ul>
            )}
          </div>
        )}
        {inbound && !message.processed_at && <span className="small muted">Waiting for the reply pipeline…</span>}
      </div>
    </div>
  );
}

function EnrollModal({ contactId, onClose }: { contactId: string; onClose: () => void }) {
  const workflows = usePublishedWorkflows();
  const [workflowId, setWorkflowId] = useState("");
  const queryClient = useQueryClient();
  const toast = useToast();
  const enroll = useMutation({
    mutationFn: (targetId: string) =>
      unwrap(
        api.POST("/api/v1/automation/workflows/{workflow_id}/enrollments", {
          params: { path: { workflow_id: targetId } },
          body: { contact_ids: [contactId] },
        }),
      ),
    onSuccess: ([attempt]) => {
      if (attempt?.enrolled) toast("Enrolled. The first step runs right away.");
      else toast(`Not enrolled: ${humanize(attempt?.skipped_reason ?? "unknown")}.`, "error");
      void queryClient.invalidateQueries({ queryKey: keys.enrollments });
      onClose();
    },
  });
  const selected = workflowId || workflows.data?.items[0]?.id || "";
  return (
    <Modal
      title="Enroll in a workflow"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            busy={enroll.isPending}
            disabled={!selected}
            onClick={() => enroll.mutate(selected)}
          >
            Enroll
          </Button>
        </>
      }
    >
      <div className="stack">
        <label className="field">
          <span>Published workflow</span>
          <select className="select" value={selected} onChange={(event) => setWorkflowId(event.target.value)}>
            {(workflows.data?.items ?? []).map((workflow) => (
              <option key={workflow.id} value={workflow.id}>
                {workflow.name} (v{workflow.current_version})
              </option>
            ))}
          </select>
        </label>
        <p className="small muted" style={{ margin: 0 }}>
          A contact can only be in a workflow once at a time. Guardrails still decide when each email may go.
        </p>
        <ErrorAlert error={enroll.error} />
      </div>
    </Modal>
  );
}

const SAMPLE_REPLIES: Record<string, string> = {
  "Rate request": "Hi, please quote 2x40HC from Chennai to Rotterdam, monthly, FOB. Cargo ready next week.",
  Interested: "Sounds good, tell me more about your services to the Gulf.",
  Meeting: "Could we schedule a call next week to discuss?",
  "Not interested": "No thanks, we are not interested at this time.",
  Later: "Busy right now, please contact me next quarter.",
  "Out of office": "I am out of the office until 12 October with limited access to email.",
  Unsubscribe: "Please unsubscribe me from your mailing list.",
  Complaint: "Stop spamming us or I will report you.",
  Referral: "I'm not the right person. Please contact my colleague sara.ops@example.com.",
};

function SimulateReplyModal({ contact, onClose }: { contact: Schemas["ContactResponse"]; onClose: () => void }) {
  const [type, setType] = useState<"reply" | "bounce" | "complaint">("reply");
  const [body, setBody] = useState(SAMPLE_REPLIES["Rate request"] ?? "");
  const [subject, setSubject] = useState("");
  const [bounceType, setBounceType] = useState<"hard" | "soft" | "blocked">("hard");
  const queryClient = useQueryClient();
  const toast = useToast();
  const simulate = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/test-tools/sending-events", {
          body: { type, contact_id: contact.id, body, subject: subject || undefined, bounce_type: bounceType },
        }),
      ),
    onSuccess: (result) => {
      const event = result.events[0];
      if (event?.skipped_reason) toast(`Not stored: ${event.skipped_reason}`, "error");
      else toast("Stored. The reply pipeline processes it now; results appear in the history.");
      window.setTimeout(() => {
        void queryClient.invalidateQueries();
      }, 1500);
      onClose();
    },
  });
  return (
    <Modal
      title={`Simulate a message from ${contact.display_name}`}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={simulate.isPending} onClick={() => simulate.mutate()}>
            Send to the pipeline
          </Button>
        </>
      }
    >
      <div className="stack">
        <p className="small muted" style={{ margin: 0 }}>
          Stored exactly like a provider webhook, in the latest thread with this contact, then
          classified (rules, then AI) and acted on by the reply policy. Dev and staging only.
        </p>
        <label className="field">
          <span>Type</span>
          <select className="select" value={type} onChange={(event) => setType(event.target.value as typeof type)}>
            <option value="reply">Reply</option>
            <option value="bounce">Bounce</option>
            <option value="complaint">Spam complaint</option>
          </select>
        </label>
        {type === "bounce" && (
          <label className="field">
            <span>Bounce type</span>
            <select className="select" value={bounceType} onChange={(event) => setBounceType(event.target.value as typeof bounceType)}>
              <option value="hard">Hard (address does not exist)</option>
              <option value="soft">Soft (mailbox full, try again)</option>
              <option value="blocked">Blocked (rejected by policy)</option>
            </select>
          </label>
        )}
        {type === "reply" && (
          <div className="chips">
            {Object.entries(SAMPLE_REPLIES).map(([label, text]) => (
              <button key={label} type="button" className={body === text ? "chip-toggle on" : "chip-toggle"} onClick={() => setBody(text)}>
                {label}
              </button>
            ))}
          </div>
        )}
        <label className="field">
          <span>Subject (optional)</span>
          <input className="input" value={subject} placeholder="Defaults to the thread's subject" onChange={(event) => setSubject(event.target.value)} />
        </label>
        <label className="field">
          <span>Body</span>
          <textarea className="textarea" value={body} onChange={(event) => setBody(event.target.value)} />
        </label>
        <ErrorAlert error={simulate.error} />
      </div>
    </Modal>
  );
}

const CONTACTS_TEMPLATE = `email,first_name,last_name,job_title,company,country,city,language,network,office,tags
deniz@ist-cargo.example,Deniz,Yilmaz,Import Manager,IST Cargo,TR,Istanbul,tr,WCA,UAE,air;vip
lea@paris-freight.example,Léa,Martin,Logistics Lead,Paris Freight,FR,Paris,fr,JCtrans,,ocean`;

function ContactImportModal({ onClose }: { onClose: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [tag, setTag] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const run = useMutation({
    mutationFn: () => {
      const form = new FormData();
      if (file) form.append("file", file);
      if (tag.trim()) form.append("tag", tag.trim());
      return unwrap(api.POST("/api/v1/contacts/import", { body: form as never }));
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.contacts });
      void queryClient.invalidateQueries({ queryKey: keys.companies });
    },
  });
  const template = `data:text/csv;charset=utf-8,${encodeURIComponent(CONTACTS_TEMPLATE)}`;
  return (
    <Modal
      title="Import contacts from CSV"
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
          Up to 5,000 rows. Contacts are matched by email (existing ones are updated), companies and offices by name;
          missing companies are created. Country: ISO code or name. Tags: separated by <code>;</code>.
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
          <div className="small">Excel: File → Save as → CSV UTF-8</div>
          <input ref={input} type="file" accept=".csv,text/csv" hidden onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
        </div>
        <label className="field">
          <span>Tag every imported contact (optional)</span>
          <input className="input" value={tag} onChange={(event) => setTag(event.target.value)} placeholder="air-intro" />
          <span className="field-help">Workflows that start on this tag start for every contact in the file — this is how an import launches a sequence.</span>
        </label>
        <a className="small" href={template} download="contacts-template.csv">Download a template</a>
        <ErrorAlert error={run.error} />
        {run.data && (
          <div className={run.data.errors.length ? "alert warning" : "alert success"}>
            {run.data.created} created, {run.data.updated} updated, {run.data.tagged} tags added, {run.data.companies_created} companies created.
            {run.data.errors.length > 0 && (
              <ul style={{ margin: "6px 0 0", paddingLeft: 18, maxHeight: 160, overflowY: "auto" }}>
                {run.data.errors.map((error, index) => (
                  <li key={index}>{error}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}


const EMAIL_STATUS_TONES: Record<string, "green" | "amber" | "red" | "gray" | "blue"> = {
  valid: "green",
  risky: "amber",
  catch_all: "blue",
  invalid: "red",
};

function EmailStatusBadge({ status }: { status: string }) {
  if (status === "unknown") return null;
  return (
    <Badge tone={EMAIL_STATUS_TONES[status] ?? "gray"}>
      <Icon name="shield" /> {status === "invalid" ? "email invalid" : status.replace("_", " ")}
    </Badge>
  );
}
