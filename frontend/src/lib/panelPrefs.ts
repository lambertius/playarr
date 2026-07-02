/**
 * Detail-page panel expand toggles, stored server-side via the preferences
 * layer.  Two components (ThumbnailsPanel, TrackHistory) share the one "panels"
 * group, so writes must read-modify-write to avoid clobbering each other.
 */
import { getPref, setPref } from "@/lib/preferences";

export interface PanelPrefs {
  thumbnailsExpanded: boolean;
  trackHistoryExpanded: boolean;
}

const GROUP = "panels";

// Legacy per-key localStorage values (stored as the strings "true"/"false") —
// read as the migration fallback.
const K_THUMBNAILS = "thumbnails_expanded";
const K_TRACK_HISTORY = "track_history_expanded";

function legacy(): PanelPrefs {
  let thumbnailsExpanded = false;
  let trackHistoryExpanded = false;
  try { thumbnailsExpanded = localStorage.getItem(K_THUMBNAILS) === "true"; } catch { /* ignore */ }
  try { trackHistoryExpanded = localStorage.getItem(K_TRACK_HISTORY) === "true"; } catch { /* ignore */ }
  return { thumbnailsExpanded, trackHistoryExpanded };
}

export function getPanelPrefs(): PanelPrefs {
  const fallback = legacy();
  return { ...fallback, ...getPref<Partial<PanelPrefs>>(GROUP, fallback) };
}

export function patchPanelPrefs(patch: Partial<PanelPrefs>): void {
  setPref(GROUP, { ...getPanelPrefs(), ...patch });
}
