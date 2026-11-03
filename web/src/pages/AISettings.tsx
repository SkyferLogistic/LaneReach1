/**
 * Settings → AI usage: this month's AI spend against the company's budget, which areas use AI
 * or rules, and the cost by task. Each kind of reply picks AI or a built-in message in Reply policy.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { useSession } from "../auth/session";
import { Icon } from "../components/icons";
import { Badge, Button, Card, ErrorAlert, Loading, Segmented, useErrorToast, useToast } from "../components/ui";
import { budgetTone, formatUsd, parseBudget, taskLabel } from "../lib/aiUsage";

type Controls = Schemas["AIControlsResponse"];
export const AI_SETTINGS_KEY = ["ai-settings"] as const;

const AREAS: {
  key: "lead_reading" | "follow_ups" | "digest";
  title: string;
  text: string;
  options: { id: string; label: string }[];
}[] = [
  {
    key: "lead_reading",
    title: "Lead scores",
    text: "AI reads buying intent in each new deal's reply. Rules score from the shipment details, reply speed and history only.",
    options: [
      { id: "ai", label: "AI reading" },
      { id: "rules", label: "Rules only" },
    ],
  },
  {
    key: "follow_ups",
    title: "Quiet-deal follow-ups",
    text: "The AI writes each follow-up from the conversation, or your built-in follow-up message is used (template “follow_up_quiet”).",
    options: [
      { id: "ai", label: "AI writes" },
      { id: "built_in", label: "Built-in message" },
    ],
  },
  {
    key: "digest",
    title: "Weekly digest",
    text: "The AI writes the week's summary from your numbers, or the numbers are shown with a plain summary.",
    options: [
      { id: "ai", label: "AI summary" },
      { id: "rules", label: "Numbers only" },
    ],
  },
];

export function AISettings({ onOpenPolicies }: { onOpenPolicies: () => void }) {
  const query = useQuery({ queryKey: AI_SETTINGS_KEY, queryFn: () => unwrap(api.GET("/api/v1/settings/ai")) });
  const { can } = useSession();
  const admin = can("admin:manage");
  const queryClient = useQueryClient();
  const toast = useToast();
  const onError = useErrorToast();
  const save = useMutation({
    mutationFn: (controls: Controls) => unwrap(api.PUT("/api/v1/settings/ai", { body: controls })),
    onSuccess: (data) => {
      queryClient.setQueryData(AI_SETTINGS_KEY, data);
      toast("AI settings saved.");
    },
    onError,
  });
  const [budget, setBudget] = useState("");
  const data = query.data;
  useEffect(() => {
    if (data) setBudget(data.controls.monthly_budget_usd === null ? "" : String(data.controls.monthly_budget_usd));
  }, [data]);

  if (query.isLoading) return <Loading />;
  if (!data) return <ErrorAlert error={query.error} />;
  const controls = data.controls;
  const parsed = parseBudget(budget);
  const percent = data.budget_used_percent;
  const tone = budgetTone(percent);
  const update = (patch: Partial<Controls>) => save.mutate({ ...controls, ...patch });
  const month = new Date(data.month_start).toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });

  return (
    <div className="grid ai-settings">
      <Card title={`AI this month · ${month}`}>
        <div className="ai-meter-head">
          <div>
            <div className="ai-spent">{formatUsd(data.spent_usd)}</div>
            <div className="small muted">
              {controls.monthly_budget_usd === null ? "No monthly budget set" : `of ${formatUsd(controls.monthly_budget_usd)} budget`}
            </div>
          </div>
          {data.budget_reached ? (
            <Badge tone="red">AI paused: budget used up</Badge>
          ) : (
            <Badge tone="green">AI on</Badge>
          )}
        </div>
        {percent !== null && percent !== undefined && (
          <div className={`ai-meter ${tone}`} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(percent, 100)} aria-label="Budget used">
            <span style={{ width: `${Math.min(percent, 100)}%` }} />
          </div>
        )}
        {data.budget_reached && (
          <p className="small" style={{ marginBottom: 0 }}>
            No model is called until next month or a higher budget. Replies still get their built-in message or go to a person; scores and digests use rules.
          </p>
        )}
        <div className="ai-models">
          <ModelLine label="Writing tasks" value={data.main_model} />
          <ModelLine label="Reading tasks" value={data.light_model ?? "same as writing tasks"} />
          {!data.light_model && data.provider !== "mock" && (
            <p className="small muted" style={{ margin: 0 }}>
              Set LLM_MODEL_LIGHT (or AZURE_OPENAI_DEPLOYMENT_LIGHT) to run sorting, shipment details, intent, summaries and lane matching on a cheaper model.
            </p>
          )}
        </div>
        {!data.prices_configured && (
          <p className="small muted" style={{ marginBottom: 0 }}>
            <Icon name="alert" /> Costs show as $0.00 until the model prices are set (LLM_PRICE_* settings); a budget only fills up with prices set.
          </p>
        )}
      </Card>

      <Card title="Budget and where AI is used">
        <div className="ai-budget-row">
          <label className="field grow">
            <span>Monthly AI budget (USD)</span>
            <input
              className="input"
              inputMode="decimal"
              placeholder="No cap"
              value={budget}
              disabled={!admin}
              onChange={(event) => setBudget(event.target.value)}
            />
            {parsed === "invalid" && <span className="field-error">Enter an amount, or leave it empty for no cap.</span>}
          </label>
          {admin && (
            <Button
              variant="primary"
              busy={save.isPending}
              disabled={parsed === "invalid" || parsed === controls.monthly_budget_usd}
              onClick={() => parsed !== "invalid" && update({ monthly_budget_usd: parsed })}
            >
              Save budget
            </Button>
          )}
        </div>
        <div className="ai-areas">
          {AREAS.map((area) => (
            <div key={area.key} className="ai-area">
              <div className="grow">
                <strong>{area.title}</strong>
                <p className="small muted">{area.text}</p>
              </div>
              {admin ? (
                <Segmented
                  label={area.title}
                  options={area.options}
                  value={controls[area.key]}
                  onChange={(value) => update({ [area.key]: value } as Partial<Controls>)}
                />
              ) : (
                <Badge tone="blue">{area.options.find((option) => option.id === controls[area.key])?.label}</Badge>
              )}
            </div>
          ))}
          <div className="ai-area">
            <div className="grow">
              <strong>Answers to replies</strong>
              <p className="small muted">Each kind of reply (rate request, meeting, referral…) is answered by an AI draft or your built-in message. Drafts always wait for review unless you enabled auto-send.</p>
            </div>
            <Button variant="ghost" onClick={onOpenPolicies}>
              <Icon name="review" /> Choose per reply
            </Button>
          </div>
        </div>
      </Card>

      <Card title="Usage by task" flush>
        {data.by_task.length === 0 ? (
          <p className="card-body small muted" style={{ margin: 0 }}>No AI runs this month yet.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Task</th>
                <th>Model</th>
                <th className="num">Runs</th>
                <th className="num">Tokens in / out</th>
                <th className="num">Cost</th>
              </tr>
            </thead>
            <tbody>
              {data.by_task.map((row) => (
                <tr key={row.task}>
                  <td>
                    <strong>{taskLabel(row.task)}</strong>
                    {row.failed > 0 && <div className="small muted">{row.failed} failed</div>}
                  </td>
                  <td>
                    <Badge tone={row.model_tier === "light" ? "cyan" : "violet"}>{row.model_tier === "light" ? "Light" : "Main"}</Badge>
                  </td>
                  <td className="num">{row.runs.toLocaleString()}</td>
                  <td className="num small">
                    {row.tokens_in.toLocaleString()} / {row.tokens_out.toLocaleString()}
                  </td>
                  <td className="num">{formatUsd(row.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function ModelLine({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="ai-model-line">
      <span className="small muted">{label}</span>
      <code>{value}</code>
    </div>
  );
}
