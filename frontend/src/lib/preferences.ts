/**
 * Server-backed client preferences.
 *
 * Replaces the scattered `localStorage` reads/writes for genuine UI
 * preferences (visualizer config, party-mode exclusions/animation, library
 * sort/view, per-page filters, …).  Preferences now live on the Playarr server
 * (`/api/preferences`) so every browser stays consistent.
 *
 * Design:
 *  - An in-memory cache is the source of truth during a session.
 *  - Every group is also mirrored to `localStorage` under `playarr:pref:<name>`
 *    so reads are instant and survive an offline server.
 *  - Writes update the cache + mirror immediately and send only changed fields
 *    with an optimistic revision.
 *  - `hydratePreferences()` runs once at startup to pull server values into the
 *    cache before the app renders.  Each consumer seeds its own legacy
 *    localStorage value as the fallback, so the first read after upgrade
 *    migrates the old value up to the server.
 *
 * Group names (the JSON shape is owned by each consumer):
 *   artwork · partyExclusions · partyAnimation · library ·
 *   queue · review · archive · panels
 */
import { prefApi } from "@/lib/api";

const MIRROR_PREFIX = "playarr:pref:";

const cache: Record<string, unknown> = {};
const revisions: Record<string, number> = {};

function mirrorKey(name: string): string {
  return MIRROR_PREFIX + name;
}

/** Read a preference group synchronously: cache → local mirror → fallback. */
export function getPref<T>(name: string, fallback: T): T {
  if (Object.prototype.hasOwnProperty.call(cache, name)) {
    return cache[name] as T;
  }
  try {
    const raw = localStorage.getItem(mirrorKey(name));
    if (raw != null) {
      const value = JSON.parse(raw) as T;
      cache[name] = value;
      return value;
    }
  } catch {
    /* ignore */
  }
  return fallback;
}

// ── Debounced server writes ────────────────────────────────
const pending: Record<string, Record<string, unknown>> = {};
let flushTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleFlush() {
  if (flushTimer) return;
  flushTimer = setTimeout(flush, 400);
}

async function flush() {
  flushTimer = null;
  const batch = { ...pending };
  for (const k of Object.keys(pending)) delete pending[k];
  await Promise.all(Object.entries(batch).map(async ([name, patch]) => {
    try {
      const result = await prefApi.patch(name, patch, revisions[name] ?? 0);
      revisions[name] = result.revision;
      const newerPatch = pending[name];
      adopt(name, newerPatch ? { ...result.value, ...newerPatch } : result.value);
    } catch {
      // A conflicting device may have advanced the revision. Rebase this
      // field-level patch once; unrelated server fields remain intact.
      try {
        const state = await prefApi.getState();
        revisions[name] = state.revisions[name] ?? 0;
        const result = await prefApi.patch(name, patch, revisions[name]);
        revisions[name] = result.revision;
        const newerPatch = pending[name];
        adopt(name, newerPatch ? { ...result.value, ...newerPatch } : result.value);
      } catch {
        // Offline: the local mirror is only a startup cache. A later user
        // change will retry; it is never uploaded as a whole-group replace.
      }
    }
  }));
}

/** Write a preference group: updates cache + mirror now, server (debounced). */
export function setPref<T>(name: string, value: T): void {
  const prior = (cache[name] ?? {}) as Record<string, unknown>;
  const next = value as Record<string, unknown>;
  const patch = Object.fromEntries(
    Object.entries(next).filter(([key, fieldValue]) => !Object.is(prior[key], fieldValue)),
  );
  cache[name] = value;
  try {
    localStorage.setItem(mirrorKey(name), JSON.stringify(value));
  } catch {
    /* ignore */
  }
  if (Object.keys(patch).length === 0) return;
  pending[name] = { ...(pending[name] ?? {}), ...patch };
  scheduleFlush();
}

/** Adopt a server value into the cache + local mirror without re-uploading it. */
function adopt(name: string, value: unknown) {
  cache[name] = value;
  try {
    localStorage.setItem(mirrorKey(name), JSON.stringify(value));
  } catch {
    /* ignore */
  }
}

type LegacyMigration = {
  group: string;
  keys: string[];
  read: () => Record<string, unknown>;
};

const legacyMigrations: LegacyMigration[] = [
  {
    group: "artwork", keys: ["playarr-artwork-settings"],
    read: () => JSON.parse(localStorage.getItem("playarr-artwork-settings") || "{}"),
  },
  {
    group: "partyExclusions", keys: ["playarr:partyMode:exclusions"],
    read: () => JSON.parse(localStorage.getItem("playarr:partyMode:exclusions") || "{}"),
  },
  {
    group: "partyAnimation", keys: ["playarr:partyMode:animation"],
    read: () => JSON.parse(localStorage.getItem("playarr:partyMode:animation") || "{}"),
  },
  {
    group: "library",
    keys: ["playarr:library:view", "playarr:library:sort", "playarr:library:dir", "playarr:library:pageSize"],
    read: () => ({
      ...(localStorage.getItem("playarr:library:view") ? { view: localStorage.getItem("playarr:library:view") } : {}),
      ...(localStorage.getItem("playarr:library:sort") ? { sort: localStorage.getItem("playarr:library:sort") } : {}),
      ...(localStorage.getItem("playarr:library:dir") ? { dir: localStorage.getItem("playarr:library:dir") } : {}),
      ...(localStorage.getItem("playarr:library:pageSize") ? { pageSize: Number(localStorage.getItem("playarr:library:pageSize")) } : {}),
    }),
  },
  {
    group: "panels", keys: ["thumbnails_expanded", "track_history_expanded"],
    read: () => ({
      thumbnailsExpanded: localStorage.getItem("thumbnails_expanded") === "true",
      trackHistoryExpanded: localStorage.getItem("track_history_expanded") === "true",
    }),
  },
  {
    group: "archive", keys: ["archive_page_size"],
    read: () => ({ pageSize: Number(localStorage.getItem("archive_page_size")) || 25 }),
  },
  {
    group: "review", keys: ["review_category_filter", "review_page_size"],
    read: () => ({
      categoryFilter: localStorage.getItem("review_category_filter") || "all",
      pageSize: Number(localStorage.getItem("review_page_size")) || 25,
    }),
  },
];

export async function migrateLegacyPreferences(
  serverValues: Record<string, unknown>,
  serverRevisions: Record<string, number>,
): Promise<void> {
  for (const migration of legacyMigrations) {
    const marker = `playarr:pref:migrated:v1:${migration.group}`;
    if (localStorage.getItem(marker) === "done") continue;
    const hasLegacy = migration.keys.some(key => localStorage.getItem(key) !== null);
    try {
      if (serverValues[migration.group] === undefined && hasLegacy) {
        const result = await prefApi.patch(migration.group, migration.read(), 0);
        serverValues[migration.group] = result.value;
        serverRevisions[migration.group] = result.revision;
      }
      if (serverValues[migration.group] !== undefined || !hasLegacy) {
        migration.keys.forEach(key => localStorage.removeItem(key));
        localStorage.setItem(marker, "done");
      }
    } catch {
      // Do not delete or mark a migration until persistence succeeds.
    }
  }
}

/**
 * Pull all server preferences into the cache.  Call once at startup BEFORE
 * rendering the app (and before importing any store that reads getPref).
 * Falls back silently to local mirrors / defaults if the server is unreachable.
 */
export async function hydratePreferences(): Promise<void> {
  try {
    const state = await prefApi.getState();
    await migrateLegacyPreferences(state.values, state.revisions);
    for (const [name, value] of Object.entries(state.values)) {
      if (value !== null && value !== undefined) adopt(name, value);
      revisions[name] = state.revisions[name] ?? 0;
    }
  } catch {
    /* offline or first run — local mirrors / consumer fallbacks cover it */
  }
}
