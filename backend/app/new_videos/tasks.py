"""Durable New Videos refresh job."""
from datetime import datetime, timezone

from app.worker import celery_app


@celery_app.task(name="app.new_videos.tasks.refresh_recommendations")
def refresh_recommendations(
    *, job_id: int, categories: list[str] | None = None, force: bool = False,
) -> dict[str, int]:
    from app.database import RequestSessionLocal
    from app.models import JobStatus, ProcessingJob
    from app.new_videos import recommendation_service

    db = RequestSessionLocal()
    try:
        job = db.get(ProcessingJob, job_id)
        if job is None:
            raise LookupError(f"unknown New Videos refresh job {job_id}")
        job.status = JobStatus.analyzing
        job.started_at = datetime.now(timezone.utc)
        job.current_step = "Building a materially fresh recommendation snapshot"
        db.commit()

        if categories:
            results = {
                category: recommendation_service.refresh_category(
                    db, category, force=force,
                )
                for category in categories
            }
        else:
            results = recommendation_service.refresh_all_categories(db, force=force)

        job = db.get(ProcessingJob, job_id)
        job.status = JobStatus.complete
        job.progress_percent = 100
        job.current_step = "Fresh recommendation snapshot ready"
        job.completed_at = datetime.now(timezone.utc)
        job.input_params = {**(job.input_params or {}), "results": results}
        db.commit()
        return results
    except Exception as exc:
        db.rollback()
        job = db.get(ProcessingJob, job_id)
        if job is not None:
            job.status = JobStatus.failed
            job.error_message = str(exc)
            job.current_step = "Recommendation refresh failed"
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
        raise
    finally:
        db.close()
