import { CheckCircle2, Circle } from "lucide-react";
import type { ProcessingState } from "@/types";

const STEP_LABELS: Record<string, string> = {
  imported: "Imported",
  downloaded: "Downloaded",
  track_identified: "Track Identified",
  metadata_resolved: "Metadata Resolved",
  canonical_linked: "Canonical Track Linked",
  description_generated: "Description Generated",
  filename_checked: "Filename Checked",
  file_organized: "File Organised",
  nfo_exported: "NFO Exported",
  xml_exported: "XML Exported",
  thumbnail_selected: "Thumbnail Selected",
  artwork_fetched: "Artwork Fetched",
  audio_normalized: "Audio Normalised",
  ai_enriched: "AI Enriched",
};

const STEP_ORDER = [
  "imported",
  "downloaded",
  "track_identified",
  "metadata_resolved",
  "canonical_linked",
  "description_generated",
  "filename_checked",
  "file_organized",
  "nfo_exported",
  "xml_exported",
  "thumbnail_selected",
  "artwork_fetched",
  "audio_normalized",
  "ai_enriched",
] as const;

interface ProcessingStepsProps {
  state: ProcessingState;
  embedded?: boolean;
}

export function ProcessingSteps({ state, embedded }: ProcessingStepsProps) {
  const completedCount = STEP_ORDER.filter(
    (k) => state[k]?.completed
  ).length;

  const content = (
    <>
      <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-3">
        Processing Progress
        <span className="ml-2 text-xs font-normal text-text-muted">
          {completedCount}/{STEP_ORDER.length}
        </span>
      </h3>
      <div className="space-y-1.5">
        {STEP_ORDER.map((key) => {
          const entry = state[key];
          const done = entry?.completed;
          return (
            <div
              key={key}
              className="flex items-center gap-2 text-sm group"
              title={
                done && entry?.timestamp
                  ? `Completed: ${new Date(entry.timestamp).toLocaleString()}${entry.method ? ` (${entry.method})` : ""}`
                  : undefined
              }
            >
              {done ? (
                <CheckCircle2 size={14} className="text-emerald-400 flex-shrink-0" />
              ) : (
                <Circle size={14} className="text-text-muted/40 flex-shrink-0" />
              )}
              <span className={done ? "text-text-primary" : "text-text-muted/60"}>
                {STEP_LABELS[key]}
              </span>
              {done && entry?.method && (
                <span className="text-[10px] text-text-muted ml-auto opacity-0 group-hover:opacity-100 transition-opacity">
                  {entry.method}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </>
  );

  if (embedded) {
    return <div className="border border-border rounded-lg p-3">{content}</div>;
  }

  return <div className="card">{content}</div>;
}
