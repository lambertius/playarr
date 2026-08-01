import { describe, expect, it } from "vitest";

import aiPanel from "../components/AIPanel.tsx?raw";
import actionsPanel from "../components/ActionsPanel.tsx?raw";
import candidateCard from "../components/CandidateCard.tsx?raw";
import metadataManager from "../pages/MetadataManagerPage.tsx?raw";
import reviewQueue from "../pages/ReviewQueuePage.tsx?raw";
import videoEditor from "../pages/VideoEditorPage.tsx?raw";


describe("UI-005 action language", () => {
  it("uses Save for committing drafts and Rename for path changes", () => {
    const permanentWorkflows = [
      aiPanel, actionsPanel, candidateCard, metadataManager, reviewQueue, videoEditor,
    ];
    for (const source of permanentWorkflows) {
      expect(source).not.toMatch(/^\s*Apply(?:\s+(?:Selected|All|High)|\s*<|\s*\()/m);
    }
    expect(aiPanel).toContain("Save all changes");
    expect(videoEditor).toContain("Save edits");
    expect(reviewQueue).toContain("/> Rename");
  });
});
