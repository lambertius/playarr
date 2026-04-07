import { useState, useMemo, useCallback, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
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
import { cn, formatBytes, timeAgo } from "@/lib/utils";
import type { ArchiveItem } from "@/types";

// ── Reason config ───────────────────────────────────────
type ArchiveReason = "all" | "redownload" | "trim" | "crop" | "both";

const REASON_CONFIG: Record<string, { label: string; icon: React.ReactNode; color: string; badgeColor: string }> = {
  redownload: { label: "Redownload", icon: <Download size={12} />, color: "bg-blue-500/10 text-blue-400", badgeColor: "bg-blue-500/15 text-blue-400 border-blue-500/20" },
  trim: { label: "Trim", icon: <Scissors size={12} />, color: "bg-orange-500/10 text-orange-400", badgeColor: "bg-orange-500/15 text-orange-400 border-orange-500/20" },
  crop: { label: "Crop", icon: <Film size={12} />, color: "bg-purple-500/10 text-purple-400", badgeColor: "bg-purple-500/15 text-purple-400 border-purple-500/20" },
  both: { label: "Trim + Crop", icon: <Scissors size={12} />, color: "bg-pink-500/10 text-pink-400", badgeColor: "bg-pink-500/15 text-pink-400 border-pink-500/20" },
};

/** Normalize legacy "edit" reason to "crop" */
function normalizeReason(reason: string): string {
  return reason === "edit" ? "crop" : reason;
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
function FilterPill({ icon, label, value, active, color, onClick, selected }: {
  icon: React.ReactNode; label: string; value: number; active?: boolean; color: string;
  onClick?: () => void; selected?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-full border text-xs font-medium transition-all duration-150 cursor-pointer whitespace-nowrap",
        selected
          ? `${color} border-current/30 ring-1 ring-current/20 shadow-md`
          : active
            ? `border-surface-border text-text-secondary hover:border-current/40 hover:shadow-sm`
            : "bg-surface/40 border-surface-border text-text-muted/50 hover:border-text-muted/30 hover:bg-surface-hover/40",
      )}
    >
      <span className="opacity-70">{icon}</span>
      <span>{label}</span>
      <span className={cn(
        "min-w-[1.25rem] px-1 py-0.5 rounded-full text-[10px] font-bold tabular-nums leading-none text-center",
        selected ? "bg-current/20" : active ? `${color}` : "bg-surface-border/50",
      )}>
        {value}
      </span>
    </button>
  );
}

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
  const syncLoop = useCallback(() => {
    if (!archiveRef.current || !libraryRef.current) return;
    const drift = libraryRef.current.currentTime - archiveRef.current.currentTime;
    if (Math.abs(drift) > 0.05) {
      libraryRef.current.currentTime = archiveRef.current.currentTime;
    }
    setCurrentTime(archiveRef.current.currentTime);
    rafRef.current = requestAnimationFrame(syncLoop);
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
  const { toast } = useToast();
  const { confirm, dialog } = useConfirm();
  const { data: items, isLoading, refetch } = useArchiveItems();
  const restoreMutation = useArchiveRestore();
  const deleteMutation = useArchiveDelete();
  const clearMutation = useArchiveClear();

  const [reasonFilter, setReasonFilter] = useState<ArchiveReason>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedFolders, setSelectedFolders] = useState<Set<string>>(new Set());
  const [comparisonItem, setComparisonItem] = useState<ArchiveItem | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(() => {
    const saved = localStorage.getItem("archive_page_size");
    return saved ? Number(saved) : 25;
  });

  // Filtered items
  const filtered = useMemo(() => {
    if (!items) return [];
    let result = items;
    if (reasonFilter !== "all") {
      result = result.filter((i) => normalizeReason(i.reason) === reasonFilter);
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter((i) =>
        i.artist.toLowerCase().includes(q) || i.title.toLowerCase().includes(q)
      );
    }
    return result;
  }, [items, reasonFilter, searchQuery]);

  // Reason counts — normalize "edit" → "crop"
  const reasonCounts = useMemo(() => {
    if (!items) return {};
    const counts: Record<string, number> = {};
    for (const item of items) {
      const r = normalizeReason(item.reason);
      counts[r] = (counts[r] || 0) + 1;
    }
    return counts;
  }, [items]);

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

  const allCount = items?.length ?? 0;
  const totalPages = Math.max(1, Math.ceil(grouped.length / pageSize));
  const pagedGroups = useMemo(() => {
    const start = (page - 1) * pageSize;
    return grouped.slice(start, start + pageSize);
  }, [grouped, page, pageSize]);

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
    const ok = await confirm({
      title: "Restore from archive?",
      description: `This will restore "${item.artist} — ${item.title}" from the archive back to the library, replacing the current file if one exists.`,
    });
    if (!ok) return;
    try {
      await restoreMutation.mutateAsync(item.folder);
      toast({ type: "success", title: "Restored from archive" });
      setSelectedFolders((prev) => { const n = new Set(prev); n.delete(item.folder); return n; });
    } catch (e: any) {
      toast({ type: "error", title: e?.response?.data?.detail || "Restore failed" });
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
      description: `This will permanently delete all ${allCount} archived item(s). This cannot be undone.`,
    });
    if (!ok) return;
    try {
      const result = await clearMutation.mutateAsync();
      toast({ type: "success", title: `Cleared ${result.deleted} item(s)` });
    } catch {
      toast({ type: "error", title: "Clear failed" });
    }
    setSelectedFolders(new Set());
  }, [clearMutation, toast, confirm, allCount]);

  const handleReasonChange = useCallback((reason: ArchiveReason) => {
    setReasonFilter(reason);
    setPage(1);
    setSelectedFolders(new Set());
  }, []);

  const handlePageSizeChange = useCallback((size: number) => {
    localStorage.setItem("archive_page_size", String(size));
    setPageSize(size);
    setPage(1);
  }, []);

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Archive</h1>
          <p className="text-sm text-text-secondary mt-0.5">
            {allCount} archived item{allCount !== 1 ? "s" : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
            <input
              type="text"
              placeholder="Search artist, title…"
              value={searchQuery}
              onChange={(e) => { setSearchQuery(e.target.value); setPage(1); }}
              className="input-field pl-8 pr-3 py-1.5 text-sm w-56"
            />
          </div>
          <button onClick={() => refetch()} className="btn-ghost btn-sm gap-1.5">
            <RefreshCw size={14} /> Refresh
          </button>
          {allCount > 0 && (
            <Tooltip content="Permanently delete all items in the archive">
              <button onClick={handleClearAll} disabled={clearMutation.isPending}
                className="btn-ghost btn-sm gap-1.5 text-red-400 hover:text-red-300">
                <Trash2 size={14} /> Clear All
              </button>
            </Tooltip>
          )}
        </div>
      </div>

      {/* Reason filter pills */}
      <div className="flex flex-wrap gap-1.5 mb-5">
        <FilterPill
          icon={<Archive size={14} />}
          label="All"
          value={allCount}
          active={allCount > 0}
          color="bg-blue-500/10 text-blue-400"
          onClick={() => handleReasonChange("all")}
          selected={reasonFilter === "all"}
        />
        {(["redownload", "trim", "crop", "both"] as const).map((reason) => {
          const config = REASON_CONFIG[reason];
          const count = reasonCounts[reason] ?? 0;
          return (
            <FilterPill
              key={reason}
              icon={config.icon}
              label={config.label}
              value={count}
              active={count > 0}
              color={config.color}
              onClick={() => handleReasonChange(reason)}
              selected={reasonFilter === reason}
            />
          );
        })}
      </div>

      {/* Bulk action bar */}
      {someSelected && (
        <div className="flex items-center gap-3 bg-surface-secondary/50 border border-surface-border rounded-lg px-4 py-2 mb-3">
          <input
            type="checkbox"
            checked={allSelected}
            ref={(el) => {
              if (el) el.indeterminate = someSelected && !allSelected;
            }}
            onChange={toggleSelectAll}
            className="accent-accent w-4 h-4 cursor-pointer"
          />
          <span className="text-sm text-text-secondary flex-1">
            {selectedFolders.size} selected
          </span>
          <Tooltip content="Restore all selected items from archive back to the library">
            <button onClick={handleBulkRestore} disabled={restoreMutation.isPending}
              className="btn-ghost btn-sm gap-1 text-emerald-400 hover:text-emerald-300 text-xs">
              <RotateCcw size={13} /> Restore
            </button>
          </Tooltip>
          <Tooltip content="Permanently delete all selected items from the archive">
            <button onClick={handleBulkDelete} disabled={deleteMutation.isPending}
              className="btn-ghost btn-sm gap-1 text-red-400 hover:text-red-300 text-xs">
              <Trash2 size={13} /> Delete
            </button>
          </Tooltip>
        </div>
      )}

      {/* Select all header (when no selection) */}
      {!someSelected && filtered.length > 0 && (
        <div className="flex items-center gap-3 bg-surface-secondary/50 border border-surface-border rounded-lg px-4 py-2 mb-3">
          <input
            type="checkbox"
            checked={false}
            onChange={toggleSelectAll}
            className="accent-accent w-4 h-4 cursor-pointer"
          />
          <span className="text-sm text-text-secondary flex-1">Select all</span>
        </div>
      )}

      {/* Item list — grouped by library track */}
      {isLoading ? (
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="card h-32 animate-pulse bg-surface-lighter rounded-lg" />
          ))}
        </div>
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
          <div className="space-y-3">
            {pagedGroups.map((group) => (
              <div key={group.key} className="card p-0 overflow-hidden">
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
            total={grouped.length}
            onPageChange={setPage}
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
