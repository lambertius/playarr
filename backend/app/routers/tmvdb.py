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
import hashlib
import json
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5
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


def _pull_candidates(video, result, db: Session) -> dict:
    """Convert remote values to reviewed field candidates without applying."""
    from app.models import ReviewCase, ReviewCaseItem

    locked = set(video.locked_fields or [])
    candidates = []
    conflicts = []
    from app.services.provenance_events import record_field_event
    retrieved_at = datetime.now(timezone.utc)
    for field, proposed in (result.fields or {}).items():
        current = getattr(video, field, None)
        conflict = current not in (None, "") and current != proposed
        candidate = {
            "field": field,
            "current": current,
            "proposed": proposed,
            "provenance": (result.field_provenance or {}).get(field),
            "confidence": result.confidence,
            "locked": field in locked,
            "conflict": conflict,
            "auto_applicable": current in (None, "") and field not in locked,
        }
        candidates.append(candidate)
        record_field_event(
            db, video, field, event_type="retrieved_candidate", actor_kind="provider",
            provider="tmvdb", source_url=getattr(result, "source_url", None),
            remote_id=str(getattr(result, "remote_id", "") or "") or None,
            prior_value=current, resulting_value=proposed,
            transformation="tmvdb_pull_candidate", retrieved_at=retrieved_at,
        )
        if conflict:
            conflicts.append(candidate)
    review_case_id = None
    if conflicts:
        material = {
            "video_stable_id": video.stable_id,
            "provider": "tmvdb",
            "conflicts": conflicts,
        }
        encoded = json.dumps(material, sort_keys=True, default=str).encode("utf-8")
        evidence_hash = hashlib.sha256(encoded).hexdigest()
        stable_id = str(uuid5(NAMESPACE_URL, f"playarr:tmvdb-conflict:{video.stable_id}"))
        case = db.query(ReviewCase).filter(ReviewCase.stable_id == stable_id).one_or_none()
        if case is None:
            case = ReviewCase(
                stable_id=stable_id,
                category="tmvdb_conflict",
                trigger_code="tmvdb_pull_conflict",
                evidence_hash=evidence_hash,
                evidence_json=material,
            )
            db.add(case)
            db.flush()
            case.items.append(ReviewCaseItem(
                video_id=video.id,
                video_stable_id=video.stable_id,
                role="current",
                evidence_summary_json={"artist": video.artist, "title": video.title},
            ))
        elif case.evidence_hash != evidence_hash:
            case.evidence_hash = evidence_hash
            case.evidence_json = material
            case.revision += 1
            case.status = "open"
            case.resolved_at = None
        db.commit()
        review_case_id = case.stable_id
    elif candidates:
        db.commit()
    return {
        "status": "found",
        "fields": result.fields,
        "candidates": candidates,
        "confidence": result.confidence,
        "field_provenance": result.field_provenance,
        "review_case_id": review_case_id,
    }


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

    return _pull_candidates(video, result, db)


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

    return _pull_candidates(video, result, db)


@router.post("/push")
def push_metadata(req: TMVDBPushRequest, force: bool = False, db: Session = Depends(get_db)):
    """
    Push local metadata for a video to TMVDB.

    Accepts an immutable, eligibility-gated snapshot into the durable outbox.
    Network delivery happens after this request commits.
    """
    from app.models import VideoItem
    from app.user_identity import get_instance_user_id
    if not _get_provider(db):
        _require_provider(db)

    video = db.query(VideoItem).get(req.video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    instance_user_id = get_instance_user_id(db)
    from app.services.contribution_outbox import enqueue_contribution
    row, eligibility, created = enqueue_contribution(
        db, video, instance_user_id, force=force,
    )
    if row is None:
        return {
            "status": "ineligible",
            "message": "No verified, edited, or locked fields are eligible for submission",
            "eligibility": eligibility["eligibility"],
        }
    db.commit()
    return {
        "status": row.status,
        "outbox_id": row.id,
        "operation_id": row.operation_id,
        "created": created,
        "eligible_fields": eligibility["eligible_fields"],
        "excluded_fields": eligibility["excluded_fields"],
        "message": "Contribution queued for TMVDB" if created else f"Contribution already {row.status}",
    }


@router.post("/push/bulk")
def push_bulk(req: TMVDBBulkPushRequest, force: bool = False, db: Session = Depends(get_db)):
    """Push metadata for multiple videos to TMVDB."""
    from app.models import VideoItem
    from app.user_identity import get_instance_user_id
    if not _get_provider(db):
        _require_provider(db)
    instance_user_id = get_instance_user_id(db)

    from app.services.contribution_outbox import enqueue_contribution
    results = {"queued": 0, "existing": 0, "ineligible": 0, "not_found": 0, "operations": []}
    for vid in req.video_ids:
        video = db.query(VideoItem).get(vid)
        if not video:
            results["not_found"] += 1
            continue
        row, _eligibility, created = enqueue_contribution(db, video, instance_user_id, force=force)
        if row is None:
            results["ineligible"] += 1
        elif created:
            results["queued"] += 1
            results["operations"].append(row.operation_id)
        else:
            results["existing"] += 1
            results["operations"].append(row.operation_id)
    db.commit()
    return results


@router.get("/contributions")
def list_contributions(video_id: Optional[int] = None, limit: int = 100,
                       db: Session = Depends(get_db)):
    """List durable outbound attempts, falling back to legacy audit entries."""
    from app.models import ContributionLog, ContributionOutbox
    outbox_query = db.query(ContributionOutbox)
    if video_id is not None:
        outbox_query = outbox_query.filter(ContributionOutbox.video_id == video_id)
    outbox_rows = outbox_query.order_by(ContributionOutbox.created_at.desc()).limit(min(limit, 500)).all()
    if outbox_rows:
        return [{
            "id": row.id,
            "video_id": row.video_id,
            "operation": "push",
            "payload_hash": row.payload_hash,
            "status": row.status,
            "remote_id": row.remote_id,
            "operation_id": row.operation_id,
            "request_id": row.request_id,
            "eligibility": row.eligibility_json,
            "response": row.response_json,
            "error": row.error_json,
            "attempts": row.attempts,
            "max_attempts": row.max_attempts,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        } for row in outbox_rows]
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
    from app.provenance import build_eligible_contribution

    video = db.query(VideoItem).get(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    return build_eligible_contribution(video, get_instance_user_id(db))


@router.post("/contributions/{outbox_id}/cancel")
def cancel_contribution(outbox_id: str, db: Session = Depends(get_db)):
    from app.models import ContributionOutbox
    row = db.get(ContributionOutbox, outbox_id)
    if not row:
        raise HTTPException(404, "Contribution not found")
    if row.status not in ("pending", "retry"):
        raise HTTPException(409, f"Cannot cancel a {row.status} contribution")
    row.status = "cancelled"
    db.commit()
    return {"status": row.status, "operation_id": row.operation_id}


@router.post("/contributions/{outbox_id}/retry")
def retry_contribution(outbox_id: str, db: Session = Depends(get_db)):
    from app.models import ContributionOutbox
    row = db.get(ContributionOutbox, outbox_id)
    if not row:
        raise HTTPException(404, "Contribution not found")
    if row.status != "failed":
        raise HTTPException(409, f"Cannot retry a {row.status} contribution")
    row.status = "pending"
    row.attempts = 0
    row.error_json = None
    row.completed_at = None
    db.commit()
    return {"status": row.status, "operation_id": row.operation_id}
