import { describe, expect, it } from "vitest";

import {
  ENRICHMENT_STATUSES,
  ENRICHMENT_STATUS_BY_VALUE,
  normaliseEnrichmentStatus,
} from "./enrichmentStatus";

describe("AI enrichment status vocabulary", () => {
  it("defines every lifecycle filter with user-facing guidance", () => {
    expect(ENRICHMENT_STATUSES.map((status) => status.value)).toEqual([
      "not_requested", "queued", "running", "partial", "complete", "failed", "stale",
    ]);
    for (const status of ENRICHMENT_STATUSES) {
      expect(ENRICHMENT_STATUS_BY_VALUE[status.value].description.length).toBeGreaterThan(20);
    }
  });

  it("maps legacy API values to the canonical lifecycle", () => {
    expect(normaliseEnrichmentStatus("pending")).toBe("not_requested");
    expect(normaliseEnrichmentStatus("enriched")).toBe("complete");
    expect(normaliseEnrichmentStatus("partial")).toBe("partial");
    expect(normaliseEnrichmentStatus("unknown")).toBeUndefined();
  });
});
