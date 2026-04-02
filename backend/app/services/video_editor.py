"""
Video Editor Service — Letterbox detection, crop calculation, and FFmpeg encoding.

Provides:
- Letterbox (black bar) detection via ffmpeg cropdetect
- Aspect ratio calculation and crop geometry
- H.264 re-encoding with quality preservation (CRF mode)
- Audio passthrough by default
"""
import json
import logging
import os
import shutil
import subprocess
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


def detect_letterbox(file_path: str, sample_duration: int = 30, skip_seconds: int = 60) -> Dict[str, Any]:
    """Detect letterboxing (black bars) using ffmpeg cropdetect.

    Samples `sample_duration` seconds of video starting at `skip_seconds`
    (to avoid intros/fades) and returns the detected crop geometry.

    Returns dict with:
        detected: bool — whether letterboxing was found
        crop_w, crop_h, crop_x, crop_y — detected crop rect
        original_w, original_h — original video dimensions
        bar_top, bar_bottom, bar_left, bar_right — black bar sizes
    """
    settings = get_settings()
    ffmpeg = settings.resolved_ffmpeg
    ffprobe = settings.resolved_ffprobe

    # Get original dimensions first
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
        duration = float(fmt["duration"])

    if not original_w or not original_h:
        raise ValueError(f"Could not determine video dimensions for {file_path}")

    # Clamp skip to avoid seeking past end
    if duration and skip_seconds >= duration:
        skip_seconds = max(0, int(duration * 0.3))
    actual_sample = min(sample_duration, int(duration - skip_seconds)) if duration else sample_duration

    cmd = [
        ffmpeg,
        "-ss", str(skip_seconds),
        "-i", file_path,
        "-t", str(actual_sample),
        "-vf", "cropdetect=64:16:0",
        "-f", "null",
        "-",
    ]

    logger.info(f"Running cropdetect on {file_path}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    # Parse cropdetect output from stderr — lines like:
    #   [Parsed_cropdetect_0 @ ...] x1:0 x2:1919 y1:140 y2:939 w:1920 h:800 ...
    #   crop=1920:800:0:140
    crop_lines = []
    for line in result.stderr.splitlines():
        if "crop=" in line:
            # Extract the crop=W:H:X:Y value
            idx = line.index("crop=")
            crop_str = line[idx + 5:].split()[0]
            crop_lines.append(crop_str)

    if not crop_lines:
        return {
            "detected": False,
            "original_w": original_w,
            "original_h": original_h,
            "crop_w": original_w,
            "crop_h": original_h,
            "crop_x": 0,
            "crop_y": 0,
            "bar_top": 0,
            "bar_bottom": 0,
            "bar_left": 0,
            "bar_right": 0,
        }

    # Use the most common crop value (mode) for stability
    from collections import Counter
    mode_crop = Counter(crop_lines).most_common(1)[0][0]
    parts = mode_crop.split(":")
    crop_w, crop_h, crop_x, crop_y = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])

    bar_top = crop_y
    bar_bottom = original_h - (crop_y + crop_h)
    bar_left = crop_x
    bar_right = original_w - (crop_x + crop_w)

    # Consider letterboxing detected if bars are significant (> 10px to avoid compression artifacts)
    detected = (bar_top > 10 or bar_bottom > 10 or bar_left > 10 or bar_right > 10)

    return {
        "detected": detected,
        "original_w": original_w,
        "original_h": original_h,
        "crop_w": crop_w,
        "crop_h": crop_h,
        "crop_x": crop_x,
        "crop_y": crop_y,
        "bar_top": bar_top,
        "bar_bottom": bar_bottom,
        "bar_left": bar_left,
        "bar_right": bar_right,
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
    progress_callback=None,
) -> Dict[str, Any]:
    """Re-encode a video with H.264, optionally cropping, trimming, and/or setting DAR.

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
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", preset,
        "-pix_fmt", "yuv420p",
    ])

    # Constrained CRF: cap bitrate at the source level so we never inflate the file
    # beyond the source's quality envelope. This ensures output is "relatively lossless"
    # compared to the source without producing unnecessarily large files.
    if source_video_bitrate and source_video_bitrate > 0:
        maxrate_kbps = source_video_bitrate // 1000
        bufsize_kbps = maxrate_kbps * 2
        cmd.extend([
            "-maxrate", f"{maxrate_kbps}k",
            "-bufsize", f"{bufsize_kbps}k",
        ])
        logger.info(f"Constrained CRF: maxrate={maxrate_kbps}k bufsize={bufsize_kbps}k (source video bitrate: {source_video_bitrate // 1000}k)")

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

    if audio_passthrough:
        cmd.extend(["-c:a", "copy"])
    else:
        # Smart audio re-encoding: match source quality by default
        chosen_codec = audio_codec or "aac"  # aac is universally compatible
        # Validate codec choice
        if chosen_codec not in ("aac", "opus", "flac"):
            chosen_codec = "aac"

        if chosen_codec == "flac":
            cmd.extend(["-c:a", "flac"])
        elif chosen_codec == "opus":
            # Determine bitrate: use explicit setting, or match source, or sensible default
            if audio_bitrate:
                br = audio_bitrate
            elif source_audio_bitrate:
                # Match source bitrate (round to nearest common value, cap at 256k)
                src_kbps = source_audio_bitrate // 1000
                br = f"{min(max(src_kbps, 64), 256)}k"
            else:
                br = "128k"
            cmd.extend(["-c:a", "libopus", "-b:a", br])
        else:
            # AAC — determine bitrate
            if audio_bitrate:
                br = audio_bitrate
            elif source_audio_bitrate:
                src_kbps = source_audio_bitrate // 1000
                br = f"{min(max(src_kbps, 96), 320)}k"
            else:
                br = "192k"
            cmd.extend(["-c:a", "aac", "-b:a", br])

    # Copy subtitle streams if present
    cmd.extend(["-c:s", "copy"])

    # Map all streams
    cmd.extend(["-map", "0"])

    # Progress tracking via pipe
    cmd.extend(["-progress", "pipe:1", "-nostats"])
    cmd.append(output_path)

    logger.info(f"Encoding: {' '.join(cmd)}")
    start_time = time.time()

    # Redirect stderr to a temp file to prevent pipe deadlock on Windows.
    # ffmpeg writes verbose output to stderr which, if piped, can fill the
    # OS buffer and block the process while we only read stdout for progress.
    stderr_tmp = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")

    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=stderr_tmp, text=True,
    )

    # Read progress from stdout
    out_time_us = 0
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
    }


def scan_library_for_letterboxing(db, limit: int = 500) -> list:
    """Scan video library for files with letterboxing.

    Returns a list of dicts with video_id, artist, title, and crop info.
    """
    from app.models import VideoItem, QualitySignature

    results = []
    videos = (
        db.query(VideoItem)
        .join(QualitySignature, VideoItem.id == QualitySignature.video_id, isouter=True)
        .filter(VideoItem.file_path.isnot(None))
        .filter(VideoItem.exclude_from_editor_scan == False)
        .limit(limit)
        .all()
    )

    for video in videos:
        if not video.file_path or not os.path.isfile(video.file_path):
            continue
        try:
            info = detect_letterbox(video.file_path)
            if info["detected"]:
                results.append({
                    "video_id": video.id,
                    "artist": video.artist,
                    "title": video.title,
                    "file_path": video.file_path,
                    **info,
                })
        except Exception as e:
            logger.warning(f"Letterbox scan failed for video {video.id}: {e}")
            continue

    return results
