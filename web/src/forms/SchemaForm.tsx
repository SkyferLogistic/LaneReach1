/**
 * Renders a node's config form from its JSON Schema (Pydantic output).
 *
 * Supported: strings (enums, formats, long text), numbers, booleans, optional values
 * (`anyOf [..., null]`), `$ref`/`$defs`, arrays of enums (chips), arrays of objects
 * (repeating groups) and arrays of strings. Fields that point at tenant data get pickers:
 * `office_id`, `sender_id`, `template_key`. Workflow conditions get the condition builder.
 */
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, unwrap } from "../api/client";
import { keys, useConditionFields, useOffices, useSenders, useTemplates } from "../api/queries";
import { humanize } from "../lib/format";
import { ConditionBuilder } from "./ConditionBuilder";

export type JsonSchema = {
  type?: string | string[];
  title?: string;
  description?: string;
  enum?: unknown[];
  format?: string;
  default?: unknown;
  examples?: unknown[];
  pattern?: string;
  minimum?: number;
  maximum?: number;
  minItems?: number;
  maxItems?: number;
  maxLength?: number;
  items?: JsonSchema;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  anyOf?: JsonSchema[];
  oneOf?: JsonSchema[];
  $ref?: string;
  $defs?: Record<string, JsonSchema>;
};

type Value = unknown;

interface FieldProps {
  name: string;
  schema: JsonSchema;
  root: JsonSchema;
  value: Value;
  required: boolean;
  onChange: (value: Value) => void;
  context: Record<string, unknown>;
}

export function resolveSchema(schema: JsonSchema, root: JsonSchema): JsonSchema {
  if (schema.$ref) {
    const name = schema.$ref.split("/").pop() ?? "";
    const target = root.$defs?.[name];
    return target ? { ...target, ...withoutRef(schema) } : schema;
  }
  return schema;
}

function withoutRef(schema: JsonSchema): JsonSchema {
  const { $ref: _ref, ...rest } = schema;
  return rest;
}

/** `anyOf [X, null]` -> X (nullable). */
function unwrapNullable(schema: JsonSchema, root: JsonSchema): { inner: JsonSchema; nullable: boolean } {
  const variants = schema.anyOf;
  if (variants) {
    const nonNull = variants.filter((variant) => variant.type !== "null");
    if (nonNull.length === 1 && nonNull[0]) {
      return {
        inner: { ...resolveSchema(nonNull[0], root), description: schema.description, title: schema.title },
        nullable: nonNull.length !== variants.length,
      };
    }
  }
  return { inner: resolveSchema(schema, root), nullable: false };
}

export function SchemaForm({
  schema,
  value,
  onChange,
  context = {},
}: {
  schema: JsonSchema;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  context?: Record<string, unknown>;
}) {
  const properties = schema.properties ?? {};
  const required = new Set(schema.required ?? []);
  if (Object.keys(properties).length === 0) {
    return <p className="muted small">This node has no settings.</p>;
  }
  return (
    <div className="stack">
      {Object.entries(properties).map(([name, property]) => (
        <FieldEditor
          key={name}
          name={name}
          schema={property}
          root={schema}
          value={value[name]}
          required={required.has(name)}
          context={{ ...context, ...value }}
          onChange={(next) => {
            const updated = { ...value };
            if (next === undefined || next === "" || next === null) delete updated[name];
            else updated[name] = next;
            onChange(updated);
          }}
        />
      ))}
    </div>
  );
}

function Labelled({
  name,
  schema,
  required,
  children,
}: {
  name: string;
  schema: JsonSchema;
  required: boolean;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span>
        {schema.title ?? humanize(name)}
        {required && <span style={{ color: "var(--danger)" }}> *</span>}
      </span>
      {children}
      {schema.description && <span className="field-help">{schema.description}</span>}
    </label>
  );
}

function FieldEditor(props: FieldProps) {
  const { name, root, value, onChange, required } = props;
  const { inner: schema } = unwrapNullable(props.schema, root);

  if (name === "condition" || name === "filter") {
    return (
      <div className="field">
        <span>
          {schema.title ?? humanize(name)}
          {required && <span style={{ color: "var(--danger)" }}> *</span>}
        </span>
        <ConditionBuilder value={value} onChange={onChange} optional={!required} />
        {props.schema.description && <span className="field-help">{props.schema.description}</span>}
      </div>
    );
  }
  if (name === "office_id") return <OfficePicker {...props} schema={schema} />;
  if (name === "sender_id") return <SenderPicker {...props} schema={schema} />;
  if (name === "template_key") return <TemplatePicker {...props} schema={schema} />;
  if (name === "workflow_id") return <WorkflowPicker {...props} schema={schema} />;
  if (name === "webhook_id") return <IntegrationPicker {...props} schema={schema} kind="webhook" />;
  if (name === "connection_id") return <IntegrationPicker {...props} schema={schema} kind="slack" />;
  if (name === "field") return <ConditionFieldPicker {...props} schema={schema} />;

  if (schema.enum) {
    return (
      <Labelled name={name} schema={schema} required={required}>
        <select
          className="select"
          value={value === undefined || value === null ? "" : String(value)}
          onChange={(event) => onChange(event.target.value || undefined)}
        >
          <option value="">{required ? "Choose…" : "—"}</option>
          {schema.enum.map((option) => (
            <option key={String(option)} value={String(option)}>
              {humanize(String(option))}
            </option>
          ))}
        </select>
      </Labelled>
    );
  }

  const type = Array.isArray(schema.type) ? schema.type[0] : schema.type;
  if (type === "boolean") {
    return (
      <div className="stack tight">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={Boolean(value ?? schema.default)}
            onChange={(event) => onChange(event.target.checked)}
          />
          {schema.title ?? humanize(name)}
        </label>
        {schema.description && <span className="field-help">{schema.description}</span>}
      </div>
    );
  }
  if (type === "integer" || type === "number") {
    return (
      <Labelled name={name} schema={schema} required={required}>
        <input
          className="input"
          type="number"
          min={schema.minimum}
          max={schema.maximum}
          step={type === "integer" ? 1 : "any"}
          value={value === undefined || value === null ? "" : String(value)}
          placeholder={schema.default !== undefined && schema.default !== null ? String(schema.default) : ""}
          onChange={(event) =>
            onChange(event.target.value === "" ? undefined : Number(event.target.value))
          }
        />
      </Labelled>
    );
  }
  if (type === "array") {
    const items = resolveSchema(schema.items ?? {}, root);
    if (items.enum) return <EnumChips {...props} schema={schema} options={items.enum.map(String)} />;
    if (items.type === "object" || items.properties) {
      return <ObjectList {...props} schema={schema} itemSchema={items} />;
    }
    return (
      <Labelled name={name} schema={schema} required={required}>
        <StringListInput value={value} onChange={onChange} />
      </Labelled>
    );
  }
  if (type === "object" || schema.properties) {
    return (
      <fieldset>
        <legend>{schema.title ?? humanize(name)}</legend>
        <SchemaForm
          schema={{ ...schema, $defs: root.$defs }}
          value={(value as Record<string, unknown>) ?? {}}
          onChange={onChange}
        />
      </fieldset>
    );
  }

  const long = (schema.maxLength ?? 0) > 300 || name === "message" || name === "body";
  const inputType =
    schema.format === "date-time" ? "datetime-local" : schema.format === "date" ? "date" : "text";
  return (
    <Labelled name={name} schema={schema} required={required}>
      {long ? (
        <textarea
          className="textarea"
          value={value === undefined || value === null ? "" : String(value)}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <input
          className="input"
          type={inputType}
          value={toInputValue(value, inputType)}
          placeholder={schema.examples?.[0] !== undefined ? String(schema.examples[0]) : ""}
          onChange={(event) => onChange(fromInputValue(event.target.value, inputType))}
        />
      )}
    </Labelled>
  );
}

function toInputValue(value: Value, inputType: string): string {
  if (value === undefined || value === null) return "";
  if (inputType === "datetime-local") {
    const date = new Date(String(value));
    if (Number.isNaN(date.getTime())) return "";
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
    return local.toISOString().slice(0, 16);
  }
  return String(value);
}

function fromInputValue(raw: string, inputType: string): Value {
  if (!raw) return undefined;
  return inputType === "datetime-local" ? new Date(raw).toISOString() : raw;
}

function EnumChips({ name, schema, value, onChange, required, options }: FieldProps & { options: string[] }) {
  const selected = new Set(Array.isArray(value) ? (value as string[]) : []);
  return (
    <Labelled name={name} schema={schema} required={required}>
      <div className="chips">
        {options.map((option) => (
          <button
            key={option}
            type="button"
            className={selected.has(option) ? "chip-toggle on" : "chip-toggle"}
            onClick={() => {
              const next = new Set(selected);
              if (next.has(option)) next.delete(option);
              else next.add(option);
              const ordered = options.filter((candidate) => next.has(candidate));
              onChange(ordered.length ? ordered : undefined);
            }}
          >
            {humanize(option)}
          </button>
        ))}
      </div>
    </Labelled>
  );
}

function ObjectList({
  name,
  schema,
  root,
  value,
  onChange,
  itemSchema,
}: FieldProps & { itemSchema: JsonSchema }) {
  const items = Array.isArray(value) ? (value as Record<string, unknown>[]) : [];
  return (
    <fieldset>
      <legend>{schema.title ?? humanize(name)}</legend>
      <div className="stack">
        {items.map((item, index) => (
          <div key={index} className="card" style={{ padding: 10 }}>
            <SchemaForm
              schema={{ ...itemSchema, $defs: root.$defs }}
              value={item}
              onChange={(next) => onChange(items.map((current, i) => (i === index ? next : current)))}
            />
            <div className="row" style={{ marginTop: 8 }}>
              <button
                type="button"
                className="btn sm danger"
                onClick={() => onChange(items.filter((_, i) => i !== index))}
              >
                Remove
              </button>
            </div>
          </div>
        ))}
        {(schema.maxItems === undefined || items.length < schema.maxItems) && (
          <button type="button" className="btn sm" onClick={() => onChange([...items, {}])}>
            + Add
          </button>
        )}
      </div>
    </fieldset>
  );
}

function OfficePicker({ name, schema, value, onChange, required }: FieldProps) {
  const offices = useOffices();
  return (
    <Labelled name={name} schema={{ ...schema, title: "Office" }} required={required}>
      <select
        className="select"
        value={typeof value === "string" ? value : ""}
        onChange={(event) => onChange(event.target.value || undefined)}
      >
        <option value="">Choose an office…</option>
        {(offices.data ?? []).map((office) => (
          <option key={office.id} value={office.id}>
            {office.name} ({office.country})
          </option>
        ))}
      </select>
    </Labelled>
  );
}

function SenderPicker({ name, schema, value, onChange, required, context }: FieldProps) {
  const senders = useSenders();
  const officeId = typeof context.office_id === "string" ? context.office_id : null;
  const options = (senders.data ?? []).filter((sender) => !officeId || sender.office_id === officeId);
  return (
    <Labelled name={name} schema={{ ...schema, title: "Sender mailbox" }} required={required}>
      <select
        className="select"
        value={typeof value === "string" ? value : ""}
        onChange={(event) => onChange(event.target.value || undefined)}
      >
        <option value="">Rotate across the office's senders</option>
        {options.map((sender) => (
          <option key={sender.id} value={sender.id}>
            {sender.display_name} &lt;{sender.email}&gt;
          </option>
        ))}
      </select>
    </Labelled>
  );
}

function TemplatePicker({ name, schema, value, onChange, required }: FieldProps) {
  const templates = useTemplates();
  const byKey = new Map<string, string[]>();
  for (const template of templates.data ?? []) {
    byKey.set(template.key, [...(byKey.get(template.key) ?? []), template.language]);
  }
  const current = typeof value === "string" ? value : "";
  return (
    <Labelled name={name} schema={{ ...schema, title: "Template" }} required={required}>
      <select
        className="select"
        value={current}
        onChange={(event) => onChange(event.target.value || undefined)}
      >
        <option value="">{required ? "Choose a template…" : "—"}</option>
        {[...byKey.entries()].map(([key, languages]) => (
          <option key={key} value={key}>
            {key} ({languages.join(", ")})
          </option>
        ))}
        {current && !byKey.has(current) && <option value={current}>{current} (missing)</option>}
      </select>
    </Labelled>
  );
}

/**
 * Comma-separated values. The text is kept while typing (so "AE, " keeps its comma) and turned
 * into a list on every change.
 */
function StringListInput({ value, onChange }: { value: unknown; onChange: (value: Value) => void }) {
  const list = Array.isArray(value) ? (value as string[]) : [];
  const [text, setText] = useState(list.join(", "));
  useEffect(() => {
    const parsed = text.split(",").map((part) => part.trim()).filter(Boolean);
    if (parsed.join("\u0000") !== list.join("\u0000")) setText(list.join(", "));
    // Only react to outside changes of the list, not to typing.
  }, [list.join("\u0000")]);
  return (
    <input
      className="input"
      value={text}
      placeholder="comma, separated, values"
      onChange={(event) => {
        setText(event.target.value);
        onChange(
          event.target.value
            .split(",")
            .map((part) => part.trim())
            .filter(Boolean),
        );
      }}
    />
  );
}

function WorkflowPicker({ name, schema, value, onChange, required }: FieldProps) {
  const workflows = useQuery({
    queryKey: [...keys.workflows, "picker"],
    queryFn: () => unwrap(api.GET("/api/v1/automation/workflows", { params: { query: { limit: 100 } } })),
  });
  const current = typeof value === "string" ? value : "";
  const items = workflows.data?.items ?? [];
  return (
    <Labelled name={name} schema={{ ...schema, title: "Workflow" }} required={required}>
      <select className="select" value={current} onChange={(event) => onChange(event.target.value || undefined)}>
        <option value="">{required ? "Choose a workflow…" : "—"}</option>
        {items.map((workflow) => (
          <option key={workflow.id} value={workflow.id}>
            {workflow.name} ({workflow.status})
          </option>
        ))}
        {current && !items.some((workflow) => workflow.id === current) && <option value={current}>{current} (missing)</option>}
      </select>
    </Labelled>
  );
}

/** Webhooks and Slack channels from Integrations (admins see names; others paste an id). */
function IntegrationPicker({ name, schema, value, onChange, required, kind }: FieldProps & { kind: "webhook" | "slack" }) {
  const overview = useQuery({
    queryKey: ["integrations"],
    queryFn: () => unwrap(api.GET("/api/v1/integrations")),
    retry: false,
  });
  const current = typeof value === "string" ? value : "";
  const options =
    kind === "webhook"
      ? (overview.data?.webhooks ?? []).map((item) => ({ id: item.id, label: `${item.name} · ${item.url_hint}`, off: !item.is_active }))
      : (overview.data?.connections ?? [])
          .filter((item) => item.kind === "slack")
          .map((item) => ({ id: item.id, label: item.name, off: !item.is_active }));
  const title = kind === "webhook" ? "Webhook" : "Slack channel";
  if (overview.isError) {
    return (
      <Labelled name={name} schema={{ ...schema, title: `${title} id` }} required={required}>
        <input className="input" value={current} onChange={(event) => onChange(event.target.value || undefined)} placeholder="Ask an admin for the id in Integrations" />
      </Labelled>
    );
  }
  return (
    <Labelled name={name} schema={{ ...schema, title }} required={required}>
      <select className="select" value={current} onChange={(event) => onChange(event.target.value || undefined)}>
        <option value="">{options.length ? `Choose a ${title.toLowerCase()}…` : `No ${title.toLowerCase()} yet: add one in Integrations`}</option>
        {options.map((option) => (
          <option key={option.id} value={option.id}>
            {option.label}
            {option.off ? " (off)" : ""}
          </option>
        ))}
        {current && !options.some((option) => option.id === current) && <option value={current}>{current} (missing)</option>}
      </select>
    </Labelled>
  );
}

function ConditionFieldPicker({ name, schema, value, onChange, required }: FieldProps) {
  const fields = useConditionFields();
  const current = typeof value === "string" ? value : "";
  const list = fields.data?.fields ?? [];
  const groups = new Map<string, typeof list>();
  for (const field of list) {
    const scope = field.field.split(".")[0] ?? "other";
    groups.set(scope, [...(groups.get(scope) ?? []), field]);
  }
  return (
    <Labelled name={name} schema={{ ...schema, title: "Field" }} required={required}>
      <select className="select" value={current} onChange={(event) => onChange(event.target.value || undefined)}>
        <option value="">{required ? "Choose a field…" : "—"}</option>
        {[...groups.entries()].map(([scope, items]) => (
          <optgroup key={scope} label={humanize(scope)}>
            {items.map((field) => (
              <option key={field.field} value={field.field}>
                {field.description} ({field.field})
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </Labelled>
  );
}

