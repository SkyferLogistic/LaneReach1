/**
 * Review queue: AI output waiting for a person. Approving a reply draft sends it in the
 * original thread (guardrails still apply); edit-and-approve keeps the original for evaluation.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, api, describeError, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { LoadMore, usePaged } from "../api/paging";
import { keys } from "../api/queries";
import { useSession } from "../auth/session";
import { Page } from "../components/Layout";
import { Badge, BucketBadge, Button, Card, Empty, ErrorAlert, Json, KeyValues, Loading, PageHeader, StatusBadge, Tabs, useToast } from "../components/ui";
import { formatDateTime, formatRelative, humanize, percent } from "../lib/format";

type Item = Schemas["ReviewItemResponse"];
type Filter = "pending" | "approved" | "edited" | "rejected" | "all";

interface Classification {
  bucket?: string;
  confidence?: number;
  summary?: string;
  reasoning_short?: string;
  needs_human?: boolean;
  language?: string;
}

export function ReviewQueuePage() {
  const [params, setParams] = useSearchParams();
  const [filter, setFilter] = useState<Filter>("pending");
  const selectedId = params.get("item");
  const page = usePaged([...keys.reviews, filter], (cursor) =>
    unwrap(
      api.GET("/api/v1/ai/reviews", {
        params: { query: { status: filter === "all" ? undefined : filter, cursor, limit: 50 } },
      }),
    ),
  );
  useEffect(() => {
    if (!selectedId && page.items[0]) setParams({ item: page.items[0].id }, { replace: true });
  }, [selectedId, page.items, setParams]);

  return (
    <Page>
      <PageHeader
        title="Review queue"
        description="Replies the AI was unsure about, drafts waiting for approval, and localized templates. Nothing here is sent until someone approves it."
      />
      <Tabs<Filter>
        value={filter}
        onChange={(next) => {
          setFilter(next);
          setParams({}, { replace: true });
        }}
        tabs={[
          { id: "pending", label: "Pending" },
          { id: "approved", label: "Approved" },
          { id: "edited", label: "Edited" },
          { id: "rejected", label: "Rejected" },
          { id: "all", label: "All" },
        ]}
      />
      <div className="grid stack-narrow" style={{ gridTemplateColumns: "minmax(280px, 380px) 1fr", alignItems: "start" }}>
        <Card flush>
          {page.isLoading && <Loading />}
          <ErrorAlert error={page.error} />
          {!page.isLoading && page.items.length === 0 && <Empty art="inbox" title="Nothing here">The queue is clear.</Empty>}
          <table className="table">
            <tbody>
              {page.items.map((item) => {
                const classification = (item.payload.classification ?? {}) as Classification;
                return (
                  <tr
                    key={item.id}
                    className="clickable"
                    style={item.id === selectedId ? { background: "var(--accent-soft)" } : undefined}
                    onClick={() => setParams({ item: item.id })}
                  >
                    <td>
                      <div className="row">
                        <strong className="truncate" style={{ maxWidth: 200 }}>
                          {String(item.payload.contact_email ?? humanize(item.kind))}
                        </strong>
                        {classification.bucket && <BucketBadge bucket={classification.bucket} />}
                      </div>
                      <div className="small muted truncate" style={{ maxWidth: 320 }}>
                        {item.reason ?? humanize(item.kind)}
                      </div>
                      <div className="small muted">{formatRelative(item.created_at)}</div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <LoadMore {...page} />
        </Card>
        {selectedId ? <ReviewDetail itemId={selectedId} /> : <Card><Empty title="Select an item" /></Card>}
      </div>
    </Page>
  );
}

function ReviewDetail({ itemId }: { itemId: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const { can } = useSession();
  const item = useQuery({
    queryKey: [...keys.reviews, "item", itemId],
    queryFn: () => unwrap(api.GET("/api/v1/ai/reviews/{item_id}", { params: { path: { item_id: itemId } } })),
  });
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<unknown>(null);
  useEffect(() => {
    if (item.data) {
      setSubject(String(item.data.payload.subject ?? ""));
      setBody(String(item.data.payload.body ?? ""));
      setNote("");
      setProblem(null);
    }
  }, [item.data]);

  const done = (message: string) => {
    toast(message);
    void queryClient.invalidateQueries({ queryKey: keys.reviews });
    void queryClient.invalidateQueries({ queryKey: keys.outbox });
  };
  const approve = useMutation({
    mutationFn: async (data: Item) => {
      const edited = subject !== String(data.payload.subject ?? "") || body !== String(data.payload.body ?? "");
      const options = { params: { path: { item_id: data.id } } };
      return edited
        ? unwrap(api.POST("/api/v1/ai/reviews/{item_id}/edit-and-approve", { ...options, body: { subject, body } }))
        : unwrap(api.POST("/api/v1/ai/reviews/{item_id}/approve", options));
    },
    onSuccess: (result) =>
      done(result.sent_message_id ? "Approved and sent in the original thread." : "Approved."),
    onError: setProblem,
  });
  const reject = useMutation({
    mutationFn: (data: Item) =>
      unwrap(api.POST("/api/v1/ai/reviews/{item_id}/reject", { params: { path: { item_id: data.id } }, body: { note: note || null } })),
    onSuccess: () => done("Rejected. Nothing was sent."),
    onError: setProblem,
  });

  if (item.isLoading) return <Card><Loading /></Card>;
  if (!item.data) return <Card><ErrorAlert error={item.error} /></Card>;
  const data = item.data;
  const payload = data.payload as Record<string, unknown>;
  const classification = (payload.classification ?? {}) as Classification;
  const pending = data.status === "pending";
  const isDraft = data.kind === "reply_draft" || data.kind === "localized_template";
  const deferred = problem instanceof ApiError && problem.status === 409;

  return (
    <div className="stack">
      <Card
        title={
          <div className="row">
            <h2>{humanize(data.kind)}</h2>
            <StatusBadge value={data.status} />
          </div>
        }
        actions={data.contact_id && <Link to={`/contacts/${data.contact_id}`}>Contact</Link>}
      >
        <div className="stack">
          {data.reason && <div className="alert info">Why a person is needed: {data.reason}</div>}
          {classification.bucket && (
            <div className="row">
              <BucketBadge bucket={classification.bucket} />
              <span className="small">confidence {percent(classification.confidence)}</span>
              {classification.needs_human && <Badge tone="amber">classifier asked for a human</Badge>}
              {classification.language && <Badge>{classification.language}</Badge>}
            </div>
          )}
          {classification.summary && <div>{classification.summary}</div>}
          {classification.reasoning_short && <div className="small muted">{classification.reasoning_short}</div>}
          {payload.inbound_excerpt ? (
            <div>
              <div className="small muted">Their message: {String(payload.inbound_subject ?? "")}</div>
              <pre className="email-preview">{String(payload.inbound_excerpt)}</pre>
            </div>
          ) : null}
          {payload.shipment ? (
            <details>
              <summary className="small muted">Extracted shipment</summary>
              <Json value={payload.shipment} />
            </details>
          ) : null}
        </div>
      </Card>
      {isDraft && (
        <Card
          title={
            data.kind === "localized_template"
              ? `Localized template (${String(payload.language ?? "")})`
              : payload.written_by === "built_in"
                ? "Draft reply · built-in message"
                : Number(payload.grounded_on ?? 0) > 0
                  ? `Draft reply · based on ${Number(payload.grounded_on)} of your past answers`
                  : "Draft reply"
          }
        >
          <div className="stack">
            <label className="field">
              <span>Subject</span>
              <input className="input" value={subject} disabled={!pending} onChange={(event) => setSubject(event.target.value)} />
            </label>
            <label className="field">
              <span>Body</span>
              <textarea className="textarea" style={{ minHeight: 240 }} value={body} disabled={!pending} onChange={(event) => setBody(event.target.value)} />
            </label>
            {data.kind === "reply_draft" && pending && (
              <span className="field-help">
                Approving sends this in the same thread. Suppressed contacts and frozen companies are still blocked; the kill switch and mailbox limits defer it (approve again later).
              </span>
            )}
          </div>
        </Card>
      )}
      {!isDraft && pending && (
        <Card>
          <p className="small" style={{ margin: 0 }}>
            No draft: this is a classification check. Approve to confirm the automatic handling, or reject with a note.
          </p>
        </Card>
      )}
      {problem !== null && (
        <div className={deferred ? "alert warning" : "alert error"}>{describeError(problem)}</div>
      )}
      {pending && can("ai:review") && (
        <div className="row">
          <input className="input" style={{ flex: 1, minWidth: 200 }} placeholder="Note (optional, for rejections)" value={note} onChange={(event) => setNote(event.target.value)} />
          <Button variant="danger" busy={reject.isPending} onClick={() => reject.mutate(data)}>
            Reject
          </Button>
          <Button variant="success" busy={approve.isPending} onClick={() => approve.mutate(data)}>
            {isDraft &&
            (subject !== String(payload.subject ?? "") || body !== String(payload.body ?? ""))
              ? "Save edits & approve"
              : data.kind === "reply_draft"
                ? "Approve & send"
                : "Approve"}
          </Button>
        </div>
      )}
      {!pending && (
        <Card>
          <KeyValues
            items={[
              ["Decided by", data.reviewer],
              ["Decided at", data.decided_at ? formatDateTime(data.decided_at) : null],
              ["Note", data.decision_note],
              ["Sent as", data.sent_message_id ? <span className="mono small">{data.sent_message_id}</span> : null],
            ]}
          />
        </Card>
      )}
    </div>
  );
}
