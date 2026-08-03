import { useState, useMemo, useCallback, useRef, useEffect, useDeferredValue } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import axios from "axios";
import {
  Archive, Trash2, RotateCcw, Search, ChevronLeft, ChevronRight,
  Scissors, Film, Download, RefreshCw, Play, Pause, Volume2, VolumeX,
  X, Maximize2, ArrowRight, FolderOpen,
} from "lucide-react";
import { useArchiveItems, useArchiveRestore, useArchiveDelete, useArchiveClear } from "@/hooks/queries";
import { settingsApi, playbackApi } from "@/lib/api";
import { useToast } from "@/components/Toast";
import { useConfirm } from "@/components/ConfirmDialog";
import { Tooltip } from "@/components/Tooltip";
import { ErrorState, Skeleton } from "@/components/Feedback";
import { cn, formatBytes, timeAgo } from "@/lib/utils";
import { getPref, setPref } from "@/lib/preferences";
import { ARCHIVE_REASON_TABS } from "@/lib/archiveTaxonomy";
import type { ArchiveItem } from "@/types";
import { ViewToggle } from "@/components/ViewToggle";

// ── Archive page preferences (server-backed) ─────────────
interface ArchivePrefs {
  pageSize: number;
  view: "list" | "grid";
  reasonFilter: ArchiveReason;
}

const K_ARCHIVE_PAGE_SIZE = "archive_page_size";

function archiveLegacy(): ArchivePrefs {
  let pageSize = 25;
  try { const n = Number(localStorage.getItem(K_ARCHIVE_PAGE_SIZE)); if (n) pageSize = n; } catch { /* ignore */ }
  return { pageSize, view: "list", reasonFilter: "all" };
}

function getArchivePrefs(): ArchivePrefs {
  const fallback = archiveLegacy();
  return { ...fallback, ...getPref<Partial<ArchivePrefs>>("archive", fallback) };
}

function patchArchivePrefs(patch: Partial<ArchivePrefs>): void {
  setPref("archive", { ...getArchivePrefs(), ...patch });
}

// ── Reason config ───────────────────────────────────────
type ArchiveReason = (typeof ARCHIVE_REASON_TABS)[number];

const REASON_CONFIG: Record<string, { label: string; icon: React.ReactNode; color: string; badgeColor: string }> = {
  edit: { label: "Edit", icon: <Film size={12} />, color: "bg-cyan-500/10 text-cyan-400", badgeColor: "bg-cyan-500/15 text-cyan-400 border-cyan-500/20" },
  redownload: { label: "Redownload", icon: <Download size={12} />, color: "bg-blue-500/10 text-blue-400", badgeColor: "bg-blue-500/15 text-blue-400 border-blue-500/20" },
  trim: { label: "Trim", icon: <Scissors size={12} />, color: "bg-orange-500/10 text-orange-400", badgeColor: "bg-orange-500/15 text-orange-400 border-orange-500/20" },
  crop: { label: "Crop", icon: <Film size={12} />, color: "bg-purple-500/10 text-purple-400", badgeColor: "bg-purple-500/15 text-purple-400 border-purple-500/20" },
  both: { label: "Trim + Crop", icon: <Scissors size={12} />, color: "bg-pink-500/10 text-pink-400", badgeColor: "bg-pink-500/15 text-pink-400 border-pink-500/20" },
  restore_conflict: { label: "Restore conflict", icon: <RotateCcw size={12} />, color: "bg-amber-500/10 text-amber-400", badgeColor: "bg-amber-500/15 text-amber-400 border-amber-500/20" },
  orphaned: { label: "Orphaned", icon: <FolderOpen size={12} />, color: "bg-red-500/10 text-red-400", badgeColor: "bg-red-500/15 text-red-400 border-red-500/20" },
};

function normalizeReason(reason: string): string {
  return reason || "edit";
}

function ReasonBadge({ reason }: { reason: string }) {
  const r = normalizeReason(reason);
  const config = REASON_CONFIG[r] ?? { label: r, icon: <Archive size={12} />, badgeColor: "bg-surface-hover text-text-secondary border-surface-border" };
  return (
    <span className={cn("inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded border", config.badgeColor)}>
      {config.icon} {config.label}
    </span>
  );
}

// ── Filter pill (matches queue style) ────────────────────
// ── Pagination ──────────────────────────────────────────
const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];

function Pagination({
  page, totalPages, pageSize, total,
  onPageChange, onPageSizeChange,
}: {
  page: number; totalPages: number; pageSize: number; total: number;
  onPageChange: (p: number) => void; onPageSizeChange: (s: number) => void;
}) {
  if (total === 0) return null;
  const start = (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);
  return (
    <div className="flex items-center justify-between text-xs text-text-muted pt-3 pb-1">
      <div className="flex items-center gap-2">
        <span>Show</span>
        <select
          value={pageSize}
          onChange={(e) => onPageSizeChange(Number(e.target.value))}
          className="bg-surface-lighter border border-surface-border rounded px-2 py-1 text-xs text-text-secondary"
        >
          {PAGE_SIZE_OPTIONS.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <span>per page</span>
      </div>
      <span className="text-text-secondary">{start}–{end} of {total}</span>
      <div className="flex items-center gap-1">
        <button onClick={() => onPageChange(page - 1)} disabled={page <= 1}
          className="p-1 rounded hover:bg-surface-lighter disabled:opacity-30 disabled:cursor-not-allowed">
          <ChevronLeft size={16} />
        </button>
        <span className="tabular-nums px-2 text-text-secondary">{page} / {totalPages || 1}</span>
        <button onClick={() => onPageChange(page + 1)} disabled={page >= totalPages}
          className="p-1 rounded hover:bg-surface-lighter disabled:opacity-30 disabled:cursor-not-allowed">
          <ChevronRight size={16} />
        </button>
      </div>
    </div>
  );
}

// ── Synchronized dual video player ──────────────────────
function ComparisonPlayer({ archiveItem, onClose }: { archiveItem: ArchiveItem; onClose: () => void }) {
  const archiveRef = useRef<HTMLVideoElement>(null);
  const libraryRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const seekBarRef = useRef<HTMLDivElement>(null);

  const archiveUrl = settingsApi.archiveStreamUrl(archiveItem.path);
  const libraryUrl = archiveItem.video_id ? playbackApi.streamUrl(archiveItem.video_id) : null;
  const rafRef = useRef<number>(0);

  // Continuous sync loop — keeps library video locked to archive video
  const syncLoop = useCallback(function frame() {
    if (!archiveRef.current || !libraryRef.current) return;
    const drift = libraryRef.current.currentTime - archiveRef.current.currentTime;
    if (Math.abs(drift) > 0.05) {
      libraryRef.current.currentTime = archiveRef.current.currentTime;
    }
    setCurrentTime(archiveRef.current.currentTime);
    rafRef.current = requestAnimationFrame(frame);
  }, []);

  // Sync playback: use archive video as master
  const togglePlay = useCallback(() => {
    if (!archiveRef.current) return;
    if (playing) {
      archiveRef.current.pause();
      libraryRef.current?.pause();
      cancelAnimationFrame(rafRef.current);
    } else {
      // Sync positions before starting
      if (libraryRef.current) {
        libraryRef.current.currentTime = archiveRef.current.currentTime;
      }
      archiveRef.current.play();
      libraryRef.current?.play();
      rafRef.current = requestAnimationFrame(syncLoop);
    }
    setPlaying(!playing);
  }, [playing, syncLoop]);

  const handleTimeUpdate = useCallback(() => {
    if (!archiveRef.current) return;
    setCurrentTime(archiveRef.current.currentTime);
  }, []);

  // Clean up sync loop on unmount
  useEffect(() => {
    return () => { cancelAnimationFrame(rafRef.current); };
  }, []);

  const handleSeek = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (!seekBarRef.current || !archiveRef.current) return;
    const rect = seekBarRef.current.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const newTime = pct * duration;
    archiveRef.current.currentTime = newTime;
    if (libraryRef.current) libraryRef.current.currentTime = newTime;
    setCurrentTime(newTime);
  }, [duration]);

  const handleLoadedMetadata = useCallback((which: "archive" | "library") => {
    if (which === "archive") {
      if (archiveRef.current) setDuration(archiveRef.current.duration);
    }
  }, []);

  const formatTime = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/90 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-3 bg-surface-light/80 border-b border-surface-border">
        <div className="flex items-center gap-3">
          <Archive size={18} className="text-accent" />
          <span className="text-sm font-medium text-text-primary">
            Archive Comparison: <span className="text-accent">{archiveItem.artist}</span> — {archiveItem.title}
          </span>
        </div>
        <button onClick={onClose} className="btn-ghost btn-sm"><X size={18} /></button>
      </div>

      {/* Video panels */}
      <div className="flex-1 flex gap-1 p-2 overflow-hidden">
        {/* Archive (left) */}
        <div className="flex-1 flex flex-col items-center">
          <div className="text-xs text-text-muted mb-1 font-medium uppercase tracking-wider">Archive (Original)</div>
          <div className="flex-1 w-full bg-neutral-300 rounded-lg overflow-hidden flex items-center justify-center">
            <video
              ref={archiveRef}
              src={archiveUrl}
              className="max-w-full max-h-full"
              muted={muted}
              onTimeUpdate={handleTimeUpdate}
              onLoadedMetadata={() => handleLoadedMetadata("archive")}
              onEnded={() => setPlaying(false)}
              playsInline
            />
          </div>
        </div>

        {/* Arrow separator */}
        <div className="flex items-center px-2">
          <ArrowRight size={24} className="text-text-muted/40" />
        </div>

        {/* Library (right) */}
        <div className="flex-1 flex flex-col items-center">
          <div className="text-xs text-text-muted mb-1 font-medium uppercase tracking-wider">Library (Current)</div>
          <div className="flex-1 w-full bg-neutral-300 rounded-lg overflow-hidden flex items-center justify-center">
            {libraryUrl ? (
              <video
                ref={libraryRef}
                src={libraryUrl}
                className="max-w-full max-h-full"
                muted
                onLoadedMetadata={() => handleLoadedMetadata("library")}
                playsInline
              />
            ) : (
              <div className="text-text-muted text-sm">No linked library track</div>
            )}
          </div>
        </div>
      </div>

      {/* Controls */}
      <div className="px-6 py-3 bg-surface-light/80 border-t border-surface-border flex items-center gap-4">
        <button onClick={togglePlay} className="btn-ghost p-2">
          {playing ? <Pause size={20} /> : <Play size={20} />}
        </button>
        <button onClick={() => setMuted(!muted)} className="btn-ghost p-2">
          {muted ? <VolumeX size={18} /> : <Volume2 size={18} />}
        </button>
        <span className="text-xs text-text-muted tabular-nums w-20">
          {formatTime(currentTime)} / {formatTime(duration)}
        </span>
        <div
          ref={seekBarRef}
          className="flex-1 h-1.5 bg-surface-border rounded-full cursor-pointer relative"
          onClick={handleSeek}
        >
          <div
            className="absolute inset-y-0 left-0 bg-accent rounded-full"
            style={{ width: duration > 0 ? `${(currentTime / duration) * 100}%` : "0%" }}
          />
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// ██ Main Page
// ═══════════════════════════════════════════════════════════

export function ArchivePage() {
  const navigate = useNavigate();
  const [routeParams, setRouteParams] = useSearchParams();
  const { toast } = useToast();
  const { confirm, dialog } = useConfirm();
  const restoreMutation = useArchiveRestore();
  const deleteMutation = useArchiveDelete();
  const clearMutation = useArchiveClear();

  const initialFocusVideoId = Number(routeParams.get("focus_video_id"));
  const [focusVideoId, setFocusVideoId] = useState<number | null>(() =>
    Number.isInteger(initialFocusVideoId) && initialFocusVideoId > 0 ? initialFocusVideoId : null,
  );
  const [reasonFilter, setReasonFilter] = useState<ArchiveReason>(() =>
    focusVideoId ? "all" : getArchivePrefs().reasonFilter,
  );
  const [searchQuery, setSearchQuery] = useState(() => routeParams.get("search") ?? "");
  const deferredSearch = useDeferredValue(searchQuery.trim());
  const [selectedFolders, setSelectedFolders] = useState<Set<string>>(new Set());
  const [comparisonItem, setComparisonItem] = useState<ArchiveItem | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(() => getArchivePrefs().pageSize);
  const [viewMode, setViewMode] = useState<"list" | "grid">(() => getArchivePrefs().view);
  const focusedEntryRef = useRef<HTMLDivElement>(null);
  const scrolledFocusRef = useRef<number | null>(null);
  const archiveQuery = useArchiveItems({
    reason: reasonFilter === "all" ? undefined : reasonFilter,
    search: focusVideoId ? undefined : deferredSearch || undefined,
    video_id: focusVideoId ?? undefined,
    page, page_size: pageSize,
  });
  const { data: archivePage, isLoading, isError, isFetching, refetch } = archiveQuery;
  const items = archivePage?.items;

  // Filtered items
  const filtered = useMemo(() => items ?? [], [items]);

  // Reason and maintenance counts.
  const reasonCounts = useMemo(() => {
    return archivePage?.reason_counts ?? {};
  }, [archivePage?.reason_counts]);

  // Group filtered items by video_id (or artist+title for orphans)
  type ArchiveGroup = { key: string; video_id: number | null; artist: string; title: string; items: ArchiveItem[] };
  const grouped = useMemo<ArchiveGroup[]>(() => {
    const map = new Map<string, ArchiveGroup>();
    for (const item of filtered) {
      const key = item.video_id ? `vid_${item.video_id}` : `orphan_${item.artist}_${item.title}`;
      let group = map.get(key);
      if (!group) {
        group = { key, video_id: item.video_id, artist: item.artist, title: item.title, items: [] };
        map.set(key, group);
      }
      group.items.push(item);
    }
    // Sort items within each group by date (newest first)
    for (const g of map.values()) {
      g.items.sort((a, b) => (b.archived_at || "").localeCompare(a.archived_at || ""));
    }
    return Array.from(map.values());
  }, [filtered]);

  const archiveCount = reasonCounts.all ?? archivePage?.total ?? 0;
  const matchingCount = archivePage?.total ?? 0;
  const totalPages = archivePage?.total_pages ?? 1;
  const pagedGroups = grouped;

  useEffect(() => {
    if (!focusVideoId || scrolledFocusRef.current === focusVideoId || !focusedEntryRef.current) return;
    focusedEntryRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    scrolledFocusRef.current = focusVideoId;
  }, [focusVideoId, grouped]);

  // Selection helpers
  const pagedFolders = useMemo(() => pagedGroups.flatMap(g => g.items.map(i => i.folder)), [pagedGroups]);
  const allSelected = pagedFolders.length > 0 && pagedFolders.every((f) => selectedFolders.has(f));
  const someSelected = selectedFolders.size > 0;

  const toggleSelect = useCallback((folder: string) => {
    setSelectedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(folder)) next.delete(folder); else next.add(folder);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback(() => {
    if (allSelected) {
      setSelectedFolders(new Set());
    } else {
      setSelectedFolders(new Set(pagedFolders));
    }
  }, [allSelected, pagedFolders]);

  // Actions
  const handleRestore = useCallback(async (item: ArchiveItem) => {
    try {
      const plan = await settingsApi.archiveRestorePreview(item.folder);
      if (!plan.restore_eligible) {
        toast({ type: "error", title: "Archive integrity check failed; restore was not started" });
        return;
      }
      const conflict = plan.current_exists
        ? " The current file will first be archived as a recoverable restore conflict; it will not be discarded."
        : "";
      const companionSummary = plan.companion_files.length
        ? ` Companion files reviewed: ${plan.companion_files.join(", ")}.`
        : " No companion files are affected.";
      const reviewSummary = plan.related_review_case_ids.length
        ? ` Related review cases: ${plan.related_review_case_ids.join(", ")}.`
        : "";
      const ok = await confirm({
        title: "Commit restore plan?",
        description: `Restore "${item.artist} — ${item.title}" to ${plan.current_path || plan.original_path || "the library"}.${conflict}${companionSummary}${reviewSummary} Operation ${plan.operation_id}.`,
      });
      if (!ok) return;
      const result = await restoreMutation.mutateAsync({
        folder: item.folder,
        operationId: plan.operation_id,
        conflictChoice: plan.current_exists ? "archive_current" : undefined,
      });
      toast({ type: "success", title: `Restored from archive (${result.operation_id})` });
      setSelectedFolders((prev) => { const n = new Set(prev); n.delete(item.folder); return n; });
    } catch (error: unknown) {
      const detail = axios.isAxiosError<{ detail?: string }>(error)
        ? error.response?.data?.detail
        : undefined;
      toast({ type: "error", title: detail || "Restore failed" });
    }
  }, [restoreMutation, toast, confirm]);

  const handleDelete = useCallback(async (item: ArchiveItem) => {
    const ok = await confirm({
      title: "Permanently delete?",
      description: `This will permanently delete "${item.artist} — ${item.title}" from the archive. This cannot be undone.`,
    });
    if (!ok) return;
    try {
      await deleteMutation.mutateAsync([item.folder]);
      toast({ type: "success", title: "Deleted from archive" });
      setSelectedFolders((prev) => { const n = new Set(prev); n.delete(item.folder); return n; });
    } catch {
      toast({ type: "error", title: "Delete failed" });
    }
  }, [deleteMutation, toast, confirm]);

  const handleBulkRestore = useCallback(async () => {
    const folders = Array.from(selectedFolders);
    if (folders.length === 0) return;
    const ok = await confirm({
      title: `Restore ${folders.length} item(s)?`,
      description: "This will restore all selected items from the archive back to the library.",
    });
    if (!ok) return;
    let restored = 0;
    for (const folder of folders) {
      try {
        await restoreMutation.mutateAsync(folder);
        restored++;
      } catch { /* skip */ }
    }
    toast({ type: "success", title: `Restored ${restored} item(s)` });
    setSelectedFolders(new Set());
    refetch();
  }, [selectedFolders, restoreMutation, toast, confirm, refetch]);

  const handleBulkDelete = useCallback(async () => {
    const folders = Array.from(selectedFolders);
    if (folders.length === 0) return;
    const ok = await confirm({
      title: `Delete ${folders.length} item(s)?`,
      description: "This will permanently delete all selected items from the archive. This cannot be undone.",
    });
    if (!ok) return;
    try {
      const result = await deleteMutation.mutateAsync(folders);
      toast({ type: "success", title: `Deleted ${result.deleted} item(s)` });
    } catch {
      toast({ type: "error", title: "Delete failed" });
    }
    setSelectedFolders(new Set());
  }, [selectedFolders, deleteMutation, toast, confirm]);

  const handleClearAll = useCallback(async () => {
    const ok = await confirm({
      title: "Clear entire archive?",
      description: `This will permanently delete all ${archiveCount} archived item(s). This cannot be undone.`,
    });
    if (!ok) return;
    try {
      const result = await clearMutation.mutateAsync();
      toast({ type: "success", title: `Cleared ${result.deleted} item(s)` });
    } catch {
      toast({ type: "error", title: "Clear failed" });
    }
    setSelectedFolders(new Set());
  }, [clearMutation, toast, confirm, archiveCount]);

  const handleReasonChange = useCallback((reason: ArchiveReason) => {
    patchArchivePrefs({ reasonFilter: reason });
    setReasonFilter(reason);
    setPage(1);
    setSelectedFolders(new Set());
  }, []);

  const handlePageSizeChange = useCallback((size: number) => {
    patchArchivePrefs({ pageSize: size });
    setPageSize(size);
    setPage(1);
    setSelectedFolders(new Set());
  }, []);

  return (
    <div className="mx-auto max-w-6xl p-4 md:p-6">
      <div className="mb-4">
        <h1 className="text-2xl font-bold text-text-primary">Archive</h1>
        <p className="mt-0.5 text-sm text-text-secondary">
          {archiveCount} recoverable version{archiveCount !== 1 ? "s" : ""}
        </p>
      </div>

      <div className="mb-3 border-b border-surface-border">
        <div className="flex overflow-x-auto" role="tablist" aria-label="Archive reason">
          {ARCHIVE_REASON_TABS.map((reason) => {
            const config = reason === "all"
              ? { label: "All", icon: <Archive size={14} /> }
              : REASON_CONFIG[reason];
            const count = reason === "all" ? archiveCount : (reasonCounts[reason] ?? 0);
            return (
              <button
                key={reason}
                onClick={() => handleReasonChange(reason)}
                role="tab"
                aria-selected={reasonFilter === reason}
                className={cn(
                  "relative flex shrink-0 items-center gap-1.5 px-3 py-2.5 text-sm font-medium",
                  reasonFilter === reason ? "text-accent" : "text-text-muted hover:text-text-secondary",
                )}
              >
                {config.icon} {config.label}
                <span className="rounded-full bg-surface-lighter px-1.5 text-[10px] tabular-nums">{count}</span>
                {reasonFilter === reason && <span className="absolute inset-x-0 bottom-0 h-0.5 rounded-t bg-accent" />}
              </button>
            );
          })}
        </div>
      </div>

      <div className="mb-4 flex flex-wrap items-end gap-2 rounded-lg border border-surface-border bg-surface/40 p-3">
        <label className="min-w-56 flex-1 text-[10px] uppercase tracking-wide text-text-muted">
          Search
          <span className="relative mt-1 block">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Artist or title"
              value={searchQuery}
              onChange={(event) => {
                const nextSearch = event.target.value;
                setSearchQuery(nextSearch);
                setFocusVideoId(null);
                setRouteParams((previous) => {
                  const next = new URLSearchParams(previous);
                  next.delete("focus_video_id");
                  if (nextSearch.trim()) next.set("search", nextSearch);
                  else next.delete("search");
                  return next;
                }, { replace: true });
                setPage(1);
                setSelectedFolders(new Set());
              }}
              className="input-field w-full py-1.5 pl-8 text-sm normal-case tracking-normal"
            />
          </span>
        </label>
        <ViewToggle value={viewMode} label="Archive layout" onChange={(next) => { setViewMode(next); patchArchivePrefs({ view: next }); }} />
        <button onClick={() => refetch()} className="btn-ghost btn-sm gap-1.5">
          <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} /> Refresh
        </button>
        {archiveCount > 0 && (
          <Tooltip content="Permanently delete every item in the archive">
            <button onClick={handleClearAll} disabled={clearMutation.isPending}
              className="btn-ghost btn-sm gap-1.5 text-red-400 hover:text-red-300">
              <Trash2 size={14} /> Clear archive…
            </button>
          </Tooltip>
        )}
      </div>

      {filtered.length > 0 && (
        <div className="mb-3 flex items-center gap-3 rounded-lg border border-surface-border bg-surface/50 px-3 py-2">
          <input
            type="checkbox"
            checked={allSelected}
            ref={(el) => {
              if (el) el.indeterminate = someSelected && !allSelected;
            }}
            onChange={toggleSelectAll}
            className="h-4 w-4 cursor-pointer accent-accent"
            aria-label="Select this page"
          />
          <span className="flex-1 text-sm text-text-secondary">
            {someSelected ? `${selectedFolders.size} selected on this page` : "Select this page"}
          </span>
          {someSelected && (
            <>
              <Tooltip content="Restore all selected items from archive back to the library">
                <button onClick={handleBulkRestore} disabled={restoreMutation.isPending}
                  className="btn-secondary btn-sm gap-1.5 text-emerald-400">
                  <RotateCcw size={13} /> Restore selected
                </button>
              </Tooltip>
              <Tooltip content="Permanently delete all selected items from the archive">
                <button onClick={handleBulkDelete} disabled={deleteMutation.isPending}
                  className="btn-ghost btn-sm gap-1.5 text-red-400 hover:text-red-300">
                  <Trash2 size={13} /> Delete selected
                </button>
              </Tooltip>
            </>
          )}
        </div>
      )}

      {/* Item list — grouped by library track */}
      {isLoading ? (
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => (
            <Skeleton key={i} className="h-32 rounded-xl" />
          ))}
        </div>
      ) : isError ? (
        <ErrorState message="Failed to load the archive" onRetry={refetch} />
      ) : grouped.length === 0 ? (
        <div className="card text-center py-12">
          <Archive className="w-12 h-12 mx-auto mb-3 opacity-30" />
          <p className="text-sm font-medium text-text-primary">No archived items</p>
          <p className="text-xs mt-1 text-text-muted">
            {reasonFilter === "all"
              ? "Videos archived via redownload or the editor will appear here."
              : `No items with reason "${REASON_CONFIG[reasonFilter]?.label || reasonFilter}".`}
          </p>
        </div>
      ) : (
        <>
          <div className={cn(viewMode === "grid" ? "grid gap-3 xl:grid-cols-2" : "space-y-3")}>
            {pagedGroups.map((group) => (
              <div
                key={group.key}
                ref={group.video_id === focusVideoId ? focusedEntryRef : undefined}
                className={cn(
                  "overflow-hidden rounded-xl border bg-surface/70",
                  group.video_id === focusVideoId
                    ? "border-accent/70 ring-1 ring-accent/30"
                    : "border-surface-border",
                )}
              >
                <div className="flex">
                  {/* Large poster — clickable to library */}
                  <div
                    className={cn(
                      "w-[140px] flex-shrink-0 bg-surface-lighter flex items-center justify-center",
                      group.video_id && "cursor-pointer hover:opacity-80 transition-opacity",
                    )}
                    onClick={() => group.video_id && navigate(`/video/${group.video_id}`)}
                    title={group.video_id ? "View in library" : undefined}
                  >
                    {group.video_id ? (
                      <img
                        src={playbackApi.posterUrl(group.video_id)}
                        alt=""
                        className="w-full h-full object-cover"
                        onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                      />
                    ) : (
                      <div className="flex flex-col items-center gap-1 text-text-muted/30">
                        <Archive size={32} />
                        <span className="text-[9px]">Orphan</span>
                      </div>
                    )}
                  </div>

                  {/* Right side — track info + edit timeline */}
                  <div className="flex-1 min-w-0">
                    {/* Track header */}
                    <div className="flex items-center gap-2 px-4 py-2.5 border-b border-surface-border bg-surface/50">
                      <div className="flex-1 min-w-0">
                        <span className="text-sm font-semibold text-text-primary truncate block">
                          {group.artist && <span className="text-accent">{group.artist}</span>}
                          {group.artist && group.title && " — "}
                          {group.title}
                        </span>
                        <span className="text-[11px] text-text-muted">
                          {group.items.length} edit{group.items.length !== 1 ? "s" : ""}
                          {group.video_id && <> · Library #{group.video_id}</>}
                        </span>
                      </div>
                    </div>

                    {/* Edit timeline — each archive entry */}
                    <div className="divide-y divide-surface-border/50">
                      {group.items.map((item, idx) => (
                        <div
                          key={item.folder}
                          className={cn(
                            "flex items-center gap-3 px-4 py-2 transition-colors hover:bg-surface-hover/30",
                            selectedFolders.has(item.folder) && "bg-accent/5",
                          )}
                        >
                          {/* Checkbox */}
                          <input
                            type="checkbox"
                            checked={selectedFolders.has(item.folder)}
                            onChange={() => toggleSelect(item.folder)}
                            className="accent-accent w-3.5 h-3.5 cursor-pointer flex-shrink-0"
                          />

                          {/* Timeline indicator */}
                          <div className="flex flex-col items-center flex-shrink-0 w-4">
                            <div className={cn(
                              "w-2 h-2 rounded-full",
                              idx === 0 ? "bg-accent" : "bg-text-muted/30",
                            )} />
                            {idx < group.items.length - 1 && (
                              <div className="w-px h-full bg-surface-border absolute" />
                            )}
                          </div>

                          {/* Reason badge */}
                          <ReasonBadge reason={item.reason} />

                          {/* Details */}
                          <div className="flex items-center gap-2 text-[11px] text-text-muted flex-1 min-w-0">
                            {item.file_size_bytes > 0 && <span>{formatBytes(item.file_size_bytes)}</span>}
                            {item.archived_at && <span className="text-text-muted/70">{timeAgo(item.archived_at)}</span>}
                            {item.operation_id && <span title={item.operation_id}>Op {item.operation_id.slice(0, 8)}</span>}
                            {item.original_path && <span className="truncate" title={`Original/current path: ${item.original_path}`}>{item.original_path}</span>}
                            {(item.checksum_sha256 || item.checksum_md5) && <span title={item.checksum_sha256 || item.checksum_md5 || ""}>Checksum verified</span>}
                            {item.restore_eligible === false && <span className="text-red-300">Not restorable</span>}
                          </div>

                          {/* Actions */}
                          <div className="flex items-center gap-0.5 flex-shrink-0">
                            <Tooltip content="Compare side-by-side">
                              <button
                                onClick={() => setComparisonItem(item)}
                                className="btn-ghost p-1 text-blue-400 hover:text-blue-300"
                              >
                                <Maximize2 size={13} />
                              </button>
                            </Tooltip>
                            <Tooltip content="Open source folder">
                              <button
                                onClick={() => settingsApi.openDirectory(item.folder)}
                                className="btn-ghost p-1 text-amber-400 hover:text-amber-300"
                              >
                                <FolderOpen size={13} />
                              </button>
                            </Tooltip>
                            <Tooltip content="Restore to library">
                              <button
                                onClick={() => handleRestore(item)}
                                disabled={restoreMutation.isPending}
                                className="btn-ghost p-1 text-emerald-400 hover:text-emerald-300"
                              >
                                <RotateCcw size={13} />
                              </button>
                            </Tooltip>
                            <Tooltip content="Delete permanently">
                              <button
                                onClick={() => handleDelete(item)}
                                disabled={deleteMutation.isPending}
                                className="btn-ghost p-1 text-red-400 hover:text-red-300"
                              >
                                <Trash2 size={13} />
                              </button>
                            </Tooltip>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <Pagination
            page={page}
            totalPages={totalPages}
            pageSize={pageSize}
            total={matchingCount}
            onPageChange={(next) => { setPage(next); setSelectedFolders(new Set()); }}
            onPageSizeChange={handlePageSizeChange}
          />
        </>
      )}

      {/* Comparison player overlay */}
      {comparisonItem && (
        <ComparisonPlayer
          archiveItem={comparisonItem}
          onClose={() => setComparisonItem(null)}
        />
      )}

      {dialog}
    </div>
  );
}
