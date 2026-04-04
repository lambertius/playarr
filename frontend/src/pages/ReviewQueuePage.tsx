import { useState, useMemo, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  Search, ChevronLeft, ChevronRight, CheckCircle2, XCircle,
  GitCompare, FolderInput, Link2, ClipboardList,
  Check, X, Tag, RefreshCw, ChevronDown, LayoutList, FileEdit,
  Trash2, ScanSearch, FolderSearch,
} from "lucide-react";
import type { ReviewParams, ReviewItem, DuplicateVideoSummary } from "@/types";
import {
  useReviewQueue, useExportKodi, useApproveReview, useDismissReview,
  useSetReviewVersion, useBatchApproveReview, useBatchDismissReview,
  useScanRenames, useApplyRename, useBatchApplyRename,
  useBatchDeleteReview, useBatchScrapeReview,
} from "@/hooks/queries";
import { useToast } from "@/components/Toast";
import { useConfirm } from "@/components/ConfirmDialog";
import { VersionBadge } from "@/components/Badges";
import { Tooltip } from "@/components/Tooltip";
import { cn, formatBytes, timeAgo } from "@/lib/utils";
import { ScrapeOptionsModal, type ScrapeOptions } from "@/components/ScrapeOptionsModal";

// ── Types ───────────────────────────────────────────────
type ReviewCategory = "all" | "version_detection" | "duplicate" | "url_import_error" | "import_error" | "manual_review" | "rename" | "scanned";

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];

// ── Category config ─────────────────────────────────────
const CATEGORY_CONFIG: Record<ReviewCategory, {
  label: string;
  icon: React.ReactNode;
  color: string;
  tooltip: string;
}> = {
  all: {
    label: "All",
    icon: <LayoutList size={16} />,
    color: "bg-blue-500/10 text-blue-400",
    tooltip: "All items currently flagged for review across all categories",
  },
  version_detection: {
    label: "Version Detection",
    icon: <Tag size={16} />,
    color: "bg-orange-500/10 text-orange-400",
    tooltip: "Items auto-categorised as Live, Cover, Alternate, etc. Approve to confirm, change the category, or dismiss to clear the flag.",
  },
  duplicate: {
    label: "Suspected Duplicates",
    icon: <GitCompare size={16} />,
    color: "bg-purple-500/10 text-purple-400",
    tooltip: "Tracks the system suspects are duplicates of existing items. Compare video data side-by-side, then choose to keep one, both, or assign a version type.",
  },
  url_import_error: {
    label: "URL Import Alerts",
    icon: <Link2 size={16} />,
    color: "bg-red-500/10 text-red-400",
    tooltip: "Non-fatal issues during URL import (download succeeded but metadata scraping or AI enrichment had errors). You can re-run metadata scraping, redownload, or dismiss.",
  },
  import_error: {
    label: "Library Import Alerts",
    icon: <FolderInput size={16} />,
    color: "bg-amber-500/10 text-amber-400",
    tooltip: "Non-fatal issues from library imports. Tracks imported successfully but had metadata or processing warnings. Take individual or bulk actions to resolve.",
  },
  manual_review: {
    label: "Manual Review",
    icon: <ClipboardList size={16} />,
    color: "bg-cyan-500/10 text-cyan-400",
    tooltip: "Items imported without scraping (e.g. library folder add or AI auto-import with manual review). Items with complete locked XMLs or completed AI auto-imports are excluded.",
  },
  rename: {
    label: "Rename",
    icon: <FileEdit size={16} />,
    color: "bg-teal-500/10 text-teal-400",
    tooltip: "Videos whose file or folder name doesn\u2019t match the current naming convention. Apply rename to fix, or dismiss to ignore.",
  },
  scanned: {
    label: "Scanned",
    icon: <FolderSearch size={16} />,
    color: "bg-lime-500/10 text-lime-400",
    tooltip: "Untracked files found in the library directory and imported via scan. Scrape metadata to enrich, or delete if unwanted.",
  },
};

// ── Version type options ────────────────────────────────
const VERSION_TYPE_OPTIONS = [
  { value: "normal", label: "Normal" },
  { value: "cover", label: "Cover" },
  { value: "live", label: "Live" },
  { value: "alternate", label: "Alternate" },
];

// ── Status badge ────────────────────────────────────────
function ReviewStatusBadge({ status }: { status: string }) {
  if (status === "needs_human_review") {
    return <span className="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-yellow-500/15 text-yellow-400">Human Review</span>;
  }
  if (status === "needs_ai_review") {
    return <span className="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-blue-500/15 text-blue-400">AI Review</span>;
  }
  return <span className="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-surface-hover text-text-secondary">{status}</span>;
}

// ── Category badge ──────────────────────────────────────
function CategoryBadge({ category }: { category: string | null }) {
  const cat = (category || "version_detection") as ReviewCategory;
  const config = CATEGORY_CONFIG[cat];
  if (!config || cat === "all") return null;
  return (
    <Tooltip content={config.tooltip}>
      <span className={cn("inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded-full", config.color)}>
        {config.label}
      </span>
    </Tooltip>
  );
}

// ── Reason parser ───────────────────────────────────────
function parseReviewReason(reason: string | null | undefined): { label: string; details: string }[] {
  if (!reason) return [];
  return reason.split("; ").map((segment) => {
    const colonIdx = segment.indexOf(": ");
    if (colonIdx === -1) return { label: "Issue", details: segment };
    return { label: segment.slice(0, colonIdx), details: segment.slice(colonIdx + 2) };
  });
}

// ── Format helpers ──────────────────────────────────────
function formatDuration(seconds: number | null | undefined): string {
  if (!seconds) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function formatBitrate(bps: number | null | undefined): string {
  if (!bps) return "—";
  if (bps >= 1_000_000) return `${(bps / 1_000_000).toFixed(1)} Mbps`;
  return `${Math.round(bps / 1000)} kbps`;
}

// ── Stat card ───────────────────────────────────────────
function StatCard({ icon, label, value, active, color, tooltip, onClick, selected }: {
  icon: React.ReactNode; label: string; value: number; active?: boolean; color: string;
  tooltip: string; onClick?: () => void; selected?: boolean;
}) {
  return (
    <Tooltip content={tooltip}>
      <button
        onClick={onClick}
        className={cn(
          "flex items-center gap-2.5 px-3 py-2 rounded-lg border transition-all duration-150 text-left w-full cursor-pointer",
          selected
            ? `${color} border-current/30 ring-1 ring-current/20 shadow-md`
            : active
              ? `${color} border-current/20 hover:border-current/40 hover:shadow-md hover:brightness-125 hover:scale-[1.02]`
              : "bg-surface/50 border-surface-border text-text-muted hover:border-text-muted/40 hover:bg-surface-hover/60 hover:shadow-md hover:scale-[1.02]",
        )}
      >
        <span className="opacity-70">{icon}</span>
        <div className="min-w-0">
          <div className="text-lg font-bold tabular-nums leading-tight">{value}</div>
          <div className="text-[10px] uppercase tracking-wider opacity-70 whitespace-nowrap">{label}</div>
        </div>
      </button>
    </Tooltip>
  );
}

// ── Pagination ──────────────────────────────────────────
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

// ── Version type dropdown ───────────────────────────────
function VersionTypeDropdown({ currentType, onSelect }: {
  currentType?: string; onSelect: (vt: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <Tooltip content="Change the version type for this track (e.g. Cover, Live, Alternate)">
        <button
          onClick={(e) => { e.stopPropagation(); setOpen(!open); }}
          className="btn-ghost btn-sm text-xs gap-1"
        >
          <Tag size={12} /> Reclassify <ChevronDown size={10} />
        </button>
      </Tooltip>
      {open && (
        <div className="absolute right-0 bottom-full mb-1 bg-surface-light border border-surface-border rounded-lg shadow-xl z-50 min-w-[140px] py-1">
          {VERSION_TYPE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={(e) => {
                e.stopPropagation();
                onSelect(opt.value);
                setOpen(false);
              }}
              className={cn(
                "w-full text-left px-3 py-1.5 text-xs hover:bg-surface-hover transition-colors",
                currentType === opt.value ? "text-accent font-medium" : "text-text-secondary",
              )}
            >
              {opt.label}
              {currentType === opt.value && <Check size={10} className="inline ml-2" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// ██ Main Page Component
// ═══════════════════════════════════════════════════════════

export default function ReviewQueuePage() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const { confirm, dialog } = useConfirm();

  const [categoryFilter, setCategoryFilter] = useState<ReviewCategory>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(() => {
    const saved = localStorage.getItem("review_page_size");
    return saved ? Number(saved) : 25;
  });

  const params: ReviewParams = useMemo(() => ({
    category: categoryFilter === "all" ? undefined : categoryFilter,
    q: searchQuery || undefined,
    sort: "updated_desc",
    page,
    page_size: pageSize,
  }), [categoryFilter, searchQuery, page, pageSize]);

  const { data, isLoading, refetch } = useReviewQueue(params);
  const exportMutation = useExportKodi();
  const approveMutation = useApproveReview();
  const dismissMutation = useDismissReview();
  const setVersionMutation = useSetReviewVersion();
  const batchApproveMutation = useBatchApproveReview();
  const batchDismissMutation = useBatchDismissReview();
  const scanRenamesMutation = useScanRenames();
  const applyRenameMutation = useApplyRename();
  const batchApplyRenameMutation = useBatchApplyRename();
  const batchDeleteMutation = useBatchDeleteReview();
  const batchScrapeMutation = useBatchScrapeReview();
  const [scrapeModalOpen, setScrapeModalOpen] = useState(false);

  const items = useMemo(() => data?.items ?? [], [data?.items]);
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const categoryCounts = data?.category_counts ?? {};

  // Aggregate total for "all" stat card
  const allCount = useMemo(() =>
    Object.values(categoryCounts).reduce((sum, n) => sum + n, 0),
    [categoryCounts],
  );

  // Selection helpers
  const allSelected = items.length > 0 && items.every((i) => selectedIds.has(i.video_id));
  const someSelected = selectedIds.size > 0;

  const toggleSelect = useCallback((id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback(() => {
    if (allSelected) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(items.map((i) => i.video_id)));
    }
  }, [allSelected, items]);

  // Actions
  const handleApprove = useCallback(async (videoId: number) => {
    await approveMutation.mutateAsync(videoId);
    toast({ type: "success", title: "Approved" });
    setSelectedIds((prev) => { const n = new Set(prev); n.delete(videoId); return n; });
  }, [approveMutation, toast]);

  const handleDismiss = useCallback(async (videoId: number) => {
    const ok = await confirm({ title: "Dismiss review?", description: "This will clear the review flag and remove it from the queue." });
    if (!ok) return;
    await dismissMutation.mutateAsync(videoId);
    toast({ type: "success", title: "Dismissed" });
    setSelectedIds((prev) => { const n = new Set(prev); n.delete(videoId); return n; });
  }, [dismissMutation, toast, confirm]);

  const handleDelete = useCallback(async (videoId: number) => {
    const ok = await confirm({ title: "Delete this video?", description: "This will permanently delete the video and its files from disk. This cannot be undone." });
    if (!ok) return;
    await batchDeleteMutation.mutateAsync([videoId]);
    toast({ type: "success", title: "Deleted" });
    setSelectedIds((prev) => { const n = new Set(prev); n.delete(videoId); return n; });
  }, [batchDeleteMutation, toast, confirm]);

  const handleSetVersion = useCallback(async (videoId: number, versionType: string, approve: boolean = true) => {
    await setVersionMutation.mutateAsync({ videoId, versionType, approve });
    toast({ type: "success", title: approve ? `Set to ${versionType} and approved` : `Reclassified as ${versionType}` });
    if (approve) setSelectedIds((prev) => { const n = new Set(prev); n.delete(videoId); return n; });
  }, [setVersionMutation, toast]);

  const handleBatchApprove = useCallback(async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    const ok = await confirm({ title: `Approve ${ids.length} item(s)?`, description: "This will accept their current classifications and remove them from review." });
    if (!ok) return;
    await batchApproveMutation.mutateAsync(ids);
    toast({ type: "success", title: `Approved ${ids.length} item(s)` });
    setSelectedIds(new Set());
  }, [selectedIds, batchApproveMutation, toast, confirm]);

  const handleBatchDismiss = useCallback(async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    const ok = await confirm({ title: `Dismiss ${ids.length} item(s)?`, description: "This will clear all review flags and remove them from the queue." });
    if (!ok) return;
    await batchDismissMutation.mutateAsync(ids);
    toast({ type: "success", title: `Dismissed ${ids.length} item(s)` });
    setSelectedIds(new Set());
  }, [selectedIds, batchDismissMutation, toast, confirm]);

  const handleScanRenames = useCallback(async () => {
    const result = await scanRenamesMutation.mutateAsync();
    toast({ type: "success", title: result.flagged > 0 ? `Found ${result.flagged} item(s) to rename` : "All files match naming convention" });
  }, [scanRenamesMutation, toast]);

  const handleApplyRename = useCallback(async (videoId: number) => {
    await applyRenameMutation.mutateAsync(videoId);
    toast({ type: "success", title: "Renamed successfully" });
    setSelectedIds((prev) => { const n = new Set(prev); n.delete(videoId); return n; });
  }, [applyRenameMutation, toast]);

  const handleBatchApplyRename = useCallback(async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    const ok = await confirm({ title: `Rename ${ids.length} item(s)?`, description: "This will rename all selected items to match the naming convention." });
    if (!ok) return;
    const result = await batchApplyRenameMutation.mutateAsync(ids);
    toast({ type: "success", title: `Renamed ${result.renamed} item(s)${result.failed > 0 ? `, ${result.failed} failed` : ""}` });
    setSelectedIds(new Set());
  }, [selectedIds, batchApplyRenameMutation, toast, confirm]);

  const handleBatchDelete = useCallback(async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    const ok = await confirm({ title: `Delete ${ids.length} item(s)?`, description: "This will permanently delete the selected videos and their files from disk. This cannot be undone." });
    if (!ok) return;
    const result = await batchDeleteMutation.mutateAsync(ids);
    toast({ type: "success", title: `Deleted ${result.count} item(s)${result.errors?.length ? `, ${result.errors.length} failed` : ""}` });
    setSelectedIds(new Set());
  }, [selectedIds, batchDeleteMutation, toast, confirm]);

  const handleBatchScrape = useCallback(async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    setScrapeModalOpen(true);
  }, [selectedIds]);

  const handleScrapeConfirm = useCallback(async (options: ScrapeOptions) => {
    const ids = Array.from(selectedIds);
    setScrapeModalOpen(false);
    const result = await batchScrapeMutation.mutateAsync({ videoIds: ids, options });
    toast({ type: "success", title: result.message });
    setSelectedIds(new Set());
  }, [selectedIds, batchScrapeMutation, toast]);

  const handleExport = useCallback(async () => {
    try {
      const result = await exportMutation.mutateAsync({});
      toast({ type: "success", title: result.message });
    } catch {
      toast({ type: "error", title: "Export failed" });
    }
  }, [exportMutation, toast]);

  // Reset page on filter changes
  const handleCategoryChange = useCallback((cat: ReviewCategory) => {
    setCategoryFilter(cat);
    setPage(1);
    setSelectedIds(new Set());
  }, []);

  const handlePageSizeChange = useCallback((size: number) => {
    localStorage.setItem("review_page_size", String(size));
    setPageSize(size);
    setPage(1);
  }, []);

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Review Queue</h1>
          <p className="text-sm text-text-secondary mt-0.5">
            {allCount} item{allCount !== 1 ? "s" : ""} need{allCount === 1 ? "s" : ""} review
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
          <Tooltip content="Scan library for files that don't match the naming convention">
            <button onClick={handleScanRenames} disabled={scanRenamesMutation.isPending} className="btn-ghost btn-sm gap-1.5">
              <FileEdit size={14} /> {scanRenamesMutation.isPending ? "Scanning…" : "Scan Renames"}
            </button>
          </Tooltip>
          <Tooltip content="Export all library items to Kodi-compatible NFO files">
            <button onClick={handleExport} className="btn-ghost btn-sm" disabled={exportMutation.isPending}>
              Export to Kodi
            </button>
          </Tooltip>
        </div>
      </div>

      {/* Category stat cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2.5 mb-5">
        <StatCard
          icon={<LayoutList size={16} />}
          label="All"
          value={allCount}
          active={allCount > 0}
          color="bg-blue-500/10 text-blue-400"
          tooltip={CATEGORY_CONFIG.all.tooltip}
          onClick={() => handleCategoryChange("all")}
          selected={categoryFilter === "all"}
        />
        <StatCard
          icon={<Tag size={16} />}
          label="Version"
          value={categoryCounts.version_detection ?? 0}
          active={(categoryCounts.version_detection ?? 0) > 0}
          color="bg-orange-500/10 text-orange-400"
          tooltip={CATEGORY_CONFIG.version_detection.tooltip}
          onClick={() => handleCategoryChange("version_detection")}
          selected={categoryFilter === "version_detection"}
        />
        <StatCard
          icon={<GitCompare size={16} />}
          label="Duplicates"
          value={categoryCounts.duplicate ?? 0}
          active={(categoryCounts.duplicate ?? 0) > 0}
          color="bg-purple-500/10 text-purple-400"
          tooltip={CATEGORY_CONFIG.duplicate.tooltip}
          onClick={() => handleCategoryChange("duplicate")}
          selected={categoryFilter === "duplicate"}
        />
        <StatCard
          icon={<Link2 size={16} />}
          label="URL Import"
          value={categoryCounts.url_import_error ?? 0}
          active={(categoryCounts.url_import_error ?? 0) > 0}
          color="bg-red-500/10 text-red-400"
          tooltip={CATEGORY_CONFIG.url_import_error.tooltip}
          onClick={() => handleCategoryChange("url_import_error")}
          selected={categoryFilter === "url_import_error"}
        />
        <StatCard
          icon={<FolderInput size={16} />}
          label="Lib Import"
          value={categoryCounts.import_error ?? 0}
          active={(categoryCounts.import_error ?? 0) > 0}
          color="bg-amber-500/10 text-amber-400"
          tooltip={CATEGORY_CONFIG.import_error.tooltip}
          onClick={() => handleCategoryChange("import_error")}
          selected={categoryFilter === "import_error"}
        />
        <StatCard
          icon={<ClipboardList size={16} />}
          label="Manual"
          value={categoryCounts.manual_review ?? 0}
          active={(categoryCounts.manual_review ?? 0) > 0}
          color="bg-cyan-500/10 text-cyan-400"
          tooltip={CATEGORY_CONFIG.manual_review.tooltip}
          onClick={() => handleCategoryChange("manual_review")}
          selected={categoryFilter === "manual_review"}
        />
        <StatCard
          icon={<FileEdit size={16} />}
          label="Rename"
          value={categoryCounts.rename ?? 0}
          active={(categoryCounts.rename ?? 0) > 0}
          color="bg-teal-500/10 text-teal-400"
          tooltip={CATEGORY_CONFIG.rename.tooltip}
          onClick={() => handleCategoryChange("rename")}
          selected={categoryFilter === "rename"}
        />
        <StatCard
          icon={<FolderSearch size={16} />}
          label="Scanned"
          value={categoryCounts.scanned ?? 0}
          active={(categoryCounts.scanned ?? 0) > 0}
          color="bg-lime-500/10 text-lime-400"
          tooltip={CATEGORY_CONFIG.scanned.tooltip}
          onClick={() => handleCategoryChange("scanned")}
          selected={categoryFilter === "scanned"}
        />
      </div>

      {/* Bulk action bar */}
      {someSelected && (
        <div className="flex items-center gap-3 bg-accent/10 border border-accent/20 rounded-lg px-4 py-2.5 mb-4">
          <span className="text-sm text-accent font-medium flex-1">
            {selectedIds.size} item{selectedIds.size !== 1 ? "s" : ""} selected
          </span>
          <Tooltip content="Approve all selected items — accepts their current classifications and removes them from review">
            <button onClick={handleBatchApprove} disabled={batchApproveMutation.isPending}
              className="btn-ghost btn-sm gap-1 text-emerald-400 hover:text-emerald-300">
              <CheckCircle2 size={14} /> Approve All
            </button>
          </Tooltip>
          <Tooltip content="Dismiss all selected items — clears review flags and removes them from the queue">
            <button onClick={handleBatchDismiss} disabled={batchDismissMutation.isPending}
              className="btn-ghost btn-sm gap-1 text-red-400 hover:text-red-300">
              <XCircle size={14} /> Dismiss All
            </button>
          </Tooltip>
          {categoryFilter === "rename" && (
            <Tooltip content="Apply naming convention rename to all selected items">
              <button onClick={handleBatchApplyRename} disabled={batchApplyRenameMutation.isPending}
                className="btn-ghost btn-sm gap-1 text-teal-400 hover:text-teal-300">
                <FileEdit size={14} /> Rename All
              </button>
            </Tooltip>
          )}
          <Tooltip content="Queue metadata scrape for all selected items — fetches artist, album, genres, artwork from MusicBrainz and Wikipedia">
            <button onClick={handleBatchScrape} disabled={batchScrapeMutation.isPending}
              className="btn-ghost btn-sm gap-1 text-blue-400 hover:text-blue-300">
              <ScanSearch size={14} /> Scrape All
            </button>
          </Tooltip>
          <Tooltip content="Permanently delete all selected items and their files from disk">
            <button onClick={handleBatchDelete} disabled={batchDeleteMutation.isPending}
              className="btn-ghost btn-sm gap-1 text-red-400 hover:text-red-300">
              <Trash2 size={14} /> Delete All
            </button>
          </Tooltip>
          <button onClick={() => setSelectedIds(new Set())} className="btn-ghost btn-sm text-xs text-text-muted">
            Clear Selection
          </button>
        </div>
      )}

      {/* Category description banner */}
      {categoryFilter !== "all" && (
        <div className="flex items-center gap-2 bg-surface-lighter border border-surface-border rounded-lg px-4 py-2 mb-4 text-xs text-text-secondary">
          <span className="opacity-60">{CATEGORY_CONFIG[categoryFilter].icon}</span>
          {CATEGORY_CONFIG[categoryFilter].tooltip}
        </div>
      )}

      {/* Item list */}
      {isLoading && items.length === 0 ? (
        <div className="space-y-2">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="card h-20 animate-pulse bg-surface-lighter rounded-lg" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="card text-center py-12">
          <svg className="w-12 h-12 mx-auto mb-3 opacity-30" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
              d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <p className="text-sm font-medium text-text-primary">All clear!</p>
          <p className="text-xs mt-1 text-text-muted">
            {categoryFilter === "all"
              ? "No items need review."
              : `No items in the ${CATEGORY_CONFIG[categoryFilter].label} category.`}
          </p>
        </div>
      ) : (
        <>
          {/* Select all header */}
          <div className="flex items-center gap-3 px-3 py-1.5 text-xs text-text-muted border-b border-surface-border mb-1">
            <Tooltip content="Select or deselect all items on this page">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleSelectAll}
                  className="rounded border-surface-border"
                />
                Select All
              </label>
            </Tooltip>
          </div>

          <div className="space-y-1.5">
            {(() => {
              // Group duplicates visually when viewing the duplicate category
              const isDupCategory = categoryFilter === "duplicate" || categoryFilter === "all";
              if (!isDupCategory) {
                return items.map((item) => (
                  <ReviewCard
                    key={item.video_id}
                    item={item}
                    isSelected={selectedIds.has(item.video_id)}
                    onToggleSelect={() => toggleSelect(item.video_id)}
                    onView={() => navigate(`/video/${item.video_id}`)}
                    onApprove={() => handleApprove(item.video_id)}
                    onDismiss={() => handleDismiss(item.video_id)}
                    onDelete={() => handleDelete(item.video_id)}
                    onSetVersion={(vt) => handleSetVersion(item.video_id, vt)}
                    onApplyRename={() => handleApplyRename(item.video_id)}
                    categoryFilter={categoryFilter}
                  />
                ));
              }

              // Build groups for duplicate items, keep others ungrouped
              const groups: { key: string; items: ReviewItem[] }[] = [];
              const ungrouped: ReviewItem[] = [];
              const groupMap = new Map<string, ReviewItem[]>();

              for (const item of items) {
                if (item.dup_group_key && (item.review_category || "").includes("duplicate")) {
                  const existing = groupMap.get(item.dup_group_key);
                  if (existing) {
                    existing.push(item);
                  } else {
                    const arr = [item];
                    groupMap.set(item.dup_group_key, arr);
                    groups.push({ key: item.dup_group_key, items: arr });
                  }
                } else {
                  ungrouped.push(item);
                }
              }

              const renderCard = (item: ReviewItem) => (
                <ReviewCard
                  key={item.video_id}
                  item={item}
                  isSelected={selectedIds.has(item.video_id)}
                  onToggleSelect={() => toggleSelect(item.video_id)}
                  onView={() => navigate(`/video/${item.video_id}`)}
                  onApprove={() => handleApprove(item.video_id)}
                  onDismiss={() => handleDismiss(item.video_id)}
                  onDelete={() => handleDelete(item.video_id)}
                  onSetVersion={(vt) => handleSetVersion(item.video_id, vt)}
                  onApplyRename={() => handleApplyRename(item.video_id)}
                  categoryFilter={categoryFilter}
                />
              );

              return (
                <>
                  {groups.map((g) => (
                    <DuplicateGroupCard
                      key={g.key}
                      items={g.items}
                      selectedIds={selectedIds}
                      onToggleSelect={toggleSelect}
                      onApprove={handleApprove}
                      onDismiss={handleDismiss}
                      onDelete={handleDelete}
                      onSetVersion={handleSetVersion}
                    />
                  ))}
                  {ungrouped.map(renderCard)}
                </>
              );
            })()}
          </div>

          <Pagination
            page={page}
            totalPages={totalPages}
            pageSize={pageSize}
            total={total}
            onPageChange={setPage}
            onPageSizeChange={handlePageSizeChange}
          />
        </>
      )}

      {dialog}

      <ScrapeOptionsModal
        open={scrapeModalOpen}
        onClose={() => setScrapeModalOpen(false)}
        onScrape={handleScrapeConfirm}
        itemCount={selectedIds.size}
        isPending={batchScrapeMutation.isPending}
      />
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// ██ Review Card (per-item)
// ═══════════════════════════════════════════════════════════

// ── Video quality spec line (reused in both card types) ──
function QualitySpecs({ item }: { item: ReviewItem | DuplicateVideoSummary }) {
  const res = item.resolution_label;
  const dur = "duration_seconds" in item ? item.duration_seconds : null;
  const vCodec = "video_codec" in item ? item.video_codec : null;
  const aCodec = "audio_codec" in item ? item.audio_codec : null;
  const vBr = "video_bitrate" in item ? item.video_bitrate : null;
  const aBr = "audio_bitrate" in item ? item.audio_bitrate : null;
  const fps = "fps" in item ? item.fps : null;
  const hdr = "hdr" in item ? item.hdr : false;
  const container = "container" in item ? item.container : null;
  const size = item.file_size_bytes;

  return (
    <div className="flex flex-wrap items-center gap-1.5 mt-1">
      {dur != null && <span className="text-[10px] text-text-secondary bg-surface-lighter px-1.5 py-0.5 rounded font-mono">{formatDuration(dur)}</span>}
      {res && <span className="text-[10px] text-text-secondary bg-surface-lighter px-1.5 py-0.5 rounded">{res}</span>}
      {vCodec && <span className="text-[10px] text-text-muted bg-surface-lighter px-1.5 py-0.5 rounded">{vCodec.toUpperCase()}</span>}
      {vBr != null && <span className="text-[10px] text-text-muted bg-surface-lighter px-1.5 py-0.5 rounded">{formatBitrate(vBr)}</span>}
      {fps != null && <span className="text-[10px] text-text-muted bg-surface-lighter px-1.5 py-0.5 rounded">{fps}fps</span>}
      {hdr && <span className="text-[10px] font-semibold text-yellow-400 bg-yellow-500/10 px-1.5 py-0.5 rounded">HDR</span>}
      {aCodec && <span className="text-[10px] text-text-muted bg-surface-lighter px-1.5 py-0.5 rounded">{aCodec.toUpperCase()}</span>}
      {aBr != null && <span className="text-[10px] text-text-muted bg-surface-lighter px-1.5 py-0.5 rounded">{formatBitrate(aBr)}</span>}
      {container && <span className="text-[10px] text-text-muted bg-surface-lighter px-1.5 py-0.5 rounded">.{container}</span>}
      {size != null && <span className="text-[10px] text-text-muted">{formatBytes(size)}</span>}
    </div>
  );
}

// ── Duplicate group card (single tile for all members) ──
function DuplicateGroupCard({
  items,
  selectedIds,
  onToggleSelect,
  onApprove,
  onDismiss,
  onDelete,
  onSetVersion,
}: {
  items: ReviewItem[];
  selectedIds: Set<number>;
  onToggleSelect: (id: number) => void;
  onApprove: (id: number) => void;
  onDismiss: (id: number) => void;
  onDelete: (id: number) => void;
  onSetVersion: (videoId: number, vt: string, approve?: boolean) => void;
}) {
  const navigate = useNavigate();
  const allIds = items.map((i) => i.video_id);
  const allGroupSelected = allIds.every((id) => selectedIds.has(id));

  // Sort by quality score descending so best is first
  const sorted = [...items].sort((a, b) => (b.quality_score ?? 0) - (a.quality_score ?? 0));
  const bestScore = sorted[0]?.quality_score ?? 0;

  const toggleGroupSelect = () => {
    if (allGroupSelected) {
      allIds.forEach((id) => onToggleSelect(id));
    } else {
      allIds.filter((id) => !selectedIds.has(id)).forEach((id) => onToggleSelect(id));
    }
  };

  const handleKeepAll = () => {
    // Approve all items in the group
    allIds.forEach((id) => onApprove(id));
  };

  const handleDismissAll = () => {
    allIds.forEach((id) => onDismiss(id));
  };

  return (
    <div className="rounded-lg border border-purple-500/20 bg-purple-500/5 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-purple-500/10 border-b border-purple-500/20">
        <Tooltip content="Select all items in this duplicate group">
          <input
            type="checkbox"
            checked={allGroupSelected}
            onChange={toggleGroupSelect}
            className="rounded border-surface-border flex-shrink-0"
          />
        </Tooltip>
        <GitCompare size={13} className="text-purple-400 flex-shrink-0" />
        <span className="text-xs font-medium text-purple-300">
          Duplicate Group — {items[0].artist} — {items[0].title}
        </span>
        <span className="text-[10px] text-purple-400/70 ml-auto">{items.length} items</span>
      </div>

      {/* Members grid */}
      <div className={cn(
        "grid divide-x divide-surface-border",
        sorted.length === 2 ? "grid-cols-2" : sorted.length === 3 ? "grid-cols-3" : "grid-cols-2",
      )}>
        {sorted.map((item, idx) => {
          const score = item.quality_score ?? 0;
          const isBest = score > 0 && score === bestScore && sorted.filter((s) => (s.quality_score ?? 0) === bestScore).length === 1;
          // For groups of 4+, wrap into rows of 2
          const isLastOdd = sorted.length > 3 && sorted.length % 2 === 1 && idx === sorted.length - 1;

          return (
            <div
              key={item.video_id}
              className={cn(
                "p-3 cursor-pointer hover:bg-surface-hover/30 transition-colors",
                isBest && "bg-emerald-500/5",
                sorted.length > 3 && "border-b border-surface-border",
                isLastOdd && "col-span-2",
              )}
              onClick={() => navigate(`/video/${item.video_id}`)}
            >
              <div className="flex items-center gap-1.5 mb-2">
                <span className={cn(
                  "text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded",
                  idx === 0 ? "text-purple-400 bg-purple-500/10" : "text-blue-400 bg-blue-500/10",
                )}>
                  #{item.video_id}
                </span>
                {item.version_type && item.version_type !== "normal" && (
                  <VersionBadge versionType={item.version_type} className="text-[10px] px-1.5 py-0" />
                )}
                {isBest && (
                  <span className="text-[10px] font-semibold text-emerald-400 bg-emerald-500/10 px-1.5 py-0.5 rounded">Best Quality</span>
                )}
              </div>
              <div className="flex gap-2">
                {item.thumbnail_url && (
                  <img src={item.thumbnail_url} alt="" className="w-20 h-14 object-cover rounded flex-shrink-0" />
                )}
                <div className="min-w-0 flex-1">
                  <p className="text-xs text-text-secondary truncate">{item.filename || "—"}</p>
                  <QualitySpecs item={item} />
                </div>
              </div>
              <div className="flex items-center justify-between mt-1.5" onClick={(e) => e.stopPropagation()}>
                <VersionTypeDropdown currentType={item.version_type} onSelect={(vt) => onSetVersion(item.video_id, vt)} />
                <Tooltip content="Delete this video and its files from disk">
                  <button onClick={() => onDelete(item.video_id)} className="btn-ghost btn-sm text-xs text-red-400/60 hover:text-red-300">
                    <Trash2 size={13} />
                  </button>
                </Tooltip>
              </div>
            </div>
          );
        })}
      </div>

      {/* Actions footer */}
      <div className="flex items-center justify-end gap-1 px-3 py-1.5 border-t border-purple-500/20 bg-purple-500/10" onClick={(e) => e.stopPropagation()}>
        <Tooltip content="Keep all — accept as valid separate versions and remove from review permanently">
          <button onClick={handleKeepAll} className="btn-ghost btn-sm text-xs text-emerald-400 hover:text-emerald-300">
            <Check size={14} /> Keep All
          </button>
        </Tooltip>
        <Tooltip content="Dismiss — clear duplicate flags and remove from review permanently">
          <button onClick={handleDismissAll} className="btn-ghost btn-sm text-xs text-red-400 hover:text-red-300">
            <X size={14} /> Dismiss All
          </button>
        </Tooltip>
      </div>
    </div>
  );
}

// ── Standard review card ────────────────────────────────
function ReviewCard({
  item,
  isSelected,
  onToggleSelect,
  onView,
  onApprove,
  onDismiss,
  onDelete,
  onSetVersion,
  onApplyRename,
  categoryFilter,
}: {
  item: ReviewItem;
  isSelected: boolean;
  onToggleSelect: () => void;
  onView: () => void;
  onApprove: () => void;
  onDismiss: () => void;
  onDelete: () => void;
  onSetVersion: (vt: string) => void;
  onApplyRename: () => void;
  categoryFilter: ReviewCategory;
}) {
  const reasons = parseReviewReason(item.review_reason);

  return (
    <div
      className={cn(
        "card flex items-start gap-3 px-3 py-2.5 cursor-pointer transition-all duration-150",
        "hover:shadow-[inset_3px_0_0_var(--color-accent)] hover:bg-surface-hover/50",
        isSelected && "ring-1 ring-accent/30 bg-accent/5",
      )}
      onClick={onView}
    >
      {/* Checkbox */}
      <Tooltip content="Select this item for bulk actions">
        <input
          type="checkbox"
          checked={isSelected}
          onChange={(e) => { e.stopPropagation(); onToggleSelect(); }}
          onClick={(e) => e.stopPropagation()}
          className="rounded border-surface-border flex-shrink-0 mt-1.5"
        />
      </Tooltip>

      {/* Thumbnail */}
      {item.thumbnail_url && (
        <img src={item.thumbnail_url} alt="" className="w-14 h-14 object-cover rounded flex-shrink-0" />
      )}

      {/* Info — left column */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-[10px] font-mono text-text-muted bg-surface-hover px-1.5 py-0.5 rounded">#{item.video_id}</span>
          <p className="text-sm text-text-primary font-medium truncate max-w-[280px]">
            {item.artist} — {item.title}
          </p>
          {item.version_type && item.version_type !== "normal" && (
            <VersionBadge versionType={item.version_type} className="text-[10px] px-1.5 py-0" />
          )}
        </div>
        <div className="mt-0.5">
          {reasons.length > 0 ? (
            <div className="space-y-0.5">
              {reasons.slice(0, 2).map((r, i) => (
                <p key={i} className="text-[11px] text-text-muted truncate">
                  <span className="font-mono text-text-muted/70">{r.label}:</span>{" "}
                  <span className="text-text-secondary">{r.details}</span>
                </p>
              ))}
              {reasons.length > 2 && (
                <p className="text-[11px] text-text-muted">+{reasons.length - 2} more</p>
              )}
            </div>
          ) : (
            <span className="text-[11px] text-text-muted">—</span>
          )}
        </div>
        <QualitySpecs item={item} />
        {item.review_category === "rename" && item.expected_path && (
          <div className="mt-1 text-[10px] font-mono leading-relaxed">
            <span className="text-red-400/80 line-through block truncate">{item.filename}</span>
            <span className="text-emerald-400/80 block truncate">→ {item.expected_path.split("/").pop()}</span>
          </div>
        )}
        {item.updated_at && (
          <span className="text-[10px] text-text-muted mt-0.5 block">{timeAgo(item.updated_at)}</span>
        )}
      </div>

      {/* Right column — badges + actions stacked */}
      <div className="flex flex-col items-end gap-1.5 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
        {/* Badges row */}
        <div className="flex items-center gap-1.5">
          <CategoryBadge category={item.review_category} />
          <ReviewStatusBadge status={item.review_status} />
        </div>
        {/* Actions row */}
        <div className="flex items-center gap-1">
          {(categoryFilter === "version_detection" || categoryFilter === "all") && item.review_category !== "rename" && (
            <VersionTypeDropdown currentType={item.version_type} onSelect={onSetVersion} />
          )}

          {item.review_category === "rename" ? (
            <Tooltip content="Rename file and folder to match the naming convention">
              <button onClick={onApplyRename} className="btn-ghost btn-sm text-xs text-teal-400 hover:text-teal-300">
                <FileEdit size={14} /> Rename
              </button>
            </Tooltip>
          ) : (
            <Tooltip content="Approve — accept the current classification and remove from review">
              <button onClick={onApprove} className="btn-ghost btn-sm text-xs text-emerald-400 hover:text-emerald-300">
                <Check size={14} />
              </button>
            </Tooltip>
          )}

          <Tooltip content="Dismiss — clear the review flag entirely and remove from queue">
            <button onClick={onDismiss} className="btn-ghost btn-sm text-xs text-red-400 hover:text-red-300">
              <X size={14} />
            </button>
          </Tooltip>

          <Tooltip content="Delete — permanently remove this video and its files from disk">
            <button onClick={onDelete} className="btn-ghost btn-sm text-xs text-red-400/60 hover:text-red-300">
              <Trash2 size={14} />
            </button>
          </Tooltip>
        </div>
      </div>
    </div>
  );
}
