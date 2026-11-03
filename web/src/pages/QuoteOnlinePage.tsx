/**
 * The customer's quote page (`/q/:token`, no sign-in): the quote from its email with its charges,
 * and Accept / Decline. The answer is recorded like a person recording it, and the deal's owner
 * is told. The link's token is signed; a wrong link shows "not found".
 */
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, describeError, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { BrandMark } from "../components/Layout";
import { Icon } from "../components/icons";
import { formatQuoteMoney } from "../lib/onlineQuote";

type OnlineQuote = Schemas["OnlineQuoteResponse"];

export function QuoteOnlinePage() {
  const { token = "" } = useParams();
  const quote = useQuery({
    queryKey: ["online-quote", token],
    queryFn: () => unwrap(api.GET("/api/v1/public/quotes/{token}", { params: { path: { token } } })),
    retry: false,
  });
  useEffect(() => {
    if (quote.data) document.title = `${quote.data.number} · ${quote.data.company_name}`;
  }, [quote.data]);

  return (
    <div className="quote-portal">
      <main className="quote-sheet">
        {quote.isLoading && <p className="muted">Loading your quote…</p>}
        {quote.error && (
          <div className="quote-missing">
            <Icon name="alert" />
            <h1>Quote not found</h1>
            <p className="muted">This link is not valid. Please use the link in the quote email, or reply to that email.</p>
          </div>
        )}
        {quote.data && <QuoteView data={quote.data} token={token} />}
      </main>
      <p className="quote-powered small muted">
        <BrandMark /> Sent with LaneReach
      </p>
    </div>
  );
}

function QuoteView({ data, token }: { data: OnlineQuote; token: string }) {
  const [current, setCurrent] = useState(data);
  const [choice, setChoice] = useState<"accept" | "decline" | null>(null);
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const decide = useMutation({
    mutationFn: (accepted: boolean) =>
      unwrap(
        api.POST("/api/v1/public/quotes/{token}/decision", {
          params: { path: { token } },
          body: { accepted, name: name.trim() || null, note: note.trim() || null },
        }),
      ),
    onSuccess: (next) => {
      setCurrent(next);
      setChoice(null);
    },
  });
  const money = (amount: number) => formatQuoteMoney(amount, current.currency);
  const facts: [string, string | null][] = [
    ["Service", current.service],
    ["Route", current.origin || current.destination ? `${current.origin ?? "?"} → ${current.destination ?? "?"}` : null],
    ["Incoterm", current.incoterm],
    ["Transit time", current.transit_days ? `${current.transit_days} days` : null],
    ["Valid until", new Date(`${current.valid_until}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })],
  ];

  return (
    <>
      <header className="quote-head" style={{ borderTopColor: current.primary_color }}>
        <div>
          <div className="small muted">{current.company_name} · Quotation</div>
          <h1>
            {current.number} · {current.title}
          </h1>
          {current.contact_first_name && <p className="muted">For {current.contact_first_name}</p>}
        </div>
        <StatusPill quote={current} />
      </header>

      <dl className="quote-facts">
        {facts
          .filter((fact): fact is [string, string] => Boolean(fact[1]))
          .map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
      </dl>

      <div className="quote-table-wrap">
        <table className="quote-table">
          <thead>
            <tr>
              <th>Charge</th>
              <th className="num">Quantity</th>
              <th className="num col-unit">Unit price</th>
              <th className="num">Amount</th>
            </tr>
          </thead>
          <tbody>
            {current.lines.map((line, index) => (
              <tr key={index}>
                <td>{line.description}</td>
                <td className="num">
                  {line.quantity} {line.unit}
                </td>
                <td className="num col-unit">{money(line.unit_price)}</td>
                <td className="num">{money(line.amount)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={2}>Total</td>
              <td className="col-unit" />
              <td className="num">{money(current.total_amount)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
      {current.notes.trim() && <p className="quote-notes">{current.notes}</p>}

      {current.can_decide ? (
        choice === null ? (
          <div className="quote-actions">
            <button type="button" className="quote-btn accept" style={{ background: current.primary_color }} onClick={() => setChoice("accept")}>
              <Icon name="check" /> Accept this quote
            </button>
            <button type="button" className="quote-btn" onClick={() => setChoice("decline")}>
              Decline
            </button>
          </div>
        ) : (
          <form
            className="quote-confirm"
            onSubmit={(event) => {
              event.preventDefault();
              decide.mutate(choice === "accept");
            }}
          >
            <strong>{choice === "accept" ? `Accept ${current.number} for ${money(current.total_amount)}?` : `Decline ${current.number}?`}</strong>
            <label>
              <span>Your name</span>
              <input value={name} maxLength={120} onChange={(event) => setName(event.target.value)} autoComplete="name" />
            </label>
            <label>
              <span>{choice === "accept" ? "Anything to add (for example the first shipment date)" : "What would work better? (optional)"}</span>
              <textarea value={note} maxLength={450} rows={3} onChange={(event) => setNote(event.target.value)} />
            </label>
            {decide.error && <p className="quote-error">{describeError(decide.error)}</p>}
            <div className="quote-actions">
              <button type="submit" className={`quote-btn ${choice === "accept" ? "accept" : "decline"}`} style={choice === "accept" ? { background: current.primary_color } : undefined} disabled={decide.isPending}>
                {decide.isPending ? "Sending…" : choice === "accept" ? "Confirm acceptance" : "Confirm decline"}
              </button>
              <button type="button" className="quote-btn" onClick={() => setChoice(null)} disabled={decide.isPending}>
                Back
              </button>
            </div>
          </form>
        )
      ) : (
        <p className="quote-done">
          {current.status === "accepted" && `Thank you! ${current.company_name} has your acceptance and will be in touch to book.`}
          {current.status === "rejected" && `Thank you for letting ${current.company_name} know.`}
          {current.expired && current.status !== "accepted" && current.status !== "rejected" && "This quote has expired. Reply to the quote email for an updated offer."}
        </p>
      )}
    </>
  );
}

function StatusPill({ quote }: { quote: OnlineQuote }) {
  if (quote.status === "accepted") return <span className="quote-pill accepted">Accepted</span>;
  if (quote.status === "rejected") return <span className="quote-pill rejected">Declined</span>;
  if (quote.expired) return <span className="quote-pill expired">Expired</span>;
  return <span className="quote-pill open">Open</span>;
}
