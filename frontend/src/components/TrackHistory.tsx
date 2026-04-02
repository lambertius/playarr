import {
  History, CheckCircle2, XCircle, Clock, AlertCircle,
  ChevronDown, ChevronUp,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import type { JobSummary, ProcessingState } from "@/types";
import { StatusBadge } from "@/components/Badges";
import { ProcessingSteps } from "@/components/ProcessingSteps";
import { PipelineStepsView, LogViewer } from "@/components/QueueComponents";
import { useJobLog } from "@/hooks/queries";
import { cn, timeAgo, isActiveJob } from "@/lib/utils";

interface TrackHistoryProps {
  jobs: JobSummary[];
  snapshots?: Array<{ id: number; reason: string; created_at: string }>;
  processingState?: ProcessingState | null;
}

/** Merge jobs and snapshots into a unified timeline sorted by date descending. */
type TimelineEntry =
  | { kind: "job"; data: JobSummary; date: Date }
  | { kind: "snapshot"; data: { id: number; reason: string; created_at: string }; date: Date };

function buildTimeline(
  jobs: JobSummary[],
  snapshots: Array<{ id: number; reason: string; created_at: string }> = []
): TimelineEntry[] {
  const entries: TimelineEntry[] = [
    ...jobs.map((j) => ({
      kind: "job" as const,
      data: j,
      date: new Date(j.created_at),
    })),
    ...snapshots.map((s) => ({
      kind: "snapshot" as const,
      data: s,
      date: new Date(s.created_at),
    })),
  ];
  return entries.sort((a, b) => b.date.getTime() - a.date.getTime());
}

function statusIcon(status: string) {
  switch (status) {
    case "complete":
      return <CheckCircle2 size={14} className="text-success" />;
    case "failed":
      return <XCircle size={14} className="text-danger" />;
    case "cancelled":
      return <AlertCircle size={14} className="text-warning" />;
    default:
      return <Clock size={14} className="text-accent animate-pulse" />;
  }
}

/** Full import card shown at the top of Track History — mirrors the Queue JobCard style. */
function FeaturedJobCard({ job }: { job: JobSummary }) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const active = isActiveJob(job.status);
  const { data: logData, isLoading: isLoadingLog } = useJobLog(detailsOpen || active ? job.id : null);

  return (
    <div
      className={cn(
        "border rounded-lg p-3 space-y-2",
        active ? "border-accent/30 bg-accent/5" :
        job.status === "failed" ? "border-red-500/30 bg-red-500/5" :
        "border-border"
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-2 flex-wrap">
        {job.video_id ? (
          <Link
            to={`/video/${job.video_id}`}
            className="font-medium text-sm text-accent hover:underline truncate"
          >
            {job.display_name || job.input_url || `Job #${job.id}`}
          </Link>
        ) : (
          <span className="font-medium text-sm text-text-primary truncate">
            {job.display_name || job.input_url || `Job #${job.id}`}
          </span>
        )}
        <StatusBadge status={job.status} currentStep={job.current_step} />
        {job.current_step && (active || (job.status === "complete" && job.current_step !== "Import complete")) && (
          <span className="badge badge-purple gap-1 text-[10px]">
            {job.current_step}
          </span>
        )}
        {job.retry_count > 0 && (
          <span className="badge badge-yellow gap-1 text-[10px]">
            Retry {job.retry_count}
          </span>
        )}
      </div>

      {/* Progress bar */}
      {(active || job.status === "complete") && (
        <div className="flex items-center gap-2">
          <div className="flex-1 h-1.5 bg-surface-lighter rounded-full overflow-hidden">
            <div
              className={cn(
                "h-full rounded-full transition-all duration-500",
                job.status === "failed" ? "bg-danger" :
                (job.status === "complete" && job.current_step && job.current_step !== "Import complete") ? "bg-amber-500 animate-pulse" :
                job.status === "complete" ? "bg-success" :
                "bg-accent"
              )}
              style={{ width: `${Math.min(job.progress_percent, 100)}%` }}
            />
          </div>
          <span className="text-[10px] text-text-muted w-8 text-right">{job.progress_percent}%</span>
        </div>
      )}

      {/* Pipeline steps */}
      {job.pipeline_steps && job.pipeline_steps.length > 0 && (
        <PipelineStepsView steps={job.pipeline_steps} />
      )}

      {/* Meta line */}
      <div className="flex items-center gap-3 text-[11px] text-text-muted">
        <span className="badge badge-gray text-[9px]">{job.job_type}</span>
        <span title={new Date(job.created_at).toLocaleString()}>
          {timeAgo(job.created_at)}
        </span>
        {job.completed_at && (
          <span>Finished {timeAgo(job.completed_at)}</span>
        )}
      </div>

      {/* Error message */}
      {job.error_message && (
        <div className="bg-red-500/5 border border-red-500/20 rounded-lg p-2 text-[11px] text-red-400">
          <AlertCircle size={12} className="inline mr-1" />
          {job.error_message}
        </div>
      )}

      {/* Expandable log */}
      <button
        onClick={() => setDetailsOpen(!detailsOpen)}
        className="text-xs text-text-muted hover:text-text-secondary flex items-center gap-1 transition-colors"
      >
        {detailsOpen ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        {detailsOpen ? "Hide log" : "Show log"}
      </button>

      {detailsOpen && (
        isLoadingLog
          ? <div className="h-24 rounded bg-surface-lighter animate-pulse" />
          : <LogViewer logText={logData?.log_text} maxHeight="200px" />
      )}
    </div>
  );
}

function JobTimelineEntry({ job }: { job: JobSummary }) {
  const [expanded, setExpanded] = useState(false);
  const isActive = !["complete", "failed", "cancelled"].includes(job.status);
  const { data: logData } = useJobLog(isActive ? job.id : null);

  return (
    <div className="relative pl-6 pb-4 last:pb-0 group">
      {/* Timeline line */}
      <div className="absolute left-[7px] top-5 bottom-0 w-px bg-surface-border group-last:hidden" />
      {/* Timeline dot */}
      <div className="absolute left-0 top-1">{statusIcon(job.status)}</div>

      <div className="space-y-1">
        <div className="flex items-center gap-2 flex-wrap">
          <StatusBadge status={job.status} currentStep={job.current_step} />
          <span className="text-sm text-text-primary font-medium">{job.job_type}</span>
          {job.current_step && (
            <span className="text-xs text-text-muted">→ {job.current_step}</span>
          )}
          <span className="text-xs text-text-muted ml-auto" title={new Date(job.created_at).toLocaleString()}>
            {timeAgo(job.created_at)}
          </span>
        </div>

        {/* Progress bar for active jobs */}
        {isActive && job.progress_percent > 0 && (
          <div className="h-1 rounded-full bg-surface-lighter overflow-hidden">
            <div
              className="h-full bg-accent transition-all duration-500"
              style={{ width: `${job.progress_percent}%` }}
            />
          </div>
        )}

        {job.error_message && (
          <p className="text-xs text-danger break-words">{job.error_message}</p>
        )}

        {/* Expandable log */}
        {(logData?.log_text || job.pipeline_steps) && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-text-muted hover:text-text-secondary flex items-center gap-1 transition-colors"
          >
            {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            {expanded ? "Hide details" : "Show details"}
          </button>
        )}

        {expanded && (
          <div className="mt-1">
            {job.pipeline_steps && job.pipeline_steps.length > 0 && (
              <div className="mb-2">
                <PipelineStepsView steps={job.pipeline_steps} />
              </div>
            )}
            {logData?.log_text && (
              <pre className="max-h-40 overflow-y-auto rounded bg-surface p-2 text-[11px] text-text-muted font-mono leading-relaxed whitespace-pre-wrap">
                {logData.log_text.slice(-3000)}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function SnapshotTimelineEntry({
  snapshot,
}: {
  snapshot: { id: number; reason: string; created_at: string };
}) {
  return (
    <div className="relative pl-6 pb-4 last:pb-0 group">
      <div className="absolute left-[7px] top-5 bottom-0 w-px bg-surface-border group-last:hidden" />
      <div className="absolute left-0 top-1">
        <History size={14} className="text-text-muted" />
      </div>
      <div className="flex items-center gap-2">
        <span className="badge-gray text-[10px]">{snapshot.reason}</span>
        <span className="text-xs text-text-muted" title={new Date(snapshot.created_at).toLocaleString()}>
          {timeAgo(snapshot.created_at)}
        </span>
      </div>
    </div>
  );
}

export function TrackHistory({ jobs, snapshots = [], processingState }: TrackHistoryProps) {
  const [expanded, setExpandedRaw] = useState(() => localStorage.getItem("track_history_expanded") === "true");
  const setExpanded = (v: boolean) => { localStorage.setItem("track_history_expanded", String(v)); setExpandedRaw(v); };
  const [showAll, setShowAll] = useState(false);
  const timeline = buildTimeline(jobs, snapshots);

  // Find the most recent import job to feature at the top
  const importTypes = ["import_video", "import_url", "library_scan"];
  const featuredJob = jobs
    .slice()
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
    .find((j) => importTypes.includes(j.job_type)) ?? (jobs.length > 0 ? jobs.reduce((a, b) =>
      new Date(b.created_at).getTime() > new Date(a.created_at).getTime() ? b : a
    ) : null);

  // Timeline entries excluding the featured job
  const restTimeline = featuredJob
    ? timeline.filter((e) => !(e.kind === "job" && e.data.id === featuredJob.id))
    : timeline;

  const maxShow = 8;
  const visible = showAll ? restTimeline : restTimeline.slice(0, maxShow);
  const hasMore = restTimeline.length > maxShow;

  return (
    <div className="card">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full text-left"
      >
        <History size={16} className="text-accent" />
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide flex-1">
          Track History
          <span className="text-[10px] font-normal text-text-muted ml-2">
            {timeline.length} event{timeline.length !== 1 ? "s" : ""}
          </span>
        </h3>
        {expanded ? (
          <ChevronUp size={14} className="text-text-muted" />
        ) : (
          <ChevronDown size={14} className="text-text-muted" />
        )}
      </button>

      {expanded && (
        <div className="mt-4 space-y-4">
          {/* Processing Progress */}
          {processingState && (
            <ProcessingSteps state={processingState} embedded />
          )}

          {/* Featured import card */}
          {featuredJob && (
            <FeaturedJobCard job={featuredJob} />
          )}

          {/* Remaining timeline */}
          {restTimeline.length === 0 && !featuredJob ? (
            <p className="text-sm text-text-muted py-4 text-center">No history yet</p>
          ) : restTimeline.length > 0 ? (
            <>
              <div>
                {visible.map((entry) =>
                  entry.kind === "job" ? (
                    <JobTimelineEntry key={`job-${entry.data.id}`} job={entry.data} />
                  ) : (
                    <SnapshotTimelineEntry key={`snap-${entry.data.id}`} snapshot={entry.data} />
                  )
                )}
              </div>

              {hasMore && (
                <button
                  onClick={() => setShowAll(!showAll)}
                  className="text-xs text-accent hover:underline flex items-center gap-1"
                >
                  {showAll ? (
                    <>
                      <ChevronUp size={12} /> Show less
                    </>
                  ) : (
                    <>
                      <ChevronDown size={12} /> Show all {timeline.length} events
                    </>
                  )}
                </button>
              )}
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}
