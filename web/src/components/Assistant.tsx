/**
 * "Ask LaneReach": questions about your own data (read-only lookups with links), questions answered
 * from the help articles (with sources), and workflow drafts from a description (saved as drafts
 * only). Opened from the top bar, Ctrl+J or `askAssistant()`.
 */
import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, describeError, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { useSession } from "../auth/session";
import { renderMarkdown } from "../lib/markdown";
import { usePermission } from "./confirm";
import { DataPanel } from "./DataAssistant";
import { Icon } from "./icons";
import { Badge, Button, Drawer, ErrorAlert, Tabs } from "./ui";

const OPEN_EVENT = "lanereach:assistant";

export interface AssistantRequest {
  question?: string;
  /** Ask straight away (true) or only prefill the box (false). */
  send?: boolean;
}

/** Open the assistant from anywhere, optionally with a question to ask or prefill. */
export function askAssistant(question?: string, send = true): void {
  window.dispatchEvent(new CustomEvent<AssistantRequest>(OPEN_EVENT, { detail: { question, send } }));
}

/** Listen for `askAssistant()` calls and Ctrl+J. */
export function useAssistantOpener(open: (request: AssistantRequest) => void): void {
  useEffect(() => {
    const onOpen = (event: Event) => open((event as CustomEvent<AssistantRequest>).detail ?? {});
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "j") {
        event.preventDefault();
        open({});
      }
    };
    window.addEventListener(OPEN_EVENT, onOpen);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener(OPEN_EVENT, onOpen);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);
}

interface Turn {
  role: "user" | "assistant";
  text: string;
  reply?: Schemas["AskResponse"];
}

const SUGGESTIONS = [
  "How do I build my first campaign?",
  "How do I connect an agent's mailbox?",
  "What checks run before an email is sent?",
  "How do I send a quote from a deal?",
  "What happens when a contact replies?",
  "Can LaneReach send WhatsApp messages?",
];

const MODE_NOTES: Record<string, { tone: "gray" | "amber" | "red" | "green"; label: string }> = {
  ai: { tone: "green", label: "Answered from the help" },
  extractive: { tone: "gray", label: "Quoted from the help (no AI key configured)" },
  sources_only: { tone: "amber", label: "No verified answer: closest help sections" },
  not_found: { tone: "red", label: "Not in the help" },
};

export function AssistantDrawer({ request, onClose }: { request: AssistantRequest; onClose: () => void }) {
  // A question passed in (from a help link) goes to the help; otherwise start with your data.
  const [tab, setTab] = useState<"data" | "ask" | "draft">(request.question ? "ask" : "data");
  return (
    <Drawer
      wide
      title={
        <span className="row">
          <span className="assistant-mark">
            <Icon name="sparkles" />
          </span>
          Ask LaneReach
        </span>
      }
      onClose={onClose}
    >
      <div className="stack assistant">
        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            { id: "data", label: "Your data" },
            { id: "ask", label: "How LaneReach works" },
            { id: "draft", label: "Draft a workflow" },
          ]}
        />
        {tab === "data" && <DataPanel />}
        {tab === "ask" && <AskPanel request={request} onDraft={() => setTab("draft")} />}
        {tab === "draft" && <DraftPanel onClose={onClose} />}
      </div>
    </Drawer>
  );
}

function AskPanel({ request, onDraft }: { request: AssistantRequest; onDraft: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState(request.send === false ? (request.question ?? "") : "");
  const end = useRef<HTMLDivElement>(null);
  const ask = useMutation({
    mutationFn: (text: string) =>
      unwrap(
        api.POST("/api/v1/assistant/ask", {
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
    if (request.question && request.send !== false) send(request.question);
    // Only the question the drawer was opened with is sent automatically.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, ask.isPending]);

  return (
    <>
      <p className="small muted" style={{ margin: 0 }}>
        Answers come only from LaneReach's help articles and the live list of workflow steps, with the sources. The assistant says when LaneReach does not do
        something and never changes anything.
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
                <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: "wrap" }}>
                  <Badge tone={turn.reply.supported ? MODE_NOTES[turn.reply.mode]?.tone ?? "gray" : "red"}>
                    {turn.reply.supported ? (MODE_NOTES[turn.reply.mode]?.label ?? turn.reply.mode) : turn.reply.mode === "sources_only" ? MODE_NOTES.sources_only?.label : "LaneReach does not do this (per the help)"}
                  </Badge>
                  <Badge tone="gray">{turn.reply.search === "hybrid" ? "vector + text search" : "text search"}</Badge>
                </div>
                {turn.reply.sources.length > 0 && (
                  <div className="sources">
                    {turn.reply.sources.map((source) => (
                      <Link key={source.id} to={source.url} className="source-chip" title={source.excerpt}>
                        <Icon name="file" /> {source.title} › {source.heading}
                      </Link>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <div className="md">{turn.text}</div>
            )}
          </div>
        ))}
        {ask.isPending && (
          <div className="bubble assistant typing-dots" aria-label="Thinking">
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
          placeholder="Ask how to do something in LaneReach…"
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              send(question);
            }
          }}
          aria-label="Your question"
        />
        <Button variant="primary" type="submit" busy={ask.isPending} disabled={question.trim().length < 2} aria-label="Ask">
          <Icon name="send" />
        </Button>
      </form>
      <button type="button" className="btn ghost sm" style={{ alignSelf: "flex-start" }} onClick={onDraft}>
        <Icon name="workflow" /> Describe a workflow and get a draft instead
      </button>
    </>
  );
}

function DraftPanel({ onClose }: { onClose: () => void }) {
  const { can } = useSession();
  const permission = usePermission("workflows:write", "admins and campaign managers");
  const [description, setDescription] = useState("");
  const draft = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/assistant/draft-workflow", { body: { description: description.trim() } })),
  });
  const result = draft.data;
  return (
    <div className="stack">
      <p className="small muted" style={{ margin: 0 }}>
        Describe what should happen, in your words. The assistant builds a workflow from LaneReach's steps, checks it with the same validator as the canvas and
        saves it as a <strong>draft</strong>. It never publishes it, enrolls anyone or sends email: you review it on the canvas first.
      </p>
      <textarea
        className="textarea"
        rows={5}
        value={description}
        onChange={(event) => setDescription(event.target.value)}
        placeholder="For example: when a contact is tagged gulf-importers, send the intro email, wait 4 business days, send a follow-up, then create a call task for the office owner."
        aria-label="Describe the workflow"
      />
      <ErrorAlert error={draft.error} />
      <div className="row">
        <Button
          variant="primary"
          className={permission.allowed ? undefined : "locked"}
          busy={draft.isPending}
          disabled={description.trim().length < 10}
          onClick={() => (can("workflows:write") ? draft.mutate() : permission.explain())}
        >
          <Icon name="sparkles" /> Draft the workflow
        </Button>
      </div>
      {result && (
        <div className="card draft-result">
          <div className="card-body stack">
            <div className="row between">
              <strong>{result.name}</strong>
              <Badge tone="violet">draft</Badge>
            </div>
            <p className="small" style={{ margin: 0 }}>
              {result.summary}
            </p>
            <ol className="draft-steps">
              {result.steps.map((step) => (
                <li key={step.id}>
                  <strong>{step.name}</strong> <span className="muted small">({step.id})</span>
                </li>
              ))}
            </ol>
            {result.assumptions.length > 0 && (
              <div className="alert info small">
                <strong>Assumptions to check:</strong>
                <ul>
                  {result.assumptions.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            )}
            {result.problems.length > 0 && (
              <div className="alert warning small">
                <strong>Fix on the canvas before publishing:</strong>
                <ul>
                  {result.problems.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            )}
            <div>
              <Link to={result.url} className="btn primary sm" onClick={onClose}>
                <Icon name="workflow" /> Open on the canvas
              </Link>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
