/** Events that start workflows (outbox pattern), plus publishing a custom event. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { LoadMore, usePaged } from "../api/paging";
import { keys } from "../api/queries";
import { useSession } from "../auth/session";
import { ContactPicker } from "../components/ContactPicker";
import { Page } from "../components/Layout";
import { Button, Card, Empty, ErrorAlert, Json, Loading, Modal, PageHeader, StatusBadge, useToast } from "../components/ui";
import { formatDateTime } from "../lib/format";

const TYPES = ["contact.created", "contact.tag_added", "reply.received", "reply.classified", "manual.enroll", "schedule.tick"];

export function EventsPage() {
  const [type, setType] = useState("");
  const [publishing, setPublishing] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const { can } = useSession();
  const page = usePaged([...keys.events, type], (cursor) =>
    unwrap(api.GET("/api/v1/automation/events", { params: { query: { type: (type || undefined) as never, cursor, limit: 50 } } })),
  );
  return (
    <Page>
      <PageHeader
        title="Events"
        description="Every trigger starts from an event. Each is processed once and matched to published workflows."
        actions={
          <>
            <select className="select" style={{ width: 200 }} value={type} onChange={(event) => setType(event.target.value)}>
              <option value="">All types</option>
              {TYPES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
            {can("events:write") && (
              <Button variant="primary" onClick={() => setPublishing(true)}>
                Publish event
              </Button>
            )}
          </>
        }
      />
      <Card flush>
        {page.isLoading && <Loading />}
        <ErrorAlert error={page.error} />
        {!page.isLoading && page.items.length === 0 && <Empty title="No events" />}
        {page.items.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Type</th>
                <th>Contact</th>
                <th>Status</th>
                <th>Enrolled</th>
                <th>Occurred</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {page.items.map((event) => (
                <Fragment key={event.id}>
                  <tr>
                    <td className="mono small">{event.type}</td>
                    <td>{event.contact_id ? <Link to={`/contacts/${event.contact_id}`}>contact</Link> : "—"}</td>
                    <td>
                      <StatusBadge value={event.status} />
                      {event.error && <div className="small" style={{ color: "var(--danger)" }}>{event.error}</div>}
                    </td>
                    <td>{event.enrollments_created}</td>
                    <td className="small">{formatDateTime(event.occurred_at)}</td>
                    <td>
                      <Button size="sm" variant="ghost" onClick={() => setOpen(open === event.id ? null : event.id)}>
                        {open === event.id ? "Hide" : "Payload"}
                      </Button>
                    </td>
                  </tr>
                  {open === event.id && (
                    <tr>
                      <td colSpan={6}>
                        <Json value={event.payload} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
        <LoadMore {...page} />
      </Card>
      {publishing && <PublishEventModal onClose={() => setPublishing(false)} />}
    </Page>
  );
}

function PublishEventModal({ onClose }: { onClose: () => void }) {
  const [type, setType] = useState("manual.enroll");
  const [contactId, setContactId] = useState<string | null>(null);
  const [payload, setPayload] = useState("{}");
  const [key, setKey] = useState("");
  const toast = useToast();
  const queryClient = useQueryClient();
  const publish = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/automation/events", {
          body: {
            type: type as Schemas["EventType"],
            contact_id: contactId as string,
            payload: JSON.parse(payload || "{}") as Record<string, unknown>,
            idempotency_key: key || undefined,
          },
        }),
      ),
    onSuccess: (event) => {
      toast(`Event published (${event.status}).`);
      void queryClient.invalidateQueries({ queryKey: keys.events });
      onClose();
    },
  });
  return (
    <Modal
      title="Publish an event"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={publish.isPending} disabled={!contactId} onClick={() => publish.mutate()}>
            Publish
          </Button>
        </>
      }
    >
      <div className="stack">
        <label className="field">
          <span>Type</span>
          <select className="select" value={type} onChange={(event) => setType(event.target.value)}>
            {TYPES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <ContactPicker value={contactId} onChange={setContactId} />
        <label className="field">
          <span>Payload (JSON)</span>
          <textarea className="textarea code" value={payload} onChange={(event) => setPayload(event.target.value)} placeholder='{"tag": "intro"}' />
          <span className="field-help">For contact.tag_added use {'{"tag": "intro"}'}.</span>
        </label>
        <label className="field">
          <span>Idempotency key (optional)</span>
          <input className="input" value={key} onChange={(event) => setKey(event.target.value)} />
        </label>
        <ErrorAlert error={publish.error} />
      </div>
    </Modal>
  );
}
