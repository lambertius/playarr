import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Pencil, Check, X, ChevronLeft, ChevronRight, Shuffle, Play, ListEnd, ListStart, ListPlus, Music, Loader2 } from "lucide-react";
import {
  useVideo, useSnapshots, useJobs, useUpdateVideo, useVideoNav,
} from "@/hooks/queries";
import { VideoPlayer } from "@/components/VideoPlayer";
import { playbackApi } from "@/lib/api";
import { MetadataEditorForm } from "@/components/MetadataEditorForm";
import { VersionBadge, ReviewStatusBadge } from "@/components/Badges";
import { ArtworkTiles } from "@/components/ArtworkTiles";
import { FilePanel } from "@/components/FilePanel";
import { ActionsPanel } from "@/components/ActionsPanel";
import { ThumbnailsPanel } from "@/components/ThumbnailsPanel";
import { TrackHistory } from "@/components/TrackHistory";
import { CanonicalTrackPanel } from "@/components/CanonicalTrackPanel";
import { AIPanel } from "@/components/AIPanel";
import { ErrorState, Skeleton } from "@/components/Feedback";
import { usePlaybackStore, type PlaybackTrack } from "@/stores/playbackStore";
import { PlaylistPicker } from "@/components/PlaylistPicker";
import { Tooltip } from "@/components/Tooltip";

export function VideoDetailPage() {
  const { videoId } = useParams<{ videoId: string }>();
  const id = Number(videoId);
  const navigate = useNavigate();

  const { data: video, isLoading, isError, refetch } = useVideo(id);
  const { data: snapshots } = useSnapshots(id);
  const { data: jobs } = useJobs({ limit: 20 });

  // Read library sort from localStorage so nav respects the user's chosen order
  const libSort = (() => {
    try {
      return {
        sort_by: localStorage.getItem("playarr:library:sort") ?? "artist",
        sort_dir: localStorage.getItem("playarr:library:dir") ?? "asc",
      };
    } catch { return { sort_by: "artist", sort_dir: "asc" }; }
  })();
  const { data: nav } = useVideoNav(id, libSort);

  const videoJobs = jobs?.filter((j) => j.video_id === id) ?? [];
  const hasUndoable = (snapshots?.length ?? 0) > 0;

  const updateMutation = useUpdateVideo(id);
  const [editingPlot, setEditingPlot] = useState(false);
  const [plotDraft, setPlotDraft] = useState("");
  const [audioDownloading, setAudioDownloading] = useState(false);

  /* ── Loading skeleton ── */
  if (isLoading) {
    return (
      <div className="p-4 md:p-6 max-w-7xl space-y-6">
        {/* Header skeleton */}
        <div className="flex items-center gap-3">
          <Skeleton className="h-9 w-9 rounded-lg" />
          <div className="space-y-2">
            <Skeleton className="h-6 w-72" />
            <Skeleton className="h-4 w-48" />
          </div>
        </div>
        {/* Content skeleton */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 space-y-6">
            <Skeleton className="aspect-video w-full rounded-xl" />
            <Skeleton className="h-40 rounded-xl" />
          </div>
          <div className="space-y-6">
            <Skeleton className="h-56 rounded-xl" />
            <Skeleton className="h-40 rounded-xl" />
            <Skeleton className="h-32 rounded-xl" />
          </div>
        </div>
      </div>
    );
  }

  if (isError || !video) {
    return (
      <div className="p-6">
        <ErrorState message="Video not found" onRetry={refetch} />
      </div>
    );
  }

  const q = video.quality_signature;

  // Derive player poster: prefer video_thumb asset, fall back to poster asset
  const thumbAsset = video.media_assets?.find((a) => a.asset_type === "video_thumb");
  const posterAsset = video.media_assets?.find((a) => a.asset_type === "poster");
  const playerPoster = thumbAsset
    ? `/api/playback/asset/${thumbAsset.id}${thumbAsset.file_hash ? `?h=${thumbAsset.file_hash}` : ""}`
    : posterAsset
      ? `${playbackApi.posterUrl(video.id)}${posterAsset.file_hash ? `?h=${posterAsset.file_hash}` : ""}`
      : undefined;

  return (
    <div className="p-4 md:p-6 max-w-7xl space-y-6 animate-slide-up">
      {/* ═══════════════════════════════════════════════════
          SECTION 1 — Header
         ═══════════════════════════════════════════════════ */}
      <header className="flex items-start gap-3">
        <button
          onClick={() => navigate(-1)}
          className="btn-ghost btn-icon mt-0.5 flex-shrink-0"
          aria-label="Go back"
        >
          <ArrowLeft size={18} />
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="text-xl md:text-2xl font-bold text-text-primary break-words min-w-0 flex-1">
              {video.artist && (
                <span className="text-accent font-semibold">{video.artist}</span>
              )}
              {video.artist && video.title && (
                <span className="text-text-muted mx-2">–</span>
              )}
              <span>{video.title}</span>
              {video.resolution_label && (
                <span className="text-sm font-normal text-text-muted ml-2">[{video.resolution_label}]</span>
              )}
            </h1>
            <div className="flex items-center gap-1 flex-shrink-0">
              <Tooltip content="Previous track">
                <button
                  onClick={() => nav?.prev_id && navigate(`/video/${nav.prev_id}`)}
                  disabled={!nav?.prev_id}
                  className="btn-ghost btn-icon disabled:opacity-30"
                  aria-label="Previous track"
                >
                  <ChevronLeft size={18} />
                </button>
              </Tooltip>
              <Tooltip content="Random track">
                <button
                  onClick={() => nav?.random_id && navigate(`/video/${nav.random_id}`)}
                  disabled={!nav?.random_id}
                  className="btn-ghost btn-icon disabled:opacity-30"
                  aria-label="Random track"
                >
                  <Shuffle size={16} />
                </button>
              </Tooltip>
              <Tooltip content="Next track">
                <button
                  onClick={() => nav?.next_id && navigate(`/video/${nav.next_id}`)}
                  disabled={!nav?.next_id}
                  className="btn-ghost btn-icon disabled:opacity-30"
                  aria-label="Next track"
                >
                  <ChevronRight size={18} />
                </button>
              </Tooltip>

              {/* ── Playback actions ── */}
              <div className="w-px h-5 bg-surface-border mx-1" />
              <PlaybackActions video={video} />

              {/* ── Audio download ── */}
              <div className="w-px h-5 bg-surface-border mx-1" />
              <Tooltip content="Download audio (MP3)">
                <button
                  disabled={audioDownloading}
                  onClick={async () => {
                    setAudioDownloading(true);
                    try {
                      const res = await fetch(`/api/playback/download-audio/${video.id}`);
                      if (!res.ok) throw new Error("Download failed");
                      const blob = await res.blob();
                      const disposition = res.headers.get("content-disposition");
                      const match = disposition?.match(/filename="(.+?)"/);
                      const filename = match?.[1] ?? `${video.artist} - ${video.title}.mp3`;
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement("a");
                      a.href = url;
                      a.download = filename;
                      a.click();
                      URL.revokeObjectURL(url);
                    } catch {
                      // silently fail
                    } finally {
                      setAudioDownloading(false);
                    }
                  }}
                  className="btn-ghost btn-icon"
                  aria-label="Download audio"
                >
                  {audioDownloading ? <Loader2 size={16} className="animate-spin" /> : <Music size={16} />}
                </button>
              </Tooltip>
            </div>
          </div>
          <div className="flex items-center gap-2 mt-1 flex-wrap">
            <VersionBadge versionType={video.version_type} alternateLabel={video.alternate_version_label} />
            <ReviewStatusBadge reviewStatus={video.review_status} />
          </div>
        </div>
      </header>

      {/* ═══════════════════════════════════════════════════
          SECTION 2 — Main Content (two-column)
         ═══════════════════════════════════════════════════ */}
      {/* ═══════════════════════════════════════════════════
          SECTION 2 — Player + Metadata (side by side, tops/bottoms aligned)
         ═══════════════════════════════════════════════════ */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Video Player — fixed 16:9 bounding box; vertical videos get pillarboxed */}
        <div className="lg:col-span-2">
          <div className="rounded-xl bg-black shadow-[0_0_30px_rgba(0,0,0,0.5)] aspect-video flex items-center justify-center">
            <VideoPlayer
              videoId={video.id}
              poster={playerPoster}
              className="w-full h-full object-contain rounded-xl"
              durationSeconds={q?.duration_seconds}
            />
          </div>
        </div>

        {/* Metadata Editor — drives the row height */}
        <div className="lg:col-span-1">
          <div className="card h-full overflow-y-auto">
            <MetadataEditorForm video={video} />
          </div>
        </div>
      </div>

      {/* ═══════════════════════════════════════════════════
          SECTION 2b — Description | Canonical Track
         ═══════════════════════════════════════════════════ */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Description */}
        <div className="lg:col-span-2">
          <div className="card h-full">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide">
                Description
              </h3>
              {!editingPlot ? (
                <button
                  className="btn-ghost btn-icon"
                  aria-label="Edit description"
                  onClick={() => { setPlotDraft(video.plot ?? ""); setEditingPlot(true); }}
                >
                  <Pencil size={14} />
                </button>
              ) : (
                <div className="flex gap-1">
                  <button
                    className="btn-ghost btn-icon text-green-400"
                    aria-label="Save description"
                    onClick={() => {
                      updateMutation.mutate({ plot: plotDraft }, { onSuccess: () => setEditingPlot(false) });
                    }}
                  >
                    <Check size={14} />
                  </button>
                  <button
                    className="btn-ghost btn-icon text-red-400"
                    aria-label="Cancel editing"
                    onClick={() => setEditingPlot(false)}
                  >
                    <X size={14} />
                  </button>
                </div>
              )}
            </div>
            {editingPlot ? (
              <textarea
                className="w-full bg-surface-hover border border-border rounded-lg p-2 text-sm text-text-primary resize-y min-h-[80px]"
                value={plotDraft}
                onChange={(e) => setPlotDraft(e.target.value)}
                autoFocus
              />
            ) : (
              <p className="text-sm text-text-primary leading-relaxed whitespace-pre-line break-words">
                {video.plot || <span className="text-text-muted italic">No description</span>}
              </p>
            )}
          </div>
        </div>

        {/* Right: Canonical Track — stretches to match description height */}
        <div className="lg:col-span-1 flex flex-col">
          <CanonicalTrackPanel
            track={video.canonical_track ?? null}
            videoId={video.id}
            parentVideoId={video.parent_video_id}
            canonicalConfidence={video.canonical_confidence}
            canonicalProvenance={video.canonical_provenance}
            className="flex-1"
          />
        </div>
      </div>

      {/* ═══════════════════════════════════════════════════
          SECTION 2c — Artwork | Actions + File
         ═══════════════════════════════════════════════════ */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Artwork Tiles */}
        <div className="lg:col-span-2">
          <ArtworkTiles video={video} className="h-full" />
        </div>

        {/* Right: Actions — stretch to match artwork height */}
        <div className="lg:col-span-1">
          <ActionsPanel
            className="h-full"
            videoId={video.id}
            hasUndoable={hasUndoable}
            quality={q}
            onDeleted={() => navigate("/library")}
            filePath={video.file_path}
            artist={video.artist}
            title={video.title}
            resolutionLabel={video.resolution_label}
            processingState={video.processing_state}
            versionType={video.version_type}
            alternateVersionLabel={video.alternate_version_label}
            isLocked={video.locked_fields?.includes("_all") ?? false}
            hasArchive={video.has_archive ?? false}
            excludeFromEditorScan={video.exclude_from_editor_scan ?? false}
          />
        </div>
      </div>

      {/* ═══════════════════════════════════════════════════
          SECTION 2d — AI Review Panel
         ═══════════════════════════════════════════════════ */}
      <AIPanel videoId={video.id} />

      {/* ═══════════════════════════════════════════════════
          SECTION 3 — Thumbnails + Track History | File Details
         ═══════════════════════════════════════════════════ */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Thumbnails + Track History stacked */}
        <div className="lg:col-span-2 flex flex-col gap-6">
          <ThumbnailsPanel
            videoId={video.id}
            processingState={video.processing_state}
          />
          <TrackHistory
            jobs={videoJobs}
            snapshots={snapshots}
            processingState={video.processing_state}
          />
        </div>

        {/* Right: File details */}
        <div className="lg:col-span-1">
          <FilePanel video={video} />
        </div>
      </div>


    </div>
  );
}

/** Small inline playback action buttons for the detail header */
function PlaybackActions({ video }: { video: { id: number; artist: string; title: string; media_assets: { asset_type: string }[]; quality_signature?: { duration_seconds?: number | null } | null } }) {
  const [pickerOpen, setPickerOpen] = useState(false);

  const hasPoster = video.media_assets?.some((a) => a.asset_type === "poster") ?? false;

  const track: PlaybackTrack = {
    videoId: video.id,
    artist: video.artist,
    title: video.title,
    hasPoster: hasPoster,
    duration: video.quality_signature?.duration_seconds ?? undefined,
  };

  const store = usePlaybackStore;

  return (
    <>
      <Tooltip content="Play audio">
        <button
          onClick={() => {
            const s = store.getState();
            if (s.queue.length > 0 && s.isPlaying) {
              s.playIndividual(track);
            } else {
              s.play(track);
            }
          }}
          className="btn-ghost btn-icon"
        >
          <Play size={15} />
        </button>
      </Tooltip>
      <Tooltip content="Play next">
        <button
          onClick={() => store.getState().playNext(track)}
          className="btn-ghost btn-icon"
        >
          <ListStart size={15} />
        </button>
      </Tooltip>
      <Tooltip content="Add to queue">
        <button
          onClick={() => store.getState().addToQueue(track)}
          className="btn-ghost btn-icon"
        >
          <ListEnd size={15} />
        </button>
      </Tooltip>
      <Tooltip content="Add to playlist">
        <button
          onClick={() => setPickerOpen(true)}
          className="btn-ghost btn-icon"
        >
          <ListPlus size={15} />
        </button>
      </Tooltip>

      <PlaylistPicker
        open={pickerOpen}
        videoIds={[video.id]}
        onClose={() => setPickerOpen(false)}
      />
    </>
  );
}
