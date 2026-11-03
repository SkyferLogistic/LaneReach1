/** FAQ: straight answers in an accordion (one open at a time), with a way to ask anything else. */
import { useId, useState } from "react";
import { Icon } from "../../components/icons";
import { faqFor } from "../../lib/landing";

export function Faq({ tasks, onContact }: { tasks: string[]; onContact: () => void }) {
  const [open, setOpen] = useState<number | null>(0);
  const base = useId();
  return (
    <div className="faq">
      <aside className="faq-side">
        <span className="faq-mark" aria-hidden>
          ?
        </span>
        <h3>Straight answers</h3>
        <p>What the AI does on its own, where your data lives, and what LaneReach does not do.</p>
        <button type="button" className="site-btn ghost" onClick={onContact}>
          Ask us something else <Icon name="send" />
        </button>
      </aside>
      <div className="faq-list">
        {faqFor(tasks).map((item, position) => {
          const isOpen = open === position;
          const id = `${base}-${position}`;
          return (
            <div key={item.question} className={isOpen ? "faq-item open" : "faq-item"}>
              <h3>
                <button type="button" id={`${id}-q`} aria-expanded={isOpen} aria-controls={`${id}-a`} onClick={() => setOpen(isOpen ? null : position)}>
                  <span>{item.question}</span>
                  <i aria-hidden />
                </button>
              </h3>
              <div className="faq-answer" id={`${id}-a`} role="region" aria-labelledby={`${id}-q`} inert={!isOpen}>
                <div>
                  <p>{item.answer}</p>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
