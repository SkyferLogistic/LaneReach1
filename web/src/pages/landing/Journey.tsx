/**
 * "Journey of a reply": one reply from first email to booked, on a line that fills as you scroll
 * (a small envelope rides along it). Stages light up as the line reaches them. Each stage says who
 * does the work: the engine, the AI or a person.
 */
import { useEffect, useRef, useState } from "react";
import { Icon } from "../../components/icons";
import type { IconName } from "../../components/icons";
import { journeyStage } from "../../lib/landing";

interface Stage {
  icon: IconName;
  who: "Engine" | "AI" | "Person" | "Inbox";
  title: string;
  text: string;
  soon?: boolean;
}

/** The stages; the new AI skills are described as "coming soon" until the running version lists them. */
function stages(newAiLive: boolean): Stage[] {
  const soon = newAiLive ? "" : "Coming soon: ";
  return [
    { icon: "send", who: "Engine", title: "The intro goes out", text: "From the office's own mailbox, in the recipient's language and business hours, and only after every send check passes." },
    { icon: "mail", who: "Inbox", title: "A reply lands", text: "In one inbox for every agent, whichever mailbox it was written to and whatever language it is in." },
    { icon: "sparkles", who: "AI", title: "The AI reads it", text: "Rate request, question, out of office or opt-out. The shipment is pulled out with port codes, and what is still missing is flagged." },
    { icon: "trending", who: "Engine", title: "A deal opens", text: `On the pipeline with the shipment filled in. ${soon}an AI score with its reasons, so the hottest deal is worked first.` },
    { icon: "quote", who: "Person", title: "A quote is drafted", text: "Priced from your own rate sheets. A person checks it and sends it in the customer's thread." },
    { icon: "clock", who: "AI", title: "If it goes quiet", text: "After the days you set, the AI suggests the next step and drafts a follow-up for a person to review.", soon: !newAiLive },
    { icon: "check", who: "Person", title: "Booked", text: `Drag the deal to Won. ${soon}the weekly AI digest shows which lanes are winning.` },
  ];
}

export function Journey({ newAiLive }: { newAiLive: boolean }) {
  const list = useRef<HTMLDivElement>(null);
  const [stage, setStage] = useState(-1);
  const items = stages(newAiLive);
  const count = items.length;

  useEffect(() => {
    let frame = 0;
    const measure = () => {
      frame = 0;
      const element = list.current;
      if (!element) return;
      const box = element.getBoundingClientRect();
      // The line is "filled" up to a point a little below the middle of the screen.
      const progress = Math.max(0, Math.min(1, (window.innerHeight * 0.6 - box.top) / Math.max(1, box.height)));
      element.style.setProperty("--fill", progress.toFixed(4));
      setStage(journeyStage(progress, count));
    };
    const onScroll = () => {
      if (!frame) frame = window.requestAnimationFrame(measure);
    };
    measure();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [count]);

  return (
    <div className="journey" ref={list}>
      <span className="journey-rail" aria-hidden>
        <span className="journey-fill" />
        <span className="journey-packet">
          <Icon name="mail" />
        </span>
      </span>
      <ol>
        {items.map((item, position) => (
          <li key={item.title} className={position <= stage ? "on" : ""}>
            <span className="journey-dot" aria-hidden>
              <Icon name={item.icon} />
            </span>
            <div className="journey-card">
              <span className={`journey-who ${item.who.toLowerCase()}`}>
                {item.who}
                {item.soon && <em>Coming soon</em>}
              </span>
              <h3>{item.title}</h3>
              <p>{item.text}</p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
