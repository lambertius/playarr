import {
  Play, Pause, SkipBack, SkipForward, Square, Shuffle,
  Repeat, Repeat1, ChevronRight,
} from "lucide-react";
import { Link } from "react-router-dom";
import { usePlaybackStore, type RepeatMode } from "@/stores/playbackStore";
import { playbackApi } from "@/lib/api";
import { useRef, useCallback, useState, useEffect } from "react";

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

export function PlayerBar() {
  const track = usePlaybackStore((s) => s.currentTrack)();
  const isPlaying = usePlaybackStore((s) => s.isPlaying);
  const currentTime = usePlaybackStore((s) => s.currentTime);
  const duration = usePlaybackStore((s) => s.duration);
  const shuffle = usePlaybackStore((s) => s.shuffle);
  const repeat = usePlaybackStore((s) => s.repeat);
  const queue = usePlaybackStore((s) => s.queue);

  const togglePlay = usePlaybackStore((s) => s.togglePlay);
  const stop = usePlaybackStore((s) => s.stop);
  const next = usePlaybackStore((s) => s.next);
  const prev = usePlaybackStore((s) => s.prev);
  const seekTo = usePlaybackStore((s) => s.seekTo);
  const toggleShuffle = usePlaybackStore((s) => s.toggleShuffle);
  const cycleRepeat = usePlaybackStore((s) => s.cycleRepeat);

  const progressRef = useRef<HTMLDivElement>(null);
  const [, setDragging] = useState(false);

  // ── Cycle display text: artist → title → album ──
  const [displayIndex, setDisplayIndex] = useState(0);
  useEffect(() => {
    if (!track) return;
    setDisplayIndex(0);
    const interval = setInterval(() => setDisplayIndex((i) => (i + 1) % 2), 4000);
    return () => clearInterval(interval);
  }, [track?.videoId]);

  const displayText = track
    ? displayIndex === 0
      ? track.artist
      : track.title
    : "";

  // ── Seek via progress bar ──
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
      setDragging(true);
      seekFromEvent(e);
      const onMove = (ev: MouseEvent) => seekFromEvent(ev);
      const onUp = () => {
        setDragging(false);
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

  if (!track && queue.length === 0) {
    // No playback: show nothing (or a ghost bar)
    return null;
  }

  return (
    <div className="flex items-center gap-2 flex-1 min-w-0">
      {/* Transport controls */}
      <div className="flex items-center gap-1 flex-shrink-0">
        <Btn onClick={toggleShuffle} active={shuffle} title="Shuffle">
          <Shuffle size={14} />
        </Btn>
        <Btn onClick={prev} title="Previous">
          <SkipBack size={14} />
        </Btn>
        <button
          onClick={togglePlay}
          className="flex h-7 w-7 items-center justify-center rounded-full bg-accent text-white hover:bg-accent/80 transition-colors"
          title={isPlaying ? "Pause" : "Play"}
        >
          {isPlaying ? <Pause size={14} fill="white" /> : <Play size={14} fill="white" />}
        </button>
        <Btn onClick={next} title="Next">
          <SkipForward size={14} />
        </Btn>
        <Btn onClick={stop} title="Stop">
          <Square size={13} />
        </Btn>
        <Btn onClick={cycleRepeat} active={repeat !== "off"} title={`Repeat: ${repeat}`}>
          <RepeatIcon size={14} />
        </Btn>
      </div>

      {/* Progress bar */}
      <span className="text-[10px] text-text-muted w-9 text-right flex-shrink-0 tabular-nums">
        {formatTime(currentTime)}
      </span>
      <div
        ref={progressRef}
        className="flex-1 h-1 bg-surface-lighter rounded-full cursor-pointer relative group"
        onMouseDown={onMouseDown}
      >
        <div
          className="absolute inset-y-0 left-0 bg-accent rounded-full transition-[width] duration-75"
          style={{ width: `${pct}%` }}
        />
        <div
          className="absolute top-1/2 -translate-y-1/2 h-3 w-3 rounded-full bg-accent shadow opacity-0 group-hover:opacity-100 transition-opacity"
          style={{ left: `${pct}%`, transform: `translate(-50%, -50%)` }}
        />
      </div>
      <span className="text-[10px] text-text-muted w-9 flex-shrink-0 tabular-nums">
        {formatTime(duration)}
      </span>

      {/* Queue count */}
      {queue.length > 1 && (
        <span className="text-[10px] text-text-muted flex-shrink-0 flex items-center gap-0.5">
          <ChevronRight size={10} />
          {queue.length}
        </span>
      )}

      {/* Poster thumbnail + track info — links to video detail */}
      {track && (
        <Link to={`/video/${track.videoId}`} className="flex items-center gap-2 flex-shrink-0 min-w-0 hover:opacity-80 transition-opacity">
          {track.hasPoster && (
            <img
              src={playbackApi.posterUrl(track.videoId)}
              alt=""
              className="h-9 w-9 rounded object-cover flex-shrink-0"
            />
          )}
          <span className="max-w-40 truncate text-xs font-medium text-text-primary">
            {displayText}
          </span>
        </Link>
      )}
    </div>
  );
}

/** Small icon button for controls */
function Btn({
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
    <button
      onClick={onClick}
      title={title}
      className={`flex h-6 w-6 items-center justify-center rounded transition-colors ${
        active
          ? "text-accent"
          : "text-text-muted hover:text-text-primary"
      }`}
    >
      {children}
    </button>
  );
}
