import { beforeEach, describe, expect, it, vi } from "vitest";

const { getPref, setPref } = vi.hoisted(() => ({ getPref: vi.fn(), setPref: vi.fn() }));
vi.mock("@/lib/preferences", () => ({ getPref, setPref }));

import { getLibraryPrefs, PAGE_SIZE_OPTIONS, patchLibraryPrefs } from "./libraryPrefs";

describe("library page-size preferences", () => {
  beforeEach(() => {
    localStorage.clear();
    getPref.mockReset();
    setPref.mockReset();
    getPref.mockImplementation((_name: string, fallback: unknown) => fallback);
  });

  it("keeps a numeric default while offering All", () => {
    expect(getLibraryPrefs().pageSize).toBe(48);
    expect(PAGE_SIZE_OPTIONS).toContain(0);
  });

  it("preserves All as a saved preference", () => {
    getPref.mockReturnValue({ pageSize: 0 });
    expect(getLibraryPrefs().pageSize).toBe(0);

    patchLibraryPrefs({ pageSize: 0 });
    expect(setPref).toHaveBeenCalledWith("library", expect.objectContaining({ pageSize: 0 }));
  });
});
