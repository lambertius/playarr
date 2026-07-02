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


def _last_pushed_hash(db: Session, video_id: int) -> Optional[str]:
    """Return the payload_hash of the most recent successful push for a video."""
    from app.models import ContributionLog
    row = (
        db.query(ContributionLog)
        .filter(
            ContributionLog.video_id == video_id,
            ContributionLog.status == "submitted",
        )
        .order_by(ContributionLog.created_at.desc())
        .first()
    )
    return row.payload_hash if row else None


def _push_one(db: Session, provider, video, instance_user_id: str,
              operation: str, force: bool = False) -> dict:
    """Build the envelope, push it, and record a ContributionLog entry.

    Returns {"status": "submitted"|"skipped"|"failed", ...}.
    Idempotent: skips when an identical payload was already accepted (unless force).
    """
    from app.models import ContributionLog
    from app.provenance import build_contribution_envelope

    envelope = build_contribution_envelope(video, instance_user_id)
    payload_hash = envelope["payload_hash"]

    if not force and _last_pushed_hash(db, video.id) == payload_hash:
        return {"status": "skipped", "video_id": video.id, "reason": "unchanged"}

    try:
        result = provider.push_track(envelope)
    except Exception as e:  # network/transport failure
        logger.warning(f"TMVDB push failed for video {video.id}: {e}")
        result = None

    status = "submitted" if result else "failed"
    remote_id = str(result.get("id")) if result and result.get("id") is not None else None

    db.add(ContributionLog(
        video_id=video.id,
        instance_user_id=instance_user_id,
        target="tmvdb",
        operation=operation,
        playarr_track_id=video.playarr_track_id,
        playarr_video_id=video.playarr_video_id,
        payload_hash=payload_hash,
        status=status,
        remote_id=remote_id,
        response=result if isinstance(result, dict) else None,
    ))
    db.commit()

    return {"status": status, "video_id": video.id, "tmvdb_id": remote_id}


@router.post("/push")
def push_metadata(req: TMVDBPushRequest, force: bool = False, db: Session = Depends(get_db)):
    """
    Push local metadata for a video to TMVDB.

    Packages the video's full provenance envelope (every field tagged with its
    source, who set/verified it, trust level, and all identity keys) and submits
    it to the community database, recording the contribution for idempotency.
    """
    from app.models import VideoItem
    from app.user_identity import get_instance_user_id
    provider = _require_provider(db)

    video = db.query(VideoItem).get(req.video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    instance_user_id = get_instance_user_id(db)
    res = _push_one(db, provider, video, instance_user_id, "push", force=force)
    if res["status"] == "submitted":
        return {"status": "submitted", "tmvdb_id": res.get("tmvdb_id"), "message": "Data submitted to TMVDB"}
    if res["status"] == "skipped":
        return {"status": "skipped", "message": "Already up to date in TMVDB (unchanged since last push)"}
    return {"status": "error", "message": "Failed to submit data to TMVDB"}


@router.post("/push/bulk")
def push_bulk(req: TMVDBBulkPushRequest, force: bool = False, db: Session = Depends(get_db)):
    """Push metadata for multiple videos to TMVDB."""
    from app.models import VideoItem
    from app.user_identity import get_instance_user_id
    provider = _require_provider(db)
    instance_user_id = get_instance_user_id(db)

    results = {"submitted": 0, "failed": 0, "skipped": 0, "not_found": 0}
    for vid in req.video_ids:
        video = db.query(VideoItem).get(vid)
        if not video:
            results["not_found"] += 1
            continue
        res = _push_one(db, provider, video, instance_user_id, "push_bulk", force=force)
        if res["status"] == "submitted":
            results["submitted"] += 1
        elif res["status"] == "skipped":
            results["skipped"] += 1
        else:
            results["failed"] += 1

    return results


@router.get("/contributions")
def list_contributions(video_id: Optional[int] = None, limit: int = 100,
                       db: Session = Depends(get_db)):
    """List outbound contribution-log entries (most recent first)."""
    from app.models import ContributionLog
    q = db.query(ContributionLog)
    if video_id is not None:
        q = q.filter(ContributionLog.video_id == video_id)
    rows = q.order_by(ContributionLog.created_at.desc()).limit(min(limit, 500)).all()
    return [
        {
            "id": r.id,
            "video_id": r.video_id,
            "instance_user_id": r.instance_user_id,
            "target": r.target,
            "operation": r.operation,
            "playarr_track_id": r.playarr_track_id,
            "playarr_video_id": r.playarr_video_id,
            "payload_hash": r.payload_hash,
            "status": r.status,
            "remote_id": r.remote_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/preview/{video_id}")
def preview_contribution(video_id: int, db: Session = Depends(get_db)):
    """Return the exact contribution envelope that would be pushed for a video.

    Lets the UI (and integrators) inspect the tagged provenance payload without
    submitting anything.
    """
    from app.models import VideoItem
    from app.user_identity import get_instance_user_id
    from app.provenance import build_contribution_envelope

    video = db.query(VideoItem).get(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    return build_contribution_envelope(video, get_instance_user_id(db))
