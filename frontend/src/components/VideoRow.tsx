import { Link } from "react-router-dom";
import ReactDOM from "react-dom";
import { MoreVertical } from "lucide-react";
import { QualityBadge, VersionBadge, EnrichmentBadge } from "@/components/Badges";
import { playbackApi } from "@/lib/api";
import type { VideoItemSummary } from "@/types";
import { timeAgo } from "@/lib/utils";
import { useState, useCallback } from "react";

interface VideoRowProps {
  video: VideoItemSummary;
  onAction?: (action: string, videoId: number) => void;
  selected?: boolean;
  onSelect?: (videoId: number, selected: boolean) => void;
}

export function VideoRow({ video, onAction, selected, onSelect }: VideoRowProps) {
  const [menuOpen, setMenuOpen] = useState(false);

  const handleAction = useCallback(
    (action: string) => {
      setMenuOpen(false);
      onAction?.(action, video.id);
    },
    [onAction, video.id]
  );

  return (
    <div className={`group flex items-center gap-3 px-4 py-2 border-b border-surface-border transition-all duration-150 ${
      selected ? "bg-accent/5 shadow-[inset_3px_0_0_var(--color-accent)]" : "hover:bg-surface-lighter/80 hover:shadow-[inset_3px_0_0_var(--color-accent)]"
    }`}>
      {/* Selection checkbox */}
      {onSelect && (
        <div className="flex-shrink-0" onClick={(e) => e.stopPropagation()}>
          <input
            type="checkbox"
            checked={selected ?? false}
            onChange={(e) => onSelect(video.id, e.target.checked)}
            className="h-4 w-4 rounded border-surface-border bg-surface-lighter text-accent focus:ring-accent cursor-pointer accent-[var(--color-accent)]"
          />
        </div>
      )}
      {/* Poster thumbnail */}
      <Link to={`/video/${video.id}`} className="flex-shrink-0">
        <div className="h-10 w-10 rounded overflow-hidden bg-surface-lighter">
          {video.has_poster ? (
            <img
              src={playbackApi.posterUrl(video.id)}
              alt=""
              className="h-full w-full object-cover"
              loading="lazy"
            />
          ) : (
            <div className="h-full w-full flex items-center justify-center text-text-muted text-[10px]">—</div>
          )}
        </div>
      </Link>

      {/* Info */}
      <Link to={`/video/${video.id}`} className="flex-1 min-w-0">
        <p className="text-sm font-medium text-text-primary truncate">{video.artist}</p>
        <p className="text-xs text-text-secondary truncate">{video.title}</p>
      </Link>

      {/* Meta cols */}
      <span className="hidden md:block w-16 text-xs text-text-muted text-center">
        {video.year ?? "—"}
      </span>
      <div className="hidden sm:block w-16 text-center">
        <QualityBadge resolution={video.resolution_label} />
      </div>
      <div className="hidden sm:block w-16 text-center">
        <VersionBadge versionType={video.version_type} />
      </div>
      <div className="hidden sm:block w-16 text-center">
        <EnrichmentBadge status={video.enrichment_status} />
      </div>
      <span className="hidden lg:block w-24 text-xs text-text-muted text-right">
        {timeAgo(video.created_at)}
      </span>

      {/* Actions */}
      <div className="relative">
        <button
          onClick={() => setMenuOpen(!menuOpen)}
          className="btn-ghost btn-icon opacity-0 group-hover:opacity-100"
          aria-label="Actions"
        >
          <MoreVertical size={14} />
        </button>
        {menuOpen && ReactDOM.createPortal(
          <div className="fixed inset-0 z-50 flex items-center justify-center">
            <div className="absolute inset-0" onClick={() => setMenuOpen(false)} />
            <div className="relative z-10 w-44 rounded-lg border border-surface-border bg-surface-light/95 backdrop-blur-md py-1 shadow-xl">
              {["Play", "Edit Metadata", "Rescan", "Normalise", "Delete"].map((action) => (
                <button
                  key={action}
                  onClick={() => handleAction(action.toLowerCase().replace(/ /g, "_"))}
                  className={`w-full px-3 py-1.5 text-left text-sm ${
                    action === "Delete"
                      ? "text-danger hover:bg-danger/10"
                      : "text-text-secondary hover:bg-surface-lighter hover:text-text-primary"
                  }`}
                >
                  {action}
                </button>
              ))}
            </div>
          </div>,
          document.body,
        )}
      </div>
    </div>
  );
}
