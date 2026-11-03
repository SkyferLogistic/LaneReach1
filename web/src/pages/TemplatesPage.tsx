/** Email templates per key and language, with AI localization into new languages. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys, useTemplates } from "../api/queries";
import { useSession } from "../auth/session";
import { Page } from "../components/Layout";
import { Badge, Button, Card, Empty, ErrorAlert, Loading, Modal, PageHeader, useToast } from "../components/ui";

type Template = Schemas["TemplateResponse"];

const PLACEHOLDER_HELP =
  "Placeholders: {{contact.first_name}}, {{contact.full_name}}, {{contact.city}}, {{company.name}}, {{office.name}}, {{office.signature}}, {{sender.name}}, {{sender.first_name}}, {{unsubscribe.url}}. Add a fallback with {{contact.first_name|there}}.";

export function TemplatesPage() {
  const templates = useTemplates();
  const { can } = useSession();
  const [editing, setEditing] = useState<Template | "new" | null>(null);
  const [localizing, setLocalizing] = useState<Template | null>(null);
  const groups = useMemo(() => {
    const byKey = new Map<string, Template[]>();
    for (const template of templates.data ?? []) byKey.set(template.key, [...(byKey.get(template.key) ?? []), template]);
    return [...byKey.entries()];
  }, [templates.data]);

  return (
    <Page>
      <PageHeader
        title="Templates"
        description="One key, one version per language. Emails use the contact's language, then the office's, then English."
        actions={
          can("workflows:write") && (
            <Button variant="primary" onClick={() => setEditing("new")}>
              New template
            </Button>
          )
        }
      />
      {templates.isLoading && <Loading />}
      <ErrorAlert error={templates.error} />
      {groups.length === 0 && !templates.isLoading && <Empty title="No templates" />}
      <div className="stack">
        {groups.map(([key, versions]) => (
          <Card key={key} title={<h2 className="mono">{key}</h2>}>
            <div className="grid three">
              {versions.map((template) => (
                <div key={template.id} className="email-preview stack tight">
                  <div className="row">
                    <Badge tone="blue">{template.language}</Badge>
                    <span className="small muted truncate">{template.name}</span>
                    <span className="spacer" />
                    {can("workflows:write") && (
                      <>
                        <Button size="sm" onClick={() => setEditing(template)}>Edit</Button>
                        <Button size="sm" onClick={() => setLocalizing(template)} title="Translate and adapt with AI">
                          Localize
                        </Button>
                      </>
                    )}
                  </div>
                  <strong>{template.subject}</strong>
                  <pre className="small" style={{ maxHeight: 140, overflow: "hidden" }}>{template.body}</pre>
                  <div className="chips">
                    {template.placeholders.map((placeholder) => (
                      <span key={placeholder} className="tag mono">{placeholder}</span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </Card>
        ))}
      </div>
      {editing && <TemplateModal template={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
      {localizing && <LocalizeModal template={localizing} onClose={() => setLocalizing(null)} />}
    </Page>
  );
}

function TemplateModal({ template, onClose, initial }: { template: Template | null; onClose: () => void; initial?: Partial<Template> }) {
  const [form, setForm] = useState({
    key: template?.key ?? initial?.key ?? "",
    language: template?.language ?? initial?.language ?? "en",
    name: template?.name ?? initial?.name ?? "",
    subject: template?.subject ?? initial?.subject ?? "",
    body: template?.body ?? initial?.body ?? "",
  });
  const queryClient = useQueryClient();
  const toast = useToast();
  const set = (name: keyof typeof form) => (event: { target: { value: string } }) => setForm((current) => ({ ...current, [name]: event.target.value }));
  const save = useMutation({
    mutationFn: () =>
      template
        ? unwrap(
            api.PUT("/api/v1/templates/{template_id}", {
              params: { path: { template_id: template.id } },
              body: { name: form.name, subject: form.subject, body: form.body },
            }),
          )
        : unwrap(api.POST("/api/v1/templates", { body: form })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.templates });
      toast("Template saved.");
      onClose();
    },
  });
  const remove = useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/templates/{template_id}", { params: { path: { template_id: template!.id } } })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.templates });
      onClose();
    },
  });
  return (
    <Modal
      title={template ? `${template.key} (${template.language})` : "New template"}
      onClose={onClose}
      footer={
        <>
          {template && (
            <Button variant="danger" busy={remove.isPending} onClick={() => window.confirm("Delete this language version?") && remove.mutate()}>
              Delete
            </Button>
          )}
          <span className="spacer" />
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={save.isPending} onClick={() => save.mutate()}>
            Save
          </Button>
        </>
      }
    >
      <div className="stack">
        <div className="form-grid">
          <label className="field"><span>Key</span><input className="input mono" value={form.key} disabled={Boolean(template)} placeholder="intro" onChange={set("key")} /></label>
          <label className="field"><span>Language</span><input className="input" maxLength={2} value={form.language} disabled={Boolean(template)} onChange={set("language")} /></label>
          <label className="field"><span>Name</span><input className="input" value={form.name} onChange={set("name")} /></label>
        </div>
        <label className="field"><span>Subject</span><input className="input" value={form.subject} onChange={set("subject")} /></label>
        <label className="field">
          <span>Body</span>
          <textarea className="textarea" style={{ minHeight: 220 }} value={form.body} onChange={set("body")} />
          <span className="field-help">{PLACEHOLDER_HELP}</span>
        </label>
        <ErrorAlert error={save.error ?? remove.error} />
      </div>
    </Modal>
  );
}

function LocalizeModal({ template, onClose }: { template: Template; onClose: () => void }) {
  const [language, setLanguage] = useState("fr");
  const [region, setRegion] = useState("");
  const [saving, setSaving] = useState(false);
  const localize = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/ai/templates/localize", {
          body: { template_id: template.id, target_language: language, region: region.toUpperCase() || null },
        }),
      ),
  });
  if (saving && localize.data) {
    return (
      <TemplateModal
        template={null}
        onClose={onClose}
        initial={{
          key: template.key,
          language,
          name: `${template.key} (${language})`,
          subject: localize.data.localized.subject,
          body: localize.data.localized.body,
        }}
      />
    );
  }
  return (
    <Modal
      title={`Localize “${template.key}”`}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          {localize.data ? (
            <Button variant="primary" onClick={() => setSaving(true)}>
              Review and save as {language}
            </Button>
          ) : (
            <Button variant="primary" busy={localize.isPending} onClick={() => localize.mutate()}>
              Localize with AI
            </Button>
          )}
        </>
      }
    >
      <div className="stack">
        <div className="form-grid">
          <label className="field"><span>Target language</span><input className="input" maxLength={2} value={language} onChange={(event) => setLanguage(event.target.value.toLowerCase())} /></label>
          <label className="field"><span>Region (optional)</span><input className="input" maxLength={2} value={region} placeholder="FR" onChange={(event) => setRegion(event.target.value)} /></label>
        </div>
        <p className="small muted" style={{ margin: 0 }}>Placeholders are kept exactly; the result is not stored until you save it.</p>
        <ErrorAlert error={localize.error} />
        {localize.data && (
          <div className="email-preview stack tight">
            <strong>{localize.data.localized.subject}</strong>
            <pre>{localize.data.localized.body}</pre>
            {localize.data.localized.notes_for_reviewer && <div className="small muted">Note: {localize.data.localized.notes_for_reviewer}</div>}
          </div>
        )}
      </div>
    </Modal>
  );
}
