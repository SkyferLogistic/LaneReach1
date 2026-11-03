/** Offices (the voice emails are sent in) and their sender mailboxes. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys, useOffices, useSenders } from "../api/queries";
import { useSession } from "../auth/session";
import { Page } from "../components/Layout";
import { ImageDrop } from "../components/ImageDrop";
import { CountrySelect, LanguageSelect, TimeZoneSelect } from "../components/pickers";
import { Badge, Button, Card, Empty, ErrorAlert, Loading, Modal, PageHeader, Segmented, useErrorToast, useToast } from "../components/ui";
import { countryFlag, initials } from "../lib/freight";
import { zoneOffset } from "../lib/locales";
import { useConfirm, useDelete } from "../components/confirm";
import { Icon } from "../components/icons";

export function OfficesPage() {
  const [confirmDialog, ask] = useConfirm();
  const removeOffice = useDelete<string>({
    run: (id) => unwrap(api.DELETE("/api/v1/offices/{office_id}", { params: { path: { office_id: id } } })),
    success: "Office deleted.",
    invalidate: [keys.offices],
  });
  const removeSender = useDelete<string>({
    run: (id) => unwrap(api.DELETE("/api/v1/senders/{sender_id}", { params: { path: { sender_id: id } } })),
    success: "Mailbox deleted.",
    invalidate: [keys.senders],
  });
  const offices = useOffices();
  const senders = useSenders();
  const { can } = useSession();
  const [editing, setEditing] = useState<Schemas["OfficeResponse"] | "new" | null>(null);
  const [addingSender, setAddingSender] = useState<string | null>(null);
  const urls = useAssetUrls();
  const queryClient = useQueryClient();
  const toast = useToast();
  const onError = useErrorToast();
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: keys.offices });
    void queryClient.invalidateQueries({ queryKey: keys.senders });
  };
  const pause = useMutation({
    mutationFn: ({ id, paused }: { id: string; paused: boolean }) =>
      unwrap(api.PATCH("/api/v1/offices/{office_id}", { params: { path: { office_id: id } }, body: { sending_paused: paused } })),
    onSuccess: (office) => {
      toast(office.sending_paused_at ? `${office.name}: sending paused.` : `${office.name}: sending resumed.`);
      refresh();
    },
    onError,
  });
  const updateSender = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Schemas["SenderUpdateRequest"] }) =>
      unwrap(api.PATCH("/api/v1/senders/{sender_id}", { params: { path: { sender_id: id } }, body })),
    onSuccess: refresh,
    onError,
  });

  return (
    <Page>
      <PageHeader
        title="Offices & senders"
        description="Each office writes in its own voice and signature and sends from its own mailboxes, rotating across them within each mailbox's daily cap."
        actions={
          can("admin:manage") && (
            <Button variant="primary" onClick={() => setEditing("new")}>
              New office
            </Button>
          )
        }
      />
      {offices.isLoading && <Loading />}
      <ErrorAlert error={offices.error} />
      {offices.data?.length === 0 && <Empty title="No offices yet" />}
      <div className="office-grid">
        {offices.data?.map((office) => {
          const mailboxes = (senders.data ?? []).filter((sender) => sender.office_id === office.id);
          return (
            <Card
              key={office.id}
              title={
                <div className="row">
                  <h2>{office.name}</h2>
                  <Badge>{office.country}</Badge>
                  {office.sending_paused_at ? <Badge tone="red">paused</Badge> : <Badge tone="green">sending</Badge>}
                </div>
              }
              actions={
                can("admin:manage") && (
                  <>
                    <Button size="sm" onClick={() => pause.mutate({ id: office.id, paused: !office.sending_paused_at })}>
                      {office.sending_paused_at ? "Resume" : "Pause"}
                    </Button>
                    <Button size="sm" onClick={() => setEditing(office)}>
                      Edit
                    </Button>
                    <button
                      type="button"
                      className="icon-button danger"
                      title="Delete office"
                      aria-label={`Delete ${office.name}`}
                      onClick={() =>
                        void ask({
                          title: `Delete ${office.name}?`,
                          message: "Only an office without agents, contacts, email history or workflows can be deleted. To stop it sending, pause it instead.",
                        }).then((ok) => ok && removeOffice.mutate(office.id))
                      }
                    >
                      <Icon name="trash" />
                    </button>
                  </>
                )
              }
            >
              <div className="stack">
                <div className="small muted office-meta">
                  {countryFlag(office.country)} {office.timezone} ({zoneOffset(office.timezone) ? `UTC${zoneOffset(office.timezone)}` : ""}) · default language {office.default_language} · owner {office.owner_name} &lt;{office.owner_email}&gt;
                </div>
                {office.signature_asset_id && urls.get(office.signature_asset_id) ? (
                  <div className="signature-preview">
                    <img src={urls.get(office.signature_asset_id)} alt={`${office.name} signature`} />
                  </div>
                ) : (
                  office.signature && <pre className="email-preview small">{office.signature}</pre>
                )}
                <div className="mailbox-list">
                  {mailboxes.map((sender) => (
                    <div key={sender.id} className={sender.is_active ? "mailbox-row" : "mailbox-row off"}>
                      <span className="avatar any mailbox-avatar">{initials(sender.display_name)}</span>
                      <div className="mailbox-who">
                        <strong className="truncate">{sender.display_name}</strong>
                        <span className="small muted truncate" title={sender.email}>
                          {sender.email}
                        </span>
                      </div>
                      <label className="cap-field" title="Daily cap: the most emails this mailbox sends per day">
                        <input
                          className="input"
                          type="number"
                          min={1}
                          defaultValue={sender.daily_cap}
                          disabled={!can("admin:manage")}
                          aria-label={`Daily cap of ${sender.display_name}`}
                          onBlur={(event) => {
                            const cap = Number(event.target.value);
                            if (cap && cap !== sender.daily_cap) updateSender.mutate({ id: sender.id, body: { daily_cap: cap } });
                          }}
                        />
                        <span className="small muted">/day</span>
                      </label>
                      <span className="mailbox-state">
                        {sender.flagged_at ? (
                          <span title={sender.flag_reason ?? ""}>
                            <Badge tone="red">flagged</Badge>
                          </span>
                        ) : sender.is_active ? (
                          <Badge tone="green">active</Badge>
                        ) : (
                          <Badge>inactive</Badge>
                        )}
                      </span>
                      {can("admin:manage") && (
                        <span className="mailbox-actions">
                          {sender.flagged_at && (
                            <Button size="sm" onClick={() => updateSender.mutate({ id: sender.id, body: { clear_flag: true } })}>
                              Clear flag
                            </Button>
                          )}
                          <Button
                            size="sm"
                            variant="ghost"
                            title={sender.is_active ? "Stop sending from this mailbox" : "Send from this mailbox again"}
                            onClick={() => updateSender.mutate({ id: sender.id, body: { is_active: !sender.is_active } })}
                          >
                            {sender.is_active ? "Deactivate" : "Activate"}
                          </Button>
                          <button
                            type="button"
                            className="icon-button danger"
                            title="Delete mailbox"
                            aria-label={`Delete ${sender.email}`}
                            onClick={() =>
                              void ask({
                                title: `Delete ${sender.display_name}?`,
                                message: "Only mailboxes that never sent can be deleted; otherwise deactivate them.",
                              }).then((ok) => ok && removeSender.mutate(sender.id))
                            }
                          >
                            <Icon name="trash" />
                          </button>
                        </span>
                      )}
                    </div>
                  ))}
                </div>
                {mailboxes.length === 0 && <div className="alert warning">No sender mailbox: this office cannot send.</div>}
                {can("admin:manage") && (
                  <div>
                    <Button size="sm" onClick={() => setAddingSender(office.id)}>
                      + Mailbox
                    </Button>
                  </div>
                )}
              </div>
            </Card>
          );
        })}
      </div>
      {editing && <OfficeModal office={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={refresh} />}
      {addingSender && <SenderModal officeId={addingSender} onClose={() => setAddingSender(null)} onSaved={refresh} />}
      {confirmDialog}
    </Page>
  );
}

function useAssetUrls() {
  const assets = useQuery({ queryKey: keys.assets, queryFn: () => unwrap(api.GET("/api/v1/scripts/assets")) });
  return useMemo(() => new Map((assets.data ?? []).map((asset) => [asset.id, asset.url])), [assets.data]);
}

function OfficeModal({ office, onClose, onSaved }: { office: Schemas["OfficeResponse"] | null; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({
    name: office?.name ?? "",
    country: office?.country ?? "",
    timezone: office?.timezone ?? "",
    default_language: office?.default_language ?? "en",
    owner_name: office?.owner_name ?? "",
    owner_email: office?.owner_email ?? "",
    signature: office?.signature ?? "",
  });
  const [mode, setMode] = useState<"text" | "picture">(office?.signature_asset_id ? "picture" : "text");
  const [picture, setPicture] = useState<File | null>(null);
  const [removePicture, setRemovePicture] = useState(false);
  const urls = useAssetUrls();
  const toast = useToast();
  const set = (name: keyof typeof form) => (value: string) => setForm((current) => ({ ...current, [name]: value }));
  const text = (name: keyof typeof form) => (event: { target: { value: string } }) => set(name)(event.target.value);
  const save = useMutation({
    mutationFn: async () => {
      const body = { ...form, country: form.country.toUpperCase() };
      const saved = office
        ? await unwrap(api.PATCH("/api/v1/offices/{office_id}", { params: { path: { office_id: office.id } }, body }))
        : await unwrap(api.POST("/api/v1/offices", { body }));
      const path = { params: { path: { office_id: saved.id } } };
      if (mode === "picture" && picture) {
        const upload = new FormData();
        upload.append("file", picture);
        await unwrap(api.POST("/api/v1/offices/{office_id}/signature-image", { ...path, body: upload as never }));
      } else if ((mode === "text" || removePicture) && saved.signature_asset_id) {
        await unwrap(api.DELETE("/api/v1/offices/{office_id}/signature-image", path));
      }
      return saved;
    },
    onSuccess: (saved) => {
      toast(`${saved.name} saved.`);
      onSaved();
      onClose();
    },
  });
  const current = office?.signature_asset_id && !removePicture ? urls.get(office.signature_asset_id) : undefined;
  const preview = useMemo(() => (picture ? URL.createObjectURL(picture) : current), [picture, current]);
  useEffect(() => () => {
    if (picture && preview) URL.revokeObjectURL(preview);
  }, [picture, preview]);
  const valid = form.name.trim() && form.country && form.timezone && form.owner_name.trim() && form.owner_email.trim();
  return (
    <Modal
      title={office ? `Edit ${office.name}` : "New office"}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={save.isPending} disabled={!valid} onClick={() => save.mutate()}>
            Save
          </Button>
        </>
      }
    >
      <div className="stack">
        <div className="form-grid">
          <label className="field">
            <span>Name *</span>
            <input className="input" value={form.name} onChange={text("name")} placeholder="UAE" />
          </label>
          <label className="field">
            <span>Country *</span>
            <CountrySelect value={form.country} onChange={set("country")} />
          </label>
          <label className="field">
            <span>Time zone *</span>
            <TimeZoneSelect value={form.timezone} onChange={set("timezone")} />
            <span className="field-help">Daily caps and the office's working day follow it.</span>
          </label>
          <label className="field">
            <span>Default language</span>
            <LanguageSelect value={form.default_language} onChange={set("default_language")} />
            <span className="field-help">Used when a contact has no language.</span>
          </label>
          <label className="field">
            <span>Owner name *</span>
            <input className="input" value={form.owner_name} onChange={text("owner_name")} />
          </label>
          <label className="field">
            <span>Owner email *</span>
            <input className="input" type="email" value={form.owner_email} onChange={text("owner_email")} />
          </label>
        </div>
        <fieldset>
          <legend>Email signature (end of every email from this office)</legend>
          <div className="stack">
            <Segmented<"text" | "picture">
              label="Signature type"
              value={mode}
              onChange={setMode}
              options={[
                { id: "text", label: "Text" },
                { id: "picture", label: "Picture (PNG / JPEG)" },
              ]}
            />
            {mode === "text" ? (
              <textarea className="textarea" value={form.signature} onChange={text("signature")} placeholder={"Best regards,\nThe UAE team"} />
            ) : (
              <>
                <ImageDrop file={picture} onFile={(file) => { setPicture(file); setRemovePicture(false); }} />
                {preview && (
                  <div className="signature-preview">
                    <img src={preview} alt="Signature preview" />
                    {!picture && current && (
                      <button type="button" className="btn ghost sm" onClick={() => setRemovePicture(true)}>
                        Remove picture
                      </button>
                    )}
                  </div>
                )}
                <span className="field-help">
                  About 600 px wide works in every mail client. The Text tab signature stays for plain-text emails and AI reply drafts.
                </span>
              </>
            )}
          </div>
        </fieldset>
        <ErrorAlert error={save.error} />
      </div>
    </Modal>
  );
}

function SenderModal({ officeId, onClose, onSaved }: { officeId: string; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({ display_name: "", email: "", daily_cap: 50 });
  const save = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/senders", { body: { office_id: officeId, ...form } })),
    onSuccess: () => {
      onSaved();
      onClose();
    },
  });
  return (
    <Modal
      title="New sender mailbox"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={save.isPending} disabled={!form.display_name || !form.email} onClick={() => save.mutate()}>
            Add
          </Button>
        </>
      }
    >
      <div className="stack">
        <label className="field"><span>Display name</span><input className="input" value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} /></label>
        <label className="field"><span>Email</span><input className="input" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} /></label>
        <label className="field"><span>Daily cap</span><input className="input" type="number" min={1} value={form.daily_cap} onChange={(event) => setForm({ ...form, daily_cap: Number(event.target.value) })} /></label>
        <ErrorAlert error={save.error} />
      </div>
    </Modal>
  );
}
