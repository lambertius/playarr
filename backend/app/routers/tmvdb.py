"""
TMVDB Router — API endpoints for The Music Video DB integration.

Handles:
- Connection testing
- Pull: retrieve metadata from TMVDB for a track
- Push: submit local metadata to improve the community database
- Fingerprint lookup
- Bulk sync operations
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tmvdb", tags=["TMVDB"])


# ── Request / response schemas ────────────────────────────────────

class TMVDBTestResponse(BaseModel):
    connected: bool
    message: str
    version: Optional[str] = None

class TMVDBPullRequest(BaseModel):
    video_id: int

class TMVDBPullByFingerprintRequest(BaseModel):
    video_id: int

class TMVDBPushRequest(BaseModel):
    video_id: int

class TMVDBBulkPushRequest(BaseModel):
    video_ids: list[int]


# ── Helpers ───────────────────────────────────────────────────────

def _get_tmvdb_settings(db: Session) -> dict:
    """Read TMVDB settings from the database."""
    from app.models import AppSetting
    keys = ["tmvdb_api_key", "tmvdb_enabled", "tmvdb_auto_pull", "tmvdb_auto_push"]
    rows = db.query(AppSetting).filter(
        AppSetting.key.in_(keys),
        AppSetting.user_id.is_(None),
    ).all()
    settings = {r.key: r.value for r in rows}
    return settings


def _get_provider(db: Session):
    """Instantiate a TMVDBProvider with current settings."""
    from app.metadata.providers.tmvdb import TMVDBProvider
    settings = _get_tmvdb_settings(db)
    api_key = settings.get("tmvdb_api_key", "")
    enabled = settings.get("tmvdb_enabled", "false") == "true"
    if not enabled or not api_key:
        return None
    return TMVDBProvider(api_key=api_key)


def _require_provider(db: Session):
    """Get provider or raise 400 if not configured."""
    provider = _get_provider(db)
    if not provider:
        raise HTTPException(
            status_code=400,
            detail="TMVDB integration is not enabled. Go to Settings → TMVDB and configure your API key.",
        )
    return provider


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("/test", response_model=TMVDBTestResponse)
def test_connection(db: Session = Depends(get_db)):
    """Test the TMVDB API connection."""
    provider = _get_provider(db)
    if not provider:
        return TMVDBTestResponse(connected=False, message="TMVDB is not configured")
    data = provider._get("/status")
    if data:
        return TMVDBTestResponse(
            connected=True,
            message="Connected to TMVDB",
            version=data.get("version"),
        )
    return TMVDBTestResponse(connected=False, message="Could not reach TMVDB API")


@router.post("/pull")
def pull_metadata(req: TMVDBPullRequest, db: Session = Depends(get_db)):
    """
    Pull metadata from TMVDB for a specific video.

    Searches TMVDB by artist+title and returns matched data without
    auto-applying it.  The caller can choose to apply the results.
    """
    from app.models import VideoItem
    provider = _require_provider(db)

    video = db.query(VideoItem).get(req.video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Try fingerprint lookup first
    result = None
    if video.audio_fingerprint:
        duration = None
        if video.quality_signature:
            duration = video.quality_signature.duration_seconds
        result = provider.lookup_by_fingerprint(video.audio_fingerprint, duration)

    # Fall back to artist+title search
    if not result:
        candidates = provider.search_track(video.artist, video.title)
        if candidates:
            result = candidates[0]

    if not result:
        return {"status": "not_found", "message": "No match found in TMVDB"}

    return {
        "status": "found",
        "fields": result.fields,
        "confidence": result.confidence,
        "field_provenance": result.field_provenance,
    }


@router.post("/pull/fingerprint")
def pull_by_fingerprint(req: TMVDBPullByFingerprintRequest, db: Session = Depends(get_db)):
    """Pull metadata from TMVDB using the video's audio fingerprint."""
    from app.models import VideoItem
    provider = _require_provider(db)

    video = db.query(VideoItem).get(req.video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if not video.audio_fingerprint:
        raise HTTPException(status_code=400, detail="Video has no audio fingerprint")

    duration = None
    if video.quality_signature:
        duration = video.quality_signature.duration_seconds

    result = provider.lookup_by_fingerprint(video.audio_fingerprint, duration)
    if not result:
        return {"status": "not_found", "message": "Fingerprint not recognised by TMVDB"}

    return {
        "status": "found",
        "fields": result.fields,
        "confidence": result.confidence,
        "field_provenance": result.field_provenance,
    }


@router.post("/push")
def push_metadata(req: TMVDBPushRequest, db: Session = Depends(get_db)):
    """
    Push local metadata for a video to TMVDB.

    Packages the video's metadata (including provenance) and submits
    it to the community database.
    """
    from app.models import VideoItem
    provider = _require_provider(db)

    video = db.query(VideoItem).get(req.video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    track_data = {
        "artist": video.artist,
        "title": video.title,
        "album": video.album,
        "year": video.year,
        "plot": video.plot,
        "genres": [g.name for g in video.genres],
        "mb_artist_id": video.mb_artist_id,
        "mb_recording_id": video.mb_recording_id,
        "mb_release_id": video.mb_release_id,
        "mb_release_group_id": video.mb_release_group_id,
        "audio_fingerprint": video.audio_fingerprint,
        "acoustid_id": video.acoustid_id,
        "version_type": video.version_type,
        "field_provenance": video.field_provenance or {},
        "source_urls": [
            {"provider": s.provider.value, "url": s.canonical_url, "type": s.source_type}
            for s in video.sources
        ],
    }

    # Include entity data if available
    if video.artist_entity:
        track_data["artist_entity"] = {
            "name": video.artist_entity.canonical_name,
            "mb_artist_id": video.artist_entity.mb_artist_id,
            "country": video.artist_entity.country,
            "field_provenance": video.artist_entity.field_provenance or {},
        }
    if video.album_entity:
        track_data["album_entity"] = {
            "title": video.album_entity.title,
            "year": video.album_entity.year,
            "mb_release_id": video.album_entity.mb_release_id,
            "album_type": video.album_entity.album_type,
            "field_provenance": video.album_entity.field_provenance or {},
        }

    result = provider.push_track(track_data)
    if result:
        return {"status": "submitted", "tmvdb_id": result.get("id"), "message": "Data submitted to TMVDB"}
    return {"status": "error", "message": "Failed to submit data to TMVDB"}


@router.post("/push/bulk")
def push_bulk(req: TMVDBBulkPushRequest, db: Session = Depends(get_db)):
    """Push metadata for multiple videos to TMVDB."""
    from app.models import VideoItem
    provider = _require_provider(db)

    results = {"submitted": 0, "failed": 0, "skipped": 0}
    for vid in req.video_ids:
        video = db.query(VideoItem).get(vid)
        if not video:
            results["skipped"] += 1
            continue
        track_data = {
            "artist": video.artist,
            "title": video.title,
            "album": video.album,
            "year": video.year,
            "genres": [g.name for g in video.genres],
            "mb_recording_id": video.mb_recording_id,
            "audio_fingerprint": video.audio_fingerprint,
            "field_provenance": video.field_provenance or {},
        }
        resp = provider.push_track(track_data)
        if resp:
            results["submitted"] += 1
        else:
            results["failed"] += 1

    return results
