/** Small hooks shared by the landing widgets. */
import { useEffect, useState } from "react";
import type { RefObject } from "react";
import { useReducedMotion } from "../../components/motion";

/** True once the element has been on screen (stays true), so scripted demos start when seen. */
export function useSeen(element: RefObject<HTMLElement | null>, threshold = 0.25): boolean {
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    if (seen) return undefined;
    if (!element.current || typeof IntersectionObserver === "undefined") {
      setSeen(true);
      return undefined;
    }
    const observer = new IntersectionObserver((entries) => entries.some((entry) => entry.isIntersecting) && setSeen(true), { threshold });
    observer.observe(element.current);
    return () => observer.disconnect();
  }, [element, threshold, seen]);
  return seen;
}

/** A counter that goes up every `every` ms while `running` (never under reduced motion). */
export function useTicker(running: boolean, every: number): [number, (value: number) => void] {
  const reduced = useReducedMotion();
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (reduced || !running) return undefined;
    const timer = window.setInterval(() => setTick((current) => current + 1), every);
    return () => window.clearInterval(timer);
  }, [reduced, running, every]);
  return [tick, setTick];
}
