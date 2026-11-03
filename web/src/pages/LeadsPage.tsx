/** Quote-ready leads for the pricing team. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { LoadMore, usePaged } from "../api/paging";
import { keys } from "../api/queries";
import { fireConfetti } from "../components/delight";
import { Icon } from "../components/icons";
import { Page } from "../components/Layout";
import { Button, Card, Drawer, Empty, ErrorAlert, KeyValues, Loading, PageHeader, StatusBadge, useErrorToast } from "../components/ui";
import { formatDateTime, formatRelative, humanize } from "../lib/format";
import { useConfirm, useDelete } from "../components/confirm";

const STATUSES = ["new", "quoting", "quoted", "won", "lost"];

interface Place {
  city?: string | null;
  country?: string | null;
  port_code?: string | null;
}

function place(value: unknown): string {
  const data = (value ?? {}) as Place;
  return [data.city, data.port_code, data.country].filter(Boolean).join(" · ") || "?";
}

export function LeadsPage() {
  const [status, setStatus] = useState("");
  const onExportError = useErrorToast();
  const exporting = useMutation({
    mutationFn: async () => {
      const { data, response } = await api.GET("/api/v1/leads/export", {
        params: { query: { status: (status || undefined) as never } },
        parseAs: "blob",
      });
      if (!response.ok || !data) throw new Error(`Export failed (${response.status}).`);
      const link = document.createElement("a");
      link.href = URL.createObjectURL(data as Blob);
      link.download = `leads${status ? `-${status}` : ""}.csv`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    },
    onError: onExportError,
  });
  const [open, setOpen] = useState<Schemas["LeadResponse"] | null>(null);
  const page = usePaged([...keys.leads, status], (cursor) =>
    unwrap(api.GET("/api/v1/leads", { params: { query: { status: (status || undefined) as never, cursor, limit: 50 } } })),
  );
  return (
    <Page>
      <PageHeader
        title="Leads"
        description="Created from rate and service questions, with the shipment details the AI extracted."
        actions={
          <>
            <select className="select" style={{ width: 160 }} value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="">All statuses</option>
              {STATUSES.map((value) => (
                <option key={value} value={value}>
                  {humanize(value)}
                </option>
              ))}
            </select>
            <Button busy={exporting.isPending} onClick={() => exporting.mutate()}>
              <Icon name="file" /> Export CSV
            </Button>
          </>
        }
      />
      <Card flush>
        {page.isLoading && <Loading />}
        <ErrorAlert error={page.error} />
        {!page.isLoading && page.items.length === 0 && <Empty art="parcel" title="No quote requests yet">Rate questions in replies create leads automatically.</Empty>}
        {page.items.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Request</th>
                <th>Route</th>
                <th>Load</th>
                <th>Priority</th>
                <th>Status</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((lead) => {
                const shipment = (lead.shipment ?? {}) as Record<string, unknown>;
                return (
                  <tr key={lead.id} className="clickable" onClick={() => setOpen(lead)}>
                    <td className="truncate" style={{ maxWidth: 320 }}>{lead.summary ?? "—"}</td>
                    <td className="small">{shipment.origin || shipment.destination ? `${place(shipment.origin)} → ${place(shipment.destination)}` : "—"}</td>
                    <td className="small">
                      {[shipment.container_quantity && `${String(shipment.container_quantity)}×`, shipment.container_type, shipment.incoterm, shipment.frequency !== "unknown" && shipment.frequency]
                        .filter(Boolean)
                        .join(" ") || "—"}
                    </td>
                    <td><StatusBadge value={lead.priority} /></td>
                    <td><StatusBadge value={lead.status} /></td>
                    <td className="small muted">{formatRelative(lead.created_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        <LoadMore {...page} />
      </Card>
      {open && <LeadDrawer lead={open} onClose={() => setOpen(null)} />}
    </Page>
  );
}

function LeadDrawer({ lead, onClose }: { lead: Schemas["LeadResponse"]; onClose: () => void }) {
  const queryClient = useQueryClient();
  const onError = useErrorToast();
  const [current, setCurrent] = useState(lead);
  const update = useMutation({
    mutationFn: (body: Schemas["LeadUpdateRequest"]) =>
      unwrap(api.PATCH("/api/v1/leads/{lead_id}", { params: { path: { lead_id: lead.id } }, body })),
    onSuccess: (updated, body) => {
      // A deal won deserves a moment.
      if (body.status === "won" && current.status !== "won") fireConfetti();
      setCurrent(updated);
      void queryClient.invalidateQueries({ queryKey: keys.leads });
    },
    onError,
  });
  const shipment = (current.shipment ?? {}) as Record<string, unknown>;
  const [confirmDialog, ask] = useConfirm();
  const removeLead = useDelete({
    run: () => unwrap(api.DELETE("/api/v1/leads/{lead_id}", { params: { path: { lead_id: lead.id } } })),
    success: "Quote request deleted.",
    invalidate: [keys.leads, keys.deals],
    onDone: onClose,
  });
  return (
    <Drawer
      title="Quote request"
      onClose={onClose}
      footer={
        <>
          <Button
            variant="danger"
            busy={removeLead.isPending}
            style={{ marginRight: "auto" }}
            onClick={() =>
              void ask({
                title: "Delete this quote request?",
                message: "The deal it opened is deleted too. The contact and their emails stay.",
              }).then((ok) => ok && removeLead.mutate())
            }
          >
            <Icon name="trash" /> Delete
          </Button>
          {confirmDialog}
          <Button onClick={onClose}>Close</Button>
        </>
      }
    >
      <div className="stack">
        <div className="row">
          <StatusBadge value={current.status} /> <StatusBadge value={current.priority} />
          <span className="small muted">source {current.source}</span>
        </div>
        {current.summary && <div>{current.summary}</div>}
        <div className="row">
          {STATUSES.map((value) => (
            <Button key={value} size="sm" variant={current.status === value ? "primary" : undefined} onClick={() => update.mutate({ status: value as Schemas["LeadStatus"] })}>
              {humanize(value)}
            </Button>
          ))}
          <Button size="sm" onClick={() => update.mutate({ priority: current.priority === "high" ? "normal" : "high" })}>
            {current.priority === "high" ? "Mark normal" : "Mark high"}
          </Button>
        </div>
        <KeyValues
          items={[
            ["Origin", place(shipment.origin)],
            ["Destination", place(shipment.destination)],
            ["Mode", shipment.mode && shipment.mode !== "UNKNOWN" ? String(shipment.mode) : null],
            ["Containers", shipment.container_quantity ? `${String(shipment.container_quantity)} × ${String(shipment.container_type ?? "?")}` : null],
            ["Commodity", shipment.commodity ? String(shipment.commodity) : null],
            ["Weight", shipment.weight_kg ? `${String(shipment.weight_kg)} kg` : null],
            ["Volume", shipment.volume_cbm ? `${String(shipment.volume_cbm)} cbm` : null],
            ["Incoterm", shipment.incoterm ? String(shipment.incoterm) : null],
            ["Frequency", shipment.frequency && shipment.frequency !== "unknown" ? humanize(String(shipment.frequency)) : null],
            ["Ready date", shipment.cargo_ready_date ? String(shipment.cargo_ready_date) : null],
            ["Hazardous", shipment.is_hazardous === true ? "yes" : shipment.is_hazardous === false ? "no" : null],
            ["Missing", Array.isArray(shipment.missing_fields) && shipment.missing_fields.length ? (shipment.missing_fields as string[]).join(", ") : null],
            ["Contact", <Link to={`/contacts/${current.contact_id}`}>Open contact</Link>],
            ["Created", formatDateTime(current.created_at)],
          ]}
        />
      </div>
    </Drawer>
  );
}
