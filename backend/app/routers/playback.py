"""
Playback API — Stream video files, serve previews, record playback history,
upload artwork.
"""
import logging
import os
import subprocess
import sys
import threading
from typing import Optional

_POPEN_FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from sqlalchemy import func

from app.database import get_db
from app.models import VideoItem, PlaybackHistory, MediaAsset, AppSetting
from app.services.preview_generator import generate_preview

router = APIRouter(prefix="/api/playback", tags=["Playback"])
logger = logging.getLogger(__name__)


# ── Cached artwork helper ──────────────────────────────────
# Artwork images change rarely.  Aggressive caching prevents repeated
# poster fetches from exhausting the DB connection pool when browsing
# large library pages (48–192 cards per page).

# In-memory artwork path cache: (video_id, asset_type) → (file_path, file_hash, mtime)
# Avoids a DB query for every image request from the background animation grid.
_artwork_cache: dict[tuple[int, str], tuple[str, str | None, float]] = {}
_artwork_cache_lock = threading.Lock()
_ARTWORK_CACHE_TTL = 120  # seconds

import time as _time

def _lookup_artwork_cached(db, video_id: int, asset_type: str):
    """Return (file_path, file_hash) from cache or DB.  Returns None if not found."""
    import time as _t
    key = (video_id, asset_type)
    now = _t.monotonic()
    with _artwork_cache_lock:
        entry = _artwork_cache.get(key)
        if entry and (now - entry[2]) < _ARTWORK_CACHE_TTL:
            fp, fh, _ = entry
            if os.path.isfile(fp):
                return fp, fh
            else:
                del _artwork_cache[key]

    # Cache miss — query DB
    asset = (
        db.query(MediaAsset)
        .filter(
            MediaAsset.video_id == video_id,
            MediaAsset.asset_type == asset_type,
            MediaAsset.status == "valid",
        )
        .first()
    )
    if not asset or not os.path.isfile(asset.file_path):
        return None
    fh = asset.file_hash
    fp = asset.file_path
    with _artwork_cache_lock:
        _artwork_cache[key] = (fp, fh, now)
    return fp, fh


def _cached_file_response(asset, request: Request) -> Response:
    """Return a FileResponse with cache headers + ETag for a MediaAsset."""
    etag = f'"{asset.file_hash}"' if getattr(asset, "file_hash", None) else None

    # If the client already has this version, return 304
    if etag:
        if_none_match = request.headers.get("if-none-match")
        if if_none_match and if_none_match.strip() == etag:
            return Response(status_code=304, headers={"ETag": etag})

    headers = {"Cache-Control": "public, max-age=86400"}
    if etag:
        headers["ETag"] = etag
    return FileResponse(asset.file_path, headers=headers)


def _cached_file_response_from_cache(file_path: str, file_hash: str | None, request: Request) -> Response:
    """Return a FileResponse from cached path+hash, skipping DB entirely."""
    etag = f'"{file_hash}"' if file_hash else None
    if etag:
        if_none_match = request.headers.get("if-none-match")
        if if_none_match and if_none_match.strip() == etag:
            return Response(status_code=304, headers={"ETag": etag})
    headers = {"Cache-Control": "public, max-age=86400"}
    if etag:
        headers["ETag"] = etag
    return FileResponse(file_path, headers=headers)


# ── Active streaming process registry ──────────────────────
# Maps normalised file path → set of subprocess.Popen objects.
_active_streams: dict[str, set[subprocess.Popen]] = {}
_streams_lock = threading.Lock()


def _register_stream(file_path: str, proc: subprocess.Popen):
    key = os.path.normpath(file_path)
    with _streams_lock:
        _active_streams.setdefault(key, set()).add(proc)


def _unregister_stream(file_path: str, proc: subprocess.Popen):
    key = os.path.normpath(file_path)
    with _streams_lock:
        procs = _active_streams.get(key)
        if procs:
            procs.discard(proc)
            if not procs:
                del _active_streams[key]


def kill_streams_for_file(file_path: str) -> int:
    """Kill all active ffmpeg streaming processes that are reading *file_path*.
    Returns the number of processes killed."""
    key = os.path.normpath(file_path)
    with _streams_lock:
        procs = list(_active_streams.pop(key, set()))
    killed = 0
    for proc in procs:
        try:
            proc.kill()
            proc.wait(timeout=5)
            killed += 1
        except Exception:
            pass
    if killed:
        logger.info(f"Killed {killed} active stream(s) for {os.path.basename(file_path)}")
    return killed


@router.get("/artwork-ids")
async def list_artwork_ids(db: Session = Depends(get_db)):
    """Return video IDs that have real poster or album_thumb artwork (not youtube thumbnails)."""
    rows = (
        db.query(MediaAsset.video_id, MediaAsset.asset_type)
        .filter(
            MediaAsset.asset_type.in_(["poster", "album_thumb"]),
            MediaAsset.status == "valid",
            MediaAsset.provenance != "youtube_thumb",
        )
        .distinct()
        .all()
    )
    result = []
    for video_id, asset_type in rows:
        result.append({"videoId": video_id, "type": asset_type})
    return result

# Audio codecs that browsers can play natively (in common containers)
_BROWSER_SAFE_AUDIO = {"aac", "mp3", "opus", "vorbis", "flac"}


@router.get("/stream/{video_id}")
async def stream_video(video_id: int, request: Request, db: Session = Depends(get_db)):
    """
    Stream a video file with Range header support for seeking.
    If the audio codec is not browser-compatible, transcode on-the-fly
    via ffmpeg (copy video, encode audio to AAC).
    """
    item = db.query(VideoItem).get(video_id)
    if not item or not item.file_path or not os.path.isfile(item.file_path):
        raise HTTPException(status_code=404, detail="Video file not found")

    # Reject files outside all configured library directories
    from app.config import get_settings
    all_dirs = get_settings().get_all_library_dirs()
    norm_path = os.path.normcase(os.path.normpath(item.file_path))
    if not any(norm_path.startswith(os.path.normcase(os.path.normpath(d)) + os.sep)
               for d in all_dirs):
        raise HTTPException(status_code=403,
                            detail="Video file is outside configured library directories")

    file_path = item.file_path

    # Check if container/codec combo needs remuxing for browser playback
    qs = item.quality_signature
    audio_codec = qs.audio_codec if qs else None
    video_codec = qs.video_codec if qs else None
    container = qs.container if qs else None

    # H.264 in MKV is not playable in Chrome — remux to fragmented MP4
    ext_lower = os.path.splitext(file_path)[1].lower()
    needs_remux = (
        ext_lower in (".mkv",)
        and video_codec
        and video_codec.lower() in ("h264", "avc", "avc1")
    )

    if needs_remux:
        # Lightweight remux (copy both streams) to fragmented MP4
        needs_audio_transcode = (
            audio_codec and audio_codec.lower() not in _BROWSER_SAFE_AUDIO
        )
        row = db.query(AppSetting).filter(
            AppSetting.key == "transcode_audio_bitrate",
            AppSetting.user_id.is_(None),
        ).first()
        bitrate = f"{row.value}k" if row else "256k"
        return _stream_remuxed(file_path, transcode_audio=needs_audio_transcode, audio_bitrate=bitrate)

    if audio_codec and audio_codec.lower() not in _BROWSER_SAFE_AUDIO:
        row = db.query(AppSetting).filter(
            AppSetting.key == "transcode_audio_bitrate",
            AppSetting.user_id.is_(None),
        ).first()
        bitrate = f"{row.value}k" if row else "256k"
        return _stream_transcoded(file_path, audio_bitrate=bitrate)

    # --- Standard raw streaming (browser-safe audio) ---
    file_size = os.path.getsize(file_path)

    # Determine MIME type
    ext = os.path.splitext(file_path)[1].lower()
    mime_map = {
        ".mp4": "video/mp4",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
        ".avi": "video/x-msvideo",
    }
    content_type = mime_map.get(ext, "video/mp4")

    # Handle Range requests for seeking
    range_header = request.headers.get("range")
    if range_header:
        start, end = _parse_range(range_header, file_size)
        chunk_size = end - start + 1

        def iter_file():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = chunk_size
                while remaining > 0:
                    read_size = min(remaining, 1024 * 1024)  # 1MB chunks
                    data = f.read(read_size)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return StreamingResponse(
            iter_file(),
            status_code=206,
            media_type=content_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(chunk_size),
            },
        )

    # Full file response
    return FileResponse(
        file_path,
        media_type=content_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
        },
    )


@router.get("/stream-video-only/{video_id}")
async def stream_video_only(video_id: int, request: Request, db: Session = Depends(get_db)):
    """
    Lightweight video-only stream for muted playback (e.g. the NowPlaying
    visual feed).  Serves the raw file directly for MP4/WebM since the
    browser can decode the video track even when the audio codec is
    unsupported — the element is muted so audio is irrelevant.
    Only MKV containers are remuxed (video-copy, no audio) because
    Chrome cannot play the MKV container at all.
    """
    item = db.query(VideoItem).get(video_id)
    if not item or not item.file_path or not os.path.isfile(item.file_path):
        raise HTTPException(status_code=404, detail="Video file not found")

    from app.config import get_settings
    all_dirs = get_settings().get_all_library_dirs()
    norm_path = os.path.normcase(os.path.normpath(item.file_path))
    if not any(norm_path.startswith(os.path.normcase(os.path.normpath(d)) + os.sep)
               for d in all_dirs):
        raise HTTPException(status_code=403,
                            detail="Video file is outside configured library directories")

    file_path = item.file_path
    ext_lower = os.path.splitext(file_path)[1].lower()

    # MKV needs remux to MP4 (video-only, no audio transcode)
    if ext_lower in (".mkv",):
        return _stream_remuxed_video_only(file_path)

    # MP4/WebM: serve raw file — muted element ignores audio track
    file_size = os.path.getsize(file_path)
    mime_map = {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".avi": "video/x-msvideo",
    }
    content_type = mime_map.get(ext_lower, "video/mp4")

    range_header = request.headers.get("range")
    if range_header:
        start, end = _parse_range(range_header, file_size)
        chunk_size = end - start + 1

        def iter_file():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = chunk_size
                while remaining > 0:
                    read_size = min(remaining, 1024 * 1024)
                    data = f.read(read_size)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return StreamingResponse(
            iter_file(),
            status_code=206,
            media_type=content_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(chunk_size),
            },
        )

    return FileResponse(
        file_path,
        media_type=content_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
        },
    )


@router.get("/stream-archive")
async def stream_archive(path: str, request: Request):
    """Stream an archived video file by its direct path (validated to be inside an archive dir)."""
    from app.config import get_settings
    _settings = get_settings()
    norm_path = os.path.normcase(os.path.normpath(path))
    allowed = False
    for lib_root in _settings.get_all_library_dirs():
        archive_root = os.path.normcase(os.path.normpath(os.path.join(lib_root, "_archive")))
        if norm_path.startswith(archive_root + os.sep) or norm_path == archive_root:
            allowed = True
            break
    if not allowed:
        raise HTTPException(status_code=403, detail="Path is not inside archive directory")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Archived file not found")

    file_size = os.path.getsize(path)
    ext = os.path.splitext(path)[1].lower()
    mime_map = {
        ".mp4": "video/mp4",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
        ".avi": "video/x-msvideo",
    }
    content_type = mime_map.get(ext, "video/mp4")

    range_header = request.headers.get("range")
    if range_header:
        start, end = _parse_range(range_header, file_size)
        chunk_size = end - start + 1

        def iter_file():
            with open(path, "rb") as f:
                f.seek(start)
                remaining = chunk_size
                while remaining > 0:
                    read_size = min(remaining, 1024 * 1024)
                    data = f.read(read_size)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return StreamingResponse(
            iter_file(),
            status_code=206,
            media_type=content_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(chunk_size),
            },
        )

    return FileResponse(
        path,
        media_type=content_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
        },
    )


@router.get("/preview/{video_id}")
async def get_preview(video_id: int, db: Session = Depends(get_db)):
    """Get or generate a preview clip for hover preview."""
    item = db.query(VideoItem).get(video_id)
    if not item or not item.file_path or not os.path.isfile(item.file_path):
        raise HTTPException(status_code=404, detail="Video not found")

    preview_path = generate_preview(item.file_path, video_id=video_id)
    if not preview_path or not os.path.isfile(preview_path):
        raise HTTPException(status_code=500, detail="Preview generation failed")

    return FileResponse(
        preview_path,
        media_type="video/mp4",
        stat_result=os.stat(preview_path),
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@router.get("/asset/{asset_id}")
async def get_asset(asset_id: int, request: Request, db: Session = Depends(get_db)):
    """Serve any MediaAsset file by its ID. Only serves valid assets."""
    asset = db.query(MediaAsset).get(asset_id)
    if not asset or not os.path.isfile(asset.file_path):
        raise HTTPException(status_code=404, detail="Asset not found")
    if getattr(asset, "status", "valid") not in ("valid", "pending", None):
        raise HTTPException(status_code=404, detail="Asset is invalid")
    return _cached_file_response(asset, request)


@router.get("/poster/{video_id}")
async def get_poster(video_id: int, request: Request, db: Session = Depends(get_db)):
    """Get the poster image for a video. Only serves valid assets."""
    cached = _lookup_artwork_cached(db, video_id, "poster")
    if not cached:
        raise HTTPException(status_code=404, detail="Poster not found")
    return _cached_file_response_from_cache(cached[0], cached[1], request)


@router.get("/artwork/{video_id}/{asset_type}")
async def get_artwork(video_id: int, asset_type: str, request: Request, db: Session = Depends(get_db)):
    """Serve artwork for a video by asset type (artist_thumb, album_thumb, etc.)."""
    allowed_types = {"artist_thumb", "album_thumb"}
    if asset_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"asset_type must be one of {allowed_types}")
    cached = _lookup_artwork_cached(db, video_id, asset_type)
    if not cached:
        raise HTTPException(status_code=404, detail=f"{asset_type} not found")
    return _cached_file_response_from_cache(cached[0], cached[1], request)


@router.get("/thumb/{video_id}")
async def get_video_thumb(video_id: int, request: Request, db: Session = Depends(get_db)):
    """Get the video player thumbnail (selected scene analysis frame). Only serves valid assets."""
    cached = _lookup_artwork_cached(db, video_id, "video_thumb")
    if not cached:
        raise HTTPException(status_code=404, detail="Video thumbnail not found")
    return _cached_file_response_from_cache(cached[0], cached[1], request)


@router.put("/artwork/{video_id}/{asset_type}")
async def upload_artwork(
    video_id: int,
    asset_type: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Upload / replace artwork for a video.
    asset_type: poster | artist_thumb | album_thumb
    Accepts image/jpeg, image/png, image/webp.

    All uploads are validated through artwork_service to prevent
    non-image content from being persisted.
    """
    from datetime import datetime, timezone
    from app.services.artwork_service import validate_and_store_upload, validate_file

    allowed_types = {"poster", "artist_thumb", "album_thumb"}
    if asset_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"asset_type must be one of {allowed_types}")

    item = db.query(VideoItem).get(video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Video not found")

    content_type = file.content_type or ""
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files allowed")

    # Determine destination folder
    folder = item.folder_path
    if not folder or not os.path.isdir(folder):
        raise HTTPException(status_code=400, detail="Video folder not found on disk")

    folder_name = os.path.basename(folder)
    dest_path = os.path.join(folder, f"{folder_name}-{asset_type}.jpg")

    # Read upload bytes and validate through artwork_service
    file_bytes = await file.read()
    result = validate_and_store_upload(file_bytes, dest_path)
    if not result.success:
        raise HTTPException(status_code=400, detail=f"Invalid image: {result.error}")

    # Upsert MediaAsset record with provenance
    now = datetime.now(timezone.utc)
    existing = (
        db.query(MediaAsset)
        .filter(MediaAsset.video_id == video_id, MediaAsset.asset_type == asset_type)
        .first()
    )
    if existing:
        # Remove old file if different path
        if existing.file_path != dest_path and os.path.isfile(existing.file_path):
            try:
                os.remove(existing.file_path)
            except OSError:
                pass
        existing.file_path = dest_path
        existing.provenance = "user_upload"
        existing.source_url = None
        existing.status = "valid"
        existing.width = result.width
        existing.height = result.height
        existing.file_size_bytes = result.file_size_bytes
        existing.file_hash = result.file_hash
        existing.last_validated_at = now
        existing.validation_error = None
    else:
        new_asset = MediaAsset(
            video_id=video_id,
            asset_type=asset_type,
            file_path=dest_path,
            provenance="user_upload",
            status="valid",
            width=result.width,
            height=result.height,
            file_size_bytes=result.file_size_bytes,
            file_hash=result.file_hash,
            last_validated_at=now,
        )
        db.add(new_asset)

    # Clear missing_artwork review flag if this upload completes the artwork set
    if asset_type == "poster" and item.review_category in ("missing_artwork", "artwork_incomplete"):
        from app.ai.models import AIThumbnail
        has_thumb = db.query(AIThumbnail.id).filter(
            AIThumbnail.video_id == video_id,
            AIThumbnail.is_selected == True,  # noqa: E712
        ).first() is not None
        if has_thumb:
            item.review_status = "none"
            item.review_reason = None
            item.review_category = None

    db.commit()
    return {"detail": "Artwork uploaded", "asset_type": asset_type, "path": dest_path}


@router.delete("/artwork/{video_id}/{asset_type}")
async def delete_artwork(
    video_id: int,
    asset_type: str,
    db: Session = Depends(get_db),
):
    """
    Delete artwork for a video.
    asset_type: poster | artist_thumb | album_thumb | thumb | video_thumb
    Removes the file, deletes the DB record, and cleans up empty parent folders.
    """
    item = db.query(VideoItem).get(video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Video not found")

    asset = (
        db.query(MediaAsset)
        .filter(MediaAsset.video_id == video_id, MediaAsset.asset_type == asset_type)
        .first()
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    # Remove file from disk
    file_path = asset.file_path
    if file_path and os.path.isfile(file_path):
        try:
            os.remove(file_path)
        except OSError:
            pass

        # Clean up orphaned parent folders (e.g. _artists/Name/ or _albums/Name/Album/)
        # Walk up removing empty directories, but stop at the library root.
        library_dir = os.environ.get("LIBRARY_DIR", "")
        parent = os.path.dirname(file_path)
        while parent and parent != library_dir and os.path.isdir(parent):
            try:
                if not os.listdir(parent):
                    os.rmdir(parent)
                    parent = os.path.dirname(parent)
                else:
                    break
            except OSError:
                break

    db.delete(asset)
    db.commit()
    return {"detail": "Artwork deleted", "asset_type": asset_type}


@router.patch("/artwork/{video_id}/{asset_type}/crop")
async def update_artwork_crop(
    video_id: int,
    asset_type: str,
    body: dict,
    db: Session = Depends(get_db),
):
    """Update crop position for a video's artwork."""
    import re
    allowed_types = {"poster", "artist_thumb", "album_thumb"}
    if asset_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"asset_type must be one of {allowed_types}")

    crop_position = body.get("crop_position")
    if crop_position is not None:
        if not re.match(r"^\d{1,3}%\s+\d{1,3}%$", crop_position):
            raise HTTPException(status_code=400, detail="crop_position must be like '50% 30%'")

    asset = (
        db.query(MediaAsset)
        .filter(MediaAsset.video_id == video_id, MediaAsset.asset_type == asset_type, MediaAsset.status == "valid")
        .first()
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    asset.crop_position = crop_position
    db.commit()
    # Invalidate artwork cache
    _artwork_cache.pop((video_id, asset_type), None)
    return {"detail": "Crop updated", "crop_position": crop_position}


@router.post("/kill-streams")
async def kill_all_streams():
    """Kill all active streaming FFmpeg processes.

    Called by the frontend on track change to ensure old streams don't linger.
    """
    total = 0
    with _streams_lock:
        keys = list(_active_streams.keys())
    for key in keys:
        total += kill_streams_for_file(key)
    return {"killed": total}


@router.post("/history/{video_id}")
def record_playback(
    video_id: int,
    duration_watched: Optional[float] = None,
    db: Session = Depends(get_db),
):
    """Record that a video was played."""
    item = db.query(VideoItem).get(video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Video not found")

    history = PlaybackHistory(
        video_id=video_id,
        duration_watched_sec=duration_watched,
    )
    db.add(history)
    db.commit()
    return {"detail": "Recorded"}


def _stream_remuxed(
    file_path: str,
    transcode_audio: bool = False,
    audio_bitrate: str = "256k",
) -> StreamingResponse:
    """
    Remux a video (e.g. H.264+MKV) to fragmented MP4 for browser playback.
    Video is always stream-copied. Audio is copied or transcoded to AAC.
    """
    from app.config import get_settings
    settings = get_settings()
    ffmpeg = settings.resolved_ffmpeg

    audio_args = ["-c:a", "aac", "-b:a", audio_bitrate] if transcode_audio else ["-c:a", "copy"]
    cmd = [
        ffmpeg,
        "-i", file_path,
        "-c:v", "copy",
        *audio_args,
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4",
        "-v", "warning",
        "pipe:1",
    ]

    logger.info(f"Remux-streaming (H.264+MKV→MP4): {os.path.basename(file_path)}")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **_POPEN_FLAGS,
    )
    _register_stream(file_path, process)

    def _generate():
        try:
            while True:
                chunk = process.stdout.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            _unregister_stream(file_path, process)
            # Force-kill immediately on disconnect to prevent orphaned processes
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.stdout.close()
            except OSError:
                pass
            try:
                process.wait(timeout=5)
            except Exception:
                pass
            try:
                if process.returncode and process.returncode not in (0, -9, -15, 4294967295):
                    stderr = process.stderr.read().decode(errors="replace")
                    if stderr.strip():
                        logger.warning(f"Remux exited {process.returncode}: {stderr[:500]}")
            except Exception:
                pass
            try:
                process.stderr.close()
            except OSError:
                pass

    return StreamingResponse(
        _generate(),
        media_type="video/mp4",
        headers={
            "Content-Type": "video/mp4",
            "Cache-Control": "no-cache",
        },
    )


def _stream_remuxed_video_only(file_path: str) -> StreamingResponse:
    """
    Remux video stream only (no audio) from MKV to fragmented MP4.
    Used for the muted visual feed — avoids audio transcoding overhead.
    """
    from app.config import get_settings
    settings = get_settings()
    ffmpeg = settings.resolved_ffmpeg

    cmd = [
        ffmpeg,
        "-i", file_path,
        "-c:v", "copy",
        "-an",  # strip audio entirely
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4",
        "-v", "warning",
        "pipe:1",
    ]

    logger.info(f"Video-only remux (MKV→MP4, no audio): {os.path.basename(file_path)}")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **_POPEN_FLAGS,
    )
    _register_stream(file_path, process)

    def _generate():
        try:
            while True:
                chunk = process.stdout.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            _unregister_stream(file_path, process)
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.stdout.close()
            except OSError:
                pass
            try:
                process.wait(timeout=5)
            except Exception:
                pass
            try:
                process.stderr.close()
            except OSError:
                pass

    return StreamingResponse(
        _generate(),
        media_type="video/mp4",
        headers={
            "Content-Type": "video/mp4",
            "Cache-Control": "no-cache",
        },
    )


def _stream_transcoded(file_path: str, audio_bitrate: str = "256k") -> StreamingResponse:
    """
    Stream a video file with on-the-fly audio transcoding to AAC.
    Video is stream-copied (no re-encode), audio is transcoded to AAC.
    Output is fragmented MP4 piped to stdout for streaming.
    """
    from app.config import get_settings
    settings = get_settings()
    ffmpeg = settings.resolved_ffmpeg

    cmd = [
        ffmpeg,
        "-i", file_path,
        "-c:v", "copy",          # pass through video untouched
        "-c:a", "aac",           # transcode audio to AAC
        "-b:a", audio_bitrate,   # configurable audio bitrate
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4",             # fragmented MP4 to stdout
        "-v", "warning",
        "pipe:1",
    ]

    logger.info(f"Transcode-streaming: {os.path.basename(file_path)}")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **_POPEN_FLAGS,
    )
    _register_stream(file_path, process)

    def _generate():
        try:
            while True:
                chunk = process.stdout.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                yield chunk
        finally:
            _unregister_stream(file_path, process)
            # Force-kill immediately on disconnect to prevent orphaned processes
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.stdout.close()
            except OSError:
                pass
            try:
                process.wait(timeout=5)
            except Exception:
                pass
            try:
                if process.returncode and process.returncode not in (0, -9, -15, 4294967295):
                    stderr = process.stderr.read().decode(errors="replace")
                    if stderr.strip():
                        logger.warning(f"Transcode exited {process.returncode}: {stderr[:500]}")
            except Exception:
                pass
            try:
                process.stderr.close()
            except OSError:
                pass

    return StreamingResponse(
        _generate(),
        media_type="video/mp4",
        headers={
            "Content-Type": "video/mp4",
            "Cache-Control": "no-cache",
        },
    )


# ── Audio download ─────────────────────────────────────────
# Windows Media Player POPM rating: 1→1, 2→64, 3→128, 4→196, 5→255
_WMP_RATING_MAP = {1: 1, 2: 64, 3: 128, 4: 196, 5: 255}


@router.get("/download-audio/{video_id}")
async def download_audio(video_id: int, db: Session = Depends(get_db)):
    """
    Extract audio from a video as a tagged CBR MP3.
    Matches source audio bitrate/channels. Tags with metadata + poster art.
    """
    import tempfile
    import shutil

    from app.config import get_settings

    item = db.query(VideoItem).get(video_id)
    if not item or not item.file_path or not os.path.isfile(item.file_path):
        raise HTTPException(status_code=404, detail="Video file not found")

    # Security: verify file is within library dirs
    settings = get_settings()
    all_dirs = settings.get_all_library_dirs()
    norm_path = os.path.normcase(os.path.normpath(item.file_path))
    if not any(norm_path.startswith(os.path.normcase(os.path.normpath(d)) + os.sep)
               for d in all_dirs):
        raise HTTPException(status_code=403,
                            detail="Video file is outside configured library directories")

    ffmpeg = settings.resolved_ffmpeg

    # Determine audio bitrate and channels from quality signature
    qs = item.quality_signature
    audio_bitrate_kbps = 192  # default
    audio_channels = 2        # default stereo
    if qs:
        if qs.audio_bitrate:
            # audio_bitrate is stored in bps, convert to kbps and clamp to
            # standard CBR values
            src_kbps = qs.audio_bitrate // 1000
            cbr_options = [64, 96, 128, 160, 192, 224, 256, 320]
            audio_bitrate_kbps = min(cbr_options, key=lambda x: abs(x - src_kbps))
        if qs.audio_channels:
            audio_channels = qs.audio_channels

    # Build output filename
    artist = item.artist or "Unknown Artist"
    title = item.title or "Unknown Title"
    safe_name = f"{artist} - {title}.mp3"
    # Sanitize filename for Content-Disposition
    for ch in r'<>:"/\\|?*':
        safe_name = safe_name.replace(ch, "_")

    tmp_dir = tempfile.mkdtemp(prefix="playarr_audio_")
    mp3_path = os.path.join(tmp_dir, "output.mp3")

    try:
        # Step 1: Extract audio to CBR MP3
        cmd = [
            ffmpeg,
            "-i", item.file_path,
            "-vn",                          # no video
            "-c:a", "libmp3lame",           # MP3 codec
            "-b:a", f"{audio_bitrate_kbps}k",  # CBR bitrate
            "-ac", str(audio_channels),     # match channel count
            "-y",                           # overwrite
            "-v", "warning",
            mp3_path,
        ]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, **_POPEN_FLAGS,
        )
        if result.returncode != 0 or not os.path.isfile(mp3_path):
            logger.error(f"FFmpeg audio extract failed: {result.stderr[:500]}")
            raise HTTPException(status_code=500, detail="Audio extraction failed")

        # Step 2: Tag the MP3 with metadata
        _tag_mp3(mp3_path, item, db)

        # Step 3: Stream the file back, then clean up
        def _iter_and_cleanup():
            try:
                with open(mp3_path, "rb") as f:
                    while True:
                        chunk = f.read(1024 * 1024)
                        if not chunk:
                            break
                        yield chunk
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        file_size = os.path.getsize(mp3_path)
        return StreamingResponse(
            _iter_and_cleanup(),
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_name}"',
                "Content-Length": str(file_size),
                "Content-Type": "audio/mpeg",
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.exception(f"Audio download failed for video {video_id}")
        raise HTTPException(status_code=500, detail=str(exc))


def _tag_mp3(mp3_path: str, item: "VideoItem", db: Session):
    """Apply ID3 tags to the MP3 file: artist, title, album, year, genre,
    WMP-compliant star rating, and poster artwork."""
    from mutagen.mp3 import MP3
    from mutagen.id3 import (
        ID3, TIT2, TPE1, TALB, TDRC, TCON, APIC, POPM, ID3NoHeaderError,
    )

    try:
        audio = MP3(mp3_path, ID3=ID3)
    except ID3NoHeaderError:
        audio = MP3(mp3_path)
        audio.add_tags()

    tags = audio.tags

    # Basic metadata
    if item.title:
        tags.add(TIT2(encoding=3, text=[item.title]))
    if item.artist:
        tags.add(TPE1(encoding=3, text=[item.artist]))

    # Album = "[Song Title] Video"
    album_title = f"{item.title} Video" if item.title else "Video"
    tags.add(TALB(encoding=3, text=[album_title]))

    if item.year:
        tags.add(TDRC(encoding=3, text=[str(item.year)]))

    # Genre — join all genres with semicolon
    genres = [g.name for g in (item.genres or [])]
    if genres:
        tags.add(TCON(encoding=3, text=["; ".join(genres)]))

    # WMP-compliant star rating via POPM frame
    if item.song_rating and 1 <= item.song_rating <= 5:
        wmp_val = _WMP_RATING_MAP.get(item.song_rating, 128)
        tags.add(POPM(
            email="Windows Media Player 9 Series",
            rating=wmp_val,
            count=0,
        ))

    # Poster artwork
    poster = (
        db.query(MediaAsset)
        .filter(
            MediaAsset.video_id == item.id,
            MediaAsset.asset_type == "poster",
        )
        .first()
    )
    if poster and poster.file_path and os.path.isfile(poster.file_path):
        try:
            with open(poster.file_path, "rb") as img_f:
                img_data = img_f.read()
            ext = os.path.splitext(poster.file_path)[1].lower()
            mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
            tags.add(APIC(
                encoding=3,
                mime=mime,
                type=3,   # Cover (front)
                desc="Cover",
                data=img_data,
            ))
        except Exception as exc:
            logger.warning(f"Failed to embed poster art: {exc}")

    audio.save()


def _parse_range(range_header: str, file_size: int):
    """Parse a Range header value into start/end byte positions."""
    range_spec = range_header.strip().lower().replace("bytes=", "")
    parts = range_spec.split("-")

    start = int(parts[0]) if parts[0] else 0
    end = int(parts[1]) if parts[1] else file_size - 1

    start = max(0, min(start, file_size - 1))
    end = max(start, min(end, file_size - 1))

    return start, end
