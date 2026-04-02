import { useState, useEffect, useRef } from "react";
import type { SearchEntityType, ManualSearchResult } from "@/types";
import { useManualSearch } from "@/hooks/queries";
import { cn } from "@/lib/utils";

interface Props {
  defaultArtist?: string;
  onSelect: (entityType: SearchEntityType, result: ManualSearchResult) => void;
  className?: string;
}

export default function ManualSearchBox({ defaultArtist, onSelect, className }: Props) {
  const [entityType, setEntityType] = useState<SearchEntityType>("recording");
  const [query, setQuery] = useState("");
  const [artist, setArtist] = useState(defaultArtist ?? "");
  const [debouncedQ, setDebouncedQ] = useState("");
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setDebouncedQ(query), 400);
    return () => clearTimeout(timerRef.current);
  }, [query]);

  const { data, isLoading } = useManualSearch(
    entityType,
    debouncedQ,
    entityType !== "artist" ? artist : undefined,
  );

  const types: { value: SearchEntityType; label: string }[] = [
    { value: "recording", label: "Track" },
    { value: "artist", label: "Artist" },
    { value: "release", label: "Album" },
  ];

  return (
    <div className={cn("space-y-3", className)}>
      {/* Entity type tabs */}
      <div className="flex gap-1 p-0.5 bg-surface rounded-lg">
        {types.map((t) => (
          <button
            key={t.value}
            onClick={() => setEntityType(t.value)}
            className={cn(
              "flex-1 text-xs py-1.5 rounded-md transition-colors",
              entityType === t.value
                ? "bg-accent text-white"
                : "text-text-secondary hover:text-text-primary"
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Search inputs */}
      <div className="space-y-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={`Search ${entityType}…`}
          className="input-field w-full"
          autoFocus
        />
        {entityType !== "artist" && (
          <input
            type="text"
            value={artist}
            onChange={(e) => setArtist(e.target.value)}
            placeholder="Artist filter (optional)"
            className="input-field w-full"
          />
        )}
      </div>

      {/* Results */}
      <div className="max-h-60 overflow-y-auto space-y-1">
        {isLoading && debouncedQ.length >= 2 && (
          <div className="flex items-center gap-2 py-4 justify-center text-text-secondary text-sm">
            <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            Searching MusicBrainz…
          </div>
        )}

        {data?.results.map((r) => (
          <button
            key={r.mbid}
            onClick={() => onSelect(entityType, r)}
            className="w-full text-left p-2 rounded-lg hover:bg-surface-hover transition-colors"
          >
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <span className="text-sm text-text-primary font-medium truncate block">
                  {r.name}
                </span>
                {r.disambiguation && (
                  <span className="text-xs text-text-secondary">({r.disambiguation})</span>
                )}
                {r.extra && (
                  <div className="text-xs text-text-secondary mt-0.5 flex gap-2 flex-wrap">
                    {typeof r.extra.artist === "string" && (
                      <span>by {r.extra.artist}</span>
                    )}
                    {typeof r.extra.type === "string" && (
                      <span>{r.extra.type}</span>
                    )}
                    {typeof r.extra.country === "string" && (
                      <span>{r.extra.country}</span>
                    )}
                    {typeof r.extra.date === "string" && (
                      <span>{r.extra.date}</span>
                    )}
                  </div>
                )}
              </div>
              <span className="text-xs text-text-secondary font-mono flex-shrink-0">
                {r.score}%
              </span>
            </div>
          </button>
        ))}

        {data && data.results.length === 0 && debouncedQ.length >= 2 && !isLoading && (
          <p className="text-sm text-text-secondary text-center py-4">
            No results found
          </p>
        )}

        {debouncedQ.length < 2 && (
          <p className="text-xs text-text-secondary text-center py-4">
            Type at least 2 characters to search
          </p>
        )}
      </div>
    </div>
  );
}
