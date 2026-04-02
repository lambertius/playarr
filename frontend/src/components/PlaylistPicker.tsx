import ReactDOM from "react-dom";
import { useState } from "react";
import { Plus, X, ListMusic } from "lucide-react";
import { usePlaylists, useCreatePlaylist, useAddToPlaylist, useAddEntriesToPlaylist } from "@/hooks/queries";
import { useToast } from "@/components/Toast";

interface PlaylistPickerProps {
  open: boolean;
  videoIds: number[];
  onClose: () => void;
}

export function PlaylistPicker({ open, videoIds, onClose }: PlaylistPickerProps) {
  const { data: playlists } = usePlaylists();
  const createMutation = useCreatePlaylist();
  const addMutation = useAddToPlaylist();
  const addBatchMutation = useAddEntriesToPlaylist();
  const [newName, setNewName] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const { toast } = useToast();

  if (!open) return null;

  const isBatch = videoIds.length > 1;
  const countLabel = isBatch ? `${videoIds.length} videos` : "1 video";

  const handleAdd = (playlistId: number) => {
    const plName = playlists?.find(p => p.id === playlistId)?.name ?? "playlist";
    if (isBatch) {
      addBatchMutation.mutate(
        { playlistId, videoIds },
        { onSuccess: () => { toast({ type: "success", title: `Added ${countLabel} to "${plName}"` }); onClose(); } },
      );
    } else {
      addMutation.mutate(
        { playlistId, videoId: videoIds[0] },
        { onSuccess: () => { toast({ type: "success", title: `Added to "${plName}"` }); onClose(); } },
      );
    }
  };

  const handleCreate = () => {
    if (!newName.trim()) return;
    createMutation.mutate(
      { name: newName.trim() },
      {
        onSuccess: (pl) => {
          setNewName("");
          setShowCreate(false);
          if (isBatch) {
            addBatchMutation.mutate(
              { playlistId: pl.id, videoIds },
              { onSuccess: () => { toast({ type: "success", title: `Added ${countLabel} to "${pl.name}"` }); onClose(); } },
            );
          } else {
            addMutation.mutate(
              { playlistId: pl.id, videoId: videoIds[0] },
              { onSuccess: () => { toast({ type: "success", title: `Added to "${pl.name}"` }); onClose(); } },
            );
          }
        },
      },
    );
  };

  return ReactDOM.createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative z-10 w-72 max-h-80 rounded-lg border border-surface-border bg-surface-light shadow-xl flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-3 py-2 border-b border-surface-border">
          <span className="text-sm font-semibold text-text-primary">Add to Playlist</span>
          <button onClick={onClose} className="text-text-muted hover:text-text-primary">
            <X size={14} />
          </button>
        </div>

        {/* Playlist list */}
        <div className="flex-1 overflow-y-auto">
          {playlists && playlists.length > 0 ? (
            playlists.map((pl) => (
              <button
                key={pl.id}
                onClick={() => handleAdd(pl.id)}
                className="w-full px-3 py-2 text-left text-sm text-text-secondary hover:bg-surface-lighter hover:text-text-primary flex items-center gap-2"
              >
                <ListMusic size={14} className="text-text-muted" />
                <span className="truncate flex-1">{pl.name}</span>
                <span className="text-[10px] text-text-muted">{pl.entry_count}</span>
              </button>
            ))
          ) : (
            <div className="px-3 py-4 text-xs text-text-muted text-center">
              No playlists yet
            </div>
          )}
        </div>

        {/* Create new */}
        <div className="border-t border-surface-border px-3 py-2">
          {showCreate ? (
            <div className="flex gap-1">
              <input
                type="text"
                placeholder="Playlist name…"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                className="flex-1 rounded bg-surface text-sm text-text-primary px-2 py-1 border border-surface-border focus:border-accent focus:outline-none"
                autoFocus
              />
              <button
                onClick={handleCreate}
                disabled={!newName.trim()}
                className="btn-primary btn-sm text-xs px-2"
              >
                Create
              </button>
            </div>
          ) : (
            <button
              onClick={() => setShowCreate(true)}
              className="flex items-center gap-1.5 text-xs text-accent hover:text-accent/80"
            >
              <Plus size={13} />
              New Playlist
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
