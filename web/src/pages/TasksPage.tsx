/** Tasks: what needs doing today, what is overdue, what comes next. */
import { useMemo, useState } from "react";
import { useTasks } from "../api/queries";
import { useSession } from "../auth/session";
import { Icon } from "../components/icons";
import { Page } from "../components/Layout";
import { SkeletonRows } from "../components/motion";
import { Empty, ErrorAlert, PageHeader, Segmented, Tabs } from "../components/ui";
import { QuickTask, TaskRow } from "../crm/DealDrawer";

type View = "today" | "overdue" | "upcoming" | "done";

export function TasksPage() {
  const { can } = useSession();
  const writer = can("crm:write");
  const [view, setView] = useState<View>("today");
  const [who, setWho] = useState<"mine" | "everyone">("mine");
  const open = useTasks({ mine: who === "mine", done: false });
  const done = useTasks({ mine: who === "mine", done: true });
  const groups = useMemo(() => {
    const now = new Date();
    const startOfDay = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const endOfDay = startOfDay + 86_400_000;
    const tasks = open.data ?? [];
    return {
      overdue: tasks.filter((task) => task.due_at && new Date(task.due_at).getTime() < startOfDay),
      today: tasks.filter((task) => task.due_at && new Date(task.due_at).getTime() >= startOfDay && new Date(task.due_at).getTime() < endOfDay),
      upcoming: tasks.filter((task) => !task.due_at || new Date(task.due_at).getTime() >= endOfDay),
    };
  }, [open.data]);
  const list = view === "done" ? (done.data ?? []) : groups[view];
  const loading = view === "done" ? done.isLoading : open.isLoading;
  return (
    <Page>
      <PageHeader
        eyebrow={<><Icon name="clock" /> CRM</>}
        title="Tasks"
        description="Follow-ups for you and the team. Workflows create tasks too (for example: call back when a quote is sent)."
        actions={
          <Segmented<"mine" | "everyone">
            label="Whose tasks"
            value={who}
            onChange={setWho}
            options={[
              { id: "mine", label: "Mine" },
              { id: "everyone", label: "Everyone" },
            ]}
          />
        }
      />
      {writer && (
        <div className="card" style={{ padding: 12, marginBottom: 16 }}>
          <QuickTask />
        </div>
      )}
      <Tabs
        value={view}
        onChange={setView}
        tabs={[
          { id: "today", label: `Today (${groups.today.length})` },
          { id: "overdue", label: <span style={{ color: groups.overdue.length ? "var(--danger)" : undefined }}>Overdue ({groups.overdue.length})</span> },
          { id: "upcoming", label: `Upcoming (${groups.upcoming.length})` },
          { id: "done", label: "Done" },
        ]}
      />
      <ErrorAlert error={open.error ?? done.error} />
      {loading && <SkeletonRows rows={4} />}
      {!loading && list.length === 0 && (
        <Empty art="chart" title={view === "done" ? "Nothing done yet" : view === "overdue" ? "Nothing overdue" : "All clear"}>
          {view === "today" ? "No tasks due today." : null}
        </Empty>
      )}
      <div className="card task-list">
        {list.map((task) => (
          <TaskRow key={task.id} task={task} canWrite={writer} />
        ))}
      </div>
    </Page>
  );
}
