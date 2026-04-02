import { useState, useCallback, useRef } from "react";
import { Eye, ChevronDown, ChevronUp, Check, Loader2, Image, Upload } from "lucide-react";
import type { AIThumbnailOut, ProcessingState } from "@/types";
import {
  useAIScenes, useAIThumbnails, useAIRunScenes, useAISelectThumbnail,
} from "@/hooks/queries";
import { useToast } from "@/components/Toast";
import { Tooltip } from "@/components/Tooltip";
import { aiApi, playbackApi } from "@/lib/api";
import { useQueryClient } from "@tanstack/react-query";

interface ThumbnailsPanelProps {
  videoId: number;
  processingState?: ProcessingState | null;
}

export function ThumbnailsPanel({ videoId, processingState }: ThumbnailsPanelProps) {
  const { toast } = useToast();
  const qc = useQueryClient();
  const [expanded, setExpandedRaw] = useState(() => localStorage.getItem("thumbnails_expanded") === "true");
  const setExpanded = useCallback((v: boolean) => { localStorage.setItem("thumbnails_expanded", String(v)); setExpandedRaw(v); }, []);
  const [applyToPoster, setApplyToPoster] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const scenes = useAIScenes(videoId);
  const thumbnails = useAIThumbnails(videoId);
  const scenesMutation = useAIRunScenes();
  const selectThumbMutation = useAISelectThumbnail();

  const isStepDone = (step: string) =>
    processingState?.[step]?.completed === true;

  const hasThumbnails = thumbnails.data && thumbnails.data.length > 0;
  const hasScenes = !!scenes.data;

  const handleUpload = useCallback(async (file: File) => {
    if (!file.type.startsWith("image/")) {
      toast({ type: "error", title: "Only image files are allowed" });
      return;
    }
    setUploading(true);
    try {
      await playbackApi.uploadArtwork(videoId, "thumb", file);
      if (applyToPoster) {
        await playbackApi.uploadArtwork(videoId, "poster", file);
      }
      toast({ type: "success", title: applyToPoster ? "Thumbnail & poster updated" : "Thumbnail updated" });
      qc.invalidateQueries({ queryKey: ["video", videoId] });
    } catch {
      toast({ type: "error", title: "Upload failed" });
    } finally {
      setUploading(false);
    }
  }, [videoId, applyToPoster, toast, qc]);

  return (
    <div className="card">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full text-left"
      >
        <Eye size={16} className="text-accent" />
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide flex-1">
          Thumbnails
        </h3>
        {expanded ? (
          <ChevronUp size={14} className="text-text-muted" />
        ) : (
          <ChevronDown size={14} className="text-text-muted" />
        )}
      </button>

      {expanded && (
        <div className="mt-4 space-y-4">
          {/* Analyze Scenes button */}
          <Tooltip content="Detect scene changes and generate thumbnail candidates using ffmpeg. Local processing — no AI tokens used.">
            <button
              className={`btn-sm w-full flex items-center justify-center gap-2 border ${
                isStepDone("scenes_analyzed")
                  ? "btn-ghost text-green-400 border-green-400/30"
                  : "btn-ghost border-border"
              }`}
              disabled={scenesMutation.isPending}
              onClick={() =>
                scenesMutation.mutate(
                  { videoId },
                  {
                    onSuccess: () => toast({ type: "success", title: "Scene analysis started" }),
                    onError: () => toast({ type: "error", title: "Scene analysis failed" }),
                  },
                )
              }
            >
              {scenesMutation.isPending ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Eye size={14} />
              )}
              Analyse Scenes
            </button>
          </Tooltip>

          {/* Thumbnail gallery */}
          {hasThumbnails && (
            <>
              <label className="flex items-center gap-2 text-xs text-text-secondary cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={applyToPoster}
                  onChange={(e) => setApplyToPoster(e.target.checked)}
                  className="rounded border-border accent-accent"
                />
                <Image size={12} className="text-text-muted" />
                Also apply as poster art
              </label>
              <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
                {thumbnails.data!.map((t: AIThumbnailOut) => (
                  <button
                    key={t.id}
                    onClick={() =>
                      selectThumbMutation.mutate(
                        { videoId, thumbnailId: t.id, applyToPoster },
                        {
                          onSuccess: () => toast({
                            type: "success",
                            title: applyToPoster ? "Thumbnail & poster updated" : "Thumbnail selected",
                          }),
                          onError: () => toast({ type: "error", title: "Selection failed" }),
                        },
                      )
                    }
                    disabled={selectThumbMutation.isPending || t.is_selected}
                    className={`relative rounded-lg overflow-hidden border-2 transition-colors ${
                      t.is_selected
                        ? "border-accent ring-1 ring-accent/30"
                        : "border-transparent hover:border-white/20"
                    }`}
                  >
                  <img
                    src={aiApi.thumbnailUrl(videoId, t.id)}
                    alt={`Thumbnail at ${t.timestamp_sec.toFixed(1)}s`}
                    className="w-full aspect-video object-cover"
                    loading="lazy"
                  />
                  <div className="absolute bottom-0 inset-x-0 bg-gradient-to-t from-black/80 to-transparent px-1.5 py-1">
                    <span className="text-[10px] text-white font-mono">
                      {t.timestamp_sec.toFixed(1)}s
                    </span>
                    <span className="text-[10px] text-white/70 ml-1">
                      {(t.score_overall * 100).toFixed(0)}%
                    </span>
                  </div>
                  {t.is_selected && (
                    <div className="absolute top-1 right-1 bg-accent rounded-full p-0.5">
                      <Check size={10} className="text-white" />
                    </div>
                  )}
                </button>
              ))}
              </div>
            </>
          )}

          {/* Scene status */}
          {hasScenes && (
            <div className="text-xs text-text-muted flex items-center gap-2">
              <Eye size={12} />
              <span>
                {scenes.data!.total_scenes} scenes detected
                {scenes.data!.duration_seconds != null && (
                  <> in {scenes.data!.duration_seconds.toFixed(0)}s</>
                )}
              </span>
            </div>
          )}

          {/* Empty state */}
          {!hasThumbnails && !hasScenes && (
            <p className="text-xs text-text-muted italic">
              No scenes analysed yet. Click above to generate thumbnails.
            </p>
          )}

          {/* Upload custom thumbnail */}
          <div
            className={`relative rounded-lg border-2 border-dashed transition-colors cursor-pointer ${
              dragging
                ? "border-accent bg-accent/10"
                : "border-surface-border hover:border-accent/50"
            }`}
            onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); setDragging(true); }}
            onDragLeave={(e) => { e.preventDefault(); e.stopPropagation(); setDragging(false); }}
            onDrop={(e) => {
              e.preventDefault(); e.stopPropagation(); setDragging(false);
              const file = e.dataTransfer.files?.[0];
              if (file) handleUpload(file);
            }}
            onClick={() => fileInputRef.current?.click()}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleUpload(file);
                e.target.value = "";
              }}
            />
            <div className="flex flex-col items-center gap-1.5 py-4 text-text-muted">
              {uploading ? (
                <Loader2 size={16} className="animate-spin text-accent" />
              ) : (
                <Upload size={16} />
              )}
              <span className="text-xs">
                {uploading ? "Uploading..." : "Upload custom thumbnail"}
              </span>
              <span className="text-[10px] text-text-muted/60">Drop image or click to browse</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
