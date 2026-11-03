/**
 * "Your data" in Ask LaneReach: questions about the company's own deals, quotes, contacts, rate
 * sheets, inbox and activity. The assistant looks things up with read-only tools and answers with
 * links to the records; every number comes from what it looked up. It never changes anything.
 */
import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, describeError, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { renderMarkdown } from "../lib/markdown";
import { Icon } from "./icons";
import type { IconName } from "./icons";
import { Badge, Button } from "./ui";

interface Turn {
  role: "user" | "assistant";
  text: string;
  reply?: Schemas["DataAnswerResponse"];
}

const SUGGESTIONS = [
  "Which hot deals do we have?",
  "Which deals went quiet?",
  "Which quotes expire soon?",
  "What is waiting for an answer in the inbox?",
  "How many replies did we get this week?",
  "What are our rates from Chennai to Jebel Ali?",
];

const KIND_ICONS: Record<Schemas["DataRecordResponse"]["kind"], IconName> = {
  deal: "leads",
  quote: "quote",
  contact: "contacts",
  rate: "pricing",
  conversation: "mail",
  help: "file",
};

const MODE_NOTES: Record<Schemas["DataAnswerResponse"]["mode"], { tone: "green" | "gray" | "amber" | "red"; label: string }> = {
  ai: { tone: "green", label: "From your data" },
  extractive: { tone: "gray", label: "Listed from your data (no AI key configured)" },
  partial: { tone: "amber", label: "No verified answer: what I found" },
  budget: { tone: "red", label: "AI budget used up" },
};

export function DataPanel() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const ask = useMutation({
    mutationFn: (text: string) =>
      unwrap(
        api.POST("/api/v1/assistant/ask-data", {
          body: { question: text, history: turns.slice(-6).map((turn) => ({ role: turn.role, text: turn.reply?.answer ?? turn.text })) },
        }),
      ),
    onSuccess: (reply) => setTurns((current) => [...current, { role: "assistant", text: reply.answer, reply }]),
    onError: (error) => setTurns((current) => [...current, { role: "assistant", text: describeError(error) }]),
  });
  const send = (text: string) => {
    const clean = text.trim();
    if (clean.length < 2 || ask.isPending) return;
    setTurns((current) => [...current, { role: "user", text: clean }]);
    setQuestion("");
    ask.mutate(clean);
  };
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, ask.isPending]);

  return (
    <>
      <p className="small muted" style={{ margin: 0 }}>
        Ask about your deals, quotes, contacts, rate sheets, inbox and activity. The assistant looks them up (read-only), answers only with numbers it found and links
        the records. It never sends or changes anything.
      </p>
      <div className="chat" aria-live="polite">
        {turns.length === 0 && (
          <div className="chat-suggestions">
            {SUGGESTIONS.map((item) => (
              <button key={item} type="button" className="chip-toggle" onClick={() => send(item)}>
                {item}
              </button>
            ))}
          </div>
        )}
        {turns.map((turn, index) => (
          <div key={index} className={`bubble ${turn.role}`}>
            {turn.role === "assistant" && turn.reply ? (
              <>
                <div className="md" dangerouslySetInnerHTML={{ __html: renderMarkdown(turn.reply.answer) }} />
                {turn.reply.records.length > 0 && (
                  <div className="sources">
                    {turn.reply.records.map((record) => (
                      <Link key={record.ref} to={record.url} className="source-chip" title={record.label}>
                        <Icon name={KIND_ICONS[record.kind]} /> {record.label}
                      </Link>
                    ))}
                  </div>
                )}
                <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: "wrap" }}>
                  <Badge tone={MODE_NOTES[turn.reply.mode].tone}>{MODE_NOTES[turn.reply.mode].label}</Badge>
                  {turn.reply.steps.length > 0 && (
                    <span className="small muted">
                      <Icon name="search" /> Looked at {turn.reply.steps.map((step) => step.summary).join(", ")}
                    </span>
                  )}
                </div>
              </>
            ) : (
              <div className="md">{turn.text}</div>
            )}
          </div>
        ))}
        {ask.isPending && (
          <div className="bubble assistant typing-dots" aria-label="Looking it up">
            <span />
            <span />
            <span />
          </div>
        )}
        <div ref={end} />
      </div>
      <form
        className="chat-composer"
        onSubmit={(event) => {
          event.preventDefault();
          send(question);
        }}
      >
        <textarea
          className="textarea"
          rows={2}
          value={question}
          placeholder="Ask about your deals, quotes, rates, inbox…"
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              send(question);
            }
          }}
          aria-label="Your question about your data"
        />
        <Button variant="primary" type="submit" busy={ask.isPending} disabled={question.trim().length < 2} aria-label="Ask">
          <Icon name="send" />
        </Button>
      </form>
    </>
  );
}
