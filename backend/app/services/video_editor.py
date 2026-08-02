"""
Video Editor Service — Letterbox detection, crop calculation, and FFmpeg encoding.

Provides:
- Letterbox (black bar) detection via ffmpeg cropdetect
- Aspect ratio calculation and crop geometry
- H.264 re-encoding with quality preservation (CRF mode)
- Audio passthrough by default
"""
import json
import hashlib
import logging
import os
import shutil
import subprocess
import sys

from app.subprocess_utils import HIDE_WINDOW
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from app.config import get_settings
from app.services.media_analyzer import probe_file

logger = logging.getLogger(__name__)

# ── Preset aspect ratios ──────────────────────────────────
ASPECT_RATIOS = {
    "16:9": (16, 9),
    "4:3": (4, 3),
    "21:9": (21, 9),
    "1:1": (1, 1),
    "2.35:1": (2.35, 1),
    "2.39:1": (2.39, 1),
    "1.85:1": (1.85, 1),
}

ENCODE_PROFILES = {"source_fidelity", "balanced", "custom"}
_LOSSLESS_AUDIO_CODECS = {"alac", "flac", "ape", "wavpack", "tta"}


def _stream_bit_depth(stream: Dict[str, Any]) -> int:
    """Return the best available decoded bit depth for a video stream."""
    for key in ("bits_per_raw_sample", "bits_per_sample"):
        try:
            value = int(stream.get(key) or 0)
            if value:
                return value
        except (TypeError, ValueError):
            pass
    pix_fmt = str(stream.get("pix_fmt") or "")
    for depth in (16, 14, 12, 10, 9):
        if str(depth) in pix_fmt:
            return depth
    return 8


def resolve_encode_plan(
    input_path: str,
    *,
    profile: str = "source_fidelity",
    crop: Optional[Dict[str, int]] = None,
    target_dar: Optional[str] = None,
    crf: int = 18,
    preset: str = "medium",
    audio_passthrough: bool = True,
    trim_start: Optional[float] = None,
    trim_end: Optional[float] = None,
    audio_codec: Optional[str] = None,
    audio_bitrate: Optional[str] = None,
    probe: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve user intent into an auditable, source-aware encode plan.

    The plan is JSON serialisable and is stored with the job.  In particular,
    source-fidelity never derives a maxrate from the input bitrate: CRF remains
    a quality target instead of an accidental quality ceiling.
    """
    if profile not in ENCODE_PROFILES:
        raise ValueError(f"Unknown encode profile: {profile}")
    media = probe or probe_file(input_path)
    streams = media.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        raise ValueError("Source does not contain a video stream")

    bit_depth = _stream_bit_depth(video)
    transfer = video.get("color_transfer")
    hdr = transfer in {"smpte2084", "arib-std-b67"}
    source_pix_fmt = video.get("pix_fmt") or "unknown"
    has_trim = bool((trim_start and trim_start > 0) or (trim_end and trim_end > 0))
    warnings: list[str] = []
    errors: list[str] = []

    if profile == "balanced" and (hdr or bit_depth > 8):
        errors.append(
            "Balanced is an 8-bit SDR profile and would discard HDR/bit-depth data; "
            "use Source fidelity or Custom."
        )

    preserve_depth = profile in {"source_fidelity", "custom"} and (hdr or bit_depth > 8)
    video_encoder = "libx265" if preserve_depth else "libx264"
    if preserve_depth:
        supported_high_depth = {
            "yuv420p10le", "yuv422p10le", "yuv444p10le",
            "yuv420p12le", "yuv422p12le", "yuv444p12le",
        }
        pixel_format = source_pix_fmt if source_pix_fmt in supported_high_depth else "yuv420p10le"
        if source_pix_fmt != pixel_format:
            warnings.append(
                f"Source pixel format {source_pix_fmt} will be encoded as {pixel_format}; "
                f"the {bit_depth}-bit/HDR signal is retained."
            )
    else:
        pixel_format = source_pix_fmt if source_pix_fmt in {"yuv420p", "yuv422p", "yuv444p"} else "yuv420p"

    copy_audio = bool(audio and audio_passthrough and not has_trim)
    resolved_audio_codec: Optional[str] = "copy" if copy_audio else None
    resolved_audio_bitrate = audio_bitrate
    if audio and not copy_audio:
        source_audio_codec = str(audio.get("codec_name") or "")
        if audio_codec:
            resolved_audio_codec = audio_codec
        elif source_audio_codec in _LOSSLESS_AUDIO_CODECS or source_audio_codec.startswith("pcm_"):
            # ALAC remains lossless and is supported by MP4/MOV; Matroska can
            # carry it too.  Never silently turn a lossless source into AAC.
            resolved_audio_codec = "alac"
            resolved_audio_bitrate = None
            warnings.append("Lossless source audio will be re-encoded losslessly as ALAC because trimming prevents stream copy.")
        else:
            resolved_audio_codec = "aac"
        if has_trim:
            warnings.append("Frame-accurate trimming requires audio re-encoding; sample rate and channel layout will be preserved.")

    input_ext = Path(input_path).suffix.lower()
    output_extension = input_ext if input_ext in {".mkv", ".mp4", ".m4v", ".mov"} else ".mkv"
    source = {
        "codec": video.get("codec_name"),
        "width": video.get("width"),
        "height": video.get("height"),
        "pixel_format": source_pix_fmt,
        "bit_depth": bit_depth,
        "hdr": hdr,
        "frame_rate": video.get("avg_frame_rate") or video.get("r_frame_rate"),
        "sample_aspect_ratio": video.get("sample_aspect_ratio"),
        "color_primaries": video.get("color_primaries"),
        "color_transfer": transfer,
        "color_space": video.get("color_space"),
        "color_range": video.get("color_range"),
        "rotation": (video.get("tags") or {}).get("rotate"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "audio_sample_rate": audio.get("sample_rate") if audio else None,
        "audio_channels": audio.get("channels") if audio else None,
        "audio_channel_layout": audio.get("channel_layout") if audio else None,
    }
    return {
        "profile": profile,
        "source": source,
        "output": {
            "extension": output_extension,
            "video_encoder": video_encoder,
            "pixel_format": pixel_format,
            "crf": crf,
            "preset": preset,
            "maxrate": None,
            "frame_timing": "passthrough",
            "metadata": "copy",
            "chapters": "copy",
            "color_metadata": "copy",
            "sample_aspect_ratio": "preserve" if not target_dar else target_dar,
            "rotation": "preserve",
            "audio_codec": resolved_audio_codec,
            "audio_bitrate": resolved_audio_bitrate,
            "audio_sample_rate": "preserve",
            "audio_channels": "preserve",
        },
        "transforms": {
            "crop": crop,
            "target_dar": target_dar,
            "trim_start": trim_start,
            "trim_end": trim_end,
        },
        "warnings": warnings,
        "errors": errors,
    }


# ── Letterbox detection tunables ────────────────────────────────────────────
# Luminance (0-255) at or below which a pixel counts as "black". Kept low so
# only *true* black bars qualify — dark scene content (shadows, night) reads
# well above this, which prevents the over-cropping the looser old limit (64)
# caused.
_CROPDETECT_LIMIT = 24
# A cropdetect reading whose detected content area is smaller than this fraction
# of the frame comes from a near-black frame (fade, dark scene) and is unreliable
# — discard it.
_MIN_CONTENT_AREA_FRAC = 0.35
_AUTO_CROP_CONFIDENCE = 0.80
_MIN_MEANINGFUL_CROP_FRAC = 0.025


def crop_evidence_hash(info: dict, source_checksum: Optional[str]) -> str:
    evidence = {
        "source_checksum": source_checksum,
        "confidence": info.get("confidence"),
        "sample_count": info.get("sample_count"),
        "samples_expected": info.get("samples_expected"),
        "per_window_bars": info.get("per_window_bars", []),
        "crop": [info.get(key) for key in ("crop_w", "crop_h", "crop_x", "crop_y")],
    }
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _score_crop_samples(per_window: list, expected_samples: int, orig_w: int, orig_h: int) -> tuple[float, str | None]:
    """Return a conservative confidence score for crop evidence windows."""
    import statistics

    if not per_window:
        return 0.0, "no_valid_samples"
    bars = [
        (y, orig_h - (y + h), x, orig_w - (x + w))
        for w, h, x, y in per_window
    ]
    medians = [statistics.median(values) for values in zip(*bars)]
    tolerances = (max(4, orig_h * 0.02), max(4, orig_h * 0.02),
                  max(4, orig_w * 0.02), max(4, orig_w * 0.02))
    stable_count = sum(
        all(abs(value - median) <= tolerance for value, median, tolerance in zip(sample, medians, tolerances))
        for sample in bars
    )
    coverage = len(per_window) / max(1, expected_samples)
    stability = stable_count / len(per_window)
    confidence = 0.4 * coverage + 0.6 * stability
    if len(per_window) == 1:
        confidence = min(confidence, 0.55)
        reason = "insufficient_samples"
    elif stability < 0.75:
        reason = "inconsistent_windows"
    elif coverage < 0.5:
        reason = "too_few_valid_windows"
    else:
        reason = None
    return round(max(0.0, min(1.0, confidence)), 3), reason


def _parse_cropdetect_lines(stderr: str, orig_w: int, orig_h: int) -> list:
    """Extract valid, plausible crop=W:H:X:Y readings from cropdetect stderr."""
    crops = []
    for line in stderr.splitlines():
        idx = line.find("crop=")
        if idx == -1:
            continue
        crop_str = line[idx + 5:].split()[0]
        parts = crop_str.split(":")
        if len(parts) != 4:
            continue
        try:
            w, h, x, y = (int(p) for p in parts)
        except (ValueError, TypeError):
            continue
        # Reject out-of-bounds / degenerate rects.
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > orig_w or y + h > orig_h:
            continue
        # Reject near-black frames (content area too small to trust).
        if (w * h) < _MIN_CONTENT_AREA_FRAC * (orig_w * orig_h):
            continue
        crops.append((w, h, x, y))
    return crops


def _cropdetect_window(ffmpeg: str, file_path: str, start: float, dur: float,
                       orig_w: int, orig_h: int):
    """Run cropdetect over one short window and return its modal crop, or None.

    Uses round=2 (accurate, even-aligned) and reset=1 so each frame produces an
    independent reading; the per-window mode is the stable crop for that window.
    """
    cmd = [
        ffmpeg,
        "-ss", f"{max(0.0, start):.3f}",
        "-i", file_path,
        "-t", f"{dur:.3f}",
        "-vf", f"cropdetect=limit={_CROPDETECT_LIMIT}:round=2:reset=1",
        "-f", "null",
        "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90, **HIDE_WINDOW)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"cropdetect window at {start:.1f}s failed: {e}")
        return None
    crops = _parse_cropdetect_lines(result.stderr, orig_w, orig_h)
    if not crops:
        return None
    from collections import Counter
    return Counter(crops).most_common(1)[0][0]


def detect_letterbox(file_path: str, sample_duration: int = 30, skip_seconds: int = 60) -> Dict[str, Any]:
    """Detect letterboxing (black bars) via multi-window ffmpeg cropdetect.

    Rather than trusting a single 30s window (which is easily fooled by one
    dark or bright scene), this samples several short windows spread across the
    whole video and reaches a robust consensus:

      1. Multi-window temporal sampling — up to 6 windows between 8%–92% of the
         runtime, so no single scene dominates.
      2. Per-window mode + across-window median of each bar — outlier windows
         (a fade-to-black, a bright flash) can't skew the result.
      3. Dark-frame rejection — readings from near-black frames are discarded.
      4. Symmetry snapping — real letterbox/pillarbox bars are symmetric; an
         axis whose two bars disagree beyond tolerance is treated as unreliable
         (no crop) rather than producing a lopsided crop into real content.
      5. Relative + absolute thresholds + even alignment — a bar must exceed
         both ~8px and ~1.8% of the dimension to count, and crops are even.

    Returns dict with (unchanged contract):
        detected, crop_w, crop_h, crop_x, crop_y,
        original_w, original_h, bar_top, bar_bottom, bar_left, bar_right
    """
    import statistics

    settings = get_settings()
    ffmpeg = settings.resolved_ffmpeg

    # Get original dimensions + duration first
    probe = probe_file(file_path)
    original_w, original_h = None, None
    duration = None
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video":
            original_w = stream.get("width")
            original_h = stream.get("height")
            break
    fmt = probe.get("format", {})
    if fmt.get("duration"):
        try:
            duration = float(fmt["duration"])
        except (ValueError, TypeError):
            duration = None

    if not original_w or not original_h:
        raise ValueError(f"Could not determine video dimensions for {file_path}")

    def _no_letterbox(reason: str = "no_meaningful_bars", samples: Optional[list] = None) -> Dict[str, Any]:
        return {
            "detected": False,
            "auto_apply": False,
            "review_suggested": False,
            "confidence": 0.0,
            "sample_count": len(samples or []),
            "samples_expected": len(windows),
            "per_window_bars": samples or [],
            "instability_reason": reason,
            "original_w": original_w, "original_h": original_h,
            "crop_w": original_w, "crop_h": original_h,
            "crop_x": 0, "crop_y": 0,
            "bar_top": 0, "bar_bottom": 0, "bar_left": 0, "bar_right": 0,
        }

    # ── Build sample windows spread across the runtime ───────────────────────
    windows: list = []
    if duration and duration > 12:
        usable_start = duration * 0.08
        usable_end = duration * 0.92
        span = max(0.0, usable_end - usable_start)
        n = 6 if duration > 90 else (4 if duration > 30 else 2)
        win_dur = min(4.0, max(2.0, span / max(1, n * 2)))
        for k in range(n):
            frac = (k / (n - 1)) if n > 1 else 0.5
            windows.append((usable_start + frac * max(0.0, span - win_dur), win_dur))
    else:
        # Short / unknown duration — fall back to a single window like before.
        start = skip_seconds if (duration and skip_seconds < duration) else 0
        windows.append((float(start), float(min(sample_duration, 10))))

    logger.info(f"Running letterbox detection on {file_path} ({len(windows)} windows)")

    per_window = []
    for (start, dur) in windows:
        crop = _cropdetect_window(ffmpeg, file_path, start, dur, original_w, original_h)
        if crop:
            per_window.append(crop)

    if not per_window:
        return _no_letterbox("no_valid_samples")

    per_window_bars = [
        {
            "top": y,
            "bottom": original_h - (y + h),
            "left": x,
            "right": original_w - (x + w),
        }
        for w, h, x, y in per_window
    ]
    confidence, instability_reason = _score_crop_samples(
        per_window, len(windows), original_w, original_h,
    )

    # ── Robust consensus: median of each bar across the sampled windows ──────
    tops = [y for (_w, _h, _x, y) in per_window]
    bottoms = [original_h - (y + h) for (_w, h, _x, y) in per_window]
    lefts = [x for (_w, _h, x, _y) in per_window]
    rights = [original_w - (x + w) for (w, _h, x, _y) in per_window]

    bt = max(0, int(statistics.median(tops)))
    bb = max(0, int(statistics.median(bottoms)))
    bl = max(0, int(statistics.median(lefts)))
    br = max(0, int(statistics.median(rights)))

    # ── Symmetry snapping ────────────────────────────────────────────────────
    def _reconcile(a: int, b: int, dim: int):
        tol = max(4, int(dim * 0.02))
        if abs(a - b) <= tol:
            v = min(a, b)          # symmetric bars → conservative common value
            return v, v
        return 0, 0                # lopsided → unreliable, don't auto-crop this axis

    raw_bt, raw_bb, raw_bl, raw_br = bt, bb, bl, br
    bt, bb = _reconcile(bt, bb, original_h)
    bl, br = _reconcile(bl, br, original_w)
    asymmetric = (
        ((raw_bt > 0 or raw_bb > 0) and bt == 0)
        or ((raw_bl > 0 or raw_br > 0) and bl == 0)
    )
    if asymmetric:
        confidence = round(confidence * 0.5, 3)
        instability_reason = "asymmetric_bars"

    # ── Relative + absolute threshold, then even-align ───────────────────────
    def _thr(v: int, dim: int) -> int:
        v = v if v >= max(8, int(dim * 0.018)) else 0
        return v - (v % 2)

    bt = _thr(bt, original_h)
    bb = _thr(bb, original_h)
    bl = _thr(bl, original_w)
    br = _thr(br, original_w)

    crop_x = bl
    crop_y = bt
    crop_w = original_w - bl - br
    crop_h = original_h - bt - bb

    # Guard against an implausible over-crop (would destroy the frame).
    if crop_w < original_w * 0.2 or crop_h < original_h * 0.2:
        logger.warning(f"Letterbox detection produced implausible crop for {file_path}; ignoring")
        return _no_letterbox("implausible_crop", per_window_bars)

    candidate_detected = (bt > 0 or bb > 0 or bl > 0 or br > 0)
    raw_candidate = max(raw_bt, raw_bb, raw_bl, raw_br) >= 4
    removed_fraction = max((bt + bb) / original_h, (bl + br) / original_w)
    meaningful = removed_fraction >= _MIN_MEANINGFUL_CROP_FRAC
    auto_apply = bool(
        candidate_detected
        and meaningful
        and confidence >= _AUTO_CROP_CONFIDENCE
        and len(per_window) >= 2
        and not asymmetric
    )
    review_suggested = bool((candidate_detected or raw_candidate) and not auto_apply)
    if review_suggested and not instability_reason:
        instability_reason = (
            "crop_below_meaningful_threshold" if not meaningful else "confidence_below_auto_apply"
        )

    return {
        "detected": auto_apply,
        "auto_apply": auto_apply,
        "review_suggested": review_suggested,
        "confidence": confidence,
        "sample_count": len(per_window),
        "samples_expected": len(windows),
        "per_window_bars": per_window_bars,
        "instability_reason": instability_reason,
        "original_w": original_w, "original_h": original_h,
        "crop_w": crop_w, "crop_h": crop_h,
        "crop_x": crop_x, "crop_y": crop_y,
        "bar_top": bt, "bar_bottom": bb, "bar_left": bl, "bar_right": br,
    }


def compute_crop_for_ratio(
    original_w: int, original_h: int,
    target_ratio_w: float, target_ratio_h: float,
) -> Dict[str, int]:
    """Compute crop geometry to achieve a target aspect ratio, centered.

    Returns: crop_w, crop_h, crop_x, crop_y
    """
    target_ratio = target_ratio_w / target_ratio_h
    current_ratio = original_w / original_h

    if abs(current_ratio - target_ratio) < 0.01:
        # Already at target ratio
        return {"crop_w": original_w, "crop_h": original_h, "crop_x": 0, "crop_y": 0}

    if current_ratio > target_ratio:
        # Wider than target — crop sides (pillarbox)
        new_w = int(original_h * target_ratio)
        new_w = new_w - (new_w % 2)  # Ensure even
        crop_x = (original_w - new_w) // 2
        return {"crop_w": new_w, "crop_h": original_h, "crop_x": crop_x, "crop_y": 0}
    else:
        # Taller than target — crop top/bottom (letterbox)
        new_h = int(original_w / target_ratio)
        new_h = new_h - (new_h % 2)  # Ensure even
        crop_y = (original_h - new_h) // 2
        return {"crop_w": original_w, "crop_h": new_h, "crop_x": 0, "crop_y": crop_y}


def encode_video(
    input_path: str,
    output_path: str,
    crop: Optional[Dict[str, int]] = None,
    target_dar: Optional[str] = None,
    crf: int = 18,
    preset: str = "medium",
    audio_passthrough: bool = True,
    trim_start: Optional[float] = None,
    trim_end: Optional[float] = None,
    audio_codec: Optional[str] = None,
    audio_bitrate: Optional[str] = None,
    profile: str = "source_fidelity",
    progress_callback=None,
) -> Dict[str, Any]:
    """Re-encode a video from a resolved source-aware profile.

    Args:
        input_path: Source video file
        output_path: Destination file
        crop: Dict with crop_w, crop_h, crop_x, crop_y (None = no crop)
        target_dar: Display aspect ratio string e.g. "16:9" (None = keep original)
        crf: Constant Rate Factor (lower = higher quality, 18 is visually lossless)
        preset: x264 preset (ultrafast..veryslow)
        audio_passthrough: If True, copy audio stream without re-encoding
        trim_start: Seconds to trim from the beginning (None = no start trim)
        trim_end: Seconds to trim from the end (None = no end trim)
        audio_codec: Audio codec to use when re-encoding ("aac", "opus", "flac", None=auto)
        audio_bitrate: Audio bitrate string e.g. "192k" (None = match source)
        progress_callback: Optional callable(percent: float) for progress updates

    Returns: Dict with encode stats (duration, output_size, etc.)
    """
    settings = get_settings()
    ffmpeg = settings.resolved_ffmpeg

    # Get source duration and audio info for progress tracking and smart defaults
    probe = probe_file(input_path)
    plan = resolve_encode_plan(
        input_path,
        profile=profile,
        crop=crop,
        target_dar=target_dar,
        crf=crf,
        preset=preset,
        audio_passthrough=audio_passthrough,
        trim_start=trim_start,
        trim_end=trim_end,
        audio_codec=audio_codec,
        audio_bitrate=audio_bitrate,
        probe=probe,
    )
    if plan["errors"]:
        raise ValueError(" ".join(plan["errors"]))
    duration = None
    source_audio_bitrate = None
    source_audio_codec = None
    source_audio_channels = None
    source_video_bitrate = None
    source_width = None
    source_height = None
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video" and source_width is None:
            source_video_bitrate = int(stream["bit_rate"]) if stream.get("bit_rate") else None
            source_width = stream.get("width")
            source_height = stream.get("height")
        if stream.get("codec_type") == "audio" and source_audio_codec is None:
            source_audio_bitrate = int(stream["bit_rate"]) if stream.get("bit_rate") else None
            source_audio_codec = stream.get("codec_name")
            source_audio_channels = stream.get("channels")
    fmt = probe.get("format", {})
    if fmt.get("duration"):
        duration = float(fmt["duration"])

    # If per-stream video bitrate unavailable, estimate from format-level bitrate
    if not source_video_bitrate and fmt.get("bit_rate") and duration:
        total_br = int(fmt["bit_rate"])
        # Subtract audio bitrate estimate to approximate video-only bitrate
        source_video_bitrate = total_br - (source_audio_bitrate or 128000)

    # Compute effective duration after trim for progress tracking
    has_trim = (trim_start and trim_start > 0) or (trim_end and trim_end > 0)
    effective_duration = duration
    if duration and has_trim:
        effective_duration = duration - (trim_start or 0) - (trim_end or 0)
        if effective_duration <= 0:
            raise ValueError(f"Trim too large: total trim ({(trim_start or 0) + (trim_end or 0):.1f}s) exceeds duration ({duration:.1f}s)")

    # Trimming requires audio re-encode for accurate cuts
    if has_trim:
        audio_passthrough = False

    # Build filter chain
    vf_filters = []
    if crop:
        vf_filters.append(f"crop={crop['crop_w']}:{crop['crop_h']}:{crop['crop_x']}:{crop['crop_y']}")
    if target_dar:
        vf_filters.append(f"setdar={target_dar}")

    # Build command
    cmd = [
        ffmpeg,
        "-y",  # Overwrite output
    ]

    # Preserve stored rotation rather than letting ffmpeg rotate pixels and then
    # also carrying the original rotation metadata into the output.
    if profile == "source_fidelity":
        cmd.append("-noautorotate")

    # Trim: use -ss before -i for fast seek, -to for end point
    if trim_start and trim_start > 0:
        cmd.extend(["-ss", str(trim_start)])

    cmd.extend(["-i", input_path])

    if trim_end and trim_end > 0 and duration:
        end_time = duration - trim_end
        if trim_start and trim_start > 0:
            end_time -= trim_start  # -to is relative to -ss when -ss is before -i
        cmd.extend(["-t", str(end_time)])

    cmd.extend([
        "-map", "0",
        "-map_metadata", "0",
        "-map_chapters", "0",
        "-c:v", plan["output"]["video_encoder"],
        "-crf", str(crf),
        "-preset", preset,
        "-pix_fmt", plan["output"]["pixel_format"],
        "-fps_mode", "passthrough",
    ])

    # Re-state colour properties explicitly: container metadata copying alone is
    # not sufficient for every encoder/muxer combination.
    color_flags = {
        "color_primaries": "-color_primaries",
        "color_transfer": "-color_trc",
        "color_space": "-colorspace",
        "color_range": "-color_range",
    }
    for source_key, flag in color_flags.items():
        value = plan["source"].get(source_key)
        if value:
            cmd.extend([flag, str(value)])

    if vf_filters:
        cmd.extend(["-vf", ",".join(vf_filters)])

    if target_dar:
        # Convert DAR like "16:9" or "1.85:1" to a decimal for -aspect flag
        # (ffmpeg -aspect doesn't accept "1.85:1" but does accept "1.85" or "16:9")
        parts = target_dar.split(":")
        if len(parts) == 2:
            try:
                num, den = float(parts[0]), float(parts[1])
                aspect_val = str(num / den) if den != 0 else target_dar
            except ValueError:
                aspect_val = target_dar
        else:
            aspect_val = target_dar
        cmd.extend(["-aspect", aspect_val])

    resolved_audio_codec = plan["output"].get("audio_codec")
    resolved_audio_bitrate = plan["output"].get("audio_bitrate")
    if resolved_audio_codec == "copy":
        cmd.extend(["-c:a", "copy"])
    elif resolved_audio_codec:
        # Smart audio re-encoding: match source quality by default
        chosen_codec = resolved_audio_codec
        # Validate codec choice
        if chosen_codec not in ("aac", "opus", "flac", "alac"):
            chosen_codec = "aac"

        if chosen_codec in {"flac", "alac"}:
            cmd.extend(["-c:a", chosen_codec])
        elif chosen_codec == "opus":
            # Determine bitrate: use explicit setting, or match source, or sensible default
            if resolved_audio_bitrate:
                br = resolved_audio_bitrate
            elif source_audio_bitrate:
                # Match source bitrate (round to nearest common value, cap at 256k)
                src_kbps = source_audio_bitrate // 1000
                br = f"{min(max(src_kbps, 64), 256)}k"
            else:
                br = "128k"
            cmd.extend(["-c:a", "libopus", "-b:a", br])
        else:
            # AAC — determine bitrate
            if resolved_audio_bitrate:
                br = resolved_audio_bitrate
            elif source_audio_bitrate:
                src_kbps = source_audio_bitrate // 1000
                br = f"{min(max(src_kbps, 96), 320)}k"
            else:
                br = "192k"
            cmd.extend(["-c:a", "aac", "-b:a", br])

    # Copy subtitle streams if present
    cmd.extend(["-c:s", "copy"])

    # Progress tracking via pipe
    cmd.extend(["-progress", "pipe:1", "-nostats"])
    cmd.append(output_path)

    logger.info(f"Encoding: {' '.join(cmd)}")
    start_time = time.time()

    # Redirect stderr to a temp file to prevent pipe deadlock on Windows.
    # ffmpeg writes verbose output to stderr which, if piped, can fill the
    # OS buffer and block the process while we only read stdout for progress.
    stderr_tmp = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")

    _popen_flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=stderr_tmp, text=True,
        **_popen_flags,
    )

    # Read progress from stdout
    out_time_us = 0
    try:
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if line.startswith("out_time_us="):
                try:
                    out_time_us = int(line.split("=")[1].strip())
                    if effective_duration and progress_callback:
                        pct = min(100.0, (out_time_us / 1_000_000) / effective_duration * 100)
                        progress_callback(pct)
                except (ValueError, IndexError):
                    pass
    except Exception:
        # Kill FFmpeg on callback error (e.g. cancellation)
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            process.kill()
        stderr_tmp.close()
        raise

    rc = process.wait()
    elapsed = time.time() - start_time

    if rc != 0:
        stderr_tmp.seek(0)
        stderr_text = stderr_tmp.read()
        stderr_tmp.close()
        raise RuntimeError(f"ffmpeg encode failed (rc={rc}): {stderr_text[:2000]}")

    stderr_tmp.close()

    output_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0

    # Probe output for post-encode summary
    output_w, output_h, output_video_bitrate = None, None, None
    try:
        out_probe = probe_file(output_path)
        for s in out_probe.get("streams", []):
            if s.get("codec_type") == "video":
                output_w = s.get("width")
                output_h = s.get("height")
                output_video_bitrate = int(s["bit_rate"]) if s.get("bit_rate") else None
                break
        if not output_video_bitrate and out_probe.get("format", {}).get("bit_rate"):
            output_video_bitrate = int(out_probe["format"]["bit_rate"])
    except Exception:
        pass

    return {
        "elapsed_seconds": round(elapsed, 1),
        "output_size_bytes": output_size,
        "input_size_bytes": os.path.getsize(input_path),
        "source_w": source_width,
        "source_h": source_height,
        "source_video_bitrate": source_video_bitrate,
        "output_w": output_w,
        "output_h": output_h,
        "output_video_bitrate": output_video_bitrate,
        "encode_plan": plan,
    }


def validate_encoded_output(
    input_path: str,
    output_path: str,
    plan: Dict[str, Any],
) -> Dict[str, Any]:
    """Decode and compare a staged encode before the library file is touched."""
    settings = get_settings()
    source_probe = probe_file(input_path)
    output_probe = probe_file(output_path)
    source_video = next((s for s in source_probe.get("streams", []) if s.get("codec_type") == "video"), None)
    output_video = next((s for s in output_probe.get("streams", []) if s.get("codec_type") == "video"), None)
    source_audio = next((s for s in source_probe.get("streams", []) if s.get("codec_type") == "audio"), None)
    output_audio = next((s for s in output_probe.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not source_video or not output_video:
        raise RuntimeError("Staged validation failed: source or output video stream is missing")

    checks: Dict[str, Any] = {}
    transforms = plan.get("transforms", {})
    source_duration = float(source_probe.get("format", {}).get("duration") or 0)
    output_duration = float(output_probe.get("format", {}).get("duration") or 0)
    expected_duration = max(
        0.0,
        source_duration
        - float(transforms.get("trim_start") or 0)
        - float(transforms.get("trim_end") or 0),
    )
    duration_tolerance = max(0.5, expected_duration * 0.01)
    checks["duration_seconds"] = output_duration
    if expected_duration and abs(output_duration - expected_duration) > duration_tolerance:
        raise RuntimeError(
            f"Staged validation failed: duration {output_duration:.3f}s differs from "
            f"expected {expected_duration:.3f}s"
        )

    if plan.get("profile") == "source_fidelity":
        source_depth = _stream_bit_depth(source_video)
        output_depth = _stream_bit_depth(output_video)
        checks["source_bit_depth"] = source_depth
        checks["output_bit_depth"] = output_depth
        if source_depth > 8 and output_depth < source_depth:
            raise RuntimeError(
                f"Staged validation failed: source is {source_depth}-bit but output is {output_depth}-bit"
            )
        source_transfer = source_video.get("color_transfer")
        checks["color_transfer"] = output_video.get("color_transfer")
        if source_transfer in {"smpte2084", "arib-std-b67"} and output_video.get("color_transfer") != source_transfer:
            raise RuntimeError("Staged validation failed: HDR transfer metadata was not preserved")

    if source_audio:
        if not output_audio:
            raise RuntimeError("Staged validation failed: audio stream is missing")
        for key in ("channels", "sample_rate"):
            source_value = str(source_audio.get(key) or "")
            output_value = str(output_audio.get(key) or "")
            checks[f"audio_{key}"] = output_value
            if source_value and output_value and source_value != output_value:
                raise RuntimeError(
                    f"Staged validation failed: audio {key} changed from {source_value} to {output_value}"
                )

    # A successful full video decode is the last mandatory gate.  Corruption is
    # caught here while the original is still in place.
    decode = subprocess.run(
        [settings.resolved_ffmpeg, "-v", "error", "-xerror", "-i", output_path,
         "-map", "0:v:0", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=21_600,
        **HIDE_WINDOW,
    )
    if decode.returncode != 0:
        raise RuntimeError(f"Staged validation failed during full decode: {decode.stderr[:1000]}")
    checks["full_decode"] = "passed"

    # SSIM is meaningful only when geometry and timing were not intentionally
    # changed.  Use a representative 30-second comparison in addition to the
    # mandatory full decode and probe checks.
    comparable = not any([
        transforms.get("crop"), transforms.get("target_dar"),
        transforms.get("trim_start"), transforms.get("trim_end"),
    ])
    checks["ssim"] = None
    if comparable:
        import re
        metric = subprocess.run(
            [settings.resolved_ffmpeg, "-v", "info", "-i", input_path, "-i", output_path,
             "-filter_complex", "[0:v:0][1:v:0]ssim", "-an", "-t", "30", "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=600,
            **HIDE_WINDOW,
        )
        match = re.search(r"All:([0-9.]+)", metric.stderr or "")
        if metric.returncode == 0 and match:
            checks["ssim"] = float(match.group(1))
            if checks["ssim"] < 0.90:
                raise RuntimeError(f"Staged validation failed: SSIM {checks['ssim']:.4f} is below 0.9000")
        else:
            checks["ssim_note"] = "metric unavailable; probe comparison and full decode passed"
    else:
        checks["ssim_note"] = "not comparable after an intentional crop, DAR, or trim transform"
    return checks


def _sync_editor_flags_from_sidecars(db, VideoItem):
    """Backfill exclude_from_editor_scan and editor_edit_type from sidecar XMLs.

    Only touches videos that currently have flags unset in the DB (i.e.
    exclude=False or edit_type=None).  Runs once per scan — fast no-op when
    the DB is already in sync.
    """
    import os
    from sqlalchemy import or_
    from app.services.playarr_xml import find_playarr_xml, parse_playarr_xml

    candidates = (
        db.query(VideoItem)
        .filter(
            VideoItem.file_path.isnot(None),
            or_(
                VideoItem.exclude_from_editor_scan == False,
                VideoItem.editor_edit_type.is_(None),
            ),
        )
        .all()
    )
    updated = 0
    for v in candidates:
        if not v.file_path:
            continue
        folder = os.path.dirname(v.file_path)
        xml_path = find_playarr_xml(folder, video_file=v.file_path)
        if not xml_path:
            continue
        try:
            xd = parse_playarr_xml(xml_path)
        except Exception:
            continue
        changed = False
        if not v.exclude_from_editor_scan and xd.get("exclude_from_editor_scan"):
            v.exclude_from_editor_scan = True
            changed = True
        et = xd.get("editor_edit_type")
        if v.editor_edit_type is None and et:
            v.editor_edit_type = et
            changed = True
        if changed:
            updated += 1
    if updated:
        db.commit()


def scan_library_for_letterboxing(
    db, limit: int = 500, include_excluded: bool = False, force_rescan: bool = False,
    skip_cropped: bool = False, skip_trimmed: bool = False,
    on_progress=None,
) -> list:
    """Scan video library for files with letterboxing.

    Persists results to QualitySignature so subsequent scans skip already-analyzed
    files. Returns a list of dicts with video_id, artist, title, and crop info
    (both newly detected and previously stored).

    Args:
        include_excluded: If True, also scan videos marked exclude_from_editor_scan.
        force_rescan: If True, re-run detection even on already-scanned videos.
        skip_cropped: If True, skip videos previously edited with crop (or both).
        skip_trimmed: If True, skip videos previously edited with trim (or both).
        on_progress: Optional callback(current, total, artist, title) called per file.
    """
    from app.models import VideoItem, QualitySignature
    from sqlalchemy import or_

    import logging
    _log = logging.getLogger("playarr")
    _log.info(f"scan_library_for_letterboxing: include_excluded={include_excluded} skip_cropped={skip_cropped} skip_trimmed={skip_trimmed}")

    # ── Pre-sync editor flags from sidecar XMLs ──────────────────────────
    # When using a fresh DB, exclude_from_editor_scan and editor_edit_type
    # may be missing even though the sidecar XML has them.  Do a quick
    # backfill so the DB filters below work correctly.
    _sync_editor_flags_from_sidecars(db, VideoItem)

    # A false-positive dismissal applies only to the exact source file
    # evidence. A replaced/changed file is eligible for review again.
    for dismissed_video in db.query(VideoItem).filter(
        VideoItem.exclude_from_editor_scan == True,
        VideoItem.editor_crop_dismissed_evidence_hash.isnot(None),
    ).all():
        signature = dismissed_video.quality_signature
        if signature and signature.letterbox_source_checksum != dismissed_video.file_checksum:
            dismissed_video.exclude_from_editor_scan = False
            dismissed_video.editor_crop_dismissed_evidence_hash = None
    db.flush()

    def _apply_edit_filters(query):
        """Apply skip_cropped / skip_trimmed filters to a query."""
        if skip_cropped and skip_trimmed:
            query = query.filter(VideoItem.editor_edit_type.is_(None))
        elif skip_cropped:
            query = query.filter(
                (VideoItem.editor_edit_type.is_(None)) | (VideoItem.editor_edit_type == "trim")
            )
        elif skip_trimmed:
            query = query.filter(
                (VideoItem.editor_edit_type.is_(None)) | (VideoItem.editor_edit_type == "crop")
            )
        return query

    # First, collect all previously-detected letterboxed videos (no limit)
    prev_query = (
        db.query(VideoItem)
        .join(QualitySignature, VideoItem.id == QualitySignature.video_id)
        .filter(
            VideoItem.file_path.isnot(None),
            QualitySignature.letterbox_scanned == True,
            or_(
                QualitySignature.letterbox_detected == True,
                QualitySignature.letterbox_review_suggested == True,
            ),
        )
    )
    if not include_excluded:
        prev_query = prev_query.filter(VideoItem.exclude_from_editor_scan == False)
    prev_query = _apply_edit_filters(prev_query)

    results = []
    prev_videos = prev_query.all()
    _log.info(f"scan: prev_query returned {len(prev_videos)} previously-detected videos")
    for video in prev_videos:
        qs = video.quality_signature
        if not qs:
            continue
        if force_rescan:
            continue  # will be re-detected below
        results.append({
            "video_id": video.id,
            "artist": video.artist,
            "title": video.title,
            "file_path": video.file_path,
            "detected": bool(qs.letterbox_detected),
            "auto_apply": bool(qs.letterbox_detected),
            "review_suggested": bool(qs.letterbox_review_suggested),
            "confidence": qs.letterbox_confidence or 0.0,
            "sample_count": qs.letterbox_sample_count,
            "samples_expected": qs.letterbox_samples_expected,
            "instability_reason": qs.letterbox_instability_reason,
            "per_window_bars": (qs.letterbox_evidence_json or {}).get("per_window_bars", []),
            "original_w": qs.width,
            "original_h": qs.height,
            "crop_w": qs.letterbox_crop_w,
            "crop_h": qs.letterbox_crop_h,
            "crop_x": qs.letterbox_crop_x,
            "crop_y": qs.letterbox_crop_y,
            "bar_top": qs.letterbox_bar_top or 0,
            "bar_bottom": qs.letterbox_bar_bottom or 0,
            "bar_left": qs.letterbox_bar_left or 0,
            "bar_right": qs.letterbox_bar_right or 0,
        })

    # Now scan unscanned videos (or all if force_rescan), applying limit to new detections
    scan_query = (
        db.query(VideoItem)
        .join(QualitySignature, VideoItem.id == QualitySignature.video_id, isouter=True)
        .filter(VideoItem.file_path.isnot(None))
    )
    if not include_excluded:
        scan_query = scan_query.filter(VideoItem.exclude_from_editor_scan == False)
    scan_query = _apply_edit_filters(scan_query)
    if not force_rescan:
        scan_query = scan_query.filter(
            (QualitySignature.letterbox_scanned == None) | (QualitySignature.letterbox_scanned == False)
        )

    videos = scan_query.all()
    total_to_scan = len(videos)
    newly_detected = 0
    scanned_count = 0
    for video in videos:
        if not video.file_path or not os.path.isfile(video.file_path):
            scanned_count += 1
            continue

        qs = video.quality_signature
        if not qs:
            scanned_count += 1
            continue

        scanned_count += 1
        if on_progress:
            try:
                on_progress(scanned_count, total_to_scan, video.artist, video.title)
            except Exception:
                pass

        try:
            info = detect_letterbox(video.file_path)

            # Persist results to QualitySignature
            qs.letterbox_scanned = True
            qs.letterbox_detected = info["detected"]
            qs.letterbox_crop_w = info["crop_w"]
            qs.letterbox_crop_h = info["crop_h"]
            qs.letterbox_crop_x = info["crop_x"]
            qs.letterbox_crop_y = info["crop_y"]
            qs.letterbox_bar_top = info["bar_top"]
            qs.letterbox_bar_bottom = info["bar_bottom"]
            qs.letterbox_bar_left = info["bar_left"]
            qs.letterbox_bar_right = info["bar_right"]
            qs.letterbox_confidence = info["confidence"]
            qs.letterbox_sample_count = info["sample_count"]
            qs.letterbox_samples_expected = info["samples_expected"]
            qs.letterbox_review_suggested = info["review_suggested"]
            qs.letterbox_instability_reason = info["instability_reason"]
            qs.letterbox_evidence_json = {"per_window_bars": info["per_window_bars"]}
            qs.letterbox_source_checksum = video.file_checksum
            qs.letterbox_evidence_hash = crop_evidence_hash(info, video.file_checksum)
            db.commit()

            # Persist through the durable outbox so future rebuilds retain it.
            from app.services.playarr_xml import write_playarr_xml
            write_playarr_xml(video, db)
            db.commit()

            if info["detected"] or info["review_suggested"]:
                results.append({
                    "video_id": video.id,
                    "artist": video.artist,
                    "title": video.title,
                    "file_path": video.file_path,
                    **info,
                })
                if info["detected"]:
                    newly_detected += 1
        except Exception as e:
            logger.warning(f"Letterbox scan failed for video {video.id}: {e}")
            continue

    return results
