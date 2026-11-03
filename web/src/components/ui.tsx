/** Small presentational building blocks shared by every page. */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { describeError } from "../api/client";
import type { ServiceLine } from "../lib/freight";
import { LINE_ICONS, SERVICE_LINE_LABELS, asServiceLine } from "../lib/freight";
import { Icon } from "./icons";
import { FreightScene } from "./illustrations";
import type { SceneKind } from "./illustrations";
import { EmptyArt } from "./motion";

export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  eyebrow?: ReactNode;
}) {
  return (
    <div className="page-header">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

export function Card({
  title,
  actions,
  children,
  flush = false,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  flush?: boolean;
}) {
  return (
    <section className="card">
      {(title || actions) && (
        <div className="card-header">
          {typeof title === "string" ? <h2>{title}</h2> : title}
          {actions && <div className="row">{actions}</div>}
        </div>
      )}
      <div className={flush ? "card-body flush" : "card-body"}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="card stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="small muted">{hint}</div>}
    </div>
  );
}

type Tone = "green" | "amber" | "red" | "blue" | "cyan" | "violet" | "gray" | "air" | "ocean";

export function Badge({ tone = "gray", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

const STATUS_TONES: Record<string, Tone> = {
  active: "blue",
  waiting: "cyan",
  paused: "amber",
  completed: "green",
  stopped: "gray",
  failed: "red",
  cancelled: "gray",
  published: "green",
  draft: "amber",
  archived: "gray",
  pending: "amber",
  processed: "green",
  approved: "green",
  edited: "green",
  rejected: "red",
  new: "blue",
  quoting: "cyan",
  quoted: "violet",
  won: "green",
  lost: "gray",
  continue: "green",
  wait: "cyan",
  defer: "amber",
  block: "red",
  stop: "gray",
  fail: "red",
  allow: "green",
  high: "red",
  normal: "gray",
  ai_draft: "violet",
  error: "red",
  inactive: "gray",
  ok: "green",
  never: "gray",
  sharepoint: "cyan",
  manual: "gray",
};

export function StatusBadge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="muted">—</span>;
  return <Badge tone={STATUS_TONES[value] ?? "gray"}>{value.replaceAll("_", " ")}</Badge>;
}

const BUCKET_TONES: Record<string, Tone> = {
  interested: "green",
  meeting_request: "green",
  rate_query: "violet",
  service_query: "violet",
  partnership_query: "blue",
  referral: "blue",
  wrong_office: "amber",
  later: "cyan",
  not_interested: "gray",
  complaint: "red",
  existing_partner: "blue",
  unsubscribe: "red",
  out_of_office: "cyan",
  auto_reply: "gray",
  bounce_hard: "red",
  bounce_soft: "amber",
  blocked: "red",
  unclear: "amber",
};

export function BucketBadge({ bucket }: { bucket: string | null | undefined }) {
  if (!bucket) return <span className="muted">—</span>;
  return <Badge tone={BUCKET_TONES[bucket] ?? "gray"}>{bucket.replaceAll("_", " ")}</Badge>;
}

export function Button({
  busy = false,
  variant,
  size,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  busy?: boolean;
  variant?: "primary" | "danger" | "ghost" | "success";
  size?: "sm";
}) {
  const classes = ["btn", variant, size].filter(Boolean).join(" ");
  return (
    <button type="button" className={classes} disabled={busy || rest.disabled} {...rest}>
      {busy && <span className="spinner" aria-hidden />}
      {children}
    </button>
  );
}

export function Empty({ title, children, art }: { title: string; children?: ReactNode; art?: SceneKind }) {
  return (
    <div className="empty">
      {art ? <FreightScene kind={art} className="freight-scene empty-scene" /> : <EmptyArt />}
      <h3>{title}</h3>
      {children && <div>{children}</div>}
    </div>
  );
}

export function ErrorAlert({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="alert error">{describeError(error)}</div>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="row muted" style={{ padding: 16 }}>
      <span className="spinner" /> {label}
    </div>
  );
}

export function Drawer({
  title,
  onClose,
  children,
  footer,
  wide = false,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  useEscape(onClose);
  // Portalled to <body>: the page's own layers (sticky top bar, animations) never cover it.
  return createPortal(
    <div className="overlay" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <aside className={wide ? "drawer wide" : "drawer"} role="dialog" aria-modal="true">
        <div className="drawer-header">
          <h2>{title}</h2>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close">
            ✕
          </Button>
        </div>
        <div className="drawer-body">{children}</div>
        {footer && <div className="drawer-footer">{footer}</div>}
      </aside>
    </div>,
    document.body,
  );
}

export function Modal({
  title,
  onClose,
  children,
  footer,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  useEscape(onClose);
  return createPortal(
    <div
      className="overlay center"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <div className="modal" role="dialog" aria-modal="true">
        <div className="modal-header">
          <h2>{title}</h2>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close">
            ✕
          </Button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

// Open drawers and modals, in the order they opened: Escape closes only the top one (a quote
// builder opened from a deal closes, the deal stays).
const escapeStack: symbol[] = [];

function useEscape(onClose: () => void): void {
  const latest = useRef(onClose);
  useEffect(() => {
    latest.current = onClose;
  });
  useEffect(() => {
    const id = Symbol("overlay");
    escapeStack.push(id);
    const listener = (event: KeyboardEvent) => {
      if (event.key === "Escape" && escapeStack[escapeStack.length - 1] === id) latest.current();
    };
    window.addEventListener("keydown", listener);
    return () => {
      window.removeEventListener("keydown", listener);
      const index = escapeStack.lastIndexOf(id);
      if (index >= 0) escapeStack.splice(index, 1);
    };
  }, []);
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { id: T; label: ReactNode }[];
  value: T;
  onChange: (id: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={tab.id === value}
          className={tab.id === value ? "tab active" : "tab"}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export function Json({ value }: { value: unknown }) {
  return <pre className="email-preview mono">{JSON.stringify(value, null, 2)}</pre>;
}

export function KeyValues({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([key, value]) => (
        <div key={key} style={{ display: "contents" }}>
          <dt>{key}</dt>
          <dd>{value ?? <span className="muted">—</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

// ---------- Toasts ----------

interface Toast {
  id: number;
  text: string;
  kind: "success" | "error";
}

const ToastContext = createContext<(text: string, kind?: Toast["kind"]) => void>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: Toast["kind"] = "success") => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, text, kind }]);
    window.setTimeout(() => setToasts((current) => current.filter((t) => t.id !== id)), 5000);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast ${toast.kind}`}>
            {toast.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}

/** Toast the outcome of a mutation's error. */
export function useErrorToast() {
  const toast = useToast();
  return useCallback((error: unknown) => toast(describeError(error), "error"), [toast]);
}

// ---------- Freight-specific bits ----------

export function LinePill({ line }: { line: ServiceLine | string }) {
  const known = asServiceLine(line);
  return (
    <span className={`line-pill ${known}`}>
      {known !== "any" && <Icon name={LINE_ICONS[known]} />}
      {SERVICE_LINE_LABELS[known]}
    </span>
  );
}

export function Segmented<T extends string | number>({
  options,
  value,
  onChange,
  label,
}: {
  options: { id: T; label: ReactNode }[];
  value: T;
  onChange: (id: T) => void;
  label: string;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      {options.map((option) => (
        <button
          key={String(option.id)}
          type="button"
          role="radio"
          aria-checked={option.id === value}
          className={option.id === value ? "on" : ""}
          onClick={() => onChange(option.id)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <input
      type="checkbox"
      role="switch"
      className="switch"
      aria-label={label}
      title={label}
      checked={checked}
      disabled={disabled}
      onChange={(event) => onChange(event.target.checked)}
    />
  );
}
