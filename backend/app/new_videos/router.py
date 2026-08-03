"""
New Videos API Router — Discovery feed, cart, dismissals, feedback, and settings.

All endpoints are under /api/new-videos/*.
"""
import logging
from typing import Optional, List, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.new_videos.models import (
    SuggestedVideo, SuggestedVideoDismissal, SuggestedVideoCartItem,
    RecommendationFeedback,
)
from app.new_videos import recommendation_service, feedback_service
from app.models import JobStatus, ProcessingJob

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/new-videos", tags=["New Videos"])


# ── Background refresh state ────────────────────────────────────────────────
# Feed refresh runs many 30 s yt-dlp searches; doing that synchronously in the
# request thread made the Refresh button "appear to lock" for minutes and tied
# up a request worker. Instead we run it in a background thread and expose a
# ``refreshing`` flag (on the feed + a status endpoint) so the UI can poll.

def _active_refresh(db: Session) -> ProcessingJob | None:
    return (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.job_type == "new_videos_refresh",
            ProcessingJob.status.in_((JobStatus.queued, JobStatus.analyzing)),
        )
        .order_by(ProcessingJob.created_at.desc())
        .first()
    )


def _start_refresh(
    db: Session, categories: Optional[List[str]], force: bool,
) -> tuple[ProcessingJob, bool]:
    active = _active_refresh(db)
    if active is not None:
        return active, False
    job = ProcessingJob(
        job_type="new_videos_refresh",
        status=JobStatus.queued,
        display_name="Build fresh New Videos list",
        action_label="New Videos fresh list",
        input_params={"categories": categories, "force": force},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    from app.new_videos.tasks import refresh_recommendations
    from app.worker import dispatch_task
    dispatch_task(
        refresh_recommendations, job_id=job.id,
        categories=categories, force=force,
    )
    return job, True

# ── Pydantic schemas ──────────────────────────────────────────────────────────

class RefreshRequest(BaseModel):
    categories: Optional[List[str]] = None  # None = refresh all
    force: bool = False


class CartAddRequest(BaseModel):
    suggested_video_id: int
    idempotency_key: Optional[str] = Field(default=None, max_length=200)


class CartRemoveRequest(BaseModel):
    suggested_video_id: int


class CartImportAllRequest(BaseModel):
    normalize: bool = True
    scrape: bool = True
    scrape_musicbrainz: bool = True
    ai_auto_analyse: bool = False
    ai_auto_fallback: bool = False


class DismissRequest(BaseModel):
    suggested_video_id: int
    dismissal_type: Literal["temporary", "permanent"] = "temporary"
    reason: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, max_length=200)


class UndismissRequest(BaseModel):
    suggested_video_id: int


class FeedbackRequest(BaseModel):
    suggested_video_id: Optional[int] = None
    feedback_type: str
    provider: Optional[str] = None
    provider_video_id: Optional[str] = None
    artist: Optional[str] = None
    category: Optional[str] = None
    context: Optional[dict] = None


class SettingUpdateRequest(BaseModel):
    key: str
    value: str


# ── Feed endpoints ────────────────────────────────────────────────────────────

@router.get("/")
def get_feed(db: Session = Depends(get_db)):
    """Return the full discovery feed grouped by category.

    Returns cached suggestions immediately. If the feed has never been
    generated, kicks off a background generation (non-blocking) so the request
    returns straight away; the ``refreshing`` flag lets the UI poll for results.
    """
    feed = recommendation_service.get_feed(db)

    # If all categories are empty, start an initial generation in the
    # background instead of blocking the request for minutes on yt-dlp.
    has_any = any(
        len(cat_data["videos"]) > 0
        for cat_data in feed["categories"].values()
    )
    active = _active_refresh(db)
    if not has_any and active is None:
        active, _ = _start_refresh(db, None, True)

    feed["refreshing"] = active is not None
    feed["refresh_job_id"] = active.id if active else None
    from app.new_videos.failed_additions import list_failed_additions
    feed["failed_additions"] = list_failed_additions(db)
    return feed


@router.post("/refresh")
def refresh_feed(req: RefreshRequest, db: Session = Depends(get_db)):
    """Regenerate the discovery feed (all or selected categories).

    Generation runs on a background thread (it performs many slow yt-dlp
    searches). Returns immediately; poll ``/refresh/status`` or the feed's
    ``refreshing`` flag for completion.
    """
    if req.categories:
        invalid = [c for c in req.categories if c not in recommendation_service.CATEGORIES]
        if invalid:
            raise HTTPException(400, f"Unknown categories: {invalid}")

    job, started = _start_refresh(db, req.categories, req.force)
    return {
        "status": "started" if started else "already_running",
        "refreshing": True,
        "job_id": job.id,
        "operation_id": job.operation_id,
    }


@router.get("/refresh/status")
def refresh_status(db: Session = Depends(get_db)):
    """Report the state of the background feed refresh."""
    job = (
        db.query(ProcessingJob)
        .filter(ProcessingJob.job_type == "new_videos_refresh")
        .order_by(ProcessingJob.created_at.desc())
        .first()
    )
    if job is None:
        return {"status": "idle", "refreshing": False, "job_id": None}
    return {
        "status": job.status.value,
        "refreshing": job.status in (JobStatus.queued, JobStatus.analyzing),
        "job_id": job.id,
        "operation_id": job.operation_id,
        "started_at": job.started_at,
        "finished_at": job.completed_at,
        "refreshed": (job.input_params or {}).get("results", {}),
        "error": job.error_message,
    }


# ── Cart endpoints ────────────────────────────────────────────────────────────

@router.get("/cart")
def get_cart(db: Session = Depends(get_db)):
    """Return all items in the import cart."""
    items = db.query(SuggestedVideoCartItem).order_by(
        SuggestedVideoCartItem.added_at.desc()
    ).all()
    return {
        "items": [
            {
                "id": item.id,
                "suggested_video_id": item.suggested_video_id,
                "url": item.url,
                "title": item.title,
                "artist": item.artist,
                "provider": item.provider,
                "provider_video_id": item.provider_video_id,
                "added_at": item.added_at.isoformat() if item.added_at else None,
            }
            for item in items
        ],
        "count": len(items),
    }


@router.post("/cart/add")
def add_to_cart(req: CartAddRequest, db: Session = Depends(get_db)):
    """Add a suggested video to the import cart."""
    sv = db.query(SuggestedVideo).filter(SuggestedVideo.id == req.suggested_video_id).first()
    if not sv:
        raise HTTPException(404, "Suggested video not found")

    existing = db.query(SuggestedVideoCartItem).filter(
        SuggestedVideoCartItem.suggested_video_id == req.suggested_video_id
    ).first()
    if existing:
        return {"status": "already_in_cart", "id": existing.id}

    item = SuggestedVideoCartItem(
        suggested_video_id=sv.id,
        url=sv.url,
        title=sv.title,
        artist=sv.artist,
        provider=sv.provider,
        provider_video_id=sv.provider_video_id,
    )
    db.add(item)

    # Record feedback
    feedback_service.record_feedback(
        db,
        feedback_type="added_to_cart",
        suggested_video_id=sv.id,
        provider=sv.provider,
        provider_video_id=sv.provider_video_id,
        artist=sv.artist,
        category=sv.category,
    )

    db.commit()
    return {"status": "added", "id": item.id}


@router.post("/cart/remove")
def remove_from_cart(req: CartRemoveRequest, db: Session = Depends(get_db)):
    """Remove a suggested video from the import cart."""
    item = db.query(SuggestedVideoCartItem).filter(
        SuggestedVideoCartItem.suggested_video_id == req.suggested_video_id
    ).first()
    if not item:
        raise HTTPException(404, "Item not in cart")

    # Record feedback
    sv = db.query(SuggestedVideo).filter(SuggestedVideo.id == req.suggested_video_id).first()
    if sv:
        feedback_service.record_feedback(
            db,
            feedback_type="removed_from_cart",
            suggested_video_id=sv.id,
            provider=sv.provider,
            provider_video_id=sv.provider_video_id,
            artist=sv.artist,
            category=sv.category,
        )

    db.delete(item)
    db.commit()
    return {"status": "removed"}


@router.post("/cart/clear")
def clear_cart(db: Session = Depends(get_db)):
    """Remove all items from the import cart."""
    count = db.query(SuggestedVideoCartItem).delete()
    db.commit()
    return {"status": "cleared", "removed": count}


@router.post("/cart/import-all")
def import_all_cart(req: CartImportAllRequest = CartImportAllRequest(), db: Session = Depends(get_db)):
    """Import all cart items using the standard Playarr import pipeline.

    Creates one import job per cart item. Does not bypass normal duplicate
    checking or import logic.
    """
    from app.models import ProcessingJob, JobStatus
    from app.worker import dispatch_task
    from app.tasks import import_video_task

    items = db.query(SuggestedVideoCartItem).all()
    if not items:
        return {"status": "empty", "jobs": []}

    from app.services.import_policy import policy_from_pipeline_options
    import_policy = policy_from_pipeline_options({
        "scrape": req.scrape,
        "scrape_musicbrainz": req.scrape_musicbrainz,
        "ai_auto_analyse": req.ai_auto_analyse,
        "ai_auto_fallback": req.ai_auto_fallback,
        "normalize": req.normalize,
    }).model_dump(mode="json")

    jobs = []
    for item in items:
        # Create a processing job using the same pattern as jobs.py
        job = ProcessingJob(
            job_type="import_url",
            status=JobStatus.queued,
            input_url=item.url,
            display_name=f"{item.artist} \u2013 {item.title} \u203a New Videos Import" if item.artist and item.title else item.url,
            action_label="New Videos import",
            input_params={
                "suggested_video_id": item.suggested_video_id,
                "provider": item.provider,
                "provider_video_id": item.provider_video_id,
                "normalize": req.normalize,
                "scrape": req.scrape,
                "scrape_musicbrainz": req.scrape_musicbrainz,
                "ai_auto_analyse": req.ai_auto_analyse,
                "ai_auto_fallback": req.ai_auto_fallback,
                "import_policy": import_policy,
            },
        )
        db.add(job)
        db.flush()

        # Record feedback
        feedback_service.record_feedback(
            db,
            feedback_type="added",
            suggested_video_id=item.suggested_video_id,
            provider=item.provider,
            provider_video_id=item.provider_video_id,
            artist=item.artist,
            category=None,
        )

        # Permanently dismiss so imported videos don't reappear in the feed
        if item.suggested_video_id or item.provider_video_id:
            dismissal = SuggestedVideoDismissal(
                suggested_video_id=item.suggested_video_id,
                dismissal_type="permanent",
                reason="imported_via_cart",
                provider=item.provider,
                provider_video_id=item.provider_video_id,
            )
            db.add(dismissal)

        jobs.append({"job_id": job.id, "url": item.url, "title": item.title})

    # Clear cart after creating jobs
    auto_clear = recommendation_service._get_setting(db, "nv_auto_clear_cart", "true", "bool")
    if auto_clear:
        db.query(SuggestedVideoCartItem).delete()

    db.commit()

    # Dispatch import tasks after commit (same pattern as jobs router)
    for j in jobs:
        dispatch_task(import_video_task, job_id=j["job_id"], url=j["url"],
                      normalize=req.normalize, scrape=req.scrape,
                      scrape_musicbrainz=req.scrape_musicbrainz,
                      ai_auto_analyse=req.ai_auto_analyse,
                      ai_auto_fallback=req.ai_auto_fallback)

    return {"status": "importing", "job_count": len(jobs), "jobs": jobs}


# ── Dismissal endpoints ──────────────────────────────────────────────────────

@router.post("/dismiss", status_code=202)
def dismiss_video(req: DismissRequest, db: Session = Depends(get_db)):
    """Queue an idempotent interactive dismissal and return immediately."""
    sv = db.query(SuggestedVideo).filter(SuggestedVideo.id == req.suggested_video_id).first()
    if not sv:
        raise HTTPException(404, "Suggested video not found")
    from app.services.mutation_api import accept_mutation, mutation_idempotency_key
    from app.services.mutation_coordinator import CommandRequest, MutationPriority
    stable_id = f"{sv.provider}:{sv.provider_video_id}"
    return accept_mutation(db, CommandRequest(
        command_type="new_videos.dismiss", entity_type="suggested_video",
        entity_stable_id=stable_id,
        payload=req.model_dump(exclude={"idempotency_key"}),
        idempotency_key=mutation_idempotency_key(
            "new_videos.dismiss", stable_id, req.idempotency_key,
        ), priority=MutationPriority.INTERACTIVE,
    ))


@router.post("/undismiss")
def undismiss_video(req: UndismissRequest, db: Session = Depends(get_db)):
    """Remove all dismissals for a suggested video."""
    deleted = db.query(SuggestedVideoDismissal).filter(
        SuggestedVideoDismissal.suggested_video_id == req.suggested_video_id
    ).delete()
    db.commit()
    return {"status": "undismissed", "removed": deleted}


@router.get("/dismissed")
def list_dismissed(
    dismissal_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List dismissed videos (for debugging / admin)."""
    q = db.query(SuggestedVideoDismissal).order_by(
        SuggestedVideoDismissal.dismissed_at.desc()
    )
    if dismissal_type:
        q = q.filter(SuggestedVideoDismissal.dismissal_type == dismissal_type)

    items = q.limit(200).all()
    return {
        "items": [
            {
                "id": d.id,
                "suggested_video_id": d.suggested_video_id,
                "dismissal_type": d.dismissal_type,
                "dismissed_at": d.dismissed_at.isoformat() if d.dismissed_at else None,
                "reason": d.reason,
                "provider_video_id": d.provider_video_id,
            }
            for d in items
        ],
        "count": len(items),
    }


# ── Quick-add (bypasses cart) ─────────────────────────────────────────────────

@router.post("/add", status_code=202)
def add_video(req: CartAddRequest, db: Session = Depends(get_db)):
    """Queue a New Videos import through the priority mutation actor."""
    sv = db.query(SuggestedVideo).filter(SuggestedVideo.id == req.suggested_video_id).first()
    if not sv:
        raise HTTPException(404, "Suggested video not found")
    from app.services.mutation_api import accept_mutation, mutation_idempotency_key
    from app.services.mutation_coordinator import CommandRequest, MutationPriority
    stable_id = f"{sv.provider}:{sv.provider_video_id}"
    return accept_mutation(db, CommandRequest(
        command_type="new_videos.add", entity_type="suggested_video",
        entity_stable_id=stable_id, payload={"suggested_video_id": sv.id},
        idempotency_key=mutation_idempotency_key(
            "new_videos.add", stable_id, req.idempotency_key,
        ), priority=MutationPriority.INTERACTIVE,
    ))


@router.post("/failed/{job_id}/restore")
def restore_failed_addition(job_id: int, db: Session = Depends(get_db)):
    """Put an auto-dismissed failed addition back into recommendation results."""
    from app.new_videos.failed_additions import restore_failed_suggestion
    try:
        return restore_failed_suggestion(db, job_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


# ── Feedback endpoint ────────────────────────────────────────────────────────

@router.post("/feedback")
def record_feedback(req: FeedbackRequest, db: Session = Depends(get_db)):
    """Record a user interaction event for future ranking improvement."""
    fb = feedback_service.record_feedback(
        db,
        feedback_type=req.feedback_type,
        suggested_video_id=req.suggested_video_id,
        provider=req.provider,
        provider_video_id=req.provider_video_id,
        artist=req.artist,
        category=req.category,
        context=req.context,
    )
    db.commit()
    return {"status": "recorded", "id": fb.id}


# ── Settings endpoints ───────────────────────────────────────────────────────

# New Videos settings with defaults
NV_SETTINGS_DEFAULTS = {
    "nv_enabled": ("true", "bool"),
    "nv_preferred_resolution": ("max", "string"),
    "nv_videos_per_category": ("15", "int"),
    "nv_refresh_interval_minutes": ("360", "int"),
    "nv_auto_refresh_on_startup": ("false", "bool"),
    "nv_include_temp_dismissed_after_refresh": ("false", "bool"),
    "nv_enable_ai_ranking": ("false", "bool"),
    "nv_enable_trusted_source_filtering": ("true", "bool"),
    "nv_min_trust_threshold": ("0.3", "float"),
    "nv_allow_unofficial_fallback": ("true", "bool"),
    "nv_preferred_providers": ("youtube", "string"),
    "nv_min_owned_for_artist_rec": ("2", "int"),
    "nv_max_recs_per_artist": ("5", "int"),
    "nv_use_ratings": ("true", "bool"),
    "nv_use_genre_similarity": ("true", "bool"),
    "nv_use_artist_similarity": ("true", "bool"),
    "nv_persist_cart": ("true", "bool"),
    "nv_auto_clear_cart": ("true", "bool"),
    "nv_famous_count": ("20", "int"),
    "nv_popular_count": ("20", "int"),
    "nv_rising_count": ("10", "int"),
    "nv_new_count": ("10", "int"),
}


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    """Return current New Videos settings."""
    from app.models import AppSetting

    result = {}
    for key, (default_val, val_type) in NV_SETTINGS_DEFAULTS.items():
        row = db.query(AppSetting).filter(
            AppSetting.key == key, AppSetting.user_id.is_(None)
        ).first()

        raw_val = row.value if row else default_val
        if val_type == "bool":
            result[key] = raw_val.lower() in ("true", "1", "yes")
        elif val_type == "int":
            try:
                result[key] = int(raw_val)
            except (ValueError, TypeError):
                result[key] = int(default_val)
        elif val_type == "float":
            try:
                result[key] = float(raw_val)
            except (ValueError, TypeError):
                result[key] = float(default_val)
        else:
            result[key] = raw_val

    return result


@router.post("/settings")
def update_settings(updates: List[SettingUpdateRequest], db: Session = Depends(get_db)):
    """Update one or more New Videos settings."""
    from app.models import AppSetting

    saved = []
    for u in updates:
        if u.key not in NV_SETTINGS_DEFAULTS:
            continue

        _, val_type = NV_SETTINGS_DEFAULTS[u.key]

        row = db.query(AppSetting).filter(
            AppSetting.key == u.key, AppSetting.user_id.is_(None)
        ).first()

        if row:
            row.value = u.value
        else:
            row = AppSetting(key=u.key, value=u.value, value_type=val_type)
            db.add(row)
        saved.append(u.key)

    db.commit()
    return {"status": "ok", "saved": saved}
