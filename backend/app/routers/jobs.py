"""
Jobs API — Import URLs, trigger rescans/normalizations, view job status.
Includes SSE streaming for real-time telemetry.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ProcessingJob, JobStatus, VideoItem, Source, SourceProvider, AppSetting
from app.schemas import (
    VideoItemCreate, JobOut, JobLogOut, NormalizeRequest,
    BatchRescanRequest, BatchActionResponse, LibraryScanRequest,
    NormalizationHistoryOut,
)
from app.tasks import import_video_task, rescan_metadata_task, normalize_task, library_scan_task, library_export_task, complete_batch_job_task, scrape_wikipedia_task, redownload_video_task, batch_import_task, duplicate_scan_task
from app.worker import dispatch_task
from app.services.url_utils import is_playlist_url
from app.services.downloader import extract_playlist_entries, get_available_formats
from app.services.telemetry import telemetry_store


def _import_action_label(req: "VideoItemCreate") -> str:
    """Build a human-readable action label for import jobs."""
    if req.ai_auto_fallback:
        return "URL Import (AI Only)"
    if req.ai_auto_analyse:
        return "URL Import (AI Auto)"
    parts = []
    if req.scrape and req.scrape_musicbrainz:
        parts.append("Wiki + MB")
    elif req.scrape:
        parts.append("Wiki")
    elif req.scrape_musicbrainz:
        parts.append("MB Only")
    else:
        parts.append("No Scraping")
    return f"URL Import ({', '.join(parts)})"


def _rescan_action_label(**opts) -> str:
    """Build a human-readable action label for rescan jobs."""
    ai_auto = opts.get("ai_auto")
    ai_only = opts.get("ai_only")
    scrape_wiki = opts.get("scrape_wikipedia", True)
    scrape_mb = opts.get("scrape_musicbrainz", True)
    scrape_tmvdb = opts.get("scrape_tmvdb", False)
    if ai_only:
        return "Rescan (AI Only)"
    if ai_auto:
        return "Rescan (AI Auto)"
    parts = []
    if scrape_wiki and scrape_mb:
        parts.append("Wiki + MB")
    elif scrape_wiki:
        parts.append("Wiki Only")
    elif scrape_mb:
        parts.append("MB Only")
    else:
        parts.append("No Scraping")
    if scrape_tmvdb:
        parts.append("TMVDB")
    return f"Rescan ({', '.join(parts)})"


def _scrape_action_label(**opts) -> str:
    """Build action label for scrape metadata jobs."""
    if opts.get("ai_only"):
        return "Scrape Metadata (AI Only)"
    if opts.get("ai_auto_analyse"):
        return "Scrape Metadata (AI Auto)"
    parts = []
    if opts.get("scrape_wikipedia", False) and opts.get("scrape_musicbrainz", False):
        parts.append("Wiki + MB")
    elif opts.get("scrape_wikipedia", False):
        parts.append("Wiki Only")
    elif opts.get("scrape_musicbrainz", False):
        parts.append("MB Only")
    else:
        parts.append("No Scraping")
    if opts.get("scrape_tmvdb", False):
        parts.append("TMVDB")
    return f"Scrape Metadata ({', '.join(parts)})"


def _require_ai_provider(db: Session) -> None:
    """Raise HTTP 400 if an AI mode is selected but no AI provider is configured."""
    row = db.query(AppSetting).filter(
        AppSetting.key == "ai_provider",
        AppSetting.user_id.is_(None),
    ).first()
    provider = row.value if row else "none"
    if provider == "none":
        raise HTTPException(
            status_code=400,
            detail="AI mode requires an AI provider. Go to Settings and select an AI provider (OpenAI, Gemini, Claude, or Local) before using AI Auto or AI Only.",
        )

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["Jobs"])


@router.post("/import", response_model=JobOut)
def import_by_url(req: VideoItemCreate, db: Session = Depends(get_db)):
    """Submit a new URL for import into the library. Handles playlists automatically."""

    # Block AI modes when no AI provider is configured
    if req.ai_auto_analyse or req.ai_auto_fallback:
        _require_ai_provider(db)

    # Detect playlist URLs and fan out into individual imports
    if is_playlist_url(req.url):
        return _import_playlist(req, db)

    job = ProcessingJob(
        job_type="import_url",
        status=JobStatus.queued,
        input_url=req.url,
        display_name=req.url,  # Will be updated to actual title during processing
        action_label=_import_action_label(req),
        input_params={"artist": req.artist, "title": req.title,
                      "normalize": req.normalize, "scrape": req.scrape,
                      "scrape_musicbrainz": req.scrape_musicbrainz,
                      "is_cover": req.is_cover, "is_live": req.is_live,
                      "is_alternate": req.is_alternate,
                      "alternate_version_label": req.alternate_version_label},
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Dispatch task (Celery or in-process thread)
    dispatch_task(
        import_video_task,
        job_id=job.id,
        url=req.url,
        artist_override=req.artist,
        title_override=req.title,
        normalize=req.normalize,
        scrape=req.scrape,
        scrape_musicbrainz=req.scrape_musicbrainz,
        hint_cover=req.is_cover,
        hint_live=req.is_live,
        hint_alternate=req.is_alternate,
        hint_uncensored=req.is_uncensored,
        hint_alternate_label=req.alternate_version_label,
        ai_auto_analyse=req.ai_auto_analyse,
        ai_auto_fallback=req.ai_auto_fallback,
    )

    return job


def _import_playlist(req: VideoItemCreate, db: Session) -> JobOut:
    """Handle a YouTube playlist URL by creating a parent job + individual child jobs."""
    # Create parent tracking job
    _child_label = _import_action_label(req)
    _playlist_mode = _child_label.split("(", 1)[-1].rstrip(")") if "(" in _child_label else ""
    parent = ProcessingJob(
        job_type="playlist_import",
        status=JobStatus.analyzing,
        input_url=req.url,
        display_name=f"Playlist: {req.url}",
        action_label=f"Playlist Import ({_playlist_mode})" if _playlist_mode else "Playlist Import",
        input_params={"artist": req.artist, "title": req.title,
                      "normalize": req.normalize, "scrape": req.scrape,
                      "scrape_musicbrainz": req.scrape_musicbrainz,
                      "is_cover": req.is_cover, "is_live": req.is_live,
                      "is_alternate": req.is_alternate,
                      "is_uncensored": req.is_uncensored,
                      "alternate_version_label": req.alternate_version_label},
        started_at=datetime.now(timezone.utc),
    )
    db.add(parent)
    db.commit()
    db.refresh(parent)

    # Extract playlist entries (this blocks briefly)
    try:
        entries = extract_playlist_entries(req.url)
    except Exception as e:
        parent.status = JobStatus.failed
        parent.error_message = f"Failed to extract playlist: {e}"
        parent.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(parent)
        return parent

    if not entries:
        parent.status = JobStatus.failed
        parent.error_message = "Playlist is empty or could not be read"
        parent.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(parent)
        return parent

    parent.display_name = f"Playlist ({len(entries)} videos)"
    parent.current_step = f"Queuing {len(entries)} videos"

    # Collect video IDs already in the library so we can skip them
    from app.services.url_utils import identify_provider as _identify
    existing_video_ids: set[str] = set()
    for entry in entries:
        try:
            _prov, _vid = _identify(entry["url"])
            hit = db.query(Source).filter(
                Source.provider == _prov,
                Source.source_video_id == _vid,
            ).first()
            if hit:
                existing_video_ids.add(entry["id"])
        except Exception:
            pass

    sub_job_ids = []
    child_specs = []
    skipped = 0
    for entry in entries:
        if entry["id"] in existing_video_ids:
            skipped += 1
            continue
        video_url = entry["url"]
        video_title = entry.get("title", video_url)
        child = ProcessingJob(
            job_type="import_url",
            status=JobStatus.queued,
            input_url=video_url,
            display_name=video_title or video_url,
            action_label=_child_label,
            input_params={"artist": req.artist, "title": req.title,
                          "normalize": req.normalize, "scrape": req.scrape,
                          "scrape_musicbrainz": req.scrape_musicbrainz,
                          "is_cover": req.is_cover, "is_live": req.is_live,
                          "is_alternate": req.is_alternate,
                          "is_uncensored": req.is_uncensored,
                          "alternate_version_label": req.alternate_version_label},
        )
        db.add(child)
        db.flush()
        sub_job_ids.append(child.id)
        child_specs.append({
            'job_id': child.id,
            'url': video_url,
            'artist': req.artist,
            'title': req.title,
            'normalize': req.normalize,
            'scrape': req.scrape,
            'scrape_musicbrainz': req.scrape_musicbrainz,
            'hint_cover': req.is_cover,
            'hint_live': req.is_live,
            'hint_alternate': req.is_alternate,
            'hint_uncensored': req.is_uncensored,
            'hint_alternate_label': req.alternate_version_label,
            'ai_auto_analyse': req.ai_auto_analyse,
            'ai_auto_fallback': req.ai_auto_fallback,
        })

    parent.input_params = {**(parent.input_params or {}), "sub_job_ids": sub_job_ids,
                           "count": len(entries), "skipped": skipped}
    if skipped:
        parent.display_name = f"Playlist ({len(entries)} videos, {skipped} already in library)"
    db.commit()

    # Dispatch parallel batch import (downloads overlap, DB writes serialize)
    dispatch_task(batch_import_task, parent_job_id=parent.id, child_specs=child_specs)

    # Schedule batch completion watcher
    dispatch_task(complete_batch_job_task, parent_job_id=parent.id, sub_job_ids=sub_job_ids)

    db.refresh(parent)
    return parent


@router.get("/formats/{video_id}")
def get_video_formats(video_id: int, db: Session = Depends(get_db)):
    """Fetch available download formats/resolutions for a video's source URL."""
    item = db.query(VideoItem).get(video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Video not found")

    source = next((s for s in item.sources), None)
    if not source:
        raise HTTPException(status_code=400, detail="No source URL found for this video")

    try:
        formats, _info = get_available_formats(source.original_url)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch formats: {e}")

    # Deduplicate by resolution, keeping the best bitrate per height
    seen: dict[int, dict] = {}
    for f in formats:
        height = f.get("height")
        vcodec = f.get("vcodec", "none")
        if not height or vcodec == "none":
            continue
        tbr = f.get("tbr") or 0
        if height not in seen or tbr > (seen[height].get("tbr") or 0):
            seen[height] = {
                "height": height,
                "width": f.get("width"),
                "label": f"{height}p",
                "format_id": f.get("format_id"),
                "ext": f.get("ext"),
                "vcodec": vcodec,
                "tbr": tbr,
            }

    resolutions = sorted(seen.values(), key=lambda r: r["height"], reverse=True)
    return {"resolutions": resolutions, "url": source.original_url}


@router.post("/redownload/{video_id}", response_model=JobOut)
def redownload_video(video_id: int, format_spec: Optional[str] = Query(None),
                     db: Session = Depends(get_db)):
    """Re-download a video from its original source URL. The old file is archived."""
    item = db.query(VideoItem).get(video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Video not found")

    # Find original URL from sources
    source = next((s for s in item.sources), None)
    if not source:
        raise HTTPException(status_code=400, detail="No source URL found for this video")

    input_params: dict = {"video_id": video_id}
    if format_spec:
        input_params["format_spec"] = format_spec

    job = ProcessingJob(
        job_type="redownload",
        status=JobStatus.queued,
        input_url=source.original_url,
        display_name=f"{item.artist} – {item.title} › Redownload",
        action_label="Redownload",
        input_params=input_params,
        video_id=video_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    dispatch_task(
        redownload_video_task,
        job_id=job.id,
        video_id=video_id,
        format_spec=format_spec,
    )

    return job


@router.post("/rescan/{video_id}", response_model=JobOut)
def rescan_metadata(video_id: int, from_disk: bool = False, db: Session = Depends(get_db)):
    """Force rescan metadata for a single video."""
    item = db.query(VideoItem).get(video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Video not found")

    _label = "Rescan from Disk" if from_disk else "Rescan"
    job = ProcessingJob(
        job_type="rescan",
        status=JobStatus.queued,
        video_id=video_id,
        action_label=_label,
        display_name=f"{item.artist} \u2013 {item.title} \u203a {_label}" if item.artist and item.title else None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    dispatch_task(rescan_metadata_task, job_id=job.id, video_id=video_id, from_disk=from_disk)
    return job


@router.post("/rescan-batch", response_model=BatchActionResponse)
def rescan_batch(req: BatchRescanRequest, db: Session = Depends(get_db)):
    """Force rescan metadata for multiple videos or entire library.

    Re-runs the metadata pipeline on each video.  Locked fields are
    respected — any field listed in a video's ``locked_fields`` will
    not be overwritten.  Videos with ``_all`` locked will have all
    text fields, entity links, and source links preserved.
    """

    # Block AI modes when no AI provider is configured
    if req.ai_auto or req.ai_only:
        _require_ai_provider(db)

    if req.video_ids:
        videos = db.query(VideoItem).filter(VideoItem.id.in_(req.video_ids)).all()
    else:
        videos = db.query(VideoItem).all()

    ids = [v.id for v in videos]

    # Create a parent job for tracking
    if req.from_disk:
        _batch_label = "Rescan from Disk"
    else:
        _batch_label = _rescan_action_label(
            ai_auto=req.ai_auto, ai_only=req.ai_only,
            scrape_wikipedia=req.scrape_wikipedia if req.scrape_wikipedia is not None else True,
            scrape_musicbrainz=req.scrape_musicbrainz if req.scrape_musicbrainz is not None else True,
            scrape_tmvdb=req.scrape_tmvdb if req.scrape_tmvdb is not None else False,
        )
    job = ProcessingJob(
        job_type="batch_rescan",
        status=JobStatus.queued,
        display_name=f"Batch Rescan ({len(ids)} videos)",
        action_label=f"Batch {_batch_label}",
        input_params={"video_ids": ids, "count": len(ids)},
    )
    db.add(job)
    db.commit()

    sub_job_ids = []
    pipeline_opts = {
        k: v for k, v in {
            "scrape_wikipedia": req.scrape_wikipedia,
            "scrape_musicbrainz": req.scrape_musicbrainz,
            "scrape_tmvdb": req.scrape_tmvdb,
            "ai_auto": req.ai_auto,
            "ai_only": req.ai_only,
            "hint_cover": req.hint_cover,
            "hint_live": req.hint_live,
            "hint_alternate": req.hint_alternate,
            "normalize": req.normalize,
            "find_source_video": req.find_source_video,
            "from_disk": req.from_disk,
        }.items() if v is not None
    }
    # Pre-fetch display names for all videos in the batch
    _vid_names = {v.id: f"{v.artist} \u2013 {v.title}" for v in videos if v.artist and v.title}
    for vid in ids:
        _vn = _vid_names.get(vid)
        sub_job = ProcessingJob(
            job_type="rescan", status=JobStatus.queued, video_id=vid,
            action_label=_batch_label,
            display_name=f"{_vn} \u203a {_batch_label}" if _vn else None,
        )
        db.add(sub_job)
        db.flush()
        sub_job_ids.append(sub_job.id)
        dispatch_task(rescan_metadata_task, job_id=sub_job.id, video_id=vid, **pipeline_opts)

    # Mark parent batch job as running, then schedule completion check
    job.status = JobStatus.analyzing
    job.current_step = f"Rescanning {len(ids)} videos"
    job.started_at = datetime.now(timezone.utc)
    job.input_params = {**(job.input_params or {}), "sub_job_ids": sub_job_ids}
    db.commit()

    dispatch_task(complete_batch_job_task, parent_job_id=job.id, sub_job_ids=sub_job_ids)
    return BatchActionResponse(job_id=job.id, message=f"Queued rescan for {len(ids)} items")


@router.post("/normalize", response_model=BatchActionResponse)
def normalize_videos(req: NormalizeRequest, db: Session = Depends(get_db)):
    """Normalize audio for selected videos or entire library."""
    if req.video_ids:
        ids = req.video_ids
    else:
        ids = [v.id for v in db.query(VideoItem.id).all()]

    job = ProcessingJob(
        job_type="batch_normalize",
        status=JobStatus.queued,
        display_name=f"Batch Normalize ({len(ids)} videos)",
        action_label="Batch Normalize",
        input_params={"video_ids": ids, "target_lufs": req.target_lufs, "count": len(ids)},
    )
    db.add(job)
    db.commit()

    # Pre-fetch display names for normalize children
    _norm_vids = db.query(VideoItem).filter(VideoItem.id.in_(ids)).all()
    _norm_names = {v.id: f"{v.artist} \u2013 {v.title}" for v in _norm_vids if v.artist and v.title}
    sub_job_ids = []
    for vid in ids:
        _nn = _norm_names.get(vid)
        sub_job = ProcessingJob(job_type="normalize", status=JobStatus.queued, video_id=vid, action_label="Normalize",
                                display_name=f"{_nn} \u203a Normalize" if _nn else None)
        db.add(sub_job)
        db.flush()
        sub_job_ids.append(sub_job.id)
        dispatch_task(normalize_task, job_id=sub_job.id, video_id=vid, target_lufs=req.target_lufs)

    # Mark parent batch job as running, then schedule completion check
    job.status = JobStatus.analyzing
    job.current_step = f"Normalizing {len(ids)} videos"
    job.started_at = datetime.now(timezone.utc)
    job.input_params = {**(job.input_params or {}), "sub_job_ids": sub_job_ids}
    db.commit()

    dispatch_task(complete_batch_job_task, parent_job_id=job.id, sub_job_ids=sub_job_ids)
    return BatchActionResponse(job_id=job.id, message=f"Queued normalization for {len(ids)} items")


@router.post("/library-scan", response_model=JobOut)
def scan_library(req: LibraryScanRequest = LibraryScanRequest(), db: Session = Depends(get_db)):
    """Scan library directory for untracked files."""
    job = ProcessingJob(
        job_type="library_scan",
        status=JobStatus.queued,
        display_name="Library Scan",
        action_label="Library Scan",
        input_params={"import_new": req.import_new},
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    dispatch_task(library_scan_task, job_id=job.id, import_new=req.import_new)
    return job


@router.post("/library-duplicate-scan", response_model=JobOut)
def scan_duplicates(rescan_all: bool = False, db: Session = Depends(get_db)):
    """Scan the library for potential duplicate video items.

    Args:
        rescan_all: If True, ignore previously resolved duplicates and
                    re-scan everything from scratch. If False, honour
                    dismissed duplicate flags.
    """
    job = ProcessingJob(
        job_type="duplicate_scan",
        status=JobStatus.queued,
        display_name="Duplicate Scan" + (" (Full Rescan)" if rescan_all else ""),
        action_label="Duplicate Scan",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    dispatch_task(duplicate_scan_task, job_id=job.id, rescan_all=rescan_all)
    return job


class LibraryExportRequest(BaseModel):
    mode: str = "skip_existing"  # skip_existing | overwrite_new | overwrite_all


@router.post("/library-export", response_model=JobOut)
def export_library(req: LibraryExportRequest = LibraryExportRequest(), db: Session = Depends(get_db)):
    """Export NFOs, Playarr XMLs, and artwork for every video in the library."""
    if req.mode not in ("skip_existing", "overwrite_new", "overwrite_all"):
        raise HTTPException(400, f"Invalid export mode: {req.mode}")

    MODE_LABELS = {
        "skip_existing": "Skip Existing",
        "overwrite_new": "Overwrite New",
        "overwrite_all": "Overwrite All",
    }
    _export_label = f"Library Export ({MODE_LABELS[req.mode]})"
    job = ProcessingJob(
        job_type="library_export",
        status=JobStatus.queued,
        display_name=_export_label,
        action_label=_export_label,
        input_params={"mode": req.mode},
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    dispatch_task(library_export_task, job_id=job.id, mode=req.mode)
    return job


@router.post("/batch/delete")
def batch_delete_jobs(
    ids: List[int],
    db: Session = Depends(get_db),
):
    """Delete specific jobs by ID (only terminal-status jobs)."""
    terminal_statuses = [
        JobStatus.complete, JobStatus.failed,
        JobStatus.cancelled, JobStatus.skipped,
    ]
    deleted = []
    skipped = []
    for jid in ids:
        job = db.query(ProcessingJob).filter(ProcessingJob.id == jid).first()
        if job and job.status in terminal_statuses:
            db.delete(job)
            deleted.append(jid)
        else:
            skipped.append(jid)
    db.commit()
    return {"deleted": deleted, "skipped": skipped, "count": len(deleted)}


@router.delete("/history")
def clear_history(
    status: Optional[str] = Query(None, description="Only clear jobs with this status (complete, failed, cancelled, skipped)"),
    job_type: Optional[str] = Query(None, description="Only clear jobs matching this job_type prefix"),
    db: Session = Depends(get_db),
):
    """Delete completed/failed/cancelled/skipped jobs, optionally filtered."""
    terminal_statuses = [
        JobStatus.complete, JobStatus.failed,
        JobStatus.cancelled, JobStatus.skipped,
    ]
    query = db.query(ProcessingJob).filter(
        ProcessingJob.status.in_(terminal_statuses)
    )
    if status:
        query = query.filter(ProcessingJob.status == status)
    if job_type:
        query = query.filter(ProcessingJob.job_type.like(f"{job_type}%"))
    count = query.delete(synchronize_session="fetch")
    db.commit()
    return {"deleted": count}


@router.get("/", response_model=List[JobOut])
def list_jobs(
    status: Optional[str] = None,
    job_type: Optional[str] = None,
    limit: int = Query(200, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List recent processing jobs."""
    query = db.query(ProcessingJob).order_by(ProcessingJob.created_at.desc())

    if status:
        query = query.filter(ProcessingJob.status == status)
    if job_type:
        query = query.filter(ProcessingJob.job_type == job_type)

    return query.offset(offset).limit(limit).all()


# ---------------------------------------------------------------------------
# SSE Streaming — Real-time telemetry for active jobs
# These MUST be defined before /{job_id} to avoid FastAPI matching
# "stream"/"telemetry" as a job_id path parameter.
# ---------------------------------------------------------------------------

@router.get("/stream")
async def stream_telemetry():
    """
    Server-Sent Events endpoint streaming real-time telemetry for all active jobs.

    Emits JSON telemetry snapshots every 500ms.
    Event types:
        - telemetry: Full snapshot of all active job metrics
        - heartbeat: Keep-alive ping (every 15s if no data)
    """
    async def event_generator():
        q = telemetry_store.subscribe()
        heartbeat_interval = 15
        last_heartbeat = asyncio.get_event_loop().time()
        try:
            while True:
                try:
                    # Push telemetry snapshots every 500ms
                    snap = telemetry_store.snapshot_all()
                    if snap:
                        data = json.dumps(snap, default=str)
                        yield f"event: telemetry\ndata: {data}\n\n"
                        last_heartbeat = asyncio.get_event_loop().time()
                    else:
                        now = asyncio.get_event_loop().time()
                        if now - last_heartbeat > heartbeat_interval:
                            yield f"event: heartbeat\ndata: {{}}\n\n"
                            last_heartbeat = now

                    await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    break
        finally:
            telemetry_store.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/telemetry")
def get_all_telemetry():
    """Get current telemetry snapshot for all active jobs (polling fallback)."""
    return telemetry_store.snapshot_all()


@router.get("/logs/history")
def list_historical_logs():
    """List all persisted per-job log files (survives app restarts)."""
    import os
    from app.config import get_settings
    jobs_log_dir = os.path.join(get_settings().log_dir, "jobs")
    if not os.path.isdir(jobs_log_dir):
        return []
    entries = []
    for fname in sorted(os.listdir(jobs_log_dir)):
        if fname.endswith(".log"):
            fpath = os.path.join(jobs_log_dir, fname)
            entries.append({
                "filename": fname,
                "job_id": fname.replace(".log", ""),
                "size_bytes": os.path.getsize(fpath),
                "modified": datetime.fromtimestamp(
                    os.path.getmtime(fpath), tz=timezone.utc
                ).isoformat(),
            })
    return entries


@router.get("/logs/history/{job_id}")
def read_historical_log(job_id: int, tail: Optional[int] = Query(None, description="Return only the last N lines")):
    """Read a persisted per-job log file from disk."""
    import os
    from app.config import get_settings
    log_path = os.path.join(get_settings().log_dir, "jobs", f"{job_id}.log")
    if not os.path.isfile(log_path):
        raise HTTPException(status_code=404, detail="Log file not found")
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    if tail and tail > 0:
        lines = lines[-tail:]
    return {"job_id": job_id, "log_text": "".join(lines), "total_lines": len(lines)}


@router.get("/logs/app")
def read_app_log(
    tail: int = Query(200, description="Return last N lines of the application log"),
    file: str = Query("playarr.log", description="Log filename (e.g. playarr.log, playarr.log.1)"),
):
    """Read the persistent application log (playarr.log) or a rotated backup."""
    import os
    from app.config import get_settings

    # Sanitise filename — only allow known log filenames
    safe_name = os.path.basename(file)
    if not (safe_name == "playarr.log" or
            (safe_name.startswith("playarr.log.") and safe_name.split(".")[-1].isdigit())):
        raise HTTPException(status_code=400, detail="Invalid log filename")

    log_path = os.path.join(get_settings().log_dir, safe_name)
    if not os.path.isfile(log_path):
        raise HTTPException(status_code=404, detail="Application log not found")
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    total = len(lines)
    if tail and tail > 0:
        lines = lines[-tail:]
    return {"log_text": "".join(lines), "total_lines": total, "file": safe_name}


@router.get("/logs/files")
def list_log_files(db: Session = Depends(get_db)):
    """List all available log files (app logs, rotated backups, job logs, and scraper test logs)."""
    import os
    from app.config import get_settings
    log_dir = get_settings().log_dir
    jobs_dir = os.path.join(log_dir, "jobs")
    scraper_dir = os.path.join(log_dir, "scraper_tests")

    result: list[dict] = []

    # Application logs (playarr.log + rotated backups)
    for fname in sorted(os.listdir(log_dir)):
        fpath = os.path.join(log_dir, fname)
        if not os.path.isfile(fpath):
            continue
        if fname.startswith("playarr.log"):
            try:
                stat = os.stat(fpath)
                result.append({
                    "filename": fname,
                    "category": "app",
                    "size_bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "label": "Current Log" if fname == "playarr.log"
                             else f"Backup {fname.split('.')[-1]}",
                })
            except OSError:
                pass

    # Build a map of job_id -> (job_type, display_name) from the DB
    job_meta: dict[str, tuple[str, str | None]] = {}
    try:
        rows = db.query(
            ProcessingJob.id, ProcessingJob.job_type, ProcessingJob.display_name
        ).all()
        for jid, jtype, dname in rows:
            job_meta[str(jid)] = (jtype or "", dname)
    except Exception:
        pass

    # Per-job logs
    if os.path.isdir(jobs_dir):
        for fname in sorted(os.listdir(jobs_dir), reverse=True):
            if not fname.endswith(".log"):
                continue
            fpath = os.path.join(jobs_dir, fname)
            try:
                stat = os.stat(fpath)
                job_id_str = fname.replace(".log", "")
                jtype, dname = job_meta.get(job_id_str, ("", None))
                result.append({
                    "filename": f"jobs/{fname}",
                    "category": "job",
                    "size_bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "label": dname or f"Job {job_id_str}",
                    "job_id": job_id_str,
                    "job_type": jtype,
                })
            except OSError:
                pass

    # Scraper test logs
    if os.path.isdir(scraper_dir):
        for fname in sorted(os.listdir(scraper_dir), reverse=True):
            if not fname.endswith(".txt"):
                continue
            fpath = os.path.join(scraper_dir, fname)
            try:
                stat = os.stat(fpath)
                # Derive a friendlier label from filename like "20260402_101803_AC_DC_Back_In_Black.txt"
                base = fname.rsplit(".", 1)[0]
                parts = base.split("_", 2)
                if len(parts) >= 3:
                    label = parts[2].replace("_", " ")
                else:
                    label = base
                result.append({
                    "filename": f"scraper_tests/{fname}",
                    "category": "scraper_test",
                    "size_bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "label": label,
                })
            except OSError:
                pass

    return result


@router.get("/logs/directory")
def get_log_directory():
    """Return the absolute path to the log directory."""
    from app.config import get_settings
    log_dir = get_settings().log_dir
    abs_path = os.path.abspath(log_dir)
    os.makedirs(abs_path, exist_ok=True)
    return {"path": abs_path}


@router.get("/logs/read")
def read_log_file(
    file: str = Query(..., description="Relative log filename (e.g. playarr.log, jobs/42.log)"),
    tail: Optional[int] = Query(None, description="Return only last N lines"),
    offset: int = Query(0, description="Skip first N lines"),
    limit: int = Query(5000, description="Max lines to return"),
):
    """Read any log file by relative path within the log directory."""
    import os
    from app.config import get_settings
    log_dir = get_settings().log_dir

    # Sanitise: resolve and ensure the path stays inside log_dir
    requested = os.path.normpath(os.path.join(log_dir, file))
    if not requested.startswith(os.path.normpath(log_dir) + os.sep) and \
       requested != os.path.normpath(log_dir):
        raise HTTPException(status_code=400, detail="Invalid log path")
    if not os.path.isfile(requested):
        raise HTTPException(status_code=404, detail="Log file not found")

    with open(requested, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()

    total = len(all_lines)
    if tail and tail > 0:
        lines = all_lines[-tail:]
    else:
        lines = all_lines[offset: offset + limit]

    return {
        "file": file,
        "log_text": "".join(lines),
        "total_lines": total,
        "returned_lines": len(lines),
        "offset": offset,
    }


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    """Get a specific job's status."""
    job = db.query(ProcessingJob).get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/{job_id}/log", response_model=JobLogOut)
def get_job_log(job_id: int, db: Session = Depends(get_db)):
    """Get the full log text for a job (from database)."""
    job = db.query(ProcessingJob).get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobLogOut(id=job.id, log_text=job.log_text)


@router.post("/{job_id}/retry", response_model=JobOut)
def retry_job(job_id: int, db: Session = Depends(get_db)):
    """Retry a failed or cancelled job."""
    job = db.query(ProcessingJob).get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (JobStatus.failed, JobStatus.cancelled):
        raise HTTPException(status_code=400, detail="Only failed or cancelled jobs can be retried")

    job.status = JobStatus.queued
    job.error_message = None
    job.retry_count += 1
    db.commit()

    if job.job_type == "import_url" and job.input_url:
        params = job.input_params or {}
        dispatch_task(
            import_video_task,
            job_id=job.id,
            url=job.input_url,
            artist_override=params.get("artist"),
            title_override=params.get("title"),
            normalize=params.get("normalize", True),
            scrape=params.get("scrape", True),
            hint_cover=params.get("is_cover", False),
            hint_live=params.get("is_live", False),
            hint_alternate=params.get("is_alternate", False),
            hint_uncensored=params.get("is_uncensored", False),
            hint_alternate_label=params.get("alternate_version_label"),
        )
    elif job.job_type == "rescan" and job.video_id:
        dispatch_task(rescan_metadata_task, job_id=job.id, video_id=job.video_id)
    elif job.job_type == "normalize" and job.video_id:
        target = (job.input_params or {}).get("target_lufs")
        dispatch_task(normalize_task, job_id=job.id, video_id=job.video_id, target_lufs=target)
    elif job.job_type == "wikipedia_scrape" and job.video_id:
        dispatch_task(scrape_wikipedia_task, job_id=job.id, video_id=job.video_id)
    elif job.job_type == "redownload" and job.video_id:
        format_spec = (job.input_params or {}).get("format_spec")
        dispatch_task(redownload_video_task, job_id=job.id, video_id=job.video_id, format_spec=format_spec)
    elif job.job_type == "playlist_import" and job.input_url:
        # Re-submit as a new playlist import (retry the parent)
        job.status = JobStatus.failed
        job.error_message = "Playlist imports cannot be retried directly. Please re-submit the URL."
        db.commit()

    db.refresh(job)
    return job


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: int, db: Session = Depends(get_db)):
    """Cancel a queued or active job (cascades to sub-jobs for playlists)."""
    from app.worker import request_cancel
    from app.database import CosmeticSessionLocal

    job = db.query(ProcessingJob).get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in (JobStatus.cancelled, JobStatus.skipped):
        raise HTTPException(status_code=400, detail=f"Job already {job.status.value}")
    # Allow force-cancelling completed batch jobs (they can appear stuck)
    BATCH_TYPES = {"batch_rescan", "batch_import", "batch_normalize", "library_import", "playlist_import"}
    if job.status == JobStatus.complete and job.job_type not in BATCH_TYPES:
        raise HTTPException(status_code=400, detail="Job already complete")

    # Flag the job(s) for cancellation in-memory — pipeline threads check this
    request_cancel(job.id)
    sub_job_ids = (job.input_params or {}).get("sub_job_ids", [])
    for sid in sub_job_ids:
        request_cancel(sid)

    # Write status to DB using the cosmetic session (15 s busy_timeout) so
    # the HTTP response returns quickly even under heavy pipeline lock
    # contention.  Retry up to 5 times with backoff to survive DB lock storms.
    import time as _time
    db_updated = False
    for _attempt in range(5):
        cdb = CosmeticSessionLocal()
        try:
            cjob = cdb.query(ProcessingJob).get(job_id)
            if cjob and cjob.status not in (JobStatus.cancelled, JobStatus.skipped):
                cjob.status = JobStatus.cancelled
                cjob.error_message = "Cancelled by user"
                cjob.completed_at = cjob.completed_at or datetime.now(timezone.utc)
            for sid in sub_job_ids:
                csub = cdb.query(ProcessingJob).get(sid)
                if csub and csub.status not in (JobStatus.complete, JobStatus.failed, JobStatus.cancelled, JobStatus.skipped):
                    csub.status = JobStatus.cancelled
                    csub.error_message = "Parent job cancelled"
                    csub.completed_at = datetime.now(timezone.utc)
            cdb.commit()
            db_updated = True
            break
        except Exception as _cancel_exc:
            cdb.rollback()
            if "database is locked" in str(_cancel_exc) and _attempt < 4:
                _time.sleep(1.0 * (_attempt + 1))
                continue
            logger.warning(f"Cancel DB write failed after {_attempt+1} attempts: {_cancel_exc}")
        finally:
            cdb.close()

    # If cosmetic session failed (e.g. DB locked), fall back to request session
    if not db_updated:
        for _fb_attempt in range(3):
            try:
                db.expire_all()
                fb_job = db.query(ProcessingJob).get(job_id)
                if fb_job and fb_job.status not in (JobStatus.cancelled, JobStatus.skipped):
                    fb_job.status = JobStatus.cancelled
                    fb_job.error_message = "Cancelled by user"
                    fb_job.completed_at = fb_job.completed_at or datetime.now(timezone.utc)
                for sid in sub_job_ids:
                    subj = db.query(ProcessingJob).get(sid)
                    if subj and subj.status not in (JobStatus.complete, JobStatus.failed, JobStatus.cancelled, JobStatus.skipped):
                        subj.status = JobStatus.cancelled
                        subj.error_message = "Parent job cancelled"
                        subj.completed_at = datetime.now(timezone.utc)
                db.commit()
                db_updated = True
                break
            except Exception as _fb_exc:
                db.rollback()
                if "database is locked" in str(_fb_exc) and _fb_attempt < 2:
                    _time.sleep(2.0 * (_fb_attempt + 1))
                    continue
                logger.warning(f"Cancel fallback DB write failed: {_fb_exc}")

    # Re-read from the request session so the response reflects current DB state
    db.expire_all()
    job = db.query(ProcessingJob).get(job_id)
    return job


@router.get("/{job_id}/telemetry")
def get_job_telemetry(job_id: int):
    """Get current telemetry for a specific job."""
    snap = telemetry_store.snapshot(job_id)
    if snap is None:
        return {"job_id": job_id, "active": False}
    return {**snap, "active": True}
