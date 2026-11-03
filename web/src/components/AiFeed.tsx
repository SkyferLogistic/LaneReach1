/**
 * A live-looking feed of what LaneReach's AI and engine do with replies: a new event slides in on
 * top every couple of seconds. Example events (labelled), not a customer's data.
 */
import { useEffect, useState } from "react";
import { Icon } from "./icons";
import type { IconName } from "./icons";
import { useReducedMotion } from "./motion";

export interface FeedEvent {
  icon: IconName;
  tone: "ai" | "deal" | "quote" | "guard" | "stop";
  title: string;
  detail: string;
}

export const FEED_EVENTS: FeedEvent[] = [
  { icon: "sparkles", tone: "ai", title: "Reply classified: rate request · 94%", detail: "Gulf Importers · English" },
  { icon: "route", tone: "ai", title: "Shipment extracted", detail: "2 × 40HC · INMAA → AEJEA · FOB · ready 15 Oct" },
  { icon: "trending", tone: "deal", title: "Deal opened in Quote requested", detail: "Handed over to pricing" },
  { icon: "pricing", tone: "quote", title: "Quote drafted from Ocean rates.xlsx", detail: "3,040.00 USD · valid 14 days · waiting for review" },
  { icon: "clock", tone: "ai", title: "Out of office until 20 Oct", detail: "Anadolu Lojistik · Turkish · follow-up moved" },
  { icon: "shield", tone: "guard", title: "11 checks passed · email sent", detail: "09:12 recipient time · Dubai office" },
  { icon: "x", tone: "stop", title: "Unsubscribe detected", detail: "Address suppressed in every workflow" },
  { icon: "reply", tone: "ai", title: "Reply drafted in Spanish", detail: "Office voice · waiting for review" },
];

const VISIBLE = 4;

export function AiFeed({ events = FEED_EVENTS, every = 2300 }: { events?: FeedEvent[]; every?: number }) {
  const reduced = useReducedMotion();
  const [head, setHead] = useState(0);
  useEffect(() => {
    if (reduced) return undefined;
    const timer = window.setInterval(() => setHead((current) => current + 1), every);
    return () => window.clearInterval(timer);
  }, [every, reduced]);
  const items = Array.from({ length: VISIBLE }, (_, offset) => {
    const index = (((head - offset) % events.length) + events.length) % events.length;
    return { key: head - offset, event: events[index] };
  });
  return (
    <div className="ai-feed" aria-label="Example activity">
      <div className="ai-feed-head">
        <span className="ai-feed-live">
          <i /> Live example
        </span>
        <span>What LaneReach does with replies</span>
      </div>
      <ol>
        {items.map(({ key, event }, position) =>
          event ? (
            <li key={key} className={`ai-feed-item ${event.tone}`} style={{ ["--pos" as string]: position }}>
              <span className="ai-feed-icon">
                <Icon name={event.icon} />
              </span>
              <span className="ai-feed-text">
                <strong>{event.title}</strong>
                <small>{event.detail}</small>
              </span>
              <span className="ai-feed-time">{position === 0 ? "now" : `${position * 2}m`}</span>
            </li>
          ) : null,
        )}
      </ol>
    </div>
  );
}
