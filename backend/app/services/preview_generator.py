"""
Preview Generator — Create short looping preview clips for hover preview in UI.

Approach: Extract a short segment from the video at a configurable start point,
transcode to a web-friendly format (mp4/h264 + aac, low bitrate) for fast loading.
"""
import logging
import os
import subprocess

from app.subprocess_utils import HIDE_WINDOW
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)


def generate_preview(
    video_path: str,
    output_path: Optional[str] = None,
    start_percent: Optional[int] = None,
    duration_sec: Optional[int] = None,
    video_id: Optional[int] = None,
) -> Optional[str]:
    """
    Generate a short preview clip from a video.

    Args:
        video_path: Source video path
        output_path: Where to save the preview (auto-generated if None)
        start_percent: Start position as percentage of duration (0-100)
        duration_sec: Duration of preview in seconds
        video_id: Optional video_id to make cache key unique per video

    Returns:
        Path to generated preview file, or None on failure
    """
    settings = get_settings()
    ffmpeg = settings.resolved_ffmpeg
    ffprobe = settings.resolved_ffprobe

    if start_percent is None:
        start_percent = settings.preview_start_percent
    if duration_sec is None:
        duration_sec = settings.preview_duration_sec

    if output_path is None:
        os.makedirs(settings.preview_cache_dir, exist_ok=True)
        basename = os.path.splitext(os.path.basename(video_path))[0]
        # Include video_id in cache key to prevent collisions between
        # different videos with the same filename.
        if video_id is not None:
            output_path = os.path.join(
                settings.preview_cache_dir, f"v{video_id}_{basename}_preview.mp4"
            )
        else:
            output_path = os.path.join(
                settings.preview_cache_dir, f"{basename}_preview.mp4"
            )

    # Skip if preview already exists and source hasn't changed
    if os.path.isfile(output_path):
        try:
            source_mtime = os.path.getmtime(video_path)
            preview_mtime = os.path.getmtime(output_path)
            if preview_mtime >= source_mtime:
                return output_path
            logger.info(f"Regenerating stale preview (source newer): {output_path}")
        except OSError:
            pass  # If we can't stat, regenerate

    try:
        # Get video duration
        duration = _get_duration(ffprobe, video_path)
        if duration is None or duration <= 0:
            logger.error(f"Cannot determine duration for: {video_path}")
            return None

        # Calculate start time
        start_time = (duration * start_percent) / 100.0
        # Ensure we don't go past the end
        if start_time + duration_sec > duration:
            start_time = max(0, duration - duration_sec)

        # Generate preview: small resolution, low bitrate for fast loading
        cmd = [
            ffmpeg, "-y",
            "-ss", str(start_time),
            "-i", video_path,
            "-t", str(duration_sec),
            "-vf", "scale=480:-2",  # 480px wide, maintain aspect ratio
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "28",
            "-c:a", "aac",
            "-b:a", "64k",
            "-ac", "1",   # Mono for previews
            "-movflags", "+faststart",
            output_path,
        ]

        logger.debug(f"Generating preview: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, **HIDE_WINDOW)

        if result.returncode != 0:
            logger.error(f"Preview generation failed: {result.stderr[-300:]}")
            return None

        logger.info(f"Preview generated: {output_path}")
        return output_path

    except subprocess.TimeoutExpired:
        logger.error(f"Preview generation timed out for: {video_path}")
    except Exception as e:
        logger.error(f"Preview generation error: {e}")

    return None


def _get_duration(ffprobe: str, file_path: str) -> Optional[float]:
    """Get video duration in seconds using ffprobe."""
    cmd = [
        ffprobe,
        "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, **HIDE_WINDOW)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as e:
        logger.error(f"Duration probe error: {e}")

    return None


def cleanup_stale_previews(max_age_days: int = 30):
    """Remove preview files older than max_age_days."""
    settings = get_settings()
    preview_dir = settings.preview_cache_dir

    if not os.path.isdir(preview_dir):
        return

    import time
    cutoff = time.time() - (max_age_days * 86400)
    removed = 0

    for fname in os.listdir(preview_dir):
        fpath = os.path.join(preview_dir, fname)
        if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
            os.remove(fpath)
            removed += 1

    if removed:
        logger.info(f"Cleaned up {removed} stale preview files")


def delete_video_previews(video_id: int, basename: Optional[str] = None):
    """Delete all preview files for a specific video.

    Removes any file in the preview cache whose name starts with
    ``v{video_id}_`` (new naming convention) **and** any legacy file
    matching ``{basename}_preview.mp4`` (old naming convention without
    video_id prefix).
    """
    settings = get_settings()
    preview_dir = settings.preview_cache_dir
    if not os.path.isdir(preview_dir):
        return 0

    prefix = f"v{video_id}_"
    removed = 0
    for fname in os.listdir(preview_dir):
        fpath = os.path.join(preview_dir, fname)
        if not os.path.isfile(fpath):
            continue
        # Match new-style "v{id}_..._preview.mp4"
        if fname.startswith(prefix):
            try:
                os.remove(fpath)
                removed += 1
                logger.debug(f"Deleted preview: {fname}")
            except OSError as e:
                logger.warning(f"Failed to delete preview {fpath}: {e}")
            continue
        # Match old-style "{basename}_preview.mp4" (no v{id}_ prefix)
        if basename and fname == f"{basename}_preview.mp4":
            try:
                os.remove(fpath)
                removed += 1
                logger.debug(f"Deleted legacy preview: {fname}")
            except OSError as e:
                logger.warning(f"Failed to delete legacy preview {fpath}: {e}")

    if removed:
        logger.info(f"Deleted {removed} preview file(s) for video {video_id}")
    return removed


def purge_orphan_previews(valid_video_basenames: dict[int, str]):
    """Remove preview files whose video ID no longer exists or whose basename
    doesn't match the current video.

    Args:
        valid_video_basenames: Mapping of ``{video_id: file_basename}`` for
            every video currently in the database.  A preview ``v{id}_X_preview.mp4``
            is kept only if *id* is in this dict **and** *X* matches the
            corresponding basename.  This handles both deleted videos and
            recycled IDs (where the ID exists but belongs to a different video).
    """
    import re
    settings = get_settings()
    preview_dir = settings.preview_cache_dir
    if not os.path.isdir(preview_dir):
        return 0

    _ID_RE = re.compile(r"^v(\d+)_(.+?)_preview\.mp4$")
    removed = 0
    for fname in os.listdir(preview_dir):
        fpath = os.path.join(preview_dir, fname)
        if not os.path.isfile(fpath):
            continue

        m = _ID_RE.match(fname)
        if m:
            vid = int(m.group(1))
            preview_basename = m.group(2)
            expected_basename = valid_video_basenames.get(vid)
            if expected_basename is None or preview_basename != expected_basename:
                try:
                    os.remove(fpath)
                    removed += 1
                except OSError:
                    pass
        else:
            # Legacy preview without video_id prefix — remove as untrackable
            if fname.endswith("_preview.mp4"):
                try:
                    os.remove(fpath)
                    removed += 1
                except OSError:
                    pass

    if removed:
        logger.info(f"Purged {removed} orphan preview file(s)")
    return removed
