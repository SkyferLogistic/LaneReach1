/**
 * Pricing: the rates from every sheet (searchable, with validity), the rate sheets themselves
 * (sync status, row problems, column mapping), the websites the team checks (link templates, never
 * logins) and a lane's price history from the sheets and from recorded market checks.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, describeError, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys } from "../api/queries";
import { useSession } from "../auth/session";
import { useDebounced } from "../components/ContactPicker";
import { useConfirm } from "../components/confirm";
import { Dropzone } from "../components/Dropzone";
import { Icon } from "../components/icons";
import { Page } from "../components/Layout";
import { SkeletonRows } from "../components/motion";
import { ServiceLineOptions } from "../components/pickers";
import { Badge, Button, Card, Drawer, Empty, ErrorAlert, LinePill, Modal, PageHeader, Switch, Tabs, useToast } from "../components/ui";
import { ValidityBadge } from "../crm/DealPricing";
import { formatDate, formatDateTime } from "../lib/format";
import { FIELD_LABELS, fillLaneTemplate, linkTemplateProblem } from "../lib/pricing";

type Tab = "rates" | "sheets" | "sources" | "history";
type Sheet = Schemas["RateSheetResponse"];
type Source = Schemas["RateSourceResponse"];

const PRICING = [...keys.crm, "pricing"] as const;
const PAGE = 50;

function money(value: number, currency: string): string {
  return `${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`;
}

const STATUS_TONES: Record<string, "green" | "amber" | "red"> = { ok: "green", partial: "amber", failed: "red" };

export function PricingPage() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab | null) ?? "rates";
  const setTab = (next: Tab) => setParams(next === "rates" ? {} : { tab: next }, { replace: true });
  const sheets = useQuery({ queryKey: [...PRICING, "sheets"], queryFn: () => unwrap(api.GET("/api/v1/pricing/sheets")) });
  const sources = useQuery({ queryKey: [...PRICING, "sources"], queryFn: () => unwrap(api.GET("/api/v1/pricing/sources")) });
  const rateCount = (sheets.data ?? []).reduce((sum, sheet) => sum + sheet.row_count, 0);
  return (
    <Page>
      <PageHeader
        eyebrow={
          <>
            <Icon name="pricing" /> CRM
          </>
        }
        title="Pricing"
        description="Your rate sheets, synced from the library's Rates folder, and the websites your team checks. Deals show matching rates and draft priced quotes for review."
      />
      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "rates", label: `Rates (${rateCount.toLocaleString()})` },
          { id: "sheets", label: `Rate sheets (${sheets.data?.length ?? "…"})` },
          { id: "sources", label: `Websites (${sources.data?.length ?? "…"})` },
          { id: "history", label: "Lane history" },
        ]}
      />
      <ErrorAlert error={sheets.error ?? sources.error} />
      {tab === "rates" && <RatesTab sheets={sheets.data ?? []} onSheets={() => setTab("sheets")} />}
      {tab === "sheets" && <SheetsTab sheets={sheets.data} loading={sheets.isLoading} />}
      {tab === "sources" && <SourcesTab sources={sources.data} loading={sources.isLoading} />}
      {tab === "history" && <HistoryTab />}
    </Page>
  );
}

// --- rates ---------------------------------------------------------------------------------------

function RatesTab({ sheets, onSheets }: { sheets: Sheet[]; onSheets: () => void }) {
  const [search, setSearch] = useState("");
  const [origin, setOrigin] = useState("");
  const [destination, setDestination] = useState("");
  const [line, setLine] = useState("");
  const [sheetId, setSheetId] = useState("");
  const [expired, setExpired] = useState(false);
  const [offset, setOffset] = useState(0);
  const lane = useDebounced(search.trim());
  const from = useDebounced(origin.trim());
  const to = useDebounced(destination.trim());
  const query = {
    lane: lane || undefined,
    origin: from || undefined,
    destination: to || undefined,
    service_line: (line || undefined) as Schemas["ServiceLine"] | undefined,
    sheet_id: sheetId || undefined,
    include_expired: expired,
    limit: PAGE,
    offset,
  };
  const rates = useQuery({
    queryKey: [...PRICING, "rates", query],
    queryFn: () => unwrap(api.GET("/api/v1/pricing/rates", { params: { query } })),
    placeholderData: (previous) => previous,
  });
  const reset = () => setOffset(0);
  const items = rates.data?.items ?? [];
  const total = rates.data?.total ?? 0;
  if (sheets.length === 0) {
    return (
      <Card>
        <Empty art="ship" title="No rate sheets yet">
          Put your Excel or CSV rate sheets in the library&apos;s <strong>Rates</strong> folder (SharePoint/OneDrive) and they sync every few minutes, or{" "}
          <button type="button" className="link-button" onClick={onSheets}>
            upload one
          </button>
          .
        </Empty>
      </Card>
    );
  }
  return (
    <div className="stack">
      <div className="filter-bar">
        <label className="search-field">
          <Icon name="search" />
          <input
            className="input"
            placeholder="Search a place, code or carrier"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              reset();
            }}
          />
        </label>
        <input className="input" placeholder="Origin" value={origin} onChange={(event) => (setOrigin(event.target.value), reset())} style={{ width: 150 }} />
        <input className="input" placeholder="Destination" value={destination} onChange={(event) => (setDestination(event.target.value), reset())} style={{ width: 150 }} />
        <select className="select" value={line} onChange={(event) => (setLine(event.target.value), reset())} style={{ width: 150 }}>
          <option value="">All service lines</option>
          <ServiceLineOptions />
        </select>
        <select className="select" value={sheetId} onChange={(event) => (setSheetId(event.target.value), reset())} style={{ width: 200 }}>
          <option value="">All sheets</option>
          {sheets.map((sheet) => (
            <option key={sheet.id} value={sheet.id}>
              {sheet.name}
            </option>
          ))}
        </select>
        <Switch checked={expired} onChange={(value) => (setExpired(value), reset())} label="Include expired" />
      </div>
      <Card flush>
        {rates.isLoading && <SkeletonRows rows={6} />}
        <ErrorAlert error={rates.error} />
        {!rates.isLoading && items.length === 0 && <Empty title="No rate matches">Try another place, or include expired rates.</Empty>}
        {items.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Lane</th>
                  <th>Mode</th>
                  <th>Carrier</th>
                  <th>Charge</th>
                  <th style={{ textAlign: "right" }}>Rate</th>
                  <th>Validity</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {items.map((rate) => (
                  <tr key={rate.id}>
                    <td>
                      <strong>{rate.origin}</strong> → <strong>{rate.destination}</strong>
                      {(rate.origin_code || rate.destination_code) && (
                        <div className="small muted">
                          {rate.origin_code ?? "?"} → {rate.destination_code ?? "?"}
                        </div>
                      )}
                    </td>
                    <td>
                      <LinePill line={rate.service_line} /> {rate.mode ? <span className="small muted">{rate.mode.toUpperCase()}</span> : null}
                      {rate.equipment ? <div className="small">{rate.equipment}</div> : null}
                    </td>
                    <td>{rate.carrier ?? <span className="muted">—</span>}</td>
                    <td>
                      {rate.description}
                      {rate.charge_type !== "freight" && <div className="small muted">{rate.charge_type}</div>}
                    </td>
                    <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
                      {money(rate.amount, rate.currency)}
                      <div className="small muted">
                        per {rate.unit}
                        {rate.minimum ? ` · min ${rate.minimum}` : ""}
                      </div>
                    </td>
                    <td>
                      <ValidityBadge validTo={rate.valid_to} />
                      {rate.transit_days ? <div className="small muted">{rate.transit_days} days transit</div> : null}
                    </td>
                    <td className="small muted">
                      {rate.sheet_name}
                      <div>
                        {rate.tab} ▸ row {rate.row_number}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {total > PAGE && (
        <div className="row between small muted">
          <span>
            {offset + 1}–{Math.min(offset + PAGE, total)} of {total.toLocaleString()}
          </span>
          <span className="row">
            <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              Previous
            </Button>
            <Button size="sm" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
              Next
            </Button>
          </span>
        </div>
      )}
    </div>
  );
}

// --- sheets --------------------------------------------------------------------------------------

function SheetsTab({ sheets, loading }: { sheets: Sheet[] | undefined; loading: boolean }) {
  const { can } = useSession();
  const writer = can("crm:write");
  const [uploading, setUploading] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const toast = useToast();
  const sync = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/scripts/sync")),
    onSuccess: (summary) => {
      toast(`Library synced: ${summary.rate_sheets} rate sheet(s) imported.`);
      void queryClient.invalidateQueries({ queryKey: PRICING });
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  return (
    <div className="stack">
      <div className="alert info small">
        <strong>Keep your rate Excel where it is.</strong> Save it in the library&apos;s <code>Rates</code> folder (SharePoint/OneDrive, any sub-folder) and LaneReach
        re-imports it whenever the file changes. Every tab is read; column names like POL, POD, 40HC, Validity and Currency are recognised.
      </div>
      <div className="row">
        {writer && (
          <Button variant="primary" onClick={() => setUploading(true)}>
            <Icon name="upload" /> Upload a sheet
          </Button>
        )}
        {can("workflows:write") && (
          <Button busy={sync.isPending} onClick={() => sync.mutate()}>
            <Icon name="sync" /> Sync library now
          </Button>
        )}
        <Link to="/settings?tab=library" className="small">
          Library connection
        </Link>
      </div>
      <Card flush>
        {loading && <SkeletonRows rows={3} />}
        {!loading && (sheets ?? []).length === 0 && <Empty title="No rate sheets yet">Upload one, or put it in the library&apos;s Rates folder.</Empty>}
        {(sheets ?? []).length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Sheet</th>
                  <th>Status</th>
                  <th style={{ textAlign: "right" }}>Rates</th>
                  <th>Tabs</th>
                  <th>Imported</th>
                </tr>
              </thead>
              <tbody>
                {(sheets ?? []).map((sheet) => (
                  <tr key={sheet.id} className="clickable" onClick={() => setOpen(sheet.id)}>
                    <td>
                      <strong>{sheet.name}</strong>
                      <div className="small muted">
                        {sheet.source === "library" ? (
                          <>
                            <Icon name="cloud" /> Rates/{sheet.name}
                          </>
                        ) : (
                          "Uploaded"
                        )}
                      </div>
                    </td>
                    <td>
                      <Badge tone={STATUS_TONES[sheet.status] ?? "gray"}>{sheet.status}</Badge>
                      {(sheet.error_count ?? 0) > 0 && <div className="small muted">{sheet.error_count} row problem(s)</div>}
                    </td>
                    <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{sheet.row_count.toLocaleString()}</td>
                    <td className="small">{sheet.tabs.join(", ")}</td>
                    <td className="small muted">{formatDateTime(sheet.imported_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {uploading && <UploadSheetModal onClose={() => setUploading(false)} onDone={(id) => setOpen(id)} />}
      {open && <SheetDrawer sheetId={open} canWrite={writer} onClose={() => setOpen(null)} />}
    </div>
  );
}

function UploadSheetModal({ onClose, onDone }: { onClose: () => void; onDone: (id: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [currency, setCurrency] = useState("USD");
  const queryClient = useQueryClient();
  const toast = useToast();
  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData();
      if (file) form.append("file", file);
      form.append("default_currency", currency.trim().toUpperCase());
      return unwrap(api.POST("/api/v1/pricing/sheets/upload", { body: form as never }));
    },
    onSuccess: (sheet) => {
      toast(`${sheet.name}: ${sheet.row_count} rates imported${sheet.errors.length ? `, ${sheet.errors.length} row problem(s)` : ""}.`);
      void queryClient.invalidateQueries({ queryKey: PRICING });
      onClose();
      onDone(sheet.id);
    },
  });
  return (
    <Modal
      title="Upload a rate sheet"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={upload.isPending} disabled={!file || !/^[A-Za-z]{3}$/.test(currency.trim())} onClick={() => upload.mutate()}>
            <Icon name="upload" /> Import
          </Button>
        </>
      }
    >
      <div className="stack">
        <Dropzone file={file} onFile={setFile} accept=".xlsx,.xlsm,.csv" hint="Excel (.xlsx) or CSV, every tab is read" />
        <label className="field">
          <span>Currency for rows that do not say</span>
          <input className="input" value={currency} maxLength={3} onChange={(event) => setCurrency(event.target.value)} style={{ width: 90, textTransform: "uppercase" }} />
        </label>
        <p className="small muted">Uploading a sheet with the same file name replaces its rates. For a sheet the team keeps updating, use the library&apos;s Rates folder instead.</p>
        <ErrorAlert error={upload.error} />
      </div>
    </Modal>
  );
}

function SheetDrawer({ sheetId, canWrite, onClose }: { sheetId: string; canWrite: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [confirmDialog, ask] = useConfirm();
  const sheet = useQuery({
    queryKey: [...PRICING, "sheet", sheetId],
    queryFn: () => unwrap(api.GET("/api/v1/pricing/sheets/{sheet_id}", { params: { path: { sheet_id: sheetId } } })),
  });
  const preview = useQuery({
    queryKey: [...PRICING, "sheet", sheetId, "preview"],
    queryFn: () => unwrap(api.GET("/api/v1/pricing/sheets/{sheet_id}/preview", { params: { path: { sheet_id: sheetId } } })),
  });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: PRICING });
  const reimport = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/pricing/sheets/{sheet_id}/reimport", { params: { path: { sheet_id: sheetId } } })),
    onSuccess: (data) => {
      toast(`${data.row_count} rates imported.`);
      refresh();
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  const remove = useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/pricing/sheets/{sheet_id}", { params: { path: { sheet_id: sheetId } } })),
    onSuccess: () => {
      toast("Rate sheet deleted.");
      refresh();
      onClose();
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  const data = sheet.data;
  return (
    <Drawer
      wide
      title={data?.name ?? "Rate sheet"}
      onClose={onClose}
      footer={
        canWrite &&
        data && (
          <>
            {data.source === "upload" && (
              <Button
                variant="danger"
                style={{ marginRight: "auto" }}
                busy={remove.isPending}
                onClick={() => void ask({ title: `Delete ${data.name}?`, message: "Its rates disappear from pricing and deal matching." }).then((ok) => ok && remove.mutate())}
              >
                <Icon name="trash" /> Delete
              </Button>
            )}
            {confirmDialog}
            <Button busy={reimport.isPending} onClick={() => reimport.mutate()}>
              <Icon name="sync" /> Re-import
            </Button>
            <Button onClick={onClose}>Close</Button>
          </>
        )
      }
    >
      {sheet.isLoading && <SkeletonRows rows={4} />}
      <ErrorAlert error={sheet.error} />
      {data && (
        <div className="stack">
          <div className="row">
            <Badge tone={STATUS_TONES[data.status] ?? "gray"}>{data.status}</Badge>
            <span className="small muted">
              {data.row_count.toLocaleString()} rates · {data.tabs.length} tab(s) · default currency {data.default_currency} · imported {formatDateTime(data.imported_at)}
            </span>
          </div>
          {data.errors.length > 0 && (
            <Card title={`Row problems (${data.errors.length}${data.errors.length >= 50 ? "+" : ""})`}>
              <ul className="problem-list">
                {data.errors.map((error, index) => (
                  <li key={`${error.tab}-${error.row}-${index}`}>
                    <span className="small muted">{error.tab ? `${error.tab} ▸ ${error.row ? `row ${error.row}` : "tab"}` : "File"}</span> {error.message}
                  </li>
                ))}
              </ul>
            </Card>
          )}
          <MappingEditor sheet={data} preview={preview.data ?? {}} canWrite={canWrite} onSaved={refresh} />
        </div>
      )}
    </Drawer>
  );
}

function MappingEditor({
  sheet,
  preview,
  canWrite,
  onSaved,
}: {
  sheet: Schemas["RateSheetDetailResponse"];
  preview: Record<string, string[][]>;
  canWrite: boolean;
  onSaved: () => void;
}) {
  const tabs = Object.keys(preview).length ? Object.keys(preview) : sheet.tabs;
  const [tab, setTab] = useState(tabs[0] ?? "");
  const detected = sheet.detected_mapping[tab] ?? {};
  const [override, setOverride] = useState<Record<string, Record<string, string>>>(sheet.mapping_override);
  const toast = useToast();
  const rows = preview[tab] ?? [];
  const headerRow = useMemo(() => {
    const wanted = new Set(Object.values(detected).map((value) => value.toLowerCase()));
    const index = rows.findIndex((row) => row.some((cell) => wanted.has(cell.trim().toLowerCase())));
    return index >= 0 ? index : 0;
  }, [rows, detected]);
  const headers = (rows[headerRow] ?? []).filter((cell) => cell.trim());
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/api/v1/pricing/sheets/{sheet_id}/mapping", {
          params: { path: { sheet_id: sheet.id } },
          body: { mapping: override },
        }),
      ),
    onSuccess: (data) => {
      toast(`Columns saved: ${data.row_count} rates imported.`);
      onSaved();
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  const chosen = (field: string) => override[tab]?.[field] ?? "";
  const choose = (field: string, header: string) =>
    setOverride((current) => {
      const next = { ...(current[tab] ?? {}) };
      if (header) next[field] = header;
      else delete next[field];
      return { ...current, [tab]: next };
    });
  return (
    <Card
      title="Columns"
      actions={
        canWrite && (
          <Button size="sm" variant="primary" busy={save.isPending} onClick={() => save.mutate()}>
            Save and re-import
          </Button>
        )
      }
    >
      <div className="stack">
        {tabs.length > 1 && (
          <div className="row">
            {tabs.map((name) => (
              <Button key={name} size="sm" variant={name === tab ? "primary" : "ghost"} onClick={() => setTab(name)}>
                {name}
              </Button>
            ))}
          </div>
        )}
        <p className="small muted">
          LaneReach guessed the columns from their names. If a guess is wrong (for example it used <em>Buy</em> but you quote from <em>Sell</em>), pick the right column.
          Columns named after a container size (20GP, 40HC) or an air weight break (+45, +100) each become their own rate.
        </p>
        <div className="mapping-grid">
          {Object.entries(FIELD_LABELS).map(([field, label]) => (
            <label key={field} className="field">
              <span className="small">
                {label}
                {detected[field] && !chosen(field) && <span className="muted"> · found “{detected[field]}”</span>}
              </span>
              <select className="select" value={chosen(field)} disabled={!canWrite} onChange={(event) => choose(field, event.target.value)}>
                <option value="">{detected[field] ? `Detected: ${detected[field]}` : "Not used"}</option>
                {headers.map((header) => (
                  <option key={header} value={header}>
                    {header}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>
        {rows.length > 0 && (
          <div className="table-wrap">
            <table className="table compact preview-table">
              <tbody>
                {rows.map((row, index) => (
                  <tr key={index} className={index === headerRow ? "header-row" : undefined}>
                    {row.map((cell, cellIndex) => (
                      <td key={cellIndex}>{cell}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Card>
  );
}

// --- websites ------------------------------------------------------------------------------------

function SourcesTab({ sources, loading }: { sources: Source[] | undefined; loading: boolean }) {
  const { can } = useSession();
  const writer = can("crm:write");
  const [editing, setEditing] = useState<Source | "new" | null>(null);
  return (
    <div className="stack">
      <div className="alert info small">
        Add the websites your team checks for rates. LaneReach stores a link, <strong>never a login</strong>: on a deal, &ldquo;Open on …&rdquo; opens the site with the lane
        filled in, and the rate you see is recorded in a few seconds. Official API connections can be added once a site offers one.
      </div>
      {writer && (
        <div>
          <Button variant="primary" onClick={() => setEditing("new")}>
            <Icon name="plus" /> Add website
          </Button>
        </div>
      )}
      <Card flush>
        {loading && <SkeletonRows rows={3} />}
        {!loading && (sources ?? []).length === 0 && <Empty title="No websites yet">Add the rate websites your team uses.</Empty>}
        {(sources ?? []).length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Website</th>
                  <th>Lane link</th>
                  <th>Service lines</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {(sources ?? []).map((source) => (
                  <tr key={source.id} className={writer ? "clickable" : undefined} onClick={() => writer && setEditing(source)}>
                    <td>
                      <strong>{source.name}</strong>
                      <div className="small">
                        <a href={source.website} target="_blank" rel="noopener noreferrer" onClick={(event) => event.stopPropagation()}>
                          {source.website}
                        </a>
                      </div>
                    </td>
                    <td className="small truncate" style={{ maxWidth: 340 }} title={source.lane_url_template ?? ""}>
                      {source.lane_url_template ?? <span className="muted">Opens the home page</span>}
                    </td>
                    <td>
                      {source.service_lines.length ? (
                        <span className="row" style={{ gap: 4 }}>
                          {source.service_lines.map((line) => (
                            <LinePill key={line} line={line} />
                          ))}
                        </span>
                      ) : (
                        <span className="small muted">All</span>
                      )}
                    </td>
                    <td>{source.is_active ? <Badge tone="green">active</Badge> : <Badge tone="gray">paused</Badge>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {editing && <SourceModal source={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </div>
  );
}

const SERVICE_LINES = ["air", "ocean", "road", "rail", "courier", "customs", "warehousing", "project"] as const;
const EXAMPLE_LANE = { origin: "Chennai", destination: "Jebel Ali", mode: "fcl", equipment: "40HC", date: new Date().toISOString().slice(0, 10) };

function SourceModal({ source, onClose }: { source: Source | null; onClose: () => void }) {
  const [name, setName] = useState(source?.name ?? "");
  const [website, setWebsite] = useState(source?.website ?? "https://");
  const [template, setTemplate] = useState(source?.lane_url_template ?? "");
  const [lines, setLines] = useState<string[]>(source?.service_lines ?? []);
  const [active, setActive] = useState(source?.is_active ?? true);
  const [notes, setNotes] = useState(source?.notes ?? "");
  const [confirmDialog, ask] = useConfirm();
  const queryClient = useQueryClient();
  const toast = useToast();
  const body = {
    name: name.trim(),
    website: website.trim(),
    lane_url_template: template.trim() || null,
    service_lines: lines as Schemas["ServiceLine"][],
    is_active: active,
    notes: notes.trim() || null,
  };
  const done = (message: string) => {
    toast(message);
    void queryClient.invalidateQueries({ queryKey: PRICING });
    onClose();
  };
  const save = useMutation({
    mutationFn: () =>
      source
        ? unwrap(api.PUT("/api/v1/pricing/sources/{source_id}", { params: { path: { source_id: source.id } }, body }))
        : unwrap(api.POST("/api/v1/pricing/sources", { body })),
    onSuccess: () => done(source ? "Website saved." : "Website added."),
  });
  const remove = useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/pricing/sources/{source_id}", { params: { path: { source_id: source?.id ?? "" } } })),
    onSuccess: () => done("Website deleted."),
    onError: (error) => toast(describeError(error), "error"),
  });
  const websiteProblem = website.trim().length > 8 ? linkTemplateProblem(website) : "Enter the website address";
  const templateProblem = linkTemplateProblem(template);
  const example = template.trim() && !templateProblem ? fillLaneTemplate(template.trim(), EXAMPLE_LANE) : null;
  return (
    <Modal
      title={source ? `Edit ${source.name}` : "Add a rate website"}
      onClose={onClose}
      footer={
        <>
          {source && (
            <Button
              variant="danger"
              style={{ marginRight: "auto" }}
              busy={remove.isPending}
              onClick={() => void ask({ title: `Delete ${source.name}?`, message: "Rates already recorded from it keep its name." }).then((ok) => ok && remove.mutate())}
            >
              <Icon name="trash" /> Delete
            </Button>
          )}
          {confirmDialog}
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={save.isPending} disabled={!name.trim() || Boolean(websiteProblem) || Boolean(templateProblem)} onClick={() => save.mutate()}>
            Save
          </Button>
        </>
      }
    >
      <div className="stack">
        <label className="field">
          <span>Name</span>
          <input className="input" value={name} maxLength={120} onChange={(event) => setName(event.target.value)} placeholder="e.g. the carrier or marketplace name" autoFocus />
        </label>
        <label className="field">
          <span>Website</span>
          <input className="input" value={website} maxLength={500} onChange={(event) => setWebsite(event.target.value)} />
          {websiteProblem && website.trim().length > 8 && <span className="field-error">{websiteProblem}</span>}
        </label>
        <label className="field">
          <span>Lane link (optional)</span>
          <input
            className="input mono"
            value={template}
            maxLength={2000}
            onChange={(event) => setTemplate(event.target.value)}
            placeholder="https://site.example/search?from={origin}&to={destination}&type={equipment}"
          />
          <span className="small muted">
            Search on the site once, copy the address and replace the places with {"{origin}"} and {"{destination}"} (also {"{mode}"}, {"{equipment}"}, {"{date}"}).
          </span>
          {templateProblem && <span className="field-error">{templateProblem}</span>}
          {example && (
            <span className="small">
              Example:{" "}
              <a href={example} target="_blank" rel="noopener noreferrer" className="mono">
                {example}
              </a>
            </span>
          )}
        </label>
        <div className="field">
          <span>Service lines (none = all)</span>
          <div className="row" style={{ gap: 6 }}>
            {SERVICE_LINES.map((line) => (
              <button
                key={line}
                type="button"
                className={lines.includes(line) ? "chip-toggle on" : "chip-toggle"}
                onClick={() => setLines(lines.includes(line) ? lines.filter((item) => item !== line) : [...lines, line])}
              >
                {line}
              </button>
            ))}
          </div>
        </div>
        <Switch checked={active} onChange={setActive} label="Show on deals" />
        <label className="field">
          <span>Notes</span>
          <input className="input" value={notes} maxLength={500} onChange={(event) => setNotes(event.target.value)} placeholder="Who has the account, what it is good for" />
        </label>
        <ErrorAlert error={save.error} />
      </div>
    </Modal>
  );
}

// --- lane history --------------------------------------------------------------------------------

function HistoryTab() {
  const [origin, setOrigin] = useState("");
  const [destination, setDestination] = useState("");
  const [equipment, setEquipment] = useState("");
  const from = useDebounced(origin.trim());
  const to = useDebounced(destination.trim());
  const eq = useDebounced(equipment.trim());
  const ready = from.length >= 2 && to.length >= 2;
  const history = useQuery({
    queryKey: [...PRICING, "history", from, to, eq],
    queryFn: () => unwrap(api.GET("/api/v1/pricing/lanes/history", { params: { query: { origin: from, destination: to, equipment: eq || undefined } } })),
    enabled: ready,
  });
  const points = history.data ?? [];
  const currencies = [...new Set(points.map((point) => `${point.currency}/${point.unit}`))];
  return (
    <div className="stack">
      <div className="filter-bar">
        <input className="input" placeholder="Origin, e.g. Chennai or INMAA" value={origin} onChange={(event) => setOrigin(event.target.value)} style={{ width: 220 }} />
        <input className="input" placeholder="Destination, e.g. Jebel Ali" value={destination} onChange={(event) => setDestination(event.target.value)} style={{ width: 220 }} />
        <input className="input" placeholder="Equipment (optional)" value={equipment} onChange={(event) => setEquipment(event.target.value)} style={{ width: 160 }} />
      </div>
      {!ready && (
        <Card>
          <Empty art="chart" title="Pick a lane">
            See how the freight rate moved: every sheet rate for the lane and every market rate your team recorded on deals.
          </Empty>
        </Card>
      )}
      <ErrorAlert error={history.error} />
      {ready && history.isLoading && <SkeletonRows rows={3} />}
      {ready && !history.isLoading && points.length === 0 && (
        <Card>
          <Empty title="No prices for this lane yet" />
        </Card>
      )}
      {currencies.map((key) => {
        const series = points.filter((point) => `${point.currency}/${point.unit}` === key);
        return <LaneChart key={key} title={`${from} → ${to} · ${key.replace("/", " per ")}`} points={series} />;
      })}
    </div>
  );
}

function LaneChart({ title, points }: { title: string; points: Schemas["LanePointResponse"][] }) {
  const width = 640;
  const height = 200;
  const pad = { top: 12, right: 12, bottom: 24, left: 56 };
  const times = points.map((point) => new Date(point.observed_on).getTime());
  const amounts = points.map((point) => point.amount);
  const minTime = Math.min(...times);
  const maxTime = Math.max(...times);
  const low = Math.min(...amounts) * 0.95;
  const high = Math.max(...amounts) * 1.05 || 1;
  const x = (time: number) => pad.left + (maxTime === minTime ? (width - pad.left - pad.right) / 2 : ((time - minTime) / (maxTime - minTime)) * (width - pad.left - pad.right));
  const y = (amount: number) => pad.top + (1 - (amount - low) / (high - low || 1)) * (height - pad.top - pad.bottom);
  // One line per sheet and carrier (never joining two carriers' prices on the same day).
  const lines = [...new Set(points.filter((point) => point.kind === "sheet").map((point) => point.label))]
    .map((label) =>
      points
        .filter((point) => point.kind === "sheet" && point.label === label)
        .map((point) => [x(new Date(point.observed_on).getTime()), y(point.amount)] as const)
        .sort((a, b) => a[0] - b[0]),
    )
    .filter((series) => new Set(series.map(([px]) => px)).size > 1)
    .map((series) => series.map(([px, py], index) => `${index ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join(" "));
  return (
    <Card title={title} actions={<span className="small muted">● sheet · ◆ market check</span>}>
      <svg viewBox={`0 0 ${width} ${height}`} className="chart lane-chart" role="img" aria-label={title}>
        {[low, (low + high) / 2, high].map((value) => (
          <g key={value}>
            <line x1={pad.left} x2={width - pad.right} y1={y(value)} y2={y(value)} className="grid-line" />
            <text x={pad.left - 8} y={y(value) + 4} textAnchor="end" className="axis-label">
              {Math.round(value).toLocaleString()}
            </text>
          </g>
        ))}
        {lines.map((path) => (
          <path key={path} d={path} fill="none" stroke="var(--accent)" strokeWidth="2" opacity={0.7} />
        ))}
        {points.map((point, index) => {
          const px = x(new Date(point.observed_on).getTime());
          const py = y(point.amount);
          return point.kind === "sheet" ? (
            <circle key={index} cx={px} cy={py} r={4} fill="var(--accent)">
              <title>{`${point.label}: ${point.amount} ${point.currency} (${formatDate(point.observed_on)})`}</title>
            </circle>
          ) : (
            <rect key={index} x={px - 4} y={py - 4} width={8} height={8} transform={`rotate(45 ${px} ${py})`} fill="var(--success)">
              <title>{`${point.label}: ${point.amount} ${point.currency} (${formatDate(point.observed_on)})`}</title>
            </rect>
          );
        })}
        <text x={pad.left} y={height - 6} className="axis-label">
          {formatDate(new Date(minTime).toISOString())}
        </text>
        {maxTime !== minTime && (
          <text x={width - pad.right} y={height - 6} textAnchor="end" className="axis-label">
            {formatDate(new Date(maxTime).toISOString())}
          </text>
        )}
      </svg>
    </Card>
  );
}
