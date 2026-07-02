/**
 * Server-backed client preferences.
 *
 * Replaces the scattered `localStorage` reads/writes for genuine UI
 * preferences (visualizer config, party-mode exclusions/animation, library
 * sort/view, per-page filters, …).  Preferences now live on the Playarr server
 * (`/api/preferences`) so every browser stays consistent and the Kodi add-on
 * can read the shared ones.
 *
 * Design:
 *  - An in-memory cache is the source of truth during a session.
 *  - Every group is also mirrored to `localStorage` under `playarr:pref:<name>`
 *    so reads are instant and survive an offline server.
 *  - Writes update the cache + mirror immediately (snappy UI) and PUT to the
 *    server debounced.
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
const pending: Record<string, unknown> = {};
let flushTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleFlush() {
  if (flushTimer) return;
  flushTimer = setTimeout(flush, 400);
}

async function flush() {
  flushTimer = null;
  const batch = { ...pending };
  for (const k of Object.keys(pending)) delete pending[k];
  await Promise.all(
    Object.entries(batch).map(([name, value]) =>
      prefApi.set(name, value).catch(() => {
        /* best-effort; local mirror keeps the value */
      }),
    ),
  );
}

/** Write a preference group: updates cache + mirror now, server (debounced). */
export function setPref<T>(name: string, value: T): void {
  cache[name] = value;
  try {
    localStorage.setItem(mirrorKey(name), JSON.stringify(value));
  } catch {
    /* ignore */
  }
  pending[name] = value;
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

/**
 * Pull all server preferences into the cache.  Call once at startup BEFORE
 * rendering the app (and before importing any store that reads getPref).
 * Falls back silently to local mirrors / defaults if the server is unreachable.
 */
export async function hydratePreferences(): Promise<void> {
  try {
    const server = await prefApi.getAll();
    for (const [name, value] of Object.entries(server)) {
      if (value !== null && value !== undefined) adopt(name, value);
    }
  } catch {
    /* offline or first run — local mirrors / consumer fallbacks cover it */
  }
}
