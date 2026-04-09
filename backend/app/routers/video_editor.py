"""
Video Editor Router — API endpoints for the video editor feature.

Provides:
- Queue management (add/remove/list items)
- Letterbox scan (detect black bars across library)
- Crop preview calculation
- Encode (apply edits)
"""
import logging
import os
import shutil
import threading
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.models import VideoItem, QualitySignature, ProcessingJob, JobStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/video-editor", tags=["Video Editor"])

# Video extensions for archive file matching
_VIDEO_EXTS = {".mkv", ".mp4", ".webm", ".avi", ".mov", ".flv", ".wmv", ".m4v"}
_MANIFEST_NAME = ".playarr-archive.json"


def _file_checksum(path: str, algo: str = "md5") -> str:
    """Compute a hex checksum of a file.  Uses 64 KB chunks for large files."""
    import hashlib
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65_536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def write_archive_manifest(
    archive_video_path: str,
    original_library_path: str,
    library_dir: str,
    video_id: Optional[int] = None,
    artist: str = "",
    title: str = "",
    archive_reason: str = "edit",
) -> None:
    """Write a manifest alongside an archived video for later re-linking.

    Args:
        archive_reason: Why this file was archived — "edit" (video editor crop/encode)
                        or "redownload" (replaced by a fresh download).
    """
    import json
    try:
        manifest = {
            "checksum_md5": _file_checksum(archive_video_path),
            "original_relative_path": os.path.relpath(original_library_path, library_dir)
                                      if original_library_path.startswith(os.path.normpath(library_dir))
                                      else os.path.basename(original_library_path),
            "video_id": video_id,
            "artist": artist,
            "title": title,
            "file_size_bytes": os.path.getsize(archive_video_path),
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "archive_reason": archive_reason,
        }
        manifest_path = os.path.join(os.path.dirname(archive_video_path), _MANIFEST_NAME)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to write archive manifest: {e}")


def find_archive_file(file_path: str, library_dir: str, archive_dir: str) -> Optional[str]:
    """Locate the archived original for a library file.

    Search strategy (returns first hit):
      1. Exact relative-path match in archive_dir
      2. Any video file in the expected archive subfolder
      3. Manifest-based scan: walk archive_dir looking for .playarr-archive.json
         files whose original_relative_path matches the library file path

    If file_path lives under a source directory rather than library_dir,
    the _archive subdir of that source directory is checked instead.
    """
    if not file_path:
        return None

    # Determine the correct archive_dir for this file
    from app.config import get_settings
    _s = get_settings()
    actual_library_dir = library_dir
    actual_archive_dir = archive_dir
    norm_fp = os.path.normpath(file_path)
    for lib_root in _s.get_all_library_dirs():
        norm_root = os.path.normcase(os.path.normpath(lib_root))
        if os.path.normcase(norm_fp).startswith(norm_root + os.sep):
            actual_library_dir = lib_root
            actual_archive_dir = os.path.join(lib_root, "_archive")
            break

    if not actual_archive_dir or not os.path.isdir(actual_archive_dir):
        return None

    library_root = os.path.normpath(actual_library_dir)
    norm_fp = os.path.normpath(file_path)
    if norm_fp.startswith(library_root + os.sep):
        rel = os.path.relpath(norm_fp, library_root)
    else:
        rel = os.path.basename(file_path)

    # 1) Exact match (same extension and name)
    archive_path = os.path.join(actual_archive_dir, rel)
    if os.path.isfile(archive_path):
        return archive_path

    # 2) Check archive folder for ANY video file (handles extension + name changes)
    archive_folder = os.path.dirname(archive_path)
    if os.path.isdir(archive_folder):
        for fname in os.listdir(archive_folder):
            if os.path.splitext(fname)[1].lower() in _VIDEO_EXTS:
                return os.path.join(archive_folder, fname)

    # 3) Manifest-based fallback: scan archive_dir for matching manifests
    import json
    rel_norm = os.path.normpath(rel)
    try:
        for root, _dirs, fnames in os.walk(actual_archive_dir):
            if _MANIFEST_NAME not in fnames:
                continue
            manifest_path = os.path.join(root, _MANIFEST_NAME)
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                if os.path.normpath(manifest.get("original_relative_path", "")) == rel_norm:
                    # Found a matching manifest — return the video file in this folder
                    for fn in os.listdir(root):
                        if os.path.splitext(fn)[1].lower() in _VIDEO_EXTS:
                            return os.path.join(root, fn)
            except (json.JSONDecodeError, OSError):
                continue
    except OSError:
        pass

    return None


def find_all_archive_files(file_path: str, library_dir: str, archive_dir: str) -> list:
    """Locate ALL archived versions for a library file, with reason metadata.

    Returns a list of dicts:
        [{"path": str, "reason": "edit"|"redownload", "archived_at": str, "file_size_bytes": int}, ...]
    """
    import json
    if not file_path:
        return []

    from app.config import get_settings
    _s = get_settings()
    actual_library_dir = library_dir
    actual_archive_dir = archive_dir
    norm_fp = os.path.normpath(file_path)
    for lib_root in _s.get_all_library_dirs():
        norm_root = os.path.normcase(os.path.normpath(lib_root))
        if os.path.normcase(norm_fp).startswith(norm_root + os.sep):
            actual_library_dir = lib_root
            actual_archive_dir = os.path.join(lib_root, "_archive")
            break

    if not actual_archive_dir or not os.path.isdir(actual_archive_dir):
        return []

    library_root = os.path.normpath(actual_library_dir)
    norm_fp_val = os.path.normpath(file_path)
    if norm_fp_val.startswith(library_root + os.sep):
        rel = os.path.relpath(norm_fp_val, library_root)
    else:
        rel = os.path.basename(file_path)
    rel_norm = os.path.normpath(rel)

    results = []

    # Scan all manifest files in archive_dir
    try:
        for root, _dirs, fnames in os.walk(actual_archive_dir):
            if _MANIFEST_NAME not in fnames:
                continue
            manifest_path = os.path.join(root, _MANIFEST_NAME)
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                if os.path.normpath(manifest.get("original_relative_path", "")) == rel_norm:
                    for fn in os.listdir(root):
                        if os.path.splitext(fn)[1].lower() in _VIDEO_EXTS:
                            results.append({
                                "path": os.path.join(root, fn),
                                "reason": manifest.get("archive_reason", "edit"),
                                "archived_at": manifest.get("archived_at", ""),
                                "file_size_bytes": manifest.get("file_size_bytes", 0),
                            })
                            break
            except (json.JSONDecodeError, OSError):
                continue
    except OSError:
        pass

    # Also check exact path match (archives without manifests)
    archive_path = os.path.join(actual_archive_dir, rel)
    if os.path.isfile(archive_path):
        already = any(os.path.normpath(r["path"]) == os.path.normpath(archive_path) for r in results)
        if not already:
            results.append({
                "path": archive_path,
                "reason": "edit",  # legacy archives without manifest are from editor
                "archived_at": "",
                "file_size_bytes": os.path.getsize(archive_path),
            })

    return results


# ── Pydantic Schemas ──────────────────────────────────────

class EditorQueueItem(BaseModel):
    video_id: int
    artist: str
    title: str
    album: Optional[str] = None
    file_path: Optional[str] = None
    resolution_label: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None
    video_codec: Optional[str] = None
    video_bitrate: Optional[int] = None
    fps: Optional[float] = None
    audio_codec: Optional[str] = None
    audio_bitrate: Optional[int] = None
    audio_channels: Optional[int] = None
    # Letterbox info
    letterbox_detected: bool = False
    crop_w: Optional[int] = None
    crop_h: Optional[int] = None
    crop_x: Optional[int] = None
    crop_y: Optional[int] = None
    bar_top: int = 0
    bar_bottom: int = 0
    bar_left: int = 0
    bar_right: int = 0
    has_archive: bool = False
    exclude_from_scan: bool = False
    created_at: Optional[str] = None  # ISO timestamp — when video was added to library


class AddToEditorRequest(BaseModel):
    video_ids: List[int]


class LetterboxScanRequest(BaseModel):
    limit: int = Field(default=200, ge=1, le=2000)
    include_excluded: bool = False


class CropPreviewRequest(BaseModel):
    video_id: int
    # If ratio provided, compute crop for that ratio
    ratio: Optional[str] = None  # e.g. "16:9", "4:3", or "custom"
    custom_ratio_w: Optional[float] = None
    custom_ratio_h: Optional[float] = None
    # Or explicit crop values
    crop_w: Optional[int] = None
    crop_h: Optional[int] = None
    crop_x: Optional[int] = None
    crop_y: Optional[int] = None


class CropPreviewResponse(BaseModel):
    video_id: int
    original_w: int
    original_h: int
    crop_w: int
    crop_h: int
    crop_x: int
    crop_y: int
    effective_ratio: str


class EncodeRequest(BaseModel):
    video_id: int
    crop_w: Optional[int] = None
    crop_h: Optional[int] = None
    crop_x: Optional[int] = None
    crop_y: Optional[int] = None
    target_dar: Optional[str] = None  # e.g. "16:9", "4:3" — sets display aspect ratio without cropping
    crf: int = Field(default=18, ge=0, le=51)
    preset: str = "medium"
    audio_passthrough: bool = True
    # Trim (seconds from start / before end)
    trim_start: Optional[float] = None
    trim_end: Optional[float] = None
    # Audio re-encode settings (used when trim is active or audio_passthrough=False)
    audio_codec: Optional[str] = None   # "aac", "opus", "flac" or None for auto
    audio_bitrate: Optional[str] = None  # e.g. "192k", "128k", None = match source


class BatchEncodeRequest(BaseModel):
    items: List[EncodeRequest]


class ExcludeFromScanRequest(BaseModel):
    video_id: int
    exclude: bool = True


class LetterboxScanResult(BaseModel):
    video_id: int
    artist: str
    title: str
    file_path: Optional[str] = None
    original_w: int
    original_h: int
    crop_w: int
    crop_h: int
    crop_x: int
    crop_y: int
    bar_top: int
    bar_bottom: int
    bar_left: int
    bar_right: int


class DetectLetterboxResponse(BaseModel):
    video_id: int
    detected: bool
    original_w: Optional[int] = None
    original_h: Optional[int] = None
    crop_w: Optional[int] = None
    crop_h: Optional[int] = None
    crop_x: Optional[int] = None
    crop_y: Optional[int] = None
    bar_top: int = 0
    bar_bottom: int = 0
    bar_left: int = 0
    bar_right: int = 0


# ── Endpoints ─────────────────────────────────────────────

@router.get("/queue", response_model=List[EditorQueueItem])
def get_editor_queue(
    video_ids: str = Query(..., description="Comma-separated list of video IDs"),
    db: Session = Depends(get_db),
):
    """Get video details for items in the editor queue.

    The queue itself is managed client-side; this just fetches enriched data.
    """
    try:
        ids = [int(x.strip()) for x in video_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(400, "Invalid video_ids format")

    if not ids:
        return []

    videos = (
        db.query(VideoItem)
        .filter(VideoItem.id.in_(ids))
        .all()
    )

    from app.config import get_settings as _get_settings
    _settings = _get_settings()
    library_root = os.path.normpath(_settings.library_dir)

    result = []
    for v in videos:
        qs = db.query(QualitySignature).filter(QualitySignature.video_id == v.id).first()

        # Check if archived original exists (extension-agnostic)
        archive_file = find_archive_file(v.file_path, _settings.library_dir, _settings.archive_dir) if v.file_path else None

        result.append(EditorQueueItem(
            video_id=v.id,
            artist=v.artist,
            title=v.title,
            album=v.album,
            file_path=v.file_path,
            created_at=v.created_at.isoformat() if v.created_at else None,
            resolution_label=v.resolution_label,
            width=qs.width if qs else None,
            height=qs.height if qs else None,
            duration_seconds=qs.duration_seconds if qs else None,
            video_codec=qs.video_codec if qs else None,
            video_bitrate=qs.video_bitrate if qs else None,
            fps=qs.fps if qs else None,
            audio_codec=qs.audio_codec if qs else None,
            audio_bitrate=qs.audio_bitrate if qs else None,
            audio_channels=qs.audio_channels if qs else None,
            letterbox_detected=qs.letterbox_detected if qs and qs.letterbox_scanned else False,
            crop_w=qs.letterbox_crop_w if qs and qs.letterbox_detected else None,
            crop_h=qs.letterbox_crop_h if qs and qs.letterbox_detected else None,
            crop_x=qs.letterbox_crop_x if qs and qs.letterbox_detected else None,
            crop_y=qs.letterbox_crop_y if qs and qs.letterbox_detected else None,
            bar_top=qs.letterbox_bar_top or 0 if qs and qs.letterbox_detected else 0,
            bar_bottom=qs.letterbox_bar_bottom or 0 if qs and qs.letterbox_detected else 0,
            bar_left=qs.letterbox_bar_left or 0 if qs and qs.letterbox_detected else 0,
            bar_right=qs.letterbox_bar_right or 0 if qs and qs.letterbox_detected else 0,
            has_archive=archive_file is not None,
            exclude_from_scan=v.exclude_from_editor_scan,
        ))

    return result


@router.post("/detect-letterbox", response_model=DetectLetterboxResponse)
def detect_letterbox_single(video_id: int = Query(...), db: Session = Depends(get_db)):
    """Detect letterboxing on a single video and persist results."""
    from app.services.video_editor import detect_letterbox

    video = db.query(VideoItem).get(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    if not video.file_path or not os.path.isfile(video.file_path):
        raise HTTPException(400, "Video file not found on disk")

    info = detect_letterbox(video.file_path)

    # Persist to QualitySignature
    qs = db.query(QualitySignature).filter(QualitySignature.video_id == video_id).first()
    if qs:
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
        db.commit()

    return DetectLetterboxResponse(video_id=video_id, **info)


@router.post("/scan-letterbox")
def scan_library_letterbox(
    req: LetterboxScanRequest,
    db: Session = Depends(get_db),
):
    """Scan library for videos with letterboxing. Returns matching videos.

    This runs synchronously for small batches. For large libraries,
    a background job is created.
    """
    if req.limit <= 20:
        # Small scan — run inline
        from app.services.video_editor import scan_library_for_letterboxing
        results = scan_library_for_letterboxing(db, limit=req.limit, include_excluded=req.include_excluded)
        return {"status": "complete", "results": results, "total_scanned": req.limit}
    else:
        # Large scan — create background job
        job = ProcessingJob(
            job_type="video_editor_scan",
            status=JobStatus.queued,
            display_name="Letterbox Scan",
            action_label="Letterbox Scan",
            input_params={"limit": req.limit, "include_excluded": req.include_excluded},
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        # Run in background thread
        def _run_scan(job_id: int, limit: int, include_excluded: bool):
            from app.services.video_editor import scan_library_for_letterboxing
            sdb = SessionLocal()
            try:
                j = sdb.query(ProcessingJob).get(job_id)
                j.status = JobStatus.analyzing
                j.started_at = datetime.now(timezone.utc)
                j.current_step = "Scanning for letterboxing..."
                sdb.commit()

                results = scan_library_for_letterboxing(sdb, limit=limit, include_excluded=include_excluded)

                j.status = JobStatus.complete
                j.completed_at = datetime.now(timezone.utc)
                j.progress_percent = 100
                j.current_step = f"Found {len(results)} videos with letterboxing"
                j.input_params = {"limit": limit, "results": results}
                sdb.commit()
            except Exception as e:
                j = sdb.query(ProcessingJob).get(job_id)
                j.status = JobStatus.failed
                j.error_message = str(e)[:2000]
                j.completed_at = datetime.now(timezone.utc)
                sdb.commit()
            finally:
                sdb.close()

        t = threading.Thread(target=_run_scan, args=(job.id, req.limit, req.include_excluded), daemon=True)
        t.start()

        return {"status": "scanning", "job_id": job.id}


@router.post("/crop-preview", response_model=CropPreviewResponse)
def crop_preview(req: CropPreviewRequest, db: Session = Depends(get_db)):
    """Calculate crop geometry for a given ratio or explicit crop values."""
    from app.services.video_editor import compute_crop_for_ratio, ASPECT_RATIOS

    video = db.query(VideoItem).get(req.video_id)
    if not video:
        raise HTTPException(404, "Video not found")

    qs = db.query(QualitySignature).filter(QualitySignature.video_id == req.video_id).first()
    if not qs or not qs.width or not qs.height:
        raise HTTPException(400, "Video has no quality signature / dimensions")

    original_w, original_h = qs.width, qs.height

    if req.crop_w is not None and req.crop_h is not None:
        # Explicit crop
        crop_w = min(req.crop_w, original_w)
        crop_h = min(req.crop_h, original_h)
        crop_x = req.crop_x or (original_w - crop_w) // 2
        crop_y = req.crop_y or (original_h - crop_h) // 2
    elif req.ratio:
        if req.ratio == "original":
            crop_w, crop_h, crop_x, crop_y = original_w, original_h, 0, 0
        elif req.ratio == "custom" and req.custom_ratio_w and req.custom_ratio_h:
            crop = compute_crop_for_ratio(original_w, original_h, req.custom_ratio_w, req.custom_ratio_h)
            crop_w, crop_h, crop_x, crop_y = crop["crop_w"], crop["crop_h"], crop["crop_x"], crop["crop_y"]
        elif req.ratio in ASPECT_RATIOS:
            rw, rh = ASPECT_RATIOS[req.ratio]
            crop = compute_crop_for_ratio(original_w, original_h, rw, rh)
            crop_w, crop_h, crop_x, crop_y = crop["crop_w"], crop["crop_h"], crop["crop_x"], crop["crop_y"]
        else:
            raise HTTPException(400, f"Unknown ratio: {req.ratio}")
    else:
        crop_w, crop_h, crop_x, crop_y = original_w, original_h, 0, 0

    # Compute the effective aspect ratio string
    from math import gcd
    g = gcd(crop_w, crop_h)
    eff_ratio = f"{crop_w // g}:{crop_h // g}"

    return CropPreviewResponse(
        video_id=req.video_id,
        original_w=original_w,
        original_h=original_h,
        crop_w=crop_w,
        crop_h=crop_h,
        crop_x=crop_x,
        crop_y=crop_y,
        effective_ratio=eff_ratio,
    )


def _run_encode_job(job_id: int, video_id: int, input_path: str, crop_params, target_dar, crf, preset, audio_pt,
                    trim_start=None, trim_end=None, audio_codec=None, audio_bitrate=None):
    """Execute a single encode job (designed to run in a background thread)."""
    from app.services.video_editor import encode_video
    from app.services.media_analyzer import extract_quality_signature

    sdb = SessionLocal()
    try:
        j = sdb.query(ProcessingJob).get(job_id)
        j.status = JobStatus.remuxing
        j.started_at = datetime.now(timezone.utc)
        j.current_step = "Encoding video..."
        sdb.commit()

        # Output to temp file next to original — always use .mp4 for H.264
        base, ext = os.path.splitext(input_path)
        temp_output = f"{base}_edited.mp4"
        final_path = f"{base}.mp4" if ext.lower() != ".mp4" else input_path

        def _progress(pct):
            j2 = sdb.query(ProcessingJob).get(job_id)
            j2.progress_percent = int(pct)
            j2.current_step = f"Encoding... {int(pct)}%"
            sdb.commit()

        stats = encode_video(
            input_path=input_path,
            output_path=temp_output,
            crop=crop_params,
            target_dar=target_dar,
            crf=crf,
            preset=preset,
            audio_passthrough=audio_pt,
            trim_start=trim_start,
            trim_end=trim_end,
            audio_codec=audio_codec,
            audio_bitrate=audio_bitrate,
            progress_callback=_progress,
        )

        # Verify output exists and is valid
        if not os.path.isfile(temp_output) or os.path.getsize(temp_output) < 1024:
            raise RuntimeError("Encoded output file is missing or too small")

        # Archive original to the _archive subdirectory of the
        # library root that contains the file
        from app.config import get_settings as _get_settings
        _settings = _get_settings()

        # Determine which library root this file belongs to
        _archive_dir = _settings.archive_dir  # default
        _lib_root = _settings.library_dir
        norm_input = os.path.normpath(input_path)
        for _lr in _settings.get_all_library_dirs():
            _nr = os.path.normcase(os.path.normpath(_lr))
            if os.path.normcase(norm_input).startswith(_nr + os.sep):
                _archive_dir = os.path.join(_lr, "_archive")
                _lib_root = _lr
                break

        library_root = os.path.normpath(_lib_root)
        if norm_input.startswith(library_root + os.sep):
            rel = os.path.relpath(norm_input, library_root)
        else:
            rel = os.path.basename(input_path)
        archive_path = os.path.join(_archive_dir, rel)
        os.makedirs(os.path.dirname(archive_path), exist_ok=True)
        shutil.move(input_path, archive_path)

        # Write archive manifest for re-linking if archive_dir changes
        v_pre = sdb.query(VideoItem).get(video_id)

        # Determine archive reason from encode parameters
        has_crop = crop_params is not None
        has_trim = trim_start is not None or trim_end is not None
        if has_crop and has_trim:
            reason = "both"
        elif has_crop:
            reason = "crop"
        elif has_trim:
            reason = "trim"
        else:
            reason = "edit"

        write_archive_manifest(
            archive_video_path=archive_path,
            original_library_path=norm_input,
            library_dir=_settings.library_dir,
            video_id=video_id,
            artist=v_pre.artist if v_pre else "",
            title=v_pre.title if v_pre else "",
            archive_reason=reason,
        )

        shutil.move(temp_output, final_path)

        # If the extension changed (e.g. .mkv -> .mp4), update DB path
        v = sdb.query(VideoItem).get(video_id)
        if final_path != input_path:
            v.file_path = final_path

        # Re-analyze quality signature
        try:
            new_sig = extract_quality_signature(final_path)
            qs = sdb.query(QualitySignature).filter(QualitySignature.video_id == video_id).first()
            if qs:
                for k, val in new_sig.items():
                    setattr(qs, k, val)
            v.file_size_bytes = os.path.getsize(final_path)
            if new_sig.get("height"):
                v.resolution_label = f"{new_sig['height']}p"
            sdb.commit()
        except Exception as e:
            logger.warning(f"Post-encode analysis failed: {e}")

        j = sdb.query(ProcessingJob).get(job_id)
        j.status = JobStatus.complete
        j.completed_at = datetime.now(timezone.utc)
        j.progress_percent = 100
        j.current_step = "Encode complete"

        # Build detailed encode summary
        input_bytes = stats['input_size_bytes']
        output_bytes = stats['output_size_bytes']
        size_ratio = output_bytes / input_bytes if input_bytes > 0 else 0
        size_change = "smaller" if size_ratio < 1 else "larger"
        size_pct = abs(1 - size_ratio) * 100

        summary_lines = [
            f"Encoded in {stats['elapsed_seconds']}s",
            f"",
            f"Resolution: {stats.get('source_w', '?')}x{stats.get('source_h', '?')} → {stats.get('output_w', '?')}x{stats.get('output_h', '?')}",
        ]
        src_br = stats.get('source_video_bitrate')
        out_br = stats.get('output_video_bitrate')
        if src_br and out_br:
            summary_lines.append(f"Video bitrate: {src_br // 1000:,}k → {out_br // 1000:,}k")
        summary_lines.extend([
            f"File size: {input_bytes:,} → {output_bytes:,} bytes ({size_pct:.0f}% {size_change})",
            f"",
            f"Original archived to: {archive_path}",
        ])
        j.log_text = "\n".join(summary_lines)
        sdb.commit()

    except Exception as e:
        logger.error(f"Encode job {job_id} failed: {e}", exc_info=True)
        # Use a fresh session to guarantee the failure status is persisted,
        # even if the original session is in a bad state after a rollback.
        sdb.close()
        sdb = SessionLocal()
        try:
            j = sdb.query(ProcessingJob).get(job_id)
            j.status = JobStatus.failed
            j.error_message = str(e)[:2000]
            j.completed_at = datetime.now(timezone.utc)
            sdb.commit()
        except Exception:
            logger.error(f"Failed to mark encode job {job_id} as failed", exc_info=True)
        finally:
            sdb.close()
        return
    finally:
        sdb.close()


@router.post("/encode")
def encode_single(req: EncodeRequest, db: Session = Depends(get_db)):
    """Encode a single video with the specified settings.

    Creates a background job for the encode operation.
    """
    video = db.query(VideoItem).get(req.video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    if not video.file_path or not os.path.isfile(video.file_path):
        raise HTTPException(400, "Video file not found on disk")

    crop = None
    if req.crop_w is not None and req.crop_h is not None:
        crop = {
            "crop_w": req.crop_w,
            "crop_h": req.crop_h,
            "crop_x": req.crop_x or 0,
            "crop_y": req.crop_y or 0,
        }

    job = ProcessingJob(
        video_id=video.id,
        job_type="video_editor_encode",
        status=JobStatus.queued,
        display_name=f"{video.artist} \u2013 {video.title} \u203a Video Edit",
        action_label="Video Edit",
        input_params={
            "crop": crop,
            "target_dar": req.target_dar,
            "crf": req.crf,
            "preset": req.preset,
            "audio_passthrough": req.audio_passthrough,
            "trim_start": req.trim_start,
            "trim_end": req.trim_end,
            "audio_codec": req.audio_codec,
            "audio_bitrate": req.audio_bitrate,
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    t = threading.Thread(
        target=_run_encode_job,
        args=(job.id, video.id, video.file_path, crop, req.target_dar, req.crf, req.preset, req.audio_passthrough),
        kwargs={"trim_start": req.trim_start, "trim_end": req.trim_end,
                "audio_codec": req.audio_codec, "audio_bitrate": req.audio_bitrate},
        daemon=True,
    )
    t.start()

    return {"job_id": job.id, "message": "Encode job started"}


@router.post("/restore-from-archive")
def restore_from_archive(video_id: int = Query(...), db: Session = Depends(get_db)):
    """Delete the encoded video and restore the original from the archive."""
    from app.config import get_settings as _get_settings
    from app.services.media_analyzer import extract_quality_signature

    video = db.query(VideoItem).get(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    if not video.file_path:
        raise HTTPException(400, "Video has no file path")

    _settings = _get_settings()
    archive_file = find_archive_file(video.file_path, _settings.library_dir, _settings.archive_dir)

    if not archive_file:
        raise HTTPException(404, "Archived original not found")

    # Kill any active streaming processes (ffmpeg remux/transcode) holding the file
    from app.routers.playback import kill_streams_for_file
    kill_streams_for_file(video.file_path)

    # Delete the encoded file at the library location
    if os.path.isfile(video.file_path):
        import time
        for attempt in range(5):
            try:
                os.remove(video.file_path)
                break
            except PermissionError:
                if attempt < 4:
                    time.sleep(0.5)
                else:
                    raise HTTPException(
                        409,
                        "Cannot delete encoded file — it is currently in use. "
                        "Stop playback and try again."
                    )

    # Determine restored file path — extension may differ from current
    archive_ext = os.path.splitext(archive_file)[1]
    current_ext = os.path.splitext(video.file_path)[1]
    if archive_ext.lower() != current_ext.lower():
        # Extension changed during encoding, restore with original extension
        restored_path = os.path.splitext(video.file_path)[0] + archive_ext
    else:
        restored_path = video.file_path

    # Restore original from archive
    os.makedirs(os.path.dirname(restored_path), exist_ok=True)
    shutil.move(archive_file, restored_path)

    # Clean up archive manifest if it exists
    archive_manifest = os.path.join(os.path.dirname(archive_file), _MANIFEST_NAME)
    if os.path.isfile(archive_manifest):
        try:
            os.remove(archive_manifest)
        except OSError:
            pass
    # Remove empty archive subfolder
    archive_subdir = os.path.dirname(archive_file)
    if os.path.isdir(archive_subdir):
        try:
            os.rmdir(archive_subdir)  # only removes if empty
        except OSError:
            pass

    # Update DB file_path if extension changed
    if restored_path != video.file_path:
        video.file_path = restored_path
        # Also update folder name if the extension is in the folder name
        folder_name = os.path.basename(os.path.dirname(restored_path))
        video.folder_path = os.path.dirname(restored_path)

    # Re-analyze quality signature
    try:
        new_sig = extract_quality_signature(video.file_path)
        qs = db.query(QualitySignature).filter(QualitySignature.video_id == video_id).first()
        if qs:
            for k, val in new_sig.items():
                setattr(qs, k, val)
        video.file_size_bytes = os.path.getsize(video.file_path)
        if new_sig.get("height"):
            video.resolution_label = f"{new_sig['height']}p"
        db.commit()
    except Exception as e:
        logger.warning(f"Post-restore analysis failed: {e}")

    logger.info(f"Restored video {video_id} from archive: {archive_file} -> {video.file_path}")
    return {"message": "Original restored from archive", "archive_path": archive_file}


@router.post("/exclude-from-scan")
def set_exclude_from_scan(req: ExcludeFromScanRequest, db: Session = Depends(get_db)):
    """Mark a video to be excluded from (or re-included in) future letterbox scans."""
    video = db.query(VideoItem).get(req.video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    video.exclude_from_editor_scan = req.exclude
    db.commit()
    action = "excluded from" if req.exclude else "re-included in"
    logger.info(f"Video {req.video_id} {action} editor scans")
    return {"video_id": req.video_id, "exclude_from_scan": req.exclude}


@router.post("/batch-encode")
def batch_encode(req: BatchEncodeRequest, db: Session = Depends(get_db)):
    """Start encode jobs for multiple videos (runs sequentially, not all at once)."""
    jobs_info = []  # list of (positional_args_tuple, kwargs_dict)

    for item in req.items:
        video = db.query(VideoItem).get(item.video_id)
        if not video or not video.file_path or not os.path.isfile(video.file_path):
            continue

        crop = None
        if item.crop_w is not None and item.crop_h is not None:
            crop = {
                "crop_w": item.crop_w,
                "crop_h": item.crop_h,
                "crop_x": item.crop_x or 0,
                "crop_y": item.crop_y or 0,
            }

        job = ProcessingJob(
            video_id=video.id,
            job_type="video_editor_encode",
            status=JobStatus.queued,
            display_name=f"{video.artist} \u2013 {video.title} \u203a Video Edit",
            action_label="Video Edit",
            input_params={
                "crop": crop,
                "target_dar": item.target_dar,
                "crf": item.crf,
                "preset": item.preset,
                "audio_passthrough": item.audio_passthrough,
                "trim_start": item.trim_start,
                "trim_end": item.trim_end,
                "audio_codec": item.audio_codec,
                "audio_bitrate": item.audio_bitrate,
            },
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        jobs_info.append((
            (job.id, video.id, video.file_path, crop, item.target_dar, item.crf, item.preset, item.audio_passthrough),
            {"trim_start": item.trim_start, "trim_end": item.trim_end,
             "audio_codec": item.audio_codec, "audio_bitrate": item.audio_bitrate},
        ))

    if not jobs_info:
        raise HTTPException(400, "No valid videos to encode")

    # Run all jobs sequentially in a single background thread
    def _run_batch(jobs):
        for args, kwargs in jobs:
            _run_encode_job(*args, **kwargs)

    t = threading.Thread(target=_run_batch, args=(jobs_info,), daemon=True)
    t.start()

    return {"job_ids": [j[0][0] for j in jobs_info], "message": f"Started {len(jobs_info)} encode jobs (sequential)"}


@router.get("/encode-status/{job_id}")
def get_encode_status(job_id: int, db: Session = Depends(get_db)):
    """Get status/progress of an encode job."""
    job = db.query(ProcessingJob).get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.job_type != "video_editor_encode":
        raise HTTPException(400, "Not a video editor encode job")

    return {
        "status": job.status.value if hasattr(job.status, 'value') else str(job.status),
        "progress_percent": job.progress_percent,
        "current_step": job.current_step,
        "error": job.error_message,
        "video_id": job.video_id,
        "summary": job.log_text if job.status == JobStatus.complete else None,
    }


@router.get("/scan-results/{job_id}")
def get_scan_results(job_id: int, db: Session = Depends(get_db)):
    """Get results from a letterbox scan job."""
    job = db.query(ProcessingJob).get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.job_type != "video_editor_scan":
        raise HTTPException(400, "Not a video editor scan job")

    result = {
        "status": job.status.value if hasattr(job.status, 'value') else str(job.status),
        "progress_percent": job.progress_percent,
        "current_step": job.current_step,
        "results": (job.input_params or {}).get("results", []) if job.status == JobStatus.complete else [],
        "error": job.error_message,
    }

    # Clean up completed/failed scan jobs from DB so they don't linger
    if job.status in (JobStatus.complete, JobStatus.failed):
        db.delete(job)
        db.commit()

    return result
