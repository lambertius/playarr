import { cn } from "@/lib/utils";

interface Props {
  reasons: string[];
  className?: string;
}

/** Maps reason codes from the backend to human-readable labels + color. */
const reasonLabels: Record<string, { label: string; color: string }> = {
  low_artist_score: { label: "Artist mismatch", color: "badge-red" },
  low_recording_score: { label: "Title mismatch", color: "badge-red" },
  no_candidates: { label: "No candidates", color: "badge-gray" },
  partial_match: { label: "Partial match", color: "badge-yellow" },
  multiple_close: { label: "Ambiguous", color: "badge-purple" },
  hysteresis_skip: { label: "Skipped (stable)", color: "badge-blue" },
  missing_metadata: { label: "Missing metadata", color: "badge-gray" },
  name_variant: { label: "Name variant", color: "badge-yellow" },
  feat_stripped: { label: "Featured artist stripped", color: "badge-yellow" },
};

export default function ReasonChips({ reasons, className }: Props) {
  if (!reasons || reasons.length === 0) return null;

  return (
    <div className={cn("flex flex-wrap gap-1", className)}>
      {reasons.map((reason) => {
        const config = reasonLabels[reason] ?? { label: reason, color: "badge-gray" };
        return (
          <span
            key={reason}
            className={cn("text-[10px] px-1.5 py-0.5 rounded-full", config.color)}
          >
            {config.label}
          </span>
        );
      })}
    </div>
  );
}
