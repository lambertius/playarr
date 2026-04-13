import { useState, useEffect } from "react";
import { X, AlertTriangle, FileX, FolderOpen, Trash2, Loader2, CheckCircle2, Unplug, Copy, Archive } from "lucide-react";
import { useLibraryHealth, useCleanStale, useCleanOrphans, useCleanRedundant, useCleanStaleArchives } from "@/hooks/queries";
import { useToast } from "@/components/Toast";
import type { StaleItem, OrphanFolder, RedundantItem, StaleArchive } from "@/types";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function CleanLibraryDialog({ open, onClose }: Props) {
  const { toast } = useToast();
  const { data, isLoading, refetch } = useLibraryHealth(open);
  const cleanStaleMutation = useCleanStale();
  const cleanOrphansMutation = useCleanOrphans();
  const cleanRedundantMutation = useCleanRedundant();
  const cleanStaleArchivesMutation = useCleanStaleArchives();
  const [selectedStale, setSelectedStale] = useState<Set<number>>(new Set());
  const [selectedUnmanaged, setSelectedUnmanaged] = useState<Set<number>>(new Set());
  const [selectedOrphans, setSelectedOrphans] = useState<Set<string>>(new Set());
  const [selectedRedundant, setSelectedRedundant] = useState<Set<string>>(new Set());
  const [selectedStaleArchives, setSelectedStaleArchives] = useState<Set<string>>(new Set());

  // Reset selections when dialog opens/data changes
  useEffect(() => {
    if (open) {
      setSelectedStale(new Set());
      setSelectedUnmanaged(new Set());
      setSelectedOrphans(new Set());
      setSelectedRedundant(new Set());
      setSelectedStaleArchives(new Set());
      refetch();
    }
  }, [open, refetch]);

  if (!open) return null;

  const staleItems: StaleItem[] = data?.stale_items ?? [];
  const unmanagedItems: StaleItem[] = data?.unmanaged_items ?? [];
  const orphanFolders: OrphanFolder[] = data?.orphan_folders ?? [];
  const redundantItems: RedundantItem[] = data?.redundant_items ?? [];
  const staleArchives: StaleArchive[] = data?.stale_archives ?? [];
  const allRedundantFiles = redundantItems.flatMap((r) => r.files.map((f) => f.file_path));

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

  const toggleUnmanaged = (id: number) => {
    setSelectedUnmanaged((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAllUnmanaged = () => {
    if (selectedUnmanaged.size === unmanagedItems.length) {
      setSelectedUnmanaged(new Set());
    } else {
      setSelectedUnmanaged(new Set(unmanagedItems.map((s) => s.id)));
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

  const handleCleanUnmanaged = () => {
    const ids = [...selectedUnmanaged];
    if (ids.length === 0) return;
    cleanStaleMutation.mutate(ids, {
      onSuccess: (res) => {
        toast({ type: "success", title: `Removed ${res.removed} unmanaged entries` });
        setSelectedUnmanaged(new Set());
        refetch();
      },
      onError: () => toast({ type: "error", title: "Failed to clean unmanaged entries" }),
    });
  };

  const handleDeleteOrphans = (forcePermanent = false) => {
    const paths = [...selectedOrphans];
    if (paths.length === 0) return;
    cleanOrphansMutation.mutate({ folder_paths: paths, mode: "delete", force_permanent: forcePermanent }, {
      onSuccess: (data) => {
        const networkPaths = data.results?.filter(
          (r: { status: string; folder: string }) => r.status === "network_confirm_required"
        ) ?? [];
        const deleted = data.results?.filter(
          (r: { status: string }) => r.status === "deleted"
        ).length ?? 0;

        if (networkPaths.length > 0 && !forcePermanent) {
          const confirmed = window.confirm(
            `${networkPaths.length} folder(s) are on a network location where the recycle bin is unavailable.\n\n` +
            `These will be permanently deleted and cannot be recovered.\n\n` +
            `Do you want to proceed?`
          );
          if (confirmed) {
            // Re-submit only the network paths with force_permanent
            setSelectedOrphans(new Set(networkPaths.map((r: { folder: string }) => r.folder)));
            handleDeleteOrphans(true);
            return;
          }
        }

        if (deleted > 0) {
          toast({ type: "success", title: `Sent ${deleted} orphaned folder(s) to the recycle bin` });
        }
        setSelectedOrphans(new Set());
        refetch();
      },
      onError: () => toast({ type: "error", title: "Failed to delete orphaned folders" }),
    });
  };

  const toggleRedundant = (path: string) => {
    setSelectedRedundant((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const selectAllRedundant = () => {
    if (selectedRedundant.size === allRedundantFiles.length) {
      setSelectedRedundant(new Set());
    } else {
      setSelectedRedundant(new Set(allRedundantFiles));
    }
  };

  const handleCleanRedundant = () => {
    const paths = [...selectedRedundant];
    if (paths.length === 0) return;
    cleanRedundantMutation.mutate(paths, {
      onSuccess: (res) => {
        toast({ type: "success", title: `Deleted ${res.deleted} redundant file(s)` });
        setSelectedRedundant(new Set());
        refetch();
      },
      onError: () => toast({ type: "error", title: "Failed to clean redundant files" }),
    });
  };

  const toggleStaleArchive = (folder: string) => {
    setSelectedStaleArchives((prev) => {
      const next = new Set(prev);
      if (next.has(folder)) next.delete(folder);
      else next.add(folder);
      return next;
    });
  };

  const selectAllStaleArchives = () => {
    if (selectedStaleArchives.size === staleArchives.length) {
      setSelectedStaleArchives(new Set());
    } else {
      setSelectedStaleArchives(new Set(staleArchives.map((a) => a.folder)));
    }
  };

  const handleCleanStaleArchives = () => {
    const folders = [...selectedStaleArchives];
    if (folders.length === 0) return;
    cleanStaleArchivesMutation.mutate(folders, {
      onSuccess: (res) => {
        toast({ type: "success", title: `Removed ${res.deleted} stale archive(s)` });
        setSelectedStaleArchives(new Set());
        refetch();
      },
      onError: () => toast({ type: "error", title: "Failed to clean stale archives" }),
    });
  };

  const healthy = staleItems.length === 0 && unmanagedItems.length === 0 && orphanFolders.length === 0 && redundantItems.length === 0 && staleArchives.length === 0;

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

              {/* Unmanaged Entries Section */}
              {unmanagedItems.length > 0 && (
                <section>
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="text-sm font-semibold text-text-primary flex items-center gap-1.5">
                      <Unplug size={16} className="text-orange-400" />
                      Outside Library ({unmanagedItems.length})
                    </h3>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={selectAllUnmanaged}
                        className="text-xs text-accent hover:underline"
                      >
                        {selectedUnmanaged.size === unmanagedItems.length ? "Deselect all" : "Select all"}
                      </button>
                      <button
                        onClick={handleCleanUnmanaged}
                        disabled={selectedUnmanaged.size === 0 || cleanStaleMutation.isPending}
                        className="btn-danger btn-sm text-xs"
                      >
                        <Trash2 size={12} /> Remove Selected
                      </button>
                    </div>
                  </div>
                  <p className="text-xs text-text-muted mb-2">
                    These entries have files on disk but outside all configured library directories. They cannot be played.
                  </p>
                  <div className="space-y-1 max-h-48 overflow-y-auto">
                    {unmanagedItems.map((item) => (
                      <label
                        key={item.id}
                        className="flex items-center gap-2 p-2 rounded hover:bg-surface-secondary/50 cursor-pointer text-sm"
                      >
                        <input
                          type="checkbox"
                          checked={selectedUnmanaged.has(item.id)}
                          onChange={() => toggleUnmanaged(item.id)}
                          className="h-3.5 w-3.5 rounded border-surface-border accent-[var(--color-accent)]"
                        />
                        <div className="flex-1 min-w-0">
                          <span className="font-medium text-text-primary truncate block">
                            {item.artist} — {item.title}
                          </span>
                          <span className="text-xs text-text-muted truncate block">
                            {item.file_path}
                          </span>
                        </div>
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
                        onClick={() => handleDeleteOrphans()}
                        disabled={selectedOrphans.size === 0 || cleanOrphansMutation.isPending}
                        className="btn-danger btn-sm text-xs"
                      >
                        <Trash2 size={12} /> Delete Selected
                      </button>
                    </div>
                  </div>
                  <p className="text-xs text-text-muted mb-2">
                    These folders exist in the library directory but aren't tracked in the database. Files will be sent to the recycle bin.
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

              {/* Redundant Files Section */}
              {redundantItems.length > 0 && (
                <section>
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="text-sm font-semibold text-text-primary flex items-center gap-1.5">
                      <Copy size={16} className="text-purple-400" />
                      Redundant Files ({allRedundantFiles.length})
                    </h3>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={selectAllRedundant}
                        className="text-xs text-accent hover:underline"
                      >
                        {selectedRedundant.size === allRedundantFiles.length ? "Deselect all" : "Select all"}
                      </button>
                      <button
                        onClick={handleCleanRedundant}
                        disabled={selectedRedundant.size === 0 || cleanRedundantMutation.isPending}
                        className="btn-danger btn-sm text-xs"
                      >
                        <Trash2 size={12} /> Delete Selected
                      </button>
                    </div>
                  </div>
                  <p className="text-xs text-text-muted mb-2">
                    Mismatched sidecar files (XMLs, NFOs, posters, thumbnails) that don't belong to the tracked video in their folder.
                  </p>
                  <div className="space-y-2 max-h-64 overflow-y-auto">
                    {redundantItems.map((item) => (
                      <div key={item.video_id} className="border border-surface-border rounded-lg p-2">
                        <div className="text-xs font-medium text-text-primary mb-1">
                          {item.artist} — {item.title}
                        </div>
                        <div className="space-y-0.5">
                          {item.files.map((f) => (
                            <label
                              key={f.file_path}
                              className="flex items-center gap-2 px-1.5 py-1 rounded hover:bg-surface-secondary/50 cursor-pointer text-xs"
                            >
                              <input
                                type="checkbox"
                                checked={selectedRedundant.has(f.file_path)}
                                onChange={() => toggleRedundant(f.file_path)}
                                className="h-3 w-3 rounded border-surface-border accent-[var(--color-accent)]"
                              />
                              <span className="flex-1 min-w-0 truncate text-text-secondary">
                                {f.file_name}
                              </span>
                              <span className="text-text-muted whitespace-nowrap">
                                {f.reason} • {(f.size_bytes / 1024).toFixed(0)} KB
                              </span>
                            </label>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* Stale Archives Section */}
              {staleArchives.length > 0 && (
                <section>
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="text-sm font-semibold text-text-primary flex items-center gap-1.5">
                      <Archive size={16} className="text-cyan-400" />
                      Stale Archives ({staleArchives.length})
                    </h3>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={selectAllStaleArchives}
                        className="text-xs text-accent hover:underline"
                      >
                        {selectedStaleArchives.size === staleArchives.length ? "Deselect all" : "Select all"}
                      </button>
                      <button
                        onClick={handleCleanStaleArchives}
                        disabled={selectedStaleArchives.size === 0 || cleanStaleArchivesMutation.isPending}
                        className="btn-danger btn-sm text-xs"
                      >
                        <Trash2 size={12} /> Delete Selected
                      </button>
                    </div>
                  </div>
                  <p className="text-xs text-text-muted mb-2">
                    Archive folders whose video file has been deleted or moved. Only leftover manifests and sidecars remain.
                  </p>
                  <div className="space-y-1 max-h-48 overflow-y-auto">
                    {staleArchives.map((item) => (
                      <label
                        key={item.folder}
                        className="flex items-center gap-2 p-2 rounded hover:bg-surface-secondary/50 cursor-pointer text-sm"
                      >
                        <input
                          type="checkbox"
                          checked={selectedStaleArchives.has(item.folder)}
                          onChange={() => toggleStaleArchive(item.folder)}
                          className="h-3.5 w-3.5 rounded border-surface-border accent-[var(--color-accent)]"
                        />
                        <div className="flex-1 min-w-0">
                          <span className="font-medium text-text-primary truncate block">
                            {item.artist && item.title ? `${item.artist} — ${item.title}` : item.folder_name}
                          </span>
                          <span className="text-xs text-text-muted">
                            {(item.size_bytes / 1024).toFixed(0)} KB leftover
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
