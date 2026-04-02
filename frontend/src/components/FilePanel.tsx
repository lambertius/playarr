import { HardDrive, Clock, FolderOpen, Monitor, Headphones, Film } from "lucide-react";
import type { VideoItemDetail } from "@/types";
import { formatBytes, formatDuration } from "@/lib/utils";

interface FilePanelProps {
  video: VideoItemDetail;
}

export function FilePanel({ video }: FilePanelProps) {
  const q = video.quality_signature;

  const videoRows: [string, string | null][] = q
    ? [
        ["Resolution", q.width && q.height ? `${q.width}×${q.height}` : null],
        ["FPS", q.fps ? q.fps.toFixed(1) : null],
        ["Video Codec", q.video_codec ?? null],
        ["Video Bitrate", q.video_bitrate ? `${(q.video_bitrate / 1000).toFixed(0)} kbps` : null],
        ["HDR", q.hdr ? "Yes" : "No"],
      ]
    : [];

  const audioRows: [string, string | null][] = q
    ? [
        ["Audio Codec", q.audio_codec ?? null],
        ["Audio Bitrate", q.audio_bitrate ? `${q.audio_bitrate} kbps` : null],
        ["Channels", q.audio_channels?.toString() ?? null],
        ["Sample Rate", q.audio_sample_rate ? `${q.audio_sample_rate} Hz` : null],
        ["Loudness", q.loudness_lufs != null ? `${q.loudness_lufs.toFixed(1)} LUFS` : null],
      ]
    : [];

  const containerValue = q?.container ?? null;

  return (
    <div className="card space-y-4">
      {/* Video section */}
      {videoRows.some(([, v]) => v) && (
        <div>
          <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-2 flex items-center gap-1.5">
            <Monitor size={14} /> Video
          </h3>
          <dl className="space-y-1.5 text-sm">
            {videoRows.map(([label, value]) =>
              value ? (
                <div key={label} className="flex justify-between items-center">
                  <dt className="text-text-muted">{label}</dt>
                  <dd className="text-text-primary font-mono text-xs" title={value}>
                    {value}
                  </dd>
                </div>
              ) : null
            )}
          </dl>
        </div>
      )}

      {/* Audio section */}
      {audioRows.some(([, v]) => v) && (
        <div>
          <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-2 flex items-center gap-1.5">
            <Headphones size={14} /> Audio
          </h3>
          <dl className="space-y-1.5 text-sm">
            {audioRows.map(([label, value]) =>
              value ? (
                <div key={label} className="flex justify-between items-center">
                  <dt className="text-text-muted">{label}</dt>
                  <dd className="text-text-primary font-mono text-xs" title={value}>
                    {value}
                  </dd>
                </div>
              ) : null
            )}
          </dl>
        </div>
      )}

      {/* Container */}
      {containerValue && (
        <div>
          <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-2 flex items-center gap-1.5">
            <Film size={14} /> Container
          </h3>
          <dl className="text-sm">
            <div className="flex justify-between items-center">
              <dt className="text-text-muted">Container</dt>
              <dd className="text-text-primary font-mono text-xs">{containerValue}</dd>
            </div>
          </dl>
        </div>
      )}

      {/* File info */}
      <div>
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-2 flex items-center gap-1.5">
          <HardDrive size={14} /> File
        </h3>
        <dl className="space-y-1.5 text-sm">
          <div className="flex justify-between items-center">
            <dt className="text-text-muted">Size</dt>
            <dd className="text-text-primary font-mono text-xs">
              {formatBytes(video.file_size_bytes)}
            </dd>
          </div>
          <div className="flex justify-between items-center">
            <dt className="text-text-muted">Duration</dt>
            <dd className="text-text-primary font-mono text-xs">
              {formatDuration(q?.duration_seconds)}
            </dd>
          </div>
          {video.file_path && (
            <div className="pt-1">
              <dt className="text-text-muted mb-0.5 flex items-center gap-1">
                <FolderOpen size={12} /> Path
              </dt>
              <dd
                className="text-[11px] text-text-muted font-mono break-all leading-relaxed hover:text-text-secondary transition-colors"
                title={video.file_path}
              >
                {video.file_path}
              </dd>
            </div>
          )}
        </dl>
      </div>

      {/* Timestamps */}
      <div>
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-2 flex items-center gap-1.5">
          <Clock size={14} /> Timestamps
        </h3>
        <dl className="space-y-1.5 text-sm">
          <div className="flex justify-between items-center">
            <dt className="text-text-muted">Added</dt>
            <dd className="text-text-primary text-xs" title={new Date(video.created_at).toLocaleString()}>
              {new Date(video.created_at).toLocaleString()}
            </dd>
          </div>
          <div className="flex justify-between items-center">
            <dt className="text-text-muted">Updated</dt>
            <dd className="text-text-primary text-xs" title={new Date(video.updated_at).toLocaleString()}>
              {new Date(video.updated_at).toLocaleString()}
            </dd>
          </div>
        </dl>
      </div>

    </div>
  );
}
