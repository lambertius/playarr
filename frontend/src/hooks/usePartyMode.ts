import { useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { libraryApi } from "@/lib/api";
import { usePlaybackStore } from "@/stores/playbackStore";
import { useFireworksStore } from "@/stores/fireworksStore";
import { useToast } from "@/components/Toast";
import { getPref, setPref } from "@/lib/preferences";
import type { PartyModeParams, PartyModeExclusions } from "@/types";

// Server-stored preference groups (shared with the Kodi add-on, which inherits
// the exclusions server-side).  LEGACY_* keys seed the one-time migration.
const EXCLUSIONS_GROUP = "partyExclusions";
const ANIMATION_GROUP = "partyAnimation";
const ERA_GROUP = "partyEra";
const PLAYLIST_GROUP = "partyPlaylist";
const LEGACY_EXCLUSIONS_KEY = "playarr:partyMode:exclusions";
const LEGACY_ANIMATION_KEY = "playarr:partyMode:animation";

export const DEFAULT_EXCLUSIONS: PartyModeExclusions = {
  version_types: [],
  artists: [],
  genres: [],
  albums: [],
  min_song_rating: null,
  min_video_rating: null,
  exclude_adult: true,
};

export interface PartyModeAnimationSettings {
  enabled: boolean;
  duration: number; // seconds, 5-15
}

export const DEFAULT_ANIMATION: PartyModeAnimationSettings = {
  enabled: true,
  duration: 8,
};

function legacyExclusions(): PartyModeExclusions {
  try {
    const raw = localStorage.getItem(LEGACY_EXCLUSIONS_KEY);
    if (raw) return { ...DEFAULT_EXCLUSIONS, ...JSON.parse(raw) };
  } catch { /* ignore */ }
  return { ...DEFAULT_EXCLUSIONS };
}

export function loadExclusions(): PartyModeExclusions {
  return { ...DEFAULT_EXCLUSIONS, ...getPref<Partial<PartyModeExclusions>>(EXCLUSIONS_GROUP, legacyExclusions()) };
}

export function saveExclusions(exclusions: PartyModeExclusions) {
  setPref(EXCLUSIONS_GROUP, exclusions);
}

function legacyAnimation(): PartyModeAnimationSettings {
  try {
    const raw = localStorage.getItem(LEGACY_ANIMATION_KEY);
    if (raw) return { ...DEFAULT_ANIMATION, ...JSON.parse(raw) };
  } catch { /* ignore */ }
  return { ...DEFAULT_ANIMATION };
}

export function loadAnimationSettings(): PartyModeAnimationSettings {
  return { ...DEFAULT_ANIMATION, ...getPref<Partial<PartyModeAnimationSettings>>(ANIMATION_GROUP, legacyAnimation()) };
}

export function saveAnimationSettings(settings: PartyModeAnimationSettings) {
  setPref(ANIMATION_GROUP, settings);
}

// "Party like it's…" — cap playback at a target year and weight the queue
// toward that era.  Stored server-side so the Kodi add-on inherits it too.
export interface PartyModeEraSettings {
  enabled: boolean;
  year: number;
}

export const DEFAULT_ERA: PartyModeEraSettings = {
  enabled: false,
  year: new Date().getFullYear(),
};

export function loadEraSettings(): PartyModeEraSettings {
  return { ...DEFAULT_ERA, ...getPref<Partial<PartyModeEraSettings>>(ERA_GROUP, DEFAULT_ERA) };
}

export function saveEraSettings(settings: PartyModeEraSettings) {
  setPref(ERA_GROUP, settings);
}

// Optional "use this playlist for Party Mode" choice.  When a playlist is
// selected, Party Mode plays it (shuffled) instead of auto-generating a queue
// from the current filter.  null → keep the default filter-based behaviour.
export interface PartyPlaylistSettings {
  playlistId: number | null;
}

export const DEFAULT_PLAYLIST: PartyPlaylistSettings = { playlistId: null };

export function loadPartyPlaylist(): PartyPlaylistSettings {
  return { ...DEFAULT_PLAYLIST, ...getPref<Partial<PartyPlaylistSettings>>(PLAYLIST_GROUP, DEFAULT_PLAYLIST) };
}

export function savePartyPlaylist(settings: PartyPlaylistSettings) {
  setPref(PLAYLIST_GROUP, settings);
}

interface LaunchOptions {
  /** Navigate to /now-playing + fireworks + toast (default). TV mode sets this
   *  false to populate the queue in place without leaving the current page. */
  navigate?: boolean;
  /** Layout to enter on launch. "theater" = artwork grid + video (default);
   *  "video" = full-screen video only. The first-access prompt on /tv and /cast
   *  passes the user's theatre/fullscreen choice here. */
  fullscreenMode?: "theater" | "video";
  /** Skip the saved "Party like it's…" era preference. The first-access prompt
   *  is authoritative about the year, so when it chooses the whole library
   *  (no party_year) the saved era must not leak back in. */
  ignoreSavedEra?: boolean;
  /** Explicit start-panel playlist. null explicitly means no playlist. */
  playlistId?: number | null;
}

interface UsePartyModeResult {
  launch: (filterParams?: Partial<PartyModeParams>, opts?: LaunchOptions) => Promise<void>;
  isLoading: boolean;
}

export function usePartyMode(): UsePartyModeResult {
  const navigate = useNavigate();
  const { toast } = useToast();
  const replaceQueue = usePlaybackStore((s) => s.replaceQueue);
  const setFullscreenMode = usePlaybackStore((s) => s.setFullscreenMode);
  const showFireworks = useFireworksStore((s) => s.show);
  const [isLoading, setIsLoading] = useState(false);

  const launch = useCallback(async (filterParams?: Partial<PartyModeParams>, opts?: LaunchOptions) => {
    const doNavigate = opts?.navigate !== false;
    setIsLoading(true);

    // Fire the navigate + fireworks + toast tail shared by both launch paths.
    const celebrate = (total: number) => {
      setFullscreenMode(opts?.fullscreenMode ?? "theater");
      if (!doNavigate) return;
      navigate("/now-playing");
      const animSettings = loadAnimationSettings();
      if (animSettings.enabled) showFireworks(animSettings.duration * 1000);
      toast({ type: "success", title: `Party Mode! ${total} track${total !== 1 ? "s" : ""} queued` });
    };

    try {
      // Load exclusion settings
      const exclusions = loadExclusions();

      const params: PartyModeParams = {
        ...filterParams,
      };
      if (opts && Object.prototype.hasOwnProperty.call(opts, "playlistId")) {
        params.use_saved_playlist = false;
        if (opts.playlistId != null) params.playlist_id = opts.playlistId;
      } else {
        const { playlistId } = loadPartyPlaylist();
        if (playlistId != null) params.playlist_id = playlistId;
      }

      // Apply exclusions
      if (exclusions.version_types.length > 0) {
        params.exclude_version_types = [
          ...exclusions.version_types,
          ...(exclusions.exclude_adult ? ["18+"] : []),
        ].filter((value, index, all) => all.indexOf(value) === index).join(",");
      } else if (exclusions.exclude_adult) {
        params.exclude_version_types = "18+";
      }
      if (exclusions.artists.length > 0) {
        params.exclude_artists = exclusions.artists.join(",");
      }
      if (exclusions.genres.length > 0) {
        params.exclude_genres = exclusions.genres.join(",");
      }
      if (exclusions.albums.length > 0) {
        params.exclude_albums = exclusions.albums.join(",");
      }
      if (exclusions.min_song_rating != null) {
        params.min_song_rating = exclusions.min_song_rating;
      }
      if (exclusions.min_video_rating != null) {
        params.min_video_rating = exclusions.min_video_rating;
      }

      // "Party like it's…" — cap at the chosen year and bias toward that era.
      // An explicit party_year from the caller (the first-access prompt) wins;
      // otherwise fall back to the saved era preference, unless the caller is
      // authoritative about the year (prompt choosing the whole library).
      if (filterParams?.party_year == null && !opts?.ignoreSavedEra) {
        const era = loadEraSettings();
        if (era.enabled && era.year) {
          params.party_year = era.year;
        }
      }

      const result = await libraryApi.partyMode(params);

      if (result.tracks.length === 0) {
        if (doNavigate) toast({ type: "info", title: "No videos match the current filters" });
        else throw new Error("no-tracks");
        return;
      }

      // Replace the queue with shuffled tracks
      replaceQueue(
        result.tracks.map((t) => ({
          queueEntryId: t.queueEntryId,
          videoId: t.videoId,
          artist: t.artist,
          title: t.title,
          hasPoster: t.hasPoster,
          playCount: t.playCount,
          duration: t.duration ?? undefined,
        })),
        0,
      );

      celebrate(result.total);
    } catch (err) {
      if (doNavigate) toast({ type: "error", title: "Failed to start party mode" });
      else throw err;
    } finally {
      setIsLoading(false);
    }
  }, [navigate, replaceQueue, setFullscreenMode, showFireworks, toast]);

  return { launch, isLoading };
}
