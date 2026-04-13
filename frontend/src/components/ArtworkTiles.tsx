import { useState, useRef, useCallback, useEffect } from "react";
import ReactDOM from "react-dom";
import { Image, RefreshCw, User, Disc3, Music, X, Upload, Trash2, Move } from "lucide-react";
import { Tooltip } from "@/components/Tooltip";
import { useQueryClient } from "@tanstack/react-query";
import type { VideoItemDetail } from "@/types";
import { playbackApi } from "@/lib/api";
import { useToast } from "@/components/Toast";
import { qk } from "@/hooks/queries";

interface ArtworkTilesProps {
  video: VideoItemDetail;
  className?: string;
}

function assetUrl(video: VideoItemDetail, ...types: string[]): string | null {
  const asset = video.media_assets?.find(
    (a) => types.includes(a.asset_type) && (!a.status || a.status === "valid")
  );
  if (!asset) return null;
  // Prefer local file via API (validated on server) over remote source_url
  if (asset.id) {
    const hash = asset.file_hash ? `?h=${asset.file_hash}` : "";
    return `/api/playback/asset/${asset.id}${hash}`;
  }
  if (asset.source_url) return asset.source_url;
  return null;
}

function assetCropPosition(video: VideoItemDetail, ...types: string[]): string | null {
  const asset = video.media_assets?.find(
    (a) => types.includes(a.asset_type) && (!a.status || a.status === "valid")
  );
  return asset?.crop_position || null;
}

interface TileProps {
  label: string;
  icon: React.ReactNode;
  src: string | null;
  fallback?: string | null;
  assetType: string;
  videoId: number;
  cropPosition?: string | null;
  onRefresh?: () => void;
  refreshing?: boolean;
  onDelete?: () => void;
  deleting?: boolean;
  onUploaded: () => void;
}

function ArtworkTile({
  label, icon, src, fallback, assetType, videoId, cropPosition,
  onRefresh, refreshing, onDelete, deleting, onUploaded,
}: TileProps) {
  const [enlarged, setEnlarged] = useState(false);
  const [imgError, setImgError] = useState(false);
  const [fbError, setFbError] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [cropping, setCropping] = useState(false);
  const [cropX, setCropX] = useState(50);
  const [cropY, setCropY] = useState(50);
  const [saving, setSaving] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const cropContainerRef = useRef<HTMLDivElement>(null);
  const { toast } = useToast();
  const qc = useQueryClient();

  // Parse initial crop position from prop
  useEffect(() => {
    if (cropPosition) {
      const parts = cropPosition.split(/\s+/);
      if (parts.length === 2) {
        setCropX(parseInt(parts[0]) || 50);
        setCropY(parseInt(parts[1]) || 50);
      }
    } else {
      setCropX(50);
      setCropY(50);
    }
  }, [cropPosition]);

  // Reset error state when src changes (e.g. after upload or scrape)
  useEffect(() => { setImgError(false); setFbError(false); }, [src]);

  const displaySrc = !imgError ? src : !fbError && fallback ? fallback : null;

  const handleFile = useCallback(
    async (file: File) => {
      if (!file.type.startsWith("image/")) {
        toast({ type: "error", title: "Only image files are allowed" });
        return;
      }
      setUploading(true);
      try {
        await playbackApi.uploadArtwork(videoId, assetType, file);
        toast({ type: "success", title: `${label} artwork updated` });
        setImgError(false);
        setFbError(false);
        onUploaded();
      } catch {
        toast({ type: "error", title: "Upload failed" });
      } finally {
        setUploading(false);
      }
    },
    [videoId, assetType, label, toast, onUploaded]
  );

  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragging(true);
  };
  const onDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragging(false);
  };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  };
  const onFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
    e.target.value = "";
  };

  return (
    <>
      <div
        className={`group relative rounded-lg overflow-hidden border bg-surface-light aspect-square cursor-pointer transition-all duration-200 hover:shadow-lg hover:shadow-accent/8 ${
          dragging
            ? "border-accent border-2 bg-accent/10 shadow-[0_0_20px_rgba(225,29,46,0.2)]"
            : "border-surface-border hover:border-accent/40"
        }`}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type="file"
          accept="image/jpeg,image/png,image/webp"
          className="hidden"
          onChange={onFileSelect}
        />

        {uploading ? (
          <div className="w-full h-full flex flex-col items-center justify-center gap-2 text-accent">
            <RefreshCw size={24} className="animate-spin" />
            <span className="text-[10px] uppercase tracking-wider">Uploading…</span>
          </div>
        ) : dragging ? (
          <div className="w-full h-full flex flex-col items-center justify-center gap-2 text-accent">
            <Upload size={24} />
            <span className="text-[10px] uppercase tracking-wider">Drop image</span>
          </div>
        ) : displaySrc ? (
          <img
            src={displaySrc}
            alt={label}
            className="w-full h-full object-cover"
            style={{ objectPosition: `${cropX}% ${cropY}%` }}
            onError={() => {
              if (!imgError) setImgError(true);
              else setFbError(true);
            }}
            onClick={() => setEnlarged(true)}
            loading="lazy"
          />
        ) : (
          <div
            className="w-full h-full flex flex-col items-center justify-center gap-2 text-text-muted"
            onClick={() => fileInputRef.current?.click()}
          >
            {icon}
            <span className="text-[10px] uppercase tracking-wider">Drop or click</span>
          </div>
        )}

        {/* Hover overlay — pointer-events-none so clicks pass through to img / empty-state */}
        {!dragging && !uploading && (
          <div className="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity flex items-end p-2 pointer-events-none">
            <span className="text-xs font-medium text-white/90 truncate flex-1">{label}</span>
            <Tooltip content="Upload new image">
            <button
              onClick={(e) => {
                e.stopPropagation();
                fileInputRef.current?.click();
              }}
              className="ml-1 p-1 rounded bg-white/20 hover:bg-white/30 text-white transition-colors pointer-events-auto"
            >
              <Upload size={12} />
            </button>
            </Tooltip>
            {onRefresh && (
              <Tooltip content="Refresh artwork from sources">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onRefresh();
                }}
                disabled={refreshing}
                className="ml-1 p-1 rounded bg-white/20 hover:bg-white/30 text-white transition-colors pointer-events-auto"
              >
                <RefreshCw size={12} className={refreshing ? "animate-spin" : ""} />
              </button>
              </Tooltip>
            )}
            {onDelete && displaySrc && (
              <Tooltip content="Delete artwork">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete();
                }}
                disabled={deleting}
                className="ml-1 p-1 rounded bg-red-500/40 hover:bg-red-500/60 text-white transition-colors pointer-events-auto"
              >
                <Trash2 size={12} />
              </button>
              </Tooltip>
            )}
          </div>
        )}
      </div>

      {/* Lightbox */}
      {enlarged && displaySrc && ReactDOM.createPortal(
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm"
          onClick={() => { setEnlarged(false); setCropping(false); }}
        >
          <button
            onClick={() => { setEnlarged(false); setCropping(false); }}
            className="absolute top-4 right-4 p-2 rounded-full bg-white/10 hover:bg-white/20 text-white transition-colors z-10"
          >
            <X size={20} />
          </button>

          {/* Crop mode toggle */}
          <div className="absolute top-4 left-4 flex gap-2 z-10">
            <Tooltip content={cropping ? "Exit crop mode" : "Adjust crop position"}>
              <button
                onClick={(e) => { e.stopPropagation(); setCropping(!cropping); }}
                className={`p-2 rounded-full transition-colors ${
                  cropping ? "bg-accent text-white" : "bg-white/10 hover:bg-white/20 text-white"
                }`}
              >
                <Move size={20} />
              </button>
            </Tooltip>
            {cropping && (
              <>
                <button
                  onClick={async (e) => {
                    e.stopPropagation();
                    setSaving(true);
                    try {
                      await playbackApi.updateArtworkCrop(videoId, assetType, `${cropX}% ${cropY}%`);
                      toast({ type: "success", title: "Crop position saved" });
                      qc.invalidateQueries({ queryKey: qk.video(videoId) });
                    } catch {
                      toast({ type: "error", title: "Failed to save crop" });
                    } finally {
                      setSaving(false);
                    }
                  }}
                  disabled={saving}
                  className="px-3 py-1.5 rounded-full bg-green-500/80 hover:bg-green-500 text-white text-sm font-medium transition-colors"
                >
                  {saving ? "Saving…" : "Save"}
                </button>
                <button
                  onClick={async (e) => {
                    e.stopPropagation();
                    setCropX(50);
                    setCropY(50);
                    setSaving(true);
                    try {
                      await playbackApi.updateArtworkCrop(videoId, assetType, null);
                      toast({ type: "success", title: "Crop position reset" });
                      qc.invalidateQueries({ queryKey: qk.video(videoId) });
                    } catch {
                      toast({ type: "error", title: "Failed to reset crop" });
                    } finally {
                      setSaving(false);
                    }
                  }}
                  className="px-3 py-1.5 rounded-full bg-white/10 hover:bg-white/20 text-white text-sm transition-colors"
                >
                  Reset
                </button>
              </>
            )}
          </div>

          <div className="flex flex-col items-center gap-4" onClick={(e) => e.stopPropagation()}>
            {cropping ? (
              <>
                {/* Crop preview: square container showing how the image will look */}
                <div
                  ref={cropContainerRef}
                  className="relative w-64 h-64 rounded-lg overflow-hidden border-2 border-accent shadow-2xl cursor-crosshair"
                  onClick={(e) => {
                    const rect = cropContainerRef.current?.getBoundingClientRect();
                    if (!rect) return;
                    const x = Math.round(((e.clientX - rect.left) / rect.width) * 100);
                    const y = Math.round(((e.clientY - rect.top) / rect.height) * 100);
                    setCropX(Math.max(0, Math.min(100, x)));
                    setCropY(Math.max(0, Math.min(100, y)));
                  }}
                >
                  <img
                    src={displaySrc}
                    alt={label}
                    className="w-full h-full object-cover"
                    style={{ objectPosition: `${cropX}% ${cropY}%` }}
                    draggable={false}
                  />
                  {/* Crosshair overlay */}
                  <div
                    className="absolute w-3 h-3 border-2 border-white rounded-full shadow-lg pointer-events-none"
                    style={{
                      left: `${cropX}%`,
                      top: `${cropY}%`,
                      transform: "translate(-50%, -50%)",
                      boxShadow: "0 0 0 1px rgba(0,0,0,0.5), 0 0 8px rgba(225,29,46,0.5)",
                    }}
                  />
                </div>
                <p className="text-xs text-white/60">Click to set focal point · {cropX}% {cropY}%</p>
              </>
            ) : (
              <img
                src={displaySrc}
                alt={label}
                className="max-w-[90vw] max-h-[90vh] rounded-lg shadow-2xl object-contain"
              />
            )}
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}

function assetSourceUrl(video: VideoItemDetail, ...types: string[]): string | null {
  const asset = video.media_assets?.find(
    (a) => types.includes(a.asset_type) && (!a.status || a.status === "valid")
  );
  return asset?.source_url || null;
}

export function ArtworkTiles({ video, className }: ArtworkTilesProps) {
  const [deleting, setDeleting] = useState<string | null>(null);
  const qc = useQueryClient();
  const { toast } = useToast();

  const artistSrc = assetUrl(video, "artist_thumb", "artist_image");
  const albumSrc = assetUrl(video, "album_thumb");
  const posterSrc = assetUrl(video, "poster");

  const artistCrop = assetCropPosition(video, "artist_thumb", "artist_image");
  const albumCrop = assetCropPosition(video, "album_thumb");
  const posterCrop = assetCropPosition(video, "poster");

  const artistSourceUrl = assetSourceUrl(video, "artist_thumb", "artist_image");
  const albumSourceUrl = assetSourceUrl(video, "album_thumb");
  const posterSourceUrl = assetSourceUrl(video, "poster");

  const handleUploaded = () => {
    qc.invalidateQueries({ queryKey: qk.video(video.id) });
  };

  const handleDelete = async (assetType: string) => {
    setDeleting(assetType);
    try {
      await playbackApi.deleteArtwork(video.id, assetType);
      toast({ type: "success", title: `${assetType.replace("_", " ")} deleted` });
      qc.invalidateQueries({ queryKey: qk.video(video.id) });
    } catch {
      toast({ type: "error", title: "Delete failed" });
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div className={`card${className ? ` ${className}` : ""}`}>
      <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-3 flex items-center gap-1.5">
        <Image size={14} /> Artwork
      </h3>
      <div className="grid grid-cols-3 gap-3">
        <div className="space-y-1.5">
          <ArtworkTile
            label={video.artist || "Artist"}
            icon={<User size={24} />}
            src={artistSrc}
            assetType="artist_thumb"
            videoId={video.id}
            cropPosition={artistCrop}
            onRefresh={() => qc.invalidateQueries({ queryKey: qk.video(video.id) })}
            onDelete={() => handleDelete("artist_thumb")}
            deleting={deleting === "artist_thumb"}
            onUploaded={handleUploaded}
          />
          {artistSourceUrl ? (
            <a
              href={artistSourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="block text-[10px] text-accent/70 hover:text-accent text-center uppercase tracking-wider transition-colors"
            >
              Artist
            </a>
          ) : (
            <p className="text-[10px] text-text-muted text-center uppercase tracking-wider">
              Artist
            </p>
          )}
        </div>
        <div className="space-y-1.5">
          <ArtworkTile
            label={video.album || "Album"}
            icon={<Disc3 size={24} />}
            src={albumSrc}
            assetType="album_thumb"
            videoId={video.id}
            cropPosition={albumCrop}
            onRefresh={() => qc.invalidateQueries({ queryKey: qk.video(video.id) })}
            onDelete={() => handleDelete("album_thumb")}
            deleting={deleting === "album_thumb"}
            onUploaded={handleUploaded}
          />
          {albumSourceUrl ? (
            <a
              href={albumSourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="block text-[10px] text-accent/70 hover:text-accent text-center uppercase tracking-wider transition-colors"
            >
              Album
            </a>
          ) : (
            <p className="text-[10px] text-text-muted text-center uppercase tracking-wider">
              Album
            </p>
          )}
        </div>
        <div className="space-y-1.5">
          <ArtworkTile
            label={video.title || "Single"}
            icon={<Music size={24} />}
            src={posterSrc}
            assetType="poster"
            videoId={video.id}
            cropPosition={posterCrop}
            onRefresh={() => qc.invalidateQueries({ queryKey: qk.video(video.id) })}
            onDelete={() => handleDelete("poster")}
            deleting={deleting === "poster"}
            onUploaded={handleUploaded}
          />
          {posterSourceUrl ? (
            <a
              href={posterSourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="block text-[10px] text-accent/70 hover:text-accent text-center uppercase tracking-wider transition-colors"
              title="Poster"
            >
              Poster
            </a>
          ) : (
            <p className="text-[10px] text-text-muted text-center uppercase tracking-wider" title="Poster">
              Poster
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
