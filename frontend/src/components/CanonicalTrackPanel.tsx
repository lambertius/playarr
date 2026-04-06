import { useState } from "react";
import { CheckCircle2, Music, Disc3, ShieldCheck, Link2, Pencil, Search, Unlink2, Save, X, GitBranch } from "lucide-react";
import { Tooltip } from "@/components/Tooltip";
import { Link } from "react-router-dom";
import type { CanonicalTrack } from "@/types";
import {
  useCanonicalScan, useCanonicalLink, useCanonicalUnlink,
  useCanonicalCreate, useCanonicalEdit,
} from "@/hooks/queries";

interface CanonicalTrackPanelProps {
  track: CanonicalTrack | null;
  videoId: number;
  parentVideoId?: number | null;
  canonicalConfidence?: number | null;
  canonicalProvenance?: string | null;
  className?: string;
}

export function CanonicalTrackPanel({
  track, videoId, parentVideoId, canonicalConfidence, canonicalProvenance, className,
}: CanonicalTrackPanelProps) {
  const [editing, setEditing] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [creating, setCreating] = useState(false);

  // Edit form state
  const [editTitle, setEditTitle] = useState("");
  const [editArtist, setEditArtist] = useState("");
  const [editAlbum, setEditAlbum] = useState("");
  const [editYear, setEditYear] = useState<number | undefined>();
  const [editIsCover, setEditIsCover] = useState(false);
  const [editOrigArtist, setEditOrigArtist] = useState("");
  const [editOrigTitle, setEditOrigTitle] = useState("");

  // Create form state
  const [createTitle, setCreateTitle] = useState("");
  const [createArtist, setCreateArtist] = useState("");

  const scanQuery = useCanonicalScan(videoId);
  const linkMut = useCanonicalLink(videoId);
  const unlinkMut = useCanonicalUnlink(videoId);
  const createMut = useCanonicalCreate(videoId);
  const editMut = useCanonicalEdit(videoId);

  function startEdit() {
    if (!track) return;
    setEditTitle(track.title);
    setEditArtist(track.artist_name ?? "");
    setEditAlbum(track.album_name ?? "");
    setEditYear(track.year ?? undefined);
    setEditIsCover(track.is_cover);
    setEditOrigArtist(track.original_artist ?? "");
    setEditOrigTitle(track.original_title ?? "");
    setEditing(true);
  }

  function saveEdit() {
    editMut.mutate({
      title: editTitle || undefined,
      artist_name: editArtist || undefined,
      album_name: editAlbum || undefined,
      year: editYear,
      is_cover: editIsCover,
      original_artist: editOrigArtist || undefined,
      original_title: editOrigTitle || undefined,
    }, { onSuccess: () => setEditing(false) });
  }

  function handleScan() {
    setScanning(true);
    scanQuery.refetch();
  }

  function handleCreate() {
    if (!createTitle) return;
    createMut.mutate({
      title: createTitle,
      artist_name: createArtist || undefined,
    }, { onSuccess: () => setCreating(false) });
  }

  // ── No canonical track linked ──
  if (!track) {
    return (
      <div className={`card${className ? ` ${className}` : ""}`}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide">
            Canonical Track
          </h3>
        </div>

        {scanning && scanQuery.data ? (
          <div className="space-y-2">
            <p className="text-xs text-text-muted mb-2">
              {scanQuery.data.candidates.length} candidate{scanQuery.data.candidates.length !== 1 ? "s" : ""} found
            </p>
            {scanQuery.data.candidates.length > 0 ? (
              <div className="space-y-1 max-h-48 overflow-y-auto">
                {scanQuery.data.candidates.map((c) => (
                  <button
                    key={c.track_id}
                    onClick={() => linkMut.mutate(c.track_id, { onSuccess: () => setScanning(false) })}
                    disabled={linkMut.isPending}
                    className="w-full text-left px-2 py-1.5 rounded text-xs hover:bg-surface-lighter transition-colors"
                  >
                    <span className="text-text-primary font-medium">{c.artist_name ? `${c.artist_name} — ` : ""}{c.title}</span>
                    <span className="text-text-muted ml-2">({c.match_method} · {c.video_count} video{c.video_count !== 1 ? "s" : ""})</span>
                  </button>
                ))}
              </div>
            ) : (
              <p className="text-xs text-text-muted italic">No matching canonical tracks found.</p>
            )}
            <div className="flex gap-2 mt-2">
              <button onClick={() => { setScanning(false); setCreating(true); }} className="btn-ghost btn-sm text-xs gap-1">
                Create New
              </button>
              <button onClick={() => setScanning(false)} className="btn-ghost btn-sm text-xs gap-1 text-text-muted">
                Cancel
              </button>
            </div>
          </div>
        ) : creating ? (
          <div className="space-y-2">
            <input value={createTitle} onChange={e => setCreateTitle(e.target.value)} placeholder="Track title *" className="input-field text-xs w-full" />
            <input value={createArtist} onChange={e => setCreateArtist(e.target.value)} placeholder="Artist name" className="input-field text-xs w-full" />
            <div className="flex gap-2">
              <button onClick={handleCreate} disabled={!createTitle || createMut.isPending} className="btn-primary btn-sm text-xs gap-1">
                <Save size={12} /> Create & Link
              </button>
              <button onClick={() => setCreating(false)} className="btn-ghost btn-sm text-xs text-text-muted">Cancel</button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center gap-3 py-4">
            <p className="text-sm text-text-muted italic">No canonical track linked</p>
            <div className="flex gap-2">
              <button onClick={handleScan} disabled={scanQuery.isFetching} className="btn-ghost btn-sm text-xs gap-1">
                <Search size={12} /> Scan for Match
              </button>
              <button onClick={() => setCreating(true)} className="btn-ghost btn-sm text-xs gap-1">
                Create New
              </button>
            </div>
          </div>
        )}
      </div>
    );
  }

  // ── Editing mode ──
  if (editing) {
    return (
      <div className={`card${className ? ` ${className}` : ""}`}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide">
            Edit Canonical Track
          </h3>
          <button onClick={() => setEditing(false)} className="btn-ghost btn-sm p-1"><X size={14} /></button>
        </div>
        <div className="space-y-2">
          <input value={editTitle} onChange={e => setEditTitle(e.target.value)} placeholder="Title" className="input-field text-xs w-full" />
          <input value={editArtist} onChange={e => setEditArtist(e.target.value)} placeholder="Artist" className="input-field text-xs w-full" />
          <input value={editAlbum} onChange={e => setEditAlbum(e.target.value)} placeholder="Album" className="input-field text-xs w-full" />
          <input value={editYear ?? ""} onChange={e => setEditYear(e.target.value ? Number(e.target.value) : undefined)} placeholder="Year" type="number" className="input-field text-xs w-full" />
          <label className="flex items-center gap-2 text-xs text-text-secondary">
            <input type="checkbox" checked={editIsCover} onChange={e => setEditIsCover(e.target.checked)} />
            This is a cover
          </label>
          {editIsCover && (
            <>
              <input value={editOrigArtist} onChange={e => setEditOrigArtist(e.target.value)} placeholder="Original artist" className="input-field text-xs w-full" />
              <input value={editOrigTitle} onChange={e => setEditOrigTitle(e.target.value)} placeholder="Original title" className="input-field text-xs w-full" />
            </>
          )}
          <div className="flex gap-2 pt-1">
            <button onClick={saveEdit} disabled={editMut.isPending} className="btn-primary btn-sm text-xs gap-1">
              <Save size={12} /> Save
            </button>
            <button onClick={() => setEditing(false)} className="btn-ghost btn-sm text-xs text-text-muted">Cancel</button>
          </div>
        </div>
      </div>
    );
  }

  // ── Normal display ──
  return (
    <div className={`card${className ? ` ${className}` : ""}`}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide">
          Canonical Track
        </h3>
        <div className="flex items-center gap-1.5">
          {canonicalProvenance && (
            <Tooltip content={`Provenance: ${canonicalProvenance}${canonicalConfidence != null ? ` (${Math.round(canonicalConfidence * 100)}% confidence)` : ""}`}>
              <span className="inline-flex items-center text-[10px] text-text-muted bg-surface-lighter px-1.5 py-0.5 rounded-full">
                {canonicalProvenance}
              </span>
            </Tooltip>
          )}
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
          <Tooltip content="Edit canonical track metadata">
            <button onClick={startEdit} className="btn-ghost p-1 text-text-muted hover:text-text-primary">
              <Pencil size={12} />
            </button>
          </Tooltip>
          <Tooltip content="Unlink this video from the canonical track">
            <button onClick={() => unlinkMut.mutate()} disabled={unlinkMut.isPending} className="btn-ghost p-1 text-text-muted hover:text-red-400">
              <Unlink2 size={12} />
            </button>
          </Tooltip>
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

        {/* Parent video link */}
        {parentVideoId && (
          <div className="ml-5 mt-1">
            <span className="text-text-muted text-xs flex items-center gap-1 mb-0.5">
              <GitBranch size={11} />
              Parent version
            </span>
            <Link
              to={`/video/${parentVideoId}`}
              className="text-xs text-accent hover:text-accent-hover transition-colors"
            >
              Video #{parentVideoId}
            </Link>
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
