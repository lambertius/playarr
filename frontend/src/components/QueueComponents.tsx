/**
 * Queue Components — Sonarr/Radarr-style job cards with real-time telemetry.
 */
import { useState, useMemo, useCallback, useEffect } from "react";
import { Tooltip } from "@/components/Tooltip";
import { Link } from "react-router-dom";
import {
  ChevronDown, ChevronUp, RotateCcw, Copy, Check,
  Activity, Download, Gauge, Clock, AlertTriangle,
  Shield, ShieldAlert, ShieldCheck, Zap, Timer,
  Layers, CircleStop, ChevronRight, CheckCircle2, XCircle, SkipForward, Tag,
} from "lucide-react";
import type {
  JobSummary, JobTelemetry, AttemptRecord, HealthInfo,
  PipelineStep,
} from "@/types";
import { cn, formatBytes, formatDuration, timeAgo, isActiveJob } from "@/lib/utils";
import { StatusBadge } from "@/components/Badges";

// ─── Helpers ──────────────────────────────────────────────

/** Job types whose pipelines dispatch deferred post-processing tasks. */
const DEFERRED_JOB_TYPES = new Set(["import_url", "rescan", "library_import_video", "playlist_import"]);

/** True when deferred tasks are still running (step has not reached completion). */
function isJobFinalizing(job: JobSummary): boolean {
  if (!DEFERRED_JOB_TYPES.has(job.job_type)) return false;
  if (job.status !== "complete" || !job.current_step) return false;
  const stepLower = job.current_step.toLowerCase();
  if (stepLower.endsWith("complete") || job.current_step.startsWith("All ") || job.current_step.startsWith("Pending review")) return false;
  // No time window — show as active until deferred tasks set step to
  // "Import complete".  The backend watchdog handles truly stuck jobs.
  return true;
}

function formatSpeed(bytesPerSec: number): string {
  if (bytesPerSec <= 0) return "0 B/s";
  if (bytesPerSec < 1024) return `${bytesPerSec.toFixed(0)} B/s`;
  if (bytesPerSec < 1024 * 1024) return `${(bytesPerSec / 1024).toFixed(1)} KB/s`;
  return `${(bytesPerSec / (1024 * 1024)).toFixed(2)} MB/s`;
}

function formatEta(seconds: number): string {
  if (seconds <= 0) return "—";
  if (seconds < 60) return `${Math.ceil(seconds)}s`;
  if (seconds < 3600) {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}m ${s}s`;
  }
  return formatDuration(seconds);
}

// ─── Job Health Badge ─────────────────────────────────────

interface JobHealthBadgeProps {
  health?: HealthInfo;
  className?: string;
}

export function JobHealthBadge({ health, className }: JobHealthBadgeProps) {
  if (!health) return null;

  const { risk_score } = health;
  let color: string;
  let Icon: typeof Shield;
  let label: string;

  if (risk_score >= 70) {
    color = "text-red-400 bg-red-500/15";
    Icon = ShieldAlert;
    label = "Critical";
  } else if (risk_score >= 40) {
    color = "text-amber-400 bg-amber-500/15";
    Icon = AlertTriangle;
    label = "Warning";
  } else if (risk_score > 0) {
    color = "text-yellow-300 bg-yellow-500/10";
    Icon = Shield;
    label = "Caution";
  } else {
    color = "text-emerald-400 bg-emerald-500/15";
    Icon = ShieldCheck;
    label = "Healthy";
  }

  return (
    <span className={cn("badge gap-1", color, className)}>
      <Icon size={12} />
      {label}
      {risk_score > 0 && <span className="opacity-60 ml-0.5">{risk_score}</span>}
    </span>
  );
}

// ─── Job Stage Badge ──────────────────────────────────────

interface JobStageBadgeProps {
  step: string;
  className?: string;
}

export function JobStageBadge({ step, className }: JobStageBadgeProps) {
  return (
    <span className={cn("badge badge-purple gap-1", className)}>
      <Activity size={10} />
      {step}
    </span>
  );
}

// ─── Elapsed Timer ────────────────────────────────────────

function JobElapsed({ job }: { job: JobSummary }) {
  const isFinalizing = isJobFinalizing(job);
  const active = isActiveJob(job.status) || isFinalizing;
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);

  const start = job.started_at ? new Date(job.started_at.endsWith("Z") ? job.started_at : job.started_at + "Z").getTime() : null;
  if (!start) return null;

  const end = (job.completed_at && !isFinalizing)
    ? new Date(job.completed_at.endsWith("Z") ? job.completed_at : job.completed_at + "Z").getTime()
    : now;
  const seconds = Math.max(0, Math.floor((end - start) / 1000));

  return (
    <span className="text-[10px] text-text-muted tabular-nums flex items-center gap-1">
      <Timer size={10} />
      {formatDuration(seconds)}
    </span>
  );
}

// ─── Progress Bars ────────────────────────────────────────

interface JobProgressBarsProps {
  job: JobSummary;
  telemetry?: JobTelemetry;
}

export function JobProgressBars({ job, telemetry }: JobProgressBarsProps) {
  const overallPct = job.progress_percent;
  const dlPct = telemetry?.download?.percent ?? 0;
  const isDownloading = job.status === "downloading";
  const isFinalizing = isJobFinalizing(job);

  return (
    <div className="space-y-1.5 w-full">
      {/* Overall pipeline progress */}
      <div className="flex items-center gap-2">
        <span className="text-[10px] text-text-muted w-12 shrink-0">Overall</span>
        <div className="flex-1 h-1.5 bg-surface-lighter rounded-full overflow-hidden">
          <div
            className={cn(
              "h-full rounded-full transition-all duration-500",
              job.status === "failed" ? "bg-danger" :
              isFinalizing ? "bg-amber-500 animate-pulse" :
              job.status === "complete" ? "bg-success" :
              "bg-accent"
            )}
            style={{ width: `${Math.min(overallPct, 100)}%` }}
          />
        </div>
        <span className="text-[10px] text-text-muted w-8 text-right">{overallPct}%</span>
      </div>

      {/* Stage-specific progress (download or processing) */}
      {isDownloading && dlPct > 0 && (
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-text-muted w-12 shrink-0">
            <Download size={10} className="inline mr-0.5" />DL
          </span>
          <div className="flex-1 h-1.5 bg-surface-lighter rounded-full overflow-hidden">
            <div
              className="h-full rounded-full bg-blue-500 transition-all duration-300"
              style={{ width: `${Math.min(dlPct, 100)}%` }}
            />
          </div>
          <span className="text-[10px] text-text-muted w-8 text-right">{dlPct.toFixed(0)}%</span>
        </div>
      )}

      {/* Fragment progress */}
      {isDownloading && telemetry && telemetry.download.fragments_total > 0 && (
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-text-muted w-12 shrink-0">
            <Layers size={10} className="inline mr-0.5" />Frag
          </span>
          <div className="flex-1 h-1.5 bg-surface-lighter rounded-full overflow-hidden">
            <div
              className="h-full rounded-full bg-indigo-500 transition-all duration-300"
              style={{
                width: `${(telemetry.download.fragments_done / telemetry.download.fragments_total) * 100}%`,
              }}
            />
          </div>
          <span className="text-[10px] text-text-muted w-12 text-right">
            {telemetry.download.fragments_done}/{telemetry.download.fragments_total}
          </span>
        </div>
      )}
    </div>
  );
}

// ─── Metrics Row ──────────────────────────────────────────

interface JobMetricsRowProps {
  telemetry?: JobTelemetry;
  status: string;
}

export function JobMetricsRow({ telemetry, status }: JobMetricsRowProps) {
  if (!telemetry) return null;
  const dl = telemetry.download;
  const proc = telemetry.process;
  const isDownloading = status === "downloading";
  const isProcessing = ["normalizing", "remuxing", "analyzing"].includes(status);

  if (!isDownloading && !isProcessing) return null;

  return (
    <div className="flex items-center gap-3 flex-wrap text-[11px] text-text-secondary">
      {isDownloading && (
        <>
          <MetricPill icon={<Gauge size={11} />} label="Speed" value={formatSpeed(dl.speed_bytes)} />
          <MetricPill icon={<Activity size={11} />} label="Avg" value={formatSpeed(dl.avg_speed_30s)} />
          {dl.eta_seconds > 0 && (
            <MetricPill icon={<Timer size={11} />} label="ETA" value={formatEta(dl.eta_seconds)} />
          )}
          {dl.total_bytes > 0 && (
            <MetricPill
              icon={<Download size={11} />}
              label="Size"
              value={`${formatBytes(dl.downloaded_bytes)} / ${formatBytes(dl.total_bytes)}`}
            />
          )}
          {dl.consecutive_stall_seconds > 30 && (
            <span className="badge badge-yellow gap-1 text-[10px]">
              <AlertTriangle size={10} />
              Stalling {Math.floor(dl.consecutive_stall_seconds)}s
            </span>
          )}
        </>
      )}
      {isProcessing && proc.step_name && (
        <>
          {proc.speed_factor > 0 && (
            <MetricPill icon={<Zap size={11} />} label="Speed" value={`${proc.speed_factor.toFixed(1)}x`} />
          )}
          {proc.fps > 0 && (
            <MetricPill icon={<Activity size={11} />} label="FPS" value={proc.fps.toFixed(0)} />
          )}
          {proc.elapsed_seconds > 0 && (
            <MetricPill icon={<Clock size={11} />} label="Elapsed" value={formatEta(proc.elapsed_seconds)} />
          )}
        </>
      )}
    </div>
  );
}

function MetricPill({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-surface-lighter/60">
      {icon}
      <span className="text-text-muted">{label}</span>
      <span className="text-text-primary font-medium">{value}</span>
    </span>
  );
}

// ─── Attempt History ──────────────────────────────────────

interface AttemptHistoryProps {
  attempts: AttemptRecord[];
}

export function AttemptHistory({ attempts }: AttemptHistoryProps) {
  if (!attempts || attempts.length === 0) return null;

  return (
    <div className="space-y-1.5">
      <h4 className="text-xs font-medium text-text-secondary uppercase tracking-wider">
        Attempt History ({attempts.length})
      </h4>
      <div className="space-y-1">
        {attempts.map((a) => (
          <div
            key={a.attempt_num}
            className={cn(
              "flex items-center gap-2 px-2 py-1 rounded text-[11px]",
              a.outcome === "success" ? "bg-emerald-500/5 text-emerald-400" :
              a.outcome === "failed" ? "bg-red-500/5 text-red-400" :
              a.outcome === "cancelled" ? "bg-yellow-500/5 text-yellow-400" :
              "bg-blue-500/5 text-blue-400"
            )}
          >
            <span className="font-mono font-bold">#{a.attempt_num}</span>
            <span className="badge badge-gray text-[9px]">{a.strategy}</span>
            <span className="flex-1 truncate text-text-muted">{a.reason || "—"}</span>
            <span className="font-medium">{a.outcome}</span>
            {a.ended_at && a.started_at && (
              <span className="text-text-muted">
                {((a.ended_at - a.started_at)).toFixed(0)}s
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Log Viewer ───────────────────────────────────────────

interface LogViewerProps {
  logText?: string | null;
  maxHeight?: string;
}

export function LogViewer({ logText, maxHeight = "200px" }: LogViewerProps) {
  if (!logText) {
    return (
      <div className="text-xs text-text-muted italic">No logs available</div>
    );
  }

  return (
    <pre
      className="text-[11px] font-mono leading-relaxed text-text-secondary bg-surface rounded-lg p-3 overflow-auto"
      style={{ maxHeight }}
    >
      {logText}
    </pre>
  );
}

// ─── Diagnostics Copy Button ──────────────────────────────

interface DiagnosticsCopyButtonProps {
  job: JobSummary;
  telemetry?: JobTelemetry;
  logText?: string | null;
}

export function DiagnosticsCopyButton({ job, telemetry, logText }: DiagnosticsCopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    const diag = {
      job_id: job.id,
      job_type: job.job_type,
      status: job.status,
      error_message: job.error_message,
      retry_count: job.retry_count,
      current_step: job.current_step,
      pipeline_steps: job.pipeline_steps,
      progress_percent: job.progress_percent,
      input_url: job.input_url,
      telemetry: telemetry ?? null,
      created_at: job.created_at,
      started_at: job.started_at,
      completed_at: job.completed_at,
    };
    const text = [
      "=== Playarr Job Diagnostics ===",
      JSON.stringify(diag, null, 2),
      "",
      "=== Log ===",
      logText || "(no logs)",
    ].join("\n");

    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [job, telemetry, logText]);

  return (
    <button
      onClick={handleCopy}
      className="btn-ghost btn-sm gap-1 text-[11px]"
    >
      {copied ? <Check size={12} /> : <Copy size={12} />}
      {copied ? "Copied" : "Copy Diagnostics"}
    </button>
  );
}

// ─── Pipeline Step Descriptions ───────────────────────────

const STEP_DESCRIPTIONS: Record<string, string> = {
  "Starting import": "Initialising the import pipeline and preparing the workspace.",
  "Resolving metadata": "Scraping MusicBrainz, Discogs, and other sources to identify artist, title, album, and year.",
  "Analyzing media": "Running ffprobe to extract codec, resolution, bitrate, and duration from the media file.",
  "Resolving entities": "Matching the track to canonical artist, album, and genre entities in the database.",
  "Applying to database": "Writing all resolved metadata, file paths, and media assets to the database in a single transaction.",
  "Finalizing": "Running deferred enrichment tasks: artwork, AI analysis, scene detection, and metadata export.",
  "Import complete": "All pipeline stages finished successfully. The track is ready in the library.",
  "Already best quality": "An existing copy at equal or better quality was found — no re-import needed.",
  "Skipped (duplicate)": "This track already exists in the library and was skipped.",
  "AI enrichment": "Using AI to refine metadata, generate descriptions, and verify correctness.",
  "Generating preview": "Creating a short preview clip for the track.",
  "Analyzing scenes": "Detecting scene changes and extracting representative thumbnails.",
  "Fetching artwork": "Downloading poster, thumbnail, and artist artwork from external sources.",
  "Exporting metadata": "Writing NFO sidecar files for Kodi/Jellyfin compatibility.",
  "Finding matches": "Searching for duplicate or related tracks in the library.",
  "Cleanup": "Removing temporary files and orphaned data.",
};

// ─── Pipeline Steps View (Vertical Timeline) ─────────────

const STEP_STATUS_STYLES = {
  success: { ring: "ring-emerald-500/40", bg: "bg-emerald-500/10", icon: "text-emerald-400" },
  failed:  { ring: "ring-red-500/40",     bg: "bg-red-500/10",     icon: "text-red-400" },
  skipped: { ring: "ring-zinc-500/30",    bg: "bg-zinc-500/10",    icon: "text-zinc-500" },
} as const;

const STEP_ICONS: Record<string, React.ReactNode> = {
  "Starting import":     <Download size={12} />,
  "Resolving metadata":  <Activity size={12} />,
  "Analyzing media":     <Gauge size={12} />,
  "Resolving entities":  <Layers size={12} />,
  "Applying to database":<Shield size={12} />,
  "Finalizing":          <Clock size={12} />,
  "Import complete":     <CheckCircle2 size={12} />,
  "AI enrichment":       <Zap size={12} />,
  "Generating preview":  <Activity size={12} />,
  "Analyzing scenes":    <Activity size={12} />,
  "Fetching artwork":    <Download size={12} />,
  "Exporting metadata":  <Copy size={12} />,
  "Finding matches":     <Activity size={12} />,
  "Cleanup":             <RotateCcw size={12} />,
};

// Display names for backend step strings (British English)
const STEP_DISPLAY_NAMES: Record<string, string> = {
  "Starting import":      "Starting import",
  "Resolving metadata":   "Resolving metadata",
  "Analyzing media":      "Analysing media",
  "Resolving entities":   "Resolving entities",
  "Applying to database": "Applying to database",
  "Finalizing":           "Finalising",
  "Import complete":      "Import complete",
  "AI enrichment":        "AI enrichment",
  "Generating preview":   "Generating preview",
  "Analyzing scenes":     "Analysing scenes",
  "Fetching artwork":     "Fetching artwork",
  "Exporting metadata":   "Exporting metadata",
  "Finding matches":      "Finding matches",
  "Cleanup":              "Cleanup",
};

interface PipelineStepsViewProps {
  steps?: PipelineStep[] | null;
}

export function PipelineStepsView({ steps }: PipelineStepsViewProps) {
  if (!steps || steps.length === 0) return null;

  return (
    <div className="relative">
      {steps.map((s, i) => {
        const isAiError = s.type === "ai_error";
        const statusKey = isAiError ? "failed" : s.status === "success" ? "success" : s.status === "failed" ? "failed" : "skipped";
        const st = STEP_STATUS_STYLES[statusKey];
        const desc = STEP_DESCRIPTIONS[s.step];
        const isLast = i === steps.length - 1;
        const label = isAiError ? (s.code || "AI Error") : (STEP_DISPLAY_NAMES[s.step] ?? s.step);
        const icon = isAiError ? <Zap size={12} /> : STEP_ICONS[s.step] ?? <ChevronRight size={12} />;

        return (
          <div key={i} className="relative flex gap-2.5">
            {/* Left: icon circle + connecting line */}
            <div className="flex flex-col items-center flex-shrink-0">
              <div className={cn(
                "w-6 h-6 rounded-full ring-1.5 flex items-center justify-center",
                st.ring, st.bg, st.icon,
              )}>
                {statusKey === "skipped" ? <SkipForward size={10} /> : icon}
              </div>
              {!isLast && (
                <div className="w-px flex-1 min-h-[8px] bg-surface-border/40" />
              )}
            </div>

            {/* Right: content */}
            <div className={cn("flex-1 min-w-0 pb-2", isLast ? "pb-0" : "")}>
              <div className="flex items-center gap-2 min-h-[24px]">
                <span className={cn(
                  "text-xs font-medium",
                  statusKey === "skipped" ? "text-text-muted" : "text-text-primary",
                )}>
                  {label}
                </span>
                {statusKey === "success" && <CheckCircle2 size={10} className="text-emerald-400" />}
                {statusKey === "failed" && <XCircle size={10} className="text-red-400" />}
              </div>
              {desc && statusKey !== "skipped" && (
                <p className="text-[10px] text-text-muted leading-snug mt-0.5">{desc}</p>
              )}
              {isAiError && s.step && (
                <p className="text-[10px] text-red-400/80 leading-snug mt-0.5">{s.step}</p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ─── Retry Countdown ──────────────────────────────────────

interface RetryCountdownProps {
  retryCount: number;
  maxRetries?: number;
}

export function RetryCountdown({ retryCount, maxRetries = 5 }: RetryCountdownProps) {
  if (retryCount === 0) return null;

  return (
    <span className="badge badge-yellow gap-1 text-[10px]">
      <RotateCcw size={10} />
      Retry {retryCount}/{maxRetries}
    </span>
  );
}

// ─── Expanded Panel ───────────────────────────────────────

interface JobExpandedPanelProps {
  job: JobSummary;
  telemetry?: JobTelemetry;
  logText?: string | null;
  isLoadingLog: boolean;
}

export function JobExpandedPanel({ job, telemetry, logText, isLoadingLog }: JobExpandedPanelProps) {
  const [activeTab, setActiveTab] = useState<"log" | "attempts" | "pipeline">("log");

  const tabs = useMemo(() => {
    const t: { key: typeof activeTab; label: string; count?: number }[] = [
      { key: "log", label: "Log" },
      { key: "pipeline", label: "Pipeline", count: job.pipeline_steps?.length },
    ];
    if (telemetry?.attempts && telemetry.attempts.length > 0) {
      t.splice(1, 0, { key: "attempts", label: "Attempts", count: telemetry.attempts.length });
    }
    return t;
  }, [job.pipeline_steps, telemetry]);

  return (
    <div className="mt-3 pt-3 border-t border-surface-border space-y-3">
      {/* Tabs */}
      <div className="flex items-center gap-1">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={cn(
              "px-2.5 py-1 rounded text-[11px] font-medium transition-colors",
              activeTab === tab.key
                ? "bg-accent/20 text-accent"
                : "text-text-muted hover:text-text-secondary hover:bg-surface-lighter"
            )}
          >
            {tab.label}
            {tab.count != null && (
              <span className="ml-1 opacity-60">{tab.count}</span>
            )}
          </button>
        ))}
        <div className="flex-1" />
        <DiagnosticsCopyButton job={job} telemetry={telemetry} logText={logText} />
      </div>

      {/* Tab content */}
      {activeTab === "log" && (
        isLoadingLog
          ? <div className="skeleton h-24 w-full" />
          : <LogViewer logText={logText} />
      )}

      {activeTab === "attempts" && telemetry?.attempts && (
        <AttemptHistory attempts={telemetry.attempts} />
      )}

      {activeTab === "pipeline" && (
        <PipelineStepsView steps={job.pipeline_steps} />
      )}

      {/* AI provider error banner */}
      {(() => {
        const aiErrors = job.pipeline_steps?.filter(s => s.type === "ai_error") ?? [];
        if (aiErrors.length === 0) return null;
        return (
          <div className="bg-orange-500/5 border border-orange-500/20 rounded-lg p-2.5 text-[11px] text-orange-400 space-y-1">
            <div className="flex items-start gap-1.5">
              <Zap size={12} className="shrink-0 mt-0.5" />
              <div>
                <div className="font-medium">AI provider error</div>
                {aiErrors.map((e, i) => (
                  <div key={i} className="mt-0.5 text-orange-300/80">
                    <span className="font-mono text-[10px] bg-orange-500/10 rounded px-1 py-0.5 mr-1">{e.code}</span>
                    {e.step}
                  </div>
                ))}
                <div className="mt-1.5 text-orange-300/60">
                  Metadata was imported without AI enrichment. Retry or re-import this job to attempt AI analysis again.
                </div>
              </div>
            </div>
          </div>
        );
      })()}

      {/* Error message */}
      {job.error_message && (
        job.error_message.includes("Server restarted") ? (
          <div className="bg-amber-500/5 border border-amber-500/20 rounded-lg p-2.5 text-[11px] text-amber-400">
            <AlertTriangle size={12} className="inline mr-1" />
            {job.error_message} — use Retry to re-queue this import.
          </div>
        ) : (
          <div className="bg-red-500/5 border border-red-500/20 rounded-lg p-2.5 text-[11px] text-red-400">
            <AlertTriangle size={12} className="inline mr-1" />
            {job.error_message}
          </div>
        )
      )}
    </div>
  );
}

// ─── Job Card ─────────────────────────────────────────────

interface JobCardProps {
  job: JobSummary;
  telemetry?: JobTelemetry;
  logText?: string | null;
  isLoadingLog: boolean;
  isExpanded: boolean;
  onToggleExpand: () => void;
  onRetry?: () => void;
  onCancel?: () => void;
  selected?: boolean;
  onSelect?: (jobId: number) => void;
}

export function JobCard({
  job,
  telemetry,
  logText,
  isLoadingLog,
  isExpanded,
  onToggleExpand,
  onRetry,
  onCancel,
  selected,
  onSelect,
}: JobCardProps) {
  const active = isActiveJob(job.status);
  const isInterrupted = job.status === "failed" && !!job.error_message && job.error_message.includes("Server restarted");
  const isFinalizing = isJobFinalizing(job);
  const canRetry = job.status === "failed" || job.status === "cancelled";
  const canCancel = active || isFinalizing;

  return (
    <div
      className={cn(
        "card transition-all duration-200",
        active && "border-accent/30",
        isInterrupted && "border-amber-500/30",
        job.status === "failed" && !isInterrupted && "border-red-500/30",
        job.status === "complete" && "border-surface-border",
      )}
    >
      {/* Header row */}
      <div className="flex items-start gap-3">
        {/* Select checkbox */}
        {onSelect && (
          <input
            type="checkbox"
            checked={!!selected}
            onChange={() => onSelect(job.id)}
            className="mt-1.5 accent-accent w-4 h-4 cursor-pointer shrink-0"
          />
        )}
        {/* Expand toggle */}
        <button
          onClick={onToggleExpand}
          className="mt-0.5 p-0.5 text-text-muted hover:text-text-primary transition-colors"
        >
          {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>

        {/* Main content */}
        <div className="flex-1 min-w-0 space-y-1.5">
          {/* Title + badges */}
          <div className="flex items-center gap-2">
            {job.video_id ? (
              <Link
                to={`/video/${job.video_id}`}
                className="font-medium text-sm truncate min-w-0 text-accent hover:underline"
              >
                {job.display_name || job.input_url || `Job #${job.id}`}
              </Link>
            ) : (
              <span className="font-medium text-sm truncate min-w-0">
                {job.display_name || job.input_url || `Job #${job.id}`}
              </span>
            )}
            {job.action_label && (
              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase tracking-wider bg-accent/10 text-accent border border-accent/20 shrink-0 whitespace-nowrap">
                <Tag size={8} />
                {job.action_label}
              </span>
            )}
            <div className="flex items-center gap-2 shrink-0 ml-auto">
              {active && telemetry?.health && (
                <JobHealthBadge health={telemetry.health} />
              )}
              {job.retry_count > 0 && (
                <RetryCountdown retryCount={job.retry_count} />
              )}
              {job.current_step && (active || (job.status === "complete" && !job.current_step.toLowerCase().endsWith("complete"))) && (
                <JobStageBadge step={job.current_step} />
              )}
              <JobElapsed job={job} />
              <StatusBadge status={job.status} jobType={job.job_type} currentStep={job.current_step} errorMessage={job.error_message} completedAt={job.completed_at} updatedAt={job.updated_at} />
            </div>
          </div>

          {/* Progress bars */}
          {(active || job.status === "complete") && (
            <JobProgressBars job={job} telemetry={telemetry} />
          )}

          {/* Skip/fail reason — visible without expanding */}
          {job.status === "skipped" && job.current_step && (
            <div className="flex items-center gap-1.5 text-[11px] text-orange-400 bg-orange-500/5 border border-orange-500/15 rounded px-2 py-1">
              <AlertTriangle size={11} className="shrink-0" />
              <span className="truncate">{job.current_step.replace(/^Skipped:\s*/i, "")}</span>
              {job.video_id && (
                <Link
                  to={`/video/${job.video_id}`}
                  className="shrink-0 text-[10px] font-medium text-orange-300 hover:text-orange-200 underline underline-offset-2"
                >
                  View match
                </Link>
              )}
            </div>
          )}
          {job.status === "failed" && job.error_message && !isExpanded && (
            <div className="flex items-center gap-1.5 text-[11px] text-red-400 bg-red-500/5 border border-red-500/15 rounded px-2 py-1">
              <AlertTriangle size={11} className="shrink-0" />
              <span className="truncate">{job.error_message}</span>
            </div>
          )}
          {/* Compact AI error badge when collapsed */}
          {!isExpanded && job.pipeline_steps?.some(s => s.type === "ai_error") && (
            <div className="flex items-center gap-1.5 text-[11px] text-orange-400 bg-orange-500/5 border border-orange-500/15 rounded px-2 py-1">
              <Zap size={11} className="shrink-0" />
              <span className="truncate">AI provider error — expand for details</span>
            </div>
          )}

          {/* Metrics row (only for active downloads/processing) */}
          {active && <JobMetricsRow telemetry={telemetry} status={job.status} />}

          {/* Meta line */}
          <div className="flex items-center gap-3 text-[11px] text-text-muted">
            <span className="badge badge-gray text-[9px]">{job.job_type}</span>
            <span>{timeAgo(job.created_at)}</span>
            {job.completed_at && (
              <span>Finished {timeAgo(job.completed_at)}</span>
            )}
            {job.video_id && (
              <span className="text-text-muted">Video #{job.video_id}</span>
            )}
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-1 shrink-0">
          {canRetry && onRetry && (
            <Tooltip content="Retry this job">
            <button onClick={onRetry} className="btn-ghost btn-sm gap-1">
              <RotateCcw size={14} />
            </button>
            </Tooltip>
          )}
          {canCancel && onCancel && (
            <Tooltip content="Cancel this job">
            <button onClick={onCancel} className="btn-danger btn-sm gap-1">
              <CircleStop size={14} />
            </button>
            </Tooltip>
          )}
        </div>
      </div>

      {/* Expanded panel */}
      {isExpanded && (
        <JobExpandedPanel
          job={job}
          telemetry={telemetry}
          logText={logText}
          isLoadingLog={isLoadingLog}
        />
      )}
    </div>
  );
}
