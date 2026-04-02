import { useRef, useEffect, useCallback, type CSSProperties } from "react";
import { playbackApi } from "@/lib/api";
import { usePlaybackStore } from "@/stores/playbackStore";

interface VideoPlayerProps {
  videoId: number;
  className?: string;
  style?: CSSProperties;
  poster?: string;
}

export function VideoPlayer({ videoId, className, style, poster }: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const lastReported = useRef(0);

  // Record playback on pause/end
  const reportPlayback = useCallback(() => {
    const el = videoRef.current;
    if (!el || el.currentTime < 2) return;
    const watched = Math.floor(el.currentTime);
    if (Math.abs(watched - lastReported.current) < 5) return; // debounce within 5s
    lastReported.current = watched;
    playbackApi.recordHistory(videoId, watched).catch(() => {});
  }, [videoId]);

  // Pause playlist when this player starts, resume when it stops
  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    const store = usePlaybackStore;

    const onPlay = () => {
      const s = store.getState();
      if (s.queue.length > 0 && s.isPlaying && !s.individualTrack) {
        s.pause();
      }
    };
    const onStop = () => {
      const s = store.getState();
      if (s.queue.length > 0 && !s.individualTrack && !s.isPlaying) {
        s.resume();
      }
    };

    el.addEventListener("play", onPlay);
    el.addEventListener("pause", onStop);
    el.addEventListener("ended", onStop);
    return () => {
      el.removeEventListener("play", onPlay);
      el.removeEventListener("pause", onStop);
      el.removeEventListener("ended", onStop);
    };
  }, []);

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    el.addEventListener("pause", reportPlayback);
    el.addEventListener("ended", reportPlayback);
    return () => {
      el.removeEventListener("pause", reportPlayback);
      el.removeEventListener("ended", reportPlayback);
      reportPlayback(); // report on unmount
    };
  }, [reportPlayback]);

  return (
    <video
      ref={videoRef}
      src={playbackApi.streamUrl(videoId)}
      poster={poster}
      className={className}
      style={style}
      controls
      playsInline
      disablePictureInPicture
      controlsList="nofullscreen nodownload noremoteplayback"
      preload="metadata"
    />
  );
}
