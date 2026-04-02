import { Link } from "react-router-dom";
import ReactDOM from "react-dom";
import { Play, MoreVertical, ListPlus, ListEnd, ListStart } from "lucide-react";
import { QualityBadge, VersionBadge, EnrichmentBadge } from "@/components/Badges";
import { useHoverPreview } from "@/hooks/useHoverPreview";
import { playbackApi } from "@/lib/api";
import { usePlaybackStore, type PlaybackTrack } from "@/stores/playbackStore";
import { PlaylistPicker } from "@/components/PlaylistPicker";
import type { VideoItemSummary } from "@/types";
import { useState, useRef, useEffect, useCallback } from "react";

interface VideoCardProps {
  video: VideoItemSummary;
  onAction?: (action: string, videoId: number) => void;
  selected?: boolean;
  onSelect?: (videoId: number, selected: boolean) => void;
}

export function VideoCard({ video, onAction, selected, onSelect }: VideoCardProps) {
  const { isActive, bind } = useHoverPreview(video.id);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [playlistPickerOpen, setPlaylistPickerOpen] = useState(false);
  const menuBtnRef = useRef<HTMLButtonElement>(null);
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null);

  // Start/stop preview video
  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    if (isActive) {
      el.currentTime = 0;
      el.play().catch(() => {});
    } else {
      el.pause();
      el.removeAttribute("src");
      el.load();
    }
  }, [isActive]);

  const toTrack = useCallback(
    (): PlaybackTrack => ({
      videoId: video.id,
      artist: video.artist,
      title: video.title,
      hasPoster: video.has_poster,
      duration: video.duration_seconds ?? undefined,
    }),
    [video],
  );

  const playStore = usePlaybackStore;

  const handleAction = useCallback(
    (action: string) => {
      setMenuOpen(false);
      // Handle playback actions locally
      switch (action) {
        case "play_now":
          playStore.getState().play(toTrack());
          return;
        case "play_next":
          playStore.getState().playNext(toTrack());
          return;
        case "add_to_queue":
          playStore.getState().addToQueue(toTrack());
          return;
        case "add_to_playlist":
          setPlaylistPickerOpen(true);
          return;
      }
      // Delegate everything else to the parent
      onAction?.(action, video.id);
    },
    [onAction, video.id, toTrack, playStore],
  );

  return (
    <div {...bind} className={`group relative rounded-lg overflow-hidden bg-surface-lighter border transition-all duration-200 ${
      selected ? "border-accent shadow-[0_0_16px_rgba(225,29,46,0.18)]" : "border-surface-border hover:border-accent/40 hover:shadow-[0_0_16px_rgba(225,29,46,0.12)]"
    }`}>
      {/* Selection checkbox */}
      {onSelect && (
        <div className="absolute top-2 right-10 z-10" onClick={(e) => e.stopPropagation()}>
          <input
            type="checkbox"
            checked={selected ?? false}
            onChange={(e) => onSelect(video.id, e.target.checked)}
            className="h-4 w-4 rounded border-surface-border bg-surface-lighter text-accent focus:ring-accent cursor-pointer accent-[var(--color-accent)]"
          />
        </div>
      )}
      {/* Thumbnail / Preview */}
      <Link to={`/video/${video.id}`} className="block aspect-square relative bg-black">
        {/* Poster */}
        {video.has_poster ? (
          <img
            src={playbackApi.posterUrl(video.id)}
            alt={`${video.artist} – ${video.title}`}
            className={`absolute inset-0 w-full h-full object-cover transition-opacity ${isActive ? "opacity-0" : "opacity-100"}`}
            loading="lazy"
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center bg-surface-lighter text-text-muted text-sm">
            No poster
          </div>
        )}

        {/* Preview video (loaded only when active) */}
        {isActive && (
          <video
            ref={videoRef}
            src={`${playbackApi.previewUrl(video.id)}?v=${video.created_at}`}
            className="absolute inset-0 w-full h-full object-cover"
            muted
            loop
            playsInline
          />
        )}

        {/* Play overlay – only the button itself is clickable */}
        <button
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            const state = playStore.getState();
            if (state.queue.length > 0 || state.isPlaying) {
              state.addToQueue(toTrack());
            } else {
              state.play(toTrack());
            }
          }}
          className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-10 flex h-10 w-10 items-center justify-center rounded-full bg-accent/90 text-white shadow-[0_0_16px_rgba(225,29,46,0.5)] opacity-0 group-hover:opacity-100 transition-all duration-200 cursor-pointer hover:scale-125 hover:shadow-[0_0_24px_rgba(225,29,46,0.7)]"
        >
          <Play size={20} fill="white" />
        </button>

        {/* Quality badge */}
        <div className="absolute top-2 left-2 flex gap-1">
          <QualityBadge resolution={video.resolution_label} />
          <VersionBadge versionType={video.version_type} />
          <EnrichmentBadge status={video.enrichment_status} />
        </div>
      </Link>

      {/* Info row */}
      <div className="p-2.5">
        <Link to={`/video/${video.id}`} className="block">
          <p className="text-sm font-medium text-text-primary truncate">
            {video.artist}
          </p>
          <p className="text-xs text-text-secondary truncate">{video.title}</p>
        </Link>

        {/* Context menu trigger */}
        <div className="absolute top-2 right-2">
          <button
            ref={menuBtnRef}
            onClick={(e) => {
              e.preventDefault();
              if (!menuOpen) {
                const rect = e.currentTarget.getBoundingClientRect();
                setMenuPos({ top: rect.bottom + 4, left: rect.right });
              }
              setMenuOpen(!menuOpen);
            }}
            className="flex h-7 w-7 items-center justify-center rounded bg-black/50 text-white opacity-0 group-hover:opacity-100 transition-opacity"
            aria-label="Video actions"
          >
            <MoreVertical size={14} />
          </button>

          {menuOpen && menuPos && ReactDOM.createPortal(
            <div className="fixed inset-0 z-50">
              <div className="absolute inset-0" onClick={() => setMenuOpen(false)} />
              <div
                className="absolute z-10 w-48 rounded-lg border border-surface-border bg-surface-light/95 backdrop-blur-md py-1 shadow-xl"
                style={{ top: menuPos.top, left: menuPos.left - 192, maxHeight: `calc(100vh - ${menuPos.top + 8}px)`, overflowY: 'auto' }}
              >
                {/* Playback group */}
                {[
                  { action: "play_now", label: "Play Now", icon: Play },
                  { action: "play_next", label: "Play Next", icon: ListStart },
                  { action: "add_to_queue", label: "Add to Queue", icon: ListEnd },
                  { action: "add_to_playlist", label: "Add to Playlist…", icon: ListPlus },
                ].map(({ action, label, icon: Icon }) => (
                  <button
                    key={action}
                    onClick={() => handleAction(action)}
                    className="w-full px-3 py-1.5 text-left text-sm text-text-secondary hover:bg-surface-lighter hover:text-text-primary flex items-center gap-2"
                  >
                    <Icon size={13} />
                    {label}
                  </button>
                ))}
                <div className="my-1 border-t border-surface-border" />
                {/* Management group */}
                {["Edit Metadata", "Rescan", "Normalise", "Redownload", "Undo Rescan"].map(
                  (action) => (
                    <button
                      key={action}
                      onClick={() => handleAction(action.toLowerCase().replace(/ /g, "_"))}
                      className="w-full px-3 py-1.5 text-left text-sm text-text-secondary hover:bg-surface-lighter hover:text-text-primary"
                    >
                      {action}
                    </button>
                  )
                )}
                <div className="my-1 border-t border-surface-border" />
                <button
                  onClick={() => handleAction("delete")}
                  className="w-full px-3 py-1.5 text-left text-sm text-danger hover:bg-danger/10"
                >
                  Delete
                </button>
              </div>
            </div>,
            document.body,
          )}
        </div>
      </div>

      {/* Playlist picker popup */}
      <PlaylistPicker
        open={playlistPickerOpen}
        videoIds={[video.id]}
        onClose={() => setPlaylistPickerOpen(false)}
      />
    </div>
  );
}
