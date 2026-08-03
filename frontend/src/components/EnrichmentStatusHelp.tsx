import { CircleHelp } from "lucide-react";
import { ENRICHMENT_STATUSES } from "@/lib/enrichmentStatus";
import { Tooltip } from "@/components/Tooltip";

export function EnrichmentStatusHelp() {
  return (
    <Tooltip content={(
      <div className="max-w-sm space-y-2 text-left">
        <p className="font-semibold text-text-primary">AI enrichment status</p>
        {ENRICHMENT_STATUSES.map((status) => (
          <p key={status.value}>
            <span className="font-medium text-text-primary">{status.label}:</span>{" "}{status.description}
          </p>
        ))}
      </div>
    )}>
      <button
        type="button"
        className="inline-flex items-center gap-1 text-xs text-text-muted hover:text-text-primary rounded focus-visible:ring-2 focus-visible:ring-accent"
        aria-label="Explain AI enrichment statuses"
      >
        <CircleHelp size={14} /> What do these mean?
      </button>
    </Tooltip>
  );
}
