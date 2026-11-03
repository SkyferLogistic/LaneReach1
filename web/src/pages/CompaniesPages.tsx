/** Companies: branches, contacts and the freeze switch (blocks every office, every branch). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { LoadMore, usePaged } from "../api/paging";
import { keys } from "../api/queries";
import { useSession } from "../auth/session";
import { useDebounced } from "../components/ContactPicker";
import { Page } from "../components/Layout";
import {
  Badge,
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
import { formatDateTime } from "../lib/format";
import { useConfirm, useDelete } from "../components/confirm";
import { Icon } from "../components/icons";

export function CompaniesPage() {
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const q = useDebounced(search);
  const navigate = useNavigate();
  const { can } = useSession();
  const page = usePaged([...keys.companies, "list", q], (cursor) =>
    unwrap(api.GET("/api/v1/companies", { params: { query: { q: q || undefined, cursor, limit: 50 } } })),
  );
  return (
    <Page>
      <PageHeader
        title="Companies"
        description="Prospects and their branches. Only one office emails a company per week, and a frozen company gets nothing."
        actions={
          can("workflows:write") && (
            <Button variant="primary" onClick={() => setCreating(true)}>
              New company
            </Button>
          )
        }
      />
      <input className="input" style={{ maxWidth: 320, marginBottom: 12 }} placeholder="Search name or domain…" value={search} onChange={(event) => setSearch(event.target.value)} />
      <Card flush>
        {page.isLoading && <Loading />}
        <ErrorAlert error={page.error} />
        {!page.isLoading && page.items.length === 0 && <Empty art="warehouse" title="No companies" />}
        {page.items.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Country</th>
                <th>Industry</th>
                <th>Branches</th>
                <th>Contacts</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((company) => (
                <tr key={company.id} className="clickable" onClick={() => navigate(`/companies/${company.id}`)}>
                  <td>
                    <strong>{company.name}</strong>
                    {company.domain && <div className="small muted">{company.domain}</div>}
                  </td>
                  <td>{company.country ?? "—"}</td>
                  <td>{company.industry ?? "—"}</td>
                  <td>{(company.branches ?? []).length}</td>
                  <td>{company.contact_count}</td>
                  <td>{company.frozen_at ? <Badge tone="red">frozen</Badge> : <Badge tone="green">active</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <LoadMore {...page} />
      </Card>
      {creating && <NewCompanyModal onClose={() => setCreating(false)} />}
    </Page>
  );
}

function NewCompanyModal({ onClose }: { onClose: () => void }) {
  const [form, setForm] = useState({ name: "", domain: "", country: "", industry: "" });
  const [branches, setBranches] = useState<{ name: string; country: string; city: string }[]>([]);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/companies", {
          body: {
            name: form.name,
            domain: form.domain || null,
            country: form.country.toUpperCase() || null,
            industry: form.industry || null,
            branches: branches
              .filter((branch) => branch.name && branch.country)
              .map((branch) => ({ name: branch.name, country: branch.country.toUpperCase(), city: branch.city || null })),
          },
        }),
      ),
    onSuccess: (company) => {
      void queryClient.invalidateQueries({ queryKey: keys.companies });
      navigate(`/companies/${company.id}`);
    },
  });
  const set = (name: keyof typeof form) => (event: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [name]: event.target.value }));
  return (
    <Modal
      title="New company"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={create.isPending} disabled={!form.name} onClick={() => create.mutate()}>
            Create
          </Button>
        </>
      }
    >
      <div className="stack">
        <div className="form-grid">
          <label className="field">
            <span>Name *</span>
            <input className="input" value={form.name} onChange={set("name")} />
          </label>
          <label className="field">
            <span>Domain</span>
            <input className="input" value={form.domain} placeholder="gulf-importers.com" onChange={set("domain")} />
          </label>
          <label className="field">
            <span>Country (ISO)</span>
            <input className="input" maxLength={2} value={form.country} onChange={set("country")} />
          </label>
          <label className="field">
            <span>Industry</span>
            <input className="input" value={form.industry} onChange={set("industry")} />
          </label>
        </div>
        <fieldset>
          <legend>Branches</legend>
          <div className="stack tight">
            {branches.map((branch, index) => (
              <div key={index} className="row">
                {(["name", "country", "city"] as const).map((field) => (
                  <input
                    key={field}
                    className="input"
                    style={{ flex: 1 }}
                    placeholder={field}
                    value={branch[field]}
                    onChange={(event) =>
                      setBranches((current) => current.map((item, i) => (i === index ? { ...item, [field]: event.target.value } : item)))
                    }
                  />
                ))}
                <Button size="sm" variant="ghost" onClick={() => setBranches((current) => current.filter((_, i) => i !== index))}>
                  ✕
                </Button>
              </div>
            ))}
            <Button size="sm" onClick={() => setBranches((current) => [...current, { name: "", country: "", city: "" }])}>
              + Branch
            </Button>
          </div>
        </fieldset>
        <ErrorAlert error={create.error} />
      </div>
    </Modal>
  );
}

export function CompanyDetailPage() {
  const { companyId = "" } = useParams();
  const queryClient = useQueryClient();
  const toast = useToast();
  const onError = useErrorToast();
  const { can } = useSession();
  const [branch, setBranch] = useState({ name: "", country: "", city: "" });
  const [freezing, setFreezing] = useState(false);
  const [reason, setReason] = useState("");
  const goTo = useNavigate();
  const [confirmDialog, ask] = useConfirm();
  const removeCompany = useDelete({
    run: () => unwrap(api.DELETE("/api/v1/companies/{company_id}", { params: { path: { company_id: companyId } } })),
    success: "Company deleted.",
    invalidate: [keys.companies],
    onDone: () => goTo("/companies"),
  });
  const company = useQuery({
    queryKey: [...keys.companies, companyId],
    queryFn: () => unwrap(api.GET("/api/v1/companies/{company_id}", { params: { path: { company_id: companyId } } })),
  });
  const contacts = useQuery({
    queryKey: [...keys.contacts, "company", companyId],
    queryFn: () => unwrap(api.GET("/api/v1/contacts", { params: { query: { company_id: companyId, limit: 100 } } })),
  });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: keys.companies });
  const update = useMutation({
    mutationFn: (body: { frozen: boolean; frozen_reason?: string | null }) =>
      unwrap(api.PATCH("/api/v1/companies/{company_id}", { params: { path: { company_id: companyId } }, body })),
    onSuccess: (updated) => {
      toast(updated.frozen_at ? "Company frozen: no office will email it." : "Company unfrozen.");
      setFreezing(false);
      refresh();
    },
    onError,
  });
  const addBranch = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/companies/{company_id}/branches", {
          params: { path: { company_id: companyId } },
          body: { name: branch.name, country: branch.country.toUpperCase(), city: branch.city || null },
        }),
      ),
    onSuccess: () => {
      setBranch({ name: "", country: "", city: "" });
      refresh();
    },
    onError,
  });
  if (company.isLoading) return <Loading />;
  if (!company.data) return <Page><ErrorAlert error={company.error} /></Page>;
  const data = company.data;
  return (
    <Page>
      <PageHeader
        title={
          <span className="row">
            {data.name} {data.frozen_at ? <Badge tone="red">frozen</Badge> : <Badge tone="green">active</Badge>}
          </span>
        }
        description={[data.domain, data.country, data.industry].filter(Boolean).join(" · ")}
        actions={
          can("workflows:write") && (
            <>
              {data.frozen_at ? (
                <Button onClick={() => update.mutate({ frozen: false })}>Unfreeze</Button>
              ) : (
                <Button variant="danger" onClick={() => setFreezing(true)}>
                  Freeze company
                </Button>
              )}
              <Button
                variant="ghost"
                busy={removeCompany.isPending}
                onClick={() =>
                  void ask({
                    title: `Delete ${data.name}?`,
                    message: "Only a company without contacts can be deleted (its branches and office history go with it). Move or erase its contacts first.",
                  }).then((ok) => ok && removeCompany.mutate())
                }
              >
                <Icon name="trash" /> Delete
              </Button>
              {confirmDialog}
            </>
          )
        }
      />
      {data.frozen_at && (
        <div className="alert warning" style={{ marginBottom: 16 }}>
          Frozen {formatDateTime(data.frozen_at)}: {data.frozen_reason ?? "no reason given"}. Every email to any branch is
          blocked by the company-freeze guardrail.
        </div>
      )}
      <div className="grid two" style={{ alignItems: "start" }}>
        <Card title="Branches">
          <div className="stack">
            {(data.branches ?? []).length === 0 && <span className="muted small">No branches</span>}
            <table className="table">
              <tbody>
                {(data.branches ?? []).map((item) => (
                  <tr key={item.id}>
                    <td>{item.name}</td>
                    <td>{item.country}</td>
                    <td>{item.city ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {can("workflows:write") && (
              <form
                className="row"
                onSubmit={(event) => {
                  event.preventDefault();
                  addBranch.mutate();
                }}
              >
                <input className="input" style={{ flex: 2 }} placeholder="Branch name" value={branch.name} onChange={(event) => setBranch({ ...branch, name: event.target.value })} />
                <input className="input" style={{ flex: 1 }} placeholder="Country" maxLength={2} value={branch.country} onChange={(event) => setBranch({ ...branch, country: event.target.value })} />
                <input className="input" style={{ flex: 1 }} placeholder="City" value={branch.city} onChange={(event) => setBranch({ ...branch, city: event.target.value })} />
                <Button type="submit" busy={addBranch.isPending} disabled={!branch.name || branch.country.length !== 2}>
                  Add
                </Button>
              </form>
            )}
          </div>
        </Card>
        <Card title={`Contacts (${data.contact_count})`} flush>
          {contacts.data?.items.length === 0 && <Empty title="No contacts" />}
          <table className="table">
            <tbody>
              {contacts.data?.items.map((contact) => (
                <tr key={contact.id}>
                  <td>
                    <Link to={`/contacts/${contact.id}`}>{contact.display_name}</Link>
                    <div className="small muted">{contact.email}</div>
                  </td>
                  <td><StatusBadge value={contact.stage} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Details">
          <KeyValues
            items={[
              ["Domain", data.domain],
              ["Country", data.country],
              ["Industry", data.industry],
              ["Created", formatDateTime(data.created_at)],
            ]}
          />
        </Card>
      </div>
      {freezing && (
        <Modal
          title="Freeze company"
          onClose={() => setFreezing(false)}
          footer={
            <>
              <Button onClick={() => setFreezing(false)}>Cancel</Button>
              <Button variant="danger" busy={update.isPending} onClick={() => update.mutate({ frozen: true, frozen_reason: reason || null })}>
                Freeze
              </Button>
            </>
          }
        >
          <label className="field">
            <span>Reason</span>
            <input className="input" value={reason} placeholder="Existing customer of our Spain office" onChange={(event) => setReason(event.target.value)} />
          </label>
        </Modal>
      )}
    </Page>
  );
}
