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
    asset = (
        db.query(MediaAsset)
        .filter(
            MediaAsset.video_id == video_id,
            MediaAsset.asset_type == "poster",
            MediaAsset.status == "valid",
        )
        .first()
    )
    if not asset or not os.path.isfile(asset.file_path):
        raise HTTPException(status_code=404, detail="Poster not found")

    return _cached_file_response(asset, request)


@router.get("/artwork/{video_id}/{asset_type}")
async def get_artwork(video_id: int, asset_type: str, request: Request, db: Session = Depends(get_db)):
    """Serve artwork for a video by asset type (artist_thumb, album_thumb, etc.)."""
    allowed_types = {"artist_thumb", "album_thumb"}
    if asset_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"asset_type must be one of {allowed_types}")
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
        raise HTTPException(status_code=404, detail=f"{asset_type} not found")
    return _cached_file_response(asset, request)


@router.get("/thumb/{video_id}")
async def get_video_thumb(video_id: int, request: Request, db: Session = Depends(get_db)):
    """Get the video player thumbnail (selected scene analysis frame). Only serves valid assets."""
    asset = (
        db.query(MediaAsset)
        .filter(
            MediaAsset.video_id == video_id,
            MediaAsset.asset_type == "video_thumb",
            MediaAsset.status == "valid",
        )
        .first()
    )
    if not asset or not os.path.isfile(asset.file_path):
        raise HTTPException(status_code=404, detail="Video thumbnail not found")

    return _cached_file_response(asset, request)


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
            process.stdout.close()
            process.wait()
            if process.returncode and process.returncode != 0:
                stderr = process.stderr.read().decode(errors="replace")
                logger.warning(f"Remux exited {process.returncode}: {stderr[:500]}")
            process.stderr.close()

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
            process.stdout.close()
            process.wait()
            if process.returncode and process.returncode != 0:
                stderr = process.stderr.read().decode(errors="replace")
                logger.warning(f"Transcode exited {process.returncode}: {stderr[:500]}")
            process.stderr.close()

    return StreamingResponse(
        _generate(),
        media_type="video/mp4",
        headers={
            "Content-Type": "video/mp4",
            "Cache-Control": "no-cache",
        },
    )


def _parse_range(range_header: str, file_size: int):
    """Parse a Range header value into start/end byte positions."""
    range_spec = range_header.strip().lower().replace("bytes=", "")
    parts = range_spec.split("-")

    start = int(parts[0]) if parts[0] else 0
    end = int(parts[1]) if parts[1] else file_size - 1

    start = max(0, min(start, file_size - 1))
    end = max(start, min(end, file_size - 1))

    return start, end
