/** Enrollments: every contact going through a workflow, and each step with its guardrails. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { LoadMore, usePaged } from "../api/paging";
import { keys } from "../api/queries";
import { useSession } from "../auth/session";
import { Page } from "../components/Layout";
import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorAlert,
  Json,
  KeyValues,
  Loading,
  PageHeader,
  StatusBadge,
  useErrorToast,
  useToast,
} from "../components/ui";
import { formatDateTime, formatRelative, humanize, shortId } from "../lib/format";
import { GuardrailList } from "../workflows/TestRunDrawer";
import { useConfirm, useDelete } from "../components/confirm";
import { Icon } from "../components/icons";

const STATUSES = ["", "active", "waiting", "paused", "completed", "stopped", "failed", "cancelled"];

export function EnrollmentsPage() {
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "";
  const workflowId = params.get("workflow_id") ?? "";
  const navigate = useNavigate();
  const workflows = useQuery({
    queryKey: [...keys.workflows, "all-for-filter"],
    queryFn: () => unwrap(api.GET("/api/v1/automation/workflows", { params: { query: { limit: 100 } } })),
  });
  const names = new Map((workflows.data?.items ?? []).map((workflow) => [workflow.id, workflow.name]));
  const page = usePaged([...keys.enrollments, status, workflowId], (cursor) =>
    unwrap(
      api.GET("/api/v1/automation/enrollments", {
        params: {
          query: {
            status: (status || undefined) as never,
            workflow_id: workflowId || undefined,
            cursor,
            limit: 50,
          },
        },
      }),
    ),
  );
  const setFilter = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next);
  };
  return (
    <Page>
      <PageHeader
        title="Enrollments"
        description="A contact's journey through one workflow version. Waiting enrollments resume on their own (dispatcher every 30 s)."
        actions={
          <>
            <select className="select" style={{ width: 220 }} value={workflowId} onChange={(event) => setFilter("workflow_id", event.target.value)}>
              <option value="">All workflows</option>
              {(workflows.data?.items ?? []).map((workflow) => (
                <option key={workflow.id} value={workflow.id}>
                  {workflow.name}
                </option>
              ))}
            </select>
            <select className="select" style={{ width: 150 }} value={status} onChange={(event) => setFilter("status", event.target.value)}>
              {STATUSES.map((value) => (
                <option key={value} value={value}>
                  {value ? humanize(value) : "All statuses"}
                </option>
              ))}
            </select>
          </>
        }
      />
      <Card flush>
        {page.isLoading && <Loading />}
        <ErrorAlert error={page.error} />
        {!page.isLoading && page.items.length === 0 && <Empty art="truck" title="No enrollments" />}
        {page.items.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Enrollment</th>
                  <th>Workflow</th>
                  <th>Status</th>
                  <th>At node</th>
                  <th>Next run</th>
                  <th>Steps</th>
                  <th>Why stopped</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((enrollment) => (
                  <tr key={enrollment.id} className="clickable" onClick={() => navigate(`/enrollments/${enrollment.id}`)}>
                    <td className="mono small">{shortId(enrollment.id)}</td>
                    <td>
                      {names.get(enrollment.workflow_id) ?? shortId(enrollment.workflow_id)}{" "}
                      <span className="muted small">v{enrollment.workflow_version}</span>
                    </td>
                    <td><StatusBadge value={enrollment.status} /></td>
                    <td className="mono small">{enrollment.current_node_id ?? "—"}</td>
                    <td className="small">{enrollment.next_run_at ? formatRelative(enrollment.next_run_at) : "—"}</td>
                    <td>{enrollment.step_count}</td>
                    <td className="small muted truncate" style={{ maxWidth: 260 }}>{enrollment.stop_reason ?? enrollment.last_error ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <LoadMore {...page} />
      </Card>
    </Page>
  );
}

export function EnrollmentDetailPage() {
  const { enrollmentId = "" } = useParams();
  const queryClient = useQueryClient();
  const toast = useToast();
  const onError = useErrorToast();
  const { can } = useSession();
  const [showVariables, setShowVariables] = useState(false);
  const goTo = useNavigate();
  const [confirmDialog, ask] = useConfirm();
  const removeEnrollment = useDelete({
    run: () => unwrap(api.DELETE("/api/v1/automation/enrollments/{enrollment_id}", { params: { path: { enrollment_id: enrollmentId } } })),
    success: "Enrollment deleted.",
    invalidate: [keys.enrollments],
    onDone: () => goTo("/enrollments"),
  });
  const enrollment = useQuery({
    queryKey: [...keys.enrollments, enrollmentId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/automation/enrollments/{enrollment_id}", { params: { path: { enrollment_id: enrollmentId } } })),
    refetchInterval: 10_000,
  });
  const steps = useQuery({
    queryKey: [...keys.enrollments, enrollmentId, "steps"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/automation/enrollments/{enrollment_id}/steps", { params: { path: { enrollment_id: enrollmentId } } }),
      ),
    refetchInterval: 10_000,
  });
  const contact = useQuery({
    queryKey: [...keys.contacts, enrollment.data?.contact_id],
    enabled: Boolean(enrollment.data),
    queryFn: () =>
      unwrap(api.GET("/api/v1/contacts/{contact_id}", { params: { path: { contact_id: enrollment.data!.contact_id } } })),
  });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: keys.enrollments });
  const action = useMutation({
    mutationFn: async (kind: "pause" | "resume" | "cancel" | "advance" | "wake") => {
      const options = { params: { path: { enrollment_id: enrollmentId } } };
      if (kind === "pause") return unwrap(api.POST("/api/v1/automation/enrollments/{enrollment_id}/pause", options));
      if (kind === "resume") return unwrap(api.POST("/api/v1/automation/enrollments/{enrollment_id}/resume", options));
      if (kind === "cancel") return unwrap(api.POST("/api/v1/automation/enrollments/{enrollment_id}/cancel", options));
      if (kind === "wake") return unwrap(api.POST("/api/v1/automation/enrollments/{enrollment_id}/wake", options));
      return unwrap(api.POST("/api/v1/automation/enrollments/{enrollment_id}/advance", options));
    },
    onSuccess: (_, kind) => {
      toast(
        {
          pause: "Paused.",
          resume: "Resumed.",
          cancel: "Cancelled.",
          advance: "Ran the due steps now.",
          wake: "Woken: the waiting step is due now.",
        }[kind],
      );
      refresh();
    },
    onError,
  });

  if (enrollment.isLoading) return <Loading />;
  if (!enrollment.data) return <Page><ErrorAlert error={enrollment.error} /></Page>;
  const data = enrollment.data;
  const open = ["active", "waiting", "paused"].includes(data.status);

  return (
    <Page>
      <PageHeader
        title={
          <span className="row">
            Enrollment <span className="mono">{shortId(data.id)}</span> <StatusBadge value={data.status} />
          </span>
        }
        description={
          <>
            <Link to={`/workflows/${data.workflow_id}`}>Workflow</Link> v{data.workflow_version} ·{" "}
            {contact.data ? <Link to={`/contacts/${data.contact_id}`}>{contact.data.display_name}</Link> : "contact"} · enrolled by{" "}
            {data.enrolled_by}
          </>
        }
        actions={
          can("enrollments:write") && open ? (
            <>
              {data.status === "paused" ? (
                <Button onClick={() => action.mutate("resume")}>Resume</Button>
              ) : (
                <Button onClick={() => action.mutate("pause")}>Pause</Button>
              )}
              {data.status === "waiting" && (
                <Button onClick={() => action.mutate("wake")} title="Dev/staging: make the wait due now">
                  Skip the wait
                </Button>
              )}
              <Button variant="primary" onClick={() => action.mutate("advance")} title="Dev/staging: run due steps now">
                Run now
              </Button>
              <Button variant="danger" onClick={() => window.confirm("Cancel this enrollment?") && action.mutate("cancel")}>
                Cancel
              </Button>
            </>
          ) : can("admin:manage") ? (
            <>
              <Button
                variant="ghost"
                busy={removeEnrollment.isPending}
                onClick={() =>
                  void ask({
                    title: "Delete this enrollment?",
                    message: "Finished enrollments that sent no email can be deleted with their steps. Ones that sent email stay for the campaign's results.",
                  }).then((ok) => ok && removeEnrollment.mutate())
                }
              >
                <Icon name="trash" /> Delete
              </Button>
              {confirmDialog}
            </>
          ) : undefined
        }
      />
      <div className="grid two" style={{ alignItems: "start" }}>
        <Card title="Steps">
          {steps.isLoading && <Loading />}
          {steps.data?.length === 0 && <Empty title="No steps yet" />}
          <div className="timeline">
            {steps.data?.map((step) => (
              <div key={step.id} className="timeline-item">
                <span
                  className={`timeline-dot ${
                    ["continue", "stop"].includes(step.result) ? "green" : step.result === "wait" ? "blue" : step.result === "defer" ? "amber" : "red"
                  }`}
                />
                <div className="stack tight">
                  <div className="row">
                    <strong className="mono">{step.node_id}</strong>
                    <span className="small muted">{humanize(step.node_type)}</span>
                    <StatusBadge value={step.result} />
                    {step.handle_taken && step.handle_taken !== "next" && <Badge tone="violet">→ {step.handle_taken}</Badge>}
                  </div>
                  <div className="small muted">{formatDateTime(step.started_at)}</div>
                  {step.detail && <div className="small">{step.detail}</div>}
                  {step.wait_until && <div className="small">waits until {formatDateTime(step.wait_until)}</div>}
                  {step.error && <div className="small" style={{ color: "var(--danger)" }}>{step.error}</div>}
                  {step.guardrail_decisions.length > 0 && <GuardrailList decisions={step.guardrail_decisions} />}
                  {step.output && Object.keys(step.output).length > 0 && (
                    <details>
                      <summary className="small muted">Output</summary>
                      <Json value={step.output} />
                    </details>
                  )}
                </div>
              </div>
            ))}
          </div>
        </Card>
        <div className="stack">
          <Card title="State">
            <KeyValues
              items={[
                ["Current node", data.current_node_id ? <span className="mono">{data.current_node_id}</span> : null],
                ["Next run", data.next_run_at ? `${formatDateTime(data.next_run_at)} (${formatRelative(data.next_run_at)})` : null],
                ["Resume via", data.resume_handle],
                ["Attempts", String(data.attempts)],
                ["Last error", data.last_error],
                ["Stop reason", data.stop_reason],
                ["Completed", data.completed_at ? formatDateTime(data.completed_at) : null],
              ]}
            />
          </Card>
          <Card
            title="Variables"
            actions={
              <Button size="sm" variant="ghost" onClick={() => setShowVariables((value) => !value)}>
                {showVariables ? "Hide" : "Show"}
              </Button>
            }
          >
            {showVariables ? <Json value={data.variables} /> : <span className="small muted">reply, lead, draft and custom variables</span>}
          </Card>
        </div>
      </div>
    </Page>
  );
}
