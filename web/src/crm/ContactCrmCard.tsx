/** The CRM side of a contact: their deals, tasks and notes. */
import { useState } from "react";
import { Link } from "react-router-dom";
import { useDeals, usePipelines, useTasks } from "../api/queries";
import { useSession } from "../auth/session";
import { Card, StatusBadge, Tabs } from "../components/ui";
import { formatMoney } from "./board";
import { DealDrawer, NotesPanel, QuickTask, TaskRow } from "./DealDrawer";

export function ContactCrmCard({ contactId }: { contactId: string }) {
  const [tab, setTab] = useState<"deals" | "tasks" | "notes">("deals");
  const [openId, setOpenId] = useState<string | null>(null);
  const { can } = useSession();
  const writer = can("crm:write");
  const deals = useDeals({ contact_id: contactId });
  const tasks = useTasks({ contact_id: contactId, done: false });
  const pipelines = usePipelines();
  const open = deals.data?.find((deal) => deal.id === openId);
  const stageName = (stageId: string) =>
    pipelines.data?.flatMap((pipeline) => pipeline.stages).find((stage) => stage.id === stageId)?.name ?? "";
  return (
    <Card title="CRM" actions={<Link to="/pipeline">Pipeline</Link>}>
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "deals", label: `Deals (${deals.data?.length ?? 0})` },
          { id: "tasks", label: `Tasks (${tasks.data?.length ?? 0})` },
          { id: "notes", label: "Notes" },
        ]}
      />
      {tab === "deals" && (
        <div className="stack tight">
          {deals.data?.length === 0 && <div className="small muted">No deals yet. A rate request opens one automatically.</div>}
          {deals.data?.map((deal) => (
            <button key={deal.id} type="button" className="deal-line" onClick={() => setOpenId(deal.id)}>
              <span className="truncate"><strong>{deal.title}</strong></span>
              <span className="small muted">{stageName(deal.stage_id)}</span>
              <StatusBadge value={deal.status} />
              <strong className="small">{formatMoney(deal.value_amount, deal.currency)}</strong>
            </button>
          ))}
        </div>
      )}
      {tab === "tasks" && (
        <div className="stack">
          {writer && <QuickTask contactId={contactId} />}
          {tasks.data?.length === 0 && <div className="small muted">No open tasks.</div>}
          {tasks.data?.map((task) => <TaskRow key={task.id} task={task} canWrite={writer} />)}
        </div>
      )}
      {tab === "notes" && <NotesPanel contactId={contactId} canWrite={writer} />}
      {open && (
        <DealDrawer
          deal={open}
          pipeline={pipelines.data?.find((pipeline) => pipeline.id === open.pipeline_id)}
          onClose={() => setOpenId(null)}
        />
      )}
    </Card>
  );
}
