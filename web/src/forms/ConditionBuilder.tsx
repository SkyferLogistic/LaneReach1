/**
 * Visual builder for the condition DSL: `{field, op, value}`, `{all: [...]}`, `{any: [...]}`,
 * `{not: ...}`. Fields and their allowed operators come from `/automation/condition-fields`,
 * so the builder only offers what the backend accepts.
 */
import { useConditionFields } from "../api/queries";
import type { Schemas } from "../api/client";

type Comparison = { field: string; op: string; value?: unknown };
type Expression =
  | Comparison
  | { all: Expression[] }
  | { any: Expression[] }
  | { not: Expression };

type Field = Schemas["ConditionFieldResponse"];

const OPERATOR_LABELS: Record<string, string> = {
  eq: "is",
  ne: "is not",
  in: "is one of",
  not_in: "is none of",
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
  contains: "contains",
  exists: "exists",
};

function isGroup(expression: Expression): expression is { all: Expression[] } | { any: Expression[] } {
  return "all" in expression || "any" in expression;
}

function emptyComparison(fields: Field[]): Comparison {
  const first = fields[0];
  return { field: first?.field ?? "contact.country", op: first?.operators[0] ?? "eq", value: "" };
}

export function ConditionBuilder({
  value,
  onChange,
  optional = false,
}: {
  value: unknown;
  onChange: (value: unknown) => void;
  optional?: boolean;
}) {
  const fields = useConditionFields();
  const list = fields.data?.fields ?? [];
  const variableOperators = fields.data?.variable_operators ?? ["eq", "ne", "exists"];
  const expression = value as Expression | undefined;

  if (!expression) {
    return (
      <div className="condition-group">
        <span className="muted small">{optional ? "No filter: every contact matches." : "No condition yet."}</span>
        <div className="row">
          <button type="button" className="btn sm" onClick={() => onChange({ all: [emptyComparison(list)] })}>
            + Add condition
          </button>
        </div>
      </div>
    );
  }
  const root: Expression = isGroup(expression) ? expression : { all: [expression] };
  return (
    <div className="stack tight">
      <GroupEditor
        expression={root}
        fields={list}
        variableOperators={variableOperators}
        onChange={(next) => onChange(next)}
        onRemove={optional ? () => onChange(undefined) : undefined}
      />
    </div>
  );
}

function GroupEditor({
  expression,
  fields,
  variableOperators,
  onChange,
  onRemove,
}: {
  expression: { all: Expression[] } | { any: Expression[] };
  fields: Field[];
  variableOperators: string[];
  onChange: (next: Expression) => void;
  onRemove?: () => void;
}) {
  const mode = "all" in expression ? "all" : "any";
  const items = "all" in expression ? expression.all : expression.any;
  const update = (nextItems: Expression[]) =>
    onChange(mode === "all" ? { all: nextItems } : { any: nextItems });

  return (
    <div className="condition-group">
      <div className="row">
        <select
          className="select"
          style={{ width: "auto" }}
          value={mode}
          onChange={(event) => onChange(event.target.value === "all" ? { all: items } : { any: items })}
        >
          <option value="all">All of these are true</option>
          <option value="any">Any of these is true</option>
        </select>
        <span className="spacer" />
        {onRemove && (
          <button type="button" className="btn sm ghost" onClick={onRemove}>
            Remove group
          </button>
        )}
      </div>
      {items.map((item, index) => {
        const replace = (next: Expression) => update(items.map((current, i) => (i === index ? next : current)));
        const remove = () => {
          const rest = items.filter((_, i) => i !== index);
          if (rest.length) update(rest);
          else onRemove?.();
        };
        if (isGroup(item)) {
          return (
            <GroupEditor
              key={index}
              expression={item}
              fields={fields}
              variableOperators={variableOperators}
              onChange={replace}
              onRemove={remove}
            />
          );
        }
        const negated = "not" in item;
        const comparison = (negated ? (item as { not: Expression }).not : item) as Comparison;
        return (
          <ComparisonRow
            key={index}
            comparison={comparison}
            negated={negated}
            fields={fields}
            variableOperators={variableOperators}
            onChange={(next, not) => replace(not ? { not: next } : next)}
            onRemove={items.length > 1 || onRemove ? remove : undefined}
          />
        );
      })}
      <div className="row">
        <button type="button" className="btn sm" onClick={() => update([...items, emptyComparison(fields)])}>
          + Condition
        </button>
        <button
          type="button"
          className="btn sm ghost"
          onClick={() => update([...items, { any: [emptyComparison(fields)] }])}
        >
          + Group
        </button>
      </div>
    </div>
  );
}

function ComparisonRow({
  comparison,
  negated,
  fields,
  variableOperators,
  onChange,
  onRemove,
}: {
  comparison: Comparison;
  negated: boolean;
  fields: Field[];
  variableOperators: string[];
  onChange: (next: Comparison, negated: boolean) => void;
  onRemove?: () => void;
}) {
  const isVariable = comparison.field.startsWith("enrollment.variables.");
  const definition = fields.find((field) => field.field === comparison.field);
  const operators = definition?.operators ?? (isVariable ? variableOperators : ["eq"]);

  const setField = (field: string) => {
    const next = fields.find((candidate) => candidate.field === field);
    const op = next && !next.operators.includes(comparison.op) ? (next.operators[0] ?? "eq") : comparison.op;
    onChange({ field, op, value: next?.type === "boolean" ? true : "" }, negated);
  };

  return (
    <div className="stack tight">
      <div className="condition-row">
        <select className="select" value={isVariable ? "__variable" : comparison.field} onChange={(event) =>
          event.target.value === "__variable" ? setField("enrollment.variables.name") : setField(event.target.value)
        }>
          {fields.map((field) => (
            <option key={field.field} value={field.field}>
              {field.field}
            </option>
          ))}
          <option value="__variable">enrollment variable…</option>
        </select>
        <select
          className="select"
          value={comparison.op}
          onChange={(event) => onChange({ ...comparison, op: event.target.value }, negated)}
        >
          {operators.map((op) => (
            <option key={op} value={op}>
              {OPERATOR_LABELS[op] ?? op}
            </option>
          ))}
        </select>
        <ValueInput comparison={comparison} definition={definition} onChange={(value) => onChange({ ...comparison, value }, negated)} />
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <button
            type="button"
            className={negated ? "btn sm primary" : "btn sm"}
            title="Negate (NOT)"
            onClick={() => onChange(comparison, !negated)}
          >
            NOT
          </button>
          {onRemove && (
            <button type="button" className="btn sm ghost" aria-label="Remove condition" onClick={onRemove}>
              ✕
            </button>
          )}
        </div>
      </div>
      {isVariable && (
        <input
          className="input"
          value={comparison.field.replace("enrollment.variables.", "")}
          placeholder="variable name"
          onChange={(event) =>
            onChange({ ...comparison, field: `enrollment.variables.${event.target.value}` }, negated)
          }
        />
      )}
      {definition?.description && <span className="field-help">{definition.description}</span>}
    </div>
  );
}

function ValueInput({
  comparison,
  definition,
  onChange,
}: {
  comparison: Comparison;
  definition: Field | undefined;
  onChange: (value: unknown) => void;
}) {
  const { op, value } = comparison;
  if (op === "exists") {
    return (
      <select className="select" value={String(value ?? true)} onChange={(event) => onChange(event.target.value === "true")}>
        <option value="true">yes</option>
        <option value="false">no</option>
      </select>
    );
  }
  const multi = op === "in" || op === "not_in";
  if (definition?.type === "enum" && definition.enum_values.length) {
    if (multi) {
      const selected = new Set(Array.isArray(value) ? (value as string[]) : []);
      return (
        <select
          className="select"
          multiple
          value={[...selected]}
          size={Math.min(5, definition.enum_values.length)}
          onChange={(event) => onChange(Array.from(event.target.selectedOptions).map((option) => option.value))}
        >
          {definition.enum_values.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      );
    }
    return (
      <select className="select" value={String(value ?? "")} onChange={(event) => onChange(event.target.value)}>
        <option value="">Choose…</option>
        {definition.enum_values.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  }
  if (definition?.type === "boolean") {
    return (
      <select className="select" value={String(value ?? true)} onChange={(event) => onChange(event.target.value === "true")}>
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    );
  }
  if (multi) {
    return (
      <input
        className="input"
        value={Array.isArray(value) ? (value as unknown[]).join(", ") : ""}
        placeholder="AE, SA, EG"
        onChange={(event) =>
          onChange(
            event.target.value
              .split(",")
              .map((part) => part.trim())
              .filter(Boolean)
              .map((part) => (definition?.type === "number" ? Number(part) : part)),
          )
        }
      />
    );
  }
  return (
    <input
      className="input"
      type={definition?.type === "number" ? "number" : definition?.type === "datetime" ? "datetime-local" : "text"}
      value={value === undefined || value === null ? "" : String(value)}
      onChange={(event) => {
        const raw = event.target.value;
        if (definition?.type === "number") onChange(raw === "" ? null : Number(raw));
        else if (definition?.type === "datetime") onChange(raw ? new Date(raw).toISOString() : null);
        else onChange(raw);
      }}
    />
  );
}
