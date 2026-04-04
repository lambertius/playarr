import { useRef, useEffect, useCallback, useState, type CSSProperties } from "react";
import { Play, Pause, Volume2, VolumeX, Maximize } from "lucide-react";
import { playbackApi } from "@/lib/api";
import { usePlaybackStore } from "@/stores/playbackStore";

interface VideoPlayerProps {
  videoId: number;
  className?: string;
  style?: CSSProperties;
  poster?: string;
  /** Authoritative duration from DB (quality_signature.duration_seconds) */
  durationSeconds?: number | null;
}

function formatTime(sec: number): string {
  if (!sec || !isFinite(sec)) return "0:00";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function VideoPlayer({ videoId, className, style, poster, durationSeconds }: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const lastReported = useRef(0);
  const progressRef = useRef<HTMLDivElement>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [elDuration, setElDuration] = useState(0);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [showControls, setShowControls] = useState(true);
  const [buffered, setBuffered] = useState(0);

  // Use stored duration if available, else fall back to element-reported duration
  const duration = (durationSeconds && durationSeconds > 0) ? durationSeconds : elDuration;

  // Record playback on pause/end
  const reportPlayback = useCallback(() => {
    const el = videoRef.current;
    if (!el || el.currentTime < 2) return;
    const watched = Math.floor(el.currentTime);
    if (Math.abs(watched - lastReported.current) < 5) return;
    lastReported.current = watched;
    playbackApi.recordHistory(videoId, watched).catch(() => {});
  }, [videoId]);

  // Pause playlist when this player starts, resume when it stops
  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    const store = usePlaybackStore;

    const onPlay = () => {
      setPlaying(true);
      const s = store.getState();
      if (s.queue.length > 0 && s.isPlaying && !s.individualTrack) {
        s.pause();
      }
    };
    const onPause = () => {
      setPlaying(false);
      const s = store.getState();
      if (s.queue.length > 0 && !s.individualTrack && !s.isPlaying) {
        s.resume();
      }
    };
    const onEnded = () => {
      setPlaying(false);
      const s = store.getState();
      if (s.queue.length > 0 && !s.individualTrack && !s.isPlaying) {
        s.resume();
      }
    };

    el.addEventListener("play", onPlay);
    el.addEventListener("pause", onPause);
    el.addEventListener("ended", onEnded);
    return () => {
      el.removeEventListener("play", onPlay);
      el.removeEventListener("pause", onPause);
      el.removeEventListener("ended", onEnded);
    };
  }, []);

  // Time updates
  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    const onTime = () => setCurrentTime(el.currentTime);
    const onDuration = () => {
      if (isFinite(el.duration)) setElDuration(el.duration);
    };
    const onProgress = () => {
      if (el.buffered.length > 0) {
        setBuffered(el.buffered.end(el.buffered.length - 1));
      }
    };
    el.addEventListener("timeupdate", onTime);
    el.addEventListener("loadedmetadata", onDuration);
    el.addEventListener("durationchange", onDuration);
    el.addEventListener("progress", onProgress);
    return () => {
      el.removeEventListener("timeupdate", onTime);
      el.removeEventListener("loadedmetadata", onDuration);
      el.removeEventListener("durationchange", onDuration);
      el.removeEventListener("progress", onProgress);
    };
  }, []);

  // Playback history reporting
  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    el.addEventListener("pause", reportPlayback);
    el.addEventListener("ended", reportPlayback);
    return () => {
      el.removeEventListener("pause", reportPlayback);
      el.removeEventListener("ended", reportPlayback);
      reportPlayback();
    };
  }, [reportPlayback]);

  // Auto-hide controls
  const resetHideTimer = useCallback(() => {
    setShowControls(true);
    if (hideTimer.current) clearTimeout(hideTimer.current);
    hideTimer.current = setTimeout(() => {
      if (videoRef.current && !videoRef.current.paused) setShowControls(false);
    }, 3000);
  }, []);

  const togglePlay = useCallback(() => {
    const el = videoRef.current;
    if (!el) return;
    if (el.paused) { el.play(); } else { el.pause(); }
    resetHideTimer();
  }, [resetHideTimer]);

  const toggleMute = useCallback(() => {
    const el = videoRef.current;
    if (!el) return;
    el.muted = !el.muted;
    setMuted(el.muted);
  }, []);

  const onVolumeChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const el = videoRef.current;
    if (!el) return;
    const v = parseFloat(e.target.value);
    el.volume = v;
    setVolume(v);
    if (v > 0 && el.muted) { el.muted = false; setMuted(false); }
  }, []);

  const seekFromEvent = useCallback((e: React.MouseEvent | MouseEvent) => {
    const bar = progressRef.current;
    const el = videoRef.current;
    if (!bar || !el || !duration) return;
    const rect = bar.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    el.currentTime = pct * duration;
  }, [duration]);

  const onSeekDown = useCallback((e: React.MouseEvent) => {
    seekFromEvent(e);
    const onMove = (ev: MouseEvent) => seekFromEvent(ev);
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }, [seekFromEvent]);

  const toggleFullscreen = useCallback(() => {
    const c = containerRef.current;
    if (!c) return;
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      c.requestFullscreen();
    }
  }, []);

  const pct = duration > 0 ? (currentTime / duration) * 100 : 0;
  const bufPct = duration > 0 ? (buffered / duration) * 100 : 0;

  return (
    <div
      ref={containerRef}
      className={`relative group ${className ?? ""}`}
      style={style}
      onMouseMove={resetHideTimer}
      onMouseLeave={() => { if (playing) setShowControls(false); }}
    >
      <video
        ref={videoRef}
        src={playbackApi.streamUrl(videoId)}
        poster={poster}
        className="w-full h-full object-contain rounded-xl cursor-pointer"
        playsInline
        disablePictureInPicture
        preload="metadata"
        onClick={togglePlay}
      />

      {/* Play overlay when paused */}
      {!playing && (
        <button
          onClick={togglePlay}
          className="absolute inset-0 flex items-center justify-center bg-black/30 rounded-xl transition-opacity"
        >
          <div className="h-16 w-16 rounded-full bg-black/60 flex items-center justify-center">
            <Play size={32} fill="white" className="text-white ml-1" />
          </div>
        </button>
      )}

      {/* Controls bar */}
      <div
        className={`absolute bottom-0 left-0 right-0 px-3 pb-2 pt-8 bg-gradient-to-t from-black/80 to-transparent rounded-b-xl transition-opacity duration-300 ${
          showControls || !playing ? "opacity-100" : "opacity-0 pointer-events-none"
        }`}
      >
        {/* Progress bar */}
        <div
          ref={progressRef}
          className="w-full h-1 bg-white/20 rounded-full cursor-pointer relative group/bar mb-2 hover:h-1.5 transition-all"
          onMouseDown={onSeekDown}
        >
          {/* Buffered */}
          <div
            className="absolute inset-y-0 left-0 bg-white/30 rounded-full"
            style={{ width: `${Math.min(bufPct, 100)}%` }}
          />
          {/* Progress */}
          <div
            className="absolute inset-y-0 left-0 bg-red-500 rounded-full"
            style={{ width: `${Math.min(pct, 100)}%` }}
          />
          {/* Thumb */}
          <div
            className="absolute top-1/2 -translate-y-1/2 h-3 w-3 rounded-full bg-red-500 shadow opacity-0 group-hover/bar:opacity-100 transition-opacity"
            style={{ left: `${Math.min(pct, 100)}%`, transform: `translate(-50%, -50%)` }}
          />
        </div>

        <div className="flex items-center gap-3">
          {/* Play/Pause */}
          <button onClick={togglePlay} className="text-white hover:text-white/80 transition-colors">
            {playing ? <Pause size={20} fill="white" /> : <Play size={20} fill="white" />}
          </button>

          {/* Time */}
          <span className="text-white/90 text-xs tabular-nums select-none">
            {formatTime(currentTime)} / {formatTime(duration)}
          </span>

          <div className="flex-1" />

          {/* Volume */}
          <button onClick={toggleMute} className="text-white hover:text-white/80 transition-colors">
            {muted || volume === 0 ? <VolumeX size={18} /> : <Volume2 size={18} />}
          </button>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={muted ? 0 : volume}
            onChange={onVolumeChange}
            className="w-16 h-1 appearance-none bg-white/30 rounded-full cursor-pointer accent-white
                       [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-2.5 [&::-webkit-slider-thumb]:h-2.5 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-white"
          />

          {/* Fullscreen */}
          <button onClick={toggleFullscreen} className="text-white hover:text-white/80 transition-colors">
            <Maximize size={18} />
          </button>
        </div>
      </div>
    </div>
  );
}
