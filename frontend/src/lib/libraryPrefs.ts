/**
 * Library browse preferences (view mode, sort, direction, page size), stored
 * server-side via the preferences layer so they're consistent across browsers
 * and other Playarr clients inherit the default sort.
 */
import { getPref, setPref } from "@/lib/preferences";

export const PAGE_SIZE_OPTIONS = [0, 12, 24, 48, 96, 192];

export interface LibraryPrefs {
  view: string;
  sort: string;
  dir: "asc" | "desc";
  pageSize: number;
}

const GROUP = "library";

// Legacy per-key localStorage values — read as the migration fallback.
const K_VIEW = "playarr:library:view";
const K_SORT = "playarr:library:sort";
const K_DIR = "playarr:library:dir";
const K_PAGE_SIZE = "playarr:library:pageSize";

function legacy(): LibraryPrefs {
  let view = "grid";
  let sort = "artist";
  let dir: "asc" | "desc" = "asc";
  let pageSize = 48;
  try { view = localStorage.getItem(K_VIEW) || view; } catch { /* ignore */ }
  try { sort = localStorage.getItem(K_SORT) || sort; } catch { /* ignore */ }
  try { const d = localStorage.getItem(K_DIR); if (d === "asc" || d === "desc") dir = d; } catch { /* ignore */ }
  try {
    const stored = localStorage.getItem(K_PAGE_SIZE);
    const n = Number(stored);
    if (stored !== null && PAGE_SIZE_OPTIONS.includes(n)) pageSize = n;
  } catch { /* ignore */ }
  return { view, sort, dir, pageSize };
}

export function getLibraryPrefs(): LibraryPrefs {
  const fallback = legacy();
  const preferences = { ...fallback, ...getPref<Partial<LibraryPrefs>>(GROUP, fallback) };
  return {
    ...preferences,
    pageSize: PAGE_SIZE_OPTIONS.includes(preferences.pageSize) ? preferences.pageSize : fallback.pageSize,
  };
}

export function patchLibraryPrefs(patch: Partial<LibraryPrefs>): void {
  setPref(GROUP, { ...getLibraryPrefs(), ...patch });
}
