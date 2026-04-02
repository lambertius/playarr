import type { MatchCandidate } from "@/types";
import { cn } from "@/lib/utils";
import { Tooltip } from "@/components/Tooltip";

interface Props {
  candidate: MatchCandidate;
  isSelected?: boolean;
  onPin?: () => void;
  onApply?: () => void;
  className?: string;
}

function entityIcon(type: string) {
  switch (type) {
    case "artist":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
        </svg>
      );
    case "recording":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
        </svg>
      );
    case "release":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 7v10a2 2 0 01-2 2H6a2 2 0 01-2-2V7m16-2H4a2 2 0 00-2 2v0a2 2 0 002 2h16a2 2 0 002-2v0a2 2 0 00-2-2z" />
        </svg>
      );
    default:
      return null;
  }
}

function scoreColor(score: number): string {
  if (score >= 80) return "text-emerald-400";
  if (score >= 60) return "text-yellow-400";
  if (score >= 40) return "text-orange-400";
  return "text-red-400";
}

export default function CandidateCard({ candidate, isSelected, onPin, onApply, className }: Props) {
  return (
    <div
      className={cn(
        "card p-3 flex items-center gap-3 transition-colors",
        isSelected && "ring-1 ring-accent bg-accent/5",
        !isSelected && "hover:bg-surface-hover",
        className
      )}
    >
      {/* Entity type icon */}
      <div className="text-text-secondary flex-shrink-0">
        {entityIcon(candidate.entity_type)}
      </div>

      {/* Info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-text-primary truncate">
            {candidate.canonical_name}
          </span>
          {isSelected && (
            <span className="badge-blue text-[10px] px-1.5 py-0 rounded">selected</span>
          )}
        </div>
        <div className="flex items-center gap-2 text-xs text-text-secondary mt-0.5">
          <span className="capitalize">{candidate.entity_type}</span>
          {candidate.provider && (
            <>
              <span className="text-text-secondary/40">·</span>
              <span>{candidate.provider}</span>
            </>
          )}
          {candidate.mbid && (
            <a
              href={`https://musicbrainz.org/${candidate.entity_type}/${candidate.mbid}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent hover:underline"
              onClick={(e) => e.stopPropagation()}
            >
              MB
            </a>
          )}
        </div>
      </div>

      {/* Score */}
      <span className={cn("text-sm font-mono font-semibold", scoreColor(candidate.score))}>
        {Math.round(candidate.score)}
      </span>

      {/* Actions */}
      <div className="flex items-center gap-1 flex-shrink-0">
        {onApply && !isSelected && (
          <Tooltip content="Apply this match without pinning — it may change on next resolve">
            <button
              onClick={(e) => { e.stopPropagation(); onApply(); }}
              className="btn-ghost btn-sm text-xs"
            >
              Apply
            </button>
          </Tooltip>
        )}
        {onPin && (
          <Tooltip content="Pin this match permanently — it won't change on future resolves">
            <button
              onClick={(e) => { e.stopPropagation(); onPin(); }}
              className="btn-primary btn-sm text-xs"
            >
              Pin
            </button>
          </Tooltip>
        )}
      </div>
    </div>
  );
}
