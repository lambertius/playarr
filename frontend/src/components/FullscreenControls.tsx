import { useRef, useCallback, useState, useEffect } from "react";
import {
  Play, Pause, SkipBack, SkipForward, Square, Shuffle,
  Repeat, Repeat1, Maximize, Minimize, Monitor,
} from "lucide-react";
import { Tooltip } from "@/components/Tooltip";
import { usePlaybackStore, type RepeatMode, type FullscreenMode } from "@/stores/playbackStore";
import { playbackApi } from "@/lib/api";

function formatTime(sec: number): string {
  if (!sec || !isFinite(sec)) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

const repeatIcon: Record<RepeatMode, typeof Repeat> = {
  off: Repeat,
  all: Repeat,
  one: Repeat1,
};

const fullscreenLabels: Record<FullscreenMode, string> = {
  off: "Theatre mode",
  theater: "Video only",
  video: "Exit fullscreen",
};

export function FullscreenControls() {
  const track = usePlaybackStore((s) => s.currentTrack)();
  const isPlaying = usePlaybackStore((s) => s.isPlaying);
  const currentTime = usePlaybackStore((s) => s.currentTime);
  const duration = usePlaybackStore((s) => s.duration);
  const shuffle = usePlaybackStore((s) => s.shuffle);
  const repeat = usePlaybackStore((s) => s.repeat);
  const fullscreenMode = usePlaybackStore((s) => s.fullscreenMode);

  const togglePlay = usePlaybackStore((s) => s.togglePlay);
  const stop = usePlaybackStore((s) => s.stop);
  const next = usePlaybackStore((s) => s.next);
  const prev = usePlaybackStore((s) => s.prev);
  const seekTo = usePlaybackStore((s) => s.seekTo);
  const toggleShuffle = usePlaybackStore((s) => s.toggleShuffle);
  const cycleRepeat = usePlaybackStore((s) => s.cycleRepeat);
  const cycleFullscreen = usePlaybackStore((s) => s.cycleFullscreen);
  const exitFullscreen = usePlaybackStore((s) => s.exitFullscreen);

  const progressRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(true);
  const hideTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const containerRef = useRef<HTMLDivElement>(null);

  // Auto-hide after 3 seconds of no mouse movement
  const resetHideTimer = useCallback(() => {
    setVisible(true);
    if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
    hideTimerRef.current = setTimeout(() => setVisible(false), 3000);
  }, []);

  // Show on mouse move anywhere in the viewport
  useEffect(() => {
    const onMove = () => resetHideTimer();
    window.addEventListener("mousemove", onMove);
    resetHideTimer();
    return () => {
      window.removeEventListener("mousemove", onMove);
      if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
    };
  }, [resetHideTimer]);

  // Keep controls visible while hovering over them
  const onEnter = () => {
    if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
    setVisible(true);
  };
  const onLeave = () => resetHideTimer();

  // Keyboard: Escape exits fullscreen, Space toggles play
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        exitFullscreen();
      } else if (e.key === " " && !e.repeat) {
        e.preventDefault();
        togglePlay();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [exitFullscreen, togglePlay]);

  // Seek via progress bar
  const seekFromEvent = useCallback(
    (e: React.MouseEvent | MouseEvent) => {
      const bar = progressRef.current;
      if (!bar || !duration) return;
      const rect = bar.getBoundingClientRect();
      const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      seekTo(pct * duration);
    },
    [duration, seekTo],
  );

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      seekFromEvent(e);
      const onMove = (ev: MouseEvent) => seekFromEvent(ev);
      const onUp = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [seekFromEvent],
  );

  const pct = duration > 0 ? (currentTime / duration) * 100 : 0;
  const RepeatIcon = repeatIcon[repeat];

  const FullscreenIcon = fullscreenMode === "video" ? Minimize : fullscreenMode === "theater" ? Maximize : Monitor;

  return (
    <div
      ref={containerRef}
      className={`absolute bottom-0 left-0 right-0 z-50 transition-all duration-300 ${
        visible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-4 pointer-events-none"
      }`}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
    >
      {/* Gradient backdrop */}
      <div className="bg-gradient-to-t from-black/90 via-black/50 to-transparent pt-12 pb-4 px-6">
        {/* Track info */}
        {track && (
          <div className="flex items-center gap-3 mb-3">
            {track.hasPoster && (
              <img
                src={playbackApi.posterUrl(track.videoId)}
                alt=""
                className="h-12 w-12 rounded object-cover flex-shrink-0"
              />
            )}
            <div className="min-w-0">
              <p className="text-sm font-semibold text-white truncate">{track.title}</p>
              <p className="text-xs text-white/60 truncate">{track.artist}</p>
            </div>
          </div>
        )}

        {/* Progress bar */}
        <div className="flex items-center gap-3 mb-3">
          <span className="text-xs text-white/60 w-10 text-right tabular-nums flex-shrink-0">
            {formatTime(currentTime)}
          </span>
          <div
            ref={progressRef}
            className="flex-1 h-1.5 bg-white/20 rounded-full cursor-pointer relative group"
            onMouseDown={onMouseDown}
          >
            <div
              className="absolute inset-y-0 left-0 bg-accent rounded-full transition-[width] duration-75"
              style={{ width: `${pct}%` }}
            />
            <div
              className="absolute top-1/2 -translate-y-1/2 h-4 w-4 rounded-full bg-accent shadow opacity-0 group-hover:opacity-100 transition-opacity"
              style={{ left: `${pct}%`, transform: `translate(-50%, -50%)` }}
            />
          </div>
          <span className="text-xs text-white/60 w-10 tabular-nums flex-shrink-0">
            {formatTime(duration)}
          </span>
        </div>

        {/* Controls */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ControlBtn onClick={toggleShuffle} active={shuffle} title="Shuffle">
              <Shuffle size={18} />
            </ControlBtn>
            <ControlBtn onClick={prev} title="Previous">
              <SkipBack size={18} />
            </ControlBtn>
            <Tooltip content={isPlaying ? "Pause" : "Play"}>
            <button
              onClick={togglePlay}
              className="flex h-10 w-10 items-center justify-center rounded-full bg-accent text-white hover:bg-accent/80 transition-colors"
            >
              {isPlaying ? <Pause size={20} fill="white" /> : <Play size={20} fill="white" />}
            </button>
            </Tooltip>
            <ControlBtn onClick={next} title="Next">
              <SkipForward size={18} />
            </ControlBtn>
            <ControlBtn onClick={stop} title="Stop">
              <Square size={16} />
            </ControlBtn>
            <ControlBtn onClick={cycleRepeat} active={repeat !== "off"} title={`Repeat: ${repeat}`}>
              <RepeatIcon size={18} />
            </ControlBtn>
          </div>

          <div className="flex items-center gap-2">
            <ControlBtn onClick={cycleFullscreen} title={fullscreenLabels[fullscreenMode]}>
              <FullscreenIcon size={18} />
            </ControlBtn>
          </div>
        </div>
      </div>
    </div>
  );
}

function ControlBtn({
  onClick,
  active,
  title,
  children,
}: {
  onClick: () => void;
  active?: boolean;
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <Tooltip content={title ?? ""}>
    <button
      onClick={onClick}
      className={`flex h-8 w-8 items-center justify-center rounded-full transition-colors ${
        active
          ? "text-accent"
          : "text-white/70 hover:text-white hover:bg-white/10"
      }`}
    >
      {children}
    </button>
    </Tooltip>
  );
}
