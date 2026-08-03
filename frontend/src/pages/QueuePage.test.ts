import { describe, expect, it } from "vitest";

import { CATEGORY_TABS, STATUS_TABS } from "@/lib/queueTaxonomy";

describe("Queue navigation taxonomy", () => {
  it("uses the requested top-level status tabs without a duplicate history layer", () => {
    expect(STATUS_TABS.map((tab) => tab.label)).toEqual([
      "Active", "Complete", "Failed", "Cancelled", "Skipped",
    ]);
  });

  it("uses one source subtab row", () => {
    expect(CATEGORY_TABS.map((tab) => tab.label)).toEqual([
      "All", "Downloads", "Imports", "Video Editor", "Scraper",
    ]);
  });
});
