/**
 * "Try it with your own email": the visitor pastes a reply and LaneReach's real reply AI reads it
 * (`POST /site/try-ai`): the kind of reply, how sure it is, the shipment and what LaneReach would do
 * next. One click on an example (in one of several languages) reads it straight away; pasted text is
 * read with the button. Nothing is stored; a few tries per minute.
 */
import { useEffect, useRef, useState } from "react";
import { api, describeError, unwrap } from "../../api/client";
import type { Schemas } from "../../api/client";
import { Icon } from "../../components/icons";
import { useReducedMotion } from "../../components/motion";
import { BUCKET_LABELS, TRY_EXAMPLES, shipmentRows } from "../../lib/landing";

type Result = Schemas["TryAiResponse"];

const READING = ["Reading the reply…", "Detecting the language…", "Deciding what it means…", "Pulling out the shipment…"];
const MISSING_LABELS: Record<string, string> = {
  origin: "origin",
  destination: "destination",
  mode: "mode",
  container_type: "container type",
  container_quantity: "number of containers",
  weight_kg: "weight",
  commodity: "commodity",
  cargo_ready_date: "ready date",
  incoterm: "Incoterm",
};

export function TryAi() {
  const [subject, setSubject] = useState(TRY_EXAMPLES[0]?.subject ?? "");
  const [text, setText] = useState(TRY_EXAMPLES[0]?.text ?? "");
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState(0);
  // A ref, not just `busy`, so two quick clicks cannot start two reads before React re-renders.
  const reading = useRef(false);
  const reduced = useReducedMotion();

  useEffect(() => {
    if (!busy || reduced) return undefined;
    const timer = window.setInterval(() => setPhase((current) => (current + 1) % READING.length), 900);
    return () => window.clearInterval(timer);
  }, [busy, reduced]);

  const run = async (readSubject: string, readText: string) => {
    if (reading.current || readText.trim().length < 10) return;
    reading.current = true;
    setBusy(true);
    setError("");
    setPhase(0);
    try {
      const response = await unwrap(api.POST("/api/v1/site/try-ai", { body: { subject: readSubject, text: readText } }));
      setResult(response);
    } catch (caught) {
      setError(describeError(caught));
      setResult(null);
    } finally {
      reading.current = false;
      setBusy(false);
    }
  };

  const ready = text.trim().length >= 10;
  return (
    <div className="try-ai">
      <div className="try-compose">
        <p className="try-hint">
          <Icon name="sparkles" /> One click on an example reads it right away, or paste your own reply below.
        </p>
        <div className="try-examples" role="group" aria-label="Example replies (one click reads them)">
          {TRY_EXAMPLES.map((example) => (
            <button
              key={`${example.code}-${example.label}`}
              type="button"
              className={example.text === text ? "on" : ""}
              disabled={busy}
              aria-label={`Read the ${example.language} example: ${example.label}`}
              onClick={() => {
                setSubject(example.subject);
                setText(example.text);
                setResult(null);
                void run(example.subject, example.text);
              }}
            >
              <i>{example.code}</i>
              <span dir="auto" lang={example.code.toLowerCase()}>
                {example.label}
              </span>
            </button>
          ))}
        </div>
        <label className="try-field">
          <span>Subject</span>
          <input value={subject} maxLength={200} dir="auto" onChange={(event) => setSubject(event.target.value)} />
        </label>
        <label className="try-field grow">
          <span>
            The reply <small>{text.length}/2000</small>
          </span>
          <textarea value={text} maxLength={2000} rows={7} dir="auto" placeholder="Paste a customer's reply in any language…" onChange={(event) => setText(event.target.value)} />
        </label>
        <button type="button" className={busy ? "site-btn primary wide try-go busy" : "site-btn primary wide try-go"} disabled={!ready || busy} onClick={() => void run(subject, text)}>
          <Icon name="sparkles" /> {busy ? READING[phase] : "Read it with LaneReach AI"}
        </button>
        <p className="try-fine">
          <Icon name="shield" /> Nothing you paste is stored, and nothing is sent. The same reply AI as in the product.
        </p>
      </div>

      <div className={`try-result${busy ? " reading" : ""}`} aria-live="polite">
        {busy ? (
          <div className="try-thinking">
            <div className="try-orb">
              <span />
              <span />
              <span />
              <Icon name="sparkles" />
            </div>
            <strong>{READING[phase]}</strong>
          </div>
        ) : error ? (
          <div className="try-thinking">
            <div className="try-orb idle">
              <Icon name="alert" />
            </div>
            <strong>{error}</strong>
          </div>
        ) : result ? (
          <Outcome result={result} />
        ) : (
          <div className="try-thinking">
            <div className="try-orb idle">
              <span />
              <Icon name="mail" />
            </div>
            <strong>Pick an example or paste a reply</strong>
            <span>See how LaneReach would sort it, what it pulls out and what it does next.</span>
          </div>
        )}
      </div>
    </div>
  );
}

function Outcome({ result }: { result: Result }) {
  const bucket = BUCKET_LABELS[result.bucket] ?? { label: result.bucket, tone: "info" as const };
  const rows = shipmentRows(result.shipment);
  const missing = ((result.shipment?.missing_fields as string[] | undefined) ?? []).map((field) => MISSING_LABELS[field] ?? field.replace(/_/g, " "));
  const percent = Math.round(result.confidence * 100);
  const circumference = 2 * Math.PI * 30;
  return (
    <div className="try-outcome">
      <div className="try-head">
        <svg viewBox="0 0 72 72" className={`try-ring ${bucket.tone}`}>
          <circle cx="36" cy="36" r="30" className="track" />
          <circle cx="36" cy="36" r="30" className="value" strokeDasharray={circumference} style={{ ["--offset" as string]: circumference * (1 - result.confidence) }} />
          <text x="36" y="41" textAnchor="middle">
            {percent}%
          </text>
        </svg>
        <div className="grow">
          <small>Classified as</small>
          <h3 className={`try-bucket ${bucket.tone}`}>{bucket.label}</h3>
          <div className="try-chips">
            <span>
              <Icon name="globe" /> {result.language.toUpperCase()}
            </span>
            <span>{result.source === "rules" ? "Decided by a rule" : "Decided by the AI model"}</span>
            {result.needs_human && <span className="warn">A person will check it</span>}
          </div>
        </div>
      </div>
      {result.summary && <p className="try-summary">{result.summary}</p>}
      {rows.length > 0 && (
        <div className="try-rows">
          {rows.map((row, index) => (
            <div key={row.label} style={{ animationDelay: `${index * 70}ms` }}>
              <small>{row.label}</small>
              <strong>{row.value}</strong>
            </div>
          ))}
        </div>
      )}
      {missing.length > 0 && (
        <p className="try-missing">
          <Icon name="alert" /> Pricing still needs: {missing.join(", ")}.
        </p>
      )}
      <div className="try-next">
        <small>What LaneReach does next (default policy)</small>
        <ol>
          {result.next_steps.map((step, index) => (
            <li key={step} style={{ animationDelay: `${200 + index * 120}ms` }}>
              <span>
                <Icon name="check" />
              </span>
              {step}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
