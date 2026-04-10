import { useEffect, useRef, useCallback } from "react";
import { usePlaybackStore } from "@/stores/playbackStore";
import { playbackApi } from "@/lib/api";

/**
 * Invisible component that owns the <audio> element and
 * keeps it in sync with the Zustand playback store.
 * Mount once in Layout.
 */
export function AudioManager() {
  const audioRef = useRef<HTMLAudioElement>(null);
  const prevVideoIdRef = useRef<number | null>(null);

  const isPlaying = usePlaybackStore((s) => s.isPlaying);
  const repeat = usePlaybackStore((s) => s.repeat);
  const next = usePlaybackStore((s) => s.next);
  const setCurrentTime = usePlaybackStore((s) => s.setCurrentTime);
  const setDuration = usePlaybackStore((s) => s.setDuration);

  const track = usePlaybackStore((s) => {
    if (s.individualTrack) return s.individualTrack;
    if (s.currentIndex >= 0 && s.currentIndex < s.queue.length) return s.queue[s.currentIndex];
    return null;
  });
  const videoId = track?.videoId ?? null;

  // Load new source when track changes
  useEffect(() => {
    const el = audioRef.current;
    if (!el) return;
    if (videoId === null) {
      el.pause();
      el.removeAttribute("src");
      el.load();
      prevVideoIdRef.current = null;
      return;
    }
    if (videoId !== prevVideoIdRef.current) {
      // Kill any lingering FFmpeg streaming processes from the previous track
      if (prevVideoIdRef.current !== null) {
        playbackApi.killStreams().catch(() => {});
      }
      el.src = playbackApi.streamUrl(videoId);
      el.load();
      prevVideoIdRef.current = videoId;
    }
  }, [videoId]);

  // Play / pause sync
  useEffect(() => {
    const el = audioRef.current;
    if (!el || videoId === null) return;
    if (isPlaying) {
      el.play().catch(() => {});
    } else {
      el.pause();
    }
  }, [isPlaying, videoId]);

  // Handle external seek requests
  const storeCurrentTime = usePlaybackStore((s) => s.currentTime);
  const seekApplied = useRef(false);
  useEffect(() => {
    const el = audioRef.current;
    if (!el || videoId === null) return;
    // Only seek if difference is significant (>1s), and not from our own timeupdate
    if (Math.abs(el.currentTime - storeCurrentTime) > 1 && !seekApplied.current) {
      el.currentTime = storeCurrentTime;
    }
    seekApplied.current = false;
  }, [storeCurrentTime, videoId]);

  const onTimeUpdate = useCallback(() => {
    const el = audioRef.current;
    if (!el) return;
    seekApplied.current = true;
    setCurrentTime(el.currentTime);
  }, [setCurrentTime]);

  const onLoadedMetadata = useCallback(() => {
    const el = audioRef.current;
    if (!el) return;
    if (Number.isFinite(el.duration) && el.duration > 0) {
      setDuration(el.duration);
    }
    // Auto‐play after loading
    if (isPlaying) el.play().catch(() => {});
  }, [setDuration, isPlaying]);

  const onDurationChange = useCallback(() => {
    const el = audioRef.current;
    if (!el) return;
    if (Number.isFinite(el.duration) && el.duration > 0) {
      setDuration(el.duration);
    }
  }, [setDuration]);

  const onEnded = useCallback(() => {
    if (repeat === "one") {
      const el = audioRef.current;
      if (el) {
        el.currentTime = 0;
        el.play().catch(() => {});
      }
    } else {
      next();
    }
  }, [repeat, next]);

  // Record playback history
  const onPause = useCallback(() => {
    const el = audioRef.current;
    if (!el || !videoId || el.currentTime < 2) return;
    playbackApi.recordHistory(videoId, Math.floor(el.currentTime)).catch(() => {});
  }, [videoId]);

  return (
    <audio
      ref={audioRef}
      preload="metadata"
      onTimeUpdate={onTimeUpdate}
      onLoadedMetadata={onLoadedMetadata}
      onDurationChange={onDurationChange}
      onEnded={onEnded}
      onPause={onPause}
      style={{ display: "none" }}
    />
  );
}
