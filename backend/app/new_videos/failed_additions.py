"""Bounded projection of failed recommendation imports."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import JobStatus, ProcessingJob
from app.new_videos.models import SuggestedVideo, SuggestedVideoDismissal


def list_failed_additions(db: Session, limit: int = 20) -> list[dict]:
    jobs = (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.job_type == "import_url",
            ProcessingJob.status.in_((JobStatus.failed, JobStatus.cancelled)),
        )
        .order_by(ProcessingJob.completed_at.desc(), ProcessingJob.id.desc())
        .limit(200)
        .all()
    )
    failed: list[dict] = []
    for job in jobs:
        suggestion_id = (job.input_params or {}).get("suggested_video_id")
        if not suggestion_id:
            continue
        suggestion = db.get(SuggestedVideo, int(suggestion_id))
        if suggestion is None:
            continue
        failed.append({
            "job_id": job.id,
            "status": job.status.value,
            "error": job.error_message or "Import did not complete",
            "suggestion": {
                "id": suggestion.id,
                "provider": suggestion.provider,
                "provider_video_id": suggestion.provider_video_id,
                "url": suggestion.url,
                "title": suggestion.title,
                "artist": suggestion.artist,
                "thumbnail_url": suggestion.thumbnail_url,
                "category": suggestion.category,
            },
        })
        if len(failed) >= limit:
            break
    return failed


def restore_failed_suggestion(db: Session, job_id: int) -> dict:
    job = db.get(ProcessingJob, job_id)
    suggestion_id = (job.input_params or {}).get("suggested_video_id") if job else None
    if job is None or not suggestion_id:
        raise LookupError("failed New Videos addition not found")
    if job.status not in (JobStatus.failed, JobStatus.cancelled):
        raise ValueError("only failed or cancelled additions can be restored")
    removed = (
        db.query(SuggestedVideoDismissal)
        .filter(
            SuggestedVideoDismissal.suggested_video_id == int(suggestion_id),
            SuggestedVideoDismissal.reason.in_((
                "auto-dismissed on add", "imported_via_cart",
            )),
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"status": "restored", "suggested_video_id": int(suggestion_id), "removed": removed}
