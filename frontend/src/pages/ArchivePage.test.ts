import { describe, expect, it } from "vitest";

import { ARCHIVE_REASON_TABS } from "@/lib/archiveTaxonomy";
import { buildArchiveFocusHref } from "@/components/ActionsPanel";

describe("Archive navigation taxonomy", () => {
  it("uses one Queue-style reason tab row without duplicate filter controls", () => {
    expect(ARCHIVE_REASON_TABS).toEqual([
      "all", "redownload", "edit", "trim", "crop", "both", "restore_conflict", "orphaned",
    ]);
    expect(new Set(ARCHIVE_REASON_TABS).size).toBe(ARCHIVE_REASON_TABS.length);
  });

  it("builds a shareable archive focus URL from the linked video", () => {
    expect(buildArchiveFocusHref(812, "Bon Iver", "Holocene")).toBe(
      "/archive?focus_video_id=812&search=Holocene",
    );
    expect(buildArchiveFocusHref(42, "Artist only", null)).toBe(
      "/archive?focus_video_id=42&search=Artist+only",
    );
  });
});
