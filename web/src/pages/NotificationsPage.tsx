/** Notifications sent to office owners, pricing and Teams channels. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { LoadMore, usePaged } from "../api/paging";
import { keys } from "../api/queries";
import { Page } from "../components/Layout";
import { Badge, Button, Card, Empty, ErrorAlert, Loading, PageHeader, useErrorToast } from "../components/ui";
import { formatRelative } from "../lib/format";

export function NotificationsPage() {
  const [unread, setUnread] = useState(false);
  const queryClient = useQueryClient();
  const onError = useErrorToast();
  const page = usePaged([...keys.notifications, unread], (cursor) =>
    unwrap(api.GET("/api/v1/notifications", { params: { query: { unread: unread || undefined, cursor, limit: 50 } } })),
  );
  const markRead = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.POST("/api/v1/notifications/{notification_id}/read", { params: { path: { notification_id: id } } })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: keys.notifications }),
    onError,
  });
  return (
    <Page>
      <PageHeader
        title="Notifications"
        description="Handovers and alerts: new leads for pricing, replies for office owners, complaints."
        actions={
          <label className="checkbox">
            <input type="checkbox" checked={unread} onChange={(event) => setUnread(event.target.checked)} /> Unread only
          </label>
        }
      />
      <Card flush>
        {page.isLoading && <Loading />}
        <ErrorAlert error={page.error} />
        {!page.isLoading && page.items.length === 0 && <Empty title="No notifications" />}
        <table className="table">
          <tbody>
            {page.items.map((notification) => (
              <tr key={notification.id}>
                <td style={{ width: 120 }}>
                  <Badge tone={notification.channel === "pricing" ? "violet" : "blue"}>{notification.channel}</Badge>
                </td>
                <td>
                  <strong>{notification.subject}</strong>
                  <div className="small muted">to {notification.recipient} · via {notification.delivered_via}</div>
                  <pre className="small" style={{ marginTop: 4 }}>{notification.body}</pre>
                  {notification.contact_id && <Link to={`/contacts/${notification.contact_id}`} className="small">Contact</Link>}
                </td>
                <td className="small muted nowrap">{formatRelative(notification.created_at)}</td>
                <td>
                  {notification.read_at ? (
                    <span className="small muted">read</span>
                  ) : (
                    <Button size="sm" onClick={() => markRead.mutate(notification.id)}>
                      Mark read
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <LoadMore {...page} />
      </Card>
    </Page>
  );
}
