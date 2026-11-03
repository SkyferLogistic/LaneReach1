/** Ctrl/⌘+K palette: jump to any screen or run a quick action. */
import { useEffect, useMemo, useRef, useState } from "react";
import { rankItems } from "../lib/search";
import { Icon } from "./icons";
import type { IconName } from "./icons";

export interface Command {
  id: string;
  label: string;
  icon: IconName;
  hint?: string;
  keywords?: string[];
  run: () => void;
}

/** Open with Ctrl+K / ⌘+K anywhere (and "/" outside text fields). */
export function useCommandPaletteShortcut(open: () => void): void {
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));
      if ((event.key === "k" || event.key === "K") && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        open();
      } else if (event.key === "/" && !typing) {
        event.preventDefault();
        open();
      }
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [open]);
}

export function CommandPalette({ commands, onClose }: { commands: Command[]; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const results = useMemo(() => rankItems(query, commands).slice(0, 12), [query, commands]);

  useEffect(() => input.current?.focus(), []);
  useEffect(() => setIndex(0), [query]);

  const choose = (command: Command | undefined) => {
    if (!command) return;
    onClose();
    command.run();
  };

  return (
    <div className="overlay top" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="palette-modal" role="dialog" aria-modal="true" aria-label="Command palette">
        <div className="palette-search">
          <Icon name="search" />
          <input
            ref={input}
            value={query}
            placeholder="Go to a screen or run an action…"
            aria-label="Search commands"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setIndex((current) => Math.min(current + 1, results.length - 1));
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setIndex((current) => Math.max(current - 1, 0));
              } else if (event.key === "Enter") {
                event.preventDefault();
                choose(results[index]);
              } else if (event.key === "Escape") {
                onClose();
              }
            }}
          />
          <span className="kbd">Esc</span>
        </div>
        <div className="palette-list" role="listbox">
          {results.length === 0 && <div className="empty small">Nothing matches “{query}”.</div>}
          {results.map((command, position) => (
            <button
              key={command.id}
              type="button"
              role="option"
              aria-selected={position === index}
              className={position === index ? "palette-option on" : "palette-option"}
              onMouseEnter={() => setIndex(position)}
              onClick={() => choose(command)}
            >
              <Icon name={command.icon} />
              {command.label}
              {command.hint && <span className="hint">{command.hint}</span>}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
