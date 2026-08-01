import { useCallback, useDeferredValue, useMemo, useState } from "react";
import {
  Activity, Ban, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight,
  Clapperboard, Clock3, Download, FileSearch, FolderInput, RefreshCw,
  RotateCcw, Search, ServerCog, SkipForward, Trash2, Wifi, WifiOff, XCircle,
} from "lucide-react";

import { JobCard } from "@/components/QueueComponents";
import { useConfirm } from "@/components/ConfirmDialog";
import { ErrorState, Skeleton } from "@/components/Feedback";
import { useToast } from "@/components/Toast";
import {
  useCancelJob, useJobLog, useJobPage, useOperationHealth, useRetryJob,
  useUpdateYtdlp, useYtdlpStatus,
} from "@/hooks/queries";
import { useJobTelemetry } from "@/hooks/useJobTelemetry";
import { jobsApi } from "@/lib/api";
import { getPref, setPref } from "@/lib/preferences";
import type {
  ClearHistoryParams, JobCategory, JobPageParams, JobStatusGroup, JobSummary,
} from "@/types";

const STATUS_TABS: Array<{
  value: JobStatusGroup;
  label: string;
  icon: typeof Activity;
}> = [
  { value: "active", label: "Active", icon: Activity },
  { value: "complete", label: "Complete", icon: CheckCircle2 },
  { value: "failed", label: "Failed", icon: XCircle },
  { value: "cancelled", label: "Cancelled", icon: Ban },
  { value: "skipped", label: "Skipped", icon: SkipForward },
];

const CATEGORY_TABS: Array<{
  value: JobCategory | "all";
  label: string;
  icon?: typeof Download;
}> = [
  { value: "all", label: "All" },
  { value: "download", label: "Downloads", icon: Download },
  { value: "import", label: "Imports", icon: FolderInput },
  { value: "video_editor", label: "Video Editor", icon: Clapperboard },
  { value: "scraper", label: "Scraper", icon: FileSearch },
];

const PAGE_SIZES = [10, 20, 50, 100];

interface QueuePreferences {
  status: JobStatusGroup;
  category: JobCategory | "all";
  pageSize: number;
}

const DEFAULT_PREFS: QueuePreferences = {
  status: "active",
  category: "all",
  pageSize: 20,
};

function loadPreferences(): QueuePreferences {
  return { ...DEFAULT_PREFS, ...getPref<Partial<QueuePreferences>>("queue-v2", DEFAULT_PREFS) };
}

function dateStart(value: string): string | undefined {
  return value ? new Date(`${value}T00:00:00`).toISOString() : undefined;
}

function dateEnd(value: string): string | undefined {
  return value ? new Date(`${value}T23:59:59.999`).toISOString() : undefined;
}

function compactAge(seconds: number): string {
  if (seconds < 1) return "none";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function sumBacklog(values?: Record<string, number>): number {
  if (!values) return 0;
  return Object.entries(values)
    .filter(([status]) => !["complete", "completed", "superseded"].includes(status))
    .reduce((total, [, count]) => total + count, 0);
}

function Pagination({
  page, pageSize, total, totalPages, onPage, onPageSize,
}: {
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
  onPage: (page: number) => void;
  onPageSize: (size: number) => void;
}) {
  if (total === 0) return null;
  const start = (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);
  return (
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-xs text-text-muted">
      <label className="flex items-center gap-2">
        Show
        <select
          value={pageSize}
          onChange={(event) => onPageSize(Number(event.target.value))}
          className="rounded border border-surface-border bg-surface-lighter px-2 py-1 text-text-secondary"
        >
          {PAGE_SIZES.map((size) => <option key={size}>{size}</option>)}
        </select>
      </label>
      <span className="tabular-nums">{start}–{end} of {total}</span>
      <div className="flex items-center gap-1">
        <button
          onClick={() => onPage(page - 1)}
          disabled={page <= 1}
          className="rounded p-1.5 hover:bg-surface-lighter disabled:opacity-30"
          aria-label="Previous page"
        >
          <ChevronLeft size={16} />
        </button>
        <span className="min-w-16 text-center tabular-nums">{page} / {totalPages}</span>
        <button
          onClick={() => onPage(page + 1)}
          disabled={page >= totalPages}
          className="rounded p-1.5 hover:bg-surface-lighter disabled:opacity-30"
          aria-label="Next page"
        >
          <ChevronRight size={16} />
        </button>
      </div>
    </div>
  );
}

export function QueuePage() {
  const initialPrefs = useMemo(loadPreferences, []);
  const { toast } = useToast();
  const { confirm, dialog } = useConfirm();
  const { connected, getJobTelemetry } = useJobTelemetry();
  const retryMutation = useRetryJob();
  const cancelMutation = useCancelJob();
  const ytdlpUpdate = useUpdateYtdlp();
  const ytdlp = useYtdlpStatus();
  const operationHealth = useOperationHealth();

  const [status, setStatus] = useState<JobStatusGroup>(initialPrefs.status);
  const [category, setCategory] = useState<JobCategory | "all">(initialPrefs.category);
  const [pageSize, setPageSize] = useState(initialPrefs.pageSize);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search.trim());
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [expandedJobId, setExpandedJobId] = useState<number | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  const params = useMemo<JobPageParams>(() => ({
    status_group: status,
    job_category: category,
    search: deferredSearch || undefined,
    date_from: dateStart(fromDate),
    date_to: dateEnd(toDate),
    sort_by: status === "active" ? "date_added" : "date_completed",
    sort_dir: "desc",
    page,
    page_size: pageSize,
  }), [category, deferredSearch, fromDate, page, pageSize, status, toDate]);

  const jobsQuery = useJobPage(params);
  const expandedLog = useJobLog(expandedJobId);
  const data = jobsQuery.data;
  const jobs = data?.items ?? [];

  const selectStatus = (next: JobStatusGroup) => {
    setStatus(next);
    setPage(1);
    setSelectedIds(new Set());
    setPref("queue-v2", { status: next, category, pageSize });
  };

  const selectCategory = (next: JobCategory | "all") => {
    setCategory(next);
    setPage(1);
    setSelectedIds(new Set());
    setPref("queue-v2", { status, category: next, pageSize });
  };

  const changePageSize = (next: number) => {
    setPageSize(next);
    setPage(1);
    setSelectedIds(new Set());
    setPref("queue-v2", { status, category, pageSize: next });
  };

  const toggleSelected = useCallback((jobId: number) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(jobId)) next.delete(jobId);
      else next.add(jobId);
      return next;
    });
  }, []);

  const togglePage = () => {
    const allSelected = jobs.length > 0 && jobs.every((job) => selectedIds.has(job.id));
    setSelectedIds(allSelected ? new Set() : new Set(jobs.map((job) => job.id)));
  };

  const cancelJob = useCallback(async (job: JobSummary) => {
    const accepted = await confirm({
      title: "Cancel this job?",
      description: `Job #${job.id} (${job.display_name || job.action_label || job.job_type}) will stop at its next safe checkpoint.`,
    });
    if (!accepted) return;
    cancelMutation.mutate(job.id, {
      onSuccess: () => toast({ type: "success", title: "Cancellation requested" }),
      onError: () => toast({ type: "error", title: "Could not cancel job" }),
    });
  }, [cancelMutation, confirm, toast]);

  const retryJob = useCallback((jobId: number) => {
    retryMutation.mutate(jobId, {
      onSuccess: () => toast({ type: "success", title: "Job queued for retry" }),
      onError: () => toast({ type: "error", title: "Could not retry job" }),
    });
  }, [retryMutation, toast]);

  const handleSelectedAction = async () => {
    const selected = jobs.filter((job) => selectedIds.has(job.id));
    if (status === "active") {
      const accepted = await confirm({
        title: `Cancel ${selected.length} selected job${selected.length === 1 ? "" : "s"}?`,
        description: "Each job will stop at its next safe checkpoint.",
      });
      if (!accepted) return;
      selected.forEach((job) => cancelMutation.mutate(job.id));
      toast({ type: "success", title: `Cancellation requested for ${selected.length} job(s)` });
    } else if (status === "failed" || status === "cancelled") {
      selected.forEach((job) => retryMutation.mutate(job.id));
      toast({ type: "success", title: `${selected.length} job(s) queued for retry` });
    }
    setSelectedIds(new Set());
  };

  const clearScope = (): ClearHistoryParams => ({
    status_group: status === "active" ? undefined : status,
    job_category: category,
    search: deferredSearch || undefined,
    date_from: dateStart(fromDate),
    date_to: dateEnd(toDate),
  });

  const clearHistory = async () => {
    if (status === "active") return;
    try {
      const scope = clearScope();
      const preview = await jobsApi.previewClearHistory(scope);
      if (preview.count === 0) {
        toast({ type: "info", title: "No matching history to clear" });
        return;
      }
      const range = fromDate || toDate
        ? ` Date range: ${fromDate || "beginning"} to ${toDate || "now"}.`
        : " All dates are included.";
      const accepted = await confirm({
        title: `Delete ${preview.count} ${status} job${preview.count === 1 ? "" : "s"}?`,
        description: `This removes only ${status} history in ${category === "all" ? "all categories" : category}.${range} Active jobs and operation audit records are preserved.`,
      });
      if (!accepted) return;
      const result = await jobsApi.clearHistory(scope);
      toast({ type: "success", title: `Cleared ${result.deleted} history item(s)` });
      setSelectedIds(new Set());
      jobsQuery.refetch();
    } catch {
      toast({ type: "error", title: "Could not clear queue history" });
    }
  };

  const queueYtdlpUpdate = () => {
    ytdlpUpdate.mutate(undefined, {
      onSuccess: (result) => toast({
        type: "success",
        title: "yt-dlp update queued",
        description: `Job #${result.job_id} will report progress in Active.`,
      }),
      onError: () => toast({ type: "error", title: "Could not queue yt-dlp update" }),
    });
  };

  if (jobsQuery.isLoading && !data) {
    return (
      <div className="mx-auto max-w-6xl space-y-4 p-4 md:p-6">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-20 w-full" />
        {Array.from({ length: 5 }).map((_, index) => (
          <Skeleton key={index} className="h-20 w-full rounded-lg" />
        ))}
      </div>
    );
  }

  if (jobsQuery.isError && !data) {
    return <div className="p-6"><ErrorState message="Failed to load the queue" onRetry={jobsQuery.refetch} /></div>;
  }

  const health = operationHealth.data;
  const selectedCount = selectedIds.size;
  const canActOnSelection = status === "active" || status === "failed" || status === "cancelled";

  return (
    <div className="mx-auto max-w-6xl p-4 md:p-6">
      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-text-primary">Queue</h1>
          <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] ${
            connected ? "bg-emerald-500/15 text-emerald-400" : "bg-amber-500/15 text-amber-400"
          }`}>
            {connected ? <Wifi size={10} /> : <WifiOff size={10} />}
            {connected ? "Live" : "Polling"}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-surface-border bg-surface/60 px-3 py-2 text-xs">
          <Download size={14} className="text-accent" />
          <span className="font-medium text-text-primary">yt-dlp</span>
          <span className="text-text-muted">Installed <b className="font-mono text-text-secondary">{ytdlp.data?.installed_version || "missing"}</b></span>
          <span className="text-text-muted">Latest <b className="font-mono text-text-secondary">{ytdlp.data?.latest_version || "unknown"}</b></span>
          <span className="text-text-muted">
            Checked {ytdlp.data?.last_checked_at ? new Date(ytdlp.data.last_checked_at).toLocaleTimeString() : "not yet"}
          </span>
          <button
            onClick={queueYtdlpUpdate}
            disabled={ytdlpUpdate.isPending}
            className="btn-secondary btn-sm gap-1"
          >
            <RefreshCw size={13} className={ytdlpUpdate.isPending ? "animate-spin" : ""} />
            {ytdlp.data?.update_available ? "Update" : "Check / reinstall"}
          </button>
        </div>
      </div>

      <details className="group mb-4 rounded-lg border border-surface-border bg-surface/40">
        <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs text-text-secondary">
          <ServerCog size={14} className="text-accent" />
          <span className="font-medium">System health</span>
          <span className="text-text-muted">
            {health
              ? `${health.deployment_profile} · ${health.mutations.pending} mutations · ${sumBacklog(health.sidecars)} sidecars · ${sumBacklog(health.files)} file ops`
              : "Loading…"}
          </span>
          <ChevronDown size={14} className="ml-auto transition-transform group-open:rotate-180" />
        </summary>
        {health && (
          <div className="grid gap-2 border-t border-surface-border px-3 py-3 text-xs sm:grid-cols-2 lg:grid-cols-5">
            <div><span className="block text-text-muted">Worker profile</span><b>{health.deployment_profile}</b></div>
            <div><span className="block text-text-muted">Pending mutations</span><b>{health.mutations.pending} / {health.mutation_queue_limit}</b></div>
            <div><span className="block text-text-muted">Oldest mutation</span><b>{compactAge(health.mutations.oldest_age_seconds)}</b></div>
            <div><span className="block text-text-muted">Database retries</span><b>{health.database_retry_count}</b></div>
            <div><span className="block text-text-muted">Outbox / cosmetic writes</span><b>{sumBacklog(health.sidecars) + sumBacklog(health.files)} / {health.cosmetic_writes.pending} of {health.cosmetic_writes.max_pending}</b></div>
          </div>
        )}
      </details>

      <div className="mb-3 border-b border-surface-border">
        <div className="flex overflow-x-auto">
          {STATUS_TABS.map(({ value, label, icon: Icon }) => {
            const count = data?.status_counts[value] ?? 0;
            return (
              <button
                key={value}
                onClick={() => selectStatus(value)}
                className={`relative flex shrink-0 items-center gap-1.5 px-3 py-2.5 text-sm font-medium ${
                  status === value ? "text-accent" : "text-text-muted hover:text-text-secondary"
                }`}
              >
                <Icon size={14} /> {label}
                <span className="rounded-full bg-surface-lighter px-1.5 text-[10px] tabular-nums">{count}</span>
                {status === value && <span className="absolute inset-x-0 bottom-0 h-0.5 rounded-t bg-accent" />}
              </button>
            );
          })}
        </div>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {CATEGORY_TABS.map(({ value, label, icon: Icon }) => {
          const count = data?.category_counts[value] ?? 0;
          return (
            <button
              key={value}
              onClick={() => selectCategory(value)}
              className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium ${
                category === value
                  ? "border-accent/30 bg-accent/15 text-accent"
                  : "border-surface-border bg-surface/40 text-text-muted hover:text-text-secondary"
              }`}
            >
              {Icon && <Icon size={12} />} {label}
              <span className="tabular-nums opacity-70">{count}</span>
            </button>
          );
        })}
      </div>

      <div className="mb-4 flex flex-wrap items-end gap-2 rounded-lg border border-surface-border bg-surface/40 p-3">
        <label className="min-w-56 flex-1 text-[10px] uppercase tracking-wide text-text-muted">
          Search
          <span className="relative mt-1 block">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input
              value={search}
              onChange={(event) => { setSearch(event.target.value); setPage(1); }}
              placeholder="Name, action, URL, type or job ID"
              className="input-field w-full py-1.5 pl-8 text-sm normal-case tracking-normal"
            />
          </span>
        </label>
        <label className="text-[10px] uppercase tracking-wide text-text-muted">
          Added from
          <input
            type="date"
            value={fromDate}
            onChange={(event) => { setFromDate(event.target.value); setPage(1); }}
            className="input-field mt-1 block py-1.5 text-sm normal-case tracking-normal"
          />
        </label>
        <label className="text-[10px] uppercase tracking-wide text-text-muted">
          Added through
          <input
            type="date"
            value={toDate}
            onChange={(event) => { setToDate(event.target.value); setPage(1); }}
            className="input-field mt-1 block py-1.5 text-sm normal-case tracking-normal"
          />
        </label>
        <button onClick={() => jobsQuery.refetch()} className="btn-ghost btn-sm gap-1.5">
          <RefreshCw size={14} className={jobsQuery.isFetching ? "animate-spin" : ""} /> Refresh
        </button>
        {status !== "active" && (
          <button onClick={clearHistory} className="btn-ghost btn-sm gap-1.5 text-red-400">
            <Trash2 size={14} /> Clear this view…
          </button>
        )}
      </div>

      {jobs.length > 0 && (
        <div className="mb-3 flex items-center gap-3 rounded-lg border border-surface-border bg-surface/50 px-3 py-2">
          <input
            type="checkbox"
            checked={jobs.every((job) => selectedIds.has(job.id))}
            onChange={togglePage}
            className="h-4 w-4 accent-accent"
            aria-label="Select this page"
          />
          <span className="flex-1 text-sm text-text-secondary">
            {selectedCount ? `${selectedCount} selected on this page` : "Select this page"}
          </span>
          {selectedCount > 0 && canActOnSelection && (
            <button onClick={handleSelectedAction} className="btn-secondary btn-sm gap-1.5">
              {status === "active" ? <Ban size={13} /> : <RotateCcw size={13} />}
              {status === "active" ? "Cancel selected" : "Retry selected"}
            </button>
          )}
        </div>
      )}

      {jobs.length === 0 ? (
        <div className="card py-14 text-center">
          <Clock3 size={26} className="mx-auto mb-2 text-text-muted" />
          <p className="text-sm text-text-muted">No {status} jobs match these filters.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {jobs.map((job) => (
            <JobCard
              key={job.id}
              job={job}
              telemetry={getJobTelemetry(job.id)}
              logText={expandedJobId === job.id ? expandedLog.data?.log_text : undefined}
              isLoadingLog={expandedJobId === job.id && expandedLog.isLoading}
              isExpanded={expandedJobId === job.id}
              onToggleExpand={() => setExpandedJobId(expandedJobId === job.id ? null : job.id)}
              onRetry={job.status_group === "failed" || job.status_group === "cancelled" ? () => retryJob(job.id) : undefined}
              onCancel={job.status_group === "active" ? () => cancelJob(job) : undefined}
              selected={selectedIds.has(job.id)}
              onSelect={toggleSelected}
            />
          ))}
        </div>
      )}

      <Pagination
        page={data?.page ?? page}
        pageSize={pageSize}
        total={data?.total ?? 0}
        totalPages={data?.total_pages ?? 1}
        onPage={(next) => { setPage(next); setSelectedIds(new Set()); }}
        onPageSize={changePageSize}
      />
      {dialog}
    </div>
  );
}
