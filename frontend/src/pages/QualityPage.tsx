import { useMemo, useState, useCallback } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { MonitorPlay, PartyPopper, ListPlus, Trash2, RefreshCw } from "lucide-react";
import { useQualityBuckets, useRescanBatch, useNormalize, useDeleteBatch } from "@/hooks/queries";
import { EmptyState, ErrorState, Skeleton } from "@/components/Feedback";
import { RecordStack } from "@/components/RecordStack";
import { DataView } from "@/components/DataView";
import { FilterBar } from "@/components/FilterBar";
import { PlaylistPicker } from "@/components/PlaylistPicker";
import { RescanOptionsDialog } from "@/components/RescanOptionsDialog";
import type { RescanOptions } from "@/components/RescanOptionsDialog";
import { useToast } from "@/components/Toast";
import { useConfirm } from "@/components/ConfirmDialog";
import type { FacetFilterParams, QualityBucket } from "@/types";
import { usePartyMode } from "@/hooks/usePartyMode";

/** Ordered quality tiers — low to high. */
const QUALITY_ORDER = ["360p", "480p", "720p", "1080p", "2K", "4K"];

/** Group quality buckets into SD (≤480p) and HD (720p+). */
interface QualityGroup {
  label: string;
  items: QualityBucket[];
  allVideoIds: number[];
  totalCount: number;
}

function groupByTier(buckets: QualityBucket[]): QualityGroup[] {
  const sd: QualityBucket[] = [];
  const hd: QualityBucket[] = [];

  // Sort buckets into defined order
  const ordered = [...buckets].sort(
    (a, b) => QUALITY_ORDER.indexOf(a.quality) - QUALITY_ORDER.indexOf(b.quality),
  );

  for (const b of ordered) {
    if (b.quality === "360p" || b.quality === "480p") sd.push(b);
    else hd.push(b);
  }

  const groups: QualityGroup[] = [];
  if (sd.length > 0) {
    groups.push({
      label: "SD",
      items: sd,
      allVideoIds: sd.flatMap((b) => b.video_ids),
      totalCount: sd.reduce((s, b) => s + b.count, 0),
    });
  }
  if (hd.length > 0) {
    groups.push({
      label: "HD",
      items: hd,
      allVideoIds: hd.flatMap((b) => b.video_ids),
      totalCount: hd.reduce((s, b) => s + b.count, 0),
    });
  }
  return groups;
}

export function QualityPage() {
  const [filters, setFilters] = useState<FacetFilterParams>({});
  const [searchParams] = useSearchParams();
  const searchTerm = searchParams.get("search") ?? "";
  const mergedFilters = useMemo(() => (searchTerm ? { ...filters, search: searchTerm } : filters), [filters, searchTerm]);
  const { data, isLoading, isError, refetch } = useQualityBuckets(mergedFilters);
  const navigate = useNavigate();
  const { launch: launchParty, isLoading: partyLoading } = usePartyMode();
  const { toast } = useToast();
  const { confirm, dialog } = useConfirm();
  const batchRescanMutation = useRescanBatch();
  const normalizeMutation = useNormalize();
  const batchDeleteMutation = useDeleteBatch();

  const [selectedQualities, setSelectedQualities] = useState<Set<string>>(new Set());
  const [playlistPickerOpen, setPlaylistPickerOpen] = useState(false);
  const [rescanDialogOpen, setRescanDialogOpen] = useState(false);

  const qualityMap = useMemo(() => {
    const m = new Map<string, number[]>();
    if (data) for (const b of data) m.set(b.quality, b.video_ids);
    return m;
  }, [data]);

  const selectedVideoIds = useMemo(() => {
    const ids: number[] = [];
    for (const key of selectedQualities) {
      const vids = qualityMap.get(key);
      if (vids) ids.push(...vids);
    }
    return ids;
  }, [selectedQualities, qualityMap]);

  const toggleSelect = useCallback((key: string, sel: boolean) => {
    setSelectedQualities((prev) => {
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

  const grouped = useMemo(() => (data ? groupByTier(data) : []), [data]);
  const ordered = useMemo(() => grouped.flatMap((group) => group.items), [grouped]);

  return (
    <div className="p-4 md:p-6">
      <div className="flex items-center gap-3 mb-2">
        <h1 className="text-xl font-bold text-text-primary flex items-center gap-2">
          <MonitorPlay size={22} /> Quality
        </h1>
        <button
          onClick={() => launchParty(mergedFilters)}
          disabled={partyLoading}
          className="btn-sm text-xs font-semibold px-3 py-1.5 rounded-lg bg-gradient-to-r from-pink-500 via-purple-500 to-indigo-500 text-white hover:from-pink-600 hover:via-purple-600 hover:to-indigo-600 transition-all shadow-lg shadow-purple-500/25 flex items-center gap-1.5"
        >
          <PartyPopper size={14} /> Party Mode
        </button>
        {selectedQualities.size > 0 && (
          <>
            <span className="text-xs text-accent">{selectedQualities.size} selected ({selectedVideoIds.length} videos)</span>
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
                  onSuccess: (res) => { toast({ type: "success", title: `Deleted ${res.count} video(s)` }); setSelectedQualities(new Set()); },
                });
              }}
              className="btn-danger btn-sm"
            >
              <Trash2 size={14} /> Delete
            </button>
          </>
        )}
      </div>
      {(isLoading || isError || !data || data.length === 0) && (
        <FilterBar filters={filters} onChange={setFilters} hideQuality />
      )}

      {isLoading ? (
        <div className="grid grid-cols-[repeat(auto-fill,150px)] gap-4">
          {Array.from({ length: 12 }).map((_, i) => (
            <Skeleton key={i} className="aspect-square rounded-xl" />
          ))}
        </div>
      ) : isError ? (
        <ErrorState message="Failed to load quality data" onRetry={refetch} />
      ) : !data || data.length === 0 ? (
        <EmptyState icon={<MonitorPlay size={48} />} title="No quality data yet" />
      ) : (
        <DataView
          rows={ordered}
          rowKey={(quality) => quality.quality}
          preferenceKey="quality"
          defaultSort="rank"
          defaultDirection="desc"
          empty={<EmptyState icon={<MonitorPlay size={48} />} title="No quality data yet" />}
          renderFilterTile={(controls) => <FilterBar filters={filters} onChange={setFilters} hideQuality>{controls}</FilterBar>}
          columns={[
            { id: "rank", label: "Quality", width: "minmax(10rem,1fr)", sortValue: (quality) => QUALITY_ORDER.indexOf(quality.quality), render: (quality) => <button className="hover:text-accent" onClick={() => navigate(`/library?quality=${quality.quality}`)}>{quality.quality}</button> },
            { id: "tier", label: "Tier", width: "7rem", sortValue: (quality) => QUALITY_ORDER.indexOf(quality.quality) <= 1 ? "SD" : "HD", render: (quality) => QUALITY_ORDER.indexOf(quality.quality) <= 1 ? "SD" : "HD" },
            { id: "count", label: "Videos", width: "6rem", align: "right", sortValue: (quality) => quality.count, render: (quality) => quality.count },
          ]}
          renderCard={(b) => (
              <RecordStack
                videoIds={b.video_ids}
                label={b.quality}
                subLabel={`${b.count} video${b.count !== 1 ? "s" : ""}`}
                onClick={() => navigate(`/library?quality=${b.quality}`)}
                selected={selectedQualities.has(b.quality)}
                onSelect={(sel) => toggleSelect(b.quality, sel)}
                onContextAction={handleContextAction}
              />
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
          }, {
            onSuccess: () => {
              toast({ type: "success", title: `Rescan queued for ${selectedVideoIds.length} video(s)` });
              setRescanDialogOpen(false);
            },
          });
        }}
      />
    </div>
  );
}
