# AUTO-SEPARATED from services/media_analyzer.py for pipeline_url pipeline
# This file is independent — changes here do NOT affect the other pipeline.
"""
Media Analyzer — ffprobe-based media analysis + quality signature.
"""
import json
import logging
import subprocess
from typing import Optional, Dict, Any

from app.config import get_settings

logger = logging.getLogger(__name__)


def probe_file(file_path: str) -> Dict[str, Any]:
    """
    Run ffprobe on a file and return parsed JSON output.
    """
    settings = get_settings()
    ffprobe = settings.resolved_ffprobe

    cmd = [
        ffprobe,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        file_path,
    ]

    logger.debug(f"Running ffprobe: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")

    return json.loads(result.stdout)


def extract_quality_signature(file_path: str) -> Dict[str, Any]:
    """
    Analyze a media file and return a quality signature dict.

    Keys: width, height, fps, video_codec, video_bitrate, hdr,
          audio_codec, audio_bitrate, audio_sample_rate, audio_channels,
          container, duration_seconds
    """
    probe = probe_file(file_path)
    streams = probe.get("streams", [])
    fmt = probe.get("format", {})

    sig: Dict[str, Any] = {
        "width": None,
        "height": None,
        "fps": None,
        "video_codec": None,
        "video_bitrate": None,
        "hdr": False,
        "audio_codec": None,
        "audio_bitrate": None,
        "audio_sample_rate": None,
        "audio_channels": None,
        "container": None,
        "duration_seconds": None,
    }

    # Format-level info
    sig["container"] = fmt.get("format_name", "").split(",")[0] if fmt.get("format_name") else None
    if fmt.get("duration"):
        sig["duration_seconds"] = float(fmt["duration"])

    # Analyze streams
    for stream in streams:
        codec_type = stream.get("codec_type")

        if codec_type == "video":
            sig["width"] = stream.get("width")
            sig["height"] = stream.get("height")
            sig["video_codec"] = stream.get("codec_name")

            # FPS from avg_frame_rate (e.g. "30000/1001")
            avg_fr = stream.get("avg_frame_rate", "0/1")
            try:
                num, den = avg_fr.split("/")
                sig["fps"] = round(int(num) / int(den), 3) if int(den) else None
            except (ValueError, ZeroDivisionError):
                sig["fps"] = None

            # Video bitrate
            if stream.get("bit_rate"):
                sig["video_bitrate"] = int(stream["bit_rate"])
            elif fmt.get("bit_rate"):
                # Fallback to format-level bitrate
                sig["video_bitrate"] = int(fmt["bit_rate"])

            # HDR detection: look for color_transfer containing "smpte2084" or "arib-std-b67"
            color_transfer = stream.get("color_transfer", "")
            if color_transfer in ("smpte2084", "arib-std-b67"):
                sig["hdr"] = True

        elif codec_type == "audio":
            sig["audio_codec"] = stream.get("codec_name")
            if stream.get("bit_rate"):
                sig["audio_bitrate"] = int(stream["bit_rate"])
            if stream.get("sample_rate"):
                sig["audio_sample_rate"] = int(stream["sample_rate"])
            sig["audio_channels"] = stream.get("channels")

    return sig


def derive_resolution_label(height: Optional[int]) -> str:
    """
    Derive a human-readable resolution label from vertical resolution.
    e.g. 2160 -> "2160p", 1080 -> "1080p"
    """
    if not height:
        return "Unknown"

    standard_labels = {
        2160: "2160p",
        1440: "1440p",
        1080: "1080p",
        720: "720p",
        480: "480p",
        360: "360p",
        240: "240p",
    }

    # Find closest standard label
    closest = min(standard_labels.keys(), key=lambda k: abs(k - height))
    if abs(closest - height) <= 100:
        return standard_labels[closest]

    return f"{height}p"


def measure_loudness(file_path: str) -> Optional[float]:
    """
    Measure integrated loudness (LUFS) of a media file using ffmpeg ebur128 filter.
    Returns the integrated loudness value or None on failure.
    """
    settings = get_settings()
    ffmpeg = settings.resolved_ffmpeg

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-i", file_path,
        "-af", "ebur128=peak=true",
        "-f", "null",
        "-",
    ]

    logger.debug(f"Measuring loudness: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        # Parse stderr for integrated loudness
        for line in result.stderr.split("\n"):
            if "I:" in line and "LUFS" in line:
                parts = line.strip().split()
                for i, part in enumerate(parts):
                    if part == "I:":
                        try:
                            return float(parts[i + 1])
                        except (IndexError, ValueError):
                            continue

    except subprocess.TimeoutExpired:
        logger.error(f"Loudness measurement timed out for {file_path}")
    except Exception as e:
        logger.error(f"Error measuring loudness: {e}")

    return None


def compare_quality(current_sig: Dict[str, Any], remote_formats: list) -> bool:
    """
    Compare current stored quality signature against available remote formats.
    Returns True if a higher quality version is available.

    remote_formats: list of dicts from yt-dlp format listing, each with
        'height', 'vbr', 'abr', 'fps', 'vcodec', 'acodec', etc.
    """
    current_score = _compute_score(current_sig)

    for fmt in remote_formats:
        if fmt.get("vcodec", "none") == "none":
            continue  # Skip audio-only
        remote_score = _compute_score({
            "height": fmt.get("height"),
            "video_bitrate": int(fmt.get("vbr", 0) or 0) * 1000,  # yt-dlp reports kbps
            "fps": fmt.get("fps"),
            "hdr": False,  # Would need more parsing
        })
        if remote_score > current_score:
            return True

    return False


def _compute_score(sig: Dict[str, Any]) -> int:
    """Compute a numeric quality score from a signature dict."""
    score = 0
    height = sig.get("height") or 0
    score += height * 1000
    vbr = sig.get("video_bitrate") or 0
    score += vbr // 1000
    fps = sig.get("fps") or 0
    if fps > 30:
        score += 500
    if sig.get("hdr"):
        score += 2000
    return score
