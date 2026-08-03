import { useMemo, useState, useCallback } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { CalendarDays, PartyPopper, ListPlus, Trash2, RefreshCw } from "lucide-react";
import { useYears, useRescanBatch, useNormalize, useDeleteBatch } from "@/hooks/queries";
import { EmptyState, ErrorState, Skeleton } from "@/components/Feedback";
import { RecordStack } from "@/components/RecordStack";
import { DataView } from "@/components/DataView";
import { GroupedSection } from "@/components/GroupedSection";
import { FilterBar } from "@/components/FilterBar";
import { PlaylistPicker } from "@/components/PlaylistPicker";
import { RescanOptionsDialog } from "@/components/RescanOptionsDialog";
import type { RescanOptions } from "@/components/RescanOptionsDialog";
import { useToast } from "@/components/Toast";
import { useConfirm } from "@/components/ConfirmDialog";
import type { FacetFilterParams } from "@/types";
import { usePartyMode } from "@/hooks/usePartyMode";

type YearEntry = { year: number | null; count: number; video_ids: number[] };

interface DecadeGroup {
  decade: string;
  decadeStart: number;
  items: Array<YearEntry & { year: number }>;
  allVideoIds: number[];
  totalCount: number;
}

/** Preserve the supplied year order while collecting each decade. */
function groupByDecade(years: Array<YearEntry & { year: number }>): DecadeGroup[] {
  const groups = new Map<number, Array<YearEntry & { year: number }>>();
  for (const year of years) {
    const decadeStart = Math.floor(year.year / 10) * 10;
    const group = groups.get(decadeStart) ?? [];
    group.push(year);
    groups.set(decadeStart, group);
  }
  return [...groups.entries()].map(([decadeStart, items]) => ({
    decade: `${decadeStart}s`,
    decadeStart,
    items,
    allVideoIds: items.flatMap((item) => item.video_ids),
    totalCount: items.reduce((total, item) => total + item.count, 0),
  }));
}

export function YearsPage() {
  const [filters, setFilters] = useState<FacetFilterParams>({});
  const [searchParams] = useSearchParams();
  const searchTerm = searchParams.get("search") ?? "";
  const mergedFilters = useMemo(() => (searchTerm ? { ...filters, search: searchTerm } : filters), [filters, searchTerm]);
  const { data, isLoading, isError, refetch } = useYears(mergedFilters);
  const navigate = useNavigate();
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
    if (data) {
      const byDecade = new Map<number, number[]>();
      for (const y of data) if (y.year != null) {
        m.set(String(y.year), y.video_ids);
        const decade = Math.floor(y.year / 10) * 10;
        byDecade.set(decade, [...(byDecade.get(decade) ?? []), ...y.video_ids]);
      }
      for (const [decade, ids] of byDecade) m.set(`${decade}s`, ids);
    }
    return m;
  }, [data]);

  const selectedVideoIds = useMemo(() => {
    const ids: number[] = [];
    for (const key of selectedYears) {
      const vids = yearMap.get(key);
      if (vids) ids.push(...vids);
    }
    return [...new Set(ids)];
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
              onError: (err: any) => toast({ type: "error", title: err?.response?.data?.detail || "Failed to delete videos" }),
            });
          }
          break;
        }
      }
    },
    [navigate, batchRescanMutation, normalizeMutation, batchDeleteMutation, toast, confirm],
  );

  // Client-side filter: when searching, only show stacks whose year matches
  const filtered = useMemo(() => {
    if (!data || !searchTerm) return data ?? [];
    const term = searchTerm.toLowerCase();
    return data.filter((y) => y.year != null && String(y.year).includes(term));
  }, [data, searchTerm]);

  const years = useMemo(() => filtered.filter((entry): entry is YearEntry & { year: number } => entry.year != null), [filtered]);

  return (
    <div className="p-4 md:p-6">
      <div className="flex items-center gap-3 mb-2">
        <h1 className="text-xl font-bold text-text-primary flex items-center gap-2">
          <CalendarDays size={22} /> Years
        </h1>
        <button
          onClick={() => launchParty(mergedFilters)}
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
                  onError: (err: any) => toast({ type: "error", title: err?.response?.data?.detail || "Failed to delete videos" }),
                });
              }}
              className="btn-danger btn-sm"
            >
              <Trash2 size={14} /> Delete
            </button>
          </>
        )}
      </div>
      {(isLoading || isError || !filtered || filtered.length === 0) && (
        <FilterBar filters={filters} onChange={setFilters} hideYearRange />
      )}

      {isLoading ? (
        <div className="grid grid-cols-[repeat(auto-fill,150px)] gap-4">
          {Array.from({ length: 24 }).map((_, i) => (
            <Skeleton key={i} className="aspect-square rounded-xl" />
          ))}
        </div>
      ) : isError ? (
        <ErrorState message="Failed to load years" onRetry={refetch} />
      ) : !filtered || filtered.length === 0 ? (
        <EmptyState icon={<CalendarDays size={48} />} title={searchTerm ? "No matching years" : "No years yet"} />
      ) : (
        <DataView
          rows={years}
          rowKey={(year) => year.year}
          preferenceKey="years"
          defaultSort="year"
          defaultDirection="desc"
          empty={<EmptyState icon={<CalendarDays size={48} />} title="No years yet" />}
          renderFilterTile={(controls) => <FilterBar filters={filters} onChange={setFilters} hideYearRange>{controls}</FilterBar>}
          paginateGrid={false}
          columns={[
            { id: "year", label: "Year", width: "minmax(10rem,1fr)", sortValue: (year) => year.year, render: (year) => <button className="hover:text-accent" onClick={() => navigate(`/library?year=${year.year}`)}>{year.year}</button> },
            { id: "decade", label: "Decade", width: "8rem", sortValue: (year) => Math.floor(year.year / 10) * 10, render: (year) => `${Math.floor(year.year / 10) * 10}s` },
            { id: "count", label: "Videos", width: "6rem", align: "right", sortValue: (year) => year.count, render: (year) => year.count },
          ]}
          renderCard={(y) => (
              <RecordStack
                videoIds={y.video_ids}
                label={String(y.year)}
                subLabel={`${y.count} video${y.count !== 1 ? "s" : ""}`}
                onClick={() => navigate(`/library?year=${y.year}`)}
                selected={selectedYears.has(String(y.year))}
                onSelect={(sel) => toggleSelect(String(y.year), sel)}
                onContextAction={handleContextAction}
              />
          )}
          renderGrid={(orderedYears) => (
            <div>
              {groupByDecade(orderedYears).map(({ decade, decadeStart, items, allVideoIds, totalCount }) => (
                <GroupedSection key={decade} heading={decade}>
                  <div className="col-span-2 row-span-2 flex items-center justify-center">
                    <RecordStack
                      videoIds={allVideoIds}
                      label={decade}
                      subLabel={`${totalCount} video${totalCount !== 1 ? "s" : ""}`}
                      onClick={() => navigate(`/library?year_from=${decadeStart}&year_to=${decadeStart + 9}`)}
                      selected={selectedYears.has(decade)}
                      onSelect={(selected) => toggleSelect(decade, selected)}
                      onContextAction={handleContextAction}
                    />
                  </div>
                  {items.map((year) => (
                    <RecordStack
                      key={year.year}
                      videoIds={year.video_ids}
                      label={String(year.year)}
                      subLabel={`${year.count} video${year.count !== 1 ? "s" : ""}`}
                      onClick={() => navigate(`/library?year=${year.year}`)}
                      selected={selectedYears.has(String(year.year))}
                      onSelect={(selected) => toggleSelect(String(year.year), selected)}
                      onContextAction={handleContextAction}
                    />
                  ))}
                </GroupedSection>
              ))}
            </div>
          )}
        />
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
