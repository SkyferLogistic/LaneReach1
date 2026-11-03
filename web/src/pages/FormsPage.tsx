/**
 * Website quote forms: build a "Request a quote" form, copy its HTML into the website, see
 * what visitors sent. Submissions create or update the contact, add the tag and publish
 * `form.submitted` (workflows with the "Web form submitted" trigger start).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, describeError, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { useOffices } from "../api/queries";
import { useSession } from "../auth/session";
import { Icon } from "../components/icons";
import { Page } from "../components/Layout";
import { SkeletonRows } from "../components/motion";
import { Badge, Button, Drawer, Empty, ErrorAlert, Modal, PageHeader, Switch, useToast } from "../components/ui";
import { formatDateTime, formatRelative, humanize } from "../lib/format";

type WebForm = Schemas["FormResponse"];
type FormRequest = Schemas["FormRequest"];

const FORMS_KEY = ["forms"] as const;

export function FormsPage() {
  const { can } = useSession();
  const writer = can("workflows:write");
  const forms = useQuery({ queryKey: FORMS_KEY, queryFn: () => unwrap(api.GET("/api/v1/forms")) });
  const offices = useOffices();
  const officeName = new Map((offices.data ?? []).map((office) => [office.id, office.name]));
  const [params] = useSearchParams();
  const [editing, setEditing] = useState<WebForm | "new" | null>(params.get("new") === "1" ? "new" : null);
  const [showing, setShowing] = useState<WebForm | null>(null);
  return (
    <Page>
      <PageHeader
        eyebrow={<><Icon name="file" /> Outreach</>}
        title="Website forms"
        description="Quote requests from your website become contacts with a tag and a workflow event, with no script on the website: paste the form's HTML."
        actions={writer && <Button variant="primary" onClick={() => setEditing("new")}><Icon name="plus" /> New form</Button>}
      />
      <ErrorAlert error={forms.error} />
      {forms.isLoading && <SkeletonRows rows={3} />}
      {forms.data?.length === 0 && <Empty art="customs" title="No forms yet">Create a "Request a quote" form and paste it into your website.</Empty>}
      <div className="grid two">
        {forms.data?.map((form) => (
          <div key={form.id} className="card integration-row">
            <div className="row between" style={{ gap: 8 }}>
              <div>
                <strong>{form.name}</strong>
                <div className="small muted">
                  {form.office_id ? officeName.get(form.office_id) ?? "Office" : "No office"} · {form.tag ? `tag ${form.tag}` : "no tag"}
                </div>
              </div>
              <Badge tone={form.is_active ? "green" : "gray"}>{form.is_active ? "Live" : "Off"}</Badge>
            </div>
            <div className="small muted">
              {form.submission_count} submissions{form.last_submission_at ? ` · last ${formatRelative(form.last_submission_at)}` : ""}
            </div>
            <div className="chips">{form.fields.map((field) => <Badge key={field}>{humanize(field)}</Badge>)}</div>
            <div className="row" style={{ gap: 6 }}>
              <Button size="sm" onClick={() => setShowing(form)}><Icon name="file" /> Embed & submissions</Button>
              {writer && <Button size="sm" variant="ghost" onClick={() => setEditing(form)}>Edit</Button>}
            </div>
          </div>
        ))}
      </div>
      {editing && <FormEditor form={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
      {showing && <FormDrawer form={showing} onClose={() => setShowing(null)} />}
    </Page>
  );
}

function FormEditor({ form, onClose }: { form: WebForm | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const offices = useOffices();
  const catalog = useQuery({ queryKey: [...FORMS_KEY, "fields"], queryFn: () => unwrap(api.GET("/api/v1/forms/fields")) });
  const [draft, setDraft] = useState<FormRequest>({
    name: form?.name ?? "Request a quote",
    office_id: form?.office_id ?? null,
    tag: form?.tag ?? "website-quote",
    fields: form?.fields ?? ["first_name", "last_name", "company", "origin", "destination", "mode", "message"],
    redirect_url: form?.redirect_url ?? null,
    thank_you_text: form?.thank_you_text ?? "Thank you, our team will come back to you shortly.",
    is_active: form?.is_active ?? true,
  });
  const fields = draft.fields ?? [];
  const save = useMutation({
    mutationFn: () =>
      form
        ? unwrap(api.PUT("/api/v1/forms/{form_id}", { params: { path: { form_id: form.id } }, body: draft }))
        : unwrap(api.POST("/api/v1/forms", { body: draft })),
    onSuccess: () => {
      toast("Form saved.");
      void queryClient.invalidateQueries({ queryKey: FORMS_KEY });
      onClose();
    },
  });
  const remove = useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/forms/{form_id}", { params: { path: { form_id: form?.id ?? "" } } })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: FORMS_KEY });
      onClose();
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  return (
    <Modal title={form ? `Edit ${form.name}` : "New website form"} onClose={onClose}>
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <label className="field"><span>Name</span><input className="input" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} required maxLength={100} /></label>
        <div className="form-grid">
          <label className="field">
            <span>Office of new contacts</span>
            <select className="select" value={draft.office_id ?? ""} onChange={(event) => setDraft({ ...draft, office_id: event.target.value || null })}>
              <option value="">No office</option>
              {(offices.data ?? []).map((office) => <option key={office.id} value={office.id}>{office.name}</option>)}
            </select>
          </label>
          <label className="field"><span>Tag</span><input className="input" value={draft.tag ?? ""} onChange={(event) => setDraft({ ...draft, tag: event.target.value || null })} pattern="[a-z0-9][a-z0-9_-]*" /></label>
        </div>
        <span className="small muted">Fields after the email (in this order)</span>
        <div className="chips">
          {(catalog.data ?? []).map((field) => (
            <button
              key={field.key}
              type="button"
              className={fields.includes(field.key) ? "chip-toggle on" : "chip-toggle"}
              onClick={() => setDraft({ ...draft, fields: fields.includes(field.key) ? fields.filter((item) => item !== field.key) : [...fields, field.key] })}
            >
              {field.label}
            </button>
          ))}
        </div>
        <label className="field"><span>Thank-you page (optional, https)</span><input className="input" type="url" value={draft.redirect_url ?? ""} onChange={(event) => setDraft({ ...draft, redirect_url: event.target.value || null })} placeholder="https://your-site.example/thanks" /></label>
        <label className="field"><span>Thank-you text (when there is no page)</span><textarea className="textarea" value={draft.thank_you_text ?? ""} onChange={(event) => setDraft({ ...draft, thank_you_text: event.target.value })} /></label>
        <label className="row between"><span>Accept submissions</span><Switch label="Accept submissions" checked={draft.is_active ?? true} onChange={(value) => setDraft({ ...draft, is_active: value })} /></label>
        <ErrorAlert error={save.error} />
        <div className="row between">
          {form ? <Button type="button" variant="danger" busy={remove.isPending} onClick={() => window.confirm("Delete this form and its submissions? The website form stops working.") && remove.mutate()}>Delete</Button> : <span />}
          <Button type="submit" variant="primary" busy={save.isPending}>Save form</Button>
        </div>
      </form>
    </Modal>
  );
}

function FormDrawer({ form, onClose }: { form: WebForm; onClose: () => void }) {
  const toast = useToast();
  const submissions = useQuery({
    queryKey: [...FORMS_KEY, form.id, "submissions"],
    queryFn: () => unwrap(api.GET("/api/v1/forms/{form_id}/submissions", { params: { path: { form_id: form.id } } })),
  });
  return (
    <Drawer wide title={form.name} onClose={onClose}>
      <div className="stack">
        <h3 className="section-title">Paste into your website</h3>
        <p className="small muted" style={{ margin: 0 }}>
          A plain HTML form: it works on any site builder. The hidden <code>website</code> field catches bots. Style it with your site's CSS.
        </p>
        <pre className="code-block">{form.embed_html}</pre>
        <div className="row" style={{ gap: 6 }}>
          <Button size="sm" onClick={() => void navigator.clipboard.writeText(form.embed_html).then(() => toast("HTML copied."))}>Copy HTML</Button>
          <Button size="sm" variant="ghost" onClick={() => void navigator.clipboard.writeText(form.action_url).then(() => toast("URL copied."))}>Copy the post URL</Button>
        </div>
        <h3 className="section-title">Submissions</h3>
        {submissions.isLoading && <SkeletonRows rows={3} />}
        {submissions.data?.length === 0 && <Empty title="No submissions yet" />}
        <div className="stack">
          {submissions.data?.map((item) => (
            <div key={item.id} className="card integration-row">
              <div className="row between">
                <strong>{item.email}</strong>
                <span className="small muted" title={formatDateTime(item.created_at)}>{formatRelative(item.created_at)}</span>
              </div>
              <div className="small">
                {[...form.fields, ...Object.keys(item.data).filter((key) => !form.fields.includes(key))]
                  .filter((key) => item.data[key] !== undefined)
                  .map((key) => [key, item.data[key]] as const)
                  .map(([key, value]) => (
                  <div key={key}><span className="muted">{humanize(key)}:</span> {String(value)}</div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </Drawer>
  );
}
