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

  // In TV/kiosk mode the on-screen <video> plays a single combined stream that
  // carries its own audio, so this global audio element must stay silent to
  // avoid double audio.
  const tvMode = usePlaybackStore((s) => s.tvMode);

  // Load new source when track changes
  useEffect(() => {
    const el = audioRef.current;
    if (!el) return;
    if (tvMode) {
      // TV/Cast owns one combined media stream. Fully detach the desktop audio
      // source instead of merely pausing it: a paused element near EOF can
      // still deliver a delayed `ended` event and advance the shared queue a
      // second time after the TV video has already moved on.
      el.pause();
      el.removeAttribute("src");
      el.load();
      prevVideoIdRef.current = null;
      return;
    }
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
  }, [videoId, tvMode]);

  // Play / pause sync
  useEffect(() => {
    const el = audioRef.current;
    if (!el || videoId === null || tvMode) return;
    if (isPlaying) {
      el.play().catch(() => {});
    } else {
      el.pause();
    }
  }, [isPlaying, videoId, tvMode]);

  // Handle external seek requests
  const storeCurrentTime = usePlaybackStore((s) => s.currentTime);
  const seekApplied = useRef(false);
  useEffect(() => {
    const el = audioRef.current;
    if (!el || videoId === null || tvMode) return;
    // Only seek if difference is significant (>1s), and not from our own timeupdate
    if (Math.abs(el.currentTime - storeCurrentTime) > 1 && !seekApplied.current) {
      el.currentTime = storeCurrentTime;
    }
    seekApplied.current = false;
  }, [storeCurrentTime, tvMode, videoId]);

  const onTimeUpdate = useCallback(() => {
    const el = audioRef.current;
    if (!el || tvMode) return;
    seekApplied.current = true;
    setCurrentTime(el.currentTime);
  }, [setCurrentTime, tvMode]);

  const onLoadedMetadata = useCallback(() => {
    const el = audioRef.current;
    if (!el || tvMode) return;
    if (Number.isFinite(el.duration) && el.duration > 0) {
      setDuration(el.duration);
    }
    // Auto‐play after loading
    if (isPlaying) el.play().catch(() => {});
  }, [setDuration, isPlaying, tvMode]);

  const onDurationChange = useCallback(() => {
    const el = audioRef.current;
    if (!el || tvMode) return;
    if (Number.isFinite(el.duration) && el.duration > 0) {
      setDuration(el.duration);
    }
  }, [setDuration, tvMode]);

  const onEnded = useCallback(() => {
    if (tvMode) return;
    if (repeat === "one") {
      const el = audioRef.current;
      if (el) {
        el.currentTime = 0;
        el.play().catch(() => {});
      }
    } else {
      next();
    }
  }, [repeat, next, tvMode]);

  // Record playback history
  const onPause = useCallback(() => {
    const el = audioRef.current;
    if (!el || tvMode || !videoId || el.currentTime < 2) return;
    playbackApi.recordHistory(videoId, Math.floor(el.currentTime)).catch(() => {});
  }, [tvMode, videoId]);

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
