/** Light/dark theme: follows the system unless the user picks one (remembered per browser). */
import { useCallback, useEffect, useState } from "react";

export type ThemeChoice = "light" | "dark" | "system";

const STORAGE_KEY = "lanereach.theme";

function readChoice(): ThemeChoice {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    return "system";
  }
}

function applyChoice(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
}

/** Apply the remembered theme before React renders (avoids a flash). */
export function applyStoredTheme(): void {
  applyChoice(readChoice());
}

export function systemPrefersDark(): boolean {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(readChoice);
  useEffect(() => applyChoice(choice), [choice]);
  const dark = choice === "dark" || (choice === "system" && systemPrefersDark());
  const choose = useCallback((next: "light" | "dark") => {
    setChoice(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Private windows may refuse storage; the choice still applies for this visit.
    }
  }, []);
  const toggle = useCallback(() => choose(dark ? "light" : "dark"), [choose, dark]);
  return { choice, dark, toggle, choose };
}

/**
 * The website and sign-in page: dark (their designed look) unless light was picked, on these pages
 * or in the console. The choice is the same saved setting, so it carries across the application.
 */
export function usePublicTheme() {
  const { choice, choose } = useTheme();
  const light = choice === "light";
  const toggle = useCallback(() => choose(light ? "dark" : "light"), [choose, light]);
  return { light, toggle };
}
