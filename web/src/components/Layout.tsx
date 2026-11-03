/** App shell: navy sidebar with icons, top bar (search, sending state, theme) and Ctrl+K palette. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, describeError, unwrap } from "../api/client";
import { keys, useInboxCounts, usePendingReviewCount, useSendingSettings, useUnreadNotifications } from "../api/queries";
import { formatRelative } from "../lib/format";
import { useSession } from "../auth/session";
import { initials } from "../lib/freight";
import { useTheme } from "../lib/theme";
import { CommandPalette, useCommandPaletteShortcut } from "./CommandPalette";
import { ShortcutsModal, TopProgress, useKeyboardShortcuts, useSpotlight } from "./delight";
import type { Command } from "./CommandPalette";
import { Icon } from "./icons";
import type { IconName } from "./icons";
import { Badge, Button, useToast } from "./ui";
import { roleNames } from "../lib/lastSignIn";
import { AssistantDrawer, useAssistantOpener } from "./Assistant";
import type { AssistantRequest } from "./Assistant";

interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  count?: number;
  keywords?: string[];
}

interface NavGroup {
  title: string;
  items: NavItem[];
}

function useNavigation(pending: number | undefined, unread: number | undefined): NavGroup[] {
  return [
    {
      title: "Overview",
      items: [
        { to: "/", label: "Dashboard", icon: "dashboard", keywords: ["home", "stats"] },
        { to: "/inbox", label: "Inbox", icon: "mail", count: unread, keywords: ["replies", "conversations", "unified", "mailbox"] },
        { to: "/reviews", label: "Review queue", icon: "review", count: pending, keywords: ["drafts", "approve"] },
        { to: "/insights", label: "AI insights", icon: "sparkles", keywords: ["digest", "weekly", "lead score", "hot deals", "follow-ups", "quiet deals", "ai"] },
      ],
    },
    {
      title: "CRM",
      items: [
        { to: "/pipeline", label: "Pipeline", icon: "trending", keywords: ["deals", "kanban", "crm", "board"] },
        { to: "/quotes", label: "Quotes", icon: "quote", keywords: ["quotation", "rates", "pricing", "charges", "crm"] },
        { to: "/pricing", label: "Pricing", icon: "pricing", keywords: ["rates", "rate sheets", "excel", "tariff", "websites", "market", "buy rates"] },
        { to: "/tasks", label: "Tasks", icon: "clock", keywords: ["to do", "follow up", "crm"] },
        { to: "/leads", label: "Quote requests", icon: "leads", keywords: ["pricing", "quotes", "leads"] },
        { to: "/contacts", label: "Contacts", icon: "contacts", keywords: ["people", "crm"] },
        { to: "/companies", label: "Companies", icon: "companies", keywords: ["accounts", "crm", "forwarders"] },
      ],
    },
    {
      title: "Automation",
      items: [
        { to: "/workflows", label: "Workflows", icon: "workflow", keywords: ["campaigns", "canvas", "sequence", "triggers", "results", "opens", "clicks"] },
        { to: "/enrollments", label: "Enrollments", icon: "enrollments" },
        { to: "/events", label: "Events", icon: "events" },
      ],
    },
    {
      title: "Outreach",
      items: [
        { to: "/scripts", label: "Scripts", icon: "scripts", keywords: ["sharepoint", "word", "templates", "email"] },
        { to: "/agents", label: "Agents", icon: "agents", keywords: ["senders", "signatures", "air", "ocean"] },
        { to: "/countries", label: "Countries & languages", icon: "globe", keywords: ["translation", "phrase"] },
        { to: "/offices", label: "Offices", icon: "offices", keywords: ["mailboxes"] },
        { to: "/templates", label: "Templates", icon: "templates", keywords: ["replies"] },
        { to: "/forms", label: "Website forms", icon: "file", keywords: ["quote form", "embed", "website", "request a quote"] },
      ],
    },
    {
      title: "Delivery",
      items: [
        { to: "/outbox", label: "Outbox", icon: "outbox", keywords: ["sent", "emails"] },
        { to: "/notifications", label: "Notifications", icon: "bell" },
        { to: "/suppressions", label: "Suppressions", icon: "shield", keywords: ["unsubscribe", "do not email"] },
      ],
    },
    {
      title: "Tools",
      items: [
        { to: "/integrations", label: "Integrations", icon: "sync", keywords: ["webhooks", "zapier", "make", "n8n", "slack", "calendly", "cal.com", "verification"] },
        { to: "/ai", label: "AI playground", icon: "sparkles", keywords: ["classify", "extract"] },
        { to: "/settings", label: "Settings", icon: "settings", keywords: ["kill switch", "branding", "sharepoint", "api keys"] },
        { to: "/help", label: "Help center", icon: "help", keywords: ["how to", "guide", "docs", "assistant", "faq"] },
      ],
    },
  ];
}

function NavSection({ group }: { group: NavGroup }) {
  return (
    <div className="nav-group">
      <div className="nav-group-title">{group.title}</div>
      {group.items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === "/"}
          className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
        >
          <Icon name={item.icon} />
          <span className="nav-label">{item.label}</span>
          {item.count ? <span className="nav-count">{item.count}</span> : null}
        </NavLink>
      ))}
    </div>
  );
}

export function BrandMark() {
  return (
    <span className="brand-mark">
      <Icon name="route" strokeWidth={2.2} />
    </span>
  );
}

export function Layout() {
  const { session, signOut, can } = useSession();
  const navigate = useNavigate();
  const settings = useSendingSettings();
  const pending = usePendingReviewCount();
  const inbox = useInboxCounts();
  const theme = useTheme();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [assistant, setAssistant] = useState<AssistantRequest | null>(null);
  const openAssistant = useCallback((request: AssistantRequest = {}) => setAssistant(request), []);
  useAssistantOpener(openAssistant);
  const location = useLocation();
  const groups = useNavigation(pending.data, inbox.data?.unread);
  const openPalette = useCallback(() => setPaletteOpen(true), []);
  const openHelp = useCallback(() => setHelpOpen(true), []);
  useCommandPaletteShortcut(openPalette);
  useKeyboardShortcuts(openHelp);
  useSpotlight();
  // On phones the menu is a drawer: close it after navigating.
  useEffect(() => setNavOpen(false), [location.pathname]);

  const sync = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/scripts/sync")),
    onSuccess: (summary) => {
      toast(`Library synced: ${summary.imported} imported, ${summary.unchanged} unchanged.`);
      void queryClient.invalidateQueries({ queryKey: keys.scripts });
    },
    onError: (error) => toast(describeError(error), "error"),
  });

  const commands = useMemo<Command[]>(() => {
    const screens = groups.flatMap((group) =>
      group.items.map((item) => ({
        id: `go:${item.to}`,
        label: item.label,
        icon: item.icon,
        hint: group.title,
        keywords: item.keywords,
        run: () => navigate(item.to),
      })),
    );
    const actions: Command[] = [
      { id: "new-workflow", label: "New workflow", icon: "plus", hint: "Action", run: () => navigate("/workflows?new=1") },
      { id: "upload-script", label: "Upload a Word script", icon: "upload", hint: "Action", keywords: ["docx", "sharepoint"], run: () => navigate("/scripts?upload=1") },
      { id: "new-deal", label: "Open the pipeline board", icon: "trending", hint: "CRM", keywords: ["deal", "crm"], run: () => navigate("/pipeline") },
      { id: "import-agents", label: "Import agents from CSV", icon: "agents", hint: "Action", run: () => navigate("/agents?import=1") },
      { id: "theme", label: theme.dark ? "Switch to light theme" : "Switch to dark theme", icon: theme.dark ? "sun" : "moon", hint: "Action", keywords: ["dark", "light", "theme"], run: theme.toggle },
      { id: "assistant", label: "Ask LaneReach (assistant)", icon: "sparkles", hint: "Help", keywords: ["help", "how", "question", "chat", "ai", "draft workflow"], run: () => openAssistant() },
      { id: "shortcuts", label: "Keyboard shortcuts", icon: "key", hint: "Help", keywords: ["help", "keys"], run: openHelp },
      { id: "sign-out", label: "Sign out", icon: "logout", hint: "Action", run: () => { signOut(); navigate("/login"); } },
    ];
    if (can("workflows:write")) {
      actions.splice(2, 0, { id: "sync", label: "Sync the scripts library now", icon: "sync", hint: "Action", keywords: ["sharepoint", "refresh"], run: () => sync.mutate() });
    }
    return [...screens, ...actions];
  }, [groups, navigate, theme, signOut, can, sync, openHelp, openAssistant]);

  return (
    <div className={navOpen ? "app nav-open" : "app"}>
      <TopProgress />
      {navOpen && <div className="nav-scrim" onClick={() => setNavOpen(false)} aria-hidden />}
      <nav className="sidebar" aria-label="Main" id="main-navigation">
        <div className="brand">
          <BrandMark />
          <span>
            LaneReach
            <span className="brand-sub">{session?.tenantName}</span>
          </span>
        </div>
        {groups.map((group) => (
          <NavSection key={group.title} group={group} />
        ))}
        <div className="sidebar-footer">
          <span className="avatar any" style={{ width: 30, height: 30, borderRadius: 9, fontSize: 12 }}>
            {initials(session?.subject ?? "?")}
          </span>
          <div style={{ minWidth: 0 }}>
            <strong className="truncate" style={{ display: "block" }}>{session?.subject}</strong>
            <div className="truncate">{session?.roles.map((role) => role.replaceAll("_", " ")).join(", ")}</div>
          </div>
        </div>
      </nav>
      <div className="main">
        <header className="topbar">
          <div className="row">
            <button
              type="button"
              className="btn ghost icon menu-button"
              aria-label="Menu"
              aria-expanded={navOpen}
              aria-controls="main-navigation"
              onClick={() => setNavOpen((open) => !open)}
            >
              <Icon name="menu" />
            </button>
            <button type="button" className="search-button" onClick={openPalette} aria-label="Search (Ctrl+K)">
              <Icon name="search" />
              <span className="label">Search or jump to…</span>
              <span className="kbd">Ctrl K</span>
            </button>
            {settings.data?.sending_paused ? (
              <Badge tone="red">
                <Icon name="pause" /> <span className="hide-mobile">Sending paused</span>
              </Badge>
            ) : (
              <Badge tone="green">
                <span className="dot live" /> <span className="hide-mobile">Sending on</span>
              </Badge>
            )}
            {settings.data && (
              <span className="small muted hide-mobile">
                {settings.data.sending_window_start.slice(0, 5)}–{settings.data.sending_window_end.slice(0, 5)} recipient time
              </span>
            )}
          </div>
          <div className="row">
            <a className="small hide-mobile" href="/docs" target="_blank" rel="noreferrer">
              API docs
            </a>
            <Button size="sm" className="btn sm ask-button" onClick={() => openAssistant()} title="Ask LaneReach (Ctrl+J)">
              <Icon name="sparkles" /> <span className="hide-mobile">Ask LaneReach</span>
            </Button>
            <NewMenu />
            <NotificationBell />
            <Button variant="ghost" size="sm" className="btn ghost sm icon" onClick={theme.toggle} aria-label={theme.dark ? "Switch to light theme" : "Switch to dark theme"} title={theme.dark ? "Switch to light theme" : "Switch to dark theme"}>
              <Icon name={theme.dark ? "sun" : "moon"} />
            </Button>
            <UserMenu
              onHelp={openHelp}
              onSignOut={() => {
                signOut();
                navigate("/login");
              }}
            />
          </div>
        </header>
        <Outlet />
      </div>
      {paletteOpen && <CommandPalette commands={commands} onClose={() => setPaletteOpen(false)} />}
      {helpOpen && <ShortcutsModal onClose={() => setHelpOpen(false)} />}
      {assistant && <AssistantDrawer request={assistant} onClose={() => setAssistant(null)} />}
    </div>
  );
}

/** Who is signed in (name, role, company) with switch role, shortcuts and sign out. */
function UserMenu({ onHelp, onSignOut }: { onHelp: () => void; onSignOut: () => void }) {
  const { session } = useSession();
  const [open, setOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setOpen(false), [location.pathname]);
  if (!session) return null;
  const roles = roleNames(session.roles);
  return (
    <div className="bell">
      <button
        type="button"
        className="user-chip"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={`Signed in as ${session.subject}, ${roles}, at ${session.tenantName}`}
      >
        <span className="avatar any">{initials(session.subject)}</span>
        <span className="user-chip-text hide-mobile">
          <strong className="truncate">{session.subject}</strong>
          <span className="truncate">
            {roles} · {session.tenantName}
          </span>
        </span>
      </button>
      {open && (
        <>
          <div className="popover-scrim" onClick={() => setOpen(false)} aria-hidden />
          <div className="popover user-menu" role="menu" aria-label="Account">
            <div className="user-menu-head">
              <span className="avatar any">{initials(session.subject)}</span>
              <div style={{ minWidth: 0 }}>
                <strong className="truncate" style={{ display: "block" }}>{session.subject}</strong>
                <span className="small muted">
                  {roles} at {session.tenantName}
                </span>
              </div>
            </div>
            <button type="button" role="menuitem" onClick={onSignOut}>
              <Icon name="user" /> Switch role or company
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onHelp();
              }}
            >
              <Icon name="key" /> Keyboard shortcuts
            </button>
            <button type="button" role="menuitem" className="danger" onClick={onSignOut}>
              <Icon name="logout" /> Sign out
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function NotificationBell() {
  const unread = useUnreadNotifications();
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const count = unread.data?.items.length ?? 0;
  useEffect(() => setOpen(false), [location.pathname]);
  useEffect(() => {
    if (!open) return undefined;
    const close = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [open]);
  return (
    <div className="bell">
      <button type="button" className={count ? "btn ghost sm icon bell-button ringing" : "btn ghost sm icon bell-button"} aria-label={`Notifications (${count} unread)`} aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        <Icon name="bell" />
        {count > 0 && <span className="bell-count">{count > 7 ? "8+" : count}</span>}
      </button>
      {open && (
        <>
          <div className="popover-scrim" onClick={() => setOpen(false)} aria-hidden />
          <div className="popover" role="dialog" aria-label="Unread notifications">
            <div className="row between" style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)" }}>
              <strong>Notifications</strong>
              <Link to="/notifications" className="small">See all</Link>
            </div>
            {count === 0 ? (
              <div className="empty small">You are all caught up.</div>
            ) : (
              <ul className="popover-list">
                {unread.data?.items.map((item) => (
                  <li key={item.id}>
                    <Link to={item.contact_id ? `/contacts/${item.contact_id}` : "/notifications"} className="plain">
                      <strong className="truncate" style={{ display: "block" }}>{item.subject}</strong>
                      <span className="small muted">{item.channel.replaceAll("_", " ")} · {formatRelative(item.created_at)}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/** Scrollable page body. */
export function Page({ children }: { children: ReactNode }) {
  return <div className="content page-in">{children}</div>;
}

const NEW_ITEMS: { to: string; label: string; icon: IconName; scope: string }[] = [
  { to: "/contacts?new=1", label: "Contact", icon: "contacts", scope: "workflows:write" },
  { to: "/pipeline?new=1", label: "Deal", icon: "trending", scope: "crm:write" },
  { to: "/tasks", label: "Task", icon: "clock", scope: "crm:write" },
  { to: "/workflows?new=1", label: "Workflow", icon: "workflow", scope: "workflows:write" },
  { to: "/forms?new=1", label: "Website form", icon: "file", scope: "workflows:write" },
  { to: "/scripts?upload=1", label: "Script (Word)", icon: "upload", scope: "workflows:write" },
];

/** "New" in the top bar: the everyday creates in one place. */
function NewMenu() {
  const { can } = useSession();
  const [open, setOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setOpen(false), [location.pathname, location.search]);
  const items = NEW_ITEMS.filter((item) => can(item.scope));
  if (items.length === 0) return null;
  return (
    <div className="bell">
      <Button size="sm" variant="primary" onClick={() => setOpen((value) => !value)} aria-expanded={open} aria-haspopup="menu">
        <Icon name="plus" /> <span className="hide-mobile">New</span>
      </Button>
      {open && (
        <>
          <div className="popover-scrim" onClick={() => setOpen(false)} aria-hidden />
          <div className="popover new-menu" role="menu" aria-label="Create">
            {items.map((item) => (
              <Link key={item.to} to={item.to} className="plain" role="menuitem">
                <span className="new-menu-icon"><Icon name={item.icon} /></span>
                {item.label}
              </Link>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
