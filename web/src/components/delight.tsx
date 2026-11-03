/**
 * Small touches that make the console feel alive: confetti for won deals, a loading bar, a
 * spotlight that follows the cursor over cards, cards that rise into view, keyboard shortcuts,
 * and friendly error/404 screens. All of it stays still under `prefers-reduced-motion`.
 */
import { useIsFetching, useIsMutating } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, isRouteErrorResponse, useNavigate, useRouteError } from "react-router-dom";
import { Icon } from "./icons";
import { RouteArt } from "./motion";
import { Modal } from "./ui";

const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

/** Burst of confetti from a point on screen (defaults to the top centre). */
export function fireConfetti(origin?: { x: number; y: number }): void {
  if (reducedMotion()) return;
  const canvas = document.createElement("canvas");
  canvas.className = "confetti";
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = window.innerWidth * ratio;
  canvas.height = window.innerHeight * ratio;
  document.body.appendChild(canvas);
  const context = canvas.getContext("2d");
  if (!context) {
    canvas.remove();
    return;
  }
  context.scale(ratio, ratio);
  const colors = ["#2f5bea", "#19c3b1", "#ffb938", "#7b5cff", "#3fd49b", "#ff6b7d"];
  const startX = origin?.x ?? window.innerWidth / 2;
  const startY = origin?.y ?? window.innerHeight / 3;
  const pieces = Array.from({ length: 140 }, (_, index) => {
    const angle = Math.random() * Math.PI * 2;
    const speed = 4 + Math.random() * 9;
    return {
      x: startX,
      y: startY,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed - 6,
      size: 5 + Math.random() * 6,
      spin: Math.random() * Math.PI,
      spinSpeed: (Math.random() - 0.5) * 0.4,
      color: colors[index % colors.length] ?? "#fff",
      shape: index % 3,
    };
  });
  const started = performance.now();
  const tick = (time: number) => {
    const elapsed = time - started;
    context.clearRect(0, 0, window.innerWidth, window.innerHeight);
    for (const piece of pieces) {
      piece.vy += 0.28;
      piece.vx *= 0.99;
      piece.x += piece.vx;
      piece.y += piece.vy;
      piece.spin += piece.spinSpeed;
      context.save();
      context.globalAlpha = Math.max(0, 1 - elapsed / 2600);
      context.translate(piece.x, piece.y);
      context.rotate(piece.spin);
      context.fillStyle = piece.color;
      if (piece.shape === 0) context.fillRect(-piece.size / 2, -piece.size / 4, piece.size, piece.size / 2);
      else if (piece.shape === 1) {
        context.beginPath();
        context.arc(0, 0, piece.size / 3, 0, Math.PI * 2);
        context.fill();
      } else {
        // A tiny plane, for the freight crowd.
        context.beginPath();
        context.moveTo(piece.size / 2, 0);
        context.lineTo(-piece.size / 2, -piece.size / 3);
        context.lineTo(-piece.size / 4, 0);
        context.lineTo(-piece.size / 2, piece.size / 3);
        context.closePath();
        context.fill();
      }
      context.restore();
    }
    if (elapsed < 2600) requestAnimationFrame(tick);
    else canvas.remove();
  };
  requestAnimationFrame(tick);
}

/** A thin bar at the top of the window while anything loads or saves. */
export function TopProgress() {
  const fetching = useIsFetching();
  const mutating = useIsMutating();
  const busy = fetching + mutating > 0;
  // Only show for work that takes a moment, so quick refetches do not flicker.
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!busy) {
      setVisible(false);
      return undefined;
    }
    const timer = window.setTimeout(() => setVisible(true), 250);
    return () => window.clearTimeout(timer);
  }, [busy]);
  return <div className={visible ? "top-progress on" : "top-progress"} aria-hidden />;
}

/** Cards with `.spotlight` get a soft light under the cursor (CSS reads --mx/--my). */
export function useSpotlight(): void {
  useEffect(() => {
    if (reducedMotion()) return undefined;
    const move = (event: PointerEvent) => {
      const card = (event.target as HTMLElement | null)?.closest?.<HTMLElement>(".spotlight, .card.lift, .agent-card");
      if (!card) return;
      const box = card.getBoundingClientRect();
      card.style.setProperty("--mx", `${event.clientX - box.left}px`);
      card.style.setProperty("--my", `${event.clientY - box.top}px`);
    };
    document.addEventListener("pointermove", move, { passive: true });
    return () => document.removeEventListener("pointermove", move);
  }, []);
}

/** Children rise into place the first time they scroll into view. */
export function Reveal({ children, delay = 0 }: { children: ReactNode; delay?: number }) {
  const element = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(() => typeof IntersectionObserver === "undefined" || reducedMotion());
  useEffect(() => {
    if (shown || !element.current) return undefined;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setShown(true);
          observer.disconnect();
        }
      },
      { rootMargin: "0px 0px -40px 0px" },
    );
    observer.observe(element.current);
    return () => observer.disconnect();
  }, [shown]);
  return (
    <div ref={element} className={shown ? "reveal shown" : "reveal"} style={{ transitionDelay: `${delay}ms` }}>
      {children}
    </div>
  );
}

export const GO_SHORTCUTS: { key: string; to: string; label: string }[] = [
  { key: "d", to: "/", label: "Dashboard" },
  { key: "i", to: "/inbox", label: "Inbox" },
  { key: "r", to: "/reviews", label: "Review queue" },
  { key: "l", to: "/leads", label: "Quote requests" },
  { key: "p", to: "/pipeline", label: "Pipeline" },
  { key: "k", to: "/tasks", label: "Tasks" },
  { key: "w", to: "/workflows", label: "Workflows" },
  { key: "s", to: "/scripts", label: "Scripts" },
  { key: "a", to: "/agents", label: "Agents" },
  { key: "c", to: "/contacts", label: "Contacts" },
  { key: "o", to: "/outbox", label: "Outbox" },
  { key: "t", to: "/settings", label: "Settings" },
];

/** "g" then a letter jumps to a screen; "?" opens the shortcut list. */
export function useKeyboardShortcuts(onHelp: () => void): void {
  const navigate = useNavigate();
  useEffect(() => {
    let pendingG = 0;
    const listener = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return;
      if (event.key === "?") {
        event.preventDefault();
        onHelp();
        return;
      }
      if (event.key === "g") {
        pendingG = Date.now();
        return;
      }
      if (Date.now() - pendingG < 1200) {
        const shortcut = GO_SHORTCUTS.find((item) => item.key === event.key.toLowerCase());
        pendingG = 0;
        if (shortcut) {
          event.preventDefault();
          navigate(shortcut.to);
        }
      }
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [navigate, onHelp]);
}

export function ShortcutsModal({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="Keyboard shortcuts" onClose={onClose}>
      <div className="shortcut-grid">
        <span><span className="kbd">Ctrl</span> <span className="kbd">K</span></span>
        <span>Search screens and actions</span>
        <span><span className="kbd">/</span></span>
        <span>Search (outside text fields)</span>
        <span><span className="kbd">Ctrl</span> <span className="kbd">J</span></span>
        <span>Ask LaneReach (assistant)</span>
        <span><span className="kbd">Ctrl</span> <span className="kbd">Z</span></span>
        <span>Undo on the workflow canvas (Ctrl Shift Z to redo)</span>
        <span><span className="kbd">?</span></span>
        <span>This list</span>
        {GO_SHORTCUTS.map((shortcut) => (
          <div key={shortcut.key} style={{ display: "contents" }}>
            <span>
              <span className="kbd">g</span> <span className="kbd">{shortcut.key}</span>
            </span>
            <span>{shortcut.label}</span>
          </div>
        ))}
        <span><span className="kbd">Esc</span></span>
        <span>Close a drawer or dialog</span>
      </div>
    </Modal>
  );
}

function LostAtSea({ title, detail, action }: { title: string; detail: string; action?: ReactNode }) {
  return (
    <div className="lost">
      <div className="lost-art">
        <RouteArt density="compact" className="route-art" />
      </div>
      <div className="lost-body">
        <div className="eyebrow">
          <Icon name="route" /> Off the lane
        </div>
        <h1>{title}</h1>
        <p>{detail}</p>
        <div className="row" style={{ justifyContent: "center" }}>
          {action}
          <Link className="btn primary" to="/">
            Back to the dashboard
          </Link>
        </div>
      </div>
    </div>
  );
}

export function NotFoundPage() {
  return <LostAtSea title="This page drifted off course" detail="The address does not match any screen. It may have moved, or the link is incomplete." />;
}

/** Shown by the router when a screen throws, instead of a blank page. */
export function RouteErrorPage() {
  const error = useRouteError();
  const detail = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : "Something unexpected happened.";
  return (
    <LostAtSea
      title="Something went wrong on this screen"
      detail={`${detail} Reload to try again; if it keeps happening, send the time and this message to support.`}
      action={
        <button type="button" className="btn" onClick={() => window.location.reload()}>
          Reload
        </button>
      }
    />
  );
}
