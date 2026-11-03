/**
 * Sign-in. Production: "Sign in with Microsoft" (the company's identity provider, OIDC with
 * PKCE, configured by `GET /auth/config`). Development: pick a tenant and a role.
 */
import { useQuery } from "@tanstack/react-query";
import { Suspense, lazy, useEffect, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { api, describeError, unwrap } from "../api/client";
import type { Schemas } from "../api/client";
import { authorizeUrl, randomToken, readCallback, redeemCode, savePending, takePending } from "../auth/pkce";
import { useSession } from "../auth/session";
import { Icon } from "../components/icons";
import type { IconName } from "../components/icons";
import { AiFeed } from "../components/AiFeed";
import { BrandMark } from "../components/Layout";
import { ThemeSwitch } from "../components/ThemeSwitch";
import "./login.css";
import { useReducedMotion } from "../components/motion";
import { Badge, Button, ErrorAlert, Loading, useToast } from "../components/ui";
import { ROLE_NAMES, readLastSignIn, saveLastSignIn } from "../lib/lastSignIn";
import { usePublicTheme } from "../lib/theme";

const ROLES: { id: string; label: string; hint: string; icon: IconName; can: string; cannot: string }[] = [
  {
    id: "admin",
    label: "Admin",
    hint: "Everything, incl. kill switch and integrations",
    icon: "key",
    can: "Everything: settings, agents and mailboxes, integrations, deletes, the sending kill switch.",
    cannot: "Nothing is off limits.",
  },
  {
    id: "campaign_manager",
    label: "Campaign manager",
    hint: "Workflows, enrollments, events",
    icon: "workflow",
    can: "Build, publish, pause and delete workflows; enroll contacts; CRM.",
    cannot: "Settings, agents and integrations.",
  },
  {
    id: "office_owner",
    label: "Office owner",
    hint: "Review drafts, enroll contacts, CRM",
    icon: "offices",
    can: "Approve AI drafts, reply in the inbox, enroll contacts, work the CRM.",
    cannot: "Build or delete workflows, edit agents, settings.",
  },
  {
    id: "pricing",
    label: "Pricing",
    hint: "Quote requests, deals, quotes",
    icon: "leads",
    can: "Quote requests, deals, quotes, tasks; approve AI drafts.",
    cannot: "Workflows, agents, settings.",
  },
  {
    id: "reviewer",
    label: "Reviewer",
    hint: "Review queue and inbox",
    icon: "review",
    can: "Read replies, approve or reject AI drafts and reply in the inbox.",
    cannot: "Change workflows, contacts or the CRM.",
  },
  { id: "viewer", label: "Viewer", hint: "Read only", icon: "eye", can: "See every screen.", cannot: "Change anything." },
];

const FACTS: { value: string; label: string }[] = [
  { value: "8", label: "service lines" },
  { value: "50+", label: "workflow steps" },
  { value: "11", label: "checks on every email" },
  { value: "AI", label: "reads every reply" },
];

/** The facts counted by the running version (`GET /site`), the static ones until it answers. */
function liveFacts(data: Schemas["SiteFactsResponse"] | undefined): { value: string; label: string }[] {
  if (!data) return FACTS;
  return [
    { value: String(data.service_lines.length), label: "service lines" },
    { value: String(data.workflow_steps.total), label: "workflow steps" },
    { value: String(data.send_checks.length), label: "checks on every email" },
    { value: "AI", label: "reads every reply" },
  ];
}

const WorldLanes = lazy(() => import("./landing/WorldLanes").then((module) => ({ default: module.WorldLanes })));

const DESTINATIONS = ["to Turkey", "vers la France", "nach Deutschland", "para o Brasil", "إلى قطر", "đến Việt Nam", "a México", "do Polski"];

/** Types each phrase, pauses, deletes it, moves on. */
function useTypewriter(phrases: string[]): string {
  const reduced = useReducedMotion();
  const [index, setIndex] = useState(0);
  const [length, setLength] = useState(0);
  const [deleting, setDeleting] = useState(false);
  const phrase = phrases[index % phrases.length] ?? "";
  useEffect(() => {
    if (reduced) return undefined;
    const done = !deleting && length === phrase.length;
    const empty = deleting && length === 0;
    const delay = done ? 1600 : empty ? 250 : deleting ? 35 : 75;
    const timer = window.setTimeout(() => {
      if (done) setDeleting(true);
      else if (empty) {
        setDeleting(false);
        setIndex((current) => current + 1);
      } else setLength((current) => current + (deleting ? -1 : 1));
    }, delay);
    return () => window.clearTimeout(timer);
  }, [reduced, deleting, length, phrase]);
  return reduced ? (phrases[0] ?? "") : [...phrase].slice(0, length).join("");
}

function useAuthConfig() {
  return useQuery({ queryKey: ["auth-config"], queryFn: () => unwrap(api.GET("/api/v1/auth/config")), retry: 1, staleTime: Infinity });
}

function redirectUri(): string {
  return `${window.location.origin}/auth/callback`;
}

export function LoginPage() {
  const destination = useTypewriter(DESTINATIONS);
  const { session } = useSession();
  const location = useLocation();
  const config = useAuthConfig();
  const [returning] = useState(() => readLastSignIn() !== null);
  const theme = usePublicTheme();
  const site = useQuery({ queryKey: ["site"], queryFn: () => unwrap(api.GET("/api/v1/site")), staleTime: 300_000 });
  if (session) return <Navigate to="/" replace />;
  const from = (location.state as { from?: string } | null)?.from ?? "/";

  return (
    <div className={theme.light ? "auth light" : "auth"}>
      <div className="auth-backdrop" aria-hidden>
        <div className="auth-aurora">
          <span />
          <span />
          <span />
        </div>
        <div className="auth-map">
          <Suspense fallback={null}>
            <WorldLanes />
          </Suspense>
        </div>
        <div className="auth-grain" />
      </div>
      <header className="auth-top">
        <Link to="/welcome" className="auth-brand">
          <BrandMark /> LaneReach
        </Link>
        <nav>
          <Link to="/welcome">Product</Link>
          <Link to="/welcome#contact">Contact us</Link>
          <ThemeSwitch light={theme.light} onToggle={theme.toggle} />
        </nav>
      </header>
      <main className="auth-grid">
        <section className="auth-story">
          <span className="auth-pill">
            <Icon name="sparkles" /> AI outreach &amp; CRM for freight forwarders
          </span>
          <h1>
            Your freight desk,
            <br />
            <span className="auth-gradient">with an AI co-pilot.</span>
          </h1>
          <p className="auth-lead">
            Outreach from your office <strong className="auth-typing" dir="auto">{destination}</strong>
            <br />
            Every reply read, every rate request turned into a deal and a priced draft.
          </p>
          <AiFeed />
          <ul className="auth-facts">
            {liveFacts(site.data).map((fact) => (
              <li key={fact.label}>
                <strong>{fact.value}</strong>
                <span>{fact.label}</span>
              </li>
            ))}
          </ul>
        </section>
        <section className="auth-panel">
          <div className="auth-card login-card stack">
            <div className="auth-card-glow" aria-hidden />
            <div>
              <div className="eyebrow">{returning ? "Welcome back" : "Welcome"}</div>
              <h1>Sign in to LaneReach</h1>
              <p className="small muted" style={{ margin: "4px 0 0" }}>
                {returning ? "Pick up where you left off." : "Use your company account to continue."}
              </p>
            </div>
            {config.isLoading && <Loading label="Loading sign-in…" />}
            {config.data?.mode === "oidc" && config.data.authority && config.data.client_id ? (
              <CompanySignIn
                authority={config.data.authority}
                clientId={config.data.client_id}
                scopes={config.data.scopes}
                providerName={config.data.provider_name}
                from={from}
              />
            ) : config.data?.mode === "oidc" ? (
              <div className="alert warning">Company sign-in is not configured yet (OIDC_AUTHORITY and OIDC_CLIENT_ID).</div>
            ) : (
              !config.isLoading && <DevSignIn from={from} />
            )}
            <div className="auth-trust">
              <span>
                <Icon name="shield" /> Data kept apart per company
              </span>
              <span>
                <Icon name="key" /> Roles for every team
              </span>
              <span>
                <Icon name="file" /> Every change audited
              </span>
            </div>
          </div>
          <p className="auth-new">
            New to LaneReach? <Link to="/welcome#contact">Talk to us</Link> or <Link to="/welcome">see what it does</Link>.
          </p>
        </section>
      </main>
    </div>
  );
}

function CompanySignIn({
  authority,
  clientId,
  scopes,
  providerName,
  from,
}: {
  authority: string;
  clientId: string;
  scopes: string[];
  providerName: string;
  from: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const pending = { state: randomToken(16), verifier: randomToken(48), returnTo: from };
      savePending(pending);
      window.location.assign(await authorizeUrl({ authority, clientId, scopes }, redirectUri(), pending));
    } catch (caught) {
      setError(caught);
      setBusy(false);
    }
  };
  return (
    <div className="card stack" style={{ padding: 20 }}>
      <Button variant="primary" busy={busy} onClick={() => void start()} className="btn primary sso-button">
        <span className="sso-mark" aria-hidden>
          <span /><span /><span /><span />
        </span>
        Sign in with {providerName}
      </Button>
      <ErrorAlert error={error} />
      <p className="small muted" style={{ margin: 0, textAlign: "center" }}>
        Use your work account. Your company's administrator decides your role in LaneReach.
      </p>
    </div>
  );
}

function DevSignIn({ from }: { from: string }) {
  const { signIn } = useSession();
  const navigate = useNavigate();
  const toast = useToast();
  const tenants = useQuery({
    queryKey: ["dev-tenants"],
    queryFn: () => unwrap(api.GET("/api/v1/auth/dev-tenants")),
    retry: false,
    // While the API restarts (a deploy), keep trying instead of leaving the page stuck.
    refetchInterval: (query) => (query.state.status === "error" ? 4000 : false),
  });
  // Opens where the person left last time (company, email, role).
  const [last] = useState(readLastSignIn);
  const [tenantId, setTenantId] = useState(last?.tenantId ?? "");
  const [subject, setSubject] = useState(last?.subject ?? "admin@forwarder.example");
  const [role, setRole] = useState(last && ROLES.some((item) => item.id === last.role) ? last.role : "admin");
  const chosen = ROLES.find((item) => item.id === role) ?? ROLES[0];
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const selectedTenant =
    tenants.data?.find((tenant) => tenant.id === tenantId) ?? tenants.data?.[0];

  const submit = async () => {
    if (!selectedTenant) return;
    setBusy(true);
    setError(null);
    try {
      await signIn(selectedTenant, subject, [role]);
      saveLastSignIn({ tenantId: selectedTenant.id, subject, role });
      toast(`Signed in as ${ROLE_NAMES[role] ?? role} at ${selectedTenant.name}.`);
      navigate(from, { replace: true });
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="card stack"
      style={{ padding: 20 }}
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <div className="row between">
        <strong>Development sign-in</strong>
        <Badge tone="amber">dev only</Badge>
      </div>
      {tenants.isLoading && <Loading label="Loading companies…" />}
      {tenants.error && (
        <div className="alert warning row nowrap" role="status">
          <span className="spinner" aria-hidden />
          <span>
            Can't reach LaneReach right now, retrying every few seconds. If this lasts, check that the API is running (development sign-in needs
            AUTH_MODE=dev).
          </span>
        </div>
      )}
      {tenants.data && tenants.data.length === 0 && <div className="alert info">No tenants yet. Run <code>make seed</code> to load the demo tenants.</div>}
      {tenants.data && tenants.data.length > 0 && (
        <label className="field">
          <span>Company</span>
          <select className="select" value={selectedTenant?.id ?? ""} onChange={(event) => setTenantId(event.target.value)}>
            {tenants.data.map((tenant) => (
              <option key={tenant.id} value={tenant.id}>
                {tenant.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="field">
        <span>Your email</span>
        <input className="input" type="email" value={subject} onChange={(event) => setSubject(event.target.value)} autoComplete="username" />
      </label>
      <div className="field">
        <span>Role</span>
        <div className="role-grid" role="radiogroup" aria-label="Role">
          {ROLES.map((option) => (
            <button
              key={option.id}
              type="button"
              role="radio"
              aria-checked={role === option.id}
              aria-label={`${option.label}: ${option.hint}`}
              className={role === option.id ? "role-tile on" : "role-tile"}
              onClick={() => setRole(option.id)}
            >
              <Icon name={option.icon} />
              <strong>{option.label}</strong>
              <span>{option.hint}</span>
            </button>
          ))}
        </div>
      </div>
      {chosen && (
        <div className="role-summary" aria-live="polite">
          <div>
            You will sign in as <strong>{chosen.label}</strong>
            {selectedTenant ? (
              <>
                {" "}at <strong>{selectedTenant.name}</strong>
              </>
            ) : null}
            .
          </div>
          <div className="small">
            <span className="role-can">Can:</span> {chosen.can}
          </div>
          <div className="small">
            <span className="role-cannot">Cannot:</span> {chosen.cannot}
          </div>
        </div>
      )}
      <ErrorAlert error={error} />
      <Button variant="primary" busy={busy} disabled={!selectedTenant || !subject} type="submit">
        Sign in as {chosen?.label ?? "…"} <Icon name="route" />
      </Button>
      <p className="small muted" style={{ margin: 0, textAlign: "center" }}>
        Dev tokens exist only in development. Staging and production sign in with the company's identity provider.
      </p>
    </form>
  );
}

/** `/auth/callback`: finish the company sign-in. */
export function AuthCallbackPage() {
  const config = useAuthConfig();
  const { signInWithToken } = useSession();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!config.data?.authority || !config.data.client_id) return;
    const answer = readCallback(window.location.search);
    const pending = takePending();
    if ("error" in answer) {
      setError(answer.error);
      return;
    }
    if (!pending || pending.state !== answer.state) {
      setError("This sign-in link has expired or was not started here. Please sign in again.");
      return;
    }
    const settings = { authority: config.data.authority, clientId: config.data.client_id, scopes: config.data.scopes };
    void redeemCode(settings, redirectUri(), answer.code, pending.verifier)
      .then(({ accessToken, expiresIn }) => signInWithToken(accessToken, expiresIn))
      .then(() => navigate(pending.returnTo || "/", { replace: true }))
      .catch((caught: unknown) => setError(describeError(caught)));
  }, [config.data, navigate, signInWithToken]);
  return (
    <div className="login-side" style={{ minHeight: "100vh" }}>
      <div className="login-card stack" style={{ textAlign: "center" }}>
        {error ? (
          <>
            <div className="alert error">{error}</div>
            <Button onClick={() => navigate("/login", { replace: true })}>Back to sign-in</Button>
          </>
        ) : (
          <Loading label="Signing you in…" />
        )}
      </div>
    </div>
  );
}
