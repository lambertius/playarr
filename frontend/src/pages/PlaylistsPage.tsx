import { useState, useMemo, useEffect } from "react";
import { ListMusic, Plus, Trash2, Play, Shuffle as ShuffleIcon, ChevronUp, ChevronDown, ArrowDownAZ, ArrowUpAZ, GripVertical, Pencil, Check, X } from "lucide-react";
import { Tooltip } from "@/components/Tooltip";
import { EmptyState } from "@/components/Feedback";
import { useToast } from "@/components/Toast";
import { usePlaylists, usePlaylist, useCreatePlaylist, useUpdatePlaylist, useDeletePlaylist, useRemoveFromPlaylist, useReorderPlaylist, useSortPlaylist } from "@/hooks/queries";
import { usePlaybackStore, type PlaybackTrack } from "@/stores/playbackStore";
import { playbackApi } from "@/lib/api";
import { shuffle } from "@/lib/shuffle";
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
    <div className="p-4 md:p-6 max-w-5xl">
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
  { value: "year", label: "Year" },
];

function PlaylistDetail({ playlistId, onDelete }: { playlistId: number; onDelete: () => void }) {
  const { data: playlist } = usePlaylist(playlistId);
  const removeMutation = useRemoveFromPlaylist();
  const reorderMutation = useReorderPlaylist();
  const sortMutation = useSortPlaylist();
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
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);

  // If a manual reorder ends up matching the server order again, drop the draft
  // so the pane returns to a clean, server-driven state. Otherwise the draft
  // stays non-null while `dirty` reads false (bar hidden, sorts re-enabled), and
  // a later server-side sort or refetch would resurface this stale order. Only
  // Save/Discard would otherwise null the draft. Placed before the early return
  // to keep hook order stable across renders where `playlist` is still loading.
  useEffect(() => {
    if (!playlist || draft === null) return;
    const server = playlist.entries;
    const matchesServerOrder =
      draft.length === server.length && draft.every((e, i) => e.id === server[i].id);
    if (matchesServerOrder) setDraft(null);
  }, [draft, playlist]);

  if (!playlist) return null;

  const serverEntries = playlist.entries;
  const dirty =
    draft !== null &&
    (draft.length !== serverEntries.length || draft.some((e, i) => e.id !== serverEntries[i].id));
  // While dirty render the draft; otherwise always render fresh server data.
  const entries = dirty && draft ? draft : serverEntries;
  const canReorder = entries.length > 1 && !reorderMutation.isPending;

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
    videoId: e.video_id,
    artist: e.artist,
    title: e.title,
    hasPoster: e.has_poster,
    duration: e.duration_seconds ?? undefined,
  }));

  // Manual reorder: swap the entry with its neighbour in the local draft.
  // Nothing is persisted until "Save order" is clicked.
  const moveEntry = (index: number, delta: number) => {
    const target = index + delta;
    if (target < 0 || target >= entries.length) return;
    const next = [...entries];
    [next[index], next[target]] = [next[target], next[index]];
    setDraft(next);
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
      const next = [...entries];
      const [moved] = next.splice(dragIndex, 1);
      next.splice(index, 0, moved);
      setDraft(next);
    }
    setDragIndex(null);
    setDragOverIndex(null);
  };
  const handleDragEnd = () => {
    setDragIndex(null);
    setDragOverIndex(null);
  };

  const saveOrder = () => {
    if (!draft) return;
    reorderMutation.mutate(
      { playlistId, entryIds: draft.map((e) => e.id) },
      {
        onSuccess: () => {
          setDraft(null);
          toast({ type: "success", title: "Playlist order saved" });
        },
        onError: () => toast({ type: "error", title: "Failed to save playlist order" }),
      },
    );
  };
  const discardOrder = () => setDraft(null);

  const removeEntry = (entryId: number) => {
    removeMutation.mutate(
      { playlistId, entryId },
      {
        // Keep an in-progress draft consistent with the server.
        onSuccess: () => setDraft((d) => (d ? d.filter((e) => e.id !== entryId) : d)),
        onError: () => toast({ type: "error", title: "Failed to remove track" }),
      },
    );
  };

  const applySort = () => sortMutation.mutate({ playlistId, field: sortField, direction: sortDir });

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
            disabled={dirty}
            className="input-field w-auto py-1 text-xs disabled:opacity-40"
            aria-label="Sort tracks by"
          >
            {TRACK_SORT_FIELDS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <Tooltip content={dirty ? "Save or discard order changes first" : (sortDir === "asc" ? "Ascending (A→Z / oldest first)" : "Descending (Z→A / newest first)")}>
            <button
              onClick={() => setSortDir((d) => (d === "asc" ? "desc" : "asc"))}
              disabled={dirty}
              className="btn-ghost btn-sm text-xs disabled:opacity-40"
              aria-label="Toggle sort direction"
            >
              {sortDir === "asc" ? <ArrowDownAZ size={14} /> : <ArrowUpAZ size={14} />}
            </button>
          </Tooltip>
          <Tooltip content={dirty ? "Save or discard order changes first" : "Reorder the whole playlist by this field"}>
            <button
              onClick={applySort}
              disabled={dirty || sortMutation.isPending}
              className="btn-secondary btn-sm text-xs disabled:opacity-40"
            >
              Apply
            </button>
          </Tooltip>
          <span className="text-[10px] text-text-muted ml-auto">Or drag tracks (or use ↑ ↓) to reorder manually</span>
        </div>
      )}

      {/* Unsaved order bar */}
      {dirty && (
        <div className="flex flex-wrap items-center gap-2 mb-3 px-3 py-2 rounded-lg bg-accent/10 border border-accent/30">
          <span className="text-xs text-text-primary flex-1 min-w-0">Track order changed — save to keep it.</span>
          <button
            onClick={saveOrder}
            disabled={reorderMutation.isPending}
            className="btn-primary btn-sm"
          >
            {reorderMutation.isPending ? "Saving…" : "Save order"}
          </button>
          <button
            onClick={discardOrder}
            disabled={reorderMutation.isPending}
            className="btn-ghost btn-sm"
          >
            Discard
          </button>
        </div>
      )}

      {entries.length === 0 ? (
        <p className="text-sm text-text-muted py-4 text-center">No tracks in this playlist yet.</p>
      ) : (
        <div className="space-y-0.5">
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
                key={entry.id}
                draggable={canReorder}
                onDragStart={handleDragStart(idx)}
                onDragOver={handleDragOver(idx)}
                onDrop={handleDrop(idx)}
                onDragEnd={handleDragEnd}
                className={`flex items-center gap-2 px-2 py-1.5 rounded hover:bg-surface-lighter group border-y-2 border-transparent ${indicator} ${
                  isDragging ? "opacity-40" : ""
                } ${isDropTarget ? "bg-surface-lighter" : ""}`}
              >
                {canReorder && (
                  <GripVertical
                    size={13}
                    className="text-text-muted opacity-0 group-hover:opacity-100 transition-opacity cursor-grab active:cursor-grabbing shrink-0"
                    aria-hidden
                  />
                )}
                <span className="text-[10px] text-text-muted w-5 text-right">{idx + 1}</span>
                {entry.has_poster ? (
                  <img
                    src={playbackApi.posterUrl(entry.video_id)}
                    alt=""
                    className="h-7 w-7 rounded object-cover flex-shrink-0 pointer-events-none"
                  />
                ) : (
                  <div className="h-7 w-7 rounded bg-surface-lighter flex-shrink-0" />
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium text-text-primary truncate">{entry.artist}</p>
                  <p className="text-[11px] text-text-secondary truncate">
                    {entry.title}{entry.year ? <span className="text-text-muted"> · {entry.year}</span> : null}
                  </p>
                </div>
                {/* Manual move up/down — edits the local draft until saved */}
                <div className="flex flex-col opacity-0 group-hover:opacity-100 transition-all">
                  <Tooltip content="Move up">
                    <button
                      onClick={() => moveEntry(idx, -1)}
                      disabled={idx === 0 || reorderMutation.isPending}
                      className="text-text-muted hover:text-text-primary disabled:opacity-30"
                      aria-label="Move track up"
                    >
                      <ChevronUp size={13} />
                    </button>
                  </Tooltip>
                  <Tooltip content="Move down">
                    <button
                      onClick={() => moveEntry(idx, 1)}
                      disabled={idx === entries.length - 1 || reorderMutation.isPending}
                      className="text-text-muted hover:text-text-primary disabled:opacity-30"
                      aria-label="Move track down"
                    >
                      <ChevronDown size={13} />
                    </button>
                  </Tooltip>
                </div>
                <Tooltip content="Remove this track from the playlist">
                <button
                  onClick={() => removeEntry(entry.id)}
                  className="opacity-0 group-hover:opacity-100 text-text-muted hover:text-danger transition-all"
                >
                  <Trash2 size={12} />
                </button>
                </Tooltip>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
