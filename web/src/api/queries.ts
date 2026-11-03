/** Shared queries (lookups used across pages) and query-key helpers. */
import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "./client";
import type { Schemas } from "./client";
import { SERVICE_LINES } from "../lib/freight";

export const keys = {
  me: ["me"] as const,
  nodes: ["nodes"] as const,
  conditionFields: ["condition-fields"] as const,
  offices: ["offices"] as const,
  senders: ["senders"] as const,
  templates: ["templates"] as const,
  workflows: ["workflows"] as const,
  workflow: (id: string) => ["workflows", id] as const,
  enrollments: ["enrollments"] as const,
  contacts: ["contacts"] as const,
  companies: ["companies"] as const,
  reviews: ["reviews"] as const,
  leads: ["leads"] as const,
  outbox: ["outbox"] as const,
  events: ["events"] as const,
  notifications: ["notifications"] as const,
  settings: ["settings"] as const,
  policies: ["reply-policies"] as const,
  scripts: ["scripts"] as const,
  countries: ["countries"] as const,
  assets: ["assets"] as const,
  branding: ["branding"] as const,
  library: ["library"] as const,
  dashboard: ["dashboard"] as const,
  crm: ["crm"] as const,
  pipelines: ["crm", "pipelines"] as const,
  deals: ["crm", "deals"] as const,
  tasks: ["crm", "tasks"] as const,
  notes: ["crm", "notes"] as const,
  inbox: ["inbox"] as const,
};

export function useNodeCatalog() {
  return useQuery({
    queryKey: keys.nodes,
    queryFn: () => unwrap(api.GET("/api/v1/automation/nodes")),
    staleTime: Infinity,
  });
}

export function useConditionFields() {
  return useQuery({
    queryKey: keys.conditionFields,
    queryFn: () => unwrap(api.GET("/api/v1/automation/condition-fields")),
    staleTime: Infinity,
  });
}

export function useOffices() {
  return useQuery({
    queryKey: keys.offices,
    queryFn: () => unwrap(api.GET("/api/v1/offices")),
  });
}

export function useSenders() {
  return useQuery({
    queryKey: keys.senders,
    queryFn: () => unwrap(api.GET("/api/v1/senders", { params: { query: {} } })),
  });
}

export function useTemplates() {
  return useQuery({
    queryKey: keys.templates,
    queryFn: () => unwrap(api.GET("/api/v1/templates", { params: { query: {} } })),
  });
}

export function usePublishedWorkflows() {
  return useQuery({
    queryKey: [...keys.workflows, "published"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/automation/workflows", {
          params: { query: { status: "published", limit: 100 } },
        }),
      ),
  });
}

export function useSendingSettings() {
  return useQuery({
    queryKey: keys.settings,
    queryFn: () => unwrap(api.GET("/api/v1/settings/sending")),
    refetchInterval: 60_000,
  });
}

export function usePendingReviewCount() {
  return useQuery({
    queryKey: [...keys.reviews, "pending-count"],
    queryFn: async () => {
      const page = await unwrap(
        api.GET("/api/v1/ai/reviews", { params: { query: { status: "pending", limit: 100 } } }),
      );
      return page.items.length;
    },
    refetchInterval: 30_000,
  });
}

export function useScriptMatrix() {
  return useQuery({
    queryKey: keys.scripts,
    queryFn: () => unwrap(api.GET("/api/v1/scripts")),
  });
}

export function useCountries() {
  return useQuery({
    queryKey: keys.countries,
    queryFn: () => unwrap(api.GET("/api/v1/countries")),
  });
}

export function useBranding() {
  return useQuery({
    queryKey: keys.branding,
    queryFn: () => unwrap(api.GET("/api/v1/settings/branding")),
  });
}

export function useLibrarySettings() {
  return useQuery({
    queryKey: keys.library,
    queryFn: () => unwrap(api.GET("/api/v1/settings/library")),
  });
}

export function useDashboardStats(days: 7 | 30 | 90) {
  return useQuery({
    queryKey: [...keys.dashboard, days],
    queryFn: () => unwrap(api.GET("/api/v1/dashboard/stats", { params: { query: { days } } })),
    refetchInterval: 60_000,
    placeholderData: (previous) => previous,
  });
}

export function useInsights(days: 7 | 30 | 90) {
  return useQuery({
    queryKey: [...keys.dashboard, "insights", days],
    queryFn: () => unwrap(api.GET("/api/v1/dashboard/insights", { params: { query: { days } } })),
    refetchInterval: 120_000,
    placeholderData: (previous) => previous,
  });
}

export function useActivity(limit = 20) {
  return useQuery({
    queryKey: [...keys.dashboard, "activity", limit],
    queryFn: () => unwrap(api.GET("/api/v1/dashboard/activity", { params: { query: { limit } } })),
    refetchInterval: 15_000,
  });
}

export function useUnreadNotifications() {
  return useQuery({
    queryKey: [...keys.notifications, "unread-popover"],
    queryFn: () => unwrap(api.GET("/api/v1/notifications", { params: { query: { unread: true, limit: 8 } } })),
    refetchInterval: 30_000,
  });
}

export function usePipelines() {
  return useQuery({
    queryKey: keys.pipelines,
    queryFn: () => unwrap(api.GET("/api/v1/crm/pipelines")),
    staleTime: 60_000,
  });
}

export function useDeals(query: { pipeline_id?: string; contact_id?: string; status?: "open" | "won" | "lost"; search?: string; service_line?: Schemas["ServiceLine"]; owner_email?: string }) {
  return useQuery({
    queryKey: [...keys.deals, query],
    queryFn: () => unwrap(api.GET("/api/v1/crm/deals", { params: { query } })),
    enabled: Boolean(query.pipeline_id || query.contact_id),
  });
}

export function useTasks(query: { mine?: boolean; done?: boolean | null; contact_id?: string; deal_id?: string; due_within_days?: number }) {
  return useQuery({
    queryKey: [...keys.tasks, query],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/crm/tasks", {
          params: { query: { ...query, done: query.done === null ? undefined : query.done } as never },
        }),
      ),
  });
}

export function useCrmSummary(days: number) {
  return useQuery({
    queryKey: [...keys.crm, "summary", days],
    queryFn: () => unwrap(api.GET("/api/v1/crm/summary", { params: { query: { days } } })),
    refetchInterval: 60_000,
  });
}

export function useInboxCounts() {
  return useQuery({
    queryKey: [...keys.inbox, "counts"],
    queryFn: () => unwrap(api.GET("/api/v1/inbox/counts", { params: { query: {} } })),
    refetchInterval: 30_000,
  });
}

export function useMailboxHealth() {
  return useQuery({
    queryKey: [...keys.senders, "health"],
    queryFn: () => unwrap(api.GET("/api/v1/senders/health")),
    refetchInterval: 120_000,
  });
}

/** Service lines the company sells (Settings → Services); every line until an admin chooses. */
export function useServiceSettings() {
  return useQuery({
    queryKey: ["settings", "services"],
    queryFn: () => unwrap(api.GET("/api/v1/settings/services")),
    staleTime: 5 * 60_000,
  });
}

/** The offered lines for menus and filters (all lines while loading). */
export function useOfferedLines(): string[] {
  const settings = useServiceSettings();
  return settings.data?.service_lines ?? [...SERVICE_LINES];
}
