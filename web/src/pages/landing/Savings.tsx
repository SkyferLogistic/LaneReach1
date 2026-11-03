/** Time-saved calculator: the visitor's own assumptions, added up openly (an estimate, not a promise). */
import { useState } from "react";
import { CountUp } from "../../components/motion";
import { SAVINGS_DEFAULTS, estimateSavings } from "../../lib/landing";
import type { SavingsInput } from "../../lib/landing";

const SLIDERS: { key: keyof SavingsInput; label: string; min: number; max: number; step: number; unit: string }[] = [
  { key: "agents", label: "Sales agents", min: 1, max: 100, step: 1, unit: "" },
  { key: "repliesPerAgentPerDay", label: "Replies per agent per day", min: 1, max: 80, step: 1, unit: "" },
  { key: "minutesPerReply", label: "Minutes to read, log and route a reply by hand", min: 1, max: 15, step: 0.5, unit: "min" },
  { key: "quotesPerWeek", label: "Quote requests per week", min: 0, max: 500, step: 5, unit: "" },
  { key: "minutesPerQuote", label: "Minutes to prepare a quote by hand", min: 5, max: 120, step: 5, unit: "min" },
  { key: "quoteShareSaved", label: "Share of quote time saved by rate-sheet drafts", min: 0, max: 80, step: 5, unit: "%" },
];

export function Savings() {
  const [input, setInput] = useState<SavingsInput>(SAVINGS_DEFAULTS);
  const result = estimateSavings(input);
  const replyShare = result.totalHours ? (result.replyHours / result.totalHours) * 100 : 0;
  return (
    <div className="savings">
      <div className="savings-inputs">
        {SLIDERS.map((slider) => (
          <label key={slider.key} className="savings-slider">
            <span>
              {slider.label}
              <b>
                {input[slider.key]}
                {slider.unit && ` ${slider.unit}`}
              </b>
            </span>
            <input
              type="range"
              min={slider.min}
              max={slider.max}
              step={slider.step}
              value={input[slider.key]}
              style={{ ["--fill" as string]: `${((input[slider.key] - slider.min) / (slider.max - slider.min)) * 100}%` }}
              onChange={(event) => setInput((current) => ({ ...current, [slider.key]: Number(event.target.value) }))}
            />
          </label>
        ))}
      </div>
      <div className="savings-result">
        <small>Your estimate</small>
        <div className="savings-total">
          <span className="savings-number">
            <CountUp value={result.totalHours} />
          </span>
          <span>hours a month</span>
        </div>
        <p>
          about <b>{result.fte}</b> full-time {result.fte === 1 ? "person" : "people"} back on selling and pricing
        </p>
        <div className="savings-bar" aria-hidden>
          <span className="replies" style={{ width: `${replyShare}%` }} />
          <span className="quotes" style={{ width: `${100 - replyShare}%` }} />
        </div>
        <ul className="savings-legend">
          <li>
            <i className="replies" /> Replies read and routed by AI <b>{result.replyHours} h</b>
          </li>
          <li>
            <i className="quotes" /> Quotes drafted from your rates <b>{result.quoteHours} h</b>
          </li>
        </ul>
        <p className="savings-note">An estimate from the numbers you set, assuming 21 working days a month; your results depend on your team and lanes.</p>
      </div>
    </div>
  );
}
