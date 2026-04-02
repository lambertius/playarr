import ReactDOM from "react-dom";
import { X, Play, Trash2 } from "lucide-react";
import { usePlaybackStore, type PlaybackTrack } from "@/stores/playbackStore";
import { playbackApi } from "@/lib/api";

interface NowPlayingPanelProps {
  open: boolean;
  onClose: () => void;
}

export function NowPlayingPanel({ open, onClose }: NowPlayingPanelProps) {
  const queue = usePlaybackStore((s) => s.queue);
  const currentIndex = usePlaybackStore((s) => s.currentIndex);
  const replaceQueue = usePlaybackStore((s) => s.replaceQueue);
  const removeFromQueue = usePlaybackStore((s) => s.removeFromQueue);
  const clearQueue = usePlaybackStore((s) => s.clearQueue);

  if (!open) return null;

  return ReactDOM.createPortal(
    <div className="fixed inset-0 z-50 flex">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />

      {/* Panel – slides from left */}
      <div className="relative z-10 w-80 h-full bg-surface-light border-r border-surface-border flex flex-col shadow-xl animate-[slideInLeft_150ms_ease-out]">
        {/* Header */}
        <div className="flex items-center justify-between h-12 px-4 border-b border-surface-border">
          <span className="text-sm font-semibold text-text-primary">Now Playing</span>
          <div className="flex items-center gap-2">
            {queue.length > 0 && (
              <button
                onClick={clearQueue}
                className="text-xs text-text-muted hover:text-danger transition-colors"
              >
                Clear
              </button>
            )}
            <button onClick={onClose} className="text-text-muted hover:text-text-primary">
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Queue list */}
        <div className="flex-1 overflow-y-auto">
          {queue.length === 0 ? (
            <div className="flex items-center justify-center h-full text-text-muted text-sm">
              Queue is empty
            </div>
          ) : (
            queue.map((track, idx) => (
              <QueueItem
                key={`${track.videoId}-${idx}`}
                track={track}
                isCurrent={idx === currentIndex}
                onPlay={() => replaceQueue(queue, idx)}
                onRemove={() => removeFromQueue(idx)}
              />
            ))
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

function QueueItem({
  track,
  isCurrent,
  onPlay,
  onRemove,
}: {
  track: PlaybackTrack;
  isCurrent: boolean;
  onPlay: () => void;
  onRemove: () => void;
}) {
  return (
    <div
      className={`flex items-center gap-2 px-3 py-2 group cursor-pointer hover:bg-surface-lighter transition-colors ${
        isCurrent ? "bg-accent/10 border-l-2 border-accent" : ""
      }`}
      onClick={onPlay}
    >
      {/* Poster */}
      {track.hasPoster ? (
        <img
          src={playbackApi.posterUrl(track.videoId)}
          alt=""
          className="h-8 w-8 rounded object-cover flex-shrink-0"
        />
      ) : (
        <div className="h-8 w-8 rounded bg-surface-lighter flex items-center justify-center flex-shrink-0">
          <Play size={12} className="text-text-muted" />
        </div>
      )}

      {/* Info */}
      <div className="flex-1 min-w-0">
        <p className="text-xs font-medium text-text-primary truncate">{track.artist}</p>
        <p className="text-[11px] text-text-secondary truncate">{track.title}</p>
      </div>

      {/* Remove button */}
      <button
        onClick={(e) => { e.stopPropagation(); onRemove(); }}
        className="flex-shrink-0 opacity-0 group-hover:opacity-100 text-text-muted hover:text-danger transition-all"
        title="Remove"
      >
        <Trash2 size={13} />
      </button>
    </div>
  );
}
