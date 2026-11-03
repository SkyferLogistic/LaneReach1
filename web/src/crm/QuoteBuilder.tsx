/**
 * Quote builder: charge lines with presets per mode, live totals, validity and transit time,
 * a preview exactly as the customer receives it, send in the thread, accept/reject.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, describeError, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys } from "../api/queries";
import { useSession } from "../auth/session";
import { useConfirm, useDelete } from "../components/confirm";
import { EmailFrame } from "../components/EmailFrame";
import { Icon } from "../components/icons";
import { CountUp } from "../components/motion";
import { ServiceLineOptions } from "../components/pickers";
import { Badge, Button, Drawer, ErrorAlert, useToast } from "../components/ui";
import { formatDate, formatDateTime } from "../lib/format";
import { asServiceLine } from "../lib/freight";
import { daysLeft, lineAmount, lineIsValid, normalizePrice, normalizeQuantity, quoteTotal } from "../lib/quotes";
import type { DraftLine } from "../lib/quotes";

type Quote = Schemas["QuoteResponse"];
type Catalog = Schemas["LogisticsCatalogResponse"];

export const QUOTES_KEY = [...keys.crm, "quotes"] as const;

export function useLogisticsCatalog() {
  return useQuery({
    queryKey: [...keys.crm, "catalog"],
    queryFn: () => unwrap(api.GET("/api/v1/crm/quotes/catalog")),
    staleTime: Infinity,
  });
}

export function QuoteStatusBadge({ quote }: { quote: Pick<Quote, "status" | "valid_until"> }) {
  if (quote.status === "sent") {
    const left = daysLeft(quote.valid_until);
    return <Badge tone={left <= 2 ? "amber" : "blue"}>sent · {left >= 0 ? `${left}d left` : "past validity"}</Badge>;
  }
  const tones: Record<string, "green" | "red" | "gray" | "violet"> = { accepted: "green", rejected: "red", expired: "gray", draft: "violet" };
  return <Badge tone={tones[quote.status] ?? "gray"}>{quote.status}</Badge>;
}

function money(value: number, currency: string): string {
  return `${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`;
}

function toDraft(quote: Quote): DraftLine[] {
  return (quote.lines ?? []).map((line) => ({
    charge_type: line.charge_type,
    description: line.description,
    quantity: String(line.quantity),
    unit: line.unit,
    unit_price: line.unit_price.toFixed(2),
  }));
}

/** Opens a new draft for a deal, then the builder. */
export function useStartQuote(onCreated: (quote: Quote) => void) {
  const queryClient = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (dealId: string) => unwrap(api.POST("/api/v1/crm/quotes", { body: { deal_id: dealId, lines: [] } })),
    onSuccess: (quote) => {
      void queryClient.invalidateQueries({ queryKey: QUOTES_KEY });
      onCreated(quote);
    },
    onError: (error) => toast(describeError(error), "error"),
  });
}

export function QuoteBuilder({ quoteId, onClose }: { quoteId: string; onClose: () => void }) {
  const { can } = useSession();
  const writer = can("crm:write");
  const queryClient = useQueryClient();
  const toast = useToast();
  const catalog = useLogisticsCatalog();
  const quote = useQuery({
    queryKey: [...QUOTES_KEY, quoteId],
    queryFn: () => unwrap(api.GET("/api/v1/crm/quotes/{quote_id}", { params: { path: { quote_id: quoteId } } })),
  });
  const data = quote.data;
  const editable = writer && data?.status === "draft";
  const [header, setHeader] = useState<Record<string, string>>({});
  const [lines, setLines] = useState<DraftLine[]>([]);
  const [intro, setIntro] = useState("");
  const [view, setView] = useState<"edit" | "preview">("edit");
  const loaded = useRef<string | null>(null);
  useEffect(() => {
    if (!data || loaded.current === data.updated_at) return;
    loaded.current = data.updated_at;
    setHeader({
      title: data.title,
      currency: data.currency,
      valid_until: data.valid_until,
      transit_days: data.transit_days === null ? "" : String(data.transit_days),
      incoterm: data.incoterm ?? "",
      service_line: data.service_line,
      mode: data.mode ?? "",
      origin: data.origin ?? "",
      destination: data.destination ?? "",
      notes: data.notes,
    });
    setLines(toDraft(data));
  }, [data]);

  const line = asServiceLine(header.service_line);
  const lineInfo = catalog.data?.service_lines.find((item) => item.key === line);
  const total = useMemo(() => quoteTotal(lines), [lines]);
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: QUOTES_KEY });
    void queryClient.invalidateQueries({ queryKey: keys.deals });
  };
  const body = () => ({
    title: header.title || null,
    currency: (header.currency || "USD").toUpperCase(),
    valid_until: header.valid_until || null,
    transit_days: header.transit_days ? Number(header.transit_days) : null,
    incoterm: header.incoterm || null,
    service_line: (header.service_line || null) as Schemas["ServiceLine"] | null,
    mode: header.mode || null,
    origin: header.origin || null,
    destination: header.destination || null,
    notes: header.notes ?? "",
    lines: lines.filter(lineIsValid).map((item) => ({
      charge_type: item.charge_type as Schemas["ChargeType"],
      description: item.description.trim(),
      quantity: normalizeQuantity(item.quantity) as never,
      unit: item.unit,
      unit_price: normalizePrice(item.unit_price) as never,
    })),
  });
  const save = useMutation({
    mutationFn: () => unwrap(api.PUT("/api/v1/crm/quotes/{quote_id}", { params: { path: { quote_id: quoteId } }, body: body() })),
    onSuccess: () => {
      toast("Quote saved.");
      refresh();
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  const send = useMutation({
    mutationFn: async () => {
      if (editable) await unwrap(api.PUT("/api/v1/crm/quotes/{quote_id}", { params: { path: { quote_id: quoteId } }, body: body() }));
      return unwrap(
        api.POST("/api/v1/crm/quotes/{quote_id}/send", {
          params: { path: { quote_id: quoteId }, header: { "Idempotency-Key": `quote-send-${quoteId}` } },
          body: { intro: intro.trim() || null },
        }),
      );
    },
    onSuccess: (sent) => {
      toast(`${sent.number} sent to ${sent.contact_name ?? "the contact"} in their thread.`);
      refresh();
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  const decide = useMutation({
    mutationFn: (accepted: boolean) =>
      unwrap(api.POST("/api/v1/crm/quotes/{quote_id}/decision", {
          params: { path: { quote_id: quoteId }, header: { "Idempotency-Key": `quote-decision-${quoteId}-${accepted ? "yes" : "no"}` } },
          body: { accepted },
        })),
    onSuccess: (decided) => {
      toast(decided.status === "accepted" ? "Accepted: the deal value is set. Move the deal to Won when booked." : "Marked as rejected.");
      refresh();
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  const coverNote = useMutation({
    mutationFn: async () => {
      // The note is written from the saved quote, so save what was typed first.
      await unwrap(api.PUT("/api/v1/crm/quotes/{quote_id}", { params: { path: { quote_id: quoteId } }, body: body() }));
      return unwrap(api.POST("/api/v1/crm/quotes/{quote_id}/cover-note", { params: { path: { quote_id: quoteId } } }));
    },
    onSuccess: (note) => {
      setIntro(note.intro);
      toast("Cover note drafted from this quote. Read it before sending.");
      refresh();
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  const fill = useMutation({
    mutationFn: async () => {
      // Keep the header typed so far, then let the server replace the lines from the sheets.
      await unwrap(api.PUT("/api/v1/crm/quotes/{quote_id}", { params: { path: { quote_id: quoteId } }, body: body() }));
      return unwrap(api.POST("/api/v1/pricing/quotes/{quote_id}/fill", { params: { path: { quote_id: quoteId } }, body: {} }));
    },
    onSuccess: (priced) => {
      toast(
        priced.needs_attention.length
          ? `Prices filled from ${priced.sheet_name}; ${priced.needs_attention.length} charge(s) in another currency are left at 0.`
          : `Prices filled from ${priced.sheet_name}.`,
      );
      refresh();
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  const duplicate = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/crm/quotes/{quote_id}/duplicate", { params: { path: { quote_id: quoteId } } })),
    onSuccess: (copy) => {
      toast(`${copy.number} created as a draft.`);
      refresh();
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  const [confirmDialog, ask] = useConfirm();
  const remove = useDelete({
    run: () => unwrap(api.DELETE("/api/v1/crm/quotes/{quote_id}", { params: { path: { quote_id: quoteId } } })),
    success: "Quote deleted.",
    invalidate: [QUOTES_KEY],
    onDone: onClose,
  });
  const preview = useQuery({
    queryKey: [...QUOTES_KEY, quoteId, "document", data?.updated_at],
    queryFn: async () => (await api.GET("/api/v1/crm/quotes/{quote_id}/document", { params: { path: { quote_id: quoteId } }, parseAs: "text" })).data ?? "",
    enabled: view === "preview" && Boolean(data),
  });

  const setField = (name: string) => (event: { target: { value: string } }) => setHeader((current) => ({ ...current, [name]: event.target.value }));
  const updateLine = (index: number, change: Partial<DraftLine>) =>
    setLines((current) => current.map((item, position) => (position === index ? { ...item, ...change } : item)));
  const addPreset = (preset?: Schemas["ChargePresetResponse"]) =>
    setLines((current) => [
      ...current,
      preset
        ? { charge_type: preset.charge_type, description: preset.description, quantity: "1", unit: preset.unit, unit_price: "" }
        : { charge_type: "other", description: "", quantity: "1", unit: "shipment", unit_price: "" },
    ]);

  if (!data) {
    return (
      <Drawer wide title="Quote" onClose={onClose}>
        <ErrorAlert error={quote.error} />
      </Drawer>
    );
  }
  const currency = (header.currency || data.currency).toUpperCase();
  return (
    <Drawer
      wide
      title={
        <span className="row">
          <Icon name="quote" /> {data.number} <QuoteStatusBadge quote={data} />
        </span>
      }
      onClose={onClose}
      footer={
        <>
          {writer && (
            <Button variant="ghost" style={{ marginRight: "auto" }} busy={remove.isPending} onClick={() => void ask({ title: `Delete ${data.number}?`, message: "The quote and its lines are deleted. An email already sent stays in the conversation." }).then((ok) => ok && remove.mutate())}>
              <Icon name="trash" /> Delete
            </Button>
          )}
          {confirmDialog}
          {writer && data.status !== "draft" && (
            <Button busy={duplicate.isPending} onClick={() => duplicate.mutate()}>Duplicate as new draft</Button>
          )}
          {writer && (data.status === "sent" || data.status === "expired") && (
            <>
              <Button variant="danger" busy={decide.isPending} onClick={() => decide.mutate(false)}>Rejected</Button>
              <Button variant="success" busy={decide.isPending} onClick={() => decide.mutate(true)}>
                <Icon name="check" /> Accepted
              </Button>
            </>
          )}
          {editable && (
            <>
              <Button busy={save.isPending} onClick={() => save.mutate()}>Save draft</Button>
              <Button variant="primary" busy={send.isPending} disabled={!lines.some(lineIsValid)} onClick={() => send.mutate()}>
                <Icon name="mail" /> Send to {data.contact_name ?? "contact"}
              </Button>
            </>
          )}
        </>
      }
    >
      <div className="stack">
        <div className="row between" style={{ flexWrap: "wrap", gap: 8 }}>
          <span className="small muted">
            For {data.contact_name ?? "contact"}
            {data.company_name ? ` · ${data.company_name}` : ""} · created {formatDate(data.created_at)}
            {data.sent_at ? ` · sent ${formatDateTime(data.sent_at)}` : ""}
          </span>
          <div className="segmented" role="tablist">
            <button type="button" className={view === "edit" ? "on" : ""} onClick={() => setView("edit")}>Charges</button>
            <button type="button" className={view === "preview" ? "on" : ""} onClick={() => setView("preview")}>Customer view</button>
          </div>
        </div>
        {view === "preview" ? (
          <>
            {editable && <div className="alert info small">The preview shows the saved draft; save first to see your latest changes.</div>}
            <EmailFrame html={preview.data ?? "<p>Loading…</p>"} subject={`Quotation ${data.number}: ${data.title}`} minHeight={520} />
          </>
        ) : (
          <>
            <div className="form-grid quote-header">
              <label className="field wide"><span>Title</span><input className="input" disabled={!editable} value={header.title ?? ""} onChange={setField("title")} /></label>
              <label className="field">
                <span>Service</span>
                <select className="select" disabled={!editable} value={header.service_line ?? "any"} onChange={(event) => setHeader((current) => ({ ...current, service_line: event.target.value, mode: "" }))}>
                  <ServiceLineOptions any="Any" current={header.service_line} />
                </select>
              </label>
              <label className="field">
                <span>Mode</span>
                <select className="select" disabled={!editable} value={header.mode ?? ""} onChange={setField("mode")}>
                  <option value="">—</option>
                  {(lineInfo?.modes ?? []).map((mode) => <option key={mode.key} value={mode.key}>{mode.label}</option>)}
                  {header.mode && !(lineInfo?.modes ?? []).some((mode) => mode.key === header.mode) && <option value={header.mode}>{header.mode}</option>}
                </select>
              </label>
              <label className="field"><span>Origin</span><input className="input" disabled={!editable} value={header.origin ?? ""} onChange={setField("origin")} /></label>
              <label className="field"><span>Destination</span><input className="input" disabled={!editable} value={header.destination ?? ""} onChange={setField("destination")} /></label>
              <label className="field">
                <span>Incoterm</span>
                <select className="select" disabled={!editable} value={header.incoterm ?? ""} onChange={setField("incoterm")}>
                  <option value="">—</option>
                  {(catalog.data?.incoterms ?? []).map((term) => <option key={term} value={term}>{term}</option>)}
                </select>
              </label>
              <label className="field"><span>Currency</span><input className="input" maxLength={3} disabled={!editable} value={header.currency ?? ""} onChange={setField("currency")} /></label>
              <label className="field"><span>Valid until</span><input className="input" type="date" disabled={!editable} value={header.valid_until ?? ""} onChange={setField("valid_until")} /></label>
              <label className="field"><span>Transit (days)</span><input className="input" type="number" min={0} disabled={!editable} value={header.transit_days ?? ""} onChange={setField("transit_days")} /></label>
            </div>
            <ChargeTable lines={lines} catalog={catalog.data} currency={currency} editable={Boolean(editable)} onChange={updateLine} onRemove={(index) => setLines((current) => current.filter((_, position) => position !== index))} />
            {editable && (
              <div className="chips" aria-label="Add a charge">
                {(lineInfo?.presets ?? catalog.data?.service_lines[0]?.presets ?? []).map((preset) => (
                  <button key={preset.description} type="button" className="chip-toggle" onClick={() => addPreset(preset)}>
                    <Icon name="plus" /> {preset.description}
                  </button>
                ))}
                <button type="button" className="chip-toggle" onClick={() => addPreset()}>
                  <Icon name="plus" /> Other charge
                </button>
                <button
                  type="button"
                  className="chip-toggle accent"
                  disabled={fill.isPending}
                  title="Replace the lines with the best matching rates from your rate sheets"
                  onClick={() => fill.mutate()}
                >
                  <Icon name="pricing" /> {fill.isPending ? "Filling…" : "Fill from rate sheets"}
                </button>
              </div>
            )}
            <div className="quote-total">
              <span>Total</span>
              <strong><CountUp value={total} format={(value) => money(value, currency)} /></strong>
            </div>
            <label className="field"><span>Notes for the customer</span><textarea className="textarea" disabled={!editable} value={header.notes ?? ""} onChange={setField("notes")} placeholder="Rates subject to space and equipment; excludes duties and taxes." /></label>
            {editable && (
              <div className="field">
                <span className="row between">
                  Message above the quote (optional)
                  {editable && can("ai:run") && (
                    <Button size="sm" variant="ghost" busy={coverNote.isPending} onClick={() => coverNote.mutate()} title="Drafted from this quote's own lines, total, validity and transit; edit it before sending">
                      <Icon name="sparkles" /> Write with AI
                    </Button>
                  )}
                </span>
                <textarea className="textarea" aria-label="Message above the quote" value={intro} onChange={(event) => setIntro(event.target.value)} placeholder={`Hello ${data.contact_name?.split(" ")[0] ?? ""}, thank you for your request…`} />
              </div>
            )}
            {data.status !== "draft" && data.decision_note && <div className="small muted">Note: {data.decision_note}</div>}
          </>
        )}
      </div>
    </Drawer>
  );
}

function ChargeTable({
  lines,
  catalog,
  currency,
  editable,
  onChange,
  onRemove,
}: {
  lines: DraftLine[];
  catalog: Catalog | undefined;
  currency: string;
  editable: boolean;
  onChange: (index: number, change: Partial<DraftLine>) => void;
  onRemove: (index: number) => void;
}) {
  if (lines.length === 0) {
    return <div className="alert info small">No charges yet. Add the typical charges for this mode below.</div>;
  }
  return (
    <div className="table-wrap">
      <table className="table quote-lines">
        <thead>
          <tr>
            <th>Charge</th>
            <th>Type</th>
            <th style={{ width: 90 }}>Qty</th>
            <th style={{ width: 150 }}>Unit</th>
            <th style={{ width: 120 }}>Unit price</th>
            <th style={{ width: 130, textAlign: "right" }}>Amount</th>
            {editable && <th aria-label="Remove" />}
          </tr>
        </thead>
        <tbody>
          {lines.map((line, index) => (
            <tr key={index}>
              <td><input className="input" disabled={!editable} value={line.description} onChange={(event) => onChange(index, { description: event.target.value })} aria-label="Charge" /></td>
              <td>
                <select className="select" disabled={!editable} value={line.charge_type} onChange={(event) => onChange(index, { charge_type: event.target.value })} aria-label="Type">
                  {(catalog?.charge_types ?? [line.charge_type]).map((type) => <option key={type} value={type}>{type}</option>)}
                </select>
              </td>
              <td><input className="input" type="number" min={0} step="any" disabled={!editable} value={line.quantity} onChange={(event) => onChange(index, { quantity: event.target.value })} aria-label="Quantity" /></td>
              <td>
                <select className="select" disabled={!editable} value={line.unit} onChange={(event) => onChange(index, { unit: event.target.value })} aria-label="Unit">
                  {[...new Set([line.unit, ...(catalog?.units ?? [])])].map((unit) => <option key={unit} value={unit}>{unit}</option>)}
                </select>
              </td>
              <td><input className="input" type="number" min={0} step="0.01" disabled={!editable} value={line.unit_price} onChange={(event) => onChange(index, { unit_price: event.target.value })} aria-label="Unit price" placeholder="0.00" /></td>
              <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}><strong>{money(lineAmount(line), currency)}</strong></td>
              {editable && (
                <td>
                  <button type="button" className="icon-button danger" aria-label="Remove charge" onClick={() => onRemove(index)}>
                    <Icon name="x" />
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
