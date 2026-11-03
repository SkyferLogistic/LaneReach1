/** Do-not-email list: unsubscribes, bounces, complaints and manual entries. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { LoadMore, usePaged } from "../api/paging";
import { useSession } from "../auth/session";
import { Page } from "../components/Layout";
import { Badge, Button, Card, Empty, ErrorAlert, Loading, Modal, PageHeader, useErrorToast, useToast } from "../components/ui";
import { formatDateTime, humanize } from "../lib/format";

const REASONS = ["do_not_contact", "unsubscribed", "bounced_hard", "bounced_soft", "blocked", "complaint", "manual"];
const KEY = ["suppressions"];

export function SuppressionsPage() {
  const [adding, setAdding] = useState(false);
  const { can } = useSession();
  const queryClient = useQueryClient();
  const toast = useToast();
  const onError = useErrorToast();
  const page = usePaged(KEY, (cursor) => unwrap(api.GET("/api/v1/suppressions", { params: { query: { cursor, limit: 50 } } })));
  const lift = useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/api/v1/suppressions/{suppression_id}", { params: { path: { suppression_id: id } } })),
    onSuccess: () => {
      toast("Suppression lifted: this address can receive email again.");
      void queryClient.invalidateQueries({ queryKey: KEY });
    },
    onError,
  });
  return (
    <Page>
      <PageHeader
        title="Suppressions"
        description="Addresses that must not receive email. The suppression guardrail blocks every email to them, whatever the workflow says."
        actions={
          can("workflows:write") && (
            <Button variant="primary" onClick={() => setAdding(true)}>
              Suppress address
            </Button>
          )
        }
      />
      <Card flush>
        {page.isLoading && <Loading />}
        <ErrorAlert error={page.error} />
        {!page.isLoading && page.items.length === 0 && <Empty title="No suppressions" />}
        {page.items.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Reason</th>
                <th>Source</th>
                <th>Until</th>
                <th>Added</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {page.items.map((row) => (
                <tr key={row.id}>
                  <td>{row.email}{row.note && <div className="small muted">{row.note}</div>}</td>
                  <td><Badge tone={["complaint", "blocked", "bounced_hard"].includes(row.reason) ? "red" : "amber"}>{humanize(row.reason)}</Badge></td>
                  <td className="small">{row.source}</td>
                  <td className="small">{row.expires_at ? formatDateTime(row.expires_at) : "permanent"}</td>
                  <td className="small muted">{formatDateTime(row.created_at)}</td>
                  <td>
                    {can("admin:manage") && (
                      <Button size="sm" variant="danger" onClick={() => window.confirm(`Allow email to ${row.email} again?`) && lift.mutate(row.id)}>
                        Lift
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <LoadMore {...page} />
      </Card>
      {adding && <SuppressModal onClose={() => setAdding(false)} />}
    </Page>
  );
}

function SuppressModal({ onClose }: { onClose: () => void }) {
  const [email, setEmail] = useState("");
  const [reason, setReason] = useState("do_not_contact");
  const [note, setNote] = useState("");
  const [until, setUntil] = useState("");
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/suppressions", {
          body: {
            email,
            reason: reason as Schemas["SuppressionReason"],
            note: note || null,
            expires_at: until ? new Date(until).toISOString() : null,
          },
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: KEY });
      onClose();
    },
  });
  return (
    <Modal
      title="Suppress an address"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={save.isPending} disabled={!email} onClick={() => save.mutate()}>
            Suppress
          </Button>
        </>
      }
    >
      <div className="stack">
        <label className="field"><span>Email</span><input className="input" value={email} onChange={(event) => setEmail(event.target.value)} /></label>
        <label className="field">
          <span>Reason</span>
          <select className="select" value={reason} onChange={(event) => setReason(event.target.value)}>
            {REASONS.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
          </select>
        </label>
        <label className="field"><span>Note</span><input className="input" value={note} onChange={(event) => setNote(event.target.value)} /></label>
        <label className="field">
          <span>Until (optional)</span>
          <input className="input" type="datetime-local" value={until} onChange={(event) => setUntil(event.target.value)} />
          <span className="field-help">Empty = permanent. A permanent Do Not Contact also marks the contact.</span>
        </label>
        <ErrorAlert error={save.error} />
      </div>
    </Modal>
  );
}
