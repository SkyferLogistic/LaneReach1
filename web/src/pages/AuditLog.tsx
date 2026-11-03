/** Settings → Audit log: who changed what, newest first (admins). */
import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { useSession } from "../auth/session";
import { Icon } from "../components/icons";
import { SkeletonRows } from "../components/motion";
import { Badge, Button, Empty, ErrorAlert, Segmented } from "../components/ui";
import { formatDateTime, formatRelative, humanize } from "../lib/format";

type AuditEvent = Schemas["AuditEventResponse"];

const AREAS = [
  { id: "", label: "Everything" },
  { id: "update_sending", label: "Sending" },
  { id: "publish_workflow", label: "Publishing" },
  { id: "set_workflow", label: "Schedules" },
  { id: "create_api_key", label: "API keys" },
] as const;

type Area = (typeof AREAS)[number]["id"];

function describeChange(value: unknown): string {
  if (value === true) return "on";
  if (value === false) return "off";
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

export function AuditLog() {
  const { can } = useSession();
  const [area, setArea] = useState<Area>("");
  const log = useInfiniteQuery({
    queryKey: ["audit", area],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/audit-events", {
          params: { query: { action: area || undefined, cursor: pageParam ?? undefined, limit: 50 } },
        }),
      ),
    getNextPageParam: (page) => page.next_cursor,
    enabled: can("admin:manage"),
  });
  if (!can("admin:manage")) return <Empty title="Admins only">The audit log is visible to admins.</Empty>;
  const items = log.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <div className="stack">
      <div className="row between" style={{ flexWrap: "wrap", gap: 8 }}>
        <p className="small muted" style={{ margin: 0 }}>
          Every change made in the console or through the API: sending controls, campaigns, settings, agents, offices,
          keys and suppressions. Kept for two years.
        </p>
        <Segmented<Area> label="Area" value={area} onChange={setArea} options={AREAS.map((item) => ({ id: item.id, label: item.label }))} />
      </div>
      <ErrorAlert error={log.error} />
      {log.isLoading && <SkeletonRows rows={5} />}
      {!log.isLoading && items.length === 0 && <Empty title="Nothing recorded yet" />}
      {items.length > 0 && (
        <div className="card audit-list">
          {items.map((item) => (
            <AuditRow key={item.id} item={item} />
          ))}
        </div>
      )}
      {log.hasNextPage && (
        <div>
          <Button size="sm" busy={log.isFetchingNextPage} onClick={() => void log.fetchNextPage()}>
            Older entries
          </Button>
        </div>
      )}
    </div>
  );
}

function AuditRow({ item }: { item: AuditEvent }) {
  const changes = Object.entries(item.changes);
  return (
    <div className="audit-row">
      <span className="audit-icon" aria-hidden>
        <Icon name="key" />
      </span>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div className="row between" style={{ gap: 8 }}>
          <strong className="truncate">{item.summary}</strong>
          <span className="small muted" title={formatDateTime(item.occurred_at)} style={{ flex: "none" }}>
            {formatRelative(item.occurred_at)}
          </span>
        </div>
        <div className="small muted truncate">
          {item.actor} · {humanize(item.entity_type)}
          {item.entity_id ? ` ${item.entity_id.slice(0, 8)}` : ""}
        </div>
        {changes.length > 0 && (
          <div className="chips" style={{ marginTop: 6 }}>
            {changes.map(([key, value]) => (
              <Badge key={key}>
                {humanize(key)}: {describeChange(value)}
              </Badge>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
