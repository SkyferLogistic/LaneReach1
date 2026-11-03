/**
 * A slowly turning globe (canvas) with air and sea lanes and cargo moving along them. Drag to spin
 * it. Static when the system asks for reduced motion; the animation pauses with the tab.
 */
import { geoDistance, geoGraticule10, geoInterpolate, geoOrthographic, geoPath } from "d3-geo";
import { useEffect, useRef } from "react";
import { ORIGIN, loadWorld } from "../lib/geo";
import type { LonLat } from "../lib/geo";
import { useReducedMotion } from "./motion";

export interface GlobeLane {
  to: LonLat;
  line: "air" | "ocean";
}

// A generic set of busy freight destinations (the login page is shown before any tenant data).
export const SAMPLE_LANES: GlobeLane[] = [
  { to: [28.98, 41.01], line: "air" }, // Istanbul
  { to: [55.03, 25.01], line: "ocean" }, // Jebel Ali
  { to: [72.88, 19.08], line: "air" }, // Mumbai
  { to: [121.47, 31.23], line: "ocean" }, // Shanghai
  { to: [4.48, 51.92], line: "ocean" }, // Rotterdam
  { to: [2.35, 48.86], line: "air" }, // Paris
  { to: [-46.33, -23.96], line: "ocean" }, // Santos
  { to: [79.86, 6.93], line: "air" }, // Colombo
  { to: [3.38, 6.52], line: "air" }, // Lagos
  { to: [-99.13, 19.43], line: "air" }, // Mexico City
  { to: [106.85, -6.21], line: "ocean" }, // Jakarta
  { to: [18.42, -33.92], line: "ocean" }, // Cape Town
];

const COLORS = { air: "255, 185, 56", ocean: "60, 200, 234" };

export function Globe({ size = 440, lanes = SAMPLE_LANES }: { size?: number; lanes?: GlobeLane[] }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const reduced = useReducedMotion();

  useEffect(() => {
    const element = canvas.current;
    const context = element?.getContext("2d");
    if (!element || !context) return undefined;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    element.width = size * ratio;
    element.height = size * ratio;
    context.scale(ratio, ratio);

    const radius = size / 2 - 6;
    const projection = geoOrthographic().scale(radius).translate([size / 2, size / 2]).clipAngle(90).precision(0.6);
    const path = geoPath(projection, context);
    const graticule = geoGraticule10();
    const routes = lanes.map((lane, index) => ({
      ...lane,
      interpolate: geoInterpolate(ORIGIN, lane.to),
      geometry: { type: "LineString" as const, coordinates: [ORIGIN, lane.to] },
      speed: 0.00006 + (index % 4) * 0.000015,
      offset: (index * 0.137) % 1,
    }));

    let land: Parameters<typeof path>[0] | null = null;
    let rotation = -20;
    let tilt = -22;
    let velocity = 0.06;
    let dragging: { x: number; y: number; rotation: number; tilt: number } | null = null;
    let frame = 0;
    let cancelled = false;

    const draw = (time: number) => {
      projection.rotate([rotation, tilt]);
      const center: LonLat = [-rotation, -tilt];
      context.clearRect(0, 0, size, size);

      // Atmosphere and sea.
      const glow = context.createRadialGradient(size / 2, size / 2, radius * 0.85, size / 2, size / 2, radius * 1.08);
      glow.addColorStop(0, "rgba(107, 141, 255, 0.35)");
      glow.addColorStop(1, "rgba(107, 141, 255, 0)");
      context.fillStyle = glow;
      context.fillRect(0, 0, size, size);
      const sea = context.createRadialGradient(size * 0.38, size * 0.32, radius * 0.1, size / 2, size / 2, radius);
      sea.addColorStop(0, "#1d3f8f");
      sea.addColorStop(1, "#081733");
      context.beginPath();
      path({ type: "Sphere" });
      context.fillStyle = sea;
      context.fill();

      context.beginPath();
      path(graticule);
      context.strokeStyle = "rgba(255, 255, 255, 0.07)";
      context.lineWidth = 0.6;
      context.stroke();

      if (land) {
        context.beginPath();
        path(land);
        context.fillStyle = "rgba(143, 176, 255, 0.28)";
        context.fill();
        context.strokeStyle = "rgba(199, 214, 255, 0.35)";
        context.lineWidth = 0.5;
        context.stroke();
      }

      for (const route of routes) {
        const rgb = COLORS[route.line];
        context.beginPath();
        path(route.geometry);
        context.strokeStyle = `rgba(${rgb}, 0.55)`;
        context.lineWidth = 1.3;
        context.setLineDash(route.line === "ocean" ? [2, 4] : []);
        context.stroke();
        context.setLineDash([]);

        const end = projection(route.to);
        if (end && geoDistance(route.to, center) < Math.PI / 2) {
          context.beginPath();
          context.arc(end[0], end[1], 2.4, 0, Math.PI * 2);
          context.fillStyle = `rgba(${rgb}, 0.95)`;
          context.fill();
        }

        const progress = reduced ? 0.55 : (time * route.speed + route.offset) % 1;
        const point = route.interpolate(progress) as LonLat;
        if (geoDistance(point, center) < Math.PI / 2) {
          const at = projection(point);
          if (at) {
            const halo = context.createRadialGradient(at[0], at[1], 0, at[0], at[1], 9);
            halo.addColorStop(0, `rgba(${rgb}, 0.9)`);
            halo.addColorStop(1, `rgba(${rgb}, 0)`);
            context.fillStyle = halo;
            context.beginPath();
            context.arc(at[0], at[1], 9, 0, Math.PI * 2);
            context.fill();
            context.beginPath();
            context.arc(at[0], at[1], 2.2, 0, Math.PI * 2);
            context.fillStyle = "#fff";
            context.fill();
          }
        }
      }

      const origin = projection(ORIGIN);
      if (origin && geoDistance(ORIGIN, center) < Math.PI / 2) {
        const pulse = reduced ? 0.5 : (time % 2400) / 2400;
        context.beginPath();
        context.arc(origin[0], origin[1], 4 + pulse * 12, 0, Math.PI * 2);
        context.strokeStyle = `rgba(255, 185, 56, ${1 - pulse})`;
        context.lineWidth = 1.5;
        context.stroke();
        context.beginPath();
        context.arc(origin[0], origin[1], 4, 0, Math.PI * 2);
        context.fillStyle = "#ffb938";
        context.fill();
      }
    };

    const loop = (time: number) => {
      if (cancelled) return;
      if (!dragging) {
        rotation += velocity;
        // Ease a flicked globe back to its cruising speed.
        velocity += (0.06 - velocity) * 0.02;
      }
      draw(time);
      frame = requestAnimationFrame(loop);
    };

    void loadWorld().then((world) => {
      land = world.land;
      if (reduced) draw(0);
    });
    if (reduced) draw(0);
    else frame = requestAnimationFrame(loop);

    const down = (event: PointerEvent) => {
      dragging = { x: event.clientX, y: event.clientY, rotation, tilt };
      element.setPointerCapture(event.pointerId);
    };
    const move = (event: PointerEvent) => {
      if (!dragging) return;
      const previous = rotation;
      rotation = dragging.rotation + (event.clientX - dragging.x) * 0.35;
      tilt = Math.max(-60, Math.min(60, dragging.tilt - (event.clientY - dragging.y) * 0.25));
      velocity = rotation - previous;
      if (reduced) draw(0);
    };
    const up = () => {
      dragging = null;
    };
    element.addEventListener("pointerdown", down);
    element.addEventListener("pointermove", move);
    element.addEventListener("pointerup", up);
    element.addEventListener("pointercancel", up);
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      element.removeEventListener("pointerdown", down);
      element.removeEventListener("pointermove", move);
      element.removeEventListener("pointerup", up);
      element.removeEventListener("pointercancel", up);
    };
  }, [size, lanes, reduced]);

  return (
    <canvas
      ref={canvas}
      className="globe"
      style={{ width: size, height: size, cursor: "grab", touchAction: "none" }}
      role="img"
      aria-label="Globe with air and ocean lanes from North America; drag to turn it"
    />
  );
}
