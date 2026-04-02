import type { JobStatus } from "@/types";
import { cn } from "@/lib/utils";

// ─── Version Badge ────────────────────────────────────────
const versionConfig: Record<string, { label: string; className: string }> = {
  cover:       { label: "Cover",             className: "bg-orange-500/15 text-orange-400" },
  live:        { label: "Live",              className: "bg-purple-500/15 text-purple-400" },
  alternate:   { label: "Alternate Version", className: "bg-blue-500/15 text-blue-400" },
  uncensored:  { label: "Uncensored",        className: "bg-rose-500/15 text-rose-400" },
};

interface VersionBadgeProps {
  versionType?: string;
  alternateLabel?: string | null;
  className?: string;
}

export function VersionBadge({ versionType, alternateLabel, className }: VersionBadgeProps) {
  if (!versionType || versionType === "normal") return null;
  const config = versionConfig[versionType];
  if (!config) return null;
  const effectiveLabel = alternateLabel && alternateLabel.toLowerCase() !== "uncensored" ? alternateLabel : null;
  const label = versionType === "alternate" && effectiveLabel ? effectiveLabel : config.label;
  return <span className={cn("badge", config.className, className)}>{label}</span>;
}

// ─── Review Status Badge ──────────────────────────────────
const reviewConfig: Record<string, { label: string; className: string }> = {
  needs_human_review: { label: "Needs Review",    className: "bg-yellow-500/15 text-yellow-400" },
  needs_ai_review:    { label: "Pending AI",      className: "bg-cyan-500/15 text-cyan-400" },
  reviewed:           { label: "Reviewed",         className: "bg-emerald-500/15 text-emerald-400" },
};

interface ReviewStatusBadgeProps {
  reviewStatus?: string;
  className?: string;
}

export function ReviewStatusBadge({ reviewStatus, className }: ReviewStatusBadgeProps) {
  if (!reviewStatus || reviewStatus === "none") return null;
  const config = reviewConfig[reviewStatus];
  if (!config) return null;
  return <span className={cn("badge", config.className, className)}>{config.label}</span>;
}

const statusConfig: Record<JobStatus, { label: string; className: string }> = {
  queued:       { label: "Queued",       className: "badge-gray" },
  downloading:  { label: "Downloading",  className: "badge-blue" },
  downloaded:   { label: "Downloaded",   className: "badge-blue" },
  remuxing:     { label: "Remuxing",     className: "badge-blue" },
  analyzing:    { label: "Analysing",    className: "badge-purple" },
  normalizing:  { label: "Normalising",  className: "badge-purple" },
  tagging:      { label: "Tagging",      className: "badge-purple" },
  writing_nfo:  { label: "Writing NFO",  className: "badge-purple" },
  asset_fetch:  { label: "Fetching Art", className: "badge-purple" },
  complete:     { label: "Complete",     className: "badge-green" },
  failed:       { label: "Failed",       className: "badge-red" },
  cancelled:    { label: "Cancelled",    className: "badge-yellow" },
  skipped:      { label: "Skipped",      className: "bg-orange-500/15 text-orange-400" },
};

interface StatusBadgeProps {
  status: JobStatus;
  className?: string;
  currentStep?: string | null;
  errorMessage?: string | null;
  completedAt?: string | null;
  updatedAt?: string | null;
}

export function StatusBadge({ status, className, currentStep, errorMessage, completedAt, updatedAt }: StatusBadgeProps) {
  const isFinalizing = status === "complete" && !!currentStep && !currentStep.endsWith("complete") && !currentStep.startsWith("All ") && !currentStep.startsWith("Pending review");
  if (isFinalizing) {
    // Use the most recent of completedAt / updatedAt to avoid false "Stuck"
    // while deferred tasks are still actively processing.
    if (completedAt) {
      const toMs = (ts: string) => new Date(ts.endsWith("Z") ? ts : ts + "Z").getTime();
      const latestMs = Math.max(toMs(completedAt), updatedAt ? toMs(updatedAt) : 0);
      const ageMin = (Date.now() - latestMs) / 60_000;
      if (ageMin > 5) {
        return <span className={cn("bg-red-500/15 text-red-400", className)}>Stuck</span>;
      }
    }
    return <span className={cn("bg-amber-500/15 text-amber-400", className)}>Finalising</span>;
  }
  const isInterrupted = status === "failed" && !!errorMessage && errorMessage.includes("Server restarted");
  if (isInterrupted) {
    return <span className={cn("bg-amber-500/15 text-amber-400", className)}>Interrupted</span>;
  }
  const config = statusConfig[status] ?? { label: status, className: "badge-gray" };
  return <span className={cn(config.className, className)}>{config.label}</span>;
}

// ─── Quality Badge ────────────────────────────────────────
interface QualityBadgeProps {
  resolution?: string | null;
  className?: string;
}

export function QualityBadge({ resolution, className }: QualityBadgeProps) {
  if (!resolution) return null;
  const is4k = resolution.includes("2160");
  const isHd = resolution.includes("1080") || resolution.includes("1440");
  return (
    <span
      className={cn(
        "badge",
        is4k ? "bg-amber-500/15 text-amber-400" : isHd ? "badge-green" : "badge-gray",
        className,
      )}
    >
      {resolution}
    </span>
  );
}

// ─── Enrichment Badge ─────────────────────────────────────
const enrichmentConfig: Record<string, { label: string; className: string }> = {
  enriched: { label: "AI",     className: "bg-emerald-500/15 text-emerald-400" },
  partial:  { label: "AI ½",   className: "bg-yellow-500/15 text-yellow-400" },
  pending:  { label: "No AI",  className: "bg-zinc-500/15 text-zinc-400" },
};

interface EnrichmentBadgeProps {
  status?: string;
  className?: string;
}

export function EnrichmentBadge({ status, className }: EnrichmentBadgeProps) {
  if (!status || status === "enriched") return null;
  const config = enrichmentConfig[status];
  if (!config) return null;
  return <span className={cn("badge", config.className, className)}>{config.label}</span>;
}

// ─── Source Badge ─────────────────────────────────────────
interface SourceBadgeProps {
  provider: string;
  className?: string;
  iconOnly?: boolean;
}

function YouTubeLogo({ size = 16 }: { size?: number }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 28.57 20"
      width={size}
      height={size * 0.7}
      className="inline-block"
    >
      <path
        d="M27.97 3.12A3.58 3.58 0 0 0 25.45.6C23.21 0 14.28 0 14.28 0S5.36 0 3.12.6A3.58 3.58 0 0 0 .6 3.12C0 5.36 0 10 0 10s0 4.64.6 6.88a3.58 3.58 0 0 0 2.52 2.52C5.36 20 14.28 20 14.28 20s8.93 0 11.17-.6a3.58 3.58 0 0 0 2.52-2.52c.6-2.24.6-6.88.6-6.88s0-4.64-.6-6.88Z"
        fill="#FF0000"
      />
      <path d="m11.43 14.28 7.44-4.28-7.44-4.28v8.56Z" fill="#fff" />
    </svg>
  );
}

export function SourceBadge({ provider, className, iconOnly }: SourceBadgeProps) {
  if (provider === "youtube") {
    const icon = <YouTubeLogo size={iconOnly ? 18 : 14} />;
    if (iconOnly) return <span className={className}>{icon}</span>;
    return (
      <span
        className={cn(
          "badge bg-red-500/15 text-red-400 gap-1",
          className,
        )}
      >
        {icon}
      </span>
    );
  }
  if (provider === "wikipedia") {
    const sz = iconOnly ? 18 : 14;
    const icon = (
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width={sz} height={sz} fill="currentColor" className="inline-block">
        <path d="M120.3 20.5c-.8 0-2.6.3-5.4.8-2.3.5-4 1.2-5 2.2-1.7 1.7-3.5 4.8-5.4 9.3L78.7 97.5c-1 2.4-2.2 3.6-3.6 3.6-1.5 0-2.8-1.3-3.8-3.8L55.5 62.5 38.6 97.3c-1 2.4-2.2 3.6-3.6 3.6-1.4 0-2.6-1.2-3.6-3.6L5.2 32c-2-4.8-3.7-7.8-5-9-1.1-1-3-1.7-5.8-2.2l.2-2.4h29.2l-.1 2.4c-3.3.3-5.5.8-6.5 1.5-1 .7-1.5 1.8-1.5 3.3 0 1.1.4 2.8 1.3 5.2l18.4 46.2 12-25.4-7.5-16.5c-2.6-5.7-4.5-9.2-5.7-10.5-1-1-2.8-1.8-5.4-2.2l.1-2.2h27.8l-.1 2.4c-2.7.2-4.5.6-5.5 1.3s-1.5 1.8-1.5 3.3c0 1.5.5 3.5 1.5 6l5.3 13 5.6-11.8c1-2.2 1.5-4.2 1.5-5.8 0-2.6-2-4.3-6-5.1l.1-2.8H71l-.1 2.4c-2.2.2-3.9.8-5 1.5-1.7 1.3-3.7 4.3-5.8 9l-8.5 17.8 13.3 30.7L85 32.5c1-2.5 1.5-4.5 1.5-5.8 0-2.7-2.2-4.3-6.5-4.8l.1-2.4h26.5l-.1 2.4c-2.6.3-4.4.8-5.3 1.5-1.5 1.3-3.3 4.5-5.3 9.5l-6 14.8 1.4 3.2 16.5-40.8c2.4.1 6.8.1 13.3 0l-.8 10.4z"/>
      </svg>
    );
    if (iconOnly) return <span className={className}>{icon}</span>;
    return (
      <span
        className={cn(
          "badge bg-slate-400/15 text-slate-300 gap-1",
          className,
        )}
      >
        {icon}
        Wikipedia
      </span>
    );
  }
  if (provider === "imdb") {
    const icon = (
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 32" width={iconOnly ? 28 : 24} height={iconOnly ? 14 : 12} className="inline-block">
        <rect width="64" height="32" rx="4" fill="#F5C518"/>
        <text x="32" y="23" textAnchor="middle" fontFamily="Impact,Arial Black,sans-serif" fontSize="18" fontWeight="bold" fill="#000">IMDb</text>
      </svg>
    );
    if (iconOnly) return <span className={className}>{icon}</span>;
    return (
      <span
        className={cn(
          "badge bg-yellow-500/15 text-yellow-400 gap-1",
          className,
        )}
      >
        {icon}
      </span>
    );
  }
  if (provider === "musicbrainz") {
    const sz = iconOnly ? 18 : 14;
    const icon = (
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width={sz} height={sz} className="inline-block">
        <defs>
          <linearGradient id="mb-grad" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#BA478F"/>
            <stop offset="100%" stopColor="#EB743B"/>
          </linearGradient>
        </defs>
        <path d="M128 12L236 68v120l-108 56L20 188V68z" fill="url(#mb-grad)"/>
        {/* Tag / label icon */}
        <g transform="translate(42,58) scale(0.32)" fill="#fff">
          <path d="M 11 1 C 5.477 1 1 5.477 1 11 L 1 173.66 C 1 176.31 2.05 178.85 3.93 180.73 L 135.27 312.07 C 139.17 315.97 145.5 315.97 149.4 312.07 L 248.07 213.4 C 251.97 209.5 251.97 203.17 248.07 199.27 L 116.73 67.93 C 114.85 66.05 112.31 65 109.66 65 L 51 65 L 51 11 C 51 5.477 46.523 1 41 1 Z M 71 105 C 82.046 105 91 113.954 91 125 C 91 136.046 82.046 145 71 145 C 59.954 145 51 136.046 51 125 C 51 113.954 59.954 105 71 105 Z"/>
        </g>
        {/* Gear icon */}
        <g transform="translate(130,100) scale(0.28)" fill="#fff">
          <path d="M200 128a72 72 0 1 1-72-72 72 72 0 0 1 72 72Zm56-24h-25.1a102.9 102.9 0 0 0-12.4-30l17.8-17.7a8 8 0 0 0 0-11.3l-22.6-22.6a8 8 0 0 0-11.3 0L184.6 40.2a103 103 0 0 0-30-12.4V2.8a8 8 0 0 0-8-8h-32a8 8 0 0 0-8 8v25.1a103 103 0 0 0-30 12.4L58.9 22.4a8 8 0 0 0-11.3 0L25 45a8 8 0 0 0 0 11.3l17.8 17.8a103 103 0 0 0-12.4 30H5.3a8 8 0 0 0-8 8v32a8 8 0 0 0 8 8h25.1a103 103 0 0 0 12.4 30L25 199.8a8 8 0 0 0 0 11.3L47.6 233.7a8 8 0 0 0 11.3 0l17.8-17.8a103 103 0 0 0 30 12.4v25.1a8 8 0 0 0 8 8h32a8 8 0 0 0 8-8v-25.1a103 103 0 0 0 30-12.4l17.8 17.8a8 8 0 0 0 11.3 0l22.6-22.6a8 8 0 0 0 0-11.3l-17.8-17.8a103 103 0 0 0 12.4-30h25.1a8 8 0 0 0 8-8v-32a8 8 0 0 0-8.1-8Z"/>
        </g>
      </svg>
    );
    if (iconOnly) return <span className={className}>{icon}</span>;
    return (
      <span
        className={cn(
          "badge bg-orange-500/15 text-orange-400 gap-1",
          className,
        )}
      >
        {icon}
        MusicBrainz
      </span>
    );
  }
  if (provider === "vimeo") {
    const label = "Vimeo";
    if (iconOnly) return <span className={className}>{label}</span>;
    return (
      <span className={cn("badge", "badge-blue", className)}>
        {label}
      </span>
    );
  }
  return (
    <span
      className={cn(
        "badge",
        "badge-blue",
        className,
      )}
    >
      {provider}
    </span>
  );
}
