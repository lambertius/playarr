"""
New Videos API Router — Discovery feed, cart, dismissals, feedback, and settings.

All endpoints are under /api/new-videos/*.
"""
import logging
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.new_videos.models import (
    SuggestedVideo, SuggestedVideoDismissal, SuggestedVideoCartItem,
    RecommendationFeedback,
)
from app.new_videos import recommendation_service, feedback_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/new-videos", tags=["New Videos"])

# ── Pydantic schemas ──────────────────────────────────────────────────────────

class RefreshRequest(BaseModel):
    categories: Optional[List[str]] = None  # None = refresh all
    force: bool = False


class CartAddRequest(BaseModel):
    suggested_video_id: int


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
    dismissal_type: str = "temporary"  # temporary | permanent
    reason: Optional[str] = None


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

    Returns cached suggestions. If the feed has never been generated,
    triggers an initial generation.
    """
    feed = recommendation_service.get_feed(db)

    # If all categories are empty, do an initial generation
    has_any = any(
        len(cat_data["videos"]) > 0
        for cat_data in feed["categories"].values()
    )
    if not has_any:
        recommendation_service.refresh_all_categories(db, force=True)
        feed = recommendation_service.get_feed(db)

    return feed


@router.post("/refresh")
def refresh_feed(
    req: RefreshRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Regenerate the discovery feed (all or selected categories).

    Runs generation synchronously for immediate feedback. For large-scale
    generation, this could be moved to background tasks.
    """
    if req.categories:
        invalid = [c for c in req.categories if c not in recommendation_service.CATEGORIES]
        if invalid:
            raise HTTPException(400, f"Unknown categories: {invalid}")
        results = {}
        for cat in req.categories:
            results[cat] = recommendation_service.refresh_category(db, cat, force=req.force)
    else:
        results = recommendation_service.refresh_all_categories(db, force=req.force)

    return {"status": "ok", "refreshed": results}


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

    jobs = []
    for item in items:
        # Create a processing job using the same pattern as jobs.py
        job = ProcessingJob(
            job_type="import_url",
            status=JobStatus.queued,
            input_url=item.url,
            display_name=f"{item.artist} \u2013 {item.title} \u203a New Videos Import" if item.artist and item.title else item.url,
            action_label="New Videos import",
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

@router.post("/dismiss")
def dismiss_video(req: DismissRequest, db: Session = Depends(get_db)):
    """Dismiss a suggested video (temporarily or permanently)."""
    sv = db.query(SuggestedVideo).filter(SuggestedVideo.id == req.suggested_video_id).first()
    if not sv:
        raise HTTPException(404, "Suggested video not found")

    dismissal = SuggestedVideoDismissal(
        suggested_video_id=sv.id,
        dismissal_type=req.dismissal_type,
        reason=req.reason,
        provider=sv.provider,
        provider_video_id=sv.provider_video_id,
    )
    db.add(dismissal)

    feedback_service.record_feedback(
        db,
        feedback_type="permanently_dismissed" if req.dismissal_type == "permanent" else "dismissed",
        suggested_video_id=sv.id,
        provider=sv.provider,
        provider_video_id=sv.provider_video_id,
        artist=sv.artist,
        category=sv.category,
    )

    db.commit()
    return {"status": "dismissed", "type": req.dismissal_type}


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

@router.post("/add")
def add_video(req: CartAddRequest, db: Session = Depends(get_db)):
    """Import a suggested video immediately (bypasses cart).

    Creates an import job using the standard Playarr pipeline.
    """
    from app.models import ProcessingJob, JobStatus
    from app.worker import dispatch_task
    from app.tasks import import_video_task

    sv = db.query(SuggestedVideo).filter(SuggestedVideo.id == req.suggested_video_id).first()
    if not sv:
        raise HTTPException(404, "Suggested video not found")

    job = ProcessingJob(
        job_type="import_url",
        status=JobStatus.queued,
        input_url=sv.url,
        display_name=f"{sv.artist} \u2013 {sv.title} \u203a New Videos Quick Add" if sv.artist and sv.title else sv.url,
        action_label="New Videos quick add",
    )
    db.add(job)
    db.flush()

    feedback_service.record_feedback(
        db,
        feedback_type="added",
        suggested_video_id=sv.id,
        provider=sv.provider,
        provider_video_id=sv.provider_video_id,
        artist=sv.artist,
        category=sv.category,
    )

    db.commit()
    dispatch_task(import_video_task, job_id=job.id, url=sv.url,
                  normalize=True, scrape=True, scrape_musicbrainz=True)

    return {"status": "importing", "job_id": job.id}


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
