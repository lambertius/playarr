import type { EnrichmentStatus } from "@/types";

export type EnrichmentLifecycle = EnrichmentStatus["state"];

export interface EnrichmentStatusDefinition {
  value: EnrichmentLifecycle;
  label: string;
  description: string;
  className: string;
}

/**
 * The canonical AI lifecycle vocabulary used by badges, filters and help text.
 * Keep this ordered from least to most actionable/complete for filter menus.
 */
export const ENRICHMENT_STATUSES: readonly EnrichmentStatusDefinition[] = [
  {
    value: "not_requested",
    label: "Not run",
    description: "No AI task was requested for this video. AI processing is optional, so this is not a review issue.",
    className: "bg-zinc-500/15 text-zinc-400",
  },
  {
    value: "queued",
    label: "Queued",
    description: "At least one AI task is waiting to start; use this to check work that has not begun yet.",
    className: "bg-blue-500/15 text-blue-400",
  },
  {
    value: "running",
    label: "Running",
    description: "At least one AI task is currently running; use this to monitor active enrichment.",
    className: "bg-cyan-500/15 text-cyan-400",
  },
  {
    value: "partial",
    label: "Incomplete",
    description: "One or more AI tasks were requested but did not finish. Unless already reviewed or dismissed, the item should also appear in Review.",
    className: "bg-yellow-500/15 text-yellow-400",
  },
  {
    value: "complete",
    label: "Complete",
    description: "Every AI task requested for this video completed. Scene analysis is only required when it was selected.",
    className: "bg-emerald-500/15 text-emerald-400",
  },
  {
    value: "failed",
    label: "Failed",
    description: "An AI task failed before any AI work completed; filter here to inspect or retry failed items.",
    className: "bg-red-500/15 text-red-400",
  },
  {
    value: "stale",
    label: "Needs refresh",
    description: "AI results exist, but later metadata changes made them stale; filter here to refresh those results.",
    className: "bg-orange-500/15 text-orange-400",
  },
] as const;

export const ENRICHMENT_STATUS_BY_VALUE = Object.fromEntries(
  ENRICHMENT_STATUSES.map((status) => [status.value, status]),
) as Record<EnrichmentLifecycle, EnrichmentStatusDefinition>;

/** Translate legacy API values while older sidecars/clients are migrated. */
export function normaliseEnrichmentStatus(status?: string | null): EnrichmentLifecycle | undefined {
  if (!status) return undefined;
  if (status === "enriched") return "complete";
  if (status === "pending") return "not_requested";
  return status in ENRICHMENT_STATUS_BY_VALUE ? status as EnrichmentLifecycle : undefined;
}
