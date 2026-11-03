/** Settings tabs added with the scripts library: branding, SharePoint connection, window grid. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys, useBranding, useLibrarySettings, useSenders } from "../api/queries";
import { useSession } from "../auth/session";
import { EmailFrame } from "../components/EmailFrame";
import { ImageDrop } from "../components/ImageDrop";
import { Icon } from "../components/icons";
import { Badge, Button, Card, ErrorAlert, KeyValues, Loading, StatusBadge, useToast } from "../components/ui";
import { formatDateTime } from "../lib/format";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Hours (0–23) inside a `HH:MM`–`HH:MM` window; windows may wrap past midnight. */
export function hoursInWindow(start: string, end: string): Set<number> {
  const toHour = (value: string) => Number(value.slice(0, 2)) + Number(value.slice(3, 5)) / 60;
  const from = toHour(start);
  const to = toHour(end);
  const hours = new Set<number>();
  for (let hour = 0; hour < 24; hour += 1) {
    const middle = hour + 0.5;
    const inside = from <= to ? middle >= from && middle < to : middle >= from || middle < to;
    if (inside) hours.add(hour);
  }
  return hours;
}

export function WindowGrid({ start, end }: { start: string; end: string }) {
  const hours = hoursInWindow(start, end);
  return (
    <div className="stack tight">
      <div className="week-grid" aria-label={`Sending window ${start} to ${end}, business days`}>
        <span />
        {Array.from({ length: 24 }, (_, hour) => (
          <span key={hour} className="hour">{hour % 3 === 0 ? hour : ""}</span>
        ))}
        {DAYS.map((day, index) => (
          <div key={day} style={{ display: "contents" }}>
            <span>{day}</span>
            {Array.from({ length: 24 }, (_, hour) => (
              <span key={hour} className={index >= 5 ? "slot weekend" : hours.has(hour) ? "slot on" : "slot"} title={`${day} ${String(hour).padStart(2, "0")}:00`} />
            ))}
          </div>
        ))}
      </div>
      <span className="small muted">
        Recipient's local time. Weekends and holidays follow each country's calendar (hatched = weekend in most countries).
      </span>
    </div>
  );
}

/** "ca, US; mx" → ["CA", "US", "MX"] (invalid entries dropped); unchanged when not edited. */
export function parseCountryCodes(text: string | null, current: string[]): string[] {
  if (text === null) return current;
  const codes = text
    .split(/[\s,;]+/)
    .map((code) => code.trim().toUpperCase())
    .filter((code) => /^[A-Z]{2}$/.test(code));
  return [...new Set(codes)];
}

const BRAND_SLOTS: { kind: "logo" | "qr" | "promo"; key: "logo_asset_id" | "qr_asset_id" | "promo_asset_id"; title: string; help: string }[] = [
  { kind: "logo", key: "logo_asset_id", title: "Logo", help: "Middle of the signature (about 300 px wide)." },
  { kind: "qr", key: "qr_asset_id", title: "QR code", help: "Under the agent's name, e.g. a vCard or website QR." },
  { kind: "promo", key: "promo_asset_id", title: "Promo banner", help: "Across the bottom, e.g. a warehouse offer (600 px wide)." },
];

function BrandImages({
  draft,
  setDraft,
  admin,
  assets,
}: {
  draft: Schemas["TenantBranding"];
  setDraft: (next: Schemas["TenantBranding"]) => void;
  admin: boolean;
  assets: Schemas["AssetResponse"][];
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [pending, setPending] = useState<{ kind: string; file: File } | null>(null);
  const upload = useMutation({
    mutationFn: ({ kind, file }: { kind: string; file: File }) => {
      const form = new FormData();
      form.append("file", file);
      form.append("kind", kind);
      return unwrap(api.POST("/api/v1/scripts/brand-images", { body: form as never }));
    },
    onSuccess: (asset) => {
      toast(`${asset.filename} saved to the library's Brand folder.`);
      setPending(null);
      const slot = BRAND_SLOTS.find((item) => item.kind === asset.kind);
      if (slot) setDraft({ ...draft, [slot.key]: asset.id });
      else setDraft({ ...draft, badge_asset_ids: [...(draft.badge_asset_ids ?? []), asset.id] });
      void queryClient.invalidateQueries({ queryKey: keys.assets });
      void queryClient.invalidateQueries({ queryKey: keys.branding });
    },
  });
  const byKind = (kind: string) => assets.filter((asset) => asset.kind === kind);
  const url = (id: string | null | undefined) => assets.find((asset) => asset.id === id)?.url;
  return (
    <fieldset>
      <legend>Brand images</legend>
      <div className="stack">
        {BRAND_SLOTS.map((slot) => {
          const current = url(draft[slot.key]);
          return (
            <div key={slot.kind} className="brand-slot">
              <div className="brand-thumb">{current ? <img src={current} alt={slot.title} /> : <Icon name="image" />}</div>
              <div className="stack tight" style={{ flex: 1, minWidth: 0 }}>
                <strong>{slot.title}</strong>
                <select className="select" disabled={!admin} value={draft[slot.key] ?? ""} onChange={(event) => setDraft({ ...draft, [slot.key]: event.target.value || null })}>
                  <option value="">{slot.kind === "logo" ? "No logo (company name as text)" : "None"}</option>
                  {byKind(slot.kind).map((asset) => (
                    <option key={asset.id} value={asset.id}>{asset.filename}</option>
                  ))}
                </select>
                <span className="field-help">{slot.help}</span>
                {admin && (
                  <label className="btn sm" style={{ alignSelf: "flex-start" }}>
                    <Icon name="upload" /> Upload
                    <input type="file" hidden accept="image/png,image/jpeg,image/gif,image/webp" onChange={(event) => event.target.files?.[0] && upload.mutate({ kind: slot.kind, file: event.target.files[0] })} />
                  </label>
                )}
              </div>
            </div>
          );
        })}
        <div className="stack tight">
          <strong>Badges (CFIB, TIA, WCA, IATA…)</strong>
          <div className="chips">
            {byKind("badge").map((asset) => {
              const on = (draft.badge_asset_ids ?? []).includes(asset.id);
              return (
                <button
                  key={asset.id}
                  type="button"
                  disabled={!admin}
                  className={on ? "chip-toggle on" : "chip-toggle"}
                  onClick={() =>
                    setDraft({
                      ...draft,
                      badge_asset_ids: on ? (draft.badge_asset_ids ?? []).filter((id) => id !== asset.id) : [...(draft.badge_asset_ids ?? []), asset.id],
                    })
                  }
                >
                  <img src={asset.url} alt="" style={{ height: 16, verticalAlign: "middle", marginRight: 4 }} />
                  {asset.filename}
                </button>
              );
            })}
            {byKind("badge").length === 0 && <span className="field-help">No badges yet.</span>}
          </div>
          {admin && (
            <div className="row">
              <ImageDrop file={pending?.kind === "badge" ? pending.file : null} onFile={(file) => setPending({ kind: "badge", file })} label="Drop a badge" />
              {pending?.kind === "badge" && (
                <Button size="sm" variant="primary" busy={upload.isPending} onClick={() => upload.mutate(pending)}>
                  Add badge
                </Button>
              )}
            </div>
          )}
        </div>
        <ErrorAlert error={upload.error} />
        <span className="field-help">Images are saved in the library's <code>Brand/</code> folder (logo.png, qr.png, promo.png, badge-*.png), so SharePoint keeps the master copy.</span>
      </div>
    </fieldset>
  );
}

export function BrandingSettings() {
  const branding = useBranding();
  const senders = useSenders();
  const assets = useQuery({ queryKey: keys.assets, queryFn: () => unwrap(api.GET("/api/v1/scripts/assets")) });
  const { can } = useSession();
  const admin = can("admin:manage");
  const toast = useToast();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Schemas["TenantBranding"] | null>(null);
  // Typed as free text ("CA, US") and parsed on save, so half-typed codes are not dropped.
  const [originsText, setOriginsText] = useState<string | null>(null);
  useEffect(() => {
    if (branding.data && draft === null) setDraft(branding.data);
  }, [branding.data, draft]);
  const sample = senders.data?.find((agent) => agent.is_active && agent.signature_mode === "generated") ?? senders.data?.[0];
  const signature = useQuery({
    queryKey: [...keys.branding, "signature", sample?.id, branding.dataUpdatedAt],
    queryFn: () => unwrap(api.GET("/api/v1/senders/{sender_id}/signature", { params: { path: { sender_id: sample?.id ?? "" } } })),
    enabled: Boolean(sample),
  });
  const save = useMutation({
    mutationFn: (body: Schemas["TenantBranding"]) => unwrap(api.PUT("/api/v1/settings/branding", { body })),
    onSuccess: (next) => {
      toast("Branding saved: generated signatures use it from the next email.");
      setDraft(next);
      void queryClient.invalidateQueries({ queryKey: keys.branding });
    },
  });
  if (!draft) return <Loading />;
  const field = (key: keyof Schemas["TenantBranding"], label: string, placeholder = "") => (
    <label className="field">
      <span>{label}</span>
      <input
        className="input"
        disabled={!admin}
        placeholder={placeholder}
        value={(draft[key] as string | null | undefined) ?? ""}
        onChange={(event) => setDraft({ ...draft, [key]: event.target.value || null })}
      />
    </label>
  );
  return (
    <div className="grid two" style={{ alignItems: "start" }}>
      <Card title="Signature branding">
        <div className="stack">
          <div className="form-grid">
            {field("company_name", "Company name", "Your company name")}
            {field("tagline", "Tagline", "Beyond Your Destination")}
          </div>
          {field("offices_strip", "Offices strip", "Canada | USA | France | Spain | UAE")}
          {field("address", "Address")}
          <div className="form-grid">
            {field("phone", "Main phone", "905-677-5000")}
            {field("fax", "Fax", "905-677-4000")}
            {field("website", "Website", "www.example.com")}
            {field("promo_link", "Promo banner link", "www.example.com/warehouse")}
          </div>
          <div className="form-grid">
            <label className="field">
              <span>Primary colour</span>
              <input className="input" type="color" disabled={!admin} value={draft.primary_color ?? "#1f3f8f"} onChange={(event) => setDraft({ ...draft, primary_color: event.target.value })} />
            </label>
            <label className="field">
              <span>Accent colour</span>
              <input className="input" type="color" disabled={!admin} value={draft.accent_color ?? "#6cbf4a"} onChange={(event) => setDraft({ ...draft, accent_color: event.target.value })} />
            </label>
          </div>
          <label className="field">
            <span>Lanes start from</span>
            <input
              className="input"
              disabled={!admin}
              placeholder="CA, US"
              value={originsText ?? (draft.lane_origin_countries ?? []).join(", ")}
              onChange={(event) => setOriginsText(event.target.value)}
            />
            <span className="field-help">Country codes of the sales desk. The first is the origin of every lane on the dashboard map.</span>
          </label>
          <BrandImages draft={draft} setDraft={setDraft} admin={admin} assets={assets.data ?? []} />
          <ErrorAlert error={save.error} />
          {admin && (
            <div>
              <Button variant="primary" busy={save.isPending} onClick={() => save.mutate({ ...draft, lane_origin_countries: parseCountryCodes(originsText, draft.lane_origin_countries ?? []) })}>
                Save branding
              </Button>
            </div>
          )}
        </div>
      </Card>
      <Card title={sample ? `Signature preview — ${sample.display_name}` : "Signature preview"}>
        {!sample && <p className="muted">Add an agent to see a signature.</p>}
        {signature.isLoading && <Loading />}
        <ErrorAlert error={signature.error} />
        {signature.data && <EmailFrame html={signature.data.html} minHeight={180} />}
        <p className="small muted" style={{ marginBottom: 0 }}>Saved changes show here after saving.</p>
      </Card>
    </div>
  );
}

export function LibrarySettings() {
  const settings = useLibrarySettings();
  const { can } = useSession();
  const admin = can("admin:manage");
  const toast = useToast();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Schemas["LibrarySettingsRequest"] | null>(null);
  useEffect(() => {
    if (settings.data && draft === null) {
      const { provider, site_url, library_name, root_folder, azure_tenant_id, client_id } = settings.data;
      setDraft({ provider, site_url, library_name, root_folder, azure_tenant_id, client_id });
    }
  }, [settings.data, draft]);
  const save = useMutation({
    mutationFn: (body: Schemas["LibrarySettingsRequest"]) => unwrap(api.PUT("/api/v1/settings/library", { body })),
    onSuccess: () => {
      toast("Library settings saved.");
      void queryClient.invalidateQueries({ queryKey: keys.library });
      void queryClient.invalidateQueries({ queryKey: keys.scripts });
    },
  });
  const test = useMutation({ mutationFn: () => unwrap(api.POST("/api/v1/settings/library/test")) });
  if (!settings.data || !draft) return <Loading />;
  const data = settings.data;
  const sharepoint = draft.provider === "sharepoint";
  const text = (key: "site_url" | "library_name" | "root_folder" | "azure_tenant_id" | "client_id", label: string, placeholder: string) => (
    <label className="field">
      <span>{label}</span>
      <input className="input" disabled={!admin} placeholder={placeholder} value={draft[key] ?? ""} onChange={(event) => setDraft({ ...draft, [key]: event.target.value || null })} />
    </label>
  );
  return (
    <div className="grid two" style={{ alignItems: "start" }}>
      <Card title="Scripts library">
        <div className="stack">
          <div className="row">
            {(["local", "sharepoint"] as const).map((provider) => (
              <label key={provider} className="checkbox card" style={{ padding: "10px 12px", flex: 1, borderColor: draft.provider === provider ? "var(--accent)" : undefined }}>
                <input type="radio" name="provider" disabled={!admin} checked={draft.provider === provider} onChange={() => setDraft({ ...draft, provider })} />
                <Icon name={provider === "local" ? "file" : "cloud"} />
                <span>
                  <strong>{provider === "local" ? "Local folder" : "SharePoint"}</strong>
                  <span className="field-help" style={{ display: "block" }}>
                    {provider === "local" ? "For development, before the Azure app exists." : "Microsoft Graph, the master copy for marketing."}
                  </span>
                </span>
              </label>
            ))}
          </div>
          {sharepoint && (
            <>
              {text("site_url", "Site URL", "https://contoso.sharepoint.com/sites/Marketing")}
              <div className="form-grid">
                {text("library_name", "Document library", "Documents")}
                {text("root_folder", "Folder", "LaneReach")}
              </div>
              <div className="form-grid">
                {text("azure_tenant_id", "Azure tenant ID", "00000000-…")}
                {text("client_id", "App (client) ID", "00000000-…")}
              </div>
              <div className={data.client_secret_configured ? "alert success small" : "alert warning small"}>
                {data.client_secret_configured
                  ? "Client secret found in SHAREPOINT_CLIENT_SECRETS."
                  : "No client secret yet: add it to SHAREPOINT_CLIENT_SECRETS on the server (it is never stored in the database or shown here)."}
              </div>
            </>
          )}
          <ErrorAlert error={save.error} />
          {admin && (
            <div className="row">
              <Button variant="primary" busy={save.isPending} onClick={() => save.mutate(draft)}>
                Save
              </Button>
              <Button busy={test.isPending} onClick={() => test.mutate()}>
                <Icon name="sync" /> Test connection
              </Button>
            </div>
          )}
          <ErrorAlert error={test.error} />
          {test.data && (
            <div className={test.data.ok ? "alert success small" : "alert error small"}>
              {test.data.detail} {test.data.ok && `(${test.data.files} files found)`}
            </div>
          )}
        </div>
      </Card>
      <div className="stack">
        <Card title="Last sync">
          <KeyValues
            items={[
              ["Status", <StatusBadge key="status" value={data.last_sync_status} />],
              ["When", data.last_sync_at ? formatDateTime(data.last_sync_at) : "never"],
              ["Summary", Object.keys(data.last_sync_summary).length ? Object.entries(data.last_sync_summary).map(([key, value]) => `${key.replaceAll("_", " ")}: ${String(value)}`).join(" · ") : "—"],
              ["Error", data.last_sync_error ?? "—"],
            ]}
          />
        </Card>
        <Card title="Connecting SharePoint">
          <ol className="small" style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 6 }}>
            <li>In Microsoft Entra ID, register an app and add the Graph application permission <Badge tone="blue">Sites.Selected</Badge>; an admin grants consent.</li>
            <li>Grant the app write access to the marketing site only (Sites.Selected keeps it off every other site).</li>
            <li>Create a client secret and put it in <code>SHAREPOINT_CLIENT_SECRETS</code> on the server, keyed by this tenant's slug.</li>
            <li>Fill in the site URL, tenant ID and client ID here, save, and test the connection.</li>
            <li>In the library folder, create <code>Scripts/Air</code>, <code>Scripts/Ocean</code>, <code>Banners/Air</code>, <code>Banners/Ocean</code> and <code>Brand</code>.</li>
          </ol>
        </Card>
      </div>
    </div>
  );
}
