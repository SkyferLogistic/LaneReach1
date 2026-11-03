/**
 * Geography for the lane map and the globe: world shapes (loaded on demand), country centroids by
 * ISO alpha-2 code, and the scales that turn outreach numbers into arc widths and colours.
 */
import { geoCentroid } from "d3-geo";
import type { Feature, FeatureCollection, Geometry, MultiPolygon, Polygon } from "geojson";
import countryCodes from "i18n-iso-countries";

export type LonLat = [number, number];

/** Where the lanes start: the USA/Canada sales desks (Toronto area). */
export const ORIGIN: LonLat = [-79.6, 43.7];

// Countries too small for the 1:110m map still need a point for their lane.
const SMALL_COUNTRIES: Record<string, LonLat> = {
  BH: [50.55, 26.07],
  HK: [114.17, 22.32],
  SG: [103.82, 1.35],
  MT: [14.43, 35.9],
  LU: [6.13, 49.61],
  MO: [113.54, 22.2],
  MV: [73.22, 3.2],
  MU: [57.55, -20.35],
  BB: [-59.54, 13.19],
};

export interface World {
  countries: Feature<Polygon | MultiPolygon, { name?: string }>[];
  land: FeatureCollection<Geometry> | Feature<Geometry>;
  byCode: Map<string, Feature<Polygon | MultiPolygon, { name?: string }>>;
}

let worldPromise: Promise<World> | null = null;

/** The 1:110m world, parsed once (the JSON is split into its own chunk). */
export function loadWorld(): Promise<World> {
  worldPromise ??= Promise.all([import("topojson-client"), import("world-atlas/countries-110m.json")]).then(
    ([topojson, atlas]) => {
      // The JSON module's types are too loose for topojson's Topology type.
      const topology = (atlas as { default?: unknown }).default ?? atlas;
      const typed = topology as Parameters<typeof topojson.feature>[0] & {
        objects: { countries: Parameters<typeof topojson.feature>[1]; land: Parameters<typeof topojson.feature>[1] };
      };
      const collection = topojson.feature(typed, typed.objects.countries) as unknown as FeatureCollection<
        Polygon | MultiPolygon,
        { name?: string }
      >;
      const land = topojson.feature(typed, typed.objects.land) as unknown as FeatureCollection<Geometry>;
      const byCode = new Map<string, Feature<Polygon | MultiPolygon, { name?: string }>>();
      for (const feature of collection.features) {
        const code = countryCodes.numericToAlpha2(String(feature.id ?? "").padStart(3, "0"));
        if (code) byCode.set(code, feature);
      }
      return { countries: collection.features, land, byCode };
    },
  );
  return worldPromise;
}

/** A point for a country's lane end (its centroid, or a fixed point for small countries). */
export function countryPoint(world: World, code: string): LonLat | null {
  const upper = code.toUpperCase();
  const small = SMALL_COUNTRIES[upper];
  if (small) return small;
  const feature = world.byCode.get(upper);
  if (!feature) return null;
  const [lon, lat] = geoCentroid(feature);
  return Number.isFinite(lon) && Number.isFinite(lat) ? [lon, lat] : null;
}

/** Arc stroke width for a country's emails (1.2 px when idle, up to 6 px for the busiest lane). */
export function laneWidth(sent: number, busiest: number): number {
  if (sent <= 0 || busiest <= 0) return 1.2;
  return Math.round((1.6 + 4.4 * Math.sqrt(sent / busiest)) * 10) / 10;
}

/** Arc colour by reply rate: slate (no replies) → teal → green (≥ 15 %). */
export function laneColor(replyRate: number, sent: number): string {
  if (sent <= 0) return "var(--border-strong)";
  if (replyRate <= 0) return "#7f8ca5";
  if (replyRate < 0.05) return "#3cc8ea";
  if (replyRate < 0.15) return "#19c3b1";
  return "#3fd49b";
}
