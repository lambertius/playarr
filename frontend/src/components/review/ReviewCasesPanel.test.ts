import { describe, expect, it } from "vitest";

import reviewCases from "./ReviewCasesPanel.tsx?raw";

describe("Review Queue comparison workflow", () => {
  it("uses the consolidated review taxonomy", () => {
    for (const label of ["Duplicates", "Versions", "Enrichment", "Volume", "Untracked"]) {
      expect(reviewCases).toContain(`label: "${label}"`);
    }
  });

  it("keeps media decisions staged until save", () => {
    expect(reviewCases).toContain("<video");
    expect(reviewCases).toContain("Why flagged:");
    expect(reviewCases).toContain("Added");
    expect(reviewCases).toContain("Reclassify");
    expect(reviewCases).toContain("Delete");
    expect(reviewCases).toContain("Undo changes");
    expect(reviewCases).toContain("Save changes");
    expect(reviewCases).not.toContain("window.prompt");
    expect(reviewCases).not.toContain("window.confirm");
  });
});
