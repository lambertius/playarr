import type { MatchStatus } from "@/types";
import { cn } from "@/lib/utils";

const statusConfig: Record<MatchStatus, { label: string; className: string }> = {
  matched_high: { label: "High Match", className: "badge-green" },
  matched_medium: { label: "Medium", className: "badge-yellow" },
  needs_review: { label: "Needs Review", className: "badge-red" },
  unmatched: { label: "Unmatched", className: "badge-gray" },
};

interface Props {
  status: MatchStatus;
  pinned?: boolean;
  className?: string;
}

export default function MatchStatusBadge({ status, pinned, className }: Props) {
  const config = statusConfig[status] ?? statusConfig.unmatched;
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full", config.className, className)}>
      {pinned && (
        <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
          <path d="M9.828 4.172a2 2 0 012.829 0l1.171 1.171a2 2 0 010 2.829L10 12l-4-4 3.828-3.828z" />
          <path d="M6 12l-2 6 6-2-4-4z" />
        </svg>
      )}
      {config.label}
    </span>
  );
}
