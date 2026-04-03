import { useMemo, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Users, PartyPopper, ListPlus, Trash2, RefreshCw } from "lucide-react";
import { useArtists, useRescanBatch, useNormalize, useDeleteBatch } from "@/hooks/queries";
import { playbackApi } from "@/lib/api";
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

/** Group artists by first letter (A-Z, with # for non-alpha). */
function groupByLetter(artists: { artist: string; count: number; video_ids: number[] }[]) {
  const groups: Record<string, typeof artists> = {};
  for (const a of artists) {
    const first = (a.artist?.[0] ?? "").toUpperCase();
    const key = /[A-Z]/.test(first) ? first : "#";
    (groups[key] ??= []).push(a);
  }
  // Sort keys: # first, then A-Z
  const sortedKeys = Object.keys(groups).sort((a, b) => {
    if (a === "#") return -1;
    if (b === "#") return 1;
    return a.localeCompare(b);
  });
  return sortedKeys.map((key) => ({ letter: key, items: groups[key] }));
}

export function ArtistsPage() {
  const [filters, setFilters] = useState<FacetFilterParams>({});
  const { data, isLoading, isError, refetch } = useArtists(filters);
  const navigate = useNavigate();
  const { launch: launchParty, isLoading: partyLoading } = usePartyMode();
  const { toast } = useToast();
  const { confirm, dialog } = useConfirm();
  const batchRescanMutation = useRescanBatch();
  const normalizeMutation = useNormalize();
  const batchDeleteMutation = useDeleteBatch();

  // Selection: track selected artist names → video IDs
  const [selectedArtists, setSelectedArtists] = useState<Set<string>>(new Set());
  const [playlistPickerOpen, setPlaylistPickerOpen] = useState(false);
  const [rescanDialogOpen, setRescanDialogOpen] = useState(false);

  const artistMap = useMemo(() => {
    const m = new Map<string, number[]>();
    if (data) for (const a of data) m.set(a.artist, a.video_ids);
    return m;
  }, [data]);

  const selectedVideoIds = useMemo(() => {
    const ids: number[] = [];
    for (const name of selectedArtists) {
      const vids = artistMap.get(name);
      if (vids) ids.push(...vids);
    }
    return ids;
  }, [selectedArtists, artistMap]);

  const toggleSelect = useCallback((artist: string, sel: boolean) => {
    setSelectedArtists((prev) => {
      const next = new Set(prev);
      if (sel) next.add(artist); else next.delete(artist);
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

  const grouped = useMemo(() => (data ? groupByLetter(data) : []), [data]);

  return (
    <div className="p-4 md:p-6">
      <div className="flex items-center gap-3 mb-2">
        <h1 className="text-xl font-bold text-text-primary flex items-center gap-2">
          <Users size={22} /> Artists
        </h1>
        <button
          onClick={() => launchParty(filters)}
          disabled={partyLoading}
          className="btn-sm text-xs font-semibold px-3 py-1.5 rounded-lg bg-gradient-to-r from-pink-500 via-purple-500 to-indigo-500 text-white hover:from-pink-600 hover:via-purple-600 hover:to-indigo-600 transition-all shadow-lg shadow-purple-500/25 flex items-center gap-1.5"
        >
          <PartyPopper size={14} /> Party Mode
        </button>
        {selectedArtists.size > 0 && (
          <>
            <span className="text-xs text-accent">{selectedArtists.size} selected ({selectedVideoIds.length} videos)</span>
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
                  onSuccess: (res) => { toast({ type: "success", title: `Deleted ${res.count} video(s)` }); setSelectedArtists(new Set()); },
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
        <FilterBar filters={filters} onChange={setFilters} hideArtist />
      </div>

      {isLoading ? (
        <div className="grid grid-cols-[repeat(auto-fill,150px)] gap-4">
          {Array.from({ length: 24 }).map((_, i) => (
            <Skeleton key={i} className="aspect-square rounded-xl" />
          ))}
        </div>
      ) : isError ? (
        <ErrorState message="Failed to load artists" onRetry={refetch} />
      ) : !data || data.length === 0 ? (
        <EmptyState icon={<Users size={48} />} title="No artists yet" />
      ) : (
        grouped.map(({ letter, items }) => (
          <GroupedSection key={letter} heading={letter}>
            {items.map((a) => (
              <RecordStack
                key={a.artist}
                videoIds={a.video_ids}
                label={a.artist}
                subLabel={`${a.count} video${a.count !== 1 ? "s" : ""}`}
                onClick={() => navigate(`/library?artist=${encodeURIComponent(a.artist)}`)}
                coverImageUrl={playbackApi.artworkUrl(a.video_ids[0], "artist_thumb")}
                selected={selectedArtists.has(a.artist)}
                onSelect={(sel) => toggleSelect(a.artist, sel)}
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
