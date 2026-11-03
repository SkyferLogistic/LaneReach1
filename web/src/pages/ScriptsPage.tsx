/**
 * Scripts: the Word scripts kept in SharePoint, as a script × language matrix.
 *
 * SharePoint is the master copy: marketing edits the Word files there and LaneReach picks the
 * changes up (every 5 minutes or "Sync now"). Uploads and scripts started here are written into
 * the library first. The preview renders exactly what a contact in a given country receives from
 * a given agent.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys, useCountries, useLibrarySettings, useScriptMatrix, useSenders } from "../api/queries";
import { useSession } from "../auth/session";
import { EmailFrame } from "../components/EmailFrame";
import { Dropzone } from "../components/Dropzone";
import { Icon } from "../components/icons";
import type { IconName } from "../components/icons";
import { Page } from "../components/Layout";
import { SkeletonRows } from "../components/motion";
import {
  Badge,
  Button,
  Card,
  Drawer,
  Empty,
  ErrorAlert,
  KeyValues,
  LinePill,
  Loading,
  Modal,
  PageHeader,
  Segmented,
  StatusBadge,
  Tabs,
  useErrorToast,
  useToast,
} from "../components/ui";
import { formatDateTime, formatRelative, percent } from "../lib/format";
import { CELL_LABELS, SERVICE_LINES, SERVICE_LINE_LABELS, countryFlag, scriptCellState, scriptCoverage } from "../lib/freight";
import type { CellState, ServiceLine } from "../lib/freight";
import { ServiceLineOptions } from "../components/pickers";

type Matrix = Schemas["ScriptMatrixResponse"];
type Script = Schemas["ScriptResponse"];
type Version = Schemas["ScriptVersionResponse"];
type Language = Schemas["LanguageColumnResponse"];
type LineFilter = "all" | ServiceLine;

const CELL_ICONS: Record<CellState, IconName> = {
  active: "check",
  ai_draft: "sparkles",
  error: "alert",
  inactive: "x",
  missing: "plus",
  held: "pause",
};

const MAX_TRANSLATIONS = 6;

function formData(fields: Record<string, string | Blob | null | undefined>): FormData {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    if (value !== null && value !== undefined && value !== "") form.append(key, value);
  }
  return form;
}

interface UploadTarget {
  scriptName?: string;
  serviceLine?: ServiceLine;
  language?: string;
}

export function ScriptsPage() {
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState<"scripts" | "banners">("scripts");
  const [line, setLine] = useState<LineFilter>("all");
  const [search, setSearch] = useState("");
  const [upload, setUpload] = useState<UploadTarget | null>(params.get("upload") === "1" ? {} : null);
  const [blank, setBlank] = useState(false);
  const [open, setOpen] = useState<{ script: Script; version: Version } | null>(null);
  const [translate, setTranslate] = useState<Script | null>(null);
  const matrix = useScriptMatrix();
  const matrixLines = SERVICE_LINES.filter(
    (option) => option === "air" || option === "ocean" || (matrix.data?.scripts ?? []).some((script) => script.service_line === option),
  );
  const { can } = useSession();
  const writer = can("workflows:write");
  const toast = useToast();
  const onError = useErrorToast();
  const queryClient = useQueryClient();

  useEffect(() => {
    if (params.has("upload")) setParams({}, { replace: true });
  }, [params, setParams]);

  const sync = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/scripts/sync")),
    onSuccess: (summary) => {
      const parts = [`${summary.imported} imported`, `${summary.unchanged} unchanged`];
      if (summary.deactivated) parts.push(`${summary.deactivated} removed`);
      if (summary.failed) parts.push(`${summary.failed} failed`);
      toast(`Library synced: ${parts.join(", ")}.`, summary.failed ? "error" : "success");
      void queryClient.invalidateQueries({ queryKey: keys.scripts });
      void queryClient.invalidateQueries({ queryKey: keys.assets });
    },
    onError,
  });

  const scripts = useMemo(() => {
    const all = matrix.data?.scripts ?? [];
    const needle = search.trim().toLowerCase();
    return all.filter(
      (script) =>
        (line === "all" || script.service_line === line) &&
        (!needle || script.name.toLowerCase().includes(needle) || script.key.includes(needle)),
    );
  }, [matrix.data, line, search]);

  return (
    <Page>
      <PageHeader
        eyebrow={<><Icon name="cloud" /> SharePoint is the master copy</>}
        title="Scripts"
        description="Every outreach email as a Word file per language. Edit them in SharePoint; LaneReach picks up changes automatically. Click a cell to preview exactly what a contact receives."
        actions={
          writer && (
            <>
              <Button onClick={() => sync.mutate()} busy={sync.isPending}>
                <Icon name="sync" /> Sync now
              </Button>
              <Button onClick={() => setBlank(true)}>
                <Icon name="plus" /> New from text
              </Button>
              <Button variant="primary" onClick={() => setUpload({})}>
                <Icon name="upload" /> Upload Word / PDF
              </Button>
            </>
          )
        }
      />
      <LibraryStrip matrix={matrix.data} />
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "scripts", label: "Scripts × languages" },
          { id: "banners", label: "Banners & brand images" },
        ]}
      />
      {tab === "scripts" && (
        <Card
          title={
            <div className="row">
              <Segmented<LineFilter>
                label="Service line"
                value={line}
                onChange={setLine}
                options={[
                  { id: "all", label: "All" },
                  ...matrixLines.map((option) => ({ id: option, label: SERVICE_LINE_LABELS[option].split(" ")[0] ?? option })),
                  { id: "any", label: "General" },
                ]}
              />
              <input className="input" style={{ width: 220 }} placeholder="Find a script…" value={search} onChange={(event) => setSearch(event.target.value)} aria-label="Find a script" />
            </div>
          }
          actions={<CellLegend />}
          flush
        >
          {matrix.isLoading && <SkeletonRows rows={5} />}
          <ErrorAlert error={matrix.error} />
          {matrix.data && scripts.length === 0 && (
            <Empty title={matrix.data.scripts.length ? "No script matches" : "No scripts yet"}>
              {matrix.data.scripts.length ? "Try another filter." : "Upload the first Word script, or put Word files in the library's Scripts folder and sync."}
            </Empty>
          )}
          {matrix.data && scripts.length > 0 && (
            <ScriptMatrix
              languages={matrix.data.languages}
              scripts={scripts}
              canWrite={writer}
              onOpen={(script, version) => setOpen({ script, version })}
              onMissing={(script, language) =>
                writer && setUpload({ scriptName: script.name, serviceLine: script.service_line, language: language.code })
              }
              onTranslate={setTranslate}
            />
          )}
        </Card>
      )}
      {tab === "banners" && <BannersPanel canWrite={writer} />}
      {upload && matrix.data && (
        <UploadModal target={upload} matrix={matrix.data} onClose={() => setUpload(null)} />
      )}
      {blank && matrix.data && <BlankScriptModal matrix={matrix.data} onClose={() => setBlank(false)} />}
      {translate && matrix.data && (
        <TranslateModal script={translate} languages={matrix.data.languages} onClose={() => setTranslate(null)} />
      )}
      {open && matrix.data && (
        <ScriptDrawer
          script={open.script}
          version={open.version}
          language={matrix.data.languages.find((item) => item.code === open.version.language)}
          onClose={() => setOpen(null)}
        />
      )}
    </Page>
  );
}

function LibraryStrip({ matrix }: { matrix: Matrix | undefined }) {
  const settings = useLibrarySettings();
  if (!matrix) return null;
  const library = matrix.library;
  const versions = matrix.scripts.flatMap((script) => Object.values(script.versions));
  const live = versions.filter((version) => version.status === "active").length;
  const drafts = versions.filter((version) => version.status === "ai_draft").length;
  const covered = new Set(versions.filter((version) => version.status === "active").map((version) => version.language));
  const held = matrix.languages.filter((language) => language.strict && !covered.has(language.code));
  return (
    <div className="grid stats" style={{ marginBottom: 16 }}>
      <div className="card stat">
        <div className="stat-label"><Icon name="cloud" /> Library</div>
        <div className="row" style={{ marginTop: 6 }}>
          <strong style={{ fontFamily: "var(--display)", fontSize: 18 }}>{library.provider === "sharepoint" ? "SharePoint" : "Local folder"}</strong>
          <StatusBadge value={library.last_sync_status} />
        </div>
        <div className="small muted truncate" title={library.location}>{library.location}</div>
        <div className="small muted">
          Synced {library.last_sync_at ? formatRelative(library.last_sync_at) : "never"}
          {settings.data?.provider === "local" && " · connect SharePoint in Settings"}
        </div>
        {library.last_sync_error && <div className="small" style={{ color: "var(--danger)" }}>{library.last_sync_error}</div>}
      </div>
      <div className="card stat">
        <div className="stat-label"><Icon name="scripts" /> Scripts</div>
        <div className="stat-value">{matrix.scripts.length}</div>
        <div className="small muted">{live} live language versions</div>
      </div>
      <div className="card stat">
        <div className="stat-label"><Icon name="globe" /> Languages covered</div>
        <div className="stat-value">{covered.size}<span className="muted" style={{ fontSize: 16 }}> / {matrix.languages.length}</span></div>
        <div className="small muted">from the countries table</div>
      </div>
      <div className="card stat">
        <div className="stat-label"><Icon name="sparkles" /> Needs attention</div>
        <div className="stat-value">{drafts + held.length}</div>
        <div className="small muted">
          {drafts} AI draft{drafts === 1 ? "" : "s"} · {held.length} held language{held.length === 1 ? "" : "s"}
          {held.length > 0 && ` (${held.map((item) => item.name).join(", ")})`}
        </div>
      </div>
    </div>
  );
}

function CellLegend() {
  const states: CellState[] = ["active", "ai_draft", "missing", "held", "error"];
  return (
    <div className="legend">
      {states.map((state) => (
        <span key={state} title={CELL_LABELS[state]}>
          <span className={`cell ${state}`} style={{ width: 18, height: 18, borderRadius: 5, cursor: "default" }}>
            <Icon name={CELL_ICONS[state]} style={{ width: 11, height: 11 }} />
          </span>
          {{ active: "Live", ai_draft: "AI draft", missing: "Missing", held: "Held", error: "Error", inactive: "Removed" }[state]}
        </span>
      ))}
    </div>
  );
}

function ScriptMatrix({
  languages,
  scripts,
  canWrite,
  onOpen,
  onMissing,
  onTranslate,
}: {
  languages: Language[];
  scripts: Script[];
  canWrite: boolean;
  onOpen: (script: Script, version: Version) => void;
  onMissing: (script: Script, language: Language) => void;
  onTranslate: (script: Script) => void;
}) {
  const codes = languages.map((language) => language.code);
  return (
    <div className="matrix-wrap">
      <table className="matrix">
        <thead>
          <tr>
            <th className="script-col">Script</th>
            {languages.map((language) => (
              <th key={language.code} title={`${language.name}: ${language.countries.join(", ")}${language.strict ? " (held without a script)" : ""}`}>
                {language.name}
                <span className="lang-flags">{language.countries.slice(0, 6).map(countryFlag).join("")}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {scripts.map((script) => {
            const coverage = scriptCoverage(script.versions, codes);
            return (
              <tr key={script.key}>
                <td className="script-col">
                  <div className="stack tight">
                    <strong className="truncate" title={script.name}>{script.name}</strong>
                    <div className="row" style={{ gap: 6 }}>
                      <LinePill line={script.service_line} />
                      <span className="small muted">{percent(coverage)} covered</span>
                    </div>
                    {canWrite && coverage < 1 && (
                      <div>
                        <Button size="sm" variant="ghost" onClick={() => onTranslate(script)}>
                          <Icon name="translate" /> Translate missing
                        </Button>
                      </div>
                    )}
                  </div>
                </td>
                {languages.map((language) => {
                  const version = script.versions[language.code];
                  const state = scriptCellState(version, language.strict);
                  const label = `${script.name} · ${language.name}: ${CELL_LABELS[state]}`;
                  return (
                    <td key={language.code}>
                      <button
                        type="button"
                        className={`cell ${state}`}
                        title={version ? `${label}\n“${version.subject}”` : label}
                        aria-label={label}
                        disabled={!version && !canWrite}
                        onClick={() => (version ? onOpen(script, version) : onMissing(script, language))}
                      >
                        <Icon name={CELL_ICONS[state]} />
                      </button>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}


function LanguageSelect({ languages, value, onChange }: { languages: Language[]; value: string; onChange: (code: string) => void }) {
  const known = languages.some((language) => language.code === value);
  return (
    <label className="field">
      <span>Language</span>
      <select className="select" value={known ? value : "__other"} onChange={(event) => onChange(event.target.value === "__other" ? "" : event.target.value)}>
        {languages.map((language) => (
          <option key={language.code} value={language.code}>
            {language.name} ({language.code}) {language.countries.slice(0, 5).map(countryFlag).join("")}
          </option>
        ))}
        <option value="__other">Other language code…</option>
      </select>
      {!known && (
        <input className="input" placeholder="e.g. ja or pt-br" value={value} onChange={(event) => onChange(event.target.value.trim().toLowerCase())} />
      )}
    </label>
  );
}

function ServiceLineSelect({ value, onChange }: { value: ServiceLine; onChange: (line: ServiceLine) => void }) {
  return (
    <label className="field">
      <span>Service line</span>
      <select className="select" value={value} onChange={(event) => onChange(event.target.value as ServiceLine)}>
        {SERVICE_LINES.map((option) => (
          <option key={option} value={option}>
            {SERVICE_LINE_LABELS[option]} (Scripts/{option.charAt(0).toUpperCase() + option.slice(1)})
          </option>
        ))}
        <option value="any">General (Scripts/General)</option>
      </select>
    </label>
  );
}

function UploadModal({ target, matrix, onClose }: { target: UploadTarget; matrix: Matrix; onClose: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState(target.scriptName ?? "");
  const [line, setLine] = useState<ServiceLine>(target.serviceLine ?? "air");
  const [language, setLanguage] = useState(target.language ?? matrix.languages[0]?.code ?? "en");
  const toast = useToast();
  const queryClient = useQueryClient();
  const upload = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/scripts/upload", {
          body: formData({ file, script_name: name.trim(), service_line: line, language }) as never,
        }),
      ),
    onSuccess: (template) => {
      toast(
        template
          ? `Saved to the library and imported: ${template.name} (${template.language}).`
          : "PDF saved to the library as the reference copy.",
      );
      void queryClient.invalidateQueries({ queryKey: keys.scripts });
      onClose();
    },
  });
  const pdf = file?.name.toLowerCase().endsWith(".pdf");
  return (
    <Modal
      title="Upload a script"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={upload.isPending} disabled={!file || !name.trim() || !language} onClick={() => upload.mutate()}>
            <Icon name="upload" /> Save to library
          </Button>
        </>
      }
    >
      <div className="stack">
        <Dropzone file={file} onFile={setFile} accept=".docx,.pdf" hint="Word (.docx) is imported; a PDF is kept as the reference copy. Up to 10 MB." />
        <label className="field">
          <span>Script name</span>
          <input className="input" list="script-names" value={name} onChange={(event) => setName(event.target.value)} placeholder="USA-Canada air to country" />
          <datalist id="script-names">
            {matrix.scripts.map((script) => (
              <option key={script.key} value={script.name} />
            ))}
          </datalist>
          <span className="field-help">Same name + line = another language of that script.</span>
        </label>
        <div className="form-grid">
          <ServiceLineSelect value={line} onChange={setLine} />
          <LanguageSelect languages={matrix.languages} value={language} onChange={setLanguage} />
        </div>
        <div className="alert info small">
          Saved as <code>Scripts/{line === "any" ? "General" : line === "air" ? "Air" : "Ocean"}/{name.trim() || "<name>"}/{language || "<language>"}.{pdf ? "pdf" : "docx"}</code>.
          Placeholders: {"{first_name|there} {to_country} {network} {banner} {signature} {unsubscribe_link|Unsubscribe here}"}.
        </div>
        <ErrorAlert error={upload.error} />
      </div>
    </Modal>
  );
}

const BLANK_BODY = `Hi {first_name|there},

Hope all is well.

Just reaching out to see if you have any upcoming air freight enquiries from the USA or Canada {to_country}.

{banner}

We look forward to quoting your next shipment.

Best regards,

{signature}

You're receiving this email as a fellow {network} member.

{unsubscribe_link|Unsubscribe here}`;

function BlankScriptModal({ matrix, onClose }: { matrix: Matrix; onClose: () => void }) {
  const [name, setName] = useState("");
  const [line, setLine] = useState<ServiceLine>("air");
  const [language, setLanguage] = useState("en");
  const [subject, setSubject] = useState("Air freight from the USA or Canada {to_country}");
  const [body, setBody] = useState(BLANK_BODY);
  const toast = useToast();
  const queryClient = useQueryClient();
  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/scripts/blank", {
          body: { script_name: name.trim(), service_line: line, language, subject, body },
        }),
      ),
    onSuccess: (template) => {
      toast(`Word file created in the library: ${template.name} (${template.language}).`);
      void queryClient.invalidateQueries({ queryKey: keys.scripts });
      onClose();
    },
  });
  return (
    <Modal
      title="New script from text"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={create.isPending} disabled={!name.trim() || !subject.trim() || !body.trim()} onClick={() => create.mutate()}>
            Create Word file
          </Button>
        </>
      }
    >
      <div className="stack">
        <p className="small muted" style={{ margin: 0 }}>
          LaneReach writes a Word document into the library; after that, edit it in SharePoint like any other script.
        </p>
        <label className="field">
          <span>Script name</span>
          <input className="input" value={name} onChange={(event) => setName(event.target.value)} placeholder="Ocean intro" />
        </label>
        <div className="form-grid">
          <ServiceLineSelect value={line} onChange={setLine} />
          <LanguageSelect languages={matrix.languages} value={language} onChange={setLanguage} />
        </div>
        <label className="field">
          <span>Subject</span>
          <input className="input" value={subject} onChange={(event) => setSubject(event.target.value)} />
        </label>
        <label className="field">
          <span>Body</span>
          <textarea className="textarea" style={{ minHeight: 260 }} value={body} onChange={(event) => setBody(event.target.value)} />
          <span className="field-help">Blank lines separate paragraphs.</span>
        </label>
        <ErrorAlert error={create.error} />
      </div>
    </Modal>
  );
}

function TranslateModal({ script, languages, onClose }: { script: Script; languages: Language[]; onClose: () => void }) {
  const missing = languages.filter((language) => !script.versions[language.code]);
  const [chosen, setChosen] = useState<string[]>(missing.slice(0, MAX_TRANSLATIONS).map((language) => language.code));
  const toast = useToast();
  const queryClient = useQueryClient();
  const run = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/scripts/{key}/translate", { params: { path: { key: script.key } }, body: { languages: chosen } })),
    onSuccess: (outcomes) => {
      const done = outcomes.filter((outcome) => outcome.template_id).length;
      const failed = outcomes.filter((outcome) => outcome.error);
      toast(
        `${done} AI draft${done === 1 ? "" : "s"} saved to the library.` +
          (failed.length ? `\nFailed: ${failed.map((outcome) => `${outcome.language} (${outcome.error})`).join(", ")}` : ""),
        failed.length ? "error" : "success",
      );
      void queryClient.invalidateQueries({ queryKey: keys.scripts });
      onClose();
    },
  });
  const toggle = (code: string) =>
    setChosen((current) =>
      current.includes(code) ? current.filter((item) => item !== code) : current.length < MAX_TRANSLATIONS ? [...current, code] : current,
    );
  return (
    <Modal
      title={`Translate “${script.name}”`}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={run.isPending} disabled={chosen.length === 0} onClick={() => run.mutate()}>
            <Icon name="translate" /> Create {chosen.length} AI draft{chosen.length === 1 ? "" : "s"}
          </Button>
        </>
      }
    >
      <div className="stack">
        <div className="alert info small">
          The AI translates the English version and keeps every placeholder. Drafts are saved as Word files in the library and are <strong>never sent until someone approves them</strong>. Up to {MAX_TRANSLATIONS} at a time.
        </div>
        {missing.length === 0 ? (
          <Empty title="Every language has a version" />
        ) : (
          <div className="chips">
            {missing.map((language) => (
              <button key={language.code} type="button" className={chosen.includes(language.code) ? "chip-toggle on" : "chip-toggle"} onClick={() => toggle(language.code)}>
                {language.countries.slice(0, 3).map(countryFlag).join("")} {language.name}
              </button>
            ))}
          </div>
        )}
        <ErrorAlert error={run.error} />
      </div>
    </Modal>
  );
}

function ScriptDrawer({
  script,
  version,
  language,
  onClose,
}: {
  script: Script;
  version: Version;
  language: Language | undefined;
  onClose: () => void;
}) {
  const countries = useCountries();
  const senders = useSenders();
  const { can } = useSession();
  const toast = useToast();
  const queryClient = useQueryClient();
  const detail = useQuery({
    queryKey: [...keys.scripts, "template", version.template_id],
    queryFn: () => unwrap(api.GET("/api/v1/scripts/templates/{template_id}", { params: { path: { template_id: version.template_id } } })),
  });
  const countryOptions = useMemo(() => {
    const all = countries.data ?? [];
    const own = all.filter((country) => country.language === version.language);
    return own.length ? own : all;
  }, [countries.data, version.language]);
  const [country, setCountry] = useState<string>(language?.countries[0] ?? "");
  const agents = (senders.data ?? []).filter(
    (agent) => agent.is_active && (script.service_line === "any" || agent.service_line === script.service_line || agent.service_line === "any"),
  );
  const [senderId, setSenderId] = useState("");
  const preview = useQuery({
    queryKey: [...keys.scripts, "preview", version.template_id, country, senderId],
    queryFn: () =>
      unwrap(
        api.POST("/api/v1/scripts/templates/{template_id}/preview", {
          params: { path: { template_id: version.template_id } },
          body: { country: country || null, sender_id: senderId || null },
        }),
      ),
  });
  const approve = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/scripts/templates/{template_id}/approve", { params: { path: { template_id: version.template_id } } })),
    onSuccess: () => {
      toast("Approved: this version is now used for sending.");
      void queryClient.invalidateQueries({ queryKey: keys.scripts });
      onClose();
    },
  });
  const template = detail.data;
  const rtl = version.language === "ar";

  return (
    <Drawer
      wide
      title={
        <span className="row">
          {script.name} <Badge tone="blue">{language?.name ?? version.language}</Badge> <StatusBadge value={version.status} />
        </span>
      }
      onClose={onClose}
      footer={
        <>
          {version.source_web_url && (
            <a className="btn" href={version.source_web_url} target="_blank" rel="noreferrer">
              <Icon name="external" /> Open in Word
            </a>
          )}
          {version.pdf_web_url && (
            <a className="btn" href={version.pdf_web_url} target="_blank" rel="noreferrer">
              <Icon name="file" /> PDF copy
            </a>
          )}
          {version.status === "ai_draft" && can("workflows:publish") && (
            <Button variant="success" busy={approve.isPending} onClick={() => approve.mutate()}>
              <Icon name="check" /> Approve for sending
            </Button>
          )}
          <Button onClick={onClose}>Close</Button>
        </>
      }
    >
      <div className="grid stack-narrow" style={{ gridTemplateColumns: "minmax(0, 1.6fr) minmax(260px, 1fr)", alignItems: "start" }}>
        <div className="stack">
          <div className="row">
            <label className="field" style={{ flex: 1, minWidth: 180 }}>
              <span>Destination country</span>
              <select className="select" value={country} onChange={(event) => setCountry(event.target.value)}>
                <option value="">Sample (no country)</option>
                {countryOptions.map((option) => (
                  <option key={option.country} value={option.country}>
                    {countryFlag(option.country)} {option.name} — “{option.to_country_phrase}”
                  </option>
                ))}
              </select>
            </label>
            <label className="field" style={{ flex: 1, minWidth: 180 }}>
              <span>Agent</span>
              <select className="select" value={senderId} onChange={(event) => setSenderId(event.target.value)}>
                <option value="">First {script.service_line === "any" ? "" : `${script.service_line} `}agent</option>
                {agents.map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.display_name} ({agent.service_line})
                  </option>
                ))}
              </select>
            </label>
          </div>
          {version.status === "ai_draft" && (
            <div className="alert warning small">AI draft: not sent to anyone until it is approved. Check the wording in Word first.</div>
          )}
          <ErrorAlert error={preview.error} />
          {preview.isLoading && <Loading label="Rendering…" />}
          {preview.data && (
            <>
              {preview.data.missing.length > 0 && (
                <div className="alert warning small">Missing values in this preview: {preview.data.missing.join(", ")}</div>
              )}
              <EmailFrame
                html={preview.data.html_body ?? ""}
                subject={preview.data.subject}
                from={preview.data.sender_name ?? undefined}
                to={country ? `a contact in ${countryOptions.find((option) => option.country === country)?.name ?? country}` : "a sample contact"}
                direction={rtl ? "rtl" : "ltr"}
              />
            </>
          )}
        </div>
        <div className="stack">
          <Card title="Version">
            <KeyValues
              items={[
                ["Status", <StatusBadge key="status" value={version.status} />],
                ["Line", <LinePill key="line" line={script.service_line} />],
                ["Language", `${language?.name ?? version.language} (${version.language})`],
                ["Countries", language?.countries.map((code) => `${countryFlag(code)} ${code}`).join("  ") ?? "—"],
                ["Destination", preview.data?.to_country_phrase ? `“${preview.data.to_country_phrase}”` : "—"],
                ["Synced", version.synced_at ? formatDateTime(version.synced_at) : "—"],
                ["File", template?.source_path ? <code key="path" className="small">{template.source_path}</code> : "—"],
              ]}
            />
          </Card>
          {version.sync_error && <div className="alert error small">{version.sync_error}</div>}
          {template && template.import_warnings.length > 0 && (
            <Card title={`Import notes (${template.import_warnings.length})`}>
              <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
                {template.import_warnings.map((warning, index) => (
                  <li key={index}>{String(warning)}</li>
                ))}
              </ul>
            </Card>
          )}
          {template && template.placeholders.length > 0 && (
            <Card title="Placeholders used">
              <div className="chips">
                {template.placeholders.map((placeholder) => (
                  <span key={String(placeholder)} className="tag mono">{String(placeholder)}</span>
                ))}
              </div>
            </Card>
          )}
          <p className="small muted" style={{ margin: 0 }}>
            To change the wording, open the Word file in SharePoint and save it: the next sync (within 5 minutes) updates this script.
          </p>
        </div>
      </div>
    </Drawer>
  );
}

function BannersPanel({ canWrite }: { canWrite: boolean }) {
  const assets = useQuery({ queryKey: keys.assets, queryFn: () => unwrap(api.GET("/api/v1/scripts/assets")) });
  const countries = useCountries();
  const [file, setFile] = useState<File | null>(null);
  const [line, setLine] = useState<ServiceLine>("air");
  const [country, setCountry] = useState("");
  const toast = useToast();
  const queryClient = useQueryClient();
  const upload = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/scripts/banners", { body: formData({ file, service_line: line, country: country || null }) as never })),
    onSuccess: () => {
      toast("Banner saved to the library.");
      setFile(null);
      void queryClient.invalidateQueries({ queryKey: keys.assets });
    },
  });
  const groups: [string, Schemas["AssetResponse"][]][] = [
    ["Air banners", (assets.data ?? []).filter((asset) => asset.kind === "banner" && asset.service_line === "air")],
    ["Ocean banners", (assets.data ?? []).filter((asset) => asset.kind === "banner" && asset.service_line === "ocean")],
    ["Brand images (logo, badges)", (assets.data ?? []).filter((asset) => asset.kind === "logo" || asset.kind === "badge")],
  ];
  return (
    <div className="grid stack-narrow" style={{ gridTemplateColumns: "minmax(0, 2fr) minmax(280px, 1fr)", alignItems: "start" }}>
      <div className="stack">
        {assets.isLoading && <SkeletonRows rows={3} />}
        <ErrorAlert error={assets.error} />
        {groups.map(([title, items]) => (
          <Card key={title} title={title}>
            {items.length === 0 ? (
              <Empty title="None yet" />
            ) : (
              <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 12 }}>
                {items.map((asset) => (
                  <figure key={asset.id} className="card lift" style={{ margin: 0, overflow: "hidden" }}>
                    <img src={asset.url} alt={asset.filename} style={{ width: "100%", height: 90, objectFit: "cover", display: "block", background: "var(--surface-2)" }} />
                    <figcaption className="small" style={{ padding: "6px 10px" }}>
                      <strong>{asset.country ? `${countryFlag(asset.country)} ${asset.country}` : asset.kind === "banner" ? "Default" : asset.kind}</strong>
                      <div className="muted truncate">{asset.filename}</div>
                    </figcaption>
                  </figure>
                ))}
              </div>
            )}
          </Card>
        ))}
      </div>
      {canWrite && (
        <Card title="Add a banner">
          <div className="stack">
            <Dropzone file={file} onFile={setFile} accept="image/png,image/jpeg,image/gif,image/webp" hint="PNG, JPEG, GIF or WEBP, up to 5 MB. About 600 px wide works best." />
            <div className="form-grid">
              <label className="field">
                <span>Line</span>
                <select className="select" value={line} onChange={(event) => setLine(event.target.value as ServiceLine)}>
                  <ServiceLineOptions current={line} />
                </select>
              </label>
              <label className="field">
                <span>Country</span>
                <select className="select" value={country} onChange={(event) => setCountry(event.target.value)}>
                  <option value="">Default (every country)</option>
                  {(countries.data ?? []).map((option) => (
                    <option key={option.country} value={option.country}>
                      {countryFlag(option.country)} {option.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <p className="small muted" style={{ margin: 0 }}>
              Used where a script has <code>{"{banner}"}</code>. Saved as <code>Banners/{line === "air" ? "Air" : "Ocean"}/{country || "default"}.png</code> in the library.
            </p>
            <ErrorAlert error={upload.error} />
            <Button variant="primary" disabled={!file} busy={upload.isPending} onClick={() => upload.mutate()}>
              <Icon name="upload" /> Save banner
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}
