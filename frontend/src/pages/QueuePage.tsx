import { useState, useMemo, useCallback, useEffect } from "react";
import {
  RefreshCw, Wifi, WifiOff, Search, AlertTriangle, RotateCcw,
  ChevronLeft, ChevronRight, Trash2,
  Download, FolderInput, Activity, CheckCircle2, XCircle, Ban, SkipForward,
  Clapperboard, FileSearch, X,
} from "lucide-react";
import { useJobs, useJobLog, useRetryJob, useCancelJob, useClearHistory, useDeleteBatch } from "@/hooks/queries";
import { jobsApi } from "@/lib/api";
import { ErrorState, Skeleton } from "@/components/Feedback";
import { useToast } from "@/components/Toast";
import { useConfirm } from "@/components/ConfirmDialog";
import { Tooltip } from "@/components/Tooltip";
import { useJobTelemetry } from "@/hooks/useJobTelemetry";
import { JobCard } from "@/components/QueueComponents";
import { isActiveJob } from "@/lib/utils";
import type { JobSummary } from "@/types";

type QueueTab = "active" | "history";
type HistoryFilter = "all" | "complete" | "failed" | "cancelled" | "skipped";
type SourceFilter = "all" | "download" | "import" | "editor" | "scraper";

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100];

const DOWNLOAD_JOB_TYPES = new Set(["import_url", "playlist_import", "redownload"]);
const IMPORT_JOB_TYPES = new Set(["library_scan", "library_import", "library_import_video"]);
const EDITOR_JOB_TYPES_PREFIX = "video_editor_";
const SCRAPER_JOB_TYPES = new Set(["metadata_refresh", "batch_metadata_refresh", "kodi_export"]);

function getSourceType(job: JobSummary): SourceFilter {
  if (DOWNLOAD_JOB_TYPES.has(job.job_type)) return "download";
  if (IMPORT_JOB_TYPES.has(job.job_type)) return "import";
  if (job.job_type.startsWith(EDITOR_JOB_TYPES_PREFIX)) return "editor";
  if (SCRAPER_JOB_TYPES.has(job.job_type)) return "scraper";
  return "download";
}

function isFinalizing(j: JobSummary) {
  if (j.job_type.startsWith("video_editor_")) return false;
  if (j.status !== "complete" || !j.current_step) return false;
  // These steps are definitively terminal — never finalizing
  if (j.current_step === "Import complete" || j.current_step.startsWith("All ") || j.current_step.startsWith("Pending review")) return false;
  // Only treat as finalizing if it was updated recently (deferred tasks actively running)
  if (!j.updated_at) return false;
  const updMs = new Date(j.updated_at.endsWith("Z") ? j.updated_at : j.updated_at + "Z").getTime();
  return (Date.now() - updMs) < 60_000;
}

function isStuckJob(j: JobSummary) {
  if (!isFinalizing(j)) return false;
  if (!j.completed_at) return false;
  const toMs = (ts: string) => new Date(ts.endsWith("Z") ? ts : ts + "Z").getTime();
  const latestMs = Math.max(toMs(j.completed_at), j.updated_at ? toMs(j.updated_at) : 0);
  return (Date.now() - latestMs) / 60_000 > 5;
}

/* ── Pagination controls ── */
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
        <button
          onClick={() => onPageChange(page - 1)}
          disabled={page <= 1}
          className="p-1 rounded hover:bg-surface-lighter disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <ChevronLeft size={16} />
        </button>
        <span className="tabular-nums px-2 text-text-secondary">{page} / {totalPages || 1}</span>
        <button
          onClick={() => onPageChange(page + 1)}
          disabled={page >= totalPages}
          className="p-1 rounded hover:bg-surface-lighter disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <ChevronRight size={16} />
        </button>
      </div>
    </div>
  );
}

/* ── Stat card ── */
function StatCard({ icon, label, value, active, selected, color, onClick }: {
  icon: React.ReactNode; label: string; value: number; active?: boolean; selected?: boolean; color: string; onClick?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2.5 px-3 py-2 rounded-lg border transition-colors text-left w-full ${
        selected ? `${color} border-current/40 ring-1 ring-current/30` :
        active ? `${color} border-current/20` : "bg-surface/50 border-surface-border text-text-muted hover:bg-surface-lighter"
      }`}
    >
      <span className="opacity-70">{icon}</span>
      <div className="min-w-0">
        <div className="text-lg font-bold tabular-nums leading-tight">{value}</div>
        <div className="text-[10px] uppercase tracking-wider opacity-70">{label}</div>
      </div>
    </button>
  );
}

export function QueuePage() {
  const { toast } = useToast();
  const { confirm, dialog } = useConfirm();
  const { data: jobs, isLoading, isError, refetch } = useJobs({ limit: 10000 });
  const retryMutation = useRetryJob();
  const cancelMutation = useCancelJob();
  const clearHistoryMutation = useClearHistory();
  const batchDeleteMutation = useDeleteBatch();
  const { connected, getJobTelemetry } = useJobTelemetry();

  const [expandedJobId, setExpandedJobId] = useState<number | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [activeTab, setActiveTab] = useState<QueueTab>(() => {
    return (localStorage.getItem("queue_tab") as QueueTab) || "active";
  });
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>(() => {
    return (localStorage.getItem("queue_source_filter") as SourceFilter) || "all";
  });
  const [searchQuery, setSearchQuery] = useState("");
  const [historyFilter, setHistoryFilter] = useState<HistoryFilter>(() => {
    return (localStorage.getItem("queue_history_filter") as HistoryFilter) || "all";
  });
  const [statusFilter, setStatusFilter] = useState<string | null>(() => {
    return localStorage.getItem("queue_status_filter") || null;
  });

  // Persist filter state
  useEffect(() => { localStorage.setItem("queue_tab", activeTab); }, [activeTab]);
  useEffect(() => { localStorage.setItem("queue_source_filter", sourceFilter); }, [sourceFilter]);
  useEffect(() => { localStorage.setItem("queue_history_filter", historyFilter); }, [historyFilter]);
  useEffect(() => {
    if (statusFilter) localStorage.setItem("queue_status_filter", statusFilter);
    else localStorage.removeItem("queue_status_filter");
  }, [statusFilter]);

  // Pagination
  const [activePage, setActivePage] = useState(1);
  const [activePageSize, setActivePageSize] = useState(() => {
    const saved = localStorage.getItem("queue_active_page_size");
    return saved ? Number(saved) : 20;
  });
  const [historyPage, setHistoryPage] = useState(1);
  const [historyPageSize, setHistoryPageSize] = useState(() => {
    const saved = localStorage.getItem("queue_history_page_size");
    return saved ? Number(saved) : 20;
  });

  // Separate active and history jobs
  const { activeJobs, allActiveCount, historyJobs } = useMemo(() => {
    if (!jobs) return { activeJobs: [], allActiveCount: 0, historyJobs: [] };

    const sourceFiltered = sourceFilter === "all"
      ? jobs
      : jobs.filter((j) => getSourceType(j) === sourceFilter);

    let pool = sourceFiltered;

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      pool = pool.filter(
        (j) =>
          (j.display_name || "").toLowerCase().includes(q) ||
          (j.input_url || "").toLowerCase().includes(q) ||
          (j.action_label || "").toLowerCase().includes(q) ||
          j.job_type.toLowerCase().includes(q) ||
          String(j.id).includes(q)
      );
    }

    let active = pool.filter((j) => isActiveJob(j.status) || isFinalizing(j));
    const allActiveCount = active.length;
    // Apply status filter for active-tab sub-filtering (e.g. "downloading" only)
    if (statusFilter === "downloading") {
      active = active.filter((j) => j.status === "downloading");
    }
    // Sort: in-progress jobs first, then queued
    active.sort((a, b) => {
      const aQueued = a.status === "queued" ? 1 : 0;
      const bQueued = b.status === "queued" ? 1 : 0;
      return aQueued - bQueued;
    });
    const history = pool.filter((j) => !isActiveJob(j.status) && !isFinalizing(j));
    return { activeJobs: active, allActiveCount, historyJobs: history };
  }, [jobs, sourceFilter, searchQuery, statusFilter]);

  // Filtered history (by status)
  const filteredHistory = useMemo(() => {
    if (historyFilter === "all") return historyJobs;
    return historyJobs.filter((j) => j.status === historyFilter);
  }, [historyJobs, historyFilter]);

  // Status breakdown for stats
  const statusCounts = useMemo(() => {
    if (!jobs) return { downloading: 0, queued: 0, processing: 0, finalizing: 0, complete: 0, failed: 0, cancelled: 0, skipped: 0 };
    const c = { downloading: 0, queued: 0, processing: 0, finalizing: 0, complete: 0, failed: 0, cancelled: 0, skipped: 0 };
    for (const j of jobs) {
      if (j.status === "downloading") c.downloading++;
      else if (j.status === "queued") c.queued++;
      else if (j.status === "failed") c.failed++;
      else if (j.status === "cancelled") c.cancelled++;
      else if (j.status === "skipped") c.skipped++;
      else if (isFinalizing(j)) c.finalizing++;
      else if (j.status === "complete") c.complete++;
      else if (isActiveJob(j.status)) c.processing++;
    }
    return c;
  }, [jobs]);

  // History status counts
  // Paginated slices
  const activeTotalPages = Math.max(1, Math.ceil(activeJobs.length / activePageSize));
  const historyTotalPages = Math.max(1, Math.ceil(filteredHistory.length / historyPageSize));

  const activeSlice = useMemo(() => {
    const start = (activePage - 1) * activePageSize;
    return activeJobs.slice(start, start + activePageSize);
  }, [activeJobs, activePage, activePageSize]);

  const historySlice = useMemo(() => {
    const start = (historyPage - 1) * historyPageSize;
    return filteredHistory.slice(start, start + historyPageSize);
  }, [filteredHistory, historyPage, historyPageSize]);

  // Reset page when filter/search changes
  const setActivePageSafe = useCallback((p: number) => setActivePage(Math.max(1, Math.min(p, activeTotalPages))), [activeTotalPages]);
  const setHistoryPageSafe = useCallback((p: number) => setHistoryPage(Math.max(1, Math.min(p, historyTotalPages))), [historyTotalPages]);

  // Clamp pages when data changes
  useMemo(() => { if (activePage > activeTotalPages) setActivePage(Math.max(1, activeTotalPages)); }, [activeTotalPages]);
  useMemo(() => { if (historyPage > historyTotalPages) setHistoryPage(Math.max(1, historyTotalPages)); }, [historyTotalPages]);

  // Job log for expanded panel
  const expandedLog = useJobLog(expandedJobId);

  const handleRetry = useCallback(
    (jobId: number) => {
      retryMutation.mutate(jobId, {
        onSuccess: () => toast({ type: "success", title: "Job retried" }),
      });
    },
    [retryMutation, toast]
  );

  const handleCancel = useCallback(
    async (job: JobSummary) => {
      const ok = await confirm({
        title: "Cancel this job?",
        description: `Job #${job.id} "${job.display_name || job.job_type}" will be cancelled.`,
      });
      if (ok) {
        cancelMutation.mutate(job.id, {
          onSuccess: () => toast({ type: "success", title: "Job cancelled" }),
        });
      }
    },
    [confirm, cancelMutation, toast]
  );

  // Detect server-restart interrupted jobs
  const interruptedJobs = useMemo(() => {
    if (!jobs) return [];
    return jobs.filter(
      (j) => j.status === "failed" && !!j.error_message && j.error_message.includes("Server restarted")
    );
  }, [jobs]);

  const [retryingAll, setRetryingAll] = useState(false);
  const handleRetryAllInterrupted = useCallback(async () => {
    setRetryingAll(true);
    for (const job of interruptedJobs) {
      retryMutation.mutate(job.id);
    }
    toast({ type: "success", title: `Retrying ${interruptedJobs.length} interrupted job(s)` });
    setRetryingAll(false);
  }, [interruptedJobs, retryMutation, toast]);

  const handleClearHistory = useCallback(async () => {
    // Build context-sensitive clear parameters
    const params: { status?: string; job_type?: string } = {};
    let description = "This will permanently delete all completed, failed, cancelled, and skipped jobs from the queue.";

    // Status filter
    if (historyFilter !== "all") {
      params.status = historyFilter;
      description = `This will permanently delete all ${historyFilter} jobs.`;
    }

    // Source filter → job_type prefix
    if (sourceFilter === "download") {
      description = historyFilter !== "all"
        ? `This will permanently delete all ${historyFilter} download jobs.`
        : "This will permanently delete all download history.";
    } else if (sourceFilter === "import") {
      description = historyFilter !== "all"
        ? `This will permanently delete all ${historyFilter} import jobs.`
        : "This will permanently delete all import history.";
    } else if (sourceFilter === "editor") {
      params.job_type = "video_editor";
      description = historyFilter !== "all"
        ? `This will permanently delete all ${historyFilter} editor jobs.`
        : "This will permanently delete all editor history.";
    } else if (sourceFilter === "scraper") {
      params.job_type = "metadata";
      description = historyFilter !== "all"
        ? `This will permanently delete all ${historyFilter} scraper jobs.`
        : "This will permanently delete all scraper history.";
    }

    const ok = await confirm({
      title: "Clear history?",
      description,
    });
    if (ok) {
      // For download/import source filters, we need to clear multiple job types
      // so we'll pass them one at a time
      if (sourceFilter === "download") {
        let total = 0;
        for (const jt of ["import_url", "playlist_import", "redownload"]) {
          const result = await jobsApi.clearHistory({ ...params, job_type: jt });
          total += result.deleted;
        }
        toast({ type: "success", title: `Cleared ${total} job(s)` });
        refetch();
      } else if (sourceFilter === "import") {
        let total = 0;
        for (const jt of ["library_scan", "library_import", "library_import_video"]) {
          const result = await jobsApi.clearHistory({ ...params, job_type: jt });
          total += result.deleted;
        }
        toast({ type: "success", title: `Cleared ${total} job(s)` });
        refetch();
      } else {
        clearHistoryMutation.mutate(params, {
          onSuccess: (data) => {
            toast({ type: "success", title: `Cleared ${data.deleted} job(s)` });
          },
        });
      }
    }
  }, [confirm, clearHistoryMutation, historyFilter, sourceFilter, toast, refetch]);

  // Auto-switch to active tab when jobs start processing
  const hasActiveJobs = allActiveCount > 0;

  // ─── Bulk selection ─────────────────────────────────────
  const toggleSelect = useCallback((id: number) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback(() => {
    const visible = activeTab === "active" ? activeSlice : historySlice;
    const allSelected = visible.length > 0 && visible.every(j => selectedIds.has(j.id));
    if (allSelected) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(visible.map(j => j.id)));
    }
  }, [activeTab, activeSlice, historySlice, selectedIds]);

  // Clear selection when tab/filter changes
  useEffect(() => { setSelectedIds(new Set()); }, [activeTab, sourceFilter, historyFilter, searchQuery]);

  const selectedCount = selectedIds.size;

  const handleBulkDelete = useCallback(async () => {
    const videoIds = Array.from(selectedIds)
      .map(id => jobs?.find(j => j.id === id)?.video_id)
      .filter((v): v is number => v != null);
    if (videoIds.length === 0) {
      toast({ type: "warning", title: "No linked videos to delete" });
      return;
    }
    const ok = await confirm({
      title: `Delete ${videoIds.length} track(s)?`,
      description: "This will permanently delete the selected tracks and their files from your library.",
    });
    if (ok) {
      batchDeleteMutation.mutate(videoIds, {
        onSuccess: (data) => {
          toast({ type: "success", title: `Deleted ${data.count} track(s)` });
          setSelectedIds(new Set());
          refetch();
        },
      });
    }
  }, [selectedIds, jobs, confirm, batchDeleteMutation, toast, refetch]);

  const handleBulkRetry = useCallback(() => {
    const ids = Array.from(selectedIds).filter(id => {
      const j = jobs?.find(j => j.id === id);
      return j?.status === "failed";
    });
    for (const id of ids) retryMutation.mutate(id);
    toast({ type: "success", title: `Retrying ${ids.length} job(s)` });
    setSelectedIds(new Set());
  }, [selectedIds, jobs, retryMutation, toast]);

  const handleBulkCancel = useCallback(async () => {
    const ids = Array.from(selectedIds).filter(id => {
      const j = jobs?.find(j => j.id === id);
      return j && isActiveJob(j.status);
    });
    const ok = await confirm({
      title: `Cancel ${ids.length} job(s)?`,
      description: "The selected active jobs will be cancelled.",
    });
    if (ok) {
      for (const id of ids) cancelMutation.mutate(id);
      toast({ type: "success", title: `Cancelled ${ids.length} job(s)` });
      setSelectedIds(new Set());
    }
  }, [selectedIds, jobs, confirm, cancelMutation, toast]);

  if (isLoading) {
    return (
      <div className="p-4 md:p-6 space-y-4">
        <Skeleton className="h-10 w-48" />
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-16 rounded-lg" />
          ))}
        </div>
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-20 rounded-lg" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <div className="p-6">
        <ErrorState message="Failed to load jobs" onRetry={refetch} />
      </div>
    );
  }

  const renderJobCard = (job: JobSummary) => (
    <JobCard
      key={job.id}
      job={job}
      telemetry={getJobTelemetry(job.id)}
      logText={expandedJobId === job.id ? expandedLog.data?.log_text : undefined}
      isLoadingLog={expandedJobId === job.id && expandedLog.isLoading}
      isExpanded={expandedJobId === job.id}
      onToggleExpand={() =>
        setExpandedJobId(expandedJobId === job.id ? null : job.id)
      }
      onRetry={job.status === "failed" ? () => handleRetry(job.id) : undefined}
      onCancel={isActiveJob(job.status) || isStuckJob(job) ? () => handleCancel(job) : undefined}
      selected={selectedIds.has(job.id)}
      onSelect={toggleSelect}
    />
  );

  const currentJobs = activeTab === "active" ? activeSlice : historySlice;
  const currentTotal = activeTab === "active" ? activeJobs.length : filteredHistory.length;
  const currentPage = activeTab === "active" ? activePage : historyPage;
  const currentTotalPages = activeTab === "active" ? activeTotalPages : historyTotalPages;
  const currentPageSize = activeTab === "active" ? activePageSize : historyPageSize;
  const onPageChange = activeTab === "active" ? setActivePageSafe : setHistoryPageSafe;
  const onPageSizeChange = activeTab === "active"
    ? (s: number) => { localStorage.setItem("queue_active_page_size", String(s)); setActivePageSize(s); setActivePage(1); }
    : (s: number) => { localStorage.setItem("queue_history_page_size", String(s)); setHistoryPageSize(s); setHistoryPage(1); };

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto">
      {/* Header row */}
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-text-primary">Queue</h1>
          <span
            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium ${
              connected
                ? "bg-emerald-500/15 text-emerald-400"
                : "bg-yellow-500/15 text-yellow-400"
            }`}
            title={connected ? "Live updates connected" : "Polling mode (SSE disconnected)"}
          >
            {connected ? <Wifi size={10} /> : <WifiOff size={10} />}
            {connected ? "Live" : "Polling"}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
            <input
              type="text"
              placeholder="Search jobs..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="input-field pl-8 pr-3 py-1.5 text-sm w-56"
            />
          </div>
          <button onClick={() => refetch()} className="btn-ghost btn-sm gap-1.5">
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      {/* Stats overview */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-2.5 mb-5">
        <StatCard icon={<Activity size={16} />} label="Active" value={allActiveCount} active={allActiveCount > 0} selected={statusFilter === "active"} color="bg-blue-500/10 text-blue-400"
          onClick={() => { if (statusFilter === "active") { setStatusFilter(null); } else { setActiveTab("active"); setHistoryFilter("all"); setStatusFilter("active"); } }} />
        <StatCard icon={<Download size={16} />} label="Downloading" value={statusCounts.downloading} active={statusCounts.downloading > 0} selected={statusFilter === "downloading"} color="bg-sky-500/10 text-sky-400"
          onClick={() => { if (statusFilter === "downloading") { setStatusFilter(null); } else { setActiveTab("active"); setHistoryFilter("all"); setStatusFilter("downloading"); } }} />
        <StatCard icon={<CheckCircle2 size={16} />} label="Complete" value={statusCounts.complete} selected={statusFilter === "complete"} color="bg-emerald-500/10 text-emerald-400"
          onClick={() => { if (statusFilter === "complete") { setStatusFilter(null); setHistoryFilter("all"); } else { setActiveTab("history"); setHistoryFilter("complete"); setStatusFilter("complete"); } }} />
        <StatCard icon={<XCircle size={16} />} label="Failed" value={statusCounts.failed} active={statusCounts.failed > 0} selected={statusFilter === "failed"} color="bg-red-500/10 text-red-400"
          onClick={() => { if (statusFilter === "failed") { setStatusFilter(null); setHistoryFilter("all"); } else { setActiveTab("history"); setHistoryFilter("failed"); setStatusFilter("failed"); } }} />
        <StatCard icon={<Ban size={16} />} label="Cancelled" value={statusCounts.cancelled} selected={statusFilter === "cancelled"} color="bg-yellow-500/10 text-yellow-400"
          onClick={() => { if (statusFilter === "cancelled") { setStatusFilter(null); setHistoryFilter("all"); } else { setActiveTab("history"); setHistoryFilter("cancelled"); setStatusFilter("cancelled"); } }} />
        <StatCard icon={<SkipForward size={16} />} label="Skipped" value={statusCounts.skipped} selected={statusFilter === "skipped"} color="bg-orange-500/10 text-orange-400"
          onClick={() => { if (statusFilter === "skipped") { setStatusFilter(null); setHistoryFilter("all"); } else { setActiveTab("history"); setHistoryFilter("skipped"); setStatusFilter("skipped"); } }} />
      </div>

      {/* Server restart interrupted banner */}
      {interruptedJobs.length > 0 && (
        <div className="mb-4 flex items-center gap-3 bg-amber-500/10 border border-amber-500/20 rounded-lg px-4 py-2.5">
          <AlertTriangle size={16} className="text-amber-400 shrink-0" />
          <span className="text-sm text-amber-300 flex-1">
            {interruptedJobs.length} job{interruptedJobs.length > 1 ? "s were" : " was"} interrupted by a server restart.
          </span>
          <button
            onClick={handleRetryAllInterrupted}
            disabled={retryingAll}
            className="btn-ghost btn-sm gap-1 text-amber-400 hover:text-amber-300"
          >
            <RotateCcw size={14} />
            Retry All
          </button>
        </div>
      )}

      {/* Tab bar + filters */}
      <div className="flex items-center gap-1 border-b border-surface-border mb-4">
        {/* Main tabs */}
        <button
          onClick={() => { setActiveTab("active"); setStatusFilter(null); }}
          className={`relative px-4 py-2.5 text-sm font-medium transition-colors ${
            activeTab === "active"
              ? "text-accent"
              : "text-text-muted hover:text-text-secondary"
          }`}
        >
          <span className="flex items-center gap-2">
            Active
            {hasActiveJobs && (
              <span className="inline-flex items-center justify-center min-w-[20px] h-5 px-1.5 rounded-full text-[11px] font-bold bg-accent/20 text-accent tabular-nums">
                {allActiveCount}
              </span>
            )}
          </span>
          {activeTab === "active" && (
            <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-accent rounded-t" />
          )}
        </button>
        <button
          onClick={() => { setActiveTab("history"); setStatusFilter(null); }}
          className={`relative px-4 py-2.5 text-sm font-medium transition-colors ${
            activeTab === "history"
              ? "text-accent"
              : "text-text-muted hover:text-text-secondary"
          }`}
        >
          <span className="flex items-center gap-2">
            History
            <span className="text-[11px] text-text-muted tabular-nums">
              {historyJobs.length}
            </span>
          </span>
          {activeTab === "history" && (
            <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-accent rounded-t" />
          )}
        </button>

        {/* Spacer */}
        <div className="flex-1" />

        {/* Source filter pills */}
        <div className="flex items-center bg-surface-lighter rounded-lg p-0.5 gap-0.5 mb-1">
          {(["all", "download", "import", "editor", "scraper"] as const).map((f) => {
            const labels: Record<SourceFilter, string> = {
              all: "All", download: "Downloads", import: "Imports", editor: "Editor", scraper: "Scraper",
            };
            return (
              <button
                key={f}
                onClick={() => setSourceFilter(f)}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                  sourceFilter === f
                    ? "bg-accent/15 text-accent"
                    : "text-text-muted hover:text-text-secondary"
                }`}
              >
                {f === "download" && <Download size={12} />}
                {f === "import" && <FolderInput size={12} />}
                {f === "editor" && <Clapperboard size={12} />}
                {f === "scraper" && <FileSearch size={12} />}
                {labels[f]}
              </button>
            );
          })}
        </div>

        {/* Clear history button (visible on history tab) */}
        {activeTab === "history" && (
          <Tooltip content="Remove finished jobs from history. Respects the current status and source filters.">
            <button
              onClick={handleClearHistory}
              disabled={clearHistoryMutation.isPending}
              className="btn-ghost btn-sm gap-1 text-text-muted hover:text-red-400 text-xs mb-1 ml-1"
            >
              <Trash2 size={13} />
              Clear
            </button>
          </Tooltip>
        )}
      </div>

      {/* Bulk action bar */}
      {selectedCount > 0 && (
        <div className="flex items-center gap-3 bg-accent/10 border border-accent/20 rounded-lg px-4 py-2 mb-3">
          <input
            type="checkbox"
            checked={currentJobs.length > 0 && currentJobs.every(j => selectedIds.has(j.id))}
            onChange={toggleSelectAll}
            className="accent-accent w-4 h-4 cursor-pointer"
          />
          <span className="text-sm text-text-secondary flex-1">
            {selectedCount} selected
          </span>
          {activeTab === "active" && (
            <button onClick={handleBulkCancel} className="btn-ghost btn-sm gap-1 text-yellow-400 hover:text-yellow-300 text-xs">
              <Ban size={13} /> Cancel
            </button>
          )}
          {activeTab === "history" && (
            <button onClick={handleBulkRetry} className="btn-ghost btn-sm gap-1 text-accent hover:text-accent/80 text-xs">
              <RotateCcw size={13} /> Retry
            </button>
          )}
          <button onClick={handleBulkDelete} className="btn-ghost btn-sm gap-1 text-red-400 hover:text-red-300 text-xs">
            <Trash2 size={13} /> Delete
          </button>
          <button onClick={() => setSelectedIds(new Set())} className="btn-ghost btn-sm gap-1 text-text-muted text-xs">
            <X size={13} /> Clear
          </button>
        </div>
      )}

      {/* Job list (single column, full width) */}
      {currentTotal === 0 ? (
        <div className="card text-center py-12">
          <p className="text-sm text-text-muted">
            {activeTab === "active"
              ? "No active jobs — submit a URL or scan your library to get started"
              : "No history items match this filter"}
          </p>
        </div>
      ) : (
        <>
          <div className="space-y-2">
            {currentJobs.map(renderJobCard)}
          </div>
          <Pagination
            page={currentPage}
            totalPages={currentTotalPages}
            pageSize={currentPageSize}
            total={currentTotal}
            onPageChange={onPageChange}
            onPageSizeChange={onPageSizeChange}
          />
        </>
      )}

      {dialog}
    </div>
  );
}
