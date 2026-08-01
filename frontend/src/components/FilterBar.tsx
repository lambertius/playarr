import type { ReactNode } from "react";
import { Filter, X } from "lucide-react";
import type { FacetFilterParams } from "@/types";

const VERSION_TYPES = [
  { value: "normal", label: "Normal" },
  { value: "cover", label: "Cover" },
  { value: "live", label: "Live" },
  { value: "alternate", label: "Alternate" },
  { value: "18+", label: "18+" },
] as const;

const RATING_OPTIONS = [1, 2, 3, 4, 5] as const;

const QUALITY_OPTIONS = [
  { value: "360p", label: "360p" },
  { value: "480p", label: "480p" },
  { value: "720p", label: "720p" },
  { value: "1080p", label: "1080p" },
  { value: "2K", label: "2K" },
  { value: "4K", label: "4K" },
] as const;

interface FilterBarProps {
  filters: FacetFilterParams;
  onChange: (filters: FacetFilterParams) => void;
  /** Hide artist filter (e.g. on artists page where artist IS the grouping) */
  hideArtist?: boolean;
  /** Hide year range (e.g. on years page) */
  hideYearRange?: boolean;
  /** Hide rating filters (on ratings page, ratings ARE the grouping) */
  hideRatings?: boolean;
  /** Hide genre filter (on genres page) */
  hideGenre?: boolean;
  /** Hide quality filter (on quality page) */
  hideQuality?: boolean;
  /** Page-specific controls that belong to the same canonical filter surface. */
  children?: ReactNode;
}

function hasActiveFilters(f: FacetFilterParams): boolean {
  return !!(f.version_type || f.artist || f.year_from || f.year_to ||
            f.song_rating || f.video_rating || f.genre || f.quality);
}

function activeFilterCount(f: FacetFilterParams): number {
  let n = 0;
  if (f.version_type) n++;
  if (f.artist) n++;
  if (f.year_from || f.year_to) n++;
  if (f.song_rating) n++;
  if (f.video_rating) n++;
  if (f.genre) n++;
  if (f.quality) n++;
  return n;
}

export function FilterBar({
  filters, onChange, hideArtist, hideYearRange, hideRatings, hideGenre, hideQuality, children,
}: FilterBarProps) {
  const active = hasActiveFilters(filters);
  const count = activeFilterCount(filters);

  const set = (patch: Partial<FacetFilterParams>) =>
    onChange({ ...filters, ...patch });

  const clear = () => onChange({});

  const chips: Array<{ key: keyof FacetFilterParams; label: string }> = [];
  if (filters.version_type) chips.push({ key: "version_type", label: `Type: ${filters.version_type}` });
  if (filters.artist) chips.push({ key: "artist", label: `Artist: ${filters.artist}` });
  if (filters.genre) chips.push({ key: "genre", label: `Genre: ${filters.genre}` });
  if (filters.year_from || filters.year_to) chips.push({ key: "year_from", label: `Years: ${filters.year_from ?? "…"}–${filters.year_to ?? "…"}` });
  if (filters.song_rating) chips.push({ key: "song_rating", label: `Song: ${filters.song_rating}★` });
  if (filters.video_rating) chips.push({ key: "video_rating", label: `Video: ${filters.video_rating}★` });
  if (filters.quality) chips.push({ key: "quality", label: `Quality: ${filters.quality}` });

  const removeChip = (key: keyof FacetFilterParams) => {
    if (key === "year_from") set({ year_from: undefined, year_to: undefined });
    else set({ [key]: undefined });
  };

  return (
    <div className="w-full mb-4 rounded-lg bg-surface-secondary/50 border border-border p-3">
      <div className="flex items-center gap-2 mb-3">
        <span className={`text-xs font-semibold flex items-center gap-1.5 ${active ? "text-accent" : "text-text-secondary"}`}>
          <Filter size={14} />
          Filters{count > 0 && ` (${count})`}
        </span>
        {active && (
          <button onClick={clear} className="btn-ghost btn-sm text-xs text-text-muted flex items-center gap-1 ml-auto">
            <X size={12} /> Clear all
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-end gap-3">
          {/* Version type */}
          <label className="flex flex-col gap-1 text-xs text-text-muted">
            Type
            <select
              value={filters.version_type ?? ""}
              onChange={(e) => set({ version_type: e.target.value || undefined })}
              className="input-field w-auto py-1 text-xs"
            >
              <option value="">All</option>
              {VERSION_TYPES.map((v) => (
                <option key={v.value} value={v.value}>{v.label}</option>
              ))}
            </select>
          </label>

          {/* Artist */}
          {!hideArtist && (
            <label className="flex flex-col gap-1 text-xs text-text-muted">
              Artist
              <input
                type="text"
                value={filters.artist ?? ""}
                onChange={(e) => set({ artist: e.target.value || undefined })}
                placeholder="Search…"
                className="input-field w-32 py-1 text-xs"
              />
            </label>
          )}

          {/* Genre */}
          {!hideGenre && (
            <label className="flex flex-col gap-1 text-xs text-text-muted">
              Genre
              <input
                type="text"
                value={filters.genre ?? ""}
                onChange={(e) => set({ genre: e.target.value || undefined })}
                placeholder="Search…"
                className="input-field w-28 py-1 text-xs"
              />
            </label>
          )}

          {/* Year range */}
          {!hideYearRange && (
            <label className="flex flex-col gap-1 text-xs text-text-muted">
              Year from
              <input
                type="number"
                value={filters.year_from ?? ""}
                onChange={(e) =>
                  set({ year_from: e.target.value ? Number(e.target.value) : undefined })
                }
                placeholder="e.g. 2000"
                className="input-field w-24 py-1 text-xs"
              />
            </label>
          )}
          {!hideYearRange && (
            <label className="flex flex-col gap-1 text-xs text-text-muted">
              Year to
              <input
                type="number"
                value={filters.year_to ?? ""}
                onChange={(e) =>
                  set({ year_to: e.target.value ? Number(e.target.value) : undefined })
                }
                placeholder="e.g. 2024"
                className="input-field w-24 py-1 text-xs"
              />
            </label>
          )}

          {/* Song rating */}
          {!hideRatings && (
            <label className="flex flex-col gap-1 text-xs text-text-muted">
              Song rating
              <select
                value={filters.song_rating ?? ""}
                onChange={(e) =>
                  set({ song_rating: e.target.value ? Number(e.target.value) : undefined })
                }
                className="input-field w-auto py-1 text-xs"
              >
                <option value="">Any</option>
                {RATING_OPTIONS.map((r) => (
                  <option key={r} value={r}>{"★".repeat(r)}</option>
                ))}
              </select>
            </label>
          )}

          {/* Video rating */}
          {!hideRatings && (
            <label className="flex flex-col gap-1 text-xs text-text-muted">
              Video rating
              <select
                value={filters.video_rating ?? ""}
                onChange={(e) =>
                  set({ video_rating: e.target.value ? Number(e.target.value) : undefined })
                }
                className="input-field w-auto py-1 text-xs"
              >
                <option value="">Any</option>
                {RATING_OPTIONS.map((r) => (
                  <option key={r} value={r}>{"★".repeat(r)}</option>
                ))}
              </select>
            </label>
          )}

          {/* Quality */}
          {!hideQuality && (
            <label className="flex flex-col gap-1 text-xs text-text-muted">
              Quality
              <select
                value={filters.quality ?? ""}
                onChange={(e) =>
                  set({ quality: e.target.value || undefined })
                }
                className="input-field w-auto py-1 text-xs"
              >
                <option value="">Any</option>
                {QUALITY_OPTIONS.map((q) => (
                  <option key={q.value} value={q.value}>{q.label}</option>
                ))}
              </select>
            </label>
          )}
          {children}
      </div>
      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-3 pt-3 border-t border-border">
          {chips.map((chip) => (
            <button key={chip.key} onClick={() => removeChip(chip.key)} className="badge-blue inline-flex items-center gap-1">
              {chip.label} <X size={10} />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
