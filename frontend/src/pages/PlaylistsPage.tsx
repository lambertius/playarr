import { useState, useMemo } from "react";
import { ListMusic, Plus, Trash2, Play, Shuffle as ShuffleIcon, ChevronUp, ChevronDown, ArrowDownAZ, ArrowUpAZ, GripVertical, Pencil, Check, X } from "lucide-react";
import { Tooltip } from "@/components/Tooltip";
import { EmptyState } from "@/components/Feedback";
import { useToast } from "@/components/Toast";
import { usePlaylists, usePlaylist, useCreatePlaylist, useUpdatePlaylist, useDeletePlaylist, useBatchEditPlaylist } from "@/hooks/queries";
import { usePlaybackStore, type PlaybackTrack } from "@/stores/playbackStore";
import { playbackApi } from "@/lib/api";
import { shuffle } from "@/lib/shuffle";
import { movePlaylistEntry, sortPlaylistDraft } from "@/lib/playlistDraft";
import type { PlaylistEntry, PlaylistSortField } from "@/types";

type SortDir = "asc" | "desc";
type SortBy = "name" | "entry_count" | "created_at" | "updated_at";

const SORT_OPTIONS: { value: SortBy; label: string }[] = [
  { value: "name", label: "Name" },
  { value: "entry_count", label: "Track Count" },
  { value: "created_at", label: "Recently Created" },
  { value: "updated_at", label: "Recently Updated" },
];

export function PlaylistsPage() {
  const { data: playlists } = usePlaylists();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [sortBy, setSortBy] = useState<SortBy>("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const createMutation = useCreatePlaylist();
  const deleteMutation = useDeletePlaylist();

  const sortedPlaylists = useMemo(() => {
    if (!playlists) return [];
    return [...playlists].sort((a, b) => {
      let cmp = 0;
      switch (sortBy) {
        case "name":
          cmp = a.name.localeCompare(b.name);
          break;
        case "entry_count":
          cmp = a.entry_count - b.entry_count;
          break;
        case "created_at":
          cmp = a.created_at.localeCompare(b.created_at);
          break;
        case "updated_at":
          cmp = a.updated_at.localeCompare(b.updated_at);
          break;
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [playlists, sortBy, sortDir]);

  const handleCreate = () => {
    if (!newName.trim()) return;
    createMutation.mutate({ name: newName.trim() }, {
      onSuccess: (pl) => {
        setNewName("");
        setShowCreate(false);
        setSelectedId(pl.id);
      },
    });
  };

  return (
    <div className="p-4 md:p-6 max-w-7xl">
      <div className="flex flex-wrap items-center gap-3 mb-6">
        <h1 className="text-xl font-bold text-text-primary flex items-center gap-2">
          <ListMusic size={22} /> Playlists
        </h1>

        <select
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value as SortBy)}
          className="input-field w-auto py-1.5 text-xs"
          aria-label="Sort by"
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        <button
          onClick={() => setSortDir((d) => (d === "asc" ? "desc" : "asc"))}
          className="btn-ghost btn-sm text-xs"
          aria-label={`Sort ${sortDir === "asc" ? "descending" : "ascending"}`}
        >
          {sortDir === "asc" ? "A→Z" : "Z→A"}
        </button>
        <button
          onClick={() => setShowCreate((v) => !v)}
          className="btn-primary btn-sm"
        >
          <Plus size={14} /> New
        </button>
      </div>

      {/* Create row */}
      {showCreate && (
        <div className="flex gap-2 mb-4">
          <input
            type="text"
            placeholder="Playlist name…"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            className="flex-1 rounded bg-surface text-sm text-text-primary px-3 py-2 border border-surface-border focus:border-accent focus:outline-none"
            autoFocus
          />
          <button onClick={handleCreate} disabled={!newName.trim()} className="btn-primary btn-sm">
            Create
          </button>
          <button onClick={() => setShowCreate(false)} className="btn-ghost btn-sm">
            Cancel
          </button>
        </div>
      )}

      {!playlists || playlists.length === 0 ? (
        <EmptyState
          icon={<ListMusic size={48} />}
          title="No playlists yet"
          description="Click 'New' to create your first playlist, or add songs from the library."
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* Left: playlist list */}
          <div className="space-y-1">
            {sortedPlaylists.map((pl) => (
              <button
                key={pl.id}
                onClick={() => setSelectedId(pl.id)}
                className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${
                  selectedId === pl.id
                    ? "bg-accent/10 text-accent"
                    : "text-text-secondary hover:bg-surface-lighter hover:text-text-primary"
                }`}
              >
                <span className="font-medium">{pl.name}</span>
                <span className="ml-2 text-xs text-text-muted">{pl.entry_count} tracks</span>
              </button>
            ))}
          </div>

          {/* Right: selected playlist detail */}
          <div className="md:col-span-2">
            {selectedId ? (
              <PlaylistDetail
                key={selectedId}
                playlistId={selectedId}
                onDelete={() => {
                  deleteMutation.mutate(selectedId);
                  setSelectedId(null);
                }}
              />
            ) : (
              <div className="flex items-center justify-center h-40 text-text-muted text-sm">
                Select a playlist
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

const TRACK_SORT_FIELDS: { value: PlaylistSortField; label: string }[] = [
  { value: "artist", label: "Artist" },
  { value: "title", label: "Title" },
  { value: "album", label: "Album" },
  { value: "year", label: "Year" },
];

function PlaylistDetail({ playlistId, onDelete }: { playlistId: number; onDelete: () => void }) {
  const { data: playlist } = usePlaylist(playlistId);
  const batchEditMutation = useBatchEditPlaylist();
  const updateMutation = useUpdatePlaylist();
  const replaceQueue = usePlaybackStore((s) => s.replaceQueue);
  const { toast } = useToast();
  const [sortField, setSortField] = useState<PlaylistSortField>("artist");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  // Local draft order: null = clean (render server order). Set on the first
  // local move; server refetches never clobber it because the draft is
  // separate state and we only render it while it actually differs.
  const [draft, setDraft] = useState<PlaylistEntry[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [pendingRemovals, setPendingRemovals] = useState<Set<string>>(() => new Set());
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);
  const [reorderAnnouncement, setReorderAnnouncement] = useState("");
  const [saveConflict, setSaveConflict] = useState(false);

  if (!playlist) return null;

  const serverEntries = playlist.entries;
  const dirty =
    pendingRemovals.size > 0 || (
      draft !== null && (
        draft.length !== serverEntries.length ||
        draft.some((entry, index) => entry.id !== serverEntries[index]?.id)
      )
    );
  // While dirty render the draft; otherwise always render fresh server data.
  const entries = draft ?? serverEntries.filter((entry) => !pendingRemovals.has(entry.occurrence_id));
  const canReorder = entries.length > 1 && !batchEditMutation.isPending;

  const startRename = () => { setNameDraft(playlist.name); setEditingName(true); };
  const saveRename = () => {
    const name = nameDraft.trim();
    if (!name || name === playlist.name) { setEditingName(false); return; }
    updateMutation.mutate(
      { id: playlistId, name },
      {
        onSuccess: () => { setEditingName(false); toast({ type: "success", title: `Renamed to "${name}"` }); },
        onError: () => toast({ type: "error", title: "Failed to rename playlist" }),
      },
    );
  };

  // Tracks reflect the order currently on screen (draft while dirty).
  const tracks: PlaybackTrack[] = entries.map((e) => ({
    queueEntryId: e.occurrence_id,
    videoId: e.video_id,
    artist: e.artist,
    title: e.title,
    hasPoster: e.has_poster,
    duration: e.duration_seconds ?? undefined,
  }));

  // Manual reorder: swap the entry with its neighbour in the local draft.
  // Nothing is persisted until "Save order" is clicked.
  const moveEntry = (index: number, delta: number, boundary?: "start" | "end") => {
    const target = boundary === "start" ? 0 : boundary === "end" ? entries.length - 1 : index + delta;
    if (target < 0 || target >= entries.length) return;
    setDraft(movePlaylistEntry(entries, index, target));
    setReorderAnnouncement(`${entries[index].artist} — ${entries[index].title} moved to position ${target + 1}`);
  };

  // Native HTML5 drag-and-drop over the draft order.
  const handleDragStart = (index: number) => (e: React.DragEvent) => {
    setDragIndex(index);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(index)); // required by Firefox
  };
  const handleDragOver = (index: number) => (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (index !== dragOverIndex) setDragOverIndex(index);
  };
  const handleDrop = (index: number) => (e: React.DragEvent) => {
    e.preventDefault();
    if (dragIndex !== null && dragIndex !== index) {
      setDraft(movePlaylistEntry(entries, dragIndex, index));
      setReorderAnnouncement(`${entries[dragIndex].artist} — ${entries[dragIndex].title} moved to position ${index + 1}`);
    }
    setDragIndex(null);
    setDragOverIndex(null);
  };
  const handleDragEnd = () => {
    setDragIndex(null);
    setDragOverIndex(null);
  };

  const saveChanges = () => {
    batchEditMutation.mutate(
      {
        playlistId,
        expectedRevision: playlist.revision,
        orderedOccurrenceIds: entries.map((entry) => entry.occurrence_id),
        removedOccurrenceIds: [...pendingRemovals],
      },
      {
        onSuccess: () => {
          setSaveConflict(false);
          setDraft(null);
          setPendingRemovals(new Set());
          setSelected(new Set());
          toast({ type: "success", title: "Playlist changes saved" });
        },
        onError: () => {
          setSaveConflict(true);
          toast({ type: "error", title: "Playlist changed on another device", description: "Choose the server version or reapply this draft." });
        },
      },
    );
  };
  const undoChanges = () => {
    setSaveConflict(false);
    setDraft(null);
    setPendingRemovals(new Set());
    setSelected(new Set());
  };

  const removeEntry = (entry: PlaylistEntry) => {
    setPendingRemovals((current) => new Set(current).add(entry.occurrence_id));
    setSelected((current) => {
      const next = new Set(current);
      next.delete(entry.occurrence_id);
      return next;
    });
    setDraft(entries.filter((item) => item.occurrence_id !== entry.occurrence_id));
  };

  const applySort = () => {
    setDraft(sortPlaylistDraft(entries, selected, sortField, sortDir));
    setReorderAnnouncement(selected.size ? `${selected.size} selected tracks sorted` : "Playlist sorted");
  };

  const toggleSelected = (occurrenceId: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(occurrenceId)) next.delete(occurrenceId);
      else next.add(occurrenceId);
      return next;
    });
  };

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3 gap-2">
        {editingName ? (
          <div className="flex items-center gap-1 flex-1 min-w-0">
            <input
              type="text"
              value={nameDraft}
              onChange={(e) => setNameDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") saveRename(); if (e.key === "Escape") setEditingName(false); }}
              className="flex-1 min-w-0 rounded bg-surface text-lg font-semibold text-text-primary px-2 py-1 border border-surface-border focus:border-accent focus:outline-none"
              autoFocus
            />
            <Tooltip content="Save name">
              <button onClick={saveRename} disabled={updateMutation.isPending} className="text-accent hover:text-accent/80 p-1">
                <Check size={16} />
              </button>
            </Tooltip>
            <Tooltip content="Cancel">
              <button onClick={() => setEditingName(false)} className="text-text-muted hover:text-text-primary p-1">
                <X size={16} />
              </button>
            </Tooltip>
          </div>
        ) : (
          <button
            onClick={startRename}
            title="Click to rename"
            className="group/name flex items-center gap-1.5 min-w-0 text-left"
          >
            <h2 className="text-lg font-semibold text-text-primary truncate">{playlist.name}</h2>
            <Pencil size={13} className="text-text-muted opacity-0 group-hover/name:opacity-100 transition-opacity shrink-0" />
          </button>
        )}
        <div className="flex items-center gap-2 shrink-0">
          {tracks.length > 0 && (
            <>
              <button
                onClick={() => replaceQueue(tracks)}
                className="btn-primary btn-sm"
              >
                <Play size={13} /> Play All
              </button>
              <Tooltip content="Shuffle & play all tracks">
                <button
                  onClick={() => replaceQueue(shuffle(tracks), 0)}
                  className="btn-secondary btn-sm"
                >
                  <ShuffleIcon size={13} /> Shuffle
                </button>
              </Tooltip>
            </>
          )}
          <button onClick={onDelete} className="btn-ghost btn-sm text-danger hover:bg-danger/10">
            <Trash2 size={13} /> Delete
          </button>
        </div>
      </div>

      {playlist.description && (
        <p className="text-xs text-text-muted mb-3">{playlist.description}</p>
      )}

      {/* Reorganise controls */}
      {serverEntries.length > 1 && (
        <div className="flex flex-wrap items-center gap-2 mb-3 pb-3 border-b border-surface-border">
          <span className="text-[11px] text-text-muted">Sort by</span>
          <select
            value={sortField}
            onChange={(e) => setSortField(e.target.value as PlaylistSortField)}
            className="input-field w-auto py-1 text-xs"
            aria-label="Sort tracks by"
          >
            {TRACK_SORT_FIELDS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <Tooltip content={sortDir === "asc" ? "Ascending (A→Z / oldest first)" : "Descending (Z→A / newest first)"}>
            <button
              onClick={() => setSortDir((d) => (d === "asc" ? "desc" : "asc"))}
              className="btn-ghost btn-sm text-xs"
              aria-label="Toggle sort direction"
            >
              {sortDir === "asc" ? <ArrowDownAZ size={14} /> : <ArrowUpAZ size={14} />}
            </button>
          </Tooltip>
          <Tooltip content={selected.size > 0 ? "Sort only selected tracks within their current positions" : "Sort the whole playlist"}>
            <button
              onClick={applySort}
              className="btn-secondary btn-sm text-xs"
            >
              {selected.size > 0 ? `Sort selected (${selected.size})` : "Sort all"}
            </button>
          </Tooltip>
          <span className="text-[10px] text-text-muted ml-auto">Or drag tracks (or use ↑ ↓) to reorder manually</span>
        </div>
      )}

      {/* Unsaved order bar */}
      {dirty && (
        <div className="flex flex-wrap items-center gap-2 mb-3 px-3 py-2 rounded-lg bg-accent/10 border border-accent/30">
          <span className="text-xs text-text-primary flex-1 min-w-0">
            Unsaved playlist changes{pendingRemovals.size > 0 ? ` · ${pendingRemovals.size} to remove` : ""}
          </span>
          <button
            onClick={saveChanges}
            disabled={batchEditMutation.isPending}
            className="btn-primary btn-sm"
          >
            {batchEditMutation.isPending ? "Saving…" : "Save changes"}
          </button>
          <button
            onClick={undoChanges}
            disabled={batchEditMutation.isPending}
            className="btn-ghost btn-sm"
          >
            Undo changes
          </button>
        </div>
      )}

      {saveConflict && (
        <div role="alert" className="flex flex-wrap items-center gap-2 mb-3 px-3 py-2 rounded-lg border border-yellow-500/40 bg-yellow-500/10">
          <span className="text-xs flex-1">The server playlist changed. Your draft is still intact.</span>
          <button className="btn-ghost btn-sm" onClick={undoChanges}>Reload server version</button>
          <button className="btn-primary btn-sm" onClick={saveChanges}>Reapply my draft</button>
        </div>
      )}

      {entries.length === 0 ? (
        <p className="text-sm text-text-muted py-4 text-center">No tracks in this playlist yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <p className="sr-only" aria-live="polite">{reorderAnnouncement}</p>
          <div className="grid grid-cols-[1.5rem_1.5rem_2rem_minmax(9rem,1fr)_minmax(10rem,1.4fr)_minmax(8rem,1fr)_4rem_4.5rem] gap-2 items-center min-w-[760px] px-2 py-2 text-[10px] uppercase tracking-wide text-text-muted border-b border-surface-border">
            <input
              type="checkbox"
              checked={entries.length > 0 && selected.size === entries.length}
              onChange={() => setSelected(
                selected.size === entries.length
                  ? new Set()
                  : new Set(entries.map((entry) => entry.occurrence_id)),
              )}
              aria-label="Select all playlist tracks"
            />
            <span aria-hidden />
            <span aria-hidden />
            <span>Artist</span>
            <span>Title</span>
            <span>Album</span>
            <span>Year</span>
            <span className="text-right">Actions</span>
          </div>
          {entries.map((entry, idx) => {
            const isDragging = dragIndex === idx;
            const isDropTarget = dragOverIndex === idx && dragIndex !== null && dragIndex !== idx;
            // The dragged row lands AT the target index: dragging down inserts
            // below the target's old slot, dragging up inserts above it.
            const indicator = isDropTarget
              ? dragIndex !== null && dragIndex < idx
                ? "border-b-accent"
                : "border-t-accent"
              : "";
            return (
              <div
                key={entry.occurrence_id}
                draggable={canReorder}
                onDragStart={handleDragStart(idx)}
                onDragOver={handleDragOver(idx)}
                onDrop={handleDrop(idx)}
                onDragEnd={handleDragEnd}
                tabIndex={0}
                onKeyDown={(event) => {
                  if (!event.altKey) return;
                  if (event.key === "ArrowUp") { event.preventDefault(); moveEntry(idx, -1); }
                  if (event.key === "ArrowDown") { event.preventDefault(); moveEntry(idx, 1); }
                  if (event.key === "Home") { event.preventDefault(); moveEntry(idx, 0, "start"); }
                  if (event.key === "End") { event.preventDefault(); moveEntry(idx, 0, "end"); }
                }}
                aria-label={`${entry.artist} — ${entry.title}. Hold Alt and use arrows, Home, or End to reorder.`}
                className={`grid grid-cols-[1.5rem_1.5rem_2rem_minmax(9rem,1fr)_minmax(10rem,1.4fr)_minmax(8rem,1fr)_4rem_4.5rem] gap-2 items-center min-w-[760px] px-2 py-1.5 rounded hover:bg-surface-lighter group border-y-2 border-transparent ${indicator} ${
                  isDragging ? "opacity-40" : ""
                } ${isDropTarget ? "bg-surface-lighter" : ""}`}
              >
                <input
                  type="checkbox"
                  checked={selected.has(entry.occurrence_id)}
                  onChange={() => toggleSelected(entry.occurrence_id)}
                  aria-label={`Select ${entry.artist} — ${entry.title}`}
                />
                {canReorder && (
                  <GripVertical
                    size={13}
                    className="text-text-muted opacity-0 group-hover:opacity-100 transition-opacity cursor-grab active:cursor-grabbing shrink-0"
                    aria-hidden
                  />
                )}
                {!canReorder && <span aria-hidden />}
                {entry.has_poster ? (
                  <img
                    src={playbackApi.posterUrl(entry.video_id)}
                    alt=""
                    className="h-7 w-7 rounded object-cover flex-shrink-0 pointer-events-none"
                  />
                ) : (
                  <div className="h-7 w-7 rounded bg-surface-lighter flex-shrink-0" />
                )}
                <p className="text-xs font-medium text-text-primary truncate">{entry.artist}</p>
                <p className="text-xs text-text-primary truncate">{entry.title}</p>
                <p className="text-[11px] text-text-secondary truncate">{entry.album || "—"}</p>
                <p className="text-[11px] text-text-secondary">{entry.year ?? "—"}</p>
                {/* Manual move up/down — edits the local draft until saved */}
                <div className="flex items-center justify-end gap-0.5 opacity-70 group-hover:opacity-100 transition-all">
                  <Tooltip content="Move up">
                    <button
                      onClick={() => moveEntry(idx, -1)}
                      disabled={idx === 0 || batchEditMutation.isPending}
                      className="text-text-muted hover:text-text-primary disabled:opacity-30"
                      aria-label="Move track up"
                    >
                      <ChevronUp size={13} />
                    </button>
                  </Tooltip>
                  <Tooltip content="Move down">
                    <button
                      onClick={() => moveEntry(idx, 1)}
                      disabled={idx === entries.length - 1 || batchEditMutation.isPending}
                      className="text-text-muted hover:text-text-primary disabled:opacity-30"
                      aria-label="Move track down"
                    >
                      <ChevronDown size={13} />
                    </button>
                  </Tooltip>
                  <Tooltip content="Remove this track from the draft">
                    <button
                      onClick={() => removeEntry(entry)}
                      className="text-text-muted hover:text-danger transition-all"
                      aria-label={`Remove ${entry.artist} — ${entry.title}`}
                    >
                      <Trash2 size={12} />
                    </button>
                  </Tooltip>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
