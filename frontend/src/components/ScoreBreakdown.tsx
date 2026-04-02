import { useState } from "react";
import { cn } from "@/lib/utils";

interface Feature {
  name: string;
  score: number;
  weight?: number;
  contribution?: number;
}

interface Props {
  breakdown: Record<string, unknown> | null;
  overallScore: number;
  className?: string;
}

function parseBreakdown(breakdown: Record<string, unknown> | null): Feature[] {
  if (!breakdown) return [];
  const features = (breakdown.features ?? {}) as Record<string, number>;
  const weights = (breakdown.weighted_contributions ?? {}) as Record<string, number>;
  return Object.entries(features).map(([name, score]) => ({
    name,
    score: typeof score === "number" ? score : 0,
    contribution: typeof weights[name] === "number" ? weights[name] : undefined,
  }));
}

function scoreColor(score: number): string {
  if (score >= 80) return "bg-emerald-500";
  if (score >= 60) return "bg-yellow-500";
  if (score >= 40) return "bg-orange-500";
  return "bg-red-500";
}

function overallColor(score: number): string {
  if (score >= 80) return "text-emerald-400";
  if (score >= 60) return "text-yellow-400";
  if (score >= 40) return "text-orange-400";
  return "text-red-400";
}

export default function ScoreBreakdown({ breakdown, overallScore, className }: Props) {
  const [expanded, setExpanded] = useState(false);
  const features = parseBreakdown(breakdown);

  return (
    <div className={cn("space-y-2", className)}>
      {/* Overall score bar */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between gap-3 group"
      >
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <span className="text-xs text-text-secondary whitespace-nowrap">Overall</span>
          <div className="flex-1 h-2 bg-surface-hover rounded-full overflow-hidden">
            <div
              className={cn("h-full rounded-full transition-all", scoreColor(overallScore))}
              style={{ width: `${Math.min(100, overallScore)}%` }}
            />
          </div>
        </div>
        <span className={cn("text-sm font-mono font-semibold min-w-[3ch] text-right", overallColor(overallScore))}>
          {Math.round(overallScore)}
        </span>
        <svg
          className={cn(
            "w-4 h-4 text-text-secondary transition-transform",
            expanded && "rotate-180"
          )}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Feature breakdown (expandable) */}
      {expanded && features.length > 0 && (
        <div className="pl-2 space-y-1.5 border-l-2 border-surface-hover ml-1">
          {features.map((f) => (
            <div key={f.name} className="flex items-center gap-2">
              <span className="text-xs text-text-secondary w-32 truncate capitalize">
                {f.name.replace(/_/g, " ")}
              </span>
              <div className="flex-1 h-1.5 bg-surface-hover rounded-full overflow-hidden">
                <div
                  className={cn("h-full rounded-full", scoreColor(f.score))}
                  style={{ width: `${Math.min(100, f.score)}%` }}
                />
              </div>
              <span className="text-xs font-mono text-text-secondary w-6 text-right">
                {Math.round(f.score)}
              </span>
            </div>
          ))}
        </div>
      )}

      {expanded && features.length === 0 && (
        <p className="text-xs text-text-secondary pl-4">No breakdown data available</p>
      )}
    </div>
  );
}
