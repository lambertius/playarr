import { CheckCircle2, Music, Disc3, ShieldCheck, Link2 } from "lucide-react";
import { Tooltip } from "@/components/Tooltip";
import { Link } from "react-router-dom";
import type { CanonicalTrack } from "@/types";

interface CanonicalTrackPanelProps {
  track: CanonicalTrack;
  className?: string;
}

export function CanonicalTrackPanel({ track, className }: CanonicalTrackPanelProps) {
  return (
    <div className={`card${className ? ` ${className}` : ""}`}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide">
          Canonical Track
        </h3>
        <div className="flex items-center gap-1.5">
          {track.ai_verified && (
            <Tooltip content={track.ai_verified_at ? `AI verified: ${new Date(track.ai_verified_at).toLocaleString()}` : "AI verified"}>
            <span
              className="inline-flex items-center gap-1 text-[10px] text-emerald-400 bg-emerald-400/10 px-1.5 py-0.5 rounded-full"
            >
              <ShieldCheck size={10} />
              AI Verified
            </span>
            </Tooltip>
          )}
          {track.canonical_verified && (
            <Tooltip content="Canonical identity confirmed — this track's metadata has been verified">
            <span
              className="inline-flex items-center gap-1 text-[10px] text-sky-400 bg-sky-400/10 px-1.5 py-0.5 rounded-full"
            >
              <CheckCircle2 size={10} />
              Confirmed
            </span>
            </Tooltip>
          )}
        </div>
      </div>

      <div className="space-y-2 text-sm">
        {/* Artist */}
        {track.artist_name && (
          <div className="flex items-start gap-2">
            <Music size={13} className="text-text-muted mt-0.5 flex-shrink-0" />
            <div>
              <span className="text-text-muted text-xs">Artist</span>
              <p className="text-text-primary">{track.artist_name}</p>
            </div>
          </div>
        )}

        {/* Title */}
        <div className="flex items-start gap-2">
          <Disc3 size={13} className="text-text-muted mt-0.5 flex-shrink-0" />
          <div>
            <span className="text-text-muted text-xs">Title</span>
            <p className="text-text-primary">{track.title}</p>
          </div>
        </div>

        {/* Album + Year row */}
        {(track.album_name || track.year) && (
          <div className="flex gap-4 ml-5">
            {track.album_name && (
              <div>
                <span className="text-text-muted text-xs">Album</span>
                <p className="text-text-primary">{track.album_name}</p>
              </div>
            )}
            {track.year && (
              <div>
                <span className="text-text-muted text-xs">Year</span>
                <p className="text-text-primary">{track.year}</p>
              </div>
            )}
          </div>
        )}

        {/* Genres */}
        {track.genres.length > 0 && (
          <div className="ml-5">
            <span className="text-text-muted text-xs">Genres</span>
            <div className="flex flex-wrap gap-1 mt-0.5">
              {track.genres.map((g) => (
                <span
                  key={g.id}
                  className="text-[10px] px-1.5 py-0.5 rounded bg-surface-lighter text-text-muted"
                >
                  {g.name}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Cover info */}
        {track.is_cover && (
          <div className="ml-5 mt-1 text-xs text-orange-400 bg-orange-400/10 rounded px-2 py-1">
            Cover of {track.original_artist}
            {track.original_title && ` — ${track.original_title}`}
          </div>
        )}

        {/* Linked videos (canonical siblings) */}
        {track.linked_videos && track.linked_videos.length > 0 && (
          <div className="ml-5 mt-2 pt-2 border-t border-border/50">
            <span className="text-text-muted text-xs flex items-center gap-1 mb-1">
              <Link2 size={11} />
              Other versions in library
            </span>
            <div className="space-y-1">
              {track.linked_videos.map((v) => (
                <Link
                  key={v.id}
                  to={`/video/${v.id}`}
                  className="flex items-center gap-2 text-xs text-accent hover:text-accent-hover transition-colors group"
                >
                  <span className="truncate">
                    {v.artist} — {v.title}
                  </span>
                  {(v.resolution_label || v.version_type !== "normal") && (
                    <span className="flex-shrink-0 text-[10px] text-text-muted group-hover:text-accent-hover">
                      {[v.resolution_label, v.version_type !== "normal" ? v.version_type : null]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                  )}
                </Link>
              ))}
            </div>
          </div>
        )}

        {/* Metadata source + video count */}
        <div className="flex items-center gap-3 ml-5 mt-2 pt-2 border-t border-border/50">
          {track.metadata_source && (
            <span className="text-[10px] text-text-muted">
              Source: {track.metadata_source}
            </span>
          )}
          {track.video_count > 1 && (
            <span className="text-[10px] text-text-muted">
              {track.video_count} video{track.video_count !== 1 ? "s" : ""} linked
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
