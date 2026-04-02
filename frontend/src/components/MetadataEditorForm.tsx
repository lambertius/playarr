import { useState, useRef, useEffect, useCallback, type FormEvent } from "react";
import { Save, X, Lock, Unlock, ExternalLink, ShieldCheck, ShieldOff, Star, Pencil, ChevronDown } from "lucide-react";
import { Tooltip } from "@/components/Tooltip";
import { Link } from "react-router-dom";
import type { VideoItemDetail, VideoItemUpdate, SourceInfo } from "@/types";
import { useUpdateVideo, useGenreBlacklist } from "@/hooks/queries";
import { useToast } from "@/components/Toast";
import { SourceBadge } from "@/components/Badges";
import { SourceEditorModal } from "@/components/SourceEditorModal";

const VERSION_TYPE_OPTIONS = [
  { value: "normal", label: "Normal" },
  { value: "cover", label: "Cover" },
  { value: "live", label: "Live" },
  { value: "alternate", label: "Alternate" },
  { value: "uncensored", label: "Uncensored" },
  { value: "18+", label: "18+" },
];

/* ── Version Type Inline Selector ── */
function VersionTypeInline({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const current = VERSION_TYPE_OPTIONS.find((o) => o.value === value) ?? VERSION_TYPE_OPTIONS[0];

  return (
    <div ref={ref} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="inline-flex items-center gap-1 text-sm text-accent hover:text-accent-hover transition-colors"
      >
        {current.label}
        <ChevronDown size={12} className={open ? "rotate-180 transition-transform" : "transition-transform"} />
      </button>
      {open && (
        <div className="absolute left-0 top-full mt-1 bg-surface-light border border-surface-border rounded-lg shadow-xl z-50 min-w-[120px] py-1">
          {VERSION_TYPE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => {
                onChange(opt.value);
                setOpen(false);
              }}
              className={`w-full text-left px-3 py-1.5 text-xs hover:bg-surface-hover transition-colors ${
                value === opt.value ? "text-accent font-medium" : "text-text-secondary"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Star Rating ── */
function StarRating({
  value,
  isSet,
  onChange,
}: {
  value: number;
  isSet: boolean;
  onChange: (rating: number) => void;
}) {
  const [hover, setHover] = useState(0);

  return (
    <span className="inline-flex gap-0.5" onMouseLeave={() => setHover(0)}>
      {[1, 2, 3, 4, 5].map((star) => {
        const filled = hover ? star <= hover : star <= value;
        const color = hover
          ? "text-accent"
          : isSet
            ? "text-accent"
            : "text-text-muted/50";
        return (
          <button
            key={star}
            type="button"
            className={`p-0 transition-colors ${color} hover:text-accent-hover`}
            onMouseEnter={() => setHover(star)}
            onClick={() => onChange(star)}
            aria-label={`${star} star${star > 1 ? "s" : ""}`}
          >
            <Star size={16} fill={filled ? "currentColor" : "none"} />
          </button>
        );
      })}
    </span>
  );
}

interface MetadataEditorFormProps {
  video: VideoItemDetail;
}

export function MetadataEditorForm({ video }: MetadataEditorFormProps) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    artist: video.artist,
    title: video.title,
    album: video.album ?? "",
    year: video.year?.toString() ?? "",
    plot: video.plot ?? "",
    version_type: video.version_type ?? "normal",
  });
  const [genreTags, setGenreTags] = useState<string[]>(video.genres.map((g) => g.name));
  const [locked, setLocked] = useState<Set<string>>(
    new Set(video.locked_fields ?? [])
  );

  const [sourceEditorOpen, setSourceEditorOpen] = useState(false);

  const updateMutation = useUpdateVideo(video.id);
  const { toast } = useToast();

  const startEdit = () => {
    setForm({
      artist: video.artist,
      title: video.title,
      album: video.album ?? "",
      year: video.year?.toString() ?? "",
      plot: video.plot ?? "",
      version_type: video.version_type ?? "normal",
    });
    setGenreTags(video.genres.map((g) => g.name));
    setLocked(new Set(video.locked_fields ?? []));
    setEditing(true);
  };

  const toggleLock = (field: string) => {
    setLocked((prev) => {
      const next = new Set(prev);
      if (next.has(field)) next.delete(field);
      else next.add(field);
      return next;
    });
  };

  const handleSave = async (e: FormEvent) => {
    e.preventDefault();
    const data: VideoItemUpdate = {
      artist: form.artist,
      title: form.title,
      album: form.album,
      year: form.year ? parseInt(form.year) : null,
      plot: form.plot,
      genres: genreTags,
      locked_fields: Array.from(locked),
      version_type: form.version_type,
    };
    try {
      await updateMutation.mutateAsync(data);
      toast({ type: "success", title: "Metadata saved" });
      setEditing(false);
    } catch {
      toast({ type: "error", title: "Failed to save metadata" });
    }
  };

  const isAllLocked = locked.has("_all");

  const toggleMasterLock = () => {
    const next = new Set(locked);
    if (next.has("_all")) next.delete("_all");
    else next.add("_all");
    setLocked(next);
    updateMutation.mutate(
      { locked_fields: Array.from(next) },
      {
        onSuccess: () => toast({ type: "success", title: next.has("_all") ? "Metadata locked" : "Metadata unlocked" }),
        onError: () => toast({ type: "error", title: "Failed to update lock" }),
      }
    );
  };

  if (!editing) {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide">
            Metadata
          </h3>
          <div className="flex items-center gap-2">
            <button
              onClick={toggleMasterLock}
              className={`btn-ghost btn-sm flex items-center gap-1.5 ${isAllLocked ? "text-accent" : "text-text-muted"}`}
              title={isAllLocked ? "Metadata is locked – rescans will not overwrite" : "Metadata is unlocked – rescans may overwrite"}
            >
              {isAllLocked ? <ShieldCheck size={14} /> : <ShieldOff size={14} />}
              <span className="text-xs">{isAllLocked ? "Locked" : "Unlocked"}</span>
            </button>
            <button onClick={startEdit} className="btn-secondary btn-sm">
              Edit
            </button>
          </div>
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <div className="contents">
            <dt className="text-text-muted">Artist</dt>
            <dd>
              {video.artist ? (
                <Link
                  to={`/library?artist=${encodeURIComponent(video.artist)}`}
                  className="text-accent hover:underline"
                >
                  {video.artist}
                </Link>
              ) : (
                <span className="text-text-primary">—</span>
              )}
            </dd>
          </div>
          <div className="contents">
            <dt className="text-text-muted">Title</dt>
            <dd className="text-text-primary font-semibold">{video.title || "—"}</dd>
          </div>
          <div className="contents">
            <dt className="text-text-muted">Album</dt>
            <dd>
              {video.album ? (
                <Link
                  to={video.album_entity_id
                    ? `/library?album_entity_id=${video.album_entity_id}&album=${encodeURIComponent(video.album)}`
                    : `/library?album=${encodeURIComponent(video.album)}`
                  }
                  className="text-accent hover:underline"
                >
                  {video.album}
                </Link>
              ) : (
                <span className="text-text-primary">—</span>
              )}
            </dd>
          </div>
          <div className="contents">
            <dt className="text-text-muted">Year</dt>
            <dd>
              {video.year ? (
                <Link
                  to={`/library?year=${video.year}`}
                  className="text-accent hover:underline"
                >
                  {video.year}
                </Link>
              ) : (
                <span className="text-text-primary">—</span>
              )}
            </dd>
          </div>
          <div className="contents">
            <dt className="text-text-muted">Genres</dt>
            <dd>
              {video.genres.length > 0 ? (
                <span className="flex flex-wrap gap-x-2 gap-y-1">
                  {video.genres.map((g, i) => (
                    <span key={g.id}>
                      <Link
                        to={`/library?genre=${encodeURIComponent(g.name)}`}
                        className="text-accent hover:underline"
                      >
                        {g.name}
                      </Link>
                      {i < video.genres.length - 1 && <span className="text-text-muted">,</span>}
                    </span>
                  ))}
                </span>
              ) : (
                <span className="text-text-primary">—</span>
              )}
            </dd>
          </div>
          <div className="contents">
            <dt className="text-text-muted">Version</dt>
            <dd className="flex items-center gap-1.5">
              <VersionTypeInline
                value={video.version_type ?? "normal"}
                onChange={(vt) =>
                  updateMutation.mutate(
                    { version_type: vt },
                    {
                      onSuccess: () => toast({ type: "success", title: `Version set to ${vt}` }),
                      onError: () => toast({ type: "error", title: "Failed to set version type" }),
                    }
                  )
                }
              />
              {video.version_type === "alternate" && video.alternate_version_label && video.alternate_version_label.toLowerCase() !== "uncensored" && (
                <span className="text-xs text-text-muted">({video.alternate_version_label})</span>
              )}
            </dd>
          </div>
        </dl>

        {/* Ratings */}
        <div className="pt-3 mt-3 border-t border-surface-border">
          <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <div className="contents">
              <dt className="text-text-muted">Song</dt>
              <dd>
                <StarRating
                  value={video.song_rating ?? 3}
                  isSet={video.song_rating_set ?? false}
                  onChange={(r) =>
                    updateMutation.mutate(
                      { song_rating: r, song_rating_set: true },
                      { onSuccess: () => toast({ type: "success", title: "Song rating saved" }) }
                    )
                  }
                />
              </dd>
            </div>
            <div className="contents">
              <dt className="text-text-muted">Video</dt>
              <dd>
                <StarRating
                  value={video.video_rating ?? 3}
                  isSet={video.video_rating_set ?? false}
                  onChange={(r) =>
                    updateMutation.mutate(
                      { video_rating: r, video_rating_set: true },
                      { onSuccess: () => toast({ type: "success", title: "Video rating saved" }) }
                    )
                  }
                />
              </dd>
            </div>
          </div>
        </div>

        {/* Sources */}
        {(() => {
          const categories = ["video", "artist", "album", "single", "recording"] as const;
          const categoryLabels: Record<string, string> = {
            video: "Video",
            artist: "Artist",
            album: "Album",
            single: "Single",
            recording: "Recording",
          };
          // Group sources by category
          const grouped: Record<string, SourceInfo[]> = {};
          for (const cat of categories) grouped[cat] = [];
          for (const s of video.sources as SourceInfo[]) {
            let cat = s.source_type || "video";
            // Strict: wikipedia/musicbrainz can NEVER be "video"
            if (cat === "video" && (s.provider === "wikipedia" || s.provider === "musicbrainz")) cat = "single";
            if (grouped[cat]) grouped[cat].push(s);
            else grouped["video"].push(s);
          }
          // Sort within each category: musicbrainz before wikipedia, then others
          const providerOrder: Record<string, number> = { youtube: 0, vimeo: 1, musicbrainz: 2, imdb: 3, wikipedia: 4 };
          for (const cat of categories) {
            grouped[cat].sort((a, b) => (providerOrder[a.provider] ?? 3) - (providerOrder[b.provider] ?? 3));
          }
          const activeCats = categories.filter((c) => grouped[c].length > 0);
          const maxRows = activeCats.length > 0 ? Math.max(...activeCats.map((c) => grouped[c].length)) : 0;

          return (
            <div className="pt-3 mt-3 border-t border-surface-border">
              <h4 className="text-xs font-medium text-text-muted uppercase tracking-wider mb-2 flex items-center gap-1.5">
                <ExternalLink size={12} /> Sources
                <Tooltip content="Edit sources">
                <button
                  type="button"
                  onClick={() => setSourceEditorOpen(true)}
                  className="ml-auto p-0.5 rounded hover:bg-surface-border/50 text-text-muted hover:text-text-primary transition-colors"
                >
                  <Pencil size={11} />
                </button>
                </Tooltip>
              </h4>
              <SourceEditorModal
                open={sourceEditorOpen}
                onClose={() => setSourceEditorOpen(false)}
                videoId={video.id}
                sources={video.sources as SourceInfo[]}
              />
              {activeCats.length > 0 && (
                <div
                  className="grid gap-x-3 gap-y-1"
                  style={{ gridTemplateColumns: `repeat(${activeCats.length}, 1fr)` }}
                >
                  {activeCats.map((cat) => (
                    <div key={cat} className="text-[10px] font-semibold text-text-muted uppercase tracking-wider text-center border-b border-surface-border pb-1 mb-1">
                      {categoryLabels[cat]}
                    </div>
                  ))}
                  {Array.from({ length: maxRows }).map((_, row) =>
                    activeCats.map((cat) => {
                      const s = grouped[cat][row];
                      return (
                        <div key={`${cat}-${row}`} className="flex justify-center items-center py-0.5">
                          {s ? (
                            <a
                              href={s.original_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="opacity-80 hover:opacity-100 transition-opacity"
                              title={`${s.provider} — ${s.original_url}`}
                            >
                              <SourceBadge provider={s.provider} iconOnly />
                            </a>
                          ) : null}
                        </div>
                      );
                    })
                  )}
                </div>
              )}
            </div>
          );
        })()}
      </div>
    );
  }

  return (
    <form onSubmit={handleSave} className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide">
          Edit Metadata
        </h3>
        <div className="flex gap-2">
          <button type="button" onClick={() => setEditing(false)} className="btn-ghost btn-sm">
            <X size={14} /> Cancel
          </button>
          <button type="submit" disabled={updateMutation.isPending} className="btn-primary btn-sm">
            <Save size={14} /> Save
          </button>
        </div>
      </div>

      {(
        [
          { key: "artist", label: "Artist", type: "text" },
          { key: "title", label: "Title", type: "text" },
          { key: "album", label: "Album", type: "text" },
          { key: "year", label: "Year", type: "number" },
        ] as const
      ).map(({ key, label, type }) => (
        <div key={key} className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => toggleLock(key)}
            className={`flex-shrink-0 p-1 rounded ${locked.has(key) ? "text-accent" : "text-text-muted hover:text-text-secondary"}`}
            title={locked.has(key) ? "Locked (won't be overwritten on rescan)" : "Unlocked"}
            aria-label={`${locked.has(key) ? "Unlock" : "Lock"} ${label}`}
          >
            {locked.has(key) ? <Lock size={14} /> : <Unlock size={14} />}
          </button>
          <label htmlFor={`meta-${key}`} className="w-16 flex-shrink-0 text-xs text-text-muted">
            {label}
          </label>
          <input
            id={`meta-${key}`}
            type={type}
            value={form[key]}
            onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
            className="input-field"
          />
        </div>
      ))}

      {/* Genres — tag input with autocomplete */}
      <div className="flex items-start gap-2">
        <button
          type="button"
          onClick={() => toggleLock("genres")}
          className={`flex-shrink-0 p-1 rounded mt-1 ${locked.has("genres") ? "text-accent" : "text-text-muted hover:text-text-secondary"}`}
          title={locked.has("genres") ? "Locked (won't be overwritten on rescan)" : "Unlocked"}
          aria-label={`${locked.has("genres") ? "Unlock" : "Lock"} Genres`}
        >
          {locked.has("genres") ? <Lock size={14} /> : <Unlock size={14} />}
        </button>
        <label className="w-16 flex-shrink-0 text-xs text-text-muted mt-2">
          Genres
        </label>
        <GenreTagInput value={genreTags} onChange={setGenreTags} />
      </div>

      {/* Version Type */}
      <div className="flex items-center gap-2">
        <span className="flex-shrink-0 p-1 w-[22px]" />
        <label htmlFor="meta-version_type" className="w-16 flex-shrink-0 text-xs text-text-muted">
          Version
        </label>
        <select
          id="meta-version_type"
          value={form.version_type}
          onChange={(e) => setForm((f) => ({ ...f, version_type: e.target.value }))}
          className="input-field"
        >
          {VERSION_TYPE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </div>

      {/* Ratings (always interactive, save immediately) */}
      <div className="pt-3 mt-1 border-t border-surface-border space-y-2">
        <div className="flex items-center gap-2">
          <span className="w-16 flex-shrink-0 text-xs text-text-muted ml-7">Song</span>
          <StarRating
            value={video.song_rating ?? 3}
            isSet={video.song_rating_set ?? false}
            onChange={(r) =>
              updateMutation.mutate(
                { song_rating: r, song_rating_set: true },
                { onSuccess: () => toast({ type: "success", title: "Song rating saved" }) }
              )
            }
          />
        </div>
        <div className="flex items-center gap-2">
          <span className="w-16 flex-shrink-0 text-xs text-text-muted ml-7">Video</span>
          <StarRating
            value={video.video_rating ?? 3}
            isSet={video.video_rating_set ?? false}
            onChange={(r) =>
              updateMutation.mutate(
                { video_rating: r, video_rating_set: true },
                { onSuccess: () => toast({ type: "success", title: "Video rating saved" }) }
              )
            }
          />
        </div>
      </div>
    </form>
  );
}

/* ── Genre Tag Input with Autocomplete ── */

function GenreTagInput({
  value,
  onChange,
}: {
  value: string[];
  onChange: (tags: string[]) => void;
}) {
  const { data: allGenres } = useGenreBlacklist();
  const [input, setInput] = useState("");
  const [highlightIdx, setHighlightIdx] = useState(-1);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // All known genre names for suggestion
  const knownNames = allGenres?.map((g) => g.name) ?? [];

  // Filter suggestions: match input, exclude already-added genres
  const suggestions = input.trim()
    ? knownNames
        .filter(
          (name) =>
            name.toLowerCase().includes(input.toLowerCase()) &&
            !value.some((v) => v.toLowerCase() === name.toLowerCase()),
        )
        .slice(0, 8)
    : [];

  const addTag = useCallback(
    (tag: string) => {
      const trimmed = tag.trim();
      if (!trimmed) return;
      // Prevent duplicates (case-insensitive)
      if (value.some((v) => v.toLowerCase() === trimmed.toLowerCase())) return;
      onChange([...value, trimmed]);
      setInput("");
      setHighlightIdx(-1);
      setShowSuggestions(false);
    },
    [value, onChange],
  );

  const removeTag = useCallback(
    (idx: number) => {
      onChange(value.filter((_, i) => i !== idx));
    },
    [value, onChange],
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Tab" && suggestions.length > 0) {
      e.preventDefault();
      const idx = highlightIdx >= 0 ? highlightIdx : 0;
      addTag(suggestions[idx]);
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      if (highlightIdx >= 0 && suggestions[highlightIdx]) {
        addTag(suggestions[highlightIdx]);
      } else if (input.trim()) {
        addTag(input);
      }
      return;
    }
    if (e.key === "Backspace" && !input && value.length > 0) {
      removeTag(value.length - 1);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlightIdx((prev) => Math.min(prev + 1, suggestions.length - 1));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlightIdx((prev) => Math.max(prev - 1, -1));
      return;
    }
    if (e.key === "Escape") {
      setShowSuggestions(false);
      setHighlightIdx(-1);
    }
  };

  // Close dropdown when clicking outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setShowSuggestions(false);
        setHighlightIdx(-1);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Reset highlight when suggestions change
  useEffect(() => {
    setHighlightIdx(-1);
  }, [input]);

  return (
    <div ref={containerRef} className="flex-1 relative">
      <div
        className="input-field flex flex-wrap items-center gap-1 min-h-[34px] py-1 px-2 cursor-text"
        onClick={() => inputRef.current?.focus()}
      >
        {value.map((tag, i) => (
          <span
            key={tag}
            className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-accent/20 text-accent text-xs"
          >
            {tag}
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); removeTag(i); }}
              className="hover:text-red-400 transition-colors ml-0.5"
              aria-label={`Remove ${tag}`}
            >
              <X size={11} />
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          type="text"
          value={input}
          onChange={(e) => { setInput(e.target.value); setShowSuggestions(true); }}
          onFocus={() => setShowSuggestions(true)}
          onKeyDown={handleKeyDown}
          placeholder={value.length === 0 ? "Type to add genres…" : ""}
          className="flex-1 min-w-[80px] bg-transparent outline-none text-sm text-text-primary placeholder:text-text-muted"
        />
      </div>

      {/* Suggestion dropdown */}
      {showSuggestions && suggestions.length > 0 && (
        <div className="absolute z-30 mt-1 left-0 right-0 bg-[var(--color-surface-lighter)] border border-white/10 rounded-lg shadow-xl overflow-hidden max-h-48 overflow-y-auto">
          {suggestions.map((name, i) => {
            const isNew = !knownNames.some((k) => k.toLowerCase() === name.toLowerCase());
            return (
              <button
                key={name}
                type="button"
                className={`w-full text-left px-3 py-1.5 text-sm transition-colors ${
                  i === highlightIdx
                    ? "bg-accent/20 text-accent"
                    : "text-text-primary hover:bg-surface-light/40"
                }`}
                onMouseEnter={() => setHighlightIdx(i)}
                onMouseDown={(e) => { e.preventDefault(); addTag(name); }}
              >
                {name}
                {isNew && <span className="ml-2 text-[10px] text-text-muted">(new)</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
