import type { MatchCandidate } from "@/types";
import CandidateCard from "./CandidateCard";
import { cn } from "@/lib/utils";

interface Props {
  candidates: MatchCandidate[];
  onPin?: (candidate: MatchCandidate) => void;
  onApply?: (candidate: MatchCandidate) => void;
  className?: string;
}

export default function CandidateList({ candidates, onPin, onApply, className }: Props) {
  if (candidates.length === 0) {
    return (
      <div className={cn("text-center py-8 text-text-secondary", className)}>
        <svg className="w-10 h-10 mx-auto mb-2 opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
        </svg>
        <p className="text-sm">No candidates found</p>
        <p className="text-xs mt-1">Try running resolve or searching manually</p>
      </div>
    );
  }

  // Group by entity type
  const grouped = candidates.reduce<Record<string, MatchCandidate[]>>((acc, c) => {
    (acc[c.entity_type] ??= []).push(c);
    return acc;
  }, {});

  const typeOrder = ["recording", "artist", "release"];
  const sortedTypes = Object.keys(grouped).sort(
    (a, b) => (typeOrder.indexOf(a) === -1 ? 99 : typeOrder.indexOf(a))
          - (typeOrder.indexOf(b) === -1 ? 99 : typeOrder.indexOf(b))
  );

  return (
    <div className={cn("space-y-4", className)}>
      {sortedTypes.map((type) => (
        <div key={type}>
          <h4 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-2">
            {type}s ({grouped[type].length})
          </h4>
          <div className="space-y-1">
            {grouped[type]
              .sort((a, b) => b.score - a.score)
              .map((c, i) => (
                <CandidateCard
                  key={`${c.entity_type}-${c.mbid ?? i}`}
                  candidate={c}
                  isSelected={c.is_selected}
                  onPin={onPin ? () => onPin(c) : undefined}
                  onApply={onApply ? () => onApply(c) : undefined}
                />
              ))}
          </div>
        </div>
      ))}
    </div>
  );
}
