import { useState, useEffect } from "react";
import { X, AlertTriangle, FileX, FolderOpen, Trash2, Loader2, CheckCircle2 } from "lucide-react";
import { useLibraryHealth, useCleanStale, useCleanOrphans } from "@/hooks/queries";
import { useToast } from "@/components/Toast";
import type { StaleItem, OrphanFolder } from "@/types";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function CleanLibraryDialog({ open, onClose }: Props) {
  const { toast } = useToast();
  const { data, isLoading, refetch } = useLibraryHealth(open);
  const cleanStaleMutation = useCleanStale();
  const cleanOrphansMutation = useCleanOrphans();
  const [selectedStale, setSelectedStale] = useState<Set<number>>(new Set());
  const [selectedOrphans, setSelectedOrphans] = useState<Set<string>>(new Set());

  // Reset selections when dialog opens/data changes
  useEffect(() => {
    if (open) {
      setSelectedStale(new Set());
      setSelectedOrphans(new Set());
      refetch();
    }
  }, [open, refetch]);

  if (!open) return null;

  const staleItems: StaleItem[] = data?.stale_items ?? [];
  const orphanFolders: OrphanFolder[] = data?.orphan_folders ?? [];

  const toggleStale = (id: number) => {
    setSelectedStale((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleOrphan = (path: string) => {
    setSelectedOrphans((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const selectAllStale = () => {
    if (selectedStale.size === staleItems.length) {
      setSelectedStale(new Set());
    } else {
      setSelectedStale(new Set(staleItems.map((s) => s.id)));
    }
  };

  const selectAllOrphans = () => {
    if (selectedOrphans.size === orphanFolders.length) {
      setSelectedOrphans(new Set());
    } else {
      setSelectedOrphans(new Set(orphanFolders.map((o) => o.folder_path)));
    }
  };

  const handleCleanStale = () => {
    const ids = [...selectedStale];
    if (ids.length === 0) return;
    cleanStaleMutation.mutate(ids, {
      onSuccess: (res) => {
        toast({ type: "success", title: `Removed ${res.removed} stale entries` });
        setSelectedStale(new Set());
        refetch();
      },
      onError: () => toast({ type: "error", title: "Failed to clean stale entries" }),
    });
  };

  const handleDeleteOrphans = () => {
    const paths = [...selectedOrphans];
    if (paths.length === 0) return;
    cleanOrphansMutation.mutate({ folder_paths: paths, mode: "delete" }, {
      onSuccess: () => {
        toast({ type: "success", title: `Deleted ${paths.length} orphaned folder(s)` });
        setSelectedOrphans(new Set());
        refetch();
      },
      onError: () => toast({ type: "error", title: "Failed to delete orphaned folders" }),
    });
  };

  const healthy = staleItems.length === 0 && orphanFolders.length === 0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div
        className="bg-surface rounded-xl shadow-2xl border border-surface-border w-full max-w-2xl max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-surface-border">
          <h2 className="text-lg font-semibold text-text-primary flex items-center gap-2">
            <AlertTriangle size={20} className="text-amber-400" />
            Clean Library
          </h2>
          <button onClick={onClose} className="btn-ghost p-1">
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-6">
          {isLoading ? (
            <div className="flex items-center justify-center py-12 text-text-muted">
              <Loader2 size={20} className="animate-spin mr-2" />
              Scanning library...
            </div>
          ) : healthy ? (
            <div className="flex flex-col items-center justify-center py-12 text-text-muted">
              <CheckCircle2 size={40} className="text-green-400 mb-3" />
              <p className="font-medium text-text-primary">Library is healthy</p>
              <p className="text-sm">No stale entries or orphaned files found.</p>
            </div>
          ) : (
            <>
              {/* Stale Entries Section */}
              {staleItems.length > 0 && (
                <section>
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="text-sm font-semibold text-text-primary flex items-center gap-1.5">
                      <FileX size={16} className="text-red-400" />
                      Missing Files ({staleItems.length})
                    </h3>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={selectAllStale}
                        className="text-xs text-accent hover:underline"
                      >
                        {selectedStale.size === staleItems.length ? "Deselect all" : "Select all"}
                      </button>
                      <button
                        onClick={handleCleanStale}
                        disabled={selectedStale.size === 0 || cleanStaleMutation.isPending}
                        className="btn-danger btn-sm text-xs"
                      >
                        <Trash2 size={12} /> Remove Selected
                      </button>
                    </div>
                  </div>
                  <p className="text-xs text-text-muted mb-2">
                    These library entries reference files that no longer exist on disk.
                  </p>
                  <div className="space-y-1 max-h-48 overflow-y-auto">
                    {staleItems.map((item) => (
                      <label
                        key={item.id}
                        className="flex items-center gap-2 p-2 rounded hover:bg-surface-secondary/50 cursor-pointer text-sm"
                      >
                        <input
                          type="checkbox"
                          checked={selectedStale.has(item.id)}
                          onChange={() => toggleStale(item.id)}
                          className="h-3.5 w-3.5 rounded border-surface-border accent-[var(--color-accent)]"
                        />
                        <span className="font-medium text-text-primary truncate">
                          {item.artist} — {item.title}
                        </span>
                      </label>
                    ))}
                  </div>
                </section>
              )}

              {/* Orphaned Files Section */}
              {orphanFolders.length > 0 && (
                <section>
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="text-sm font-semibold text-text-primary flex items-center gap-1.5">
                      <FolderOpen size={16} className="text-amber-400" />
                      Orphaned Folders ({orphanFolders.length})
                    </h3>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={selectAllOrphans}
                        className="text-xs text-accent hover:underline"
                      >
                        {selectedOrphans.size === orphanFolders.length ? "Deselect all" : "Select all"}
                      </button>
                      <button
                        onClick={handleDeleteOrphans}
                        disabled={selectedOrphans.size === 0 || cleanOrphansMutation.isPending}
                        className="btn-danger btn-sm text-xs"
                      >
                        <Trash2 size={12} /> Delete Selected
                      </button>
                    </div>
                  </div>
                  <p className="text-xs text-text-muted mb-2">
                    These folders exist in the library directory but aren't tracked in the database.
                  </p>
                  <div className="space-y-1 max-h-48 overflow-y-auto">
                    {orphanFolders.map((folder) => (
                      <label
                        key={folder.folder_path}
                        className="flex items-center gap-2 p-2 rounded hover:bg-surface-secondary/50 cursor-pointer text-sm"
                      >
                        <input
                          type="checkbox"
                          checked={selectedOrphans.has(folder.folder_path)}
                          onChange={() => toggleOrphan(folder.folder_path)}
                          className="h-3.5 w-3.5 rounded border-surface-border accent-[var(--color-accent)]"
                        />
                        <div className="flex-1 min-w-0">
                          <span className="font-medium text-text-primary truncate block">
                            {folder.folder_name}
                          </span>
                          <span className="text-xs text-text-muted">
                            {folder.file_count} file{folder.file_count !== 1 ? "s" : ""}
                            {folder.has_video && " • has video"}
                            {" • "}
                            {(folder.size_bytes / 1024 / 1024).toFixed(1)} MB
                          </span>
                        </div>
                      </label>
                    ))}
                  </div>
                </section>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-surface-border">
          <button onClick={onClose} className="btn-ghost btn-sm">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
