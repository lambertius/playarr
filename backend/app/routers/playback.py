"""
Playback API — Stream video files, serve previews, record playback history,
upload artwork.
"""
import asyncio
import collections
import logging
import os
import subprocess
import sys
import threading
from typing import Optional

_POPEN_FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.models import VideoItem, PlaybackHistory, MediaAsset, AppSetting
from app.runtime_dirs import get_runtime_dirs
from app.services.playback_stream_cache import serve_transformed_media
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


def active_stream_count() -> int:
    """Total number of active streaming ffmpeg processes (diagnostics)."""
    with _streams_lock:
        return sum(len(procs) for procs in _active_streams.values())


def is_streaming_file(file_path: str) -> bool:
    with _streams_lock:
        return bool(_active_streams.get(os.path.normpath(file_path)))


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


def _validate_library_path(file_path: str) -> None:
    """Validate that a playback path is inside a configured library directory
    and NOT inside its ``_archive`` subdirectory.

    Archived originals (bumped there by the video editor / re-download flows)
    must never be playable through the normal playback endpoints — only via
    the explicit Archive manager flows (``/stream-archive`` preview, restore).

    Raises HTTPException(403) on violation.
    """
    from app.config import get_settings
    all_dirs = get_settings().get_all_library_dirs()
    norm_path = os.path.normcase(os.path.normpath(file_path))
    inside_library = False
    for d in all_dirs:
        norm_root = os.path.normcase(os.path.normpath(d))
        if not norm_path.startswith(norm_root + os.sep):
            continue
        inside_library = True
        archive_root = os.path.join(norm_root, "_archive")
        if norm_path == archive_root or norm_path.startswith(archive_root + os.sep):
            raise HTTPException(
                status_code=403,
                detail="File is in the archive — restore it to play",
            )
    if not inside_library:
        raise HTTPException(
            status_code=403,
            detail="Video file is outside configured library directories",
        )


@router.get("/stream/{video_id}")
async def stream_video(
    video_id: int,
    request: Request,
    transcode: bool = Query(False, description="Force a full H.264/AAC compatibility transcode"),
    db: Session = Depends(get_db),
):
    """
    Stream a video file with Range header support for seeking.
    If the audio codec is not browser-compatible, transcode on-the-fly
    via ffmpeg (copy video, encode audio to AAC).  With ?transcode=1 the video
    is fully re-encoded to a broadly-compatible, network-friendly H.264 stream.
    """
    item = db.query(VideoItem).get(video_id)
    if not item or not item.file_path or not os.path.isfile(item.file_path):
        raise HTTPException(status_code=404, detail="Video file not found")

    # Reject files outside all configured library directories or archived originals
    _validate_library_path(item.file_path)

    file_path = item.file_path

    # Compatibility mode: full video+audio transcode regardless of source codec.
    if transcode:
        return await _stream_compat(
            request, file_path, with_audio=True, audio_bitrate=_audio_bitrate(db)
        )

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
        return await _stream_remuxed(
            request, file_path, transcode_audio=needs_audio_transcode, audio_bitrate=bitrate
        )

    if audio_codec and audio_codec.lower() not in _BROWSER_SAFE_AUDIO:
        row = db.query(AppSetting).filter(
            AppSetting.key == "transcode_audio_bitrate",
            AppSetting.user_id.is_(None),
        ).first()
        bitrate = f"{row.value}k" if row else "256k"
        return await _stream_transcoded(request, file_path, audio_bitrate=bitrate)

    # --- Standard raw streaming (browser-safe audio) ---
    stat = os.stat(file_path)
    file_size = stat.st_size

    # Cache validators derived from file mtime+size so the browser revalidates
    # instead of blindly serving a stale copy. Critical after an in-place
    # re-encode/trim: the URL is unchanged, so without a changing validator the
    # browser would keep playing the pre-edit video from its media cache.
    etag = f'"{int(stat.st_mtime)}-{file_size}"'

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
                "Cache-Control": "no-cache",
                "ETag": etag,
            },
        )

    # Full file response
    return FileResponse(
        file_path,
        media_type=content_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Cache-Control": "no-cache",
            "ETag": etag,
        },
    )




@router.get("/stream-video-only/{video_id}")
async def stream_video_only(
    video_id: int,
    request: Request,
    transcode: bool = Query(False, description="Force a full H.264 compatibility transcode (no audio)"),
    db: Session = Depends(get_db),
):
    """
    Lightweight video-only stream for muted playback (e.g. the NowPlaying
    visual feed).  Serves the raw file directly for MP4/WebM since the
    browser can decode the video track even when the audio codec is
    unsupported — the element is muted so audio is irrelevant.
    Only MKV containers are remuxed (video-copy, no audio) because
    Chrome cannot play the MKV container at all.  With ?transcode=1 the video
    is fully re-encoded to a broadly-compatible H.264 stream.
    """
    item = db.query(VideoItem).get(video_id)
    if not item or not item.file_path or not os.path.isfile(item.file_path):
        raise HTTPException(status_code=404, detail="Video file not found")

    # Reject files outside all configured library directories or archived originals
    _validate_library_path(item.file_path)

    file_path = item.file_path
    ext_lower = os.path.splitext(file_path)[1].lower()

    # Compatibility mode: full video transcode (no audio), regardless of source.
    if transcode:
        return await _stream_compat(request, file_path, with_audio=False)

    # MKV needs remux to MP4 (video-only, no audio transcode)
    if ext_lower in (".mkv",):
        return await _stream_remuxed_video_only(request, file_path)

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
    The kill itself (proc.kill + proc.wait) is blocking, so run it in a thread —
    otherwise, under TV mode's rapid track cycling, it freezes the event loop
    (and a wait on a wedged ffmpeg could stall the whole server).
    """
    def _kill_all() -> int:
        with _streams_lock:
            keys = list(_active_streams.keys())
        return sum(kill_streams_for_file(key) for key in keys)

    total = await asyncio.to_thread(_kill_all)
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


# ── Client-side playback diagnostics ──────────────────────
class ClientPlaybackMetrics(BaseModel):
    video_id: Optional[int] = None
    mode: Optional[str] = None          # "tv" | "browser" | "video-only" | …
    dropped: int = 0                    # dropped video frames (cumulative)
    total: int = 0                      # total video frames (cumulative)
    stalls: int = 0                     # buffer-underrun events this interval
    waiting_ms: int = 0                 # time spent stalled this interval
    buffered_ahead: Optional[float] = None  # seconds buffered ahead of playhead


@router.post("/client-metrics")
def client_metrics(m: ClientPlaybackMetrics):
    """Record client-side playback health (dropped frames, stalls, buffer
    health) to the server log so network drop-outs/hangs can be diagnosed
    without opening the browser dev tools — useful for TV devices."""
    pct = (m.dropped / m.total * 100.0) if m.total else 0.0
    logger.info(
        "Client playback [%s] vid=%s: dropped %d/%d (%.1f%%), stalls=%d, waiting=%dms, buffered_ahead=%.1fs",
        m.mode or "?", m.video_id, m.dropped, m.total, pct, m.stalls, m.waiting_ms,
        (m.buffered_ahead if m.buffered_ahead is not None else -1.0),
    )
    return {"ok": True}


# Full re-encodes (libx264) are CPU-bound — a handful running at once can
# saturate the machine. Heavy streams acquire this semaphore (light remux/copy
# streams skip it). It is acquired *inside* the streaming generator, which
# Starlette pumps on a threadpool thread, so queuing never blocks the event loop.
_MAX_CONCURRENT_TRANSCODES = 3
_transcode_sem = threading.BoundedSemaphore(_MAX_CONCURRENT_TRANSCODES)

def _audio_bitrate(db: Session, default: str = "256k") -> str:
    """Configured AAC transcode bitrate (e.g. '256k')."""
    row = db.query(AppSetting).filter(
        AppSetting.key == "transcode_audio_bitrate",
        AppSetting.user_id.is_(None),
    ).first()
    return f"{row.value}k" if row and row.value else default


def _drain_stderr(proc: subprocess.Popen, buf: "collections.deque[str]") -> None:
    """Continuously drain ffmpeg's stderr into a bounded ring buffer.

    We MUST keep reading stderr: ffmpeg emits warnings (e.g. non-monotonic DTS)
    and if its stderr pipe buffer fills, ffmpeg blocks on the write — which
    stalls stdout and hangs the stream (this previously exhausted the thread
    pool and took the server down). Draining on a daemon thread into a
    fixed-size deque keeps memory bounded while still capturing the error tail.
    """
    try:
        for line in iter(proc.stderr.readline, b""):
            buf.append(line.decode("utf-8", "replace").rstrip())
    except Exception:
        pass
    finally:
        try:
            proc.stderr.close()
        except OSError:
            pass


def _spawn_ffmpeg(cmd: list) -> subprocess.Popen:
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **_POPEN_FLAGS,
    )
    tail: "collections.deque[str]" = collections.deque(maxlen=40)
    process._stderr_tail = tail  # type: ignore[attr-defined]
    threading.Thread(target=_drain_stderr, args=(process, tail), daemon=True).start()
    return process


async def _streaming_response(
    request: Request,
    cmd: list,
    file_path: str,
    label: str,
    heavy: bool = False,
) -> Response:
    """Serve one byte-stable transformed representation with ranged retries."""
    return await serve_transformed_media(
        request,
        cmd=cmd,
        file_path=file_path,
        label=label,
        heavy=heavy,
        cache_root=get_runtime_dirs().cache_dir,
        spawn_process=_spawn_ffmpeg,
        register_process=_register_stream,
        unregister_process=_unregister_stream,
        heavy_semaphore=_transcode_sem,
    )


async def _stream_remuxed(
    request: Request,
    file_path: str,
    transcode_audio: bool = False,
    audio_bitrate: str = "256k",
) -> Response:
    """Remux a video (e.g. H.264+MKV) to fragmented MP4 for browser playback.
    Video is always stream-copied; audio is copied or transcoded to AAC."""
    from app.config import get_settings
    ffmpeg = get_settings().resolved_ffmpeg
    audio_args = ["-c:a", "aac", "-b:a", audio_bitrate] if transcode_audio else ["-c:a", "copy"]
    cmd = [
        ffmpeg, "-i", file_path,
        "-c:v", "copy", *audio_args,
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4", "-v", "warning", "pipe:1",
    ]
    logger.info(f"Remux-streaming (H.264+MKV→MP4): {os.path.basename(file_path)}")
    return await _streaming_response(request, cmd, file_path, "remux")


async def _stream_remuxed_video_only(request: Request, file_path: str) -> Response:
    """Remux video stream only (no audio) from MKV to fragmented MP4 — for the
    muted visual feed; avoids audio transcoding overhead."""
    from app.config import get_settings
    ffmpeg = get_settings().resolved_ffmpeg
    cmd = [
        ffmpeg, "-i", file_path,
        "-c:v", "copy", "-an",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4", "-v", "warning", "pipe:1",
    ]
    logger.info(f"Video-only remux (MKV→MP4, no audio): {os.path.basename(file_path)}")
    return await _streaming_response(request, cmd, file_path, "remux-vo")


async def _stream_transcoded(
    request: Request,
    file_path: str,
    audio_bitrate: str = "256k",
) -> Response:
    """Stream with on-the-fly audio transcode to AAC; video is stream-copied."""
    from app.config import get_settings
    ffmpeg = get_settings().resolved_ffmpeg
    cmd = [
        ffmpeg, "-i", file_path,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4", "-v", "warning", "pipe:1",
    ]
    logger.info(f"Transcode-streaming (audio): {os.path.basename(file_path)}")
    return await _streaming_response(request, cmd, file_path, "atranscode")


async def _stream_compat(
    request: Request,
    file_path: str,
    with_audio: bool = True,
    audio_bitrate: str = "192k",
) -> Response:
    """Full compatibility transcode: re-encode video to H.264 (High profile,
    8-bit yuv420p), capped to 1080p with a bounded bitrate, and audio to AAC.

    For devices/networks where stream-copy playback drops frames or stutters —
    e.g. HEVC/VP9/AV1 sources, 10-bit or High-10 profiles, or bitrates too high
    for the link.  Costs server CPU but produces the most broadly-playable,
    network-friendly stream.
    """
    from app.config import get_settings
    ffmpeg = get_settings().resolved_ffmpeg
    audio_args = ["-c:a", "aac", "-b:a", audio_bitrate] if with_audio else ["-an"]
    cmd = [
        ffmpeg, "-i", file_path,
        "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-vf", "scale='min(1920,iw)':-2",   # cap width to 1920 (≤1080p), keep aspect
        "-crf", "23", "-maxrate", "6M", "-bufsize", "12M",
        "-g", "60",
        *audio_args,
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4", "-v", "warning", "pipe:1",
    ]
    logger.info("Compat-transcode streaming (%s): %s",
                "A/V" if with_audio else "video-only", os.path.basename(file_path))
    return await _streaming_response(
        request,
        cmd,
        file_path,
        "compat" if with_audio else "compat-vo",
        heavy=True,
    )


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
