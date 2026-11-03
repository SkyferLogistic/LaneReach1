/**
 * "Summarize with AI" for an inbox conversation: where it stands, what the prospect wants, their
 * open questions and the next step. Shown on screen only; nothing is stored or sent.
 */
import { useMutation } from "@tanstack/react-query";
import { useEffect } from "react";
import { api, describeError, unwrap } from "../api/client";
import { useSession } from "../auth/session";
import { Icon } from "./icons";
import { Badge, Button } from "./ui";

const SENTIMENT_TONES = { positive: "green", neutral: "gray", negative: "red" } as const;

export function ThreadSummary({ threadId }: { threadId: string }) {
  const { can } = useSession();
  const summarize = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/inbox/conversation/summary", { params: { query: { thread_id: threadId } } })),
  });
  // A different conversation starts without a summary.
  useEffect(() => summarize.reset(), [threadId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!can("ai:run")) return null;
  const data = summarize.data;
  if (!data) {
    return (
      <div className="thread-ai-bar">
        <span className="small muted">
          <Icon name="sparkles" /> Catch up in seconds: what they want, open questions and the next step.
        </span>
        <Button size="sm" busy={summarize.isPending} onClick={() => summarize.mutate()}>
          <Icon name="sparkles" /> Summarize with AI
        </Button>
        {summarize.error && <span className="field-error">{describeError(summarize.error)}</span>}
      </div>
    );
  }
  return (
    <section className="thread-ai" aria-label="AI summary">
      <header className="row between nowrap">
        <strong className="row" style={{ gap: 6 }}>
          <Icon name="sparkles" /> AI summary
          <Badge tone={SENTIMENT_TONES[data.sentiment]}>{data.sentiment}</Badge>
          <span className="small muted">· {data.messages_read} messages read</span>
        </strong>
        <span className="row" style={{ gap: 4 }}>
          <Button size="sm" variant="ghost" busy={summarize.isPending} onClick={() => summarize.mutate()} aria-label="Summarize again">
            <Icon name="sync" />
          </Button>
          <Button size="sm" variant="ghost" onClick={() => summarize.reset()} aria-label="Close the summary">
            <Icon name="x" />
          </Button>
        </span>
      </header>
      <p>{data.summary}</p>
      <div className="thread-ai-grid">
        {data.customer_wants.length > 0 && (
          <div>
            <small>They want</small>
            <ul>
              {data.customer_wants.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        )}
        {data.open_questions.length > 0 && (
          <div>
            <small>Still to answer</small>
            <ul>
              {data.open_questions.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
      <div className="thread-ai-next">
        <Icon name="route" /> <strong>Next step:</strong> {data.next_step}
      </div>
    </section>
  );
}
