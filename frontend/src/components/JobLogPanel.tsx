import type { JobSummary } from "@/types";
import { StatusBadge } from "@/components/Badges";
import { useJobLog } from "@/hooks/queries";
import { timeAgo } from "@/lib/utils";

interface JobLogPanelProps {
  jobs: JobSummary[];
  maxShow?: number;
}

export function JobLogPanel({ jobs, maxShow = 5 }: JobLogPanelProps) {
  const recent = jobs.slice(0, maxShow);

  if (recent.length === 0) {
    return (
      <p className="text-sm text-text-muted py-4 text-center">No recent jobs</p>
    );
  }

  return (
    <div className="space-y-2">
      {recent.map((job) => (
        <JobEntry key={job.id} job={job} />
      ))}
    </div>
  );
}

function JobEntry({ job }: { job: JobSummary }) {
  const isFinished = job.status === "complete" || job.status === "cancelled";
  const { data: logData } = useJobLog(job.id, isFinished);

  return (
    <div className="rounded-lg border border-surface-border p-3 text-sm">
      <div className="flex items-center gap-2 mb-1">
        <StatusBadge status={job.status} currentStep={job.current_step} />
        <span className="text-text-secondary">{job.job_type}</span>
        {job.current_step && (
          <span className="text-xs text-text-muted">→ {job.current_step}</span>
        )}
        <span className="ml-auto text-xs text-text-muted">{timeAgo(job.created_at)}</span>
      </div>

      {/* Progress bar */}
      {job.progress_percent > 0 && job.status !== "complete" && (
        <div className="h-1 rounded-full bg-surface-lighter overflow-hidden mb-1">
          <div
            className="h-full bg-accent transition-all duration-500"
            style={{ width: `${job.progress_percent}%` }}
          />
        </div>
      )}

      {job.error_message && (
        <p className="text-xs text-danger mt-1 truncate">{job.error_message}</p>
      )}

      {logData?.log_text && (
        <pre className="mt-2 max-h-32 overflow-y-auto rounded bg-surface p-2 text-[11px] text-text-muted font-mono leading-relaxed whitespace-pre-wrap">
          {logData.log_text.slice(-2000)}
        </pre>
      )}
    </div>
  );
}
