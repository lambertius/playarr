import { useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { libraryApi } from "@/lib/api";
import { usePlaybackStore } from "@/stores/playbackStore";
import { useFireworksStore } from "@/stores/fireworksStore";
import { useToast } from "@/components/Toast";
import type { PartyModeParams, PartyModeExclusions } from "@/types";

const EXCLUSIONS_KEY = "playarr:partyMode:exclusions";
const ANIMATION_KEY = "playarr:partyMode:animation";

export const DEFAULT_EXCLUSIONS: PartyModeExclusions = {
  version_types: [],
  artists: [],
  genres: [],
  albums: [],
  min_song_rating: null,
  min_video_rating: null,
};

export interface PartyModeAnimationSettings {
  enabled: boolean;
  duration: number; // seconds, 5-15
}

export const DEFAULT_ANIMATION: PartyModeAnimationSettings = {
  enabled: true,
  duration: 8,
};

export function loadExclusions(): PartyModeExclusions {
  try {
    const raw = localStorage.getItem(EXCLUSIONS_KEY);
    if (raw) return { ...DEFAULT_EXCLUSIONS, ...JSON.parse(raw) };
  } catch { /* ignore */ }
  return { ...DEFAULT_EXCLUSIONS };
}

export function saveExclusions(exclusions: PartyModeExclusions) {
  try {
    localStorage.setItem(EXCLUSIONS_KEY, JSON.stringify(exclusions));
  } catch { /* ignore */ }
}

export function loadAnimationSettings(): PartyModeAnimationSettings {
  try {
    const raw = localStorage.getItem(ANIMATION_KEY);
    if (raw) return { ...DEFAULT_ANIMATION, ...JSON.parse(raw) };
  } catch { /* ignore */ }
  return { ...DEFAULT_ANIMATION };
}

export function saveAnimationSettings(settings: PartyModeAnimationSettings) {
  try {
    localStorage.setItem(ANIMATION_KEY, JSON.stringify(settings));
  } catch { /* ignore */ }
}

interface UsePartyModeResult {
  launch: (filterParams?: Partial<PartyModeParams>) => Promise<void>;
  isLoading: boolean;
}

export function usePartyMode(): UsePartyModeResult {
  const navigate = useNavigate();
  const { toast } = useToast();
  const replaceQueue = usePlaybackStore((s) => s.replaceQueue);
  const setFullscreenMode = usePlaybackStore((s) => s.setFullscreenMode);
  const showFireworks = useFireworksStore((s) => s.show);
  const [isLoading, setIsLoading] = useState(false);

  const launch = useCallback(async (filterParams?: Partial<PartyModeParams>) => {
    setIsLoading(true);
    try {
      // Load exclusion settings
      const exclusions = loadExclusions();

      const params: PartyModeParams = {
        ...filterParams,
      };

      // Apply exclusions
      if (exclusions.version_types.length > 0) {
        params.exclude_version_types = exclusions.version_types.join(",");
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

      const result = await libraryApi.partyMode(params);

      if (result.tracks.length === 0) {
        toast({ type: "info", title: "No videos match the current filters" });
        return;
      }

      // Replace the queue with shuffled tracks
      replaceQueue(
        result.tracks.map((t) => ({
          videoId: t.videoId,
          artist: t.artist,
          title: t.title,
          hasPoster: t.hasPoster,
          playCount: t.playCount,
          duration: t.duration ?? undefined,
        })),
        0,
      );

      // Navigate to now playing in theater mode immediately so audio starts
      setFullscreenMode("theater");
      navigate("/now-playing");

      // Show fireworks overlay if animation is enabled
      const animSettings = loadAnimationSettings();
      if (animSettings.enabled) {
        showFireworks(animSettings.duration * 1000);
      }

      toast({
        type: "success",
        title: `Party Mode! ${result.total} track${result.total !== 1 ? "s" : ""} queued`,
      });
    } catch {
      toast({ type: "error", title: "Failed to start party mode" });
    } finally {
      setIsLoading(false);
    }
  }, [navigate, replaceQueue, setFullscreenMode, showFireworks, toast]);

  return { launch, isLoading };
}
