import { Film, Monitor, Headphones } from "lucide-react";
import type { QualitySignature } from "@/types";

interface QualityPanelProps {
  quality: QualitySignature;
}

export function QualityPanel({ quality: q }: QualityPanelProps) {
  const videoRows: [string, string | null][] = [
    ["Resolution", q.width && q.height ? `${q.width}×${q.height}` : null],
    ["FPS", q.fps ? q.fps.toFixed(1) : null],
    ["Video Codec", q.video_codec ?? null],
    ["Video Bitrate", q.video_bitrate ? `${(q.video_bitrate / 1000).toFixed(0)} kbps` : null],
    ["HDR", q.hdr ? "Yes" : "No"],
  ];

  const audioRows: [string, string | null][] = [
    ["Audio Codec", q.audio_codec ?? null],
    ["Audio Bitrate", q.audio_bitrate ? `${q.audio_bitrate} kbps` : null],
    ["Channels", q.audio_channels?.toString() ?? null],
    ["Sample Rate", q.audio_sample_rate ? `${q.audio_sample_rate} Hz` : null],
    ["Loudness", q.loudness_lufs != null ? `${q.loudness_lufs.toFixed(1)} LUFS` : null],
  ];

  const containerRow: [string, string | null] = ["Container", q.container ?? null];

  return (
    <div className="card space-y-4">
      {/* Video section */}
      <div>
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-2 flex items-center gap-1.5">
          <Monitor size={14} /> Video
        </h3>
        <dl className="space-y-1.5 text-sm">
          {videoRows.map(([label, value]) =>
            value ? (
              <div key={label} className="flex justify-between items-center group">
                <dt className="text-text-muted">{label}</dt>
                <dd className="text-text-primary font-mono text-xs" title={value}>
                  {value}
                </dd>
              </div>
            ) : null
          )}
        </dl>
      </div>

      {/* Audio section */}
      <div>
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-2 flex items-center gap-1.5">
          <Headphones size={14} /> Audio
        </h3>
        <dl className="space-y-1.5 text-sm">
          {audioRows.map(([label, value]) =>
            value ? (
              <div key={label} className="flex justify-between items-center group">
                <dt className="text-text-muted">{label}</dt>
                <dd className="text-text-primary font-mono text-xs" title={value}>
                  {value}
                </dd>
              </div>
            ) : null
          )}
        </dl>
      </div>

      {/* Container */}
      {containerRow[1] && (
        <div>
          <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide mb-2 flex items-center gap-1.5">
            <Film size={14} /> Container
          </h3>
          <dl className="text-sm">
            <div className="flex justify-between items-center">
              <dt className="text-text-muted">{containerRow[0]}</dt>
              <dd className="text-text-primary font-mono text-xs">{containerRow[1]}</dd>
            </div>
          </dl>
        </div>
      )}
    </div>
  );
}
