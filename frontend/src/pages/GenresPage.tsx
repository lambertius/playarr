import { useMemo, useState, useCallback } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Tags, PartyPopper, EyeOff, CheckSquare, Square, X, ListPlus, Trash2, RefreshCw } from "lucide-react";
import { useGenres, useRescanBatch, useNormalize, useDeleteBatch } from "@/hooks/queries";
import { useUpdateGenreBlacklist } from "@/hooks/queries";
import { EmptyState, ErrorState, Skeleton } from "@/components/Feedback";
import { RecordStack } from "@/components/RecordStack";
import { GroupedSection } from "@/components/GroupedSection";
import { FilterBar } from "@/components/FilterBar";
import { PlaylistPicker } from "@/components/PlaylistPicker";
import { RescanOptionsDialog } from "@/components/RescanOptionsDialog";
import type { RescanOptions } from "@/components/RescanOptionsDialog";
import { useToast } from "@/components/Toast";
import { useConfirm } from "@/components/ConfirmDialog";
import type { FacetFilterParams } from "@/types";
import { usePartyMode } from "@/hooks/usePartyMode";
import { settingsApi } from "@/lib/api";

type SortDir = "asc" | "desc";

/** Group genres alphabetically by first letter. */
function groupByLetter(genres: { genre: string; count: number; video_ids: number[] }[], dir: SortDir) {
  const sorted = [...genres].sort((a, b) =>
    dir === "asc" ? a.genre.localeCompare(b.genre) : b.genre.localeCompare(a.genre),
  );
  const groups: Record<string, typeof genres> = {};
  for (const g of sorted) {
    const first = (g.genre?.[0] ?? "").toUpperCase();
    const key = /[A-Z]/.test(first) ? first : "#";
    (groups[key] ??= []).push(g);
  }
  const sortedKeys = Object.keys(groups).sort((a, b) => {
    if (a === "#") return dir === "asc" ? -1 : 1;
    if (b === "#") return dir === "asc" ? 1 : -1;
    return dir === "asc" ? a.localeCompare(b) : b.localeCompare(a);
  });
  return sortedKeys.map((key) => ({ letter: key, items: groups[key] }));
}

export function GenresPage() {
  const [filters, setFilters] = useState<FacetFilterParams>({});
  const [searchParams] = useSearchParams();
  const searchTerm = searchParams.get("search") ?? "";
  const mergedFilters = useMemo(() => (searchTerm ? { ...filters, search: searchTerm } : filters), [filters, searchTerm]);
  const { data, isLoading, isError, refetch } = useGenres(mergedFilters);
  const navigate = useNavigate();
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const { launch: launchParty, isLoading: partyLoading } = usePartyMode();
  const { toast } = useToast();
  const { confirm, dialog } = useConfirm();
  const batchRescanMutation = useRescanBatch();
  const normalizeMutation = useNormalize();
  const batchDeleteMutation = useDeleteBatch();

  // Blacklist selection mode state
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const blacklistMutation = useUpdateGenreBlacklist();

  // Stack selection for playlist/bulk actions
  const [selectedGenres, setSelectedGenres] = useState<Set<string>>(new Set());
  const [playlistPickerOpen, setPlaylistPickerOpen] = useState(false);
  const [rescanDialogOpen, setRescanDialogOpen] = useState(false);

  const genreMap = useMemo(() => {
    const m = new Map<string, number[]>();
    if (data) for (const g of data) m.set(g.genre, g.video_ids);
    return m;
  }, [data]);

  const selectedVideoIds = useMemo(() => {
    const ids: number[] = [];
    for (const name of selectedGenres) {
      const vids = genreMap.get(name);
      if (vids) ids.push(...vids);
    }
    return ids;
  }, [selectedGenres, genreMap]);

  const toggleStackSelect = useCallback((genre: string, sel: boolean) => {
    setSelectedGenres((prev) => {
      const next = new Set(prev);
      if (sel) next.add(genre); else next.delete(genre);
      return next;
    });
  }, []);

  const handleContextAction = useCallback(
    async (action: string, videoIds: number[]) => {
      switch (action) {
        case "edit_metadata":
          if (videoIds.length === 1) navigate(`/video/${videoIds[0]}`);
          break;
        case "rescan":
          batchRescanMutation.mutate({ video_ids: videoIds }, {
            onSuccess: () => toast({ type: "success", title: `Rescan queued for ${videoIds.length} video(s)` }),
          });
          break;
        case "normalise":
        case "normalize":
          normalizeMutation.mutate({ video_ids: videoIds }, {
            onSuccess: () => toast({ type: "success", title: `Normalisation queued for ${videoIds.length} video(s)` }),
          });
          break;
        case "redownload":
          toast({ type: "info", title: "Open individual video pages to redownload" });
          break;
        case "undo_rescan":
          toast({ type: "info", title: "Open individual video pages to undo rescan" });
          break;
        case "delete": {
          const ok = await confirm({
            title: `Delete ${videoIds.length} video(s)?`,
            description: "The video files and all metadata will be permanently removed.",
            confirmLabel: "Delete",
            variant: "danger",
          });
          if (ok) {
            batchDeleteMutation.mutate(videoIds, {
              onSuccess: (res) => toast({ type: "success", title: `Deleted ${res.count} video(s)` }),
            });
          }
          break;
        }
      }
    },
    [navigate, batchRescanMutation, normalizeMutation, batchDeleteMutation, toast, confirm],
  );

  // We need genre name → id mapping for the blacklist API. Fetch on demand.
  const [genreIdMap, setGenreIdMap] = useState<Record<string, number>>({});

  const enterSelectMode = useCallback(async () => {
    setSelectMode(true);
    setSelected(new Set());
    // Fetch genre id map
    try {
      const all = await settingsApi.genreBlacklist();
      const map: Record<string, number> = {};
      for (const g of all) map[g.name] = g.id;
      setGenreIdMap(map);
    } catch { /* ignore */ }
  }, []);

  const exitSelectMode = useCallback(() => {
    setSelectMode(false);
    setSelected(new Set());
  }, []);

  const toggleGenre = useCallback((name: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }, []);

  const hideSelected = useCallback(() => {
    const ids = Array.from(selected).map((name) => genreIdMap[name]).filter(Boolean);
    if (!ids.length) return;
    blacklistMutation.mutate(
      { genre_ids: ids, blacklisted: true },
      {
        onSuccess: () => {
          toast({ type: "success", title: `${ids.length} genre${ids.length !== 1 ? "s" : ""} hidden` });
          exitSelectMode();
          refetch();
        },
        onError: () => toast({ type: "error", title: "Failed to hide genres" }),
      },
    );
  }, [selected, genreIdMap, blacklistMutation, toast, exitSelectMode, refetch]);

  // Client-side filter: when searching, only show stacks whose genre name matches
  const filtered = useMemo(() => {
    if (!data || !searchTerm) return data ?? [];
    const term = searchTerm.toLowerCase();
    return data.filter((g) => g.genre.toLowerCase().includes(term));
  }, [data, searchTerm]);

  const grouped = useMemo(() => (filtered.length ? groupByLetter(filtered, sortDir) : []), [filtered, sortDir]);

  return (
    <div className="p-4 md:p-6">
      <div className="flex items-center gap-3 mb-2">
        <h1 className="text-xl font-bold text-text-primary flex items-center gap-2">
          <Tags size={22} /> Genres
        </h1>
        <button
          onClick={() => setSortDir((d) => (d === "asc" ? "desc" : "asc"))}
          className="btn-ghost btn-sm text-xs"
          aria-label={`Sort ${sortDir === "asc" ? "descending" : "ascending"}`}
        >
          {sortDir === "asc" ? "A→Z" : "Z→A"}
        </button>
        <button
          onClick={() => launchParty(mergedFilters)}
          disabled={partyLoading}
          className="btn-sm text-xs font-semibold px-3 py-1.5 rounded-lg bg-gradient-to-r from-pink-500 via-purple-500 to-indigo-500 text-white hover:from-pink-600 hover:via-purple-600 hover:to-indigo-600 transition-all shadow-lg shadow-purple-500/25 flex items-center gap-1.5"
        >
          <PartyPopper size={14} /> Party Mode
        </button>
        {selectedGenres.size > 0 && !selectMode && (
          <>
            <span className="text-xs text-accent">{selectedGenres.size} selected ({selectedVideoIds.length} videos)</span>
            <button onClick={() => setPlaylistPickerOpen(true)} className="btn-secondary btn-sm">
              <ListPlus size={14} /> Add to Playlist
            </button>
            <button onClick={() => setRescanDialogOpen(true)} className="btn-secondary btn-sm">
              <RefreshCw size={14} /> Rescan
            </button>
            <button
              onClick={async () => {
                const ok = await confirm({
                  title: `Delete ${selectedVideoIds.length} video(s)?`,
                  description: "The video files and all metadata will be permanently removed.",
                  confirmLabel: "Delete",
                  variant: "danger",
                });
                if (ok) batchDeleteMutation.mutate(selectedVideoIds, {
                  onSuccess: (res) => { toast({ type: "success", title: `Deleted ${res.count} video(s)` }); setSelectedGenres(new Set()); },
                });
              }}
              className="btn-danger btn-sm"
            >
              <Trash2 size={14} /> Delete
            </button>
          </>
        )}
        <div className="flex-1" />
        {!selectMode ? (
          <button
            onClick={enterSelectMode}
            className="btn-ghost btn-sm text-xs flex items-center gap-1.5"
          >
            <EyeOff size={14} /> Manage
          </button>
        ) : (
          <div className="flex items-center gap-2">
            <span className="text-xs text-text-muted">
              {selected.size} selected
            </span>
            <button
              onClick={hideSelected}
              disabled={selected.size === 0 || blacklistMutation.isPending}
              className="btn-sm text-xs font-semibold px-3 py-1.5 rounded-lg bg-red-500/20 text-red-400 hover:bg-red-500/30 transition-all flex items-center gap-1.5 disabled:opacity-40"
            >
              <EyeOff size={13} /> Hide Selected
            </button>
            <button
              onClick={exitSelectMode}
              className="btn-ghost btn-sm text-xs flex items-center gap-1"
            >
              <X size={14} /> Cancel
            </button>
          </div>
        )}
      </div>
      <div className="mb-4">
        <FilterBar filters={filters} onChange={setFilters} hideGenre />
      </div>

      {isLoading ? (
        <div className="grid grid-cols-[repeat(auto-fill,150px)] gap-4">
          {Array.from({ length: 24 }).map((_, i) => (
            <Skeleton key={i} className="aspect-square rounded-xl" />
          ))}
        </div>
      ) : isError ? (
        <ErrorState message="Failed to load genres" onRetry={refetch} />
      ) : !filtered || filtered.length === 0 ? (
        <EmptyState icon={<Tags size={48} />} title={searchTerm ? "No matching genres" : "No genres yet"} />
      ) : (
        grouped.map(({ letter, items }) => (
          <GroupedSection key={letter} heading={letter}>
            {items.map((g) => (
              <div key={g.genre} className="relative">
                {selectMode && (
                  <button
                    onClick={(e) => { e.stopPropagation(); toggleGenre(g.genre); }}
                    className="absolute top-1 left-1 z-20 p-0.5 rounded bg-black/50 hover:bg-black/70 transition-colors"
                  >
                    {selected.has(g.genre) ? (
                      <CheckSquare size={20} className="text-accent" />
                    ) : (
                      <Square size={20} className="text-white/60" />
                    )}
                  </button>
                )}
                <div className={selectMode && selected.has(g.genre) ? "ring-2 ring-accent rounded-xl" : ""}>
                  <RecordStack
                    videoIds={g.video_ids}
                    label={g.genre}
                    subLabel={`${g.count} video${g.count !== 1 ? "s" : ""}`}
                    onClick={() => {
                      if (selectMode) {
                        toggleGenre(g.genre);
                      } else {
                        navigate(`/library?genre=${encodeURIComponent(g.genre)}`);
                      }
                    }}
                    selected={!selectMode && selectedGenres.has(g.genre)}
                    onSelect={!selectMode ? (sel) => toggleStackSelect(g.genre, sel) : undefined}
                    onContextAction={!selectMode ? handleContextAction : undefined}
                  />
                </div>
              </div>
            ))}
          </GroupedSection>
        ))
      )}

      {dialog}
      <PlaylistPicker
        open={playlistPickerOpen}
        videoIds={selectedVideoIds}
        onClose={() => setPlaylistPickerOpen(false)}
      />
      <RescanOptionsDialog
        open={rescanDialogOpen}
        count={selectedVideoIds.length}
        isPending={batchRescanMutation.isPending}
        onClose={() => setRescanDialogOpen(false)}
        onConfirm={(opts: RescanOptions) => {
          batchRescanMutation.mutate({
            video_ids: selectedVideoIds,
            scrape_wikipedia: opts.scrape_wikipedia,
            scrape_musicbrainz: opts.scrape_musicbrainz,
            ai_auto: opts.ai_auto,
            ai_only: opts.ai_only,
            hint_cover: opts.hint_cover,
            hint_live: opts.hint_live,
            hint_alternate: opts.hint_alternate,
            normalize: opts.normalize,
            find_source_video: opts.find_source_video,
            from_disk: opts.from_disk,
          }, {
            onSuccess: () => { setRescanDialogOpen(false); toast({ type: "success", title: `Rescan queued for ${selectedVideoIds.length} video(s)` }); },
          });
        }}
      />
    </div>
  );
}
