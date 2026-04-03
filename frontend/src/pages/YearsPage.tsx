import { useMemo, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { CalendarDays, PartyPopper, ListPlus, Trash2, RefreshCw } from "lucide-react";
import { useYears, useRescanBatch, useNormalize, useDeleteBatch } from "@/hooks/queries";
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

type SortDir = "asc" | "desc";
type YearEntry = { year: number | null; count: number; video_ids: number[] };

interface DecadeGroup {
  decade: string;
  decadeStart: number;
  items: YearEntry[];
  /** All video IDs across every year in the decade. */
  allVideoIds: number[];
  totalCount: number;
}

/** Group years by decade, direction controls sort order. */
function groupByDecade(years: YearEntry[], dir: SortDir): DecadeGroup[] {
  const sorted = [...years]
    .filter((y) => y.year != null)
    .sort((a, b) =>
      dir === "desc"
        ? (b.year ?? 0) - (a.year ?? 0)
        : (a.year ?? 0) - (b.year ?? 0),
    );

  const groups: Record<string, YearEntry[]> = {};
  for (const y of sorted) {
    const decade = Math.floor((y.year ?? 0) / 10) * 10;
    const key = `${decade}s`;
    (groups[key] ??= []).push(y);
  }

  const sortedKeys = Object.keys(groups).sort((a, b) => {
    const da = parseInt(a);
    const db = parseInt(b);
    return dir === "desc" ? db - da : da - db;
  });
  return sortedKeys.map((key) => {
    const items = groups[key];
    const decadeStart = parseInt(key);
    const allVideoIds = items.flatMap((y) => y.video_ids);
    const totalCount = items.reduce((sum, y) => sum + y.count, 0);
    return { decade: key, decadeStart, items, allVideoIds, totalCount };
  });
}

export function YearsPage() {
  const [filters, setFilters] = useState<FacetFilterParams>({});
  const { data, isLoading, isError, refetch } = useYears(filters);
  const navigate = useNavigate();
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const { launch: launchParty, isLoading: partyLoading } = usePartyMode();
  const { toast } = useToast();
  const { confirm, dialog } = useConfirm();
  const batchRescanMutation = useRescanBatch();
  const normalizeMutation = useNormalize();
  const batchDeleteMutation = useDeleteBatch();

  // Selection by year key (string of year number)
  const [selectedYears, setSelectedYears] = useState<Set<string>>(new Set());
  const [playlistPickerOpen, setPlaylistPickerOpen] = useState(false);
  const [rescanDialogOpen, setRescanDialogOpen] = useState(false);

  const yearMap = useMemo(() => {
    const m = new Map<string, number[]>();
    if (data) for (const y of data) if (y.year != null) m.set(String(y.year), y.video_ids);
    return m;
  }, [data]);

  const selectedVideoIds = useMemo(() => {
    const ids: number[] = [];
    for (const key of selectedYears) {
      const vids = yearMap.get(key);
      if (vids) ids.push(...vids);
    }
    return ids;
  }, [selectedYears, yearMap]);

  const toggleSelect = useCallback((key: string, sel: boolean) => {
    setSelectedYears((prev) => {
      const next = new Set(prev);
      if (sel) next.add(key); else next.delete(key);
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

  const grouped = useMemo(() => (data ? groupByDecade(data, sortDir) : []), [data, sortDir]);

  return (
    <div className="p-4 md:p-6">
      <div className="flex items-center gap-3 mb-2">
        <h1 className="text-xl font-bold text-text-primary flex items-center gap-2">
          <CalendarDays size={22} /> Years
        </h1>
        <button
          onClick={() => setSortDir((d) => (d === "desc" ? "asc" : "desc"))}
          className="btn-ghost btn-sm text-xs"
          aria-label={`Sort ${sortDir === "desc" ? "oldest first" : "newest first"}`}
        >
          {sortDir === "desc" ? "New→Old" : "Old→New"}
        </button>
        <button
          onClick={() => launchParty(filters)}
          disabled={partyLoading}
          className="btn-sm text-xs font-semibold px-3 py-1.5 rounded-lg bg-gradient-to-r from-pink-500 via-purple-500 to-indigo-500 text-white hover:from-pink-600 hover:via-purple-600 hover:to-indigo-600 transition-all shadow-lg shadow-purple-500/25 flex items-center gap-1.5"
        >
          <PartyPopper size={14} /> Party Mode
        </button>
        {selectedYears.size > 0 && (
          <>
            <span className="text-xs text-accent">{selectedYears.size} selected ({selectedVideoIds.length} videos)</span>
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
                  onSuccess: (res) => { toast({ type: "success", title: `Deleted ${res.count} video(s)` }); setSelectedYears(new Set()); },
                });
              }}
              className="btn-danger btn-sm"
            >
              <Trash2 size={14} /> Delete
            </button>
          </>
        )}
      </div>
      <div className="mb-4">
        <FilterBar filters={filters} onChange={setFilters} hideYearRange />
      </div>

      {isLoading ? (
        <div className="grid grid-cols-[repeat(auto-fill,150px)] gap-4">
          {Array.from({ length: 24 }).map((_, i) => (
            <Skeleton key={i} className="aspect-square rounded-xl" />
          ))}
        </div>
      ) : isError ? (
        <ErrorState message="Failed to load years" onRetry={refetch} />
      ) : !data || data.length === 0 ? (
        <EmptyState icon={<CalendarDays size={48} />} title="No years yet" />
      ) : (
        grouped.map(({ decade, decadeStart, items, allVideoIds, totalCount }) => (
          <GroupedSection key={decade} heading={decade}>
            {/* Decade tile — spans 2 columns × 2 rows, self-centers vertically */}
            <div className="col-span-2 row-span-2 flex items-center justify-center">
              <RecordStack
                videoIds={allVideoIds}
                label={decade}
                subLabel={`${totalCount} video${totalCount !== 1 ? "s" : ""}`}
                onClick={() =>
                  navigate(
                    `/library?year_from=${decadeStart}&year_to=${decadeStart + 9}`,
                  )
                }
                selected={selectedYears.has(decade)}
                onSelect={(sel) => toggleSelect(decade, sel)}
                onContextAction={handleContextAction}
              />
            </div>
            {items.map((y) => (
              <RecordStack
                key={y.year ?? "null"}
                videoIds={y.video_ids}
                label={String(y.year ?? "—")}
                subLabel={`${y.count} video${y.count !== 1 ? "s" : ""}`}
                onClick={() =>
                  y.year != null
                    ? navigate(`/library?year=${y.year}`)
                    : navigate("/library")
                }
                selected={y.year != null && selectedYears.has(String(y.year))}
                onSelect={(sel) => y.year != null && toggleSelect(String(y.year), sel)}
                onContextAction={handleContextAction}
              />
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
