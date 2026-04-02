import { useMemo, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Star, Film, Music, PartyPopper, ListPlus, Trash2, RefreshCw } from "lucide-react";
import { useSongRatings, useVideoRatings, useRescanBatch, useNormalize, useDeleteBatch } from "@/hooks/queries";
import { playbackApi } from "@/lib/api";
import { EmptyState, ErrorState, Skeleton } from "@/components/Feedback";
import { RecordStack } from "@/components/RecordStack";
import { FilterBar } from "@/components/FilterBar";
import { PlaylistPicker } from "@/components/PlaylistPicker";
import { RescanOptionsDialog } from "@/components/RescanOptionsDialog";
import type { RescanOptions } from "@/components/RescanOptionsDialog";
import { useToast } from "@/components/Toast";
import { useConfirm } from "@/components/ConfirmDialog";
import type { RatingBucket, FacetFilterParams } from "@/types";
import { usePartyMode } from "@/hooks/usePartyMode";

type SortDir = "asc" | "desc";

function StarsDisplay({ rating }: { rating: number }) {
  return (
    <span className="inline-flex gap-0.5">
      {[1, 2, 3, 4, 5].map((star) => (
        <Star
          key={star}
          size={16}
          className={star <= rating ? "text-accent" : "text-text-muted/50"}
          fill={star <= rating ? "currentColor" : "none"}
        />
      ))}
    </span>
  );
}

function RatingColumn({
  title,
  icon,
  data,
  isLoading,
  isError,
  refetch,
  filterKey,
  sortDir,
  selectedRatings,
  onToggleSelect,
  onContextAction,
}: {
  title: string;
  icon: React.ReactNode;
  data: RatingBucket[] | undefined;
  isLoading: boolean;
  isError: boolean;
  refetch: () => void;
  filterKey: "song_rating" | "video_rating";
  sortDir: SortDir;
  selectedRatings: Set<string>;
  onToggleSelect: (key: string, sel: boolean) => void;
  onContextAction: (action: string, videoIds: number[]) => void;
}) {
  const navigate = useNavigate();

  const sorted = useMemo(
    () =>
      data?.slice().sort((a, b) =>
        sortDir === "desc" ? b.rating - a.rating : a.rating - b.rating,
      ),
    [data, sortDir],
  );

  return (
    <div className="flex-1 min-w-0">
      <h2 className="text-2xl font-bold text-text-primary flex items-center gap-2 mb-6">
        {icon} {title}
      </h2>

      {isLoading ? (
        <div className="flex flex-col items-center gap-6">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="w-60 aspect-square rounded-xl" />
          ))}
        </div>
      ) : isError ? (
        <ErrorState message={`Failed to load ${title.toLowerCase()}`} onRetry={refetch} />
      ) : !sorted || sorted.length === 0 ? (
        <EmptyState icon={<Star size={36} />} title={`No ${title.toLowerCase()} yet`} />
      ) : (
        <div className="flex flex-col items-center gap-6">
          {sorted.map((bucket) => {
            const ratingKey = `${filterKey}-${bucket.rating}`;
            return (
              <div key={bucket.rating} className="w-60 flex flex-col items-center gap-1">
                <RecordStack
                  videoIds={bucket.video_ids}
                  label={`${bucket.count} video${bucket.count !== 1 ? "s" : ""}`}
                  onClick={() => navigate(`/library?${filterKey}=${bucket.rating}`)}
                  coverImageUrl={playbackApi.artworkUrl(bucket.video_ids[0], "poster_thumb")}
                  selected={selectedRatings.has(ratingKey)}
                  onSelect={(sel) => onToggleSelect(ratingKey, sel)}
                  onContextAction={onContextAction}
                />
                <StarsDisplay rating={bucket.rating} />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function RatingsPage() {
  const [filters, setFilters] = useState<FacetFilterParams>({});
  const songRatings = useSongRatings(filters);
  const videoRatings = useVideoRatings(filters);
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const { launch: launchParty, isLoading: partyLoading } = usePartyMode();
  const navigate = useNavigate();
  const { toast } = useToast();
  const { confirm, dialog } = useConfirm();
  const batchRescanMutation = useRescanBatch();
  const normalizeMutation = useNormalize();
  const batchDeleteMutation = useDeleteBatch();

  const [selectedRatings, setSelectedRatings] = useState<Set<string>>(new Set());
  const [playlistPickerOpen, setPlaylistPickerOpen] = useState(false);
  const [rescanDialogOpen, setRescanDialogOpen] = useState(false);

  // Build rating key → video IDs map from both columns
  const ratingVideoMap = useMemo(() => {
    const m = new Map<string, number[]>();
    if (videoRatings.data) for (const b of videoRatings.data) m.set(`video_rating-${b.rating}`, b.video_ids);
    if (songRatings.data) for (const b of songRatings.data) m.set(`song_rating-${b.rating}`, b.video_ids);
    return m;
  }, [videoRatings.data, songRatings.data]);

  const selectedVideoIds = useMemo(() => {
    const ids: number[] = [];
    for (const key of selectedRatings) {
      const vids = ratingVideoMap.get(key);
      if (vids) ids.push(...vids);
    }
    return [...new Set(ids)]; // deduplicate
  }, [selectedRatings, ratingVideoMap]);

  const toggleSelect = useCallback((key: string, sel: boolean) => {
    setSelectedRatings((prev) => {
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

  return (
    <div className="p-4 md:p-6">
      <div className="flex items-center gap-3 mb-2">
        <h1 className="text-xl font-bold text-text-primary flex items-center gap-2">
          <Star size={22} /> Ratings
        </h1>
        <button
          onClick={() => setSortDir((d) => (d === "desc" ? "asc" : "desc"))}
          className="btn-ghost btn-sm text-xs"
          aria-label={`Sort ${sortDir === "desc" ? "low to high" : "high to low"}`}
        >
          {sortDir === "desc" ? "High→Low" : "Low→High"}
        </button>
        <button
          onClick={() => launchParty(filters)}
          disabled={partyLoading}
          className="btn-sm text-xs font-semibold px-3 py-1.5 rounded-lg bg-gradient-to-r from-pink-500 via-purple-500 to-indigo-500 text-white hover:from-pink-600 hover:via-purple-600 hover:to-indigo-600 transition-all shadow-lg shadow-purple-500/25 flex items-center gap-1.5"
        >
          <PartyPopper size={14} /> Party Mode
        </button>
        {selectedRatings.size > 0 && (
          <>
            <span className="text-xs text-accent">{selectedRatings.size} selected ({selectedVideoIds.length} videos)</span>
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
                  onSuccess: (res) => { toast({ type: "success", title: `Deleted ${res.count} video(s)` }); setSelectedRatings(new Set()); },
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
        <FilterBar filters={filters} onChange={setFilters} hideRatings />
      </div>

      <div className="flex flex-col md:flex-row gap-0">
        <RatingColumn
          title="Video Ratings"
          icon={<Film size={24} />}
          data={videoRatings.data}
          isLoading={videoRatings.isLoading}
          isError={videoRatings.isError}
          refetch={videoRatings.refetch}
          filterKey="video_rating"
          sortDir={sortDir}
          selectedRatings={selectedRatings}
          onToggleSelect={toggleSelect}
          onContextAction={handleContextAction}
        />

        {/* Red vertical divider */}
        <div className="hidden md:block w-px bg-red-500 mx-6 self-stretch" />
        <div className="md:hidden h-px bg-red-500 my-6" />

        <RatingColumn
          title="Music Ratings"
          icon={<Music size={24} />}
          data={songRatings.data}
          isLoading={songRatings.isLoading}
          isError={songRatings.isError}
          refetch={songRatings.refetch}
          filterKey="song_rating"
          sortDir={sortDir}
          selectedRatings={selectedRatings}
          onToggleSelect={toggleSelect}
          onContextAction={handleContextAction}
        />
      </div>

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
          }, {
            onSuccess: () => { setRescanDialogOpen(false); toast({ type: "success", title: `Rescan queued for ${selectedVideoIds.length} video(s)` }); },
          });
        }}
      />
    </div>
  );
}
