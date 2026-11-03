/** Landing sections: feature bento (tiles tilt towards the pointer), the AI orbit beside what it never does, how it works, and the comparison table. */
import { useEffect, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { Icon } from "../../components/icons";
import type { IconName } from "../../components/icons";
import { useReducedMotion } from "../../components/motion";
import { AI_NEVER, COMPARE_COLUMNS, COMPARE_ROWS, onlyLaneReach, tiltFromPointer } from "../../lib/landing";
import type { Support } from "../../lib/landing";
import { AiOrbit } from "./AiOrbit";

const GREETINGS = ["Hello", "Merhaba", "Hola", "مرحبا", "Bonjour", "Olá", "नमस्ते", "Hallo", "Ciao"];

function useCycle(length: number, every: number): number {
  const reduced = useReducedMotion();
  const [index, setIndex] = useState(0);
  useEffect(() => {
    if (reduced) return undefined;
    const timer = window.setInterval(() => setIndex((current) => (current + 1) % length), every);
    return () => window.clearInterval(timer);
  }, [length, every, reduced]);
  return index;
}

/** A feature tile that tilts a little towards a mouse pointer (not for touch or reduced motion). */
function Tile({ icon, title, children, art, className = "" }: { icon: IconName; title: string; children: ReactNode; art: ReactNode; className?: string }) {
  const reduced = useReducedMotion();
  const tilt = (event: ReactPointerEvent<HTMLElement>) => {
    if (reduced || event.pointerType !== "mouse") return;
    const box = event.currentTarget.getBoundingClientRect();
    const { rotateX, rotateY } = tiltFromPointer(event.clientX - box.left, event.clientY - box.top, box.width, box.height, 5);
    event.currentTarget.style.setProperty("--rx", `${rotateX}deg`);
    event.currentTarget.style.setProperty("--ry", `${rotateY}deg`);
  };
  const reset = (event: ReactPointerEvent<HTMLElement>) => {
    event.currentTarget.style.removeProperty("--rx");
    event.currentTarget.style.removeProperty("--ry");
  };
  return (
    <article className={`bento-tile spotlight ${className}`} onPointerMove={tilt} onPointerLeave={reset}>
      <div className="bento-art">{art}</div>
      <div className="bento-text">
        <h3>
          <Icon name={icon} /> {title}
        </h3>
        <p>{children}</p>
      </div>
    </article>
  );
}

function GreetingArt() {
  const index = useCycle(GREETINGS.length, 1400);
  return (
    <div className="art-greeting">
      <span key={index} className="art-hello" dir="auto">
        {GREETINGS[index]},
      </span>
      <div className="art-offices">
        {["Toronto", "Dubai", "Istanbul", "Chennai"].map((office, position) => (
          <span key={office} className={position === index % 4 ? "on" : ""}>
            {office}
          </span>
        ))}
      </div>
    </div>
  );
}

function CanvasArt() {
  const nodes: [number, number, string][] = [
    [20, 50, "Trigger"],
    [110, 22, "AI read"],
    [110, 78, "Wait 3d"],
    [200, 50, "Branch"],
    [290, 30, "Quote"],
    [290, 72, "Follow-up"],
  ];
  const edges: [number, number][] = [
    [0, 1],
    [0, 2],
    [1, 3],
    [2, 3],
    [3, 4],
    [3, 5],
  ];
  return (
    <svg viewBox="-12 0 352 100" className="art-canvas">
      {edges.map(([from, to]) => {
        const [x1, y1] = nodes[from] ?? [0, 0];
        const [x2, y2] = nodes[to] ?? [0, 0];
        return <path key={`${from}-${to}`} d={`M${x1 + 26},${y1} C${x1 + 60},${y1} ${x2 - 34},${y2} ${x2 - 26},${y2}`} className="art-edge" />;
      })}
      {nodes.map(([x, y, label], position) => (
        <g key={label} className="art-node" style={{ animationDelay: `${position * 0.25}s` }}>
          <rect x={x - 26} y={y - 10} width={52} height={20} rx={6} />
          <text x={x} y={y + 3.5} textAnchor="middle">
            {label}
          </text>
        </g>
      ))}
    </svg>
  );
}

function InboxArt() {
  const items = [
    ["AK", "Please quote 2×40HC", "Rate request"],
    ["EY", "Ofis dışındayım", "Out of office"],
    ["LM", "Can we talk Tuesday?", "Meeting"],
  ];
  const top = useCycle(items.length, 1800);
  return (
    <div className="art-inbox">
      {items.map((item, position) => {
        const order = (position - top + items.length) % items.length;
        return (
          <div key={item[0]} className="art-mail" style={{ ["--order" as string]: order }}>
            <span>{item[0]}</span>
            <div>
              <strong>{item[1]}</strong>
              <small>AI · {item[2]}</small>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function PipelineArt() {
  const stage = useCycle(4, 1300);
  return (
    <div className="art-pipeline">
      {["Requested", "Sourcing", "Quoted", "Won"].map((name, position) => (
        <div key={name} className="art-column">
          <small>{name}</small>
          <i />
          {position === stage && <b className="art-deal">2×40HC</b>}
        </div>
      ))}
    </div>
  );
}

function PricingArt() {
  const row = useCycle(4, 1100);
  const rows = [
    ["INMAA", "AEJEA", "40HC", "1,450"],
    ["INMAA", "AEJEA", "40HC", "1,520"],
    ["INNSA", "AEJEA", "40HC", "1,180"],
    ["CNSHA", "NLRTM", "40HC", "2,050"],
  ];
  return (
    <div className="art-sheet">
      <div className="art-sheet-grid">
        {rows.map((cells, position) => (
          <div key={position} className={position === row ? "art-row on" : "art-row"}>
            {cells.map((cell, cellIndex) => (
              <span key={cellIndex}>{cell}</span>
            ))}
          </div>
        ))}
      </div>
      <div className="art-total">
        <small>Draft quote</small>
        <strong>{rows[row]?.[3]} USD</strong>
      </div>
    </div>
  );
}

function GuardArt({ checks }: { checks: number }) {
  const lit = useCycle(checks + 3, 380);
  return (
    <div className="art-guard">
      <svg viewBox="0 0 120 120">
        {Array.from({ length: checks }, (_, position) => {
          const angle = (position / checks) * Math.PI * 2 - Math.PI / 2;
          return <circle key={position} cx={60 + Math.cos(angle) * 46} cy={60 + Math.sin(angle) * 46} r={6} className={position < lit ? "tick on" : "tick"} />;
        })}
      </svg>
      <Icon name="shield" />
    </div>
  );
}

function AssistantArt() {
  return (
    <div className="art-chat">
      <span className="art-q">How do I send a quote from a deal?</span>
      <span className="art-a">
        Open the deal → <b>Quotes</b> → <b>Send</b>. It goes in the customer&apos;s thread <i>· Quotes › Send</i>
      </span>
    </div>
  );
}

export function Bento({ checks, steps, lines }: { checks?: number; steps?: number; lines?: number }) {
  return (
    <div className="bento">
      <Tile icon="globe" title="Outreach that speaks local" art={<GreetingArt />} className="wide">
        Agents per office with their own mailbox and signature, scripts per country and language, sent in the recipient&apos;s business hours{lines ? ` across ${lines} service lines` : ""}.
      </Tile>
      <Tile icon="workflow" title="Workflows you can see" art={<CanvasArt />} className="wide">
        Drag {steps ? `${steps} ` : ""}triggers, actions, AI and protection steps onto a canvas. Undo, tidy up, test-run, publish.
      </Tile>
      <Tile icon="mail" title="One inbox for every agent" art={<InboxArt />}>
        Every reply sorted by AI, with drafts ready, snooze, assign and labels.
      </Tile>
      <Tile icon="trending" title="Pipeline built in" art={<PipelineArt />}>
        A rate request opens a deal; drag it to Won when booked.
      </Tile>
      <Tile icon="pricing" title="Quotes from your rate sheets" art={<PricingArt />}>
        Your Excel stays in SharePoint; matching rates price the draft.
      </Tile>
      <Tile icon="shield" title={checks ? `${checks} checks before every email` : "Checks before every email"} art={<GuardArt checks={checks ?? 11} />}>
        Suppression, caps, send windows, rotation and a kill switch, enforced by the engine.
      </Tile>
      <Tile icon="chat" title="An assistant that knows the product" art={<AssistantArt />} className="wide full">
        Ask how to do anything or describe a workflow; it answers from the help with sources and drafts the workflow for you to review.
      </Tile>
    </div>
  );
}

export function AiSection({ tasks }: { tasks: string[] }) {
  return (
    <div className="ai-split">
      <div className="ai-does">
        <h3>
          <Icon name="sparkles" /> What the AI does
        </h3>
        <AiOrbit tasks={tasks} />
      </div>
      <div className="ai-never">
        <h3>
          <Icon name="shield" /> What it never does
        </h3>
        <ul>
          {AI_NEVER.map((item) => (
            <li key={item}>
              <Icon name="x" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
        <p className="ai-note">
          Works with Anthropic Claude or Azure OpenAI. Every AI decision is logged with its prompt version, and you choose per reply type whether drafts are sent or wait for a person.
        </p>
      </div>
    </div>
  );
}

const STEPS: { icon: IconName; title: string; text: string }[] = [
  { icon: "offices", title: "Set up your desks", text: "Offices, agents with their own mailboxes, and scripts from your SharePoint library in every language." },
  { icon: "workflow", title: "Draw the workflow", text: "Pick a starter or describe it to the assistant: intro, wait, follow-up, branch on the reply." },
  { icon: "sparkles", title: "AI reads every reply", text: "Rate requests become deals with the shipment filled in; out-of-office and opt-outs are handled for you." },
  { icon: "quote", title: "Pricing quotes faster", text: "Matching rates from your sheets and rate websites, a priced draft, sent in the customer's thread." },
];

export function HowItWorks() {
  return (
    <ol className="how-steps">
      {STEPS.map((step, position) => (
        <li key={step.title} style={{ ["--i" as string]: position }}>
          <span className="how-number">{position + 1}</span>
          <span className="how-icon">
            <Icon name={step.icon} />
          </span>
          <h3>{step.title}</h3>
          <p>{step.text}</p>
        </li>
      ))}
    </ol>
  );
}

const SUPPORT_LABELS: Record<Support, string> = { yes: "Built in", partial: "Partly, or with add-ons", no: "Not typically" };

export function Compare() {
  const [only, setOnly] = useState(false);
  const rows = only ? onlyLaneReach(COMPARE_ROWS) : COMPARE_ROWS;
  return (
    <div className="compare">
      <div className="compare-toolbar">
        <label className="compare-toggle">
          <input type="checkbox" checked={only} onChange={(event) => setOnly(event.target.checked)} />
          <span /> Only what is unique to LaneReach
        </label>
        <span className="compare-legend">
          <i className="yes" /> Built in <i className="partial" /> Partly / add-ons <i className="no" /> Not typically
        </span>
      </div>
      <p className="compare-swipe" aria-hidden>
        Swipe the table sideways to see every column <Icon name="route" />
      </p>
      <div className="compare-wrap">
        <table className="compare-table">
          <thead>
            <tr>
              <th />
              {COMPARE_COLUMNS.map((column, position) => (
                <th key={column} className={position === 0 ? "us" : undefined}>
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.feature}>
                <th scope="row">
                  {row.feature}
                  <small>{row.hint}</small>
                </th>
                {row.values.map((value, position) => (
                  <td key={position} className={position === 0 ? "us" : undefined}>
                    <span className={`support ${value}`} title={SUPPORT_LABELS[value]} aria-label={SUPPORT_LABELS[value]}>
                      {value === "yes" ? <Icon name="check" /> : value === "partial" ? "~" : "–"}
                    </span>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="compare-note">
        Columns describe what tools in each category typically offer out of the box, not any one product; many can be extended with integrations. LaneReach is built for freight forwarders end to end.
      </p>
    </div>
  );
}
