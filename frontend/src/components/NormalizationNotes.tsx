import type { NormalizationResult } from "@/types";
import { cn } from "@/lib/utils";

interface Props {
  normalization: NormalizationResult;
  className?: string;
}

function DiffRow({ label, raw, normalized }: { label: string; raw: string; normalized: string }) {
  const changed = raw !== normalized;
  return (
    <div className="grid grid-cols-[100px_1fr_1fr] gap-2 text-xs">
      <span className="text-text-secondary font-medium">{label}</span>
      <span className={cn("font-mono", changed ? "text-red-400 line-through" : "text-text-secondary")}>
        {raw || "—"}
      </span>
      <span className={cn("font-mono", changed ? "text-emerald-400" : "text-text-secondary")}>
        {normalized || "—"}
      </span>
    </div>
  );
}

export default function NormalizationNotes({ normalization, className }: Props) {
  const n = normalization;

  return (
    <div className={cn("space-y-3", className)}>
      <div className="grid grid-cols-[100px_1fr_1fr] gap-2 text-[10px] uppercase tracking-wider text-text-secondary font-semibold">
        <span>Field</span>
        <span>Raw</span>
        <span>Normalised</span>
      </div>

      <div className="space-y-1.5">
        <DiffRow label="Artist" raw={n.raw_artist} normalized={n.artist_display} />
        <DiffRow label="Title" raw={n.raw_title} normalized={n.title_display} />
        {(n.raw_album || n.album_display) && (
          <DiffRow label="Album" raw={n.raw_album ?? ""} normalized={n.album_display ?? ""} />
        )}
      </div>

      {/* Parsed details */}
      <div className="border-t border-surface-hover pt-2 space-y-1 text-xs">
        <div className="flex items-center gap-2">
          <span className="text-text-secondary w-24">Primary artist</span>
          <span className="text-text-primary">{n.primary_artist}</span>
        </div>
        {n.featured_artists && n.featured_artists.length > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-text-secondary w-24">Featured</span>
            <span className="text-text-primary">{n.featured_artists.join(", ")}</span>
          </div>
        )}
        <div className="flex items-center gap-2">
          <span className="text-text-secondary w-24">Title base</span>
          <span className="text-text-primary font-mono">{n.title_base}</span>
        </div>
        {n.qualifiers && n.qualifiers.length > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-text-secondary w-24">Qualifiers</span>
            <div className="flex gap-1">
              {n.qualifiers.map((q) => (
                <span key={q} className="badge-purple text-[10px] px-1.5 py-0 rounded">{q}</span>
              ))}
            </div>
          </div>
        )}
        <div className="flex items-center gap-2">
          <span className="text-text-secondary w-24">Artist key</span>
          <span className="text-text-primary font-mono text-[11px]">{n.artist_key}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-text-secondary w-24">Title key</span>
          <span className="text-text-primary font-mono text-[11px]">{n.title_key}</span>
        </div>
      </div>
    </div>
  );
}
