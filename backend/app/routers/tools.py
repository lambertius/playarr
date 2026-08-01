"""
Tools API — manage the external CLI tools Playarr depends on (currently yt-dlp).

Lets an installed app keep yt-dlp current without a full reinstall.
"""
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import JobStatus, ProcessingJob
from app.services import ytdlp_updater

router = APIRouter(prefix="/api/tools", tags=["Tools"])
logger = logging.getLogger(__name__)


class YtdlpStatus(BaseModel):
    installed_version: str | None = None
    latest_version: str | None = None
    update_available: bool = False
    managed: bool = False
    path: str | None = None
    managed_path: str | None = None
    last_checked_at: str | None = None


class YtdlpUpdateAccepted(BaseModel):
    status: str
    job_id: int
    message: str


@router.get("/ytdlp", response_model=YtdlpStatus)
def ytdlp_status():
    """Current yt-dlp version, the latest available, and whether it's managed."""
    return ytdlp_updater.get_status()


@router.post("/ytdlp/update", response_model=YtdlpUpdateAccepted)
def ytdlp_update(db: Session = Depends(get_db)):
    """Accept a visible background job; never block on the binary download."""
    from app.tasks import ytdlp_update_task
    from app.worker import dispatch_task

    job = ProcessingJob(
        job_type="ytdlp_update",
        status=JobStatus.queued,
        display_name="yt-dlp update",
        action_label="Update yt-dlp",
        current_step="Waiting to start",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    dispatch_task(ytdlp_update_task, job_id=job.id)
    return {
        "status": "queued",
        "job_id": job.id,
        "message": "yt-dlp update queued. Progress and errors are visible here in Queue.",
    }
