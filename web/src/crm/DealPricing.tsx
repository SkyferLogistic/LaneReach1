/**
 * A deal's Pricing tab: matching rate-sheet options (with why they matched), one-click links to the
 * team's rate websites with the lane filled in, rates recorded from those sites, and "draft a quote
 * from these rates". Nothing here sends anything: drafts are reviewed in the quote builder.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, describeError, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys } from "../api/queries";
import { useSession } from "../auth/session";
import { Icon } from "../components/icons";
import { SkeletonRows } from "../components/motion";
import { Badge, Button, ErrorAlert, useToast } from "../components/ui";
import { formatDate, formatRelative } from "../lib/format";
import { cheapestByCurrency, priceDifference, validityOf } from "../lib/pricing";
import { QUOTES_KEY, QuoteBuilder } from "./QuoteBuilder";

type Pricing = Schemas["DealPricingResponse"];
type Option = Schemas["RateOptionResponse"];
type Check = Schemas["MarketCheckResponse"];
type Lane = { origin: string; destination: string };

export const PRICING_KEY = [...keys.crm, "pricing"] as const;

function money(value: number, currency: string): string {
  return `${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`;
}

export function ValidityBadge({ validTo }: { validTo: string | null | undefined }) {
  const state = validityOf(validTo);
  if (state === "open") return <Badge tone="gray">no end date</Badge>;
  const tone = state === "expired" ? "red" : state === "soon" ? "amber" : "green";
  const label = state === "expired" ? "expired" : `until ${formatDate(validTo)}`;
  return <Badge tone={tone}>{label}</Badge>;
}

export function DealPricing({ dealId, canWrite }: { dealId: string; canWrite: boolean }) {
  const { can } = useSession();
  const [lane, setLane] = useState<Lane | null>(null);
  const [aiResult, setAiResult] = useState<Pricing | null>(null);
  const [markup, setMarkup] = useState("0");
  const [openQuote, setOpenQuote] = useState<string | null>(null);
  const toast = useToast();
  const queryClient = useQueryClient();
  const pricing = useQuery({
    queryKey: [...PRICING_KEY, dealId, lane],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/pricing/deals/{deal_id}", {
          params: { path: { deal_id: dealId }, query: { origin: lane?.origin, destination: lane?.destination } },
        }),
      ),
  });
  const matchLane = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/pricing/deals/{deal_id}/match-lane", { params: { path: { deal_id: dealId } } })),
    onSuccess: (result) => {
      setAiResult(result);
      if (result.lane?.usable && result.lane.origin && result.lane.destination) {
        setLane({ origin: result.lane.origin, destination: result.lane.destination });
      } else {
        toast("The AI found no lane in your sheets that is the same place.", "error");
      }
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  const draft = useMutation({
    mutationFn: (option: Option) =>
      unwrap(
        api.POST("/api/v1/pricing/deals/{deal_id}/quotes", {
          params: { path: { deal_id: dealId } },
          body: { option_index: option.index, markup_percent: Number(markup) || 0, origin: lane?.origin, destination: lane?.destination },
        }),
      ),
    onSuccess: (priced) => {
      toast(
        priced.needs_attention.length
          ? `Draft ${priced.number} created. ${priced.needs_attention.length} charge(s) in another currency are left at 0.`
          : `Draft ${priced.number} created from ${priced.sheet_name}.`,
      );
      void queryClient.invalidateQueries({ queryKey: QUOTES_KEY });
      void queryClient.invalidateQueries({ queryKey: keys.deals });
      setOpenQuote(priced.quote_id);
    },
    onError: (error) => toast(describeError(error), "error"),
  });

  const data = pricing.data;
  if (pricing.isLoading) return <SkeletonRows rows={3} />;
  if (!data) return <ErrorAlert error={pricing.error} />;
  const query = data.query;
  const shipment = [
    query.mode?.toUpperCase(),
    query.equipment && `${query.container_quantity ?? 1} × ${query.equipment}`,
    query.weight_kg ? `${query.weight_kg} kg` : null,
    query.volume_cbm ? `${query.volume_cbm} cbm` : null,
    query.on_date ? `ready ${formatDate(query.on_date)}` : null,
  ].filter(Boolean);
  const best = data.options[0];
  const bestFreight = best?.rates.find((rate) => rate.charge_type === "freight");

  return (
    <div className="stack">
      <div className="pricing-summary">
        <div>
          <span className="small muted">Pricing</span>
          <strong>
            {query.origin ?? "?"} → {query.destination ?? "?"}
          </strong>
          <span className="small muted">{shipment.join(" · ") || "No shipment details yet"}</span>
        </div>
        {canWrite && data.options.length > 0 && (
          <label className="field compact" title="Added to sheet prices. Leave 0 when the sheet holds selling rates.">
            <span className="small muted">Markup %</span>
            <input className="input" type="number" min={0} max={100} step={0.5} value={markup} onChange={(event) => setMarkup(event.target.value)} style={{ width: 84 }} />
          </label>
        )}
      </div>

      {data.sheets === 0 && (
        <div className="alert info small">
          No rate sheets yet. Put your Excel rate sheets in the library&apos;s <strong>Rates</strong> folder (SharePoint/OneDrive) or upload one on the{" "}
          <Link to="/pricing?tab=sheets">Pricing page</Link>.
        </div>
      )}
      {lane && (
        <div className="alert info small row between">
          <span>
            Pricing the lane as <strong>{lane.origin}</strong> → <strong>{lane.destination}</strong>
            {aiResult?.lane ? ` (AI match, ${Math.round(aiResult.lane.confidence * 100)}% sure)` : ""}.
          </span>
          <Button size="sm" variant="ghost" onClick={() => setLane(null)}>
            Use the deal&apos;s lane
          </Button>
        </div>
      )}
      {data.sheets > 0 && data.options.length === 0 && (
        <div className="empty-note">
          <Icon name="search" />
          <div className="grow">
            <strong>No sheet rate for this lane, mode and date.</strong>
            <div className="small muted">
              Rates match on port/airport codes or place names, a compatible mode and equipment, and validity on the cargo-ready date.
            </div>
          </div>
          {can("ai:run") && !lane && (
            <Button busy={matchLane.isPending} onClick={() => matchLane.mutate()} title="The AI may only choose places that are in your sheets">
              <Icon name="sparkles" /> Match the lane with AI
            </Button>
          )}
        </div>
      )}

      {data.options.map((option) => (
        <RateOptionCard
          key={`${option.sheet_id}-${option.tab}-${option.index}`}
          option={option}
          best={option === best}
          canWrite={canWrite}
          busy={draft.isPending && draft.variables === option}
          onDraft={() => draft.mutate(option)}
        />
      ))}

      <RateWebsites dealId={dealId} links={data.links} checks={data.checks} equipment={query.equipment} canWrite={canWrite} sheetFreight={bestFreight} />
      {openQuote && <QuoteBuilder quoteId={openQuote} onClose={() => setOpenQuote(null)} />}
    </div>
  );
}

function RateOptionCard({ option, best, canWrite, busy, onDraft }: { option: Option; best: boolean; canWrite: boolean; busy: boolean; onDraft: () => void }) {
  const [open, setOpen] = useState(best);
  return (
    <div className={best ? "rate-option best" : "rate-option"}>
      <div className="rate-option-head">
        <div className="grow" style={{ minWidth: 0 }}>
          <div className="row">
            {best && <Badge tone="green">best match</Badge>}
            <strong>{option.carrier ?? "Any carrier"}</strong>
            {option.equipment && <Badge tone="blue">{option.equipment}</Badge>}
            <ValidityBadge validTo={option.valid_to} />
            {option.transit_days ? <span className="small muted">{option.transit_days} days transit</span> : null}
          </div>
          <div className="small muted truncate" title={`${option.sheet_name} ▸ ${option.tab}`}>
            <Icon name="file" /> {option.sheet_name} ▸ {option.tab} · {option.origin} → {option.destination}
          </div>
        </div>
        <div className="rate-option-total">
          <strong>{money(option.total, option.currency)}</strong>
          <span className="small muted">all-in for this shipment</span>
        </div>
      </div>
      <div className="row" style={{ gap: 6 }}>
        {option.reasons.map((reason) => (
          <span key={reason} className="reason-chip">
            <Icon name="check" /> {reason}
          </span>
        ))}
      </div>
      {option.needs_attention.length > 0 && (
        <div className="alert warning small">
          Not added to the total (another currency, convert by hand): {option.needs_attention.join("; ")}
        </div>
      )}
      {open && (
        <div className="table-wrap">
          <table className="table compact">
            <thead>
              <tr>
                <th>Charge</th>
                <th>Unit</th>
                <th style={{ textAlign: "right" }}>Rate</th>
                <th>Row</th>
              </tr>
            </thead>
            <tbody>
              {option.rates.map((rate) => (
                <tr key={rate.id}>
                  <td>
                    {rate.description}
                    {rate.equipment ? <span className="muted"> · {rate.equipment}</span> : null}
                  </td>
                  <td>
                    per {rate.unit}
                    {rate.minimum ? <span className="muted"> (min {rate.minimum})</span> : null}
                  </td>
                  <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{money(rate.amount, rate.currency)}</td>
                  <td className="small muted">
                    {rate.tab} ▸ {rate.row_number}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="row between">
        <Button size="sm" variant="ghost" onClick={() => setOpen(!open)}>
          {open ? "Hide charges" : `Show ${option.rates.length} charges`}
        </Button>
        {canWrite && (
          <Button size="sm" variant={best ? "primary" : undefined} busy={busy} onClick={onDraft}>
            <Icon name="quote" /> Draft quote from these rates
          </Button>
        )}
      </div>
    </div>
  );
}

function RateWebsites({
  dealId,
  links,
  checks,
  equipment,
  canWrite,
  sheetFreight,
}: {
  dealId: string;
  links: Schemas["SourceLinkResponse"][];
  checks: Check[];
  equipment: string | null | undefined;
  canWrite: boolean;
  sheetFreight: Schemas["RateResponse"] | undefined;
}) {
  const [recording, setRecording] = useState<{ sourceId: string | null; name: string } | null>(null);
  const queryClient = useQueryClient();
  const toast = useToast();
  const remove = useMutation({
    mutationFn: (checkId: string) => unwrap(api.DELETE("/api/v1/pricing/checks/{check_id}", { params: { path: { check_id: checkId } } })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: PRICING_KEY }),
    onError: (error) => toast(describeError(error), "error"),
  });
  const cheapest = cheapestByCurrency(checks);
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="row between">
        <h3 className="section-title">Rate websites</h3>
        <Link to="/pricing?tab=sources" className="small">
          Manage websites
        </Link>
      </div>
      {links.length === 0 ? (
        <div className="small muted">
          Add the websites your team checks on the <Link to="/pricing?tab=sources">Pricing page</Link>; each opens with this lane filled in.
        </div>
      ) : (
        <div className="source-links">
          {links.map((link) => (
            <div key={link.source_id} className="source-link">
              <a className="btn sm" href={link.url} target="_blank" rel="noopener noreferrer">
                <Icon name="external" /> Open on {link.name}
              </a>
              {canWrite && (
                <Button size="sm" variant="ghost" onClick={() => setRecording({ sourceId: link.source_id, name: link.name })}>
                  <Icon name="plus" /> Record rate
                </Button>
              )}
            </div>
          ))}
          {canWrite && (
            <Button size="sm" variant="ghost" onClick={() => setRecording({ sourceId: null, name: "" })}>
              <Icon name="plus" /> Rate from another source
            </Button>
          )}
        </div>
      )}
      {recording && (
        <RecordCheckForm
          dealId={dealId}
          source={recording}
          equipment={equipment}
          defaultCurrency={sheetFreight?.currency ?? "USD"}
          onDone={() => setRecording(null)}
        />
      )}
      {checks.length > 0 && (
        <div className="table-wrap">
          <table className="table compact">
            <thead>
              <tr>
                <th>Source</th>
                <th style={{ textAlign: "right" }}>Rate</th>
                <th>Valid until</th>
                <th>Compared</th>
                <th>Recorded</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {checks.map((check) => {
                const lowest = cheapest.get(check.currency)?.id === check.id && checks.length > 1;
                const comparable = sheetFreight && sheetFreight.currency === check.currency && sheetFreight.unit === check.unit;
                return (
                  <tr key={check.id} className={lowest ? "row-best" : undefined}>
                    <td>
                      <strong>{check.source_name}</strong>
                      {check.note ? <div className="small muted">{check.note}</div> : null}
                    </td>
                    <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                      {money(check.amount, check.currency)} <span className="small muted">/ {check.unit}</span>
                      {lowest && (
                        <div>
                          <Badge tone="green">cheapest</Badge>
                        </div>
                      )}
                    </td>
                    <td>{check.valid_until ? formatDate(check.valid_until) : "—"}</td>
                    <td className="small">{comparable ? priceDifference(check.amount, sheetFreight.amount) : <span className="muted">—</span>}</td>
                    <td className="small muted">
                      {formatRelative(check.created_at)} · {check.checked_by}
                    </td>
                    <td>
                      {canWrite && (
                        <Button size="sm" variant="ghost" aria-label="Delete" busy={remove.isPending && remove.variables === check.id} onClick={() => remove.mutate(check.id)}>
                          <Icon name="trash" />
                        </Button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const UNITS = ["container", "kg", "cbm", "shipment", "truck", "pallet", "wagon"];

function RecordCheckForm({
  dealId,
  source,
  equipment,
  defaultCurrency,
  onDone,
}: {
  dealId: string;
  source: { sourceId: string | null; name: string };
  equipment: string | null | undefined;
  defaultCurrency: string;
  onDone: () => void;
}) {
  const [name, setName] = useState(source.name);
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState(defaultCurrency);
  const [unit, setUnit] = useState(equipment ? "container" : "shipment");
  const [eq, setEq] = useState(equipment ?? "");
  const [validUntil, setValidUntil] = useState("");
  const [note, setNote] = useState("");
  const queryClient = useQueryClient();
  const toast = useToast();
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/pricing/deals/{deal_id}/checks", {
          params: { path: { deal_id: dealId } },
          body: {
            source_id: source.sourceId,
            source_name: source.sourceId ? null : name.trim(),
            amount: Number(amount),
            currency: currency.trim().toUpperCase(),
            unit,
            equipment: eq.trim() || null,
            valid_until: validUntil || null,
            note: note.trim() || null,
          },
        }),
      ),
    onSuccess: () => {
      toast("Market rate recorded on the deal.");
      void queryClient.invalidateQueries({ queryKey: PRICING_KEY });
      onDone();
    },
  });
  const valid = Number(amount) > 0 && /^[A-Za-z]{3}$/.test(currency.trim()) && (source.sourceId || name.trim());
  return (
    <form
      className="check-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (valid) save.mutate();
      }}
    >
      <div className="check-form-grid">
        {source.sourceId ? (
          <div className="field">
            <span className="small muted">Source</span>
            <strong>{source.name}</strong>
          </div>
        ) : (
          <label className="field">
            <span className="small muted">Source</span>
            <input className="input" value={name} onChange={(event) => setName(event.target.value)} placeholder="Website or agent" maxLength={120} autoFocus />
          </label>
        )}
        <label className="field">
          <span className="small muted">Rate</span>
          <input className="input" type="number" min={0} step="0.01" value={amount} onChange={(event) => setAmount(event.target.value)} autoFocus={Boolean(source.sourceId)} required />
        </label>
        <label className="field">
          <span className="small muted">Currency</span>
          <input className="input" value={currency} onChange={(event) => setCurrency(event.target.value)} maxLength={3} style={{ textTransform: "uppercase" }} />
        </label>
        <label className="field">
          <span className="small muted">Per</span>
          <select className="select" value={unit} onChange={(event) => setUnit(event.target.value)}>
            {UNITS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="small muted">Equipment</span>
          <input className="input" value={eq} onChange={(event) => setEq(event.target.value)} placeholder="40HC" maxLength={30} />
        </label>
        <label className="field">
          <span className="small muted">Valid until</span>
          <input className="input" type="date" value={validUntil} onChange={(event) => setValidUntil(event.target.value)} />
        </label>
      </div>
      <input className="input" value={note} onChange={(event) => setNote(event.target.value)} placeholder="Note (optional), e.g. free time, routing" maxLength={500} />
      <ErrorAlert error={save.error} />
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" busy={save.isPending} disabled={!valid}>
          Save rate
        </Button>
      </div>
    </form>
  );
}
