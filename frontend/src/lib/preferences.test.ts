import { beforeEach, describe, expect, it, vi } from "vitest";

const { patch } = vi.hoisted(() => ({ patch: vi.fn() }));
vi.mock("@/lib/api", () => ({ prefApi: { patch } }));

import { migrateLegacyPreferences } from "./preferences";

describe("legacy preference migration", () => {
  beforeEach(() => { localStorage.clear(); patch.mockReset(); });

  it("persists once, deletes old keys, and cannot overwrite later server state", async () => {
    localStorage.setItem("playarr:library:view", "list");
    patch.mockResolvedValue({ value: { view: "list", sort: "artist", dir: "asc", pageSize: 50 }, revision: 1 });
    const values: Record<string, unknown> = {};
    const revisions: Record<string, number> = {};

    await migrateLegacyPreferences(values, revisions);
    expect(patch).toHaveBeenCalledWith("library", { view: "list" }, 0);
    expect(localStorage.getItem("playarr:library:view")).toBeNull();
    expect(localStorage.getItem("playarr:pref:migrated:v1:library")).toBe("done");

    patch.mockClear();
    values.library = { view: "grid" };
    await migrateLegacyPreferences(values, revisions);
    expect(patch).not.toHaveBeenCalled();
    expect(values.library).toEqual({ view: "grid" });
  });

  it("keeps legacy data when server persistence fails", async () => {
    localStorage.setItem("review_page_size", "50");
    patch.mockRejectedValue(new Error("offline"));
    await migrateLegacyPreferences({}, {});
    expect(localStorage.getItem("review_page_size")).toBe("50");
    expect(localStorage.getItem("playarr:pref:migrated:v1:review")).toBeNull();
  });
});
