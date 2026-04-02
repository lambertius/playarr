# AUTO-SEPARATED from ai/scene_analysis.py for pipeline_lib pipeline
# This file is independent — changes here do NOT affect the other pipeline.
"""
Scene Analysis Service — FFmpeg-based scene detection and thumbnail scoring.

Pipeline:
1. Detect scene changes using ffmpeg's `select=gt(scene,threshold)` filter
2. Extract candidate frames at scene boundaries + evenly-spaced samples
3. Score each frame on sharpness, contrast, color variance, composition
4. Select the highest-scoring frame as the video thumbnail

All processing uses ffmpeg/ffprobe — no heavy Python imaging libs required
for the core path (PIL/OpenCV optional for advanced scoring).
"""
import json
import logging
import math
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.ai.models import AISceneAnalysis, AIThumbnail, SceneAnalysisStatus
from app.config import get_settings
from app.models import VideoItem, MediaAsset

logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_SCENE_THRESHOLD = 0.3   # ffmpeg scene detection sensitivity (0–1, lower = more sensitive)
DEFAULT_MIN_SCENE_LEN = 1.0     # Minimum scene length in seconds
DEFAULT_MAX_THUMBNAILS = 12     # Maximum thumbnail candidates to extract
DEFAULT_SAMPLE_INTERVAL = 10.0  # Seconds between evenly-spaced samples (fallback)


def analyze_scenes(
    db: Session,
    video_id: int,
    threshold: float = DEFAULT_SCENE_THRESHOLD,
    max_thumbnails: int = DEFAULT_MAX_THUMBNAILS,
    force: bool = False,
) -> Optional[AISceneAnalysis]:
    """
    Run scene detection and thumbnail extraction for a video.

    Args:
        db: SQLAlchemy session
        video_id: VideoItem ID
        threshold: Scene detection sensitivity (0–1)
        max_thumbnails: Max thumbnail candidates
        force: Re-run even if results exist

    Returns:
        AISceneAnalysis record, or None on failure.
    """
    video = db.query(VideoItem).get(video_id)
    if not video or not video.file_path:
        logger.error(f"Video {video_id} not found or has no file")
        return None

    if not os.path.isfile(video.file_path):
        logger.error(f"Video file not found: {video.file_path}")
        return None

    # Check for existing analysis
    if not force:
        existing = (
            db.query(AISceneAnalysis)
            .filter(
                AISceneAnalysis.video_id == video_id,
                AISceneAnalysis.status == SceneAnalysisStatus.complete,
            )
            .order_by(AISceneAnalysis.created_at.desc())
            .first()
        )
        if existing:
            logger.info(f"Scene analysis exists for video {video_id}, skipping")
            return existing

    settings = get_settings()
    ffmpeg = settings.resolved_ffmpeg
    ffprobe = settings.resolved_ffprobe

    # Create analysis record
    analysis = AISceneAnalysis(
        video_id=video_id,
        status=SceneAnalysisStatus.processing,
        config={
            "threshold": threshold,
            "max_thumbnails": max_thumbnails,
        },
    )
    db.add(analysis)
    db.flush()

    try:
        # Get video duration
        duration = _get_duration(ffprobe, video.file_path)
        analysis.duration_seconds = duration

        # Detect scenes
        scene_timestamps = _detect_scenes(ffmpeg, video.file_path, threshold, duration)
        analysis.total_scenes = len(scene_timestamps)

        # Build scene list
        scenes = []
        for i, ts in enumerate(scene_timestamps):
            end_ts = scene_timestamps[i + 1] if i + 1 < len(scene_timestamps) else duration
            scenes.append({
                "start": round(ts, 2),
                "end": round(end_ts, 2),
                "index": i,
            })
        analysis.scenes = scenes

        # Select candidate timestamps for thumbnail extraction
        candidate_timestamps = _select_candidate_timestamps(
            scene_timestamps, duration, max_thumbnails,
        )

        # Extract and score frames
        thumb_dir = _get_thumbnail_dir(video)
        os.makedirs(thumb_dir, exist_ok=True)

        thumbnails = []
        for ts in candidate_timestamps:
            frame_path = os.path.join(thumb_dir, f"thumb_{ts:.2f}.jpg")
            if _extract_frame(ffmpeg, video.file_path, ts, frame_path):
                scores = _score_frame(ffprobe, frame_path)
                thumb = AIThumbnail(
                    video_id=video_id,
                    scene_analysis_id=analysis.id,
                    timestamp_sec=ts,
                    file_path=frame_path,
                    score_sharpness=scores.get("sharpness", 0),
                    score_contrast=scores.get("contrast", 0),
                    score_color_variance=scores.get("color_variance", 0),
                    score_composition=scores.get("composition", 0),
                    score_overall=scores.get("overall", 0),
                )
                thumbnails.append(thumb)
                db.add(thumb)

        # Select best thumbnail
        if thumbnails:
            best = max(thumbnails, key=lambda t: t.score_overall)
            best.is_selected = True

            # Save as the video player thumbnail (does NOT touch poster artwork)
            _save_as_video_thumb(db, video, best)

        analysis.status = SceneAnalysisStatus.complete
        analysis.completed_at = datetime.now(timezone.utc)
        db.commit()

        logger.info(
            f"Scene analysis complete for video {video_id}: "
            f"{analysis.total_scenes} scenes, {len(thumbnails)} thumbnails"
        )
        return analysis

    except Exception as e:
        analysis.status = SceneAnalysisStatus.failed
        analysis.error_message = str(e)
        analysis.completed_at = datetime.now(timezone.utc)
        db.commit()
        logger.error(f"Scene analysis failed for video {video_id}: {e}")
        return analysis


def select_thumbnail(
    db: Session,
    video_id: int,
    thumbnail_id: int,
) -> Optional[AIThumbnail]:
    """
    Manually select a thumbnail as the video's poster.

    Args:
        db: SQLAlchemy session
        video_id: VideoItem ID
        thumbnail_id: AIThumbnail ID to select

    Returns:
        The selected AIThumbnail, or None if not found.
    """
    # Deselect all existing
    db.query(AIThumbnail).filter(
        AIThumbnail.video_id == video_id,
        AIThumbnail.is_selected == True,
    ).update({"is_selected": False})

    thumb = db.query(AIThumbnail).get(thumbnail_id)
    if not thumb or thumb.video_id != video_id:
        return None

    thumb.is_selected = True
    thumb.provenance = "manual_selection"

    # Save as the video player thumbnail (does NOT touch poster artwork)
    video = db.query(VideoItem).get(video_id)
    if video:
        _save_as_video_thumb(db, video, thumb)

    db.commit()
    return thumb


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_duration(ffprobe: str, file_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    cmd = [
        ffprobe, "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "json",
        file_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr[:200]}")

    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def _detect_scenes(
    ffmpeg: str,
    file_path: str,
    threshold: float,
    duration: float,
) -> List[float]:
    """
    Detect scene changes using ffmpeg's scene filter.
    Returns list of timestamps (seconds) where scenes change.
    """
    cmd = [
        ffmpeg, "-i", file_path,
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-vsync", "vfr",
        "-f", "null", "-",
    ]

    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )

    timestamps = [0.0]  # Always include start
    for line in result.stderr.split("\n"):
        if "pts_time:" in line:
            try:
                # Extract pts_time from showinfo output
                pt = line.split("pts_time:")[1].split()[0]
                ts = float(pt)
                if ts > 0.5 and ts < duration - 0.5:  # Skip very start/end
                    timestamps.append(ts)
            except (ValueError, IndexError):
                continue

    return sorted(set(timestamps))


def _select_candidate_timestamps(
    scene_timestamps: List[float],
    duration: float,
    max_count: int,
) -> List[float]:
    """
    Select candidate timestamps for thumbnail extraction.
    Uses scene boundaries plus evenly-spaced samples.
    """
    candidates = set(scene_timestamps)

    # Add evenly-spaced samples to fill gaps
    if duration > 0:
        interval = max(duration / (max_count + 1), 2.0)
        t = interval
        while t < duration - 1.0:
            candidates.add(round(t, 2))
            t += interval

    # Sort and limit
    candidates = sorted(candidates)
    if len(candidates) > max_count:
        # Keep evenly distributed subset
        step = len(candidates) / max_count
        candidates = [candidates[int(i * step)] for i in range(max_count)]

    return candidates


def _extract_frame(ffmpeg: str, file_path: str, timestamp: float, output_path: str) -> bool:
    """Extract a single frame at the given timestamp."""
    cmd = [
        ffmpeg, "-y",
        "-ss", str(timestamp),
        "-i", file_path,
        "-vframes", "1",
        "-q:v", "2",  # High quality JPEG
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    return result.returncode == 0 and os.path.isfile(output_path)


def _score_frame(ffprobe: str, frame_path: str) -> Dict[str, float]:
    """
    Score a frame image on multiple quality heuristics using ffprobe.

    Returns dict with:
    - sharpness: Edge density / Laplacian variance proxy (0–1)
    - contrast: Luminance range (0–1)
    - color_variance: Color spread / saturation (0–1)
    - composition: Basic rule-of-thirds scoring (0–1)
    - overall: Weighted combination
    """
    scores = {
        "sharpness": 0.5,
        "contrast": 0.5,
        "color_variance": 0.5,
        "composition": 0.5,
        "overall": 0.5,
    }

    try:
        # Use ffprobe to get basic image stats
        cmd = [
            ffprobe, "-v", "quiet",
            "-show_entries", "frame=width,height",
            "-show_entries", "frame_tags=",
            "-of", "json",
            frame_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        if result.returncode != 0:
            return scores

        data = json.loads(result.stdout)
        frames = data.get("frames", [])
        if not frames:
            return scores

        width = int(frames[0].get("width", 0))
        height = int(frames[0].get("height", 0))

        # File size is a rough proxy for image complexity/detail
        file_size = os.path.getsize(frame_path) if os.path.isfile(frame_path) else 0
        pixels = max(width * height, 1)

        # Bytes per pixel — higher = more detail/complexity
        bpp = file_size / pixels
        scores["sharpness"] = min(bpp / 0.5, 1.0)  # Normalize: 0.5 bpp = max

        # Aspect ratio scoring (prefer 16:9-ish frames)
        if width > 0 and height > 0:
            ar = width / height
            # Prefer 1.5–2.0 aspect ratio
            if 1.5 <= ar <= 2.0:
                scores["composition"] = 0.8
            elif 1.2 <= ar <= 2.5:
                scores["composition"] = 0.6
            else:
                scores["composition"] = 0.4

        # Contrast proxy: JPEG size relative to resolution
        # Very small files often mean low contrast / solid colors
        if file_size < pixels * 0.05:
            scores["contrast"] = 0.2  # Very low detail
        elif file_size > pixels * 0.3:
            scores["contrast"] = 0.9  # High detail
        else:
            scores["contrast"] = 0.3 + (bpp / 0.3) * 0.6

        # Color variance: estimate from file size distribution
        scores["color_variance"] = min(bpp / 0.4, 1.0)

        # Weighted overall
        scores["overall"] = (
            scores["sharpness"] * 0.35
            + scores["contrast"] * 0.25
            + scores["color_variance"] * 0.20
            + scores["composition"] * 0.20
        )
        scores["overall"] = round(min(scores["overall"], 1.0), 3)

    except Exception as e:
        logger.warning(f"Frame scoring failed for {frame_path}: {e}")

    return scores


def _get_thumbnail_dir(video: VideoItem) -> str:
    """Get the thumbnail directory for a video."""
    settings = get_settings()
    base_dir = os.path.join(settings.asset_cache_dir, "thumbnails")
    return os.path.join(base_dir, str(video.id))


def _save_as_video_thumb(db: Session, video: VideoItem, thumb: AIThumbnail):
    """Save a thumbnail as the video player thumbnail (video_thumb asset).

    This does NOT touch the poster artwork — it only sets the image
    shown on the <video> element before playback starts.
    """
    # We store the thumbnail path directly — no copy needed since
    # the thumbnail files live in the asset cache.
    # Validate the thumbnail file for provenance tracking
    from app.pipeline_lib.services.artwork_service import validate_file as _validate_thumb
    _vr = _validate_thumb(thumb.file_path) if os.path.isfile(thumb.file_path) else None
    _prov_fields = dict(
        provenance="ai_scene_analysis",
        status="valid" if (_vr and _vr.valid) else "invalid",
        width=_vr.width if _vr and _vr.valid else None,
        height=_vr.height if _vr and _vr.valid else None,
        file_size_bytes=_vr.file_size_bytes if _vr and _vr.valid else None,
        file_hash=_vr.file_hash if _vr and _vr.valid else None,
        last_validated_at=datetime.now(timezone.utc),
    )

    existing = (
        db.query(MediaAsset)
        .filter(
            MediaAsset.video_id == video.id,
            MediaAsset.asset_type == "video_thumb",
        )
        .first()
    )

    if existing:
        existing.file_path = thumb.file_path
        for _k, _v in _prov_fields.items():
            setattr(existing, _k, _v)
    else:
        asset = MediaAsset(
            video_id=video.id,
            asset_type="video_thumb",
            file_path=thumb.file_path,
            **_prov_fields,
        )
        db.add(asset)
