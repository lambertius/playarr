import { useMemo, useState, useCallback } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Disc3, PartyPopper, ListPlus, Trash2, RefreshCw } from "lucide-react";
import { useAlbums, useRescanBatch, useNormalize, useDeleteBatch } from "@/hooks/queries";
import { playbackApi } from "@/lib/api";
import { EmptyState, ErrorState, Skeleton } from "@/components/Feedback";
import { RecordStack } from "@/components/RecordStack";
import { DataView } from "@/components/DataView";
import { FilterBar } from "@/components/FilterBar";
import { PlaylistPicker } from "@/components/PlaylistPicker";
import { RescanOptionsDialog } from "@/components/RescanOptionsDialog";
import type { RescanOptions } from "@/components/RescanOptionsDialog";
import { useToast } from "@/components/Toast";
import { useConfirm } from "@/components/ConfirmDialog";
import type { FacetFilterParams, AlbumBucket } from "@/types";
import { usePartyMode } from "@/hooks/usePartyMode";

/** Group albums alphabetically by first letter. */
function groupByLetter(albums: AlbumBucket[]) {
  const named = albums.filter((a) => a.album);
  const sorted = [...named].sort((a, b) => a.album!.localeCompare(b.album!));
  const groups: Record<string, typeof named> = {};
  for (const a of sorted) {
    const first = (a.album?.[0] ?? "").toUpperCase();
    const key = /[A-Z]/.test(first) ? first : "#";
    (groups[key] ??= []).push(a);
  }
  const sortedKeys = Object.keys(groups).sort((a, b) => {
    if (a === "#") return -1;
    if (b === "#") return 1;
    return a.localeCompare(b);
  });
  return sortedKeys.map((key) => ({ letter: key, items: groups[key] }));
}

export function AlbumsPage() {
  const [filters, setFilters] = useState<FacetFilterParams>({});
  const [searchParams] = useSearchParams();
  const searchTerm = searchParams.get("search") ?? "";
  const mergedFilters = useMemo(() => (searchTerm ? { ...filters, search: searchTerm } : filters), [filters, searchTerm]);
  const { data, isLoading, isError, refetch } = useAlbums(mergedFilters);
  const navigate = useNavigate();
  const { launch: launchParty, isLoading: partyLoading } = usePartyMode();
  const { toast } = useToast();
  const { confirm, dialog } = useConfirm();
  const batchRescanMutation = useRescanBatch();
  const normalizeMutation = useNormalize();
  const batchDeleteMutation = useDeleteBatch();

  const [selectedAlbums, setSelectedAlbums] = useState<Set<string>>(new Set());
  const [playlistPickerOpen, setPlaylistPickerOpen] = useState(false);
  const [rescanDialogOpen, setRescanDialogOpen] = useState(false);

  /** Unique key for each album bucket — uses entity ID when available. */
  const albumKey = useCallback((a: AlbumBucket) =>
    a.album_entity_id ? `entity-${a.album_entity_id}` : a.album ?? "", []);

  const albumMap = useMemo(() => {
    const m = new Map<string, number[]>();
    if (data) for (const a of data) if (a.album) m.set(albumKey(a), a.video_ids);
    return m;
  }, [data, albumKey]);

  const selectedVideoIds = useMemo(() => {
    const ids: number[] = [];
    for (const name of selectedAlbums) {
      const vids = albumMap.get(name);
      if (vids) ids.push(...vids);
    }
    return ids;
  }, [selectedAlbums, albumMap]);

  const toggleSelect = useCallback((album: string, sel: boolean) => {
    setSelectedAlbums((prev) => {
      const next = new Set(prev);
      if (sel) next.add(album); else next.delete(album);
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

  // Client-side filter: when searching, only show stacks whose album or artist name matches
  const filtered = useMemo(() => {
    if (!data || !searchTerm) return data ?? [];
    const term = searchTerm.toLowerCase();
    return data.filter((a) => a.album?.toLowerCase().includes(term) || a.artist?.toLowerCase().includes(term));
  }, [data, searchTerm]);

  const grouped = useMemo(() => (filtered.length ? groupByLetter(filtered) : []), [filtered]);
  const ordered = useMemo(() => grouped.flatMap((group) => group.items), [grouped]);

  return (
    <div className="p-4 md:p-6">
      <div className="flex items-center gap-3 mb-2">
        <h1 className="text-xl font-bold text-text-primary flex items-center gap-2">
          <Disc3 size={22} /> Albums
        </h1>
        <button
          onClick={() => launchParty(mergedFilters)}
          disabled={partyLoading}
          className="btn-sm text-xs font-semibold px-3 py-1.5 rounded-lg bg-gradient-to-r from-pink-500 via-purple-500 to-indigo-500 text-white hover:from-pink-600 hover:via-purple-600 hover:to-indigo-600 transition-all shadow-lg shadow-purple-500/25 flex items-center gap-1.5"
        >
          <PartyPopper size={14} /> Party Mode
        </button>
        {selectedAlbums.size > 0 && (
          <>
            <span className="text-xs text-accent">{selectedAlbums.size} selected ({selectedVideoIds.length} videos)</span>
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
                  onSuccess: (res) => { toast({ type: "success", title: `Deleted ${res.count} video(s)` }); setSelectedAlbums(new Set()); },
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
      {(isLoading || isError || !filtered || filtered.filter((album) => album.album).length === 0) && (
        <FilterBar filters={filters} onChange={setFilters} />
      )}

      {isLoading ? (
        <div className="grid grid-cols-[repeat(auto-fill,150px)] gap-4">
          {Array.from({ length: 24 }).map((_, i) => (
            <Skeleton key={i} className="aspect-square rounded-xl" />
          ))}
        </div>
      ) : isError ? (
        <ErrorState message="Failed to load albums" onRetry={refetch} />
      ) : !filtered || filtered.filter((a) => a.album).length === 0 ? (
        <EmptyState icon={<Disc3 size={48} />} title={searchTerm ? "No matching albums" : "No albums yet"} />
      ) : (
        <DataView
          rows={ordered}
          rowKey={albumKey}
          preferenceKey="albums"
          defaultSort="name"
          empty={<EmptyState icon={<Disc3 size={48} />} title="No albums yet" />}
          renderFilterTile={(controls) => <FilterBar filters={filters} onChange={setFilters}>{controls}</FilterBar>}
          columns={[
            { id: "name", label: "Album", width: "minmax(12rem,1fr)", sortValue: (album) => album.album ?? "", render: (album) => <button className="truncate hover:text-accent" onClick={() => navigate(album.album_entity_id ? `/library?album_entity_id=${album.album_entity_id}&album=${encodeURIComponent(album.album!)}` : `/library?album=${encodeURIComponent(album.album!)}`)}>{album.album}</button> },
            { id: "artist", label: "Artist", width: "minmax(10rem,1fr)", sortValue: (album) => album.artist ?? "", render: (album) => album.artist || "—" },
            { id: "count", label: "Videos", width: "6rem", align: "right", sortValue: (album) => album.count, render: (album) => album.count },
            { id: "artwork", label: "Artwork", width: "7rem", render: (album) => <img src={playbackApi.artworkUrl(album.video_ids[0], "album_thumb")} alt="" className="h-9 w-16 rounded object-cover" /> },
          ]}
          renderCard={(a) => (
              <RecordStack
                key={a.album_entity_id ? `entity-${a.album_entity_id}` : a.album!}
                videoIds={a.video_ids}
                label={a.album!}
                subLabel={a.artist ? `${a.artist} · ${a.count} video${a.count !== 1 ? "s" : ""}` : `${a.count} video${a.count !== 1 ? "s" : ""}`}
                onClick={() => {
                  if (a.album_entity_id) {
                    navigate(`/library?album_entity_id=${a.album_entity_id}&album=${encodeURIComponent(a.album!)}`);
                  } else {
                    navigate(`/library?album=${encodeURIComponent(a.album!)}`);
                  }
                }}
                coverImageUrl={playbackApi.artworkUrl(a.video_ids[0], "album_thumb")}
                selected={selectedAlbums.has(albumKey(a))}
                onSelect={(sel) => toggleSelect(albumKey(a), sel)}
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
