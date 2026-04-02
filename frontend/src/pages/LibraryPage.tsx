import { useState, useCallback } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import {
  LayoutGrid, List, RefreshCw, ChevronLeft, ChevronRight,
  Library as LibraryIcon, Trash2, CheckSquare, Square, PartyPopper, ListPlus,
} from "lucide-react";
import { useLibrary, useRescanBatch, useRescan, useNormalize, useDeleteVideo, useDeleteBatch } from "@/hooks/queries";
import { VideoCard } from "@/components/VideoCard";
import { VideoRow } from "@/components/VideoRow";
import { FilterBar } from "@/components/FilterBar";
import { LibrarySkeleton, EmptyState, ErrorState } from "@/components/Feedback";
import { useToast } from "@/components/Toast";
import { useConfirm } from "@/components/ConfirmDialog";
import { Tooltip } from "@/components/Tooltip";
import { RescanOptionsDialog } from "@/components/RescanOptionsDialog";
import type { RescanOptions } from "@/components/RescanOptionsDialog";
import { PlaylistPicker } from "@/components/PlaylistPicker";
import { usePartyMode } from "@/hooks/usePartyMode";
import type { ViewMode, LibraryParams, FacetFilterParams } from "@/types";

const SORT_OPTIONS = [
  { value: "artist", label: "Artist" },
  { value: "title", label: "Title" },
  { value: "year", label: "Year" },
  { value: "created_at", label: "Recently Added" },
  { value: "updated_at", label: "Recently Updated" },
];

const STORAGE_KEY_VIEW = "playarr:library:view";
const STORAGE_KEY_SORT = "playarr:library:sort";
const STORAGE_KEY_DIR = "playarr:library:dir";
const STORAGE_KEY_PAGE_SIZE = "playarr:library:pageSize";
const PAGE_SIZE_OPTIONS = [12, 24, 48, 96, 192];

function loadStorage(key: string, fallback: string): string {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}

export function LibraryPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const [view, setViewRaw] = useState<ViewMode>(() => loadStorage(STORAGE_KEY_VIEW, "grid") as ViewMode);
  const setView = useCallback((v: ViewMode) => {
    setViewRaw(v);
    try { localStorage.setItem(STORAGE_KEY_VIEW, v); } catch { /* ignore */ }
  }, []);
  const [pageSize, setPageSizeRaw] = useState<number>(() => {
    const stored = loadStorage(STORAGE_KEY_PAGE_SIZE, "48");
    const n = Number(stored);
    return PAGE_SIZE_OPTIONS.includes(n) ? n : 48;
  });
  const setPageSize = useCallback((n: number) => {
    setPageSizeRaw(n);
    try { localStorage.setItem(STORAGE_KEY_PAGE_SIZE, String(n)); } catch { /* ignore */ }
  }, []);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [rescanDialogOpen, setRescanDialogOpen] = useState(false);
  const [playlistPickerOpen, setPlaylistPickerOpen] = useState(false);
  const { toast } = useToast();
  const { confirm, dialog } = useConfirm();

  // Parse params — fall back to localStorage for sort settings
  const params: LibraryParams = {
    page: Number(searchParams.get("page")) || 1,
    page_size: pageSize,
    search: searchParams.get("search") ?? undefined,
    artist: searchParams.get("artist") ?? undefined,
    album: searchParams.get("album") ?? undefined,
    album_entity_id: searchParams.get("album_entity_id") ? Number(searchParams.get("album_entity_id")) : undefined,
    genre: searchParams.get("genre") ?? undefined,
    year: searchParams.get("year") ? Number(searchParams.get("year")) : undefined,
    year_from: searchParams.get("year_from") ? Number(searchParams.get("year_from")) : undefined,
    year_to: searchParams.get("year_to") ? Number(searchParams.get("year_to")) : undefined,
    version_type: searchParams.get("version_type") ?? undefined,
    enrichment: searchParams.get("enrichment") ?? undefined,
    import_method: searchParams.get("import_method") ?? undefined,
    song_rating: searchParams.get("song_rating") ? Number(searchParams.get("song_rating")) : undefined,
    video_rating: searchParams.get("video_rating") ? Number(searchParams.get("video_rating")) : undefined,
    sort_by: searchParams.get("sort") ?? loadStorage(STORAGE_KEY_SORT, "artist"),
    sort_dir: (searchParams.get("dir") as "asc" | "desc") ?? loadStorage(STORAGE_KEY_DIR, "asc") as "asc" | "desc",
  };

  const { data, isLoading, isError, refetch } = useLibrary(params);
  const batchRescanMutation = useRescanBatch();
  const rescanMutation = useRescan();
  const normalizeMutation = useNormalize();
  const deleteMutation = useDeleteVideo();
  const batchDeleteMutation = useDeleteBatch();
  const { launch: launchParty, isLoading: partyLoading } = usePartyMode();

  // Selection helpers
  const handleSelect = useCallback((videoId: number, checked: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(videoId);
      else next.delete(videoId);
      return next;
    });
  }, []);

  const pageIds = data?.items.map((v) => v.id) ?? [];
  const allPageSelected = pageIds.length > 0 && pageIds.every((id) => selectedIds.has(id));

  const toggleSelectAll = useCallback(() => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allPageSelected) {
        pageIds.forEach((id) => next.delete(id));
      } else {
        pageIds.forEach((id) => next.add(id));
      }
      return next;
    });
  }, [allPageSelected, pageIds]);

  const setParam = useCallback(
    (key: string, value: string | null) => {
      // Persist sort settings to localStorage
      try {
        if (key === "sort" && value) localStorage.setItem(STORAGE_KEY_SORT, value);
        if (key === "dir" && value) localStorage.setItem(STORAGE_KEY_DIR, value);
      } catch { /* ignore */ }
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(key, value);
        else next.delete(key);
        if (key !== "page") next.delete("page"); // reset page on filter change
        return next;
      });
    },
    [setSearchParams]
  );

  // Card/row action handler
  const handleAction = useCallback(
    async (action: string, videoId: number) => {
      switch (action) {
        case "play":
          navigate(`/video/${videoId}`);
          break;
        case "edit_metadata":
          navigate(`/video/${videoId}`);
          break;
        case "add_to_playlist":
          // Will be handled by playlist popup later
          break;
        case "rescan":
          rescanMutation.mutate(videoId, {
            onSuccess: () => toast({ type: "success", title: "Rescan queued" }),
          });
          break;
        case "normalise":
        case "normalize":
          normalizeMutation.mutate({ video_ids: [videoId] }, {
            onSuccess: () => toast({ type: "success", title: "Normalisation queued" }),
          });
          break;
        case "undo_rescan":
          toast({ type: "info", title: "Open detail page to undo rescan" });
          break;
        case "delete": {
          const ok = await confirm({
            title: "Delete this video?",
            description: "The video file and all metadata will be permanently removed.",
            confirmLabel: "Delete",
            variant: "danger",
          });
          if (ok) {
            deleteMutation.mutate(videoId, {
              onSuccess: () => toast({ type: "success", title: "Video deleted" }),
            });
          }
          break;
        }
      }
    },
    [navigate, rescanMutation, normalizeMutation, deleteMutation, toast, confirm]
  );

  // Derive FilterBar state from URL params
  const facetFilters: FacetFilterParams = {
    version_type: params.version_type,
    artist: params.artist,
    genre: params.genre,
    year_from: params.year_from,
    year_to: params.year_to,
    song_rating: params.song_rating,
    video_rating: params.video_rating,
  };

  const handleFilterChange = useCallback((f: FacetFilterParams) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      // Sync all facet keys
      const keys: (keyof FacetFilterParams)[] = ["version_type", "artist", "genre", "year_from", "year_to", "song_rating", "video_rating"];
      for (const k of keys) {
        if (f[k] != null && f[k] !== "") next.set(k, String(f[k]));
        else next.delete(k);
      }
      next.delete("page"); // reset page on filter change
      return next;
    });
  }, [setSearchParams]);

  // Active filter pills (only non-facet filters — facet ones live in FilterBar)
  const activeFilters = [
    params.search && { key: "search", label: `"${params.search}"` },
    params.album_entity_id && { key: "album_entity_id", label: params.album ? `Album: ${params.album}` : `Album ID: ${params.album_entity_id}` },
    !params.album_entity_id && params.album && { key: "album", label: `Album: ${params.album}` },
    params.year && { key: "year", label: `Year: ${params.year}` },
    params.enrichment && { key: "enrichment", label: `AI: ${params.enrichment}` },
    params.import_method && { key: "import_method", label: `Source: ${params.import_method}` },
  ].filter(Boolean) as { key: string; label: string }[];

  return (
    <div className="p-4 md:p-6">
      {/* Header toolbar */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <h1 className="text-xl font-bold text-text-primary flex items-center gap-2">
          <LibraryIcon size={22} /> Library
        </h1>
        <Tooltip content="Shuffle the current library view into a continuous playback queue. Active filters are applied — only matching videos are included.">
          <button
            onClick={() => launchParty({
              search: params.search,
              artist: params.artist,
              album: params.album,
              genre: params.genre,
              year: params.year,
              year_from: params.year_from,
              year_to: params.year_to,
              version_type: params.version_type,
              enrichment: params.enrichment,
              song_rating: params.song_rating,
              video_rating: params.video_rating,
            })}
            disabled={partyLoading}
            className="btn-sm text-xs font-semibold px-3 py-1.5 rounded-lg bg-gradient-to-r from-pink-500 via-purple-500 to-indigo-500 text-white hover:from-pink-600 hover:via-purple-600 hover:to-indigo-600 transition-all shadow-lg shadow-purple-500/25 flex items-center gap-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-400"
          >
            <PartyPopper size={14} /> Party Mode
          </button>
        </Tooltip>
        <div className="ml-auto" />

        {/* Sort */}
        <select
          value={params.enrichment ?? ""}
          onChange={(e) => setParam("enrichment", e.target.value || null)}
          className="input-field w-auto py-1.5 text-xs"
          aria-label="Filter by enrichment"
        >
          <option value="">All AI</option>
          <option value="enriched">AI Enriched</option>
          <option value="partial">Partial AI</option>
          <option value="pending">No AI</option>
        </select>
        <select
          value={params.import_method ?? ""}
          onChange={(e) => setParam("import_method", e.target.value || null)}
          className="input-field w-auto py-1.5 text-xs"
          aria-label="Filter by source"
        >
          <option value="">All Sources</option>
          <option value="url">URL Import</option>
          <option value="import">Library Import</option>
          <option value="scanned">Scanned</option>
        </select>
        <select
          value={params.sort_by}
          onChange={(e) => setParam("sort", e.target.value)}
          className="input-field w-auto py-1.5 text-xs"
          aria-label="Sort by"
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        <Tooltip content={params.sort_dir === "asc" ? "Currently ascending — click to sort descending" : "Currently descending — click to sort ascending"}>
          <button
            onClick={() => setParam("dir", params.sort_dir === "asc" ? "desc" : "asc")}
            className="btn-ghost btn-sm text-xs"
            aria-label={`Sort ${params.sort_dir === "asc" ? "descending" : "ascending"}`}
          >
            {params.sort_dir === "asc" ? "A\u2192Z" : "Z\u2192A"}
          </button>
        </Tooltip>

        {/* Per-page selector */}
        <select
          value={pageSize}
          onChange={(e) => {
            setPageSize(Number(e.target.value));
            setParam("page", null);
          }}
          className="input-field w-auto py-1.5 text-xs"
          aria-label="Items per page"
        >
          {PAGE_SIZE_OPTIONS.map((n) => (
            <option key={n} value={n}>{n} per page</option>
          ))}
        </select>

        {/* View toggle */}
        <div className="flex border border-surface-border rounded-lg overflow-hidden">
          <Tooltip content="Grid view">
            <button
              onClick={() => setView("grid")}
              className={`p-1.5 ${view === "grid" ? "bg-accent/10 text-accent" : "text-text-muted hover:text-text-primary"}`}
              aria-label="Grid view"
            >
              <LayoutGrid size={16} />
            </button>
          </Tooltip>
          <Tooltip content="List view">
            <button
              onClick={() => setView("list")}
              className={`p-1.5 ${view === "list" ? "bg-accent/10 text-accent" : "text-text-muted hover:text-text-primary"}`}
              aria-label="List view"
            >
              <List size={16} />
            </button>
          </Tooltip>
        </div>

        {/* Library actions */}
        <Tooltip content={allPageSelected ? "Deselect all videos on this page" : "Select all videos on this page for bulk actions"}>
          <button
            onClick={toggleSelectAll}
            className="btn-ghost btn-sm"
          >
            {allPageSelected ? <CheckSquare size={14} /> : <Square size={14} />}
            {selectedIds.size > 0 && (
              <span className="text-xs text-accent">{selectedIds.size}</span>
            )}
          </button>
        </Tooltip>
        <Tooltip content="Run metadata scrapers on selected videos. Choose sources in the options dialog.">
          <button
            onClick={() => {
              if (selectedIds.size === 0) {
                toast({ type: "info", title: "Select videos to rescan" });
                return;
              }
              setRescanDialogOpen(true);
            }}
            disabled={batchRescanMutation.isPending || selectedIds.size === 0}
            className="btn-secondary btn-sm"
          >
            <RefreshCw size={14} /> Rescan Selected
          </button>
        </Tooltip>
        <Tooltip content="Add selected videos to an existing or new playlist.">
          <button
            onClick={() => {
              if (selectedIds.size === 0) {
                toast({ type: "info", title: "Select videos first" });
                return;
              }
              setPlaylistPickerOpen(true);
            }}
            disabled={selectedIds.size === 0}
            className="btn-secondary btn-sm"
          >
            <ListPlus size={14} /> Add to Playlist
          </button>
        </Tooltip>
        <Tooltip content="Permanently delete selected videos and all associated metadata. This cannot be undone.">
          <button
            onClick={async () => {
              const ids = [...selectedIds];
              if (ids.length === 0) {
                toast({ type: "info", title: "Select videos to delete" });
                return;
              }
              const ok = await confirm({
                title: `Delete ${ids.length} video(s)?`,
                description: "The video files and all metadata will be permanently removed. This cannot be undone.",
                confirmLabel: "Delete",
                variant: "danger",
              });
              if (ok) {
                batchDeleteMutation.mutate(ids, {
                  onSuccess: (res) => {
                    toast({ type: "success", title: `Deleted ${res.count} video(s)` });
                    setSelectedIds(new Set());
                  },
                });
              }
            }}
            disabled={batchDeleteMutation.isPending || selectedIds.size === 0}
            className="btn-danger btn-sm"
          >
            <Trash2 size={14} /> Delete Selected
          </button>
        </Tooltip>
      </div>

      {/* FilterBar */}
      <FilterBar filters={facetFilters} onChange={handleFilterChange} />

      {/* Active filter pills */}
      {activeFilters.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-4">
          {activeFilters.map((f) => (
            <span
              key={f.key}
              className="inline-flex items-center gap-1 badge-blue cursor-pointer"
              onClick={() => {
                if (f.key === "album_entity_id") {
                  // Clear both album_entity_id and the companion album name param
                  setSearchParams((prev) => {
                    const next = new URLSearchParams(prev);
                    next.delete("album_entity_id");
                    next.delete("album");
                    next.delete("page");
                    return next;
                  });
                } else {
                  setParam(f.key, null);
                }
              }}
            >
              {f.label}
              <span className="text-blue-300 hover:text-white">&times;</span>
            </span>
          ))}
          <button
            onClick={() => setSearchParams({})}
            className="text-xs text-text-muted hover:text-text-primary"
          >
            Clear all
          </button>
        </div>
      )}

      {/* Content */}
      {isLoading ? (
        <LibrarySkeleton />
      ) : isError ? (
        <ErrorState message="Failed to load library" onRetry={refetch} />
      ) : !data || data.items.length === 0 ? (
        <EmptyState
          icon={<LibraryIcon size={48} />}
          title="No videos yet"
          description="Import your first music video using the Add Video button."
        />
      ) : (
        <>
          {view === "grid" ? (
            <div className="grid grid-cols-[repeat(auto-fill,200px)] gap-4">
              {data.items.map((v) => (
                <VideoCard
                  key={v.id}
                  video={v}
                  onAction={handleAction}
                  selected={selectedIds.has(v.id)}
                  onSelect={handleSelect}
                />
              ))}
            </div>
          ) : (
            <div className="card p-0 overflow-hidden">
              {/* Header row */}
              <div className="flex items-center gap-3 px-4 py-2 border-b border-surface-border text-xs text-text-muted font-medium">
                <div className="flex-shrink-0">
                  <input
                    type="checkbox"
                    checked={allPageSelected}
                    onChange={toggleSelectAll}
                    className="h-4 w-4 rounded border-surface-border bg-surface-lighter text-accent focus:ring-accent cursor-pointer accent-[var(--color-accent)]"
                  />
                </div>
                <span className="w-16 flex-shrink-0" />
                <span className="flex-1">Artist / Title</span>
                <span className="hidden md:block w-16 text-center">Year</span>
                <span className="hidden sm:block w-16 text-center">Quality</span>
                <span className="hidden sm:block w-16 text-center">AI</span>
                <span className="hidden lg:block w-24 text-right">Added</span>
                <span className="w-8" />
              </div>
              {data.items.map((v) => (
                <VideoRow
                  key={v.id}
                  video={v}
                  onAction={handleAction}
                  selected={selectedIds.has(v.id)}
                  onSelect={handleSelect}
                />
              ))}
            </div>
          )}

          {/* Pagination */}
          {data.total_pages > 1 && (
            <div className="flex items-center justify-center gap-4 mt-6">
              <button
                onClick={() => setParam("page", String(data.page - 1))}
                disabled={data.page <= 1}
                className="btn-ghost btn-sm"
              >
                <ChevronLeft size={16} /> Prev
              </button>
              <span className="text-sm text-text-secondary">
                Page {data.page} of {data.total_pages}
                <span className="ml-2 text-text-muted">({data.total} items)</span>
              </span>
              <button
                onClick={() => setParam("page", String(data.page + 1))}
                disabled={data.page >= data.total_pages}
                className="btn-ghost btn-sm"
              >
                Next <ChevronRight size={16} />
              </button>
            </div>
          )}
        </>
      )}

      {dialog}
      <RescanOptionsDialog
        open={rescanDialogOpen}
        count={selectedIds.size}
        isPending={batchRescanMutation.isPending}
        onClose={() => setRescanDialogOpen(false)}
        onConfirm={(opts: RescanOptions) => {
          const ids = [...selectedIds];
          batchRescanMutation.mutate({
            video_ids: ids,
            scrape_wikipedia: opts.scrape_wikipedia,
            scrape_musicbrainz: opts.scrape_musicbrainz,
            ai_auto: opts.ai_auto,
            ai_only: opts.ai_only,
            hint_cover: opts.hint_cover,
            hint_live: opts.hint_live,
            hint_alternate: opts.hint_alternate,
            normalize: opts.normalize,
            find_source_video: opts.find_source_video,
          }, {
            onSuccess: (res) => {
              setRescanDialogOpen(false);
              if (res.locked_skipped) {
                toast({ type: "info", title: `Rescan queued for ${ids.length - (res.locked_skipped ?? 0)} video(s) — ${res.locked_skipped} with locked metadata excluded` });
              } else {
                toast({ type: "success", title: `Rescan queued for ${ids.length} video(s)` });
              }
            },
            onError: (err) => {
              const msg = (err as any)?.response?.data?.detail ?? "Rescan failed";
              toast({ type: "error", title: "Rescan failed", description: String(msg) });
            },
          });
        }}
      />
      <PlaylistPicker
        open={playlistPickerOpen}
        videoIds={[...selectedIds]}
        onClose={() => setPlaylistPickerOpen(false)}
      />
    </div>
  );
}
