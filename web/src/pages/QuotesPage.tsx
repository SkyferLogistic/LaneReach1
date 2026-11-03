/** Quotes: every quotation the pricing team has built, what is waiting on the customer, what expires soon. */
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { Icon } from "../components/icons";
import { Page } from "../components/Layout";
import { CountUp, SkeletonRows } from "../components/motion";
import { Empty, ErrorAlert, LinePill, PageHeader, Stat, Tabs } from "../components/ui";
import { QUOTES_KEY, QuoteBuilder, QuoteStatusBadge } from "../crm/QuoteBuilder";
import { formatDate } from "../lib/format";

type View = "all" | "draft" | "sent" | "expiring" | "accepted" | "rejected" | "expired";

function totalsByCurrency(quotes: Schemas["QuoteResponse"][]): string {
  const sums = new Map<string, number>();
  for (const quote of quotes) sums.set(quote.currency, (sums.get(quote.currency) ?? 0) + quote.total_amount);
  if (sums.size === 0) return "—";
  return [...sums.entries()].map(([currency, sum]) => `${Math.round(sum).toLocaleString()} ${currency}`).join(" · ");
}

export function QuotesPage() {
  const [view, setView] = useState<View>("all");
  // `/quotes?quote=<id>` (from the assistant or a notification) opens that quote.
  const [params] = useSearchParams();
  const [open, setOpen] = useState<string | null>(params.get("quote"));
  const all = useQuery({ queryKey: [...QUOTES_KEY, "all"], queryFn: () => unwrap(api.GET("/api/v1/crm/quotes")) });
  const expiring = useQuery({
    queryKey: [...QUOTES_KEY, "expiring"],
    queryFn: () => unwrap(api.GET("/api/v1/crm/quotes", { params: { query: { expiring_within_days: 3 } } })),
  });
  const quotes = useMemo(() => all.data ?? [], [all.data]);
  const count = (status: string) => quotes.filter((quote) => quote.status === status).length;
  const list = view === "all" ? quotes : view === "expiring" ? (expiring.data ?? []) : quotes.filter((quote) => quote.status === view);
  const decided = count("accepted") + count("rejected");
  const winRate = decided === 0 ? null : Math.round((count("accepted") / decided) * 100);
  return (
    <Page>
      <PageHeader
        eyebrow={<><Icon name="quote" /> CRM</>}
        title="Quotes"
        description="Quotations with charge lines, sent in the customer's thread. Accepted quotes set the deal value; sent quotes expire automatically after their validity date."
      />
      <div className="quote-stats" style={{ marginBottom: 16 }}>
        <Stat label="Waiting on customer" value={<CountUp value={count("sent")} />} hint={totalsByCurrency(quotes.filter((quote) => quote.status === "sent"))} />
        <Stat label="Expiring in 3 days" value={<CountUp value={expiring.data?.length ?? 0} />} hint="Follow up before they lapse" />
        <Stat label="Accepted" value={<CountUp value={count("accepted")} />} hint={totalsByCurrency(quotes.filter((quote) => quote.status === "accepted"))} />
        <Stat label="Win rate" value={winRate === null ? "—" : `${winRate}%`} hint={`${decided} decided`} />
      </div>
      <Tabs
        value={view}
        onChange={setView}
        tabs={[
          { id: "all", label: `All (${quotes.length})` },
          { id: "draft", label: `Drafts (${count("draft")})` },
          { id: "sent", label: `Sent (${count("sent")})` },
          { id: "expiring", label: <span style={{ color: expiring.data?.length ? "var(--warning, #b45309)" : undefined }}>Expiring soon ({expiring.data?.length ?? 0})</span> },
          { id: "accepted", label: `Accepted (${count("accepted")})` },
          { id: "rejected", label: `Rejected (${count("rejected")})` },
          { id: "expired", label: `Expired (${count("expired")})` },
        ]}
      />
      <ErrorAlert error={all.error ?? expiring.error} />
      {all.isLoading && <SkeletonRows rows={5} />}
      {!all.isLoading && list.length === 0 && (
        <Empty art="ship" title={view === "all" ? "No quotes yet" : "Nothing here"}>
          {view === "all" ? (
            <>
              Open a deal in the <Link to="/pipeline">pipeline</Link> and choose <strong>Quotes → New quote</strong>. Charge presets fill in the usual lines for air, ocean, road, rail and more.
            </>
          ) : null}
        </Empty>
      )}
      {list.length > 0 && (
        <div className="card">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Quote</th>
                  <th>Customer</th>
                  <th>Service</th>
                  <th>Lane</th>
                  <th>Valid until</th>
                  <th style={{ textAlign: "right" }}>Total</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {list.map((quote) => (
                  <tr key={quote.id} className="clickable" onClick={() => setOpen(quote.id)}>
                    <td>
                      <strong>{quote.number}</strong>
                      <div className="small muted">{quote.title}</div>
                    </td>
                    <td>
                      <Link to={`/contacts/${quote.contact_id}`} onClick={(event) => event.stopPropagation()}>
                        {quote.contact_name ?? "Contact"}
                      </Link>
                      {quote.company_name && <div className="small muted">{quote.company_name}</div>}
                    </td>
                    <td><LinePill line={quote.service_line} /></td>
                    <td className="small">{[quote.origin, quote.destination].filter(Boolean).join(" → ") || "—"}</td>
                    <td className="small">{formatDate(quote.valid_until)}</td>
                    <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                      <strong>{quote.total_amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong> {quote.currency}
                    </td>
                    <td><QuoteStatusBadge quote={quote} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {open && <QuoteBuilder quoteId={open} onClose={() => setOpen(null)} />}
    </Page>
  );
}
