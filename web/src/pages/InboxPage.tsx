/**
 * Unified inbox: every agent mailbox in one place. Folders and filters on the left, the
 * conversations in the middle, the thread and a composer on the right. Replies go out in the
 * thread from the agent who owns it, with the agent's signature, through every send guardrail.
 */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, describeError, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { keys, useInboxCounts, useOffices, useSenders } from "../api/queries";
import { useSession } from "../auth/session";
import { Icon } from "../components/icons";
import { SkeletonRows } from "../components/motion";
import { Badge, BucketBadge, Button, Empty, ErrorAlert, LinePill, useToast } from "../components/ui";
import { formatDateTime, formatRelative, humanize } from "../lib/format";
import { countryFlag, initials } from "../lib/freight";
import { FOLDERS, isFolder, neighbour, snoozeOptions, withoutQuotedText } from "../lib/inbox";
import type { Folder } from "../lib/inbox";
import { ThreadSummary } from "../components/ThreadSummary";

type Conversation = Schemas["ConversationResponse"];
type InboxMessage = Schemas["InboxMessageResponse"];
type ConversationUpdate = Schemas["ConversationUpdateRequest"];

interface Filters {
  sender_id?: string;
  office_id?: string;
  label?: string;
  search?: string;
  mine?: boolean;
}

function useConversations(folder: Folder, filters: Filters) {
  return useInfiniteQuery({
    queryKey: [...keys.inbox, "list", folder, filters],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/inbox/conversations", {
          params: { query: { folder, ...filters, cursor: pageParam ?? undefined, limit: 30 } },
        }),
      ),
    getNextPageParam: (page) => page.next_cursor,
    refetchInterval: 30_000,
  });
}

function useConversation(threadId: string | null) {
  return useQuery({
    queryKey: [...keys.inbox, "thread", threadId],
    queryFn: () => unwrap(api.GET("/api/v1/inbox/conversation", { params: { query: { thread_id: threadId ?? "" } } })),
    enabled: Boolean(threadId),
  });
}

function useUpdateConversation() {
  const queryClient = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({ threadId, change }: { threadId: string; change: ConversationUpdate }) =>
      unwrap(api.PATCH("/api/v1/inbox/conversation", { params: { query: { thread_id: threadId } }, body: change })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: keys.inbox }),
    onError: (error) => toast(describeError(error), "error"),
  });
}

export function InboxPage() {
  const [params, setParams] = useSearchParams();
  const folderParam = params.get("folder");
  const folder: Folder = isFolder(folderParam) ? folderParam : "inbox";
  const threadId = params.get("thread");
  const [filters, setFilters] = useState<Filters>({});
  const [searchText, setSearchText] = useState("");
  const counts = useInboxCounts();
  const list = useConversations(folder, filters);
  const update = useUpdateConversation();
  const items = useMemo(() => list.data?.pages.flatMap((page) => page.items) ?? [], [list.data]);
  const composer = useRef<HTMLTextAreaElement>(null);
  const { can } = useSession();
  const responder = can("ai:review");

  // Search after the user stops typing.
  useEffect(() => {
    const timer = window.setTimeout(() => setFilters((current) => ({ ...current, search: searchText.trim() || undefined })), 300);
    return () => window.clearTimeout(timer);
  }, [searchText]);

  const select = useCallback(
    (id: string | null) => {
      const next = new URLSearchParams(params);
      if (id) next.set("thread", id);
      else next.delete("thread");
      setParams(next, { replace: true });
    },
    [params, setParams],
  );
  const chooseFolder = (id: Folder) => {
    const next = new URLSearchParams();
    if (id !== "inbox") next.set("folder", id);
    setParams(next, { replace: true });
  };
  const selected = items.find((item) => item.thread_id === threadId) ?? null;

  const act = useCallback(
    (change: ConversationUpdate, after?: "next") => {
      if (!threadId) return;
      if (after === "next") {
        // Done and snoozed conversations leave the list: move on to the one below (or above).
        const ids = items.map((item) => item.thread_id);
        const below = neighbour(ids, threadId, 1);
        const above = neighbour(ids, threadId, -1);
        select(below !== threadId ? below : above !== threadId ? above : null);
      }
      update.mutate({ threadId, change });
    },
    [threadId, items, select, update],
  );

  // j/k move, e done, u unread, r reply, s snooze until tomorrow.
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return;
      const ids = items.map((item) => item.thread_id);
      if (event.key === "j" || event.key === "ArrowDown") {
        event.preventDefault();
        select(neighbour(ids, threadId, 1));
      } else if (event.key === "k" || event.key === "ArrowUp") {
        event.preventDefault();
        select(neighbour(ids, threadId, -1));
      } else if (event.key === "Escape") {
        select(null);
      } else if (!threadId || !responder) {
        return;
      } else if (event.key === "e") {
        act(selected?.done ? { done: false } : { done: true }, selected?.done ? undefined : "next");
      } else if (event.key === "u") {
        act({ read: false });
      } else if (event.key === "s") {
        const tomorrow = snoozeOptions(new Date())[1];
        if (tomorrow) act({ snoozed_until: tomorrow.until.toISOString() }, "next");
      } else if (event.key === "r") {
        event.preventDefault();
        composer.current?.focus();
      }
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [items, threadId, selected, select, act, responder]);

  return (
    <div className="content flush page-in">
      <div className={threadId ? "inbox has-thread" : "inbox"}>
        <FolderRail
          folder={folder}
          counts={counts.data}
          filters={filters}
          onFolder={chooseFolder}
          onFilters={setFilters}
        />
        <section className="inbox-list" aria-label="Conversations">
          <div className="inbox-list-head">
            <div className="input-icon">
              <Icon name="search" />
              <input
                className="input"
                placeholder="Search name, email or subject"
                value={searchText}
                onChange={(event) => setSearchText(event.target.value)}
                aria-label="Search conversations"
              />
            </div>
            <div className="row between small muted" style={{ marginTop: 8 }}>
              <span>{FOLDERS.find((item) => item.id === folder)?.hint}</span>
              <span className="hide-mobile"><span className="kbd">j</span> <span className="kbd">k</span> move</span>
            </div>
          </div>
          <ErrorAlert error={list.error} />
          {list.isLoading && <div style={{ padding: 12 }}><SkeletonRows rows={6} /></div>}
          {!list.isLoading && items.length === 0 && (
            <Empty art="inbox" title={folder === "inbox" ? "Inbox zero" : "Nothing here"}>
              {folder === "inbox" ? "Every reply is handled. New replies appear here as they arrive." : "No conversation matches."}
            </Empty>
          )}
          <ul className="inbox-items">
            {items.map((item) => (
              <ConversationRow key={item.thread_id} item={item} active={item.thread_id === threadId} onSelect={() => select(item.thread_id)} />
            ))}
          </ul>
          {list.hasNextPage && (
            <div style={{ padding: 12, textAlign: "center" }}>
              <Button size="sm" busy={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
                Load more
              </Button>
            </div>
          )}
        </section>
        <section className="inbox-thread" aria-label="Conversation">
          {threadId ? (
            <Thread
              key={threadId}
              threadId={threadId}
              composer={composer}
              responder={responder}
              onBack={() => select(null)}
              onAct={act}
            />
          ) : (
            <div className="inbox-placeholder">
              <Icon name="mail" />
              <strong>Pick a conversation</strong>
              <span className="small muted">
                <span className="kbd">j</span>/<span className="kbd">k</span> to move, <span className="kbd">e</span> done,{" "}
                <span className="kbd">s</span> snooze, <span className="kbd">u</span> unread, <span className="kbd">r</span> reply
              </span>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function FolderRail({
  folder,
  counts,
  filters,
  onFolder,
  onFilters,
}: {
  folder: Folder;
  counts: Schemas["FolderCountsResponse"] | undefined;
  filters: Filters;
  onFolder: (folder: Folder) => void;
  onFilters: (filters: Filters) => void;
}) {
  const senders = useSenders();
  const offices = useOffices();
  const labels = useQuery({
    queryKey: [...keys.inbox, "labels"],
    queryFn: () => unwrap(api.GET("/api/v1/inbox/labels")),
  });
  const count = (id: Folder): number | undefined => {
    if (!counts || id === "sent" || id === "all") return undefined;
    return counts[id];
  };
  return (
    <aside className="inbox-rail" aria-label="Folders">
      <div className="inbox-rail-title">Inbox</div>
      {FOLDERS.map((item) => (
        <button
          key={item.id}
          type="button"
          className={item.id === folder ? "inbox-folder active" : "inbox-folder"}
          onClick={() => onFolder(item.id)}
          title={item.hint}
        >
          <span>{item.label}</span>
          {count(item.id) ? <span className={item.id === "unread" ? "nav-count" : "inbox-count"}>{count(item.id)}</span> : null}
        </button>
      ))}
      <label className="inbox-toggle">
        <input type="checkbox" checked={Boolean(filters.mine)} onChange={(event) => onFilters({ ...filters, mine: event.target.checked || undefined })} />
        Assigned to me
      </label>
      <div className="inbox-rail-title">Agent mailbox</div>
      <select
        className="select"
        value={filters.sender_id ?? ""}
        onChange={(event) => onFilters({ ...filters, sender_id: event.target.value || undefined })}
        aria-label="Agent mailbox"
      >
        <option value="">All agents</option>
        {(senders.data ?? []).map((sender) => (
          <option key={sender.id} value={sender.id}>
            {sender.display_name}
          </option>
        ))}
      </select>
      <div className="inbox-rail-title">Office</div>
      <select
        className="select"
        value={filters.office_id ?? ""}
        onChange={(event) => onFilters({ ...filters, office_id: event.target.value || undefined })}
        aria-label="Office"
      >
        <option value="">All offices</option>
        {(offices.data ?? []).map((office) => (
          <option key={office.id} value={office.id}>
            {office.name}
          </option>
        ))}
      </select>
      {(labels.data?.length ?? 0) > 0 && (
        <>
          <div className="inbox-rail-title">Labels</div>
          <div className="chips">
            {labels.data?.map((label) => (
              <button
                key={label}
                type="button"
                className={filters.label === label ? "chip-toggle on" : "chip-toggle"}
                onClick={() => onFilters({ ...filters, label: filters.label === label ? undefined : label })}
              >
                <Icon name="tag" /> {label}
              </button>
            ))}
          </div>
        </>
      )}
    </aside>
  );
}

function ConversationRow({ item, active, onSelect }: { item: Conversation; active: boolean; onSelect: () => void }) {
  const classes = ["inbox-item", active ? "active" : "", item.unread ? "unread" : ""].filter(Boolean).join(" ");
  return (
    <li>
      <button type="button" className={classes} onClick={onSelect} aria-current={active ? "true" : undefined}>
        <span className={`avatar ${item.service_line ?? "any"}`}>{initials(item.contact_name || item.contact_email || "?")}</span>
        <span className="inbox-item-body">
          <span className="row between" style={{ gap: 8 }}>
            <strong className="truncate">
              {item.unread && <span className="unread-dot" aria-label="Unread" />}
              {item.contact_name || item.contact_email}
            </strong>
            <span className="small muted" title={formatDateTime(item.last_message_at)} style={{ flex: "none" }}>
              {formatRelative(item.last_message_at)}
            </span>
          </span>
          <span className="small muted truncate">
            {countryFlag(item.country)} {item.company_name ?? item.contact_email}
            {item.agent_name ? ` · ${item.agent_name}` : ""}
          </span>
          <span className="truncate inbox-subject">{item.subject}</span>
          <span className="small muted inbox-preview">
            {item.last_direction === "outbound" && <Icon name="reply" />} {item.preview}
          </span>
          <span className="chips" style={{ marginTop: 4 }}>
            {item.last_bucket && <BucketBadge bucket={item.last_bucket} />}
            {item.pending_draft_id && <Badge tone="violet"><Icon name="sparkles" /> AI draft</Badge>}
            {item.awaiting_reply && !item.done && <Badge tone="amber">Awaiting reply</Badge>}
            {item.snoozed_until && <Badge tone="blue"><Icon name="clock" /> {formatRelative(item.snoozed_until)}</Badge>}
            {item.labels.map((label) => (
              <Badge key={label}><Icon name="tag" /> {label}</Badge>
            ))}
          </span>
        </span>
      </button>
    </li>
  );
}

function Thread({
  threadId,
  composer,
  responder,
  onBack,
  onAct,
}: {
  threadId: string;
  composer: React.RefObject<HTMLTextAreaElement | null>;
  responder: boolean;
  onBack: () => void;
  onAct: (change: ConversationUpdate, after?: "next") => void;
}) {
  const detail = useConversation(threadId);
  const update = useUpdateConversation();
  const conversation = detail.data?.conversation;
  const end = useRef<HTMLDivElement>(null);

  // Opening a conversation reads it.
  useEffect(() => {
    if (responder && conversation?.unread) update.mutate({ threadId, change: { read: true } });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId, conversation?.unread, responder]);
  useEffect(() => {
    // Block body: newer browsers return a promise from scrollIntoView, which is not a cleanup.
    end.current?.scrollIntoView({ block: "end" });
  }, [detail.data?.messages.length]);

  if (detail.isLoading) return <div style={{ padding: 20 }}><SkeletonRows rows={5} /></div>;
  if (detail.error || !detail.data || !conversation) return <div style={{ padding: 20 }}><ErrorAlert error={detail.error} /></div>;
  return (
    <div className="thread">
      <header className="thread-head">
        <button type="button" className="btn ghost sm icon show-mobile" onClick={onBack} aria-label="Back to the list">
          <Icon name="reply" />
        </button>
        <div className="thread-title">
          <h2 className="truncate" style={{ margin: 0 }}>{conversation.subject}</h2>
          <div className="small muted truncate">
            {conversation.contact_id ? (
              <Link to={`/contacts/${conversation.contact_id}`}>{conversation.contact_name}</Link>
            ) : (
              conversation.contact_name
            )}{" "}
            &lt;{conversation.contact_email}&gt; · {countryFlag(conversation.country)} {conversation.company_name ?? ""}
            {conversation.agent_name && <> · agent {conversation.agent_name}</>}
          </div>
        </div>
        {responder && <ThreadActions conversation={conversation} onAct={onAct} />}
      </header>
      {conversation.pending_draft_id && (
        <div className="thread-banner">
          <Icon name="sparkles" /> An AI reply draft waits for approval.{" "}
          <Link to="/reviews">Open the review queue</Link>
        </div>
      )}
      <ThreadSummary threadId={threadId} />
      <div className="thread-messages">
        {detail.data.messages.map((message) => (
          <MessageBubble key={message.id} message={message} contactName={conversation.contact_name} />
        ))}
        <div ref={end} />
      </div>
      {responder ? (
        <Composer conversation={conversation} messages={detail.data.messages} composer={composer} />
      ) : (
        <div className="thread-banner">You can read conversations; replying needs the reviewer role.</div>
      )}
    </div>
  );
}

function ThreadActions({ conversation, onAct }: { conversation: Conversation; onAct: (change: ConversationUpdate, after?: "next") => void }) {
  const { session } = useSession();
  const [menu, setMenu] = useState<"snooze" | "labels" | null>(null);
  const [labelText, setLabelText] = useState("");
  const mine = session?.subject && conversation.assignee_email === session.subject.toLowerCase();
  return (
    <div className="row" style={{ gap: 6, flexWrap: "wrap", justifyContent: "flex-end", position: "relative" }}>
      {conversation.assignee_email && <Badge tone="blue">{mine ? "Assigned to you" : conversation.assignee_email}</Badge>}
      <Button size="sm" variant="ghost" onClick={() => onAct(mine ? { unassign: true } : { assignee_email: session?.subject ?? "" })} title={mine ? "Unassign" : "Assign to me"}>
        {mine ? "Unassign" : "Assign to me"}
      </Button>
      <Button size="sm" variant="ghost" onClick={() => onAct({ read: false })} title="Mark unread (u)">
        <Icon name="eye" /> Unread
      </Button>
      <Button size="sm" variant="ghost" onClick={() => setMenu(menu === "labels" ? null : "labels")} title="Labels">
        <Icon name="tag" /> Label
      </Button>
      {conversation.snoozed_until ? (
        <Button size="sm" variant="ghost" onClick={() => onAct({ unsnooze: true })}>Unsnooze</Button>
      ) : (
        <Button size="sm" variant="ghost" onClick={() => setMenu(menu === "snooze" ? null : "snooze")} title="Snooze (s)">
          <Icon name="clock" /> Snooze
        </Button>
      )}
      {conversation.done ? (
        <Button size="sm" onClick={() => onAct({ done: false })}>Reopen</Button>
      ) : (
        <Button size="sm" variant="primary" onClick={() => onAct({ done: true }, "next")} title="Done (e)">
          <Icon name="check" /> Done
        </Button>
      )}
      {menu === "snooze" && (
        <div className="menu-pop" role="menu">
          {snoozeOptions(new Date()).map((option) => (
            <button
              key={option.id}
              type="button"
              role="menuitem"
              onClick={() => {
                setMenu(null);
                onAct({ snoozed_until: option.until.toISOString() }, "next");
              }}
            >
              <span>{option.label}</span>
              <span className="small muted">{formatDateTime(option.until.toISOString())}</span>
            </button>
          ))}
        </div>
      )}
      {menu === "labels" && (
        <div className="menu-pop" role="dialog" aria-label="Labels">
          <div className="chips" style={{ padding: 8 }}>
            {conversation.labels.length === 0 && <span className="small muted">No labels yet</span>}
            {conversation.labels.map((label) => (
              <button key={label} type="button" className="chip-toggle on" onClick={() => onAct({ labels: conversation.labels.filter((item) => item !== label) })} title="Remove">
                {label} <Icon name="x" />
              </button>
            ))}
          </div>
          <form
            className="row"
            style={{ padding: 8, gap: 6 }}
            onSubmit={(event) => {
              event.preventDefault();
              if (!labelText.trim()) return;
              onAct({ labels: [...conversation.labels, labelText.trim()] });
              setLabelText("");
            }}
          >
            <input className="input" value={labelText} maxLength={40} placeholder="New label, e.g. Reefer" onChange={(event) => setLabelText(event.target.value)} autoFocus />
            <Button size="sm" type="submit">Add</Button>
          </form>
        </div>
      )}
    </div>
  );
}

function MessageBubble({ message, contactName }: { message: InboxMessage; contactName: string }) {
  const [expanded, setExpanded] = useState(false);
  const inbound = message.direction === "inbound";
  const body = message.body ?? "";
  const { text, quoted } = withoutQuotedText(body);
  const classification = (message.processing_result as { classification?: { bucket?: string; summary?: string } } | null)?.classification;
  return (
    <article className={inbound ? "bubble inbound" : "bubble outbound"}>
      <div className="row between small" style={{ gap: 8 }}>
        <strong>{inbound ? contactName || message.from_email : message.from_email}</strong>
        <span className="muted" title={formatDateTime(message.occurred_at)}>{formatRelative(message.occurred_at)}</span>
      </div>
      <div className="small muted">
        to {message.to_email} · {humanize(message.kind)}
        {classification?.bucket && <> · <BucketBadge bucket={classification.bucket} /></>}
      </div>
      {message.body === null ? (
        <p className="muted small">The text was removed after the retention period.</p>
      ) : (
        <div className="bubble-body">{expanded ? body : text || body}</div>
      )}
      {!inbound && <Engagement message={message} />}
      {quoted && message.body !== null && (
        <button type="button" className="link-button small" onClick={() => setExpanded((value) => !value)}>
          {expanded ? "Hide quoted text" : "Show quoted text"}
        </button>
      )}
    </article>
  );
}

function Composer({
  conversation,
  messages,
  composer,
}: {
  conversation: Conversation;
  messages: InboxMessage[];
  composer: React.RefObject<HTMLTextAreaElement | null>;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const draftKey = `lanereach.inbox.draft.${conversation.thread_id}`;
  const [body, setBody] = useState(() => {
    try {
      return window.sessionStorage.getItem(draftKey) ?? "";
    } catch {
      return "";
    }
  });
  const idempotencyKey = useRef(crypto.randomUUID());
  useEffect(() => {
    try {
      if (body) window.sessionStorage.setItem(draftKey, body);
      else window.sessionStorage.removeItem(draftKey);
    } catch {
      // Drafts are a convenience; private windows may refuse storage.
    }
  }, [body, draftKey]);

  const lastInbound = [...messages].reverse().find((message) => message.direction === "inbound");
  const suggest = useMutation({
    mutationFn: () => {
      if (!conversation.office_id || !lastInbound) throw new Error("There is no reply from the contact to answer yet.");
      return unwrap(
        api.POST("/api/v1/ai/replies/draft", {
          body: {
            subject: lastInbound.subject,
            body: lastInbound.body ?? "",
            office_id: conversation.office_id,
            contact_id: conversation.contact_id,
            sender_id: conversation.agent_id,
            bucket: (conversation.last_bucket ?? "interested") as Schemas["ReplyBucket"],
          },
        }),
      );
    },
    onSuccess: (result) => {
      setBody(result.draft.body);
      composer.current?.focus();
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  const send = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/inbox/conversation/reply", {
          params: { query: { thread_id: conversation.thread_id }, header: { "Idempotency-Key": idempotencyKey.current } },
          body: { body },
        }),
      ),
    onSuccess: () => {
      toast(`Sent from ${conversation.agent_name ?? "the office"} in the thread.`);
      setBody("");
      idempotencyKey.current = crypto.randomUUID();
      void queryClient.invalidateQueries({ queryKey: keys.inbox });
      void queryClient.invalidateQueries({ queryKey: keys.outbox });
    },
    onError: (error) => toast(describeError(error), "error"),
  });
  const canSend = body.trim().length > 0 && !send.isPending;
  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSend) send.mutate();
      }}
    >
      <textarea
        ref={composer}
        className="textarea"
        placeholder={`Reply to ${conversation.contact_name || conversation.contact_email}…`}
        value={body}
        onChange={(event) => setBody(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && canSend) {
            event.preventDefault();
            send.mutate();
          }
        }}
        aria-label="Reply"
      />
      <div className="row between" style={{ gap: 8, flexWrap: "wrap" }}>
        <span className="small muted">
          {conversation.service_line && <LinePill line={conversation.service_line} />} Sent as {conversation.agent_name ?? "an agent of the office"}; the
          signature is added. <span className="kbd">Ctrl</span> <span className="kbd">Enter</span> sends.
        </span>
        <span className="row" style={{ gap: 6 }}>
          <Button type="button" size="sm" variant="ghost" busy={suggest.isPending} disabled={!lastInbound} onClick={() => suggest.mutate()}>
            <Icon name="sparkles" /> Suggest with AI
          </Button>
          <Button type="submit" size="sm" variant="primary" busy={send.isPending} disabled={!canSend}>
            <Icon name="reply" /> Send reply
          </Button>
        </span>
      </div>
    </form>
  );
}

function Engagement({ message }: { message: InboxMessage }) {
  if (!message.tracking_id) return null;
  const opened = Boolean(message.first_opened_at);
  const clicked = Boolean(message.first_clicked_at);
  return (
    <div className="engagement">
      <span className={opened ? "on" : undefined} title={opened ? `First opened ${formatDateTime(message.first_opened_at)}` : "Not opened yet"}>
        <Icon name="eye" /> {opened ? `Opened${(message.open_count ?? 0) > 1 ? ` ${message.open_count}×` : ""}` : "Not opened"}
      </span>
      {clicked && (
        <span className="on" title={`First clicked ${formatDateTime(message.first_clicked_at)}`}>
          <Icon name="external" /> Clicked{(message.click_count ?? 0) > 1 ? ` ${message.click_count}×` : ""}
        </span>
      )}
    </div>
  );
}
