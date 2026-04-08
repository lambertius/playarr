"""
Playarr Background Tasks â€” Celery task definitions.

Job Pipeline State Machine:
    queued â†’ downloading â†’ downloaded â†’ remuxing â†’ analyzing â†’
    normalizing â†’ tagging â†’ writing_nfo â†’ asset_fetch â†’ complete

Failure at any step â†’ failed (with error details + retry logic)

Each task is idempotent: safe to re-run. Partial state is tracked
via ProcessingJob records so cleanup/resume is possible.
"""
import json
import logging
import os
import random
import traceback
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from app.worker import celery_app
from app.database import SessionLocal, CosmeticSessionLocal
from app.models import (
    VideoItem, Source, QualitySignature, ProcessingJob, JobStatus,
    MetadataSnapshot, MediaAsset, Genre, NormalizationHistory,
    SourceProvider, video_genres,
)
from app.services.url_utils import identify_provider, canonicalize_url
from app.services.downloader import (
    download_video, get_available_formats, extract_metadata_from_ytdlp,
    get_best_thumbnail_url,
)
from app.services.media_analyzer import (
    extract_quality_signature, derive_resolution_label, measure_loudness,
    compare_quality,
)
from app.services.file_organizer import (
    organize_file, write_nfo_file, scan_library_directory, parse_folder_name,
    build_folder_name, sanitize_filename,
)
from app.services.metadata_resolver import (
    resolve_metadata, extract_artist_title, download_image, clean_title,
    search_wikipedia, scrape_wikipedia_page, detect_article_mismatch,
    search_imdb_music_video, search_musicbrainz,
    search_wikipedia_artist, search_wikipedia_album,
    _find_parent_album, _init_musicbrainz,
)
from app.scraper.unified_metadata import resolve_metadata_unified
from app.services.normalizer import normalize_video
from app.services.preview_generator import generate_preview
from app.services.ai_summary import generate_ai_summary
from app.services.artwork_manager import process_artist_album_artwork
from app.services.telemetry import telemetry_store
from app.services.retry_policy import decide_retry, should_auto_retry, MAX_ATTEMPTS
from app.config import get_settings

# AI enrichment subsystem
from app.ai.metadata_service import enrich_video_metadata
from app.ai.scene_analysis import analyze_scenes as ai_analyze_scenes

# New metadata architecture imports
from app.metadata.resolver import (
    resolve_artist, resolve_album, resolve_track,
    get_or_create_artist, get_or_create_album, get_or_create_track,
)
from app.metadata.assets import download_entity_assets, get_cached_asset_path
from app.metadata.revisions import save_revision
from app.metadata.exporters.kodi import (
    export_artist, export_album, export_video as export_video_kodi, export_all as export_all_kodi,
    clean_stale_exports,
)
from app.matching.resolver import resolve_video as matching_resolve_video
from app.matching.normalization import make_comparison_key
from app.services.canonical_track import (
    get_or_create_canonical_track, link_video_to_canonical_track,
    should_skip_ai_metadata, mark_canonical_ai_verified,
)
from app.worker import is_cancelled, clear_cancel, request_cancel, JobCancelledError, dispatch_task

import threading

logger = logging.getLogger(__name__)

# Serialise DB-write phases of the import pipeline.  SQLite (even in WAL
# mode) allows only ONE concurrent writer.  Without serialisation, 8+
# threads all trying to flush/commit simultaneously starve each other for
# the RESERVED lock (busy_timeout 30s) and every job stalls.
#
# The lock is acquired ONLY around the DB-write phases (entity creation,
# VideoItem save, source records, commit).  All non-DB work â€” downloads,
# ffprobe analysis, metadata resolution (MusicBrainz / Wikipedia / AI),
# audio normalization, file organization, NFO writing, poster download,
# entity resolution Phase 1 (network) â€” runs WITHOUT the lock so multiple
# pipeline threads can overlap their I/O-heavy phases.
_pipeline_lock = threading.Lock()


def _check_cancelled(job_id: int) -> None:
    """Raise JobCancelledError if the job has been flagged for cancellation."""
    if is_cancelled(job_id):
        raise JobCancelledError(f"Job {job_id} cancelled by user")


def _update_job(job_id: int, _retries: int = 10, **kwargs):
    """Update a processing job record via the centralised write queue.

    Fire-and-forget for cosmetic updates (progress, step).
    Blocking for terminal status changes (failed, complete, cancelled).
    """
    from app.pipeline_url.write_queue import db_write, db_write_soon
    from sqlalchemy.orm.attributes import flag_modified

    _is_terminal = False
    _status = kwargs.get("status")
    if _status is not None and hasattr(_status, "value"):
        _is_terminal = _status.value in ("failed", "complete", "cancelled", "skipped")

    _kw = dict(kwargs)  # snapshot for closure

    def _write():
        db = CosmeticSessionLocal()
        try:
            job = db.query(ProcessingJob).get(job_id)
            if job:
                # Guard: never overwrite 'cancelled' with a non-terminal status.
                if (job.status == JobStatus.cancelled
                        and _kw.get('status') not in (None, JobStatus.cancelled)):
                    return
                for k, v in _kw.items():
                    setattr(job, k, v)
                if "pipeline_steps" in _kw:
                    flag_modified(job, "pipeline_steps")
                db.commit()
        finally:
            db.close()

    if _is_terminal:
        try:
            db_write(_write)
        except Exception as exc:
            logger.error(f"_update_job(job={job_id}) failed: {exc}")
            _update_job_raw_fallback(job_id, **kwargs)
    else:
        db_write_soon(_write)


def _update_job_raw_fallback(job_id: int, **kwargs):
    """Last-resort raw sqlite3 write for critical job columns.

    Bypasses SQLAlchemy ORM entirely.  Only writes columns that can be
    expressed as simple SQL SET clauses (status, started_at, completed_at,
    progress_percent, current_step, error_message).  Skips complex columns
    like pipeline_steps (JSON) and log_text (handled by _append_job_log).
    """
    import sqlite3 as _sqlite3
    import time as _time
    from app.config import get_settings as _gs

    _db_url = _gs().database_url
    if not _db_url.startswith("sqlite"):
        return
    _db_path = _db_url.replace("sqlite:///", "").replace("sqlite://", "")

    # Map kwargs to simple SQL columns
    _ALLOWED = {"status", "started_at", "completed_at", "progress_percent",
                "current_step", "error_message", "celery_task_id"}
    sets = []
    vals = []
    for col, val in kwargs.items():
        if col not in _ALLOWED:
            continue
        if col == "status" and hasattr(val, "value"):
            val = val.value  # JobStatus enum â†’ string
        if isinstance(val, datetime):
            val = val.isoformat()
        sets.append(f"{col}=?")
        vals.append(val)
    if not sets:
        return

    # Guard: check if already cancelled before writing
    wc = None
    for attempt in range(5):
        try:
            wc = _sqlite3.connect(_db_path, timeout=30)
            wc.execute("PRAGMA journal_mode=WAL")
            wc.execute("PRAGMA busy_timeout=30000")
            cur_status = wc.execute(
                "SELECT status FROM processing_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if cur_status and cur_status[0] == "cancelled":
                new_status = kwargs.get("status")
                if hasattr(new_status, "value"):
                    new_status = new_status.value
                if new_status not in (None, "cancelled"):
                    return
            sets.append("updated_at=?")
            vals.append(datetime.now(timezone.utc).isoformat())
            vals.append(job_id)
            wc.execute(
                f"UPDATE processing_jobs SET {', '.join(sets)} WHERE id=?",
                tuple(vals),
            )
            wc.commit()
            logger.info(f"_update_job(job={job_id}) raw fallback succeeded")
            return
        except Exception as exc:
            logger.warning(f"_update_job(job={job_id}) raw fallback attempt {attempt+1}/5: {exc}")
            if attempt < 4:
                _time.sleep(2.0 * (attempt + 1))
        finally:
            if wc:
                try:
                    wc.close()
                except Exception:
                    pass
    logger.error(f"_update_job(job={job_id}) raw fallback also failed â€” status update lost")


def _append_job_log(job_id: int, message: str, _retries: int = 10):
    """Append a log message to a processing job (with retry for transient SQLite locks).

    Also writes to a persistent per-job log file in <log_dir>/jobs/<job_id>.log
    so that full history is available after app restarts.
    """
    import time as _time
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    log_line = f"[{timestamp}] {message}\n"

    # --- Persist to file (fire-and-forget, never blocks the pipeline) ---
    try:
        from app.config import get_settings as _gs
        _log_dir = os.path.join(_gs().log_dir, "jobs")
        os.makedirs(_log_dir, exist_ok=True)
        with open(os.path.join(_log_dir, f"{job_id}.log"), "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception:
        pass  # never crash for file logging

    # --- Persist to database ---
    from app.pipeline_url.write_queue import db_write_soon

    _log_line = log_line  # capture for closure

    def _write():
        db = CosmeticSessionLocal()
        try:
            job = db.query(ProcessingJob).get(job_id)
            if job:
                current = job.log_text or ""
                job.log_text = f"{current}{_log_line}"
                db.commit()
        finally:
            db.close()

    db_write_soon(_write)


def _get_or_create_genre(db, genre_name: str) -> Genre:
    """Get existing genre or create new one (properly capitalised)."""
    from app.services.metadata_resolver import capitalize_genre
    normalised = capitalize_genre(genre_name)
    genre = db.query(Genre).filter(Genre.name == normalised).first()
    if not genre:
        genre = Genre(name=normalised)
        db.add(genre)
        db.flush()
    return genre


def _set_pipeline_step(job_id: int, step: str, status: str, _retries: int = 10):
    """Record a pipeline step result via write queue (fire-and-forget)."""
    from app.pipeline_url.write_queue import db_write_soon
    from sqlalchemy.orm.attributes import flag_modified

    _step = step
    _status = status

    def _write():
        db = CosmeticSessionLocal()
        try:
            job = db.query(ProcessingJob).get(job_id)
            if job:
                steps = list(job.pipeline_steps or [])
                steps.append({"step": _step, "status": _status})
                job.pipeline_steps = steps
                flag_modified(job, "pipeline_steps")
                db.commit()
        finally:
            db.close()

    db_write_soon(_write)


def _get_setting_str(db, key: str, default: str = "") -> str:
    """Read a global setting from the DB (within current session)."""
    from app.models import AppSetting
    row = db.query(AppSetting).filter(
        AppSetting.key == key,
        AppSetting.user_id.is_(None),
    ).first()
    return row.value if row else default


def _get_setting_bool(db, key: str, default: bool = False) -> bool:
    """Read a boolean global setting from the DB."""
    val = _get_setting_str(db, key, str(default).lower())
    return val.lower() in ("true", "1", "yes")


def _save_metadata_snapshot(db, video_item: VideoItem, reason: str):
    """Save a snapshot of current metadata for undo."""
    snapshot_data = {
        "artist": video_item.artist,
        "title": video_item.title,
        "album": video_item.album,
        "year": video_item.year,
        "plot": video_item.plot,
        "genres": [g.name for g in video_item.genres],
        "mb_artist_id": video_item.mb_artist_id,
        "mb_recording_id": video_item.mb_recording_id,
        "mb_release_id": video_item.mb_release_id,
    }
    snapshot = MetadataSnapshot(
        video_id=video_item.id,
        snapshot_data=snapshot_data,
        reason=reason,
    )
    db.add(snapshot)


# ---------------------------------------------------------------------------
# Platform metadata backfill
# ---------------------------------------------------------------------------

def _backfill_source_platform_metadata(db, video_item, job_id: int, force: bool = False):
    """Re-fetch platform metadata from yt-dlp for Sources missing channel_name.

    When ``force=True`` always re-fetch — used by scrape-metadata to pick up
    corrected source URLs.
    """
    if not video_item.sources:
        return
    source = video_item.sources[0]
    if source.channel_name and not force:
        return  # already populated
    url = source.canonical_url or source.original_url
    if not url:
        return
    try:
        _append_job_log(job_id, "Refreshing platform metadata from yt-dlp...")
        _, info = get_available_formats(url)
        meta = extract_metadata_from_ytdlp(info)
        source.channel_name = meta.get("channel") or meta.get("uploader")
        source.platform_title = meta.get("title")
        source.platform_description = meta.get("description")
        source.platform_tags = meta.get("tags")
        source.upload_date = meta.get("upload_date")
        _append_job_log(job_id, f"Platform metadata: channel={source.channel_name}")
    except Exception as e:
        _append_job_log(job_id, f"Platform metadata backfill failed: {e}")
        logger.warning(f"Platform metadata backfill failed for video {video_item.id}: {e}")


# ---------------------------------------------------------------------------
# Processing state helpers
# ---------------------------------------------------------------------------

# Valid processing step names
PROCESSING_STEPS = (
    "imported",
    "downloaded",
    "metadata_resolved",
    "metadata_scraped",
    "metadata_ai_analyzed",
    "track_identified",
    "canonical_linked",
    "scenes_analyzed",
    "audio_normalized",
    "description_generated",
    "filename_checked",
    "file_organized",
    "nfo_exported",
    "xml_exported",
    "artwork_fetched",
    "thumbnail_selected",
    "ai_enriched",
)


def _merge_existing_xml_quality(db, video_item: VideoItem, folder_path: str):
    """Backfill QualitySignature fields from the existing XML sidecar.

    When the DB is missing quality data (e.g. loudness_lufs is NULL because
    the library scan had an autoflush bug or the field was never scraped),
    read the existing .playarr.xml and merge any non-null values so the
    subsequent XML write doesn't overwrite production data with nulls.
    """
    from app.services.playarr_xml import find_playarr_xml, parse_playarr_xml
    xml_path = find_playarr_xml(folder_path) if folder_path else None
    if not xml_path:
        return
    existing = parse_playarr_xml(xml_path)
    if not existing:
        return
    xml_q = existing.get("quality", {})
    if not xml_q:
        return
    qs = video_item.quality_signature
    if not qs:
        return
    for field in ("loudness_lufs", "audio_codec", "audio_bitrate",
                  "audio_sample_rate", "audio_channels",
                  "letterbox_scanned", "letterbox_detected",
                  "letterbox_crop_w", "letterbox_crop_h",
                  "letterbox_crop_x", "letterbox_crop_y",
                  "letterbox_bar_top", "letterbox_bar_bottom",
                  "letterbox_bar_left", "letterbox_bar_right"):
        db_val = getattr(qs, field, None)
        xml_val = xml_q.get(field)
        if db_val is None and xml_val is not None:
            setattr(qs, field, xml_val)


def _set_processing_flag(db, video_item: VideoItem, step: str, *,
                         method: str = "auto", version: str = "1.0"):
    """Mark a processing step as completed on a VideoItem.

    Args:
        db: Active SQLAlchemy session (caller must commit).
        video_item: The VideoItem ORM instance to update.
        step: One of PROCESSING_STEPS.
        method: How the step was completed (e.g. "import", "scraper", "ai", "manual").
        version: Arbitrary version string for future compatibility.
    """
    from sqlalchemy.orm.attributes import flag_modified
    state = dict(video_item.processing_state or {})
    state[step] = {
        "completed": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "version": version,
    }
    video_item.processing_state = state
    flag_modified(video_item, "processing_state")


def _clear_processing_flag(db, video_item: VideoItem, step: str):
    """Clear (remove) a processing step flag."""
    from sqlalchemy.orm.attributes import flag_modified
    state = dict(video_item.processing_state or {})
    state.pop(step, None)
    video_item.processing_state = state
    flag_modified(video_item, "processing_state")


def _is_processing_complete(video_item: VideoItem, step: str) -> bool:
    """Check whether a processing step has been marked as completed."""
    state = video_item.processing_state or {}
    entry = state.get(step)
    return bool(entry and entry.get("completed"))


def _safe_commit(db, retries: int = 5):
    """Commit with retry on transient SQLite 'database is locked' errors.

    On lock contention, rolls back, waits with exponential backoff, and
    retries.  Pending ORM modifications are lost after rollback — callers
    should use this ONLY for commits where losing the batch is acceptable
    (e.g. non-fatal sidecar sections) or re-apply changes after failure.
    """
    import time as _time
    for attempt in range(retries):
        try:
            db.commit()
            return
        except Exception as e:
            _err = str(e).lower()
            _cause = str(getattr(e, '__cause__', '') or '').lower()
            if ('database is locked' in _err or 'database is locked' in _cause
                    or 'has been rolled back' in _err):
                logger.warning(
                    "DB locked on commit (attempt %d/%d), retrying...",
                    attempt + 1, retries,
                )
                try:
                    db.rollback()
                except Exception:
                    pass
                _time.sleep(1.0 * (attempt + 1))
                continue
            raise
    # final attempt — let it raise
    db.commit()


def _purge_stale_scene_data(db, video_id: int):
    """Delete any existing scene analyses and thumbnails for a video ID.

    Handles ID recycling: when a video is deleted and SQLite reuses the ID
    for a new video, stale analysis/thumbnail data from the old video may
    persist.  This purges both DB records and the on-disk thumbnail directory.
    """
    from app.ai.models import AISceneAnalysis, AIThumbnail
    try:
        db.query(AIThumbnail).filter(AIThumbnail.video_id == video_id).delete()
        db.query(AISceneAnalysis).filter(AISceneAnalysis.video_id == video_id).delete()
        db.flush()
    except Exception as e:
        logger.warning(f"Failed to purge stale scene data for video {video_id}: {e}")
        db.rollback()
    # Also remove thumbnail files from disk
    try:
        import shutil
        thumb_dir = os.path.join(get_settings().asset_cache_dir, "thumbnails", str(video_id))
        if os.path.isdir(thumb_dir):
            shutil.rmtree(thumb_dir, ignore_errors=True)
    except Exception:
        pass


def _validate_video_entity_artwork(db, video_item, job_id: int):
    """Validate cached artwork for a video's linked entities (artist/album).

    Purges corrupt cached assets (HTML-as-jpg, zero-byte, etc.) so they
    are not carried forward into rescans, reimports, or entity re-resolution.
    This is a targeted validation â€” only checks assets linked to this video's
    artist and album entities, not the entire cache.
    """
    from app.metadata.models import CachedAsset
    from app.services.artwork_service import validate_file, _safe_delete

    entity_filters = []
    if hasattr(video_item, 'artist_entity_id') and video_item.artist_entity_id:
        entity_filters.append(("artist", video_item.artist_entity_id))
    if hasattr(video_item, 'album_entity_id') and video_item.album_entity_id:
        entity_filters.append(("album", video_item.album_entity_id))

    if not entity_filters:
        return

    for etype, eid in entity_filters:
        assets = db.query(CachedAsset).filter(
            CachedAsset.entity_type == etype,
            CachedAsset.entity_id == eid,
        ).all()
        for asset in assets:
            if not asset.local_cache_path or not os.path.isfile(asset.local_cache_path):
                if asset.status != "missing":
                    asset.status = "missing"
                    asset.validation_error = "File not found on disk"
                    asset.last_validated_at = datetime.now(timezone.utc)
                continue
            vr = validate_file(asset.local_cache_path)
            if vr.valid:
                if asset.status != "valid":
                    asset.status = "valid"
                    asset.width = vr.width
                    asset.height = vr.height
                    asset.file_size_bytes = vr.file_size_bytes
                    asset.file_hash = vr.file_hash
                    asset.validation_error = None
                    asset.last_validated_at = datetime.now(timezone.utc)
            else:
                _append_job_log(job_id, f"Purging corrupt cached {etype} artwork: {vr.error}")
                _safe_delete(asset.local_cache_path)
                asset.status = "invalid"
                asset.validation_error = vr.error
                asset.last_validated_at = datetime.now(timezone.utc)
    db.flush()


# ---------------------------------------------------------------------------
# Main import task
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, max_retries=3)
def import_video_task(self, job_id: int, url: str, artist_override: str = None,
                      title_override: str = None, normalize: bool = True,
                      scrape: bool = True,
                      scrape_musicbrainz: bool = True,
                      hint_cover: bool = False, hint_live: bool = False,
                      hint_alternate: bool = False,
                      hint_uncensored: bool = False,
                      hint_alternate_label: str = "",
                      ai_auto_analyse: bool = False,
                      ai_auto_fallback: bool = False,
                      format_spec: str = None):
    """
    Full import pipeline for a video URL.
    Delegates to the staged pipeline (workspace â†’ mutation plan â†’ serial apply).
    """
    from app.pipeline_url.stages import run_url_import_pipeline

    _update_job(job_id, status=JobStatus.downloading, started_at=datetime.now(timezone.utc),
                celery_task_id=getattr(getattr(self, 'request', None), 'id', None),
                display_name=f"Import: {url[:80]}")

    run_url_import_pipeline(
        job_id, url,
        artist_override=artist_override,
        title_override=title_override,
        normalize=normalize,
        scrape=scrape,
        scrape_musicbrainz=scrape_musicbrainz,
        hint_cover=hint_cover,
        hint_live=hint_live,
        hint_alternate=hint_alternate,
        hint_uncensored=hint_uncensored,
        hint_alternate_label=hint_alternate_label,
        ai_auto_analyse=ai_auto_analyse,
        ai_auto_fallback=ai_auto_fallback,
        format_spec=format_spec,
    )


# ---------------------------------------------------------------------------
# Batch import task â€” parallel downloads, serial DB writes
# ---------------------------------------------------------------------------

MAX_PARALLEL_DOWNLOADS = 4

@celery_app.task(bind=True, max_retries=0)
def batch_import_task(self, parent_job_id: int, child_specs: list):
    """
    Run multiple URL imports in parallel using ThreadPoolExecutor.
    Downloads overlap; DB writes serialize through the write queue.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from app.pipeline_url.stages import run_url_import_pipeline

    celery_id = getattr(getattr(self, 'request', None), 'id', None)

    def _run_one(spec):
        jid = spec['job_id']
        url = spec['url']
        _update_job(jid, status=JobStatus.downloading,
                    started_at=datetime.now(timezone.utc),
                    celery_task_id=celery_id,
                    display_name=f"Import: {url[:80]}")
        run_url_import_pipeline(
            jid, url,
            artist_override=spec.get('artist'),
            title_override=spec.get('title'),
            normalize=spec.get('normalize', True),
            scrape=spec.get('scrape', True),
            scrape_musicbrainz=spec.get('scrape_musicbrainz', True),
            hint_cover=spec.get('hint_cover', False),
            hint_live=spec.get('hint_live', False),
            hint_alternate=spec.get('hint_alternate', False),
            hint_uncensored=spec.get('hint_uncensored', False),
            hint_alternate_label=spec.get('hint_alternate_label', ''),
            ai_auto_analyse=spec.get('ai_auto_analyse', False),
            ai_auto_fallback=spec.get('ai_auto_fallback', False),
            format_spec=spec.get('format_spec'),
        )

    max_workers = min(len(child_specs), MAX_PARALLEL_DOWNLOADS)
    total = len(child_specs)
    completed = 0
    child_errors = 0

    with ThreadPoolExecutor(max_workers=max_workers,
                            thread_name_prefix="batch-dl") as pool:
        futures = {pool.submit(_run_one, s): s['job_id'] for s in child_specs}
        for fut in as_completed(futures):
            jid = futures[fut]
            try:
                fut.result()
            except Exception as e:
                child_errors += 1
                logger.error(f"[Batch {parent_job_id}] child {jid} raised: {e}")
            completed += 1
            _update_job(parent_job_id,
                        current_step=f"{completed}/{total} complete",
                        progress_percent=int((completed / total) * 100))

    # --- Finalize parent job directly ---
    if child_errors == total:
        _final_status = JobStatus.failed
        _final_msg = f"All {total} sub-jobs failed"
    elif child_errors > 0:
        _final_msg = f"Done ({total - child_errors} OK, {child_errors} failed)"
        _final_status = JobStatus.complete
    else:
        _final_msg = f"All {total} imports complete"
        _final_status = JobStatus.complete

    _update_job(
        parent_job_id,
        status=_final_status,
        current_step=_final_msg + " \u00b7 Album art & previews may still be processing",
        progress_percent=100,
        error_message=_final_msg if child_errors == total else None,
        completed_at=datetime.now(timezone.utc),
    )
    logger.info(f"[Batch {parent_job_id}] Finalized parent: {_final_status.value}")


# ---------------------------------------------------------------------------
# Redownload video task â€” replaces the video file ONLY, no metadata changes
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, max_retries=2)
def redownload_video_task(self, job_id: int, video_id: int, format_spec: str = None):
    """
    Replace the video file with a higher-quality download.

    This task ONLY:
    - Downloads the video at the requested format
    - Archives the old folder
    - Moves the new file to the library
    - Runs ffprobe analysis + updates QualitySignature
    - Normalizes audio
    - Regenerates the NFO from EXISTING metadata on the VideoItem
    - Updates file-related fields (file_path, folder_path, file_size_bytes, resolution_label)

    It does NOT touch: artist, title, album, year, plot, genres, MusicBrainz IDs,
    entity links, version fields, review status, or any other content metadata.
    """
    import tempfile
    import time as _dl_time
    import shutil

    _update_job(job_id, status=JobStatus.downloading, started_at=datetime.now(timezone.utc),
                celery_task_id=getattr(getattr(self, 'request', None), 'id', None))
    _append_job_log(job_id, f"Starting redownload for video_id={video_id}")
    _update_job(job_id, pipeline_steps=[])

    db = SessionLocal()
    _pipeline_locked = False
    try:
        # --- Load existing video item ---
        video_item = db.query(VideoItem).get(video_id)
        if not video_item:
            _update_job(job_id, status=JobStatus.failed, error_message="Video not found")
            return

        _update_job(job_id, display_name=f"{video_item.artist} \u2013 {video_item.title} \u203a Redownload")

        source = next((s for s in video_item.sources), None)
        if not source:
            _update_job(job_id, status=JobStatus.failed, error_message="No source URL")
            return

        url = source.original_url
        _append_job_log(job_id, f"Source URL: {url}")

        # --- Download (with retry + telemetry) ---
        _check_cancelled(job_id)
        _update_job(job_id, status=JobStatus.downloading, current_step="Downloading", progress_percent=10)

        temp_dir = tempfile.mkdtemp(prefix="playarr_rdl_")
        telemetry_store.create(job_id)

        def progress_cb(pct, msg, metrics=None):
            try:
                scaled = 10 + int(pct * 0.4)  # 10%â€“50%
                _update_job(job_id, progress_percent=scaled, current_step=f"Downloading ({pct}%)")
                if metrics:
                    telemetry_store.update_download(
                        job_id,
                        speed_bytes=metrics.get("speed_bytes"),
                        downloaded_bytes=metrics.get("downloaded_bytes"),
                        total_bytes=metrics.get("total_bytes"),
                        eta_seconds=metrics.get("eta_seconds"),
                        fragments_done=metrics.get("fragments_done"),
                        fragments_total=metrics.get("fragments_total"),
                        percent=metrics.get("percent"),
                    )
            except Exception as exc:
                logger.warning(f"progress_cb(job={job_id}): {exc}")

        downloaded_file = None
        current_attempt = 0
        _format_spec = format_spec
        last_error = ""

        while current_attempt < MAX_ATTEMPTS:
            current_attempt += 1

            if current_attempt > 1:
                decision = decide_retry(current_attempt - 1, last_error)
                if not decision.should_retry:
                    break
                _format_spec = decision.format_spec
                _append_job_log(job_id, f"Retry attempt {current_attempt}/{MAX_ATTEMPTS}: "
                                f"{decision.reason} (strategy: {decision.strategy_name})")
                _dl_time.sleep(decision.backoff_seconds)
                _check_cancelled(job_id)

            telemetry_store.start_attempt(
                job_id, current_attempt,
                strategy="redownload",
                reason=last_error[:200] if current_attempt > 1 else "redownload",
                format_spec=_format_spec or "auto",
            )

            try:
                _container = _get_setting_str(db, "preferred_container", "mkv")
                downloaded_file, _ = download_video(
                    url, temp_dir,
                    format_spec=_format_spec,
                    progress_callback=progress_cb,
                    cancel_check=lambda: _check_cancelled(job_id),
                    container=_container,
                )
                telemetry_store.end_attempt(job_id, "success")
                last_error = ""
                break
            except JobCancelledError:
                telemetry_store.end_attempt(job_id, "cancelled")
                raise
            except Exception as e:
                last_error = str(e)
                telemetry_store.end_attempt(job_id, "failed", error=last_error)
                logger.error(f"[Job {job_id}] Download attempt {current_attempt} failed: {e}")
                _append_job_log(job_id, f"Download attempt {current_attempt} failed: {e}")
                if not should_auto_retry(last_error):
                    break

        if not downloaded_file or not os.path.isfile(downloaded_file):
            _update_job(job_id, status=JobStatus.failed, error_message=last_error,
                        retry_count=current_attempt)
            _append_job_log(job_id, f"Download failed after {current_attempt} attempt(s): {last_error}")
            _set_pipeline_step(job_id, "download", "failed")
            telemetry_store.remove(job_id)
            return

        _append_job_log(job_id, f"Downloaded: {downloaded_file}")
        _set_pipeline_step(job_id, "download", "success")

        # Acquire pipeline lock for DB writes
        _pipeline_lock.acquire()
        _pipeline_locked = True

        # --- Analyze ---
        _check_cancelled(job_id)
        _update_job(job_id, status=JobStatus.analyzing, current_step="Analyzing media", progress_percent=55)

        try:
            sig = extract_quality_signature(downloaded_file)
            _set_pipeline_step(job_id, "analyze", "success")
        except Exception as e:
            _append_job_log(job_id, f"Analysis warning: {e}")
            sig = {}
            _set_pipeline_step(job_id, "analyze", "failed")

        resolution_label = derive_resolution_label(sig.get("height"))
        _append_job_log(job_id, f"Quality: {sig.get('width')}x{sig.get('height')} {resolution_label}")

        loudness = measure_loudness(downloaded_file)
        sig["loudness_lufs"] = loudness

        # --- Organize files (archive old, move new) ---
        _check_cancelled(job_id)
        _update_job(job_id, current_step="Organizing files", progress_percent=65)

        existing_folder = video_item.folder_path
        _existing_file_path = video_item.file_path  # capture before archive

        # Preserve artwork files (poster, thumb) from the old folder before
        # archiving -- organize_file archives the entire folder and these
        # non-video assets would otherwise be lost.
        _preserved_artwork: list = []
        if existing_folder and os.path.isdir(existing_folder):
            for _af in os.listdir(existing_folder):
                _af_lower = _af.lower()
                if _af_lower.endswith(('.jpg', '.png', '.jpeg')) and (
                    '-poster' in _af_lower or '-thumb' in _af_lower
                    or _af_lower.startswith('poster') or _af_lower.startswith('thumb')
                ):
                    _src_art = os.path.join(existing_folder, _af)
                    if os.path.isfile(_src_art):
                        _art_data = open(_src_art, 'rb').read()
                        _preserved_artwork.append((_af, _art_data))

        # Archive the existing folder first (with manifest for restore)
        _archive_dest = None
        if existing_folder and os.path.isdir(existing_folder):
            from app.services.file_organizer import archive_folder as _archive_folder
            _archive_dest = _archive_folder(existing_folder)
            _append_job_log(job_id, f"Archived original to: {_archive_dest}")

            # Write archive manifest with redownload reason
            try:
                from app.routers.video_editor import write_archive_manifest
                _archive_video = None
                for _afn in os.listdir(_archive_dest):
                    if os.path.splitext(_afn)[1].lower() in ('.mkv', '.mp4', '.webm', '.avi', '.mov', '.flv'):
                        _archive_video = os.path.join(_archive_dest, _afn)
                        break
                if _archive_video and _existing_file_path:
                    _rdl_settings = get_settings()
                    write_archive_manifest(
                        _archive_video,
                        _existing_file_path,
                        _rdl_settings.library_dir,
                        video_id=video_item.id,
                        artist=video_item.artist or "",
                        title=video_item.title or "",
                        archive_reason="redownload",
                    )
            except Exception as _e:
                _append_job_log(job_id, f"Archive manifest write warning: {_e}")

        new_folder, new_file = organize_file(
            downloaded_file,
            video_item.artist,
            video_item.title,
            resolution_label,
            version_type=video_item.version_type or "normal",
            alternate_version_label=video_item.alternate_version_label or "",
        )

        # Restore preserved artwork into the new folder, renaming to match
        # the new folder name convention.
        if _preserved_artwork:
            _new_base = os.path.basename(new_folder)
            for _orig_name, _art_bytes in _preserved_artwork:
                # Derive suffix: e.g. "-poster.jpg", "-thumb.jpg"
                _ext = os.path.splitext(_orig_name)[1]
                if '-poster' in _orig_name.lower():
                    _new_art_name = f"{_new_base}-poster{_ext}"
                elif '-thumb' in _orig_name.lower():
                    _new_art_name = f"{_new_base}-thumb{_ext}"
                else:
                    _new_art_name = _orig_name
                _dst_art = os.path.join(new_folder, _new_art_name)
                with open(_dst_art, 'wb') as _fw:
                    _fw.write(_art_bytes)
                _append_job_log(job_id, f"Poster restored: {_new_art_name}")

        _append_job_log(job_id, f"Organized to: {new_folder}")
        _set_pipeline_step(job_id, "organize", "success")

        # --- Normalize audio ---
        _update_job(job_id, status=JobStatus.normalizing, current_step="Normalizing audio", progress_percent=75)
        # Clear old flag first — the file has changed so prior normalization is stale
        _clear_processing_flag(db, video_item, "audio_normalized")
        before, after, gain = None, None, None
        try:
            before, after, gain = normalize_video(new_file)
            if before is not None:
                _append_job_log(job_id, f"Normalized: {before:.1f} -> {after:.1f} LUFS (gain: {gain:.2f}dB)")
                sig["loudness_lufs"] = after
                _set_pipeline_step(job_id, "normalize", "success")
                _set_processing_flag(db, video_item, "audio_normalized", method="redownload")
                # Clear review flag if it was set due to a prior normalization failure
                if (video_item.review_status == "needs_human_review"
                        and video_item.review_reason
                        and "normalization failed" in video_item.review_reason.lower()):
                    video_item.review_status = "none"
                    video_item.review_reason = None
                    video_item.review_category = None
            else:
                _append_job_log(job_id, "Normalization skipped or failed")
                _set_pipeline_step(job_id, "normalize", "skipped")
        except Exception as e:
            _append_job_log(job_id, f"Normalization error (non-fatal): {e}")
            _set_pipeline_step(job_id, "normalize", "failed")
            if video_item.review_status == "none":
                video_item.review_status = "needs_human_review"
                video_item.review_reason = "Audio normalization failed (possible codec incompatibility)"

        # --- Write NFO from EXISTING metadata ---
        _update_job(job_id, status=JobStatus.writing_nfo, current_step="Writing NFO", progress_percent=85)
        try:
            # Collect genres from existing video_item relationship
            existing_genres = [g.name for g in video_item.genres] if video_item.genres else []

            # Find canonical URL for NFO
            from app.services.url_utils import canonicalize_url as _canon_url
            try:
                _provider, _vid = identify_provider(url)
                canonical_url = _canon_url(_provider, _vid)
            except Exception:
                canonical_url = url

            write_nfo_file(
                new_folder,
                artist=video_item.artist,
                title=video_item.title,
                album=video_item.album or "",
                year=video_item.year,
                genres=existing_genres,
                plot=video_item.plot or "",
                source_url=canonical_url,
                resolution_label=resolution_label,
                version_type=video_item.version_type or "normal",
                alternate_version_label=video_item.alternate_version_label or "",
                original_artist=video_item.original_artist or "",
                original_title=video_item.original_title or "",
            )
            _append_job_log(job_id, "NFO written from existing metadata")
            _set_pipeline_step(job_id, "nfo", "success")
        except Exception as e:
            _append_job_log(job_id, f"NFO write error: {e}")
            _set_pipeline_step(job_id, "nfo", "failed")

        _set_processing_flag(db, video_item, "nfo_exported", method="redownload")

        # Write Playarr XML sidecar
        try:
            from app.services.playarr_xml import write_playarr_xml
            write_playarr_xml(video_item, db)
            _set_processing_flag(db, video_item, "xml_exported", method="redownload")
        except Exception as e:
            _append_job_log(job_id, f"Playarr XML write error: {e}")

        # --- Update ONLY file-related fields on video_item ---
        # Route the final commit through the centralised write queue so it
        # serialises with import-pipeline writes.  The original ``db``
        # session is NOT committed -- all state is replayed inside a fresh
        # session on the writer thread, eliminating SQLite lock contention.
        _update_job(job_id, current_step="Updating file metadata", progress_percent=90)

        # Capture values computed during the task so the write-queue
        # closure is fully self-contained.
        _final_processing_state = dict(video_item.processing_state or {})
        _file_size = os.path.getsize(new_file) if os.path.isfile(new_file) else None
        _vi_review_status = video_item.review_status
        _vi_review_reason = video_item.review_reason
        _vi_review_category = video_item.review_category
        _vi_id = video_item.id
        _snapshot_data = {
            "artist": video_item.artist,
            "title": video_item.title,
            "album": video_item.album,
            "year": video_item.year,
            "plot": video_item.plot,
            "genres": [g.name for g in video_item.genres] if video_item.genres else [],
            "mb_artist_id": video_item.mb_artist_id,
            "mb_recording_id": video_item.mb_recording_id,
            "mb_release_id": video_item.mb_release_id,
        }
        _sig_copy = dict(sig)
        _norm_before, _norm_after, _norm_gain = before, after, gain
        _norm_target = get_settings().normalization_target_lufs
        _new_folder_final = new_folder
        _new_file_final = new_file
        _resolution_final = resolution_label

        # Close the outer session -- we no longer need it for writes.
        db.rollback()
        db.close()

        from app.pipeline_url.write_queue import db_write
        from sqlalchemy.orm.attributes import flag_modified as _flag_modified

        def _apply_redownload_commit():
            _db = SessionLocal()
            try:
                _vi = _db.query(VideoItem).get(_vi_id)

                # Metadata snapshot (undo breadcrumb)
                _snap = MetadataSnapshot(
                    video_id=_vi_id,
                    snapshot_data=_snapshot_data,
                    reason="redownload",
                )
                _db.add(_snap)

                _vi.folder_path = _new_folder_final
                _vi.file_path = _new_file_final
                _vi.file_size_bytes = _file_size
                _vi.resolution_label = _resolution_final
                _vi.processing_state = _final_processing_state
                _vi.review_status = _vi_review_status
                _vi.review_reason = _vi_review_reason
                _vi.review_category = _vi_review_category
                _flag_modified(_vi, "processing_state")

                # Update MediaAsset file_path records
                for _ma in _db.query(MediaAsset).filter(
                    MediaAsset.video_id == _vi_id,
                ).all():
                    if _ma.file_path and os.path.dirname(_ma.file_path) != _new_folder_final:
                        _old_basename = os.path.basename(_ma.file_path)
                        _new_asset_path = os.path.join(_new_folder_final, _old_basename)
                        if os.path.isfile(_new_asset_path):
                            _ma.file_path = _new_asset_path
                        else:
                            _new_base = os.path.basename(_new_folder_final)
                            _ext = os.path.splitext(_old_basename)[1]
                            if _ma.asset_type == "poster":
                                _candidate = os.path.join(_new_folder_final, f"{_new_base}-poster{_ext}")
                            elif _ma.asset_type == "video_thumb":
                                _candidate = os.path.join(_new_folder_final, f"{_new_base}-thumb{_ext}")
                            else:
                                _candidate = ""
                            if _candidate and os.path.isfile(_candidate):
                                _ma.file_path = _candidate

                # Quality signature
                qs = _vi.quality_signature
                if not qs:
                    qs = QualitySignature(video_id=_vi_id)
                    _db.add(qs)
                for key, val in _sig_copy.items():
                    if hasattr(qs, key):
                        setattr(qs, key, val)

                # Normalization history
                if _norm_before is not None:
                    _db.add(NormalizationHistory(
                        video_id=_vi_id,
                        target_lufs=_norm_target,
                        measured_lufs_before=_norm_before,
                        measured_lufs_after=_norm_after,
                        gain_applied_db=_norm_gain,
                    ))

                # Link job to video & mark complete
                _job = _db.query(ProcessingJob).get(job_id)
                if _job:
                    _job.video_id = _vi_id
                    _job.status = JobStatus.complete
                    _job.progress_percent = 100
                    _job.current_step = "Complete"
                    _job.completed_at = datetime.now(timezone.utc)

                _db.commit()
            finally:
                _db.close()

        db_write(_apply_redownload_commit)
        _append_job_log(job_id, f"Redownload complete â€” {resolution_label}")
        telemetry_store.remove(job_id)

        # Clean up temp dir
        try:
            if os.path.isdir(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass

    except JobCancelledError:
        try:
            db.rollback()
        except Exception:
            pass
        clear_cancel(job_id)
        _update_job(job_id, status=JobStatus.cancelled,
                    error_message="Cancelled by user",
                    completed_at=datetime.now(timezone.utc))
        _append_job_log(job_id, "Job cancelled by user")
        telemetry_store.remove(job_id)
        try:
            import shutil as _shutil
            if 'temp_dir' in dir() and os.path.isdir(temp_dir):
                _shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        logger.error(f"[Job {job_id}] Redownload FATAL: {e}\n{traceback.format_exc()}")
        _update_job(job_id, status=JobStatus.failed, error_message=str(e),
                     completed_at=datetime.now(timezone.utc))
        _append_job_log(job_id, f"FATAL ERROR: {e}")
        telemetry_store.remove(job_id)
    finally:
        if _pipeline_locked:
            _pipeline_lock.release()
        try:
            db.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Rescan from disk — restore metadata from .playarr.xml sidecar
# ---------------------------------------------------------------------------

def _rescan_from_disk(job_id: int, video_id: int,
                      folder_path: str, file_path: str,
                      normalize: bool = False):
    """Read .playarr.xml and apply all stored metadata to the DB.

    Restores: identity, ratings, sources, loudness, processing state,
    genres, MusicBrainz IDs, artwork, and entity links.
    """
    from app.services.playarr_xml import find_playarr_xml, parse_playarr_xml
    from app.pipeline_url.db_apply import _upsert_source
    from app.pipeline_url.write_queue import db_write

    _update_job(job_id, current_step="Reading XML sidecar", progress_percent=10)

    xml_path = find_playarr_xml(folder_path) if folder_path else None
    if not xml_path:
        _update_job(job_id, status=JobStatus.failed,
                    error_message="No .playarr.xml file found in video folder")
        _append_job_log(job_id, f"No .playarr.xml found in: {folder_path}")
        return

    data = parse_playarr_xml(xml_path)
    if not data:
        _update_job(job_id, status=JobStatus.failed,
                    error_message="Failed to parse .playarr.xml")
        return

    _append_job_log(job_id, f"Parsed XML: {xml_path}")
    _set_pipeline_step(job_id, "Reading XML sidecar", "success")
    _check_cancelled(job_id)

    # Extract fields from parsed XML
    xml_artist = data.get("artist", "")
    xml_title = data.get("title", "")
    xml_album = data.get("album")
    xml_year = data.get("year")
    xml_plot = data.get("plot")
    xml_genres = data.get("genres", [])
    xml_version_type = data.get("version_type", "normal")
    xml_alt_label = data.get("alternate_version_label")
    xml_original_artist = data.get("original_artist")
    xml_original_title = data.get("original_title")

    # MusicBrainz IDs
    xml_mb_artist_id = data.get("mb_artist_id")
    xml_mb_recording_id = data.get("mb_recording_id")
    xml_mb_release_id = data.get("mb_release_id")
    xml_mb_release_group_id = data.get("mb_release_group_id")

    # Ratings
    xml_song_rating = data.get("song_rating")
    xml_song_rating_set = data.get("song_rating_set", False)
    xml_video_rating = data.get("video_rating")
    xml_video_rating_set = data.get("video_rating_set", False)

    # Processing state
    xml_processing_state = data.get("processing_state")

    # Sources
    xml_sources = data.get("sources", [])

    # Quality / loudness
    xml_quality = data.get("quality", {})
    xml_loudness = xml_quality.get("loudness_lufs") if xml_quality else None

    # Artwork
    xml_artwork = data.get("artwork", [])

    # File info
    xml_resolution_label = data.get("resolution_label")
    xml_audio_fingerprint = data.get("audio_fingerprint")
    xml_acoustid_id = data.get("acoustid_id")

    # Entity refs (for re-linking)
    xml_entity_refs = data.get("entity_refs", {})

    # Flags
    xml_locked_fields = data.get("locked_fields")
    xml_exclude_editor = data.get("exclude_from_editor_scan", False)
    xml_review_status = data.get("review_status")
    xml_review_reason = data.get("review_reason")
    xml_review_category = data.get("review_category")

    # Import method / provenance
    xml_import_method = data.get("import_method")

    # Field provenance (video-level)
    xml_field_provenance = data.get("field_provenance")

    # Related versions
    xml_related_versions = data.get("related_versions")

    _update_job(job_id, current_step="Applying XML data", progress_percent=30)

    # --- Resolve entities (network-free, uses cached MB data from XML) ---
    from app.metadata.resolver import (
        resolve_artist, resolve_album, resolve_track,
        get_or_create_artist, get_or_create_album, get_or_create_track,
    )
    from app.services.canonical_track import (
        get_or_create_canonical_track, link_video_to_canonical_track,
    )
    from app.metadata.revisions import save_revision

    resolved_artist = {}
    resolved_album = {}
    resolved_track = {}
    try:
        resolved_artist = resolve_artist(
            xml_artist, mb_artist_id=xml_mb_artist_id,
            skip_musicbrainz=True, skip_wikipedia=True,
        )
    except Exception as _e:
        _append_job_log(job_id, f"Artist resolution warning: {_e}")

    if xml_album:
        try:
            resolved_album = resolve_album(
                xml_artist, xml_album,
                mb_release_id=xml_mb_release_id,
                skip_musicbrainz=True, skip_wikipedia=True,
            )
        except Exception as _e:
            _append_job_log(job_id, f"Album resolution warning: {_e}")

    try:
        resolved_track = resolve_track(
            xml_artist, xml_title,
            mb_recording_id=xml_mb_recording_id,
            skip_musicbrainz=True, skip_wikipedia=True,
        )
    except Exception as _e:
        _append_job_log(job_id, f"Track resolution warning: {_e}")

    _check_cancelled(job_id)

    # ==================================================================
    # Write phase — apply all XML data to the database atomically
    # ==================================================================
    def _execute_from_disk_write():
        db = SessionLocal()
        try:
            video_item = db.query(VideoItem).get(video_id)
            if not video_item:
                _update_job(job_id, status=JobStatus.failed,
                            error_message="Video not found (write phase)")
                return

            # Pre-rescan snapshot
            _save_metadata_snapshot(db, video_item, "rescan_from_disk")

            # Identity fields — normalize feat credits to semicolons
            from app.services.source_validation import normalize_feat_to_semicolons, build_artist_ids
            _rescan_artist = xml_artist or video_item.artist
            video_item.artist = normalize_feat_to_semicolons(_rescan_artist)
            video_item.title = xml_title or video_item.title
            video_item.album = xml_album
            video_item.year = xml_year
            video_item.plot = xml_plot

            # Version info
            video_item.version_type = xml_version_type or "normal"
            video_item.alternate_version_label = xml_alt_label
            video_item.original_artist = xml_original_artist
            video_item.original_title = xml_original_title

            # MusicBrainz IDs
            video_item.mb_artist_id = xml_mb_artist_id
            video_item.mb_recording_id = xml_mb_recording_id
            video_item.mb_release_id = xml_mb_release_id
            video_item.mb_release_group_id = xml_mb_release_group_id

            # Artist IDs (rebuild from normalized artist + available MBIDs)
            video_item.artist_ids = build_artist_ids(
                video_item.artist,
                primary_mb_artist_id=xml_mb_artist_id,
            )

            # Genres
            if xml_genres:
                video_item.genres.clear()
                for g in xml_genres:
                    video_item.genres.append(_get_or_create_genre(db, g))

            # Ratings
            if xml_song_rating_set:
                video_item.song_rating = xml_song_rating
                video_item.song_rating_set = True
            if xml_video_rating_set:
                video_item.video_rating = xml_video_rating
                video_item.video_rating_set = True

            # Resolution label
            if xml_resolution_label:
                video_item.resolution_label = xml_resolution_label

            # Audio fingerprint / AcoustID
            if xml_audio_fingerprint:
                video_item.audio_fingerprint = xml_audio_fingerprint
            if xml_acoustid_id:
                video_item.acoustid_id = xml_acoustid_id

            # Processing state
            if xml_processing_state:
                video_item.processing_state = xml_processing_state

            # Locked fields
            if xml_locked_fields:
                video_item.locked_fields = xml_locked_fields

            # Exclude from editor scan
            video_item.exclude_from_editor_scan = xml_exclude_editor

            # Review status
            if xml_review_status:
                video_item.review_status = xml_review_status
            if xml_review_reason:
                video_item.review_reason = xml_review_reason
            if xml_review_category:
                video_item.review_category = xml_review_category

            # Import method
            if xml_import_method:
                video_item.import_method = xml_import_method

            # Field provenance
            if xml_field_provenance:
                video_item.field_provenance = xml_field_provenance

            # Related versions
            if xml_related_versions:
                video_item.related_versions = xml_related_versions

            # Full quality signature restore
            if xml_quality and video_item.quality_signature:
                qs = video_item.quality_signature
                for qfield in ("width", "height", "fps", "video_codec",
                                "video_bitrate", "hdr", "audio_codec",
                                "audio_bitrate", "audio_sample_rate",
                                "audio_channels", "container",
                                "duration_seconds", "loudness_lufs"):
                    val = xml_quality.get(qfield)
                    if val is not None:
                        setattr(qs, qfield, val)

            # Sources — restore all from XML
            # Clear existing non-video sources first, then upsert all
            from app.models import Source
            db.query(Source).filter(
                Source.video_id == video_id,
            ).delete(synchronize_session="fetch")

            for src_data in xml_sources:
                _upsert_source(db, video_id, {
                    "provider": src_data.get("provider", ""),
                    "source_video_id": src_data.get("source_video_id", ""),
                    "original_url": src_data.get("original_url", ""),
                    "canonical_url": src_data.get("canonical_url", ""),
                    "source_type": src_data.get("source_type", "video"),
                    "provenance": src_data.get("provenance", "xml_import"),
                    "channel_name": src_data.get("channel_name"),
                    "platform_title": src_data.get("platform_title"),
                    "upload_date": src_data.get("upload_date"),
                })
            if xml_sources:
                _append_job_log(job_id, f"Restored {len(xml_sources)} source(s) from XML")

            # Artwork — restore MediaAsset records
            from app.models import MediaAsset
            if xml_artwork:
                for art in xml_artwork:
                    art_path = art.get("file_path")
                    if art_path and os.path.isfile(art_path):
                        existing = db.query(MediaAsset).filter(
                            MediaAsset.video_id == video_id,
                            MediaAsset.asset_type == art["asset_type"],
                            MediaAsset.file_path == art_path,
                        ).first()
                        if not existing:
                            db.add(MediaAsset(
                                video_id=video_id,
                                asset_type=art["asset_type"],
                                file_path=art_path,
                                source_url=art.get("source_url"),
                                provenance=art.get("provenance", "xml_import"),
                                source_provider=art.get("source_provider"),
                                file_hash=art.get("file_hash"),
                                status=art.get("status", "valid"),
                                width=art.get("width"),
                                height=art.get("height"),
                                last_validated_at=datetime.now(timezone.utc),
                            ))
                _append_job_log(job_id, f"Restored {len(xml_artwork)} artwork asset(s) from XML")

            # Clear entity links for re-resolution
            video_item.artist_entity_id = None
            video_item.album_entity_id = None
            video_item.track_id = None
            db.flush()

            # Entity resolution
            artist_entity = None
            album_entity = None
            track_entity = None
            canonical_track = None

            if xml_artist:
                try:
                    with db.begin_nested():
                        artist_entity = get_or_create_artist(
                            db, xml_artist, resolved=resolved_artist,
                        )
                        save_revision(db, "artist", artist_entity.id, "auto_import", "rescan_from_disk")
                except Exception as _e:
                    _append_job_log(job_id, f"Artist entity warning: {_e}")

            if xml_album and artist_entity:
                try:
                    with db.begin_nested():
                        album_entity = get_or_create_album(
                            db, artist_entity, xml_album,
                            resolved=resolved_album,
                        )
                        save_revision(db, "album", album_entity.id, "auto_import", "rescan_from_disk")
                except Exception as _e:
                    _append_job_log(job_id, f"Album entity warning: {_e}")

            if xml_title and artist_entity:
                try:
                    with db.begin_nested():
                        track_entity = get_or_create_track(
                            db, artist_entity, album_entity, xml_title,
                            resolved=resolved_track,
                        )
                except Exception as _e:
                    _append_job_log(job_id, f"Track entity warning: {_e}")

            if artist_entity:
                try:
                    with db.begin_nested():
                        canonical_track, _ = get_or_create_canonical_track(
                            db,
                            title=xml_title,
                            year=xml_year,
                            mb_recording_id=xml_mb_recording_id,
                            mb_release_id=xml_mb_release_id,
                            mb_release_group_id=xml_mb_release_group_id,
                            mb_artist_id=xml_mb_artist_id,
                            version_type=xml_version_type or "normal",
                            original_artist=xml_original_artist,
                            original_title=xml_original_title,
                            genres=xml_genres,
                            resolved_track=resolved_track or None,
                            artist_entity=artist_entity,
                            album_entity=album_entity,
                        )
                except Exception as _e:
                    _append_job_log(job_id, f"Canonical track warning: {_e}")

            # Inherit album from canonical track if missing
            if not album_entity and canonical_track and canonical_track.album_id:
                from app.metadata.models import AlbumEntity
                _ct_album = db.query(AlbumEntity).get(canonical_track.album_id)
                if _ct_album:
                    album_entity = _ct_album
                    if not video_item.album:
                        video_item.album = _ct_album.title

            # Link video to entities
            if artist_entity:
                video_item.artist_entity_id = artist_entity.id
            if album_entity:
                video_item.album_entity_id = album_entity.id
            if track_entity:
                video_item.track_id = track_entity.id
            if canonical_track:
                link_video_to_canonical_track(db, video_item, canonical_track)

            # Processing flags
            _set_processing_flag(db, video_item, "metadata_scraped", method="rescan_from_disk")
            _set_processing_flag(db, video_item, "metadata_resolved", method="rescan_from_disk")
            if track_entity or canonical_track:
                _set_processing_flag(db, video_item, "track_identified", method="rescan_from_disk")
                _set_processing_flag(db, video_item, "canonical_linked", method="rescan_from_disk")

            # Promote scanned items to full library imports
            if video_item.import_method == "scanned":
                video_item.import_method = "import"
                _append_job_log(job_id, "Promoted import_method from 'scanned' to 'import'")

            # Post-rescan snapshot
            _save_metadata_snapshot(db, video_item, "rescan_from_disk_complete")

            # Rewrite NFO with restored data
            # Set xml_exported flag (the XML already existed before rescan)
            _set_processing_flag(db, video_item, "xml_exported", method="rescan_from_disk")

            try:
                from app.services.file_organizer import write_nfo_file
                if folder_path:
                    write_nfo_file(
                        folder_path,
                        artist=xml_artist,
                        title=xml_title,
                        album=xml_album or "",
                        year=xml_year,
                        genres=xml_genres,
                        plot=xml_plot or "",
                        source_url="",
                        resolution_label=xml_resolution_label or "",
                    )
                    _set_processing_flag(db, video_item, "nfo_exported", method="rescan_from_disk")
            except Exception as _nfo_e:
                _append_job_log(job_id, f"NFO rewrite warning: {_nfo_e}")

            # Compute Playarr content IDs
            try:
                from app.services.content_id import compute_ids_for_video
                ids = compute_ids_for_video(video_item)
                video_item.playarr_track_id = ids["playarr_track_id"]
                video_item.playarr_video_id = ids["playarr_video_id"]
            except Exception as _cid_e:
                _append_job_log(job_id, f"Content ID generation warning: {_cid_e}")

            db.commit()
            _append_job_log(job_id, "All XML data applied successfully")
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    db_write(_execute_from_disk_write)

    _set_pipeline_step(job_id, "Applying XML data", "success")
    _set_pipeline_step(job_id, "Resolving entities", "success")
    _update_job(job_id, status=JobStatus.complete, progress_percent=100,
                current_step="Rescan complete", completed_at=datetime.now(timezone.utc))
    _append_job_log(job_id, "Rescan from disk complete — dispatching deferred tasks")

    # Deferred tasks (preview, entity artwork, etc.)
    try:
        from app.pipeline_url.workspace import ImportWorkspace
        from app.pipeline_url.deferred import dispatch_deferred

        deferred_tasks = ["preview", "matching", "kodi_export",
                          "entity_artwork", "orphan_cleanup"]
        ws = ImportWorkspace(job_id)
        ws.log("Rescan-from-disk deferred tasks starting")
        dispatch_deferred(video_id, deferred_tasks, ws,
                          update_job_progress=False)
    except Exception as de:
        logger.error(f"Rescan-from-disk deferred dispatch failed: {de}")
        _append_job_log(job_id, f"Deferred dispatch failed: {de}")

    # Queue normalize as follow-up if requested
    if normalize and file_path:
        try:
            norm_db = SessionLocal()
            try:
                _nv = norm_db.query(VideoItem).get(video_id)
                _norm_display = f"{_nv.artist} – {_nv.title} › Normalize" if _nv and _nv.artist and _nv.title else None
                norm_job = ProcessingJob(
                    job_type="normalize", status=JobStatus.queued,
                    video_id=video_id,
                    action_label="Normalize",
                    display_name=_norm_display,
                )
                norm_db.add(norm_job)
                norm_db.commit()
                dispatch_task(normalize_task, job_id=norm_job.id, video_id=video_id)
                _append_job_log(job_id, "Normalize queued as follow-up")
            finally:
                norm_db.close()
        except Exception as ne:
            _append_job_log(job_id, f"Failed to queue normalize: {ne}")


# ---------------------------------------------------------------------------
# Rescan metadata task
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, max_retries=2)
def rescan_metadata_task(self, job_id: int, video_id: int,
                         scrape_wikipedia: bool = True,
                         scrape_musicbrainz: bool = True,
                         ai_auto: bool = False,
                         ai_only: bool = False,
                         hint_cover: bool = False,
                         hint_live: bool = False,
                         hint_alternate: bool = False,
                         hint_uncensored: bool = False,
                         normalize: bool = False,
                         find_source_video: bool = False,
                         from_disk: bool = False):
    """Rescan metadata for a single video item.

    Uses a two-phase architecture (same as the URL import pipeline):
      Phase A â€” read context + network I/O (no long-lived DB session)
      Phase B â€” single locked write phase (all mutations applied atomically)
    This prevents SQLite write-contention when multiple rescans run in parallel.
    """
    _update_job(job_id, status=JobStatus.tagging, started_at=datetime.now(timezone.utc),
                celery_task_id=getattr(getattr(self, 'request', None), 'id', None))

    try:
        _check_cancelled(job_id)

        # ==================================================================
        # PHASE A â€” Read context & network I/O (no long-held DB session)
        # ==================================================================

        # --- A1: Load video context into plain Python values ---
        rd = SessionLocal()
        try:
            video_item = rd.query(VideoItem).get(video_id)
            if not video_item:
                _update_job(job_id, status=JobStatus.failed, error_message="Video not found")
                return

            ctx_artist = video_item.artist
            ctx_title = video_item.title
            ctx_album = video_item.album
            ctx_year = video_item.year
            ctx_plot = video_item.plot
            ctx_file_path = video_item.file_path
            ctx_folder_path = video_item.folder_path
            ctx_resolution_label = video_item.resolution_label
            ctx_import_method = video_item.import_method
            ctx_genre_names = [g.name for g in video_item.genres]
            ctx_duration = (video_item.quality_signature.duration_seconds
                            if video_item.quality_signature else None)

            # Locked fields & existing MB IDs (for fallback if locked)
            ctx_locked_fields = video_item.locked_fields or []
            ctx_mb_artist_id = video_item.mb_artist_id
            ctx_mb_recording_id = video_item.mb_recording_id
            ctx_mb_release_id = video_item.mb_release_id
            ctx_mb_release_group_id = video_item.mb_release_group_id

            # Source context
            _source = video_item.sources[0] if video_item.sources else None
            ctx_source_url = (_source.canonical_url if _source else "") or ""
            ctx_source_original_url = (_source.original_url if _source else "") or ""
            ctx_channel_name = (_source.channel_name or "") if _source else ""
            ctx_platform_title = (_source.platform_title or "") if _source else ""
            ctx_platform_description = ((_source.platform_description or "")[:3000]) if _source else ""
            ctx_platform_tags = (_source.platform_tags or []) if _source else []
            ctx_upload_date = (_source.upload_date or "") if _source else ""
            ctx_needs_backfill = bool(_source and not _source.channel_name and
                                      (_source.canonical_url or _source.original_url))
            ctx_backfill_url = ((_source.canonical_url or _source.original_url)
                                if ctx_needs_backfill else None)

            # Existing YouTube source URL (for verify-first matching)
            ctx_existing_yt_url = ""
            for _s in video_item.sources:
                if _s.provider.value == "youtube" and _s.source_type == "video":
                    ctx_existing_yt_url = _s.canonical_url or _s.original_url or ""
                    break

            # Re-parse artist/title from raw platform title so rescan matches
            # the scraper-test / fresh-import behaviour instead of feeding
            # already-enriched DB values back into the pipeline.
            if ctx_platform_title:
                _rp_artist, _rp_title = extract_artist_title(ctx_platform_title)
                if _rp_artist:
                    ctx_artist = _rp_artist
                if _rp_title:
                    ctx_title = _rp_title
                # Strip duplicated artist prefix from title
                if ctx_artist and ctx_title:
                    for _sep in [" - ", " \u2014 ", " \u2013 ", " : "]:
                        _prefix = ctx_artist + _sep
                        if ctx_title.lower().startswith(_prefix.lower()):
                            ctx_title = ctx_title[len(_prefix):].strip()
                            break
                if not ctx_artist:
                    ctx_artist = ctx_channel_name or video_item.artist or ""
        finally:
            rd.close()

        _append_job_log(job_id, f"Rescanning: {ctx_artist} - {ctx_title}")
        _set_pipeline_step(job_id, "Rescanning metadata", "success")
        _check_cancelled(job_id)

        # ==================================================================
        # FROM DISK — read .playarr.xml and apply all data directly
        # ==================================================================
        if from_disk:
            _rescan_from_disk(job_id, video_id, ctx_folder_path, ctx_file_path, normalize)
            return

        # --- A2: Validate entity artwork (short independent DB session) ---
        try:
            aw_db = SessionLocal()
            try:
                aw_vi = aw_db.query(VideoItem).get(video_id)
                if aw_vi:
                    _validate_video_entity_artwork(aw_db, aw_vi, job_id)
                    aw_db.commit()
            finally:
                aw_db.close()
        except Exception as _ve:
            _append_job_log(job_id, f"Entity artwork validation warning: {_ve}")

        # --- A3: Backfill platform metadata (network I/O) ---
        backfill_data = {}
        if ctx_needs_backfill:
            try:
                _append_job_log(job_id, "Backfilling platform metadata from yt-dlp...")
                _, info = get_available_formats(ctx_backfill_url)
                meta = extract_metadata_from_ytdlp(info)
                backfill_data = {
                    "channel_name": meta.get("channel") or meta.get("uploader"),
                    "platform_title": meta.get("title"),
                    "platform_description": meta.get("description"),
                    "platform_tags": meta.get("tags"),
                    "upload_date": meta.get("upload_date"),
                }
                # Use backfilled values for the upcoming metadata resolution
                ctx_channel_name = backfill_data.get("channel_name") or ctx_channel_name
                ctx_platform_title = backfill_data.get("platform_title") or ctx_platform_title
                ctx_platform_description = (backfill_data.get("platform_description") or "")[:3000] or ctx_platform_description
                ctx_platform_tags = backfill_data.get("platform_tags") or ctx_platform_tags
                ctx_upload_date = backfill_data.get("upload_date") or ctx_upload_date
                _append_job_log(job_id, f"Platform metadata: channel={ctx_channel_name}")
            except Exception as e:
                _append_job_log(job_id, f"Platform metadata backfill failed: {e}")
                logger.warning(f"Platform metadata backfill failed for video {video_id}: {e}")

        _check_cancelled(job_id)

        # --- A4: Resolve metadata (network I/O + reads only) ---
        _skip_wiki = not scrape_wikipedia
        _skip_mb = not scrape_musicbrainz
        _skip_ai = not (ai_auto or ai_only)
        if ai_only:
            _skip_wiki = True
            _skip_mb = True
            _skip_ai = False

        # resolve_metadata_unified only reads settings from its db param
        settings_db = SessionLocal()
        try:
            _update_job(job_id, current_step="Resolving metadata", progress_percent=20)
            metadata = resolve_metadata_unified(
                artist=ctx_artist,
                title=ctx_title,
                db=settings_db,
                source_url=ctx_source_url,
                platform_title=ctx_platform_title,
                channel_name=ctx_channel_name,
                platform_description=ctx_platform_description,
                platform_tags=ctx_platform_tags,
                upload_date=ctx_upload_date,
                filename=os.path.basename(ctx_file_path) if ctx_file_path else "",
                folder_name=os.path.basename(ctx_folder_path) if ctx_folder_path else "",
                duration_seconds=ctx_duration,
                skip_wikipedia=_skip_wiki,
                skip_musicbrainz=_skip_mb,
                skip_ai=_skip_ai,
                log_callback=lambda msg: _append_job_log(job_id, msg),
            )
        finally:
            settings_db.close()

        _set_pipeline_step(job_id, "Resolving metadata", "success")

        # --- A5: Pre-compute field values that will be written ---
        locked = ctx_locked_fields
        all_locked = "_all" in locked

        new_artist = ctx_artist
        new_title = ctx_title
        new_album = ctx_album
        new_year = ctx_year
        new_plot = ctx_plot

        if not all_locked and "artist" not in locked and metadata.get("artist"):
            new_artist = metadata["artist"]
        if not all_locked and "title" not in locked and metadata.get("title"):
            new_title = metadata["title"]
        if not all_locked and "album" not in locked and metadata.get("album"):
            from app.services.source_validation import sanitize_album as _sanitize_album
            new_album = _sanitize_album(metadata["album"], title=metadata.get("title") or "") or None
        if not all_locked and "year" not in locked and metadata.get("year"):
            new_year = metadata["year"]
        if not all_locked and "plot" not in locked and metadata.get("plot"):
            plot = metadata["plot"]
            ai = generate_ai_summary(plot)
            new_plot = ai if ai else plot

        # MB IDs: use scraped values with existing as fallback
        new_mb_artist_id = metadata.get("mb_artist_id") or ctx_mb_artist_id
        new_mb_recording_id = metadata.get("mb_recording_id") or ctx_mb_recording_id
        new_mb_release_id = metadata.get("mb_release_id") or ctx_mb_release_id
        new_mb_release_group_id = metadata.get("mb_release_group_id") or ctx_mb_release_group_id

        new_genres = None
        if not all_locked and "genres" not in locked and metadata.get("genres"):
            new_genres = list(metadata["genres"])

        # Determine version type
        new_version_type = None
        if hint_cover:
            new_version_type = "cover"
        elif hint_live:
            new_version_type = "live"
        elif hint_uncensored:
            new_version_type = "uncensored"
        elif hint_alternate:
            new_version_type = "alternate"

        # --- A6: Write NFO file (pure file I/O) ---
        _update_job(job_id, current_step="Writing NFO", progress_percent=40)
        nfo_genres = new_genres if new_genres is not None else ctx_genre_names
        if ctx_folder_path:
            try:
                write_nfo_file(
                    ctx_folder_path,
                    artist=new_artist,
                    title=new_title,
                    album=new_album or "",
                    year=new_year,
                    genres=nfo_genres,
                    plot=new_plot or "",
                    source_url=ctx_source_url,
                    resolution_label=ctx_resolution_label or "",
                )
                _set_pipeline_step(job_id, "Writing NFO", "success")
            except Exception as e:
                _append_job_log(job_id, f"NFO rewrite error: {e}")
                _set_pipeline_step(job_id, "Writing NFO", "failed")

        # --- A7: Download poster image (network I/O + file I/O) ---
        _update_job(job_id, current_step="Fetching artwork", progress_percent=50)
        poster_downloaded = False
        poster_path = None
        poster_vr = None
        if metadata.get("image_url") and ctx_folder_path:
            folder_name = os.path.basename(ctx_folder_path)
            poster_path = os.path.join(ctx_folder_path, f"{folder_name}-poster.jpg")
            if download_image(metadata["image_url"], poster_path):
                from app.services.artwork_service import validate_file as _vf
                poster_vr = _vf(poster_path) if os.path.isfile(poster_path) else None
                poster_downloaded = True

        _check_cancelled(job_id)

        if poster_downloaded:
            _set_pipeline_step(job_id, "Fetching artwork", "success")

        # --- A8: Collect source links from metadata (network I/O) ---
        import re as _re
        source_links = {}
        try:
            # Wikipedia link â€” classify by page type
            wiki_url = metadata.get("source_url")
            if wiki_url and "wikipedia.org" in wiki_url:
                page_id = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", wiki_url)
                scraper_sources = metadata.get("scraper_sources_used", [])
                prov = "ai" if any("wikipedia:ai" in s for s in scraper_sources) else "scraped"
                wiki_page_type = metadata.get("wiki_page_type", "single")
                if wiki_page_type not in ("unrelated", "disambiguation"):
                    if wiki_page_type == "album":
                        wiki_key, wiki_st = "wikipedia_album", "album"
                    elif wiki_page_type == "artist":
                        wiki_key, wiki_st = "wikipedia_artist", "artist"
                    else:
                        wiki_key, wiki_st = "wikipedia_single", "single"
                    source_links[wiki_key] = {
                        "provider": "wikipedia", "id": page_id, "url": wiki_url,
                        "source_type": wiki_st, "provenance": prov,
                    }

            # IMDB
            if not metadata.get("imdb_url") and not (_skip_wiki and _skip_mb):
                try:
                    imdb_url = search_imdb_music_video(new_artist, new_title)
                    if imdb_url:
                        metadata["imdb_url"] = imdb_url
                except Exception:
                    pass
            if metadata.get("imdb_url"):
                m = _re.search(r"(tt\d+|nm\d+)", metadata["imdb_url"])
                source_links["imdb"] = {
                    "provider": "imdb",
                    "id": m.group(1) if m else metadata["imdb_url"],
                    "url": metadata["imdb_url"],
                    "source_type": "video", "provenance": "scraped",
                }

            # MusicBrainz single (release-group)
            mb_rg = metadata.get("mb_release_group_id")
            if mb_rg:
                source_links["musicbrainz_single"] = {
                    "provider": "musicbrainz", "id": mb_rg,
                    "url": f"https://musicbrainz.org/release-group/{mb_rg}",
                    "source_type": "single", "provenance": "scraped",
                }
            # MusicBrainz artist
            if new_mb_artist_id:
                source_links["musicbrainz_artist"] = {
                    "provider": "musicbrainz", "id": new_mb_artist_id,
                    "url": f"https://musicbrainz.org/artist/{new_mb_artist_id}",
                    "source_type": "artist", "provenance": "scraped",
                }
            # MusicBrainz album release-group
            mb_album_rg = metadata.get("mb_album_release_group_id")
            if mb_album_rg:
                source_links["musicbrainz_album"] = {
                    "provider": "musicbrainz", "id": mb_album_rg,
                    "url": f"https://musicbrainz.org/release-group/{mb_album_rg}",
                    "source_type": "album", "provenance": "scraped",
                }

            # Wikipedia artist/album search (only if Wikipedia scraping is enabled)
            if not _skip_wiki:
                try:
                    wa_url = search_wikipedia_artist(
                        metadata.get("primary_artist") or new_artist
                    )
                    if wa_url:
                        page_id = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", wa_url)
                        source_links["wikipedia_artist"] = {
                            "provider": "wikipedia", "id": page_id, "url": wa_url,
                            "source_type": "artist", "provenance": "scraped",
                        }
                except Exception:
                    pass

                if new_album:
                    try:
                        wl_url = search_wikipedia_album(new_artist, new_album)
                        if wl_url:
                            page_id = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", wl_url)
                            source_links["wikipedia_album"] = {
                                "provider": "wikipedia", "id": page_id, "url": wl_url,
                                "source_type": "album", "provenance": "scraped",
                            }
                    except Exception:
                        pass

            if source_links:
                _append_job_log(job_id, f"Collected {len(source_links)} source link(s)")
        except Exception as _sl_err:
            _append_job_log(job_id, f"Source link collection warning: {_sl_err}")

        # --- A8b: YouTube source matching (network I/O) ---
        if find_source_video:
            _check_cancelled(job_id)
            _set_pipeline_step(job_id, "YouTube matching", "running")
            try:
                from app.services.youtube_matcher import find_best_youtube_match, verify_youtube_link
                _yt_match = None

                # If an existing YouTube link is present, verify it first
                if ctx_existing_yt_url:
                    _append_job_log(job_id, f"Verifying existing YouTube link: {ctx_existing_yt_url}")
                    _yt_match = verify_youtube_link(
                        ctx_existing_yt_url, new_artist, new_title,
                        duration_seconds=int(ctx_duration) if ctx_duration else None,
                    )
                    if _yt_match:
                        _append_job_log(job_id, f"Existing YouTube link verified (score={_yt_match.overall_score:.3f})")
                    else:
                        _append_job_log(job_id, "Existing YouTube link failed verification, searching...")

                # Fall back to general search if no verified link
                if not _yt_match:
                    _yt_match = find_best_youtube_match(
                        new_artist, new_title,
                        duration_seconds=int(ctx_duration) if ctx_duration else None,
                    )

                if _yt_match:
                    source_links["youtube"] = {
                        "provider": "youtube",
                        "id": _yt_match.video_id,
                        "url": _yt_match.url,
                        "source_type": "video",
                        "provenance": "rescan",
                    }
                    _append_job_log(job_id, f"YouTube match: {_yt_match.url} (score={_yt_match.overall_score:.3f})")
                    _set_pipeline_step(job_id, "YouTube matching", "success")
                else:
                    _append_job_log(job_id, "No YouTube match found above threshold")
                    _set_pipeline_step(job_id, "YouTube matching", "skipped")
            except Exception as _yt_err:
                _append_job_log(job_id, f"YouTube matching warning: {_yt_err}")
                _set_pipeline_step(job_id, "YouTube matching", "warning")

        _check_cancelled(job_id)

        _update_job(job_id, current_step="Resolving entities", progress_percent=65)
        _set_pipeline_step(job_id, "Collecting sources", "success")

        # --- A9: Resolve entities (network I/O, no DB writes) ---
        resolved_artist = {}
        resolved_album = {}
        resolved_track = {}
        canonical_params = {}
        try:
            resolved_artist = resolve_artist(
                new_artist,
                mb_artist_id=new_mb_artist_id,
                skip_musicbrainz=_skip_mb,
                skip_wikipedia=_skip_wiki,
            )
            _append_job_log(job_id, f"Resolved artist: {resolved_artist.get('canonical_name', new_artist)}")
        except Exception as e:
            _append_job_log(job_id, f"Artist resolution warning: {e}")

        if new_album:
            try:
                from app.services.source_validation import sanitize_album as _sanitize_album_ent
                _clean_album = _sanitize_album_ent(new_album, title=new_title) or new_album
            except Exception:
                _clean_album = new_album
            try:
                resolved_album = resolve_album(
                    new_artist, _clean_album,
                    mb_release_id=metadata.get("mb_album_release_id"),
                    skip_musicbrainz=_skip_mb,
                    skip_wikipedia=_skip_wiki,
                )
                # Propagate parent-album release-group ID
                if resolved_album and metadata.get("mb_album_release_group_id"):
                    resolved_album.setdefault(
                        "mb_release_group_id",
                        metadata["mb_album_release_group_id"],
                    )
            except Exception as e:
                _append_job_log(job_id, f"Album resolution warning: {e}")

        try:
            resolved_track = resolve_track(
                new_artist, new_title,
                mb_recording_id=new_mb_recording_id,
                skip_musicbrainz=_skip_mb,
                skip_wikipedia=_skip_wiki,
            )
        except Exception as e:
            _append_job_log(job_id, f"Track resolution warning: {e}")

        canonical_params = {
            "title": new_title,
            "year": new_year,
            "mb_recording_id": new_mb_recording_id,
            "mb_release_id": new_mb_release_id,
            "mb_release_group_id": new_mb_release_group_id,
            "mb_artist_id": new_mb_artist_id,
            "version_type": new_version_type or "normal",
            "original_artist": None,
            "original_title": None,
            "genres": new_genres if new_genres is not None else ctx_genre_names,
            "resolved_track": resolved_track or None,
        }

        # ==================================================================
        # PHASE B — Single serialized write (via write queue)
        # ==================================================================
        # All DB mutations are submitted to the centralised write queue's
        # single writer thread.  This makes SQLite lock contention
        # impossible by construction — no _pipeline_lock needed.
        _check_cancelled(job_id)

        def _execute_rescan_write():
            """Run all Phase B DB mutations in the write queue's single writer thread."""
            db = SessionLocal()
            try:
                video_item = db.query(VideoItem).get(video_id)
                if not video_item:
                    _update_job(job_id, status=JobStatus.failed, error_message="Video not found (write phase)")
                    return

                # Pre-rescan snapshot
                _save_metadata_snapshot(db, video_item, "rescan")

                # Apply backfilled source metadata
                if backfill_data and video_item.sources:
                    src = video_item.sources[0]
                    if backfill_data.get("channel_name"):
                        src.channel_name = backfill_data["channel_name"]
                    if backfill_data.get("platform_title"):
                        src.platform_title = backfill_data["platform_title"]
                    if backfill_data.get("platform_description"):
                        src.platform_description = backfill_data["platform_description"]
                    if backfill_data.get("platform_tags"):
                        src.platform_tags = backfill_data["platform_tags"]
                    if backfill_data.get("upload_date"):
                        src.upload_date = backfill_data["upload_date"]

                # Apply metadata fields — normalize feat credits to semicolons
                from app.services.source_validation import normalize_feat_to_semicolons, build_artist_ids
                video_item.artist = normalize_feat_to_semicolons(new_artist)
                video_item.title = new_title
                video_item.album = new_album
                video_item.year = new_year
                video_item.plot = new_plot

                # Artist IDs (structured multi-artist list)
                _scrape_artist_ids = metadata.get("artist_ids") or build_artist_ids(
                    video_item.artist,
                    mb_artist_credits=metadata.get("mb_artist_credits"),
                    primary_mb_artist_id=new_mb_artist_id,
                )
                video_item.artist_ids = _scrape_artist_ids

                # MusicBrainz IDs
                video_item.mb_artist_id = new_mb_artist_id
                video_item.mb_recording_id = new_mb_recording_id
                video_item.mb_release_id = new_mb_release_id
                video_item.mb_release_group_id = new_mb_release_group_id

                # Genres
                if new_genres is not None:
                    video_item.genres.clear()
                    for g in new_genres:
                        video_item.genres.append(_get_or_create_genre(db, g))

                # Poster MediaAsset upsert
                if poster_downloaded and poster_path:
                    _pvr = poster_vr
                    existing_poster = db.query(MediaAsset).filter(
                        MediaAsset.video_id == video_id,
                        MediaAsset.asset_type == "poster"
                    ).first()
                    if existing_poster:
                        existing_poster.file_path = poster_path
                        existing_poster.source_url = metadata["image_url"]
                        existing_poster.provenance = "rescan"
                        existing_poster.status = "valid" if (_pvr and _pvr.valid) else "invalid"
                        existing_poster.width = _pvr.width if _pvr and _pvr.valid else None
                        existing_poster.height = _pvr.height if _pvr and _pvr.valid else None
                        existing_poster.file_size_bytes = _pvr.file_size_bytes if _pvr and _pvr.valid else None
                        existing_poster.file_hash = _pvr.file_hash if _pvr and _pvr.valid else None
                        existing_poster.last_validated_at = datetime.now(timezone.utc)
                    else:
                        db.add(MediaAsset(
                            video_id=video_id, asset_type="poster",
                            file_path=poster_path, source_url=metadata["image_url"],
                            provenance="rescan",
                            status="valid" if (_pvr and _pvr.valid) else "invalid",
                            width=_pvr.width if _pvr and _pvr.valid else None,
                            height=_pvr.height if _pvr and _pvr.valid else None,
                            file_size_bytes=_pvr.file_size_bytes if _pvr and _pvr.valid else None,
                            file_hash=_pvr.file_hash if _pvr and _pvr.valid else None,
                            last_validated_at=datetime.now(timezone.utc),
                        ))

                # Sources — clear non-video reference links (Wikipedia, MB,
                # IMDB) so stale links from a previous scrape don't persist.
                # The primary video source (YouTube etc.) is preserved.
                # Skip clearing when all fields are locked.
                # Only clear if the scrape actually found replacement sources;
                # otherwise we'd destroy existing data with nothing to replace it.
                from app.pipeline_url.db_apply import _upsert_source
                if not all_locked and source_links:
                    _cleared = db.query(Source).filter(
                        Source.video_id == video_id,
                        Source.source_type != "video",
                    ).delete(synchronize_session="fetch")
                    if _cleared:
                        _append_job_log(job_id, f"Cleared {_cleared} stale reference source(s)")

                for _src_key, _src_data in source_links.items():
                    _upsert_source(db, video_id, {
                        "provider": _src_data["provider"],
                        "source_video_id": _src_data["id"],
                        "original_url": _src_data.get("url", ""),
                        "canonical_url": _src_data.get("url", ""),
                        "source_type": _src_data.get("source_type", "video"),
                        "provenance": _src_data.get("provenance", "rescan"),
                    })
                if source_links:
                    _append_job_log(job_id, f"Upserted {len(source_links)} source(s)")

                # Clear entity links so rescan re-resolves from scratch.
                # Skip when all fields are locked — preserve existing links.
                if not all_locked:
                    video_item.artist_entity_id = None
                    video_item.album_entity_id = None
                    video_item.track_id = None

                # Flush core changes before entity resolution so savepoints
                # only contain entity-related writes.
                db.flush()

                # Entity resolution (DB writes with savepoints)
                artist_entity = None
                album_entity = None
                track_entity = None
                canonical_track = None

                if new_artist:
                    try:
                        with db.begin_nested():
                            artist_entity = get_or_create_artist(
                                db, new_artist, resolved=resolved_artist,
                            )
                            save_revision(db, "artist", artist_entity.id, "auto_import", "rescan")
                    except Exception as _e:
                        _append_job_log(job_id, f"Artist entity warning: {_e}")

                if new_album and artist_entity:
                    try:
                        with db.begin_nested():
                            album_entity = get_or_create_album(
                                db, artist_entity, new_album,
                                resolved=resolved_album,
                            )
                            save_revision(db, "album", album_entity.id, "auto_import", "rescan")
                    except Exception as _e:
                        _append_job_log(job_id, f"Album entity warning: {_e}")

                if new_title and artist_entity:
                    try:
                        with db.begin_nested():
                            track_entity = get_or_create_track(
                                db, artist_entity, album_entity, new_title,
                                resolved=resolved_track,
                            )
                    except Exception as _e:
                        _append_job_log(job_id, f"Track entity warning: {_e}")

                if artist_entity:
                    try:
                        with db.begin_nested():
                            ct_params = dict(canonical_params)
                            ct_params["artist_entity"] = artist_entity
                            ct_params["album_entity"] = album_entity
                            canonical_track, _ct_created = get_or_create_canonical_track(db, **ct_params)
                    except Exception as _e:
                        _append_job_log(job_id, f"Canonical track warning: {_e}")

                # Inherit album from canonical track if missing
                if not album_entity and canonical_track and canonical_track.album_id:
                    from app.metadata.models import AlbumEntity
                    _ct_album = db.query(AlbumEntity).get(canonical_track.album_id)
                    if _ct_album:
                        album_entity = _ct_album
                        if not video_item.album:
                            video_item.album = _ct_album.title

                # Link video to entities
                if artist_entity:
                    video_item.artist_entity_id = artist_entity.id
                if album_entity:
                    video_item.album_entity_id = album_entity.id
                if track_entity:
                    video_item.track_id = track_entity.id
                if canonical_track:
                    link_video_to_canonical_track(db, video_item, canonical_track)

                # Entity resolution processing flag
                if track_entity or canonical_track:
                    _set_processing_flag(db, video_item, "track_identified", method="rescan")
                    _set_processing_flag(db, video_item, "canonical_linked", method="rescan")

                # Post-rescan snapshot
                _save_metadata_snapshot(db, video_item, "rescan_complete")

                # Version type hints
                if new_version_type:
                    video_item.version_type = new_version_type
                    _append_job_log(job_id, f"Version type set to: {new_version_type}")

                # Promote scanned items to full library imports
                if video_item.import_method == "scanned":
                    video_item.import_method = "import"
                    _append_job_log(job_id, "Promoted import_method from 'scanned' to 'import'")

                # Processing state flags for progress display
                _set_processing_flag(db, video_item, "metadata_scraped", method="rescan")
                _set_processing_flag(db, video_item, "metadata_resolved", method="rescan")
                if poster_downloaded:
                    _set_processing_flag(db, video_item, "artwork_fetched", method="rescan")
                if new_plot:
                    _set_processing_flag(db, video_item, "description_generated", method="rescan")
                if ctx_folder_path:
                    _set_processing_flag(db, video_item, "nfo_exported", method="rescan")
                    # Write Playarr XML sidecar — merge existing XML quality
                    # data first so that values the DB doesn't have (e.g.
                    # loudness_lufs from a previous normalization) are not
                    # lost when the XML is rewritten.
                    try:
                        from app.services.playarr_xml import (
                            write_playarr_xml, find_playarr_xml, parse_playarr_xml,
                        )
                        _merge_existing_xml_quality(db, video_item, ctx_folder_path)
                        db.flush()  # persist entity links before XML generation
                        db.refresh(video_item)
                        write_playarr_xml(video_item, db)
                        _set_processing_flag(db, video_item, "xml_exported", method="rescan")
                    except Exception as _xml_e:
                        _append_job_log(job_id, f"Playarr XML write error: {_xml_e}")

                # Compute Playarr content IDs
                try:
                    from app.services.content_id import compute_ids_for_video
                    ids = compute_ids_for_video(video_item)
                    video_item.playarr_track_id = ids["playarr_track_id"]
                    video_item.playarr_video_id = ids["playarr_video_id"]
                except Exception as _cid_e:
                    _append_job_log(job_id, f"Content ID generation warning: {_cid_e}")

                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        # Job status update (uses own session via _update_job)
        _set_pipeline_step(job_id, "Resolving entities", "success")
        _set_pipeline_step(job_id, "Applying to database", "success")
        _update_job(job_id, status=JobStatus.complete, progress_percent=90,
                    current_step="Finalizing", completed_at=datetime.now(timezone.utc))
        _append_job_log(job_id, "Rescan complete â€” dispatching deferred tasks")

        # â”€â”€ Stage D: Deferred tasks (AI enrichment, artwork, scenes, etc.) â”€â”€
        # Mirrors the import pipeline's advanced-mode deferred task list.
        # dispatch_deferred runs in a daemon thread and sets current_step
        # to "Import complete" when finished.
        try:
            from app.pipeline_url.workspace import ImportWorkspace
            from app.pipeline_url.deferred import dispatch_deferred

            deferred_tasks = ["preview", "matching", "kodi_export",
                              "entity_artwork", "orphan_cleanup",
                              "scene_analysis"]
            if ai_auto:
                deferred_tasks.append("ai_enrichment")

            ws = ImportWorkspace(job_id)
            ws.log("Rescan deferred tasks starting")
            dispatch_deferred(video_id, deferred_tasks, ws)
        except Exception as de:
            logger.error(f"Rescan deferred dispatch failed: {de}")
            _append_job_log(job_id, f"Deferred dispatch failed: {de}")
            _update_job(job_id, current_step="Import complete", progress_percent=100)

        # Queue normalize as a follow-up if requested
        if normalize and ctx_file_path:
            try:
                norm_db = SessionLocal()
                try:
                    _nv = norm_db.query(VideoItem).get(video_id)
                    _norm_display = f"{_nv.artist} \u2013 {_nv.title} \u203a Normalize" if _nv and _nv.artist and _nv.title else None
                    norm_job = ProcessingJob(
                        job_type="normalize", status=JobStatus.queued,
                        video_id=video_id,
                        action_label="Normalize",
                        display_name=_norm_display,
                    )
                    norm_db.add(norm_job)
                    norm_db.commit()
                    dispatch_task(normalize_task, job_id=norm_job.id, video_id=video_id)
                    _append_job_log(job_id, "Normalize queued as follow-up")
                finally:
                    norm_db.close()
            except Exception as ne:
                _append_job_log(job_id, f"Failed to queue normalize: {ne}")

    except JobCancelledError:
        clear_cancel(job_id)
        _update_job(job_id, status=JobStatus.cancelled,
                    error_message="Cancelled by user",
                    completed_at=datetime.now(timezone.utc))
        _append_job_log(job_id, "Job cancelled by user")

    except Exception as e:
        logger.error(f"Rescan failed: {e}")
        _update_job(job_id, status=JobStatus.failed, error_message=str(e))
        _append_job_log(job_id, f"ERROR: {e}")


# ---------------------------------------------------------------------------
# Batch job completion â€” poll sub-jobs until all finish
# ---------------------------------------------------------------------------

@celery_app.task(bind=True)
def complete_batch_job_task(self, parent_job_id: int, sub_job_ids: list):
    """Wait for all sub-jobs to complete, then mark the parent batch job done.

    Key design: READ and WRITE phases use SEPARATE sqlite3 connections so
    that reads (which never block in WAL mode) cannot be stalled by write
    contention from child pipeline threads.  Write failures are silently
    tolerated â€” progress will be retried on the next iteration.
    """
    import json as _json
    import time
    import sqlite3 as _sqlite3

    STUCK_THRESHOLD = 300  # 5 minutes with no status change â†’ assume stuck
    FORCE_FAIL_THRESHOLD = 600  # 10 minutes stuck â†’ force-fail the child

    from app.config import get_settings as _gs
    _db_url = _gs().database_url
    _db_path = _db_url.replace("sqlite:///", "").replace("sqlite://", "")

    max_idle = 1800  # 30 min with no sub-job completing → timeout
    poll_interval = 5
    total = len(sub_job_ids)
    last_seen_status: dict[int, tuple[str, float]] = {}
    logged_complete: set[int] = set()
    last_progress_time = time.monotonic()  # reset whenever a sub-job finishes
    pipeline_steps: list[dict] = [
        {"step": f"Watching {total} child jobs", "status": "success"},
    ]

    # --- File-based log (never blocks, source of truth) ---
    _log_dir = os.path.join(_gs().log_dir, "jobs")
    os.makedirs(_log_dir, exist_ok=True)
    _log_file = os.path.join(_log_dir, f"{parent_job_id}.log")

    def _flog(msg: str):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        try:
            with open(_log_file, "a", encoding="utf-8") as fh:
                fh.write(f"[{ts}] {msg}\n")
        except Exception:
            pass

    def _read_log_text() -> str:
        try:
            with open(_log_file, "r", encoding="utf-8") as fh:
                return fh.read()
        except Exception:
            return ""

    def _db_write(pct: int, step_msg: str):
        """Best-effort write of parent progress to DB via write queue.

        Defence-in-depth: each write checks if the parent is already in a
        terminal state.  If so, the write is silently skipped to prevent
        fire-and-forget progress writes from clobbering the final status.
        """
        from app.pipeline_url.write_queue import db_write_soon

        _pct = pct
        _step = step_msg
        _steps_snapshot = list(pipeline_steps)

        _TERMINAL = ("complete", "failed", "cancelled", "skipped")

        def _write():
            wc = None
            try:
                log_text = _read_log_text()
                wc = _sqlite3.connect(_db_path, timeout=10)
                wc.execute("PRAGMA journal_mode=WAL")
                wc.execute("PRAGMA busy_timeout=10000")
                # Guard: never overwrite a terminal parent
                cur = wc.execute(
                    "SELECT status FROM processing_jobs WHERE id=?",
                    (parent_job_id,),
                ).fetchone()
                if cur and cur[0] in _TERMINAL:
                    return
                wc.execute(
                    "UPDATE processing_jobs SET progress_percent=?, current_step=?, "
                    "log_text=?, pipeline_steps=?, updated_at=? WHERE id=?",
                    (_pct, _step, log_text, _json.dumps(_steps_snapshot),
                     datetime.now(timezone.utc).isoformat(), parent_job_id),
                )
                wc.commit()
            except Exception as exc:
                logger.warning(f"[Batch {parent_job_id}] write failed: {exc}")
            finally:
                if wc:
                    try:
                        wc.close()
                    except Exception:
                        pass

        db_write_soon(_write)

    # â”€â”€ Pre-flight: if parent is already terminal, skip entirely â”€â”€
    # With --pool=solo, batch_import_task finishes before this task starts.
    # Writing "0/N complete" AFTER the parent is already done would clobber
    # the final step.  Check BEFORE any writes to avoid the race.
    try:
        _pre_rc = _sqlite3.connect(_db_path, timeout=5)
        _pre_rc.execute("PRAGMA journal_mode=WAL")
        _pre_status = _pre_rc.execute(
            "SELECT status FROM processing_jobs WHERE id=?",
            (parent_job_id,),
        ).fetchone()
        _pre_rc.close()
        if _pre_status and _pre_status[0] in ("complete", "failed", "cancelled", "skipped"):
            logger.info(
                f"[Batch {parent_job_id}] Parent already {_pre_status[0]} "
                f"before watcher started â€” skipping"
            )
            _flog(f"Parent already {_pre_status[0]} â€” watcher exiting")
            return
    except Exception:
        pass  # If pre-flight read fails, proceed normally

    _flog(f"Watching {total} child jobs")
    _db_write(0, f"0/{total} complete \u00b7 {total} queued")

    while True:
        idle_elapsed = time.monotonic() - last_progress_time
        if idle_elapsed >= max_idle:
            break
        # â”€â”€ PHASE 1: READ child statuses (separate connection, WAL = never blocks) â”€â”€
        child_data: dict[int, tuple[str, str]] = {}
        parent_cancelled = False
        rc = None
        try:
            rc = _sqlite3.connect(_db_path, timeout=5)
            rc.execute("PRAGMA journal_mode=WAL")
            ps = rc.execute(
                "SELECT status FROM processing_jobs WHERE id=?",
                (parent_job_id,),
            ).fetchone()
            if ps and ps[0] == "cancelled":
                parent_cancelled = True
            elif ps and ps[0] in ("complete", "failed"):
                # Parent already finalized (e.g., by batch_import_task)
                logger.info(f"[Batch {parent_job_id}] Parent already {ps[0]}, nothing to do")
                if rc:
                    rc.close()
                return
            for sid in sub_job_ids:
                row = rc.execute(
                    "SELECT status, display_name FROM processing_jobs WHERE id=?",
                    (sid,),
                ).fetchone()
                if row:
                    child_data[sid] = (row[0], row[1] or f"Job {sid}")
        except Exception as exc:
            logger.warning(f"[Batch {parent_job_id}] read failed: {exc}")
            time.sleep(poll_interval)
            elapsed += poll_interval
            continue
        finally:
            if rc:
                try:
                    rc.close()
                except Exception:
                    pass

        # â”€â”€ Handle parent cancellation â”€â”€
        if parent_cancelled:
            from app.pipeline_url.write_queue import db_write
            def _cancel_children():
                db = CosmeticSessionLocal()
                try:
                    for sid in sub_job_ids:
                        sub = db.query(ProcessingJob).get(sid)
                        if sub:
                            if sub.status == JobStatus.queued:
                                sub.status = JobStatus.cancelled
                                sub.error_message = "Parent job cancelled"
                            elif sub.status not in (
                                JobStatus.complete, JobStatus.failed, JobStatus.cancelled,
                                JobStatus.skipped,
                            ):
                                request_cancel(sid)
                    db.commit()
                finally:
                    db.close()
            db_write(_cancel_children)
            _flog("Playlist cancelled by user")
            return

        # â”€â”€ PHASE 2: Process results in memory â”€â”€
        now_mono = time.monotonic()
        prev_done_count = len(logged_complete)
        done = 0
        failed = 0
        cancelled = 0
        in_progress = 0
        queued = 0

        for sid in sub_job_ids:
            if sid not in child_data:
                continue
            st, name = child_data[sid]
            if st == "complete":
                done += 1
                if sid not in logged_complete:
                    logged_complete.add(sid)
                    _flog(f"âœ“ {name}")
                    pipeline_steps.append({
                        "step": f"Video #{len(logged_complete)}: {name}",
                        "status": "success",
                    })
            elif st == "cancelled":
                done += 1
                cancelled += 1
                if sid not in logged_complete:
                    logged_complete.add(sid)
                    _flog(f"âœ— {name} (cancelled)")
            elif st == "failed":
                done += 1
                failed += 1
                if sid not in logged_complete:
                    logged_complete.add(sid)
                    _flog(f"âœ— {name} (failed)")
                    pipeline_steps.append({"step": name, "status": "failed"})
            elif st == "skipped":
                done += 1
                if sid not in logged_complete:
                    logged_complete.add(sid)
                    _flog(f"â€“ {name} (skipped)")
            elif st == "queued":
                queued += 1
            else:
                in_progress += 1
                prev = last_seen_status.get(sid)
                if prev is None or prev[0] != st:
                    last_seen_status[sid] = (st, now_mono)
                elif now_mono - prev[1] > FORCE_FAIL_THRESHOLD:
                    # Force-fail the stuck child via direct DB write
                    # AND register cancellation so the pipeline thread stops
                    logger.error(
                        f"[Batch {parent_job_id}] Child {sid} stuck "
                        f"in '{st}' >{FORCE_FAIL_THRESHOLD}s â€” force-failing"
                    )
                    request_cancel(sid)  # signal the running thread to abort
                    try:
                        from app.pipeline_url.write_queue import db_write
                        _sid = sid  # capture for closure
                        def _force_fail():
                            _force_db = CosmeticSessionLocal()
                            try:
                                _stuck_job = _force_db.query(ProcessingJob).get(_sid)
                                if _stuck_job and _stuck_job.status not in (
                                    JobStatus.complete, JobStatus.failed, JobStatus.cancelled,
                                    JobStatus.skipped,
                                ):
                                    _stuck_job.status = JobStatus.failed
                                    _stuck_job.error_message = (
                                        _stuck_job.error_message or "Pipeline hung â€” force-failed by batch monitor"
                                    )
                                    _stuck_job.completed_at = datetime.now(timezone.utc)
                                    _force_db.commit()
                                    _flog(f"Force-failed stuck child {_sid}")
                            finally:
                                _force_db.close()
                        db_write(_force_fail)
                    except Exception as _fe:
                        logger.warning(f"[Batch {parent_job_id}] force-fail {sid}: {_fe}")
                elif now_mono - prev[1] > STUCK_THRESHOLD:
                    logger.warning(
                        f"[Batch {parent_job_id}] Child {sid} stuck "
                        f"in '{st}' >{STUCK_THRESHOLD}s"
                    )

        # Build progress text
        if len(logged_complete) > prev_done_count:
            last_progress_time = time.monotonic()
        parts = [f"{done}/{total} complete"]
        if in_progress:
            parts.append(f"{in_progress} importing")
        if queued:
            parts.append(f"{queued} queued")
        if failed:
            parts.append(f"{failed} failed")
        if cancelled:
            parts.append(f"{cancelled} cancelled")
        step_msg = " \u00b7 ".join(parts)
        pct = int((done / max(total, 1)) * 100)

        # â”€â”€ PHASE 3: WRITE parent progress (best-effort, separate connection) â”€â”€
        _db_write(pct, step_msg)

        if done >= total:
            break

        time.sleep(poll_interval)

    # --- Final status ---
    # Use direct sqlite3 for the terminal write â€” the ORM-based _update_job
    # can silently fail under heavy SQLite contention from concurrent pipeline
    # threads, leaving the parent stuck in "analyzing" forever.
    succeeded = total - failed - cancelled
    _deferred_note = " \u00b7 Album art & previews may still be processing"
    _final_log = _read_log_text()

    if cancelled == total:
        _flog("All sub-jobs cancelled")
        _final_status = "cancelled"
        _final_error = "All sub-jobs cancelled"
        _final_step = f"All {total} sub-jobs cancelled"
        _final_pct = 100
    elif failed + cancelled == total:
        _flog(f"All {total} sub-jobs failed or were cancelled")
        _final_status = "failed"
        _final_error = f"All {total} sub-jobs failed or were cancelled"
        _final_step = _final_error
        _final_pct = 100
    elif failed + cancelled > 0:
        final_msg = f"Done ({succeeded} OK, {failed} failed, {cancelled} cancelled)"
        _flog(final_msg)
        pipeline_steps.append({"step": final_msg, "status": "success"})
        _final_status = "complete"
        _final_error = None
        _final_step = final_msg + _deferred_note
        _final_pct = 100
    elif (time.monotonic() - last_progress_time) >= max_idle:
        _idle_min = int(max_idle // 60)
        _flog(f"Timed out — no sub-job completed in {_idle_min} minutes")
        _final_status = "failed"
        _final_error = f"Timed out — no sub-job completed in {_idle_min} minutes"
        _final_step = _final_error
        _final_pct = int((done / max(total, 1)) * 100)
    else:
        final_msg = f"All {total} imports complete"
        _flog(final_msg)
        pipeline_steps.append({"step": final_msg, "status": "success"})
        _final_status = "complete"
        _final_error = None
        _final_step = final_msg + _deferred_note
        _final_pct = 100

    _final_log = _read_log_text()  # re-read after flog writes

    # Terminal write via write queue â€” must succeed
    from app.pipeline_url.write_queue import db_write
    def _write_final():
        _wc = _sqlite3.connect(_db_path, timeout=30)
        try:
            _wc.execute("PRAGMA journal_mode=WAL")
            _wc.execute("PRAGMA busy_timeout=30000")
            _wc.execute(
                "UPDATE processing_jobs SET status=?, progress_percent=?, "
                "current_step=?, error_message=?, log_text=?, pipeline_steps=?, "
                "completed_at=?, updated_at=? WHERE id=?",
                (_final_status, _final_pct, _final_step, _final_error,
                 _final_log, _json.dumps(pipeline_steps),
                 datetime.now(timezone.utc).isoformat(),
                 datetime.now(timezone.utc).isoformat(),
                 parent_job_id),
            )
            _wc.commit()
            logger.info(f"[Batch {parent_job_id}] Final status: {_final_status}")
        finally:
            _wc.close()
    try:
        db_write(_write_final)
    except Exception as e:
        logger.error(f"[Batch {parent_job_id}] final write failed: {e}")
        # Fallback: try direct write outside queue
        try:
            _write_final()
            logger.info(f"[Batch {parent_job_id}] Final status (fallback): {_final_status}")
        except Exception as e2:
            logger.error(f"[Batch {parent_job_id}] final fallback also failed: {e2}")


# ---------------------------------------------------------------------------
# Normalize task
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, max_retries=2)
def normalize_task(self, job_id: int, video_id: int, target_lufs: float = None):
    """Normalize audio for a single video item."""
    settings = get_settings()
    if target_lufs is None:
        target_lufs = settings.normalization_target_lufs

    _update_job(job_id, status=JobStatus.normalizing, started_at=datetime.now(timezone.utc),
                celery_task_id=getattr(getattr(self, 'request', None), 'id', None))

    db = SessionLocal()
    try:
        _check_cancelled(job_id)

        video_item = db.query(VideoItem).get(video_id)
        if not video_item or not video_item.file_path:
            _update_job(job_id, status=JobStatus.failed, error_message="Video file not found")
            return

        _append_job_log(job_id, f"Normalizing: {video_item.artist} - {video_item.title} to {target_lufs} LUFS")

        # Skip early if quality_signature already shows audio is at target
        if video_item.quality_signature and video_item.quality_signature.loudness_lufs is not None:
            current_lufs = video_item.quality_signature.loudness_lufs
            if abs(target_lufs - current_lufs) < 0.5:
                _append_job_log(job_id, f"Already at target ({current_lufs:.1f} LUFS) — skipping normalization")
                _set_processing_flag(db, video_item, "audio_normalized", method="normalize")
                _update_job(job_id, status=JobStatus.complete, progress_percent=100,
                            current_step="Already normalized",
                            completed_at=datetime.now(timezone.utc))
                db.commit()
                return

        _check_cancelled(job_id)

        before, after, gain = normalize_video(video_item.file_path, target_lufs)

        if before is not None:
            norm_hist = NormalizationHistory(
                video_id=video_id,
                target_lufs=target_lufs,
                measured_lufs_before=before,
                measured_lufs_after=after,
                gain_applied_db=gain,
            )
            db.add(norm_hist)

            if video_item.quality_signature:
                video_item.quality_signature.loudness_lufs = after

            _append_job_log(job_id, f"Done: {before:.1f} -> {after:.1f} LUFS")

        _set_processing_flag(db, video_item, "audio_normalized", method="normalize")

        _update_job(job_id, status=JobStatus.complete, progress_percent=100,
                    completed_at=datetime.now(timezone.utc))
        db.commit()

    except JobCancelledError:
        db.rollback()
        clear_cancel(job_id)
        _update_job(job_id, status=JobStatus.cancelled,
                    error_message="Cancelled by user",
                    completed_at=datetime.now(timezone.utc))
        _append_job_log(job_id, "Job cancelled by user")

    except Exception as e:
        db.rollback()
        logger.error(f"Normalize failed: {e}")
        _update_job(job_id, status=JobStatus.failed, error_message=str(e))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Consolidated metadata scrape task
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, max_retries=2)
def scrape_metadata_task(self, job_id: int, video_id: int,
                         ai_auto_analyse: bool = False,
                         ai_only: bool = False,
                         scrape_wikipedia: bool = False,
                         wikipedia_url: str = None,
                         scrape_musicbrainz: bool = False,
                         musicbrainz_url: str = None,
                         scrape_tmvdb: bool = False,
                         hint_cover: bool = False,
                         hint_live: bool = False,
                         hint_alternate: bool = False,
                         hint_uncensored: bool = False,
                         hint_alternate_label: str = "",
                         find_source_video: bool = False,
                         normalize_audio: bool = False):
    """Consolidated metadata scrape task supporting multiple modes.

    Modes (can be combined except AI Auto Analyse + AI Only):
    - ai_auto_analyse: Full import-style pipeline (AI + MusicBrainz + Wikipedia + IMDB)
    - ai_only: AI enrichment only (no external scrapers)
    - scrape_wikipedia: Wikipedia search/scrape only (optional URL to skip search)
    - scrape_musicbrainz: MusicBrainz search only (optional URL to skip search)
    """
    _update_job(job_id, status=JobStatus.tagging, started_at=datetime.now(timezone.utc),
                celery_task_id=getattr(getattr(self, 'request', None), 'id', None))

    db = SessionLocal()
    try:
        video_item = db.query(VideoItem).get(video_id)
        if not video_item:
            _update_job(job_id, status=JobStatus.failed, error_message="Video not found")
            return

        _check_cancelled(job_id)
        _save_metadata_snapshot(db, video_item, "metadata_scrape")
        _backfill_source_platform_metadata(db, video_item, job_id, force=True)

        modes = []
        if ai_auto_analyse:
            modes.append("AI Auto Analyse")
        if ai_only:
            modes.append("AI Only")
        if scrape_wikipedia:
            modes.append("Scrape Wikipedia")
        if scrape_musicbrainz:
            modes.append("Scrape MusicBrainz")
        _append_job_log(job_id, f"Metadata scrape for: {video_item.artist} - {video_item.title} [{', '.join(modes)}]")

        locked = video_item.locked_fields or []
        updated_fields = []
        proposed = {}  # Collect proposed metadata changes for user review
        proposed_source_list: list[dict] = []  # Collect source link proposals for user review
        import re as _re
        from sqlalchemy.exc import IntegrityError as _IntegrityError
        from app.services.source_validation import sanitize_album as _sanitize_album
        source_log: list[str] = []
        progress = 5
        # Track album-specific MusicBrainz IDs for artwork pipeline
        _local_mb_album_release_id: str | None = None
        _local_mb_album_release_group_id: str | None = None
        # Album name resolved by the AI unified pipeline â€” preserved so later
        # modes (MusicBrainz scrape) cannot override it for artwork lookup.
        # This keeps the artwork pathway aligned with the scraper test.
        _ai_resolved_album: str | None = None
        _ai_resolved_artist: str | None = None
        _ai_wiki_single_url: str | None = None  # Wikipedia single/song page URL from unified pipeline
        _pipeline_artist_art_url: str | None = None  # Artist art URL from unified pipeline artwork candidates
        _pipeline_album_art_url: str | None = None  # Album art URL from unified pipeline artwork candidates

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # Apply user-provided version type hints
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        if hint_cover or hint_live or hint_alternate or hint_uncensored:
            if hint_cover:
                video_item.version_type = "cover"
            elif hint_live:
                video_item.version_type = "live"
            elif hint_uncensored:
                video_item.version_type = "uncensored"
            elif hint_alternate:
                video_item.version_type = "alternate"
            video_item.alternate_version_label = hint_alternate_label or None
            _safe_commit(db)
            _append_job_log(job_id, f"Version type set: {video_item.version_type}"
                            + (f" ({hint_alternate_label})" if hint_alternate_label else ""))

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # MODE: AI Auto Analyse (full import-style unified pipeline)
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        if ai_auto_analyse:
            _check_cancelled(job_id)
            _update_job(job_id, current_step="Running AI auto-analyse pipeline", progress_percent=10)
            _append_job_log(job_id, "Running full AI-guided pipeline (same as import)...")

            # Get source info for the unified pipeline
            yt_source = db.query(Source).filter(
                Source.video_id == video_id,
                Source.provider.in_([SourceProvider.youtube, SourceProvider.vimeo]),
            ).first()
            source_url = yt_source.canonical_url if yt_source else ""

            # Re-parse artist/title from raw platform title to match the
            # scraper-test behaviour.  Using the already-enriched DB values
            # feeds previous corrections back into the AI and leads to
            # different MusicBrainz/Wikipedia matches.
            _raw_platform_title = (yt_source.platform_title or "") if yt_source else ""
            if _raw_platform_title:
                _parsed_artist, _parsed_title = extract_artist_title(_raw_platform_title)
            else:
                _parsed_artist, _parsed_title = "", ""
            _pipeline_artist = _parsed_artist or (
                (yt_source.channel_name or "") if yt_source else ""
            ) or video_item.artist or ""
            _pipeline_title = _parsed_title or video_item.title or ""
            # Strip duplicated artist prefix from title (same as scraper test)
            if _pipeline_artist and _pipeline_title:
                for _sep in [" - ", " \u2014 ", " \u2013 ", " : "]:
                    _prefix = _pipeline_artist + _sep
                    if _pipeline_title.lower().startswith(_prefix.lower()):
                        _pipeline_title = _pipeline_title[len(_prefix):].strip()
                        break

            try:
                # Get duration from quality signature if available
                _duration = None
                if video_item.quality_signature:
                    _duration = video_item.quality_signature.duration_seconds

                metadata = resolve_metadata_unified(
                    artist=_pipeline_artist,
                    title=_pipeline_title,
                    db=db,
                    source_url=source_url,
                    platform_title=_raw_platform_title,
                    channel_name=yt_source.channel_name or "" if yt_source else "",
                    platform_description=(yt_source.platform_description or "")[:3000] if yt_source else "",
                    platform_tags=(yt_source.platform_tags if yt_source and yt_source.platform_tags else []),
                    upload_date=yt_source.upload_date or "" if yt_source else "",
                    duration_seconds=_duration,
                    filename=os.path.basename(video_item.file_path) if video_item.file_path else "",
                    folder_name=os.path.basename(video_item.folder_path) if video_item.folder_path else "",
                    skip_wikipedia=False,
                    skip_musicbrainz=False,
                    skip_ai=False,
                    log_callback=lambda msg: _append_job_log(job_id, msg),
                )

                # Collect proposed metadata for user review (not applied yet)
                if metadata.get("artist") and "artist" not in locked:
                    proposed["artist"] = metadata["artist"]
                    updated_fields.append("artist")
                if metadata.get("title") and "title" not in locked:
                    proposed["title"] = metadata["title"]
                    updated_fields.append("title")
                if metadata.get("album") and "album" not in locked:
                    proposed["album"] = metadata["album"]
                    updated_fields.append("album")
                # Preserve AI-resolved names for artwork (scraper test parity)
                _ai_resolved_album = metadata.get("album")
                _ai_resolved_artist = metadata.get("artist")
                _ai_wiki_single_url = metadata.get("_source_urls", {}).get("wikipedia")

                # Extract pipeline-found artist/album art URLs from artwork candidates.
                # The unified pipeline already discovers the correct Wikipedia
                # artist page (via infobox cross-link or search) and scrapes
                # the image.  The artwork manager's independent name-search
                # can match the wrong page (e.g. "Mason (band)" vs "Mason_(DJ)"),
                # so we keep the pipeline's result as a fallback.
                _pipeline_artist_art_url: str | None = None
                _pipeline_album_art_url: str | None = None
                for _cand in metadata.get("_artwork_candidates", []):
                    if _cand.get("art_type") == "artist" and _cand.get("url") and not _pipeline_artist_art_url:
                        _pipeline_artist_art_url = _cand["url"]
                    if _cand.get("art_type") == "album" and _cand.get("url") and not _pipeline_album_art_url:
                        _pipeline_album_art_url = _cand["url"]
                if metadata.get("year") and "year" not in locked:
                    proposed["year"] = metadata["year"]
                    updated_fields.append("year")
                if metadata.get("plot") and "plot" not in locked:
                    proposed["plot"] = metadata["plot"]
                    updated_fields.append("plot")
                if metadata.get("genres") and "genres" not in locked:
                    proposed["genres"] = metadata["genres"]
                    updated_fields.append("genres")

                # Extract version_type from AI source resolution (unless user provided hints)
                if not (hint_cover or hint_live or hint_alternate or hint_uncensored):
                    _ai_src = metadata.get("ai_source_resolution")
                    if _ai_src and hasattr(_ai_src, "identity") and _ai_src.identity:
                        _vtype = getattr(_ai_src.identity, "version_type", None)
                        _vlabel = getattr(_ai_src.identity, "alternate_version_label", None)
                        if _vtype and _vtype != "normal":
                            video_item.version_type = _vtype
                            video_item.alternate_version_label = _vlabel or None
                            _append_job_log(job_id, f"Version type from AI: {_vtype}"
                                            + (f" ({_vlabel})" if _vlabel else ""))
                        elif _vtype == "normal" and video_item.version_type:
                            # AI says normal but DB has a version type â€” leave it alone
                            pass

                # Update MusicBrainz IDs
                if metadata.get("mb_artist_id"):
                    video_item.mb_artist_id = metadata["mb_artist_id"]
                if metadata.get("mb_recording_id"):
                    video_item.mb_recording_id = metadata["mb_recording_id"]
                if metadata.get("mb_release_id"):
                    video_item.mb_release_id = metadata["mb_release_id"]
                if metadata.get("mb_release_group_id"):
                    video_item.mb_release_group_id = metadata["mb_release_group_id"]
                # Extract album-level MusicBrainz IDs for artwork pipeline
                if metadata.get("mb_album_release_id"):
                    _local_mb_album_release_id = metadata["mb_album_release_id"]
                if metadata.get("mb_album_release_group_id"):
                    _local_mb_album_release_group_id = metadata["mb_album_release_group_id"]

                # Download poster if available (save as pending for user review)
                if metadata.get("image_url") and video_item.folder_path:
                    folder_name = os.path.basename(video_item.folder_path)
                    _ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
                    poster_path = os.path.join(video_item.folder_path, f"{folder_name}-poster-pending-{_ts}.jpg")
                    thumb_path = os.path.join(video_item.folder_path, f"{folder_name}-thumb-pending-{_ts}.jpg")
                    if download_image(metadata["image_url"], poster_path):
                        from app.services.artwork_service import guarded_copy, validate_file as _vf
                        guarded_copy(poster_path, thumb_path)
                        _vr = _vf(poster_path) if os.path.isfile(poster_path) else None
                        # Hash dedup: skip if valid poster already has identical content
                        _skip_ai_poster = False
                        if _vr and _vr.valid:
                            _existing_ai_vp = db.query(MediaAsset).filter(
                                MediaAsset.video_id == video_id,
                                MediaAsset.asset_type == "poster",
                                MediaAsset.status == "valid",
                                MediaAsset.file_hash == _vr.file_hash,
                            ).first()
                            if _existing_ai_vp and os.path.isfile(_existing_ai_vp.file_path):
                                _skip_ai_poster = True
                                for _tmp_ai in (poster_path, thumb_path):
                                    if os.path.isfile(_tmp_ai):
                                        os.remove(_tmp_ai)
                        if not _skip_ai_poster:
                            for asset_type, asset_path in [("poster", poster_path), ("thumb", thumb_path)]:
                                # Remove any previous pending assets for this type
                                db.query(MediaAsset).filter(
                                    MediaAsset.video_id == video_id,
                                    MediaAsset.asset_type == asset_type,
                                    MediaAsset.status == "pending",
                                ).delete(synchronize_session="fetch")
                                db.add(MediaAsset(
                                    video_id=video_id, asset_type=asset_type,
                                    file_path=asset_path, source_url=metadata["image_url"],
                                    provenance="ai_pipeline",
                                    status="pending" if (_vr and _vr.valid) else "invalid",
                                    width=_vr.width if _vr and _vr.valid else None,
                                    height=_vr.height if _vr and _vr.valid else None,
                                    file_size_bytes=_vr.file_size_bytes if _vr and _vr.valid else None,
                                    file_hash=_vr.file_hash if _vr and _vr.valid else None,
                                    last_validated_at=datetime.now(timezone.utc),
                                ))
                            updated_fields.append("poster")

                db.commit()

                # Record sources from unified pipeline
                _update_job(job_id, current_step="Recording sources", progress_percent=50)
                _unified_source_urls = metadata.get("_source_urls", {})
                proposed_source_list.extend(
                    _collect_source_proposals(db, video_item, video_id, source_log,
                                             metadata_source_urls=_unified_source_urls,
                                             proposed_artist=proposed.get("artist"),
                                             proposed_album=proposed.get("album"),
                                             mb_album_rg_id=_local_mb_album_release_group_id))
                for msg in source_log:
                    _append_job_log(job_id, msg)
                source_log.clear()

                db.commit()

                _append_job_log(job_id, f"AI auto-analyse complete: {', '.join(updated_fields) if updated_fields else 'no changes'}")
                _set_pipeline_step(job_id, "AI auto-analyse", "success")
                _set_processing_flag(db, video_item, "ai_enriched", method="ai_auto_analyse")
                db.commit()
            except Exception as e:
                db.rollback()
                _append_job_log(job_id, f"AI auto-analyse failed: {e}")
                _set_pipeline_step(job_id, "AI auto-analyse", "failed")
                logger.warning(f"AI auto-analyse failed for video {video_id}: {e}")

            progress = 40

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # MODE: AI Only (enrich with AI, no external scrapers)
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        if ai_only:
            _check_cancelled(job_id)
            _update_job(job_id, current_step="Running AI-only enrichment", progress_percent=max(progress, 10))
            _append_job_log(job_id, "Running AI-only enrichment (no MusicBrainz/Wikipedia)...")
            try:
                ai_result = enrich_video_metadata(
                    db, video_id,
                    auto_apply=False,
                    force=True,
                )
                if ai_result:
                    _append_job_log(job_id, f"AI enrichment complete: confidence={ai_result.confidence_score:.2f}")
                    updated_fields.append("ai_enrichment")
                    _set_processing_flag(db, video_item, "ai_enriched", method="ai_enrichment")
                    db.commit()
                    _set_pipeline_step(job_id, "AI enrichment", "success")
                else:
                    _append_job_log(job_id, "AI enrichment skipped (provider not configured)")
                    _set_pipeline_step(job_id, "AI enrichment", "skipped")
            except Exception as e:
                db.rollback()
                _append_job_log(job_id, f"AI enrichment failed: {e}")
                _set_pipeline_step(job_id, "AI enrichment", "failed")
                logger.warning(f"AI-only enrichment failed for video {video_id}: {e}")

            progress = max(progress, 30)

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # MODE: Scrape MusicBrainz
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        if scrape_musicbrainz:
            _check_cancelled(job_id)
            _update_job(job_id, current_step="Scraping MusicBrainz", progress_percent=max(progress, 20))

            # Use the same unified pipeline as the scraper tester for
            # identical MusicBrainz resolution (supports recording URLs,
            # release-group URLs, and search fallback).
            try:
                _mb_metadata = resolve_metadata_unified(
                    artist=video_item.artist or "",
                    title=video_item.title or "",
                    db=db,
                    skip_wikipedia=True,
                    skip_musicbrainz=False,
                    skip_ai=True,
                    musicbrainz_url=musicbrainz_url,
                    log_callback=lambda msg: _append_job_log(job_id, msg),
                )

                # Map unified pipeline results to video_item MB IDs
                if _mb_metadata.get("mb_recording_id"):
                    video_item.mb_recording_id = _mb_metadata["mb_recording_id"]
                if _mb_metadata.get("mb_artist_id"):
                    video_item.mb_artist_id = _mb_metadata["mb_artist_id"]
                if _mb_metadata.get("mb_release_id"):
                    video_item.mb_release_id = _mb_metadata["mb_release_id"]
                if _mb_metadata.get("mb_release_group_id"):
                    video_item.mb_release_group_id = _mb_metadata["mb_release_group_id"]
                if _mb_metadata.get("mb_album_release_id"):
                    _local_mb_album_release_id = _mb_metadata["mb_album_release_id"]
                if _mb_metadata.get("mb_album_release_group_id"):
                    _local_mb_album_release_group_id = _mb_metadata["mb_album_release_group_id"]

                # Collect proposed metadata for user review
                if _mb_metadata.get("artist") and "artist" not in locked:
                    proposed["artist"] = _mb_metadata["artist"]
                    updated_fields.append("artist")
                if _mb_metadata.get("album") and "album" not in locked:
                    _clean_mb_album = _sanitize_album(
                        _mb_metadata["album"],
                        title=_mb_metadata.get("title") or video_item.title or "",
                    )
                    if _clean_mb_album:
                        proposed["album"] = _clean_mb_album
                        updated_fields.append("album")
                if _mb_metadata.get("year") and "year" not in locked:
                    proposed["year"] = _mb_metadata["year"]
                    updated_fields.append("year")
                if _mb_metadata.get("genres") and "genres" not in locked:
                    proposed["genres"] = _mb_metadata["genres"]
                    updated_fields.append("genres")

                if _mb_metadata.get("mb_recording_id"):
                    _append_job_log(job_id,
                        f"MusicBrainz resolved: {_mb_metadata.get('artist', '?')} "
                        f"- {_mb_metadata.get('album', '?')}")
                else:
                    _append_job_log(job_id, "No MusicBrainz match found")
            except Exception as e:
                db.rollback()
                _append_job_log(job_id, f"MusicBrainz resolution failed: {e}")

            _safe_commit(db)

            # Collect MusicBrainz source proposals
            proposed_source_list.extend(
                _collect_mb_source_proposals(db, video_item, video_id, source_log, mb_album_rg_id=_local_mb_album_release_group_id))
            for msg in source_log:
                _append_job_log(job_id, msg)
            source_log.clear()

            _set_pipeline_step(job_id, "Scraping MusicBrainz", "success")
            progress = max(progress, 50)

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # MODE: Scrape Wikipedia
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        wiki = {}
        mismatch_reason = None

        if scrape_wikipedia:
            _check_cancelled(job_id)
            _update_job(job_id, current_step="Scraping Wikipedia", progress_percent=max(progress, 40))

            if wikipedia_url and wikipedia_url.strip():
                wiki_url = wikipedia_url.strip()
                _append_job_log(job_id, f"Using provided Wikipedia URL: {wiki_url}")
            else:
                _append_job_log(job_id, f"Searching Wikipedia for: {video_item.artist} - {video_item.title}")
                wiki_url = search_wikipedia(video_item.title, video_item.artist)

            if not wiki_url:
                _append_job_log(job_id, "No Wikipedia article found")
            else:
                _append_job_log(job_id, f"Found: {wiki_url}")
                _update_job(job_id, current_step="Scraping Wikipedia page", progress_percent=max(progress, 50))
                wiki = scrape_wikipedia_page(wiki_url)

                _append_job_log(job_id, f"Scraped â€” album: {wiki.get('album')}, year: {wiki.get('year')}, "
                                f"genres: {wiki.get('genres')}, has_plot: {bool(wiki.get('plot'))}, "
                                f"has_image: {bool(wiki.get('image_url'))}")

                # Mismatch detection
                mismatch_reason = detect_article_mismatch(wiki, video_item.artist, video_item.title)
                if mismatch_reason:
                    _append_job_log(job_id,
                        f"âš  Article mismatch: {mismatch_reason}. Metadata will NOT be applied.")
                else:
                    # Cover song protection: when the infobox artist differs
                    # from the expected artist, the article describes a song
                    # originally by another artist.  The infobox album/year
                    # belong to the original release — skip merging those.
                    _wiki_artist_raw = (wiki.get("artist") or "").lower().strip()
                    _expected_art_lower = (video_item.artist or "").lower().strip()
                    _is_cover = (
                        _wiki_artist_raw
                        and _expected_art_lower
                        and _wiki_artist_raw != _expected_art_lower
                        and _expected_art_lower not in _wiki_artist_raw
                        and _wiki_artist_raw not in _expected_art_lower
                    )
                    if _is_cover:
                        _append_job_log(job_id,
                            f"Cover song detected (infobox artist "
                            f"'{wiki.get('artist')}' vs expected "
                            f"'{video_item.artist}') — skipping album/year merge")

                    # Collect proposed Wikipedia metadata for review
                    if not _is_cover and wiki.get("album") and "album" not in locked:
                        _clean_wiki_album = _sanitize_album(wiki["album"], title=video_item.title)
                        if _clean_wiki_album:
                            proposed["album"] = _clean_wiki_album
                            updated_fields.append("album")

                    # Propagate the Wikipedia single page URL so the artwork
                    # pipeline can follow infobox cross-links to discover
                    # album/artist art (same as the AI Auto pathway).
                    if not _ai_wiki_single_url:
                        _ai_wiki_single_url = wiki_url
                    if not _is_cover and wiki.get("year") and "year" not in locked:
                        proposed["year"] = wiki["year"]
                        updated_fields.append("year")
                    if wiki.get("plot") and "plot" not in locked:
                        plot = wiki["plot"]
                        try:
                            ai_sum = generate_ai_summary(plot)
                            if ai_sum:
                                plot = ai_sum
                                _append_job_log(job_id, "AI summary generated for plot")
                        except Exception:
                            pass
                        proposed["plot"] = plot
                        updated_fields.append("plot")
                    if wiki.get("genres") and "genres" not in locked:
                        proposed["genres"] = wiki["genres"]
                        updated_fields.append("genres")

                    # Poster + thumb
                    if wiki.get("image_url") and video_item.folder_path:
                        folder_name = os.path.basename(video_item.folder_path)
                        _pending_suffix = f"-pending-{int(datetime.now(timezone.utc).timestamp())}"
                        poster_path = os.path.join(video_item.folder_path, f"{folder_name}-poster{_pending_suffix}.jpg")
                        thumb_path = os.path.join(video_item.folder_path, f"{folder_name}-thumb{_pending_suffix}.jpg")
                        _update_job(job_id, current_step="Downloading poster", progress_percent=max(progress, 55))
                        if download_image(wiki["image_url"], poster_path):
                            from app.services.artwork_service import guarded_copy, validate_file as _vf
                            guarded_copy(poster_path, thumb_path)
                            _vr = _vf(poster_path) if os.path.isfile(poster_path) else None
                            # Remove any previous pending assets for these types
                            db.query(MediaAsset).filter(
                                MediaAsset.video_id == video_id,
                                MediaAsset.asset_type.in_(["poster", "thumb"]),
                                MediaAsset.status == "pending",
                            ).delete(synchronize_session="fetch")
                            for asset_type, asset_path in [("poster", poster_path), ("thumb", thumb_path)]:
                                db.add(MediaAsset(
                                    video_id=video_id, asset_type=asset_type,
                                    file_path=asset_path, source_url=wiki["image_url"],
                                    provenance="wikipedia_scrape",
                                    status="pending" if (_vr and _vr.valid) else "invalid",
                                    width=_vr.width if _vr and _vr.valid else None,
                                    height=_vr.height if _vr and _vr.valid else None,
                                    file_size_bytes=_vr.file_size_bytes if _vr and _vr.valid else None,
                                    file_hash=_vr.file_hash if _vr and _vr.valid else None,
                                    last_validated_at=datetime.now(timezone.utc),
                                ))
                            updated_fields.append("poster")

                _safe_commit(db)

                # Collect Wikipedia source proposal
                if wiki_url and not mismatch_reason:
                    wiki_page_id = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", wiki_url)
                    # Use Wikipedia page type classification for correct source_type
                    _wiki_page_type = wiki.get("page_type", "single")
                    _wiki_source_type = (
                        "artist" if _wiki_page_type == "artist"
                        else "album" if _wiki_page_type == "album"
                        else "single"  # default for song/single/unrelated
                    )
                    existing_wiki_src = db.query(Source).filter(
                        Source.video_id == video_id,
                        Source.provider == SourceProvider.wikipedia,
                        Source.source_type.in_(["single", "recording", _wiki_source_type]),
                    ).first()
                    if not existing_wiki_src:
                        proposed_source_list.append({
                            "provider": "wikipedia",
                            "source_type": _wiki_source_type,
                            "source_video_id": wiki_page_id,
                            "original_url": wiki_url,
                            "provenance": "scraped",
                        })
                    source_log.append(f"Wikipedia source found ({_wiki_source_type}): {wiki_url}")

                # IMDB source (from Wikipedia or direct search)
                imdb_url = wiki.get("imdb_url")
                if not imdb_url:
                    imdb_url = search_imdb_music_video(video_item.artist, video_item.title)
                if imdb_url:
                    existing_imdb_src = db.query(Source).filter(
                        Source.video_id == video_id,
                        Source.provider == SourceProvider.imdb,
                    ).first()
                    if not existing_imdb_src:
                        imdb_id_match = _re.search(r"(tt\d+|nm\d+)", imdb_url)
                        imdb_id = imdb_id_match.group(1) if imdb_id_match else imdb_url
                        proposed_source_list.append({
                            "provider": "imdb",
                            "source_type": "video",
                            "source_video_id": imdb_id,
                            "original_url": imdb_url,
                            "provenance": "scraped",
                        })
                    source_log.append(f"IMDB source found: {imdb_url}")

                # Wikipedia artist/album source proposals
                proposed_source_list.extend(
                    _collect_wiki_source_proposals(db, video_item, video_id, source_log, _re,
                                                  single_wiki_url=wiki_url))

                for msg in source_log:
                    _append_job_log(job_id, msg)
                source_log.clear()

            # Collect artist/album Wikipedia sources even when no song article was found
            if not wiki_url:
                proposed_source_list.extend(
                    _collect_wiki_source_proposals(db, video_item, video_id, source_log, _re))
                if source_log:
                    for msg in source_log:
                        _append_job_log(job_id, msg)
                    source_log.clear()

            progress = max(progress, 65)
            _set_pipeline_step(job_id, "Scraping Wikipedia", "success")

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # Artist / Album artwork pipeline (source-scoped per mode)
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        if scrape_wikipedia or scrape_musicbrainz or ai_auto_analyse:
            _check_cancelled(job_id)

            # Determine artwork source based on active scrape mode
            if scrape_wikipedia and not scrape_musicbrainz and not ai_auto_analyse:
                _art_source = "wikipedia"
            elif scrape_musicbrainz and not scrape_wikipedia and not ai_auto_analyse:
                _art_source = "musicbrainz"
            else:
                _art_source = "all"

            # Use AI-resolved album for artwork when available â€” this matches
            # the scraper test pathway which uses the unified pipeline's album.
            # Fall back to proposed (may include MusicBrainz scrape override)
            # then to the database value.
            _art_album = _ai_resolved_album or proposed.get("album") or video_item.album

            _update_job(job_id, current_step=f"Fetching artist/album artwork ({_art_source})", progress_percent=max(progress, 70))
            # Match the scraper test: pass the full artist name, do NOT
            # apply parse_multi_artist here â€” the artist scraper applies it
            # internally for artist artwork, and the album scraper uses the
            # full name (same as the scraper test's resolved_artist_name).
            _art_artist = _ai_resolved_artist or proposed.get("artist") or video_item.artist

            # â”€â”€ Wikipedia cross-link: follow single page â†’ album page â”€â”€
            # Mirrors the scraper test: follow the single's Wikipedia infobox
            # "from the album" link to discover the correct album page and
            # its artwork.  This is necessary because MusicBrainz may resolve
            # a parent album (e.g. "Disclaimer II") while the single actually
            # appeared on a different release (e.g. "The Punisher: The Album").
            _crosslink_album_image_url: str | None = None
            _crosslink_album_name: str | None = None
            if _ai_wiki_single_url and _art_source != "musicbrainz":
                try:
                    from app.scraper.metadata_resolver import (
                        extract_wiki_infobox_links,
                        scrape_wikipedia_page as _scrape_wiki_page,
                    )
                    from urllib.parse import unquote as _url_unquote_art
                    _single_links = extract_wiki_infobox_links(_ai_wiki_single_url)

                    # Cover song protection: validate the infobox artist
                    # matches our resolved artist before following album link.
                    _art_xlink_ok = True
                    _linked_artist_url = _single_links.get("artist_url")
                    if _linked_artist_url and _art_artist:
                        _link_page = _url_unquote_art(
                            _linked_artist_url.rsplit("/wiki/", 1)[-1]
                        ).replace("_", " ").strip()
                        _lp = _link_page.lower()
                        _ra = _art_artist.lower().strip()
                        if not (_lp == _ra or _lp in _ra or _ra in _lp):
                            _art_xlink_ok = False
                            _append_job_log(job_id,
                                f"Wikipedia cross-link: infobox artist "
                                f"'{_link_page}' doesn't match resolved "
                                f"'{_art_artist}' (cover song?) "
                                f"-- discarding artwork cross-link")

                    _linked_album_url = _single_links.get("album_url")
                    if _linked_album_url and _art_xlink_ok:
                        _append_job_log(job_id, f"Wikipedia cross-link: album page â†’ {_linked_album_url}")
                        _album_wiki_page = _scrape_wiki_page(_linked_album_url)
                        if _album_wiki_page and _album_wiki_page.get("image_url"):
                            _crosslink_album_image_url = _album_wiki_page["image_url"]
                            _crosslink_album_name = _album_wiki_page.get("title")
                            _append_job_log(job_id, f"Wikipedia cross-link album art: {_crosslink_album_image_url} (album: {_crosslink_album_name})")
                except Exception as e:
                    _append_job_log(job_id, f"Wikipedia cross-link failed (non-fatal): {e}")

            try:
                art_result = process_artist_album_artwork(
                    artist=_art_artist,
                    album=_art_album,
                    mb_artist_id=video_item.mb_artist_id,
                    log_callback=lambda msg: _append_job_log(job_id, msg),
                    overwrite=True,
                    source=_art_source,
                )

                # â”€â”€ Wikipedia cross-link override â”€â”€
                # The cross-link follows the single's Wikipedia infobox to
                # discover the actual album the track appeared on.  When the
                # cross-link resolves a DIFFERENT album than the one MusicBrainz
                # found (e.g. "The Punisher: The Album" vs "Disclaimer II"),
                # the cross-link is more specific and always wins â€” regardless
                # of whether the MusicBrainz name-search found CAA art, because
                # that CAA art would be for the wrong album.
                # When the albums match, the existing art is kept (CAA quality).
                if _crosslink_album_image_url:
                    _existing_album_url = art_result.get("album_image_url") or ""
                    _cl_album = _crosslink_album_name or _art_album
                    _albums_differ = (
                        _crosslink_album_name
                        and _crosslink_album_name.lower().strip() != _art_album.lower().strip()
                    )
                    if _albums_differ or "coverartarchive.org" not in _existing_album_url:
                        # Use the cross-linked album name for the artwork folder
                        _reason = f"different album: {_cl_album} vs {_art_album}" if _albums_differ else "overrides Wikipedia name-search"
                        _append_job_log(job_id, f"Applying Wikipedia cross-link album art ({_reason}) â†’ album: {_cl_album}")
                        from app.services.artwork_manager import ensure_album_artwork as _ensure_crosslink
                        _cl_override = _ensure_crosslink(
                            artist=_art_artist,
                            album=_cl_album,
                            image_url=_crosslink_album_image_url,
                            overwrite=True,
                        )
                        if _cl_override.get("poster_path"):
                            art_result["album_poster"] = _cl_override["poster_path"]
                            art_result["album_image_url"] = _crosslink_album_image_url
                            # Update the album name so the DB/UI reflects the
                            # cross-linked album, not the MusicBrainz parent.
                            if _crosslink_album_name:
                                proposed["album"] = _crosslink_album_name
                                _art_album = _crosslink_album_name
                                _append_job_log(job_id, f"Album name updated to cross-link: {_crosslink_album_name}")
                    else:
                        _append_job_log(job_id, f"Same album â€” CoverArtArchive art takes priority over cross-link")

                
                # ── Pipeline artist art fallback ──
                # The unified pipeline discovers the correct Wikipedia artist
                # page via infobox cross-link (e.g. Mason_(DJ)) and scrapes
                # the image.  If the artwork manager's independent name-search
                # found nothing (it can match the wrong page), use the pipeline
                # result instead.
                if _pipeline_artist_art_url and not art_result.get("artist_image_url"):
                    _append_job_log(job_id, f"Artist art: applying pipeline-found image (artwork manager found none) -> {_pipeline_artist_art_url}")
                    from app.services.artwork_manager import ensure_artist_artwork as _ensure_pipeline_artist
                    _pa_override = _ensure_pipeline_artist(
                        artist=_art_artist,
                        image_url=_pipeline_artist_art_url,
                        mb_artist_id=video_item.mb_artist_id,
                        overwrite=True,
                    )
                    if _pa_override.get("poster_path"):
                        art_result["artist_poster"] = _pa_override["poster_path"]
                        art_result["artist_image_url"] = _pipeline_artist_art_url
                        _append_job_log(job_id, f"Artist poster saved (pipeline fallback): {_pa_override['poster_path']}")

                # ── Pipeline album art fallback ──
                # Same pattern as artist art: the unified pipeline discovers
                # the Wikipedia album page and its cover image.  If the artwork
                # manager's own Wikipedia/CAA search found nothing, use the
                # pipeline result instead.
                if _pipeline_album_art_url and not art_result.get("album_poster"):
                    _append_job_log(job_id, f"Album art: applying pipeline-found image (artwork manager found none) -> {_pipeline_album_art_url}")
                    from app.services.artwork_manager import ensure_album_artwork as _ensure_pipeline_album
                    _pal_override = _ensure_pipeline_album(
                        artist=_art_artist,
                        album=_art_album,
                        image_url=_pipeline_album_art_url,
                        overwrite=True,
                    )
                    if _pal_override.get("poster_path"):
                        art_result["album_poster"] = _pal_override["poster_path"]
                        art_result["album_image_url"] = _pipeline_album_art_url
                        _append_job_log(job_id, f"Album poster saved (pipeline fallback): {_pal_override['poster_path']}")

                from app.services.artwork_service import validate_file as _vf_art
                # When no parent album was found the release is a single â€”
                # its cover art belongs on the video poster, not album art.
                # In wiki-only mode MB IDs won't be set, so also check whether
                # an album name was resolved (from Wikipedia or proposed metadata).
                _has_parent_album = bool(
                    _local_mb_album_release_id
                    or _local_mb_album_release_group_id
                    or _art_album  # album name resolved by any scraper
                )
                if not _has_parent_album:
                    # Single: remove any stale pending album_thumb from prior scans
                    db.query(MediaAsset).filter(
                        MediaAsset.video_id == video_id,
                        MediaAsset.asset_type == "album_thumb",
                        MediaAsset.status == "pending",
                    ).delete(synchronize_session="fetch")
                for _art_key, _art_asset_type in [("artist_poster", "artist_thumb"), ("album_poster", "album_thumb")]:
                    if _art_asset_type == "album_thumb" and not _has_parent_album:
                        continue  # single cover â†’ poster only, handled below
                    _art_path = art_result.get(_art_key)
                    if not _art_path:
                        continue
                    _art_vr = _vf_art(_art_path) if os.path.isfile(_art_path) else None
                    # Always clean up stale pending assets BEFORE hash dedup.
                    # A prior run may have created a pending asset for a wrong
                    # album (e.g. cover song cross-link bug).  If the correct
                    # valid asset already exists with the same hash, we skip
                    # creating a new pending asset but must still remove stale.
                    db.query(MediaAsset).filter(
                        MediaAsset.video_id == video_id,
                        MediaAsset.asset_type == _art_asset_type,
                        MediaAsset.status == "pending",
                    ).delete(synchronize_session="fetch")
                    # Hash dedup: skip if valid asset already has identical content
                    if _art_vr and _art_vr.valid:
                        _existing_valid_art = db.query(MediaAsset).filter(
                            MediaAsset.video_id == video_id,
                            MediaAsset.asset_type == _art_asset_type,
                            MediaAsset.status == "valid",
                        ).first()
                        if _existing_valid_art and _existing_valid_art.file_hash == _art_vr.file_hash:
                            if os.path.isfile(_existing_valid_art.file_path):
                                continue  # identical content, file OK
                            # Valid file missing - repair path in-place
                            _existing_valid_art.file_path = _art_path
                            _existing_valid_art.last_validated_at = datetime.now(timezone.utc)
                            _append_job_log(job_id, f"Repaired {_art_asset_type} path (same content, file was missing)")
                            continue
                        # Same file path (entity folder overwritten) - refresh hash in-place
                        if _existing_valid_art and os.path.normpath(_existing_valid_art.file_path) == os.path.normpath(_art_path):
                            _existing_valid_art.file_hash = _art_vr.file_hash
                            _existing_valid_art.width = _art_vr.width
                            _existing_valid_art.height = _art_vr.height
                            _existing_valid_art.file_size_bytes = _art_vr.file_size_bytes
                            _existing_valid_art.last_validated_at = datetime.now(timezone.utc)
                            continue
                    # Remove any previous pending assets for this type
                    db.query(MediaAsset).filter(
                        MediaAsset.video_id == video_id,
                        MediaAsset.asset_type == _art_asset_type,
                        MediaAsset.status == "pending",
                    ).delete(synchronize_session="fetch")
                    _art_fields = dict(
                        file_path=_art_path,
                        provenance="artwork_pipeline",
                        status="pending" if (_art_vr and _art_vr.valid) else "invalid",
                        width=_art_vr.width if _art_vr and _art_vr.valid else None,
                        height=_art_vr.height if _art_vr and _art_vr.valid else None,
                        file_size_bytes=_art_vr.file_size_bytes if _art_vr and _art_vr.valid else None,
                        file_hash=_art_vr.file_hash if _art_vr and _art_vr.valid else None,
                        last_validated_at=datetime.now(timezone.utc),
                    )
                    db.add(MediaAsset(video_id=video_id, asset_type=_art_asset_type, **_art_fields))
                    updated_fields.append(f"{_art_asset_type.split('_')[0]}_artwork")

                # Video poster from entity artwork â€” policy aligned with
                # import logic (step 8c.7):
                #   1. Single cover from CoverArtArchive â†’ video poster
                #      (guarded: skip when mb_release_id matches album release)
                #   2. Album cover is NOT used as video poster when a parent
                #      album exists (generic album art is a downgrade from the
                #      YouTube thumbnail for music videos)
                #   3. When NO parent album, the artwork pipeline cover IS the
                #      single/release cover and can serve as video poster
                # Wikipedia scrape already sets its own poster from the
                # infobox image (earlier block) â€” don't overwrite it.
                _wiki_already_set_poster = scrape_wikipedia and "poster" in updated_fields
                _video_poster_url = None
                _video_poster_source = None

                if not _wiki_already_set_poster:
                    # Use shared selection logic (identical to scraper tester)
                    try:
                        from app.scraper.artwork_selection import fetch_caa_artwork
                        _caa_url, _caa_source, _caa_art_type = fetch_caa_artwork(
                            mb_release_id=video_item.mb_release_id,
                            mb_release_group_id=video_item.mb_release_group_id,
                            mb_album_release_group_id=_local_mb_album_release_group_id,
                        )
                        if _caa_url and _caa_art_type == "poster":
                            # Skip the upgrade if a quality poster already exists
                            # AND it's from the same CAA URL.  When the existing
                            # poster came from a different CAA release (e.g. a
                            # remix pressing instead of the canonical single),
                            # allow the upgrade so the correct art replaces it.
                            _existing_poster = db.query(MediaAsset).filter(
                                MediaAsset.video_id == video_id,
                                MediaAsset.asset_type == "poster",
                                MediaAsset.status == "valid",
                            ).first()
                            _existing_source = (
                                getattr(_existing_poster, "source_url", None)
                                or getattr(_existing_poster, "resolved_url", None)
                            ) if _existing_poster else None
                            _same_source = bool(
                                _existing_source and _caa_url
                                and _existing_source == _caa_url
                            )
                            _has_quality_poster = (
                                _existing_poster is not None
                                and _existing_poster.provenance not in ("thumb_fallback", "video_thumb_fallback")
                                and _same_source
                            )
                            if _has_quality_poster:
                                _append_job_log(job_id, "Single poster already exists — skipping CAA upgrade")
                            else:
                                _video_poster_url = _caa_url
                                _video_poster_source = "single_cover"
                                _append_job_log(job_id, f"Using single cover art for video poster")
                    except Exception as _e:
                        _append_job_log(job_id, f"Single cover lookup failed (non-fatal): {_e}")
                    # No parent album fallback: pipeline cover IS the single cover
                    if not _video_poster_url and not _has_parent_album and art_result.get("album_image_url"):
                        _video_poster_url = art_result["album_image_url"]
                        _video_poster_source = "single_cover"

                _album_art_path = art_result.get("album_poster")
                _video_poster_path = None

                if _video_poster_url and video_item.folder_path:
                    from app.services.artwork_service import guarded_copy
                    _folder_name = os.path.basename(video_item.folder_path)
                    _pending_ts = int(datetime.now(timezone.utc).timestamp())
                    _poster_dst = os.path.join(video_item.folder_path, f"{_folder_name}-poster-pending-{_pending_ts}.jpg")
                    _thumb_dst = os.path.join(video_item.folder_path, f"{_folder_name}-thumb-pending-{_pending_ts}.jpg")

                    _poster_ok = False
                    # Prefer local file when available (no-parent-album case),
                    # otherwise download from CoverArtArchive URL
                    if not _has_parent_album and _album_art_path and os.path.isfile(_album_art_path):
                        import shutil as _art_shutil
                        _art_shutil.copy2(_album_art_path, _poster_dst)
                        _poster_ok = True
                    else:
                        _poster_ok = download_image(_video_poster_url, _poster_dst)

                    if _poster_ok:
                        guarded_copy(_poster_dst, _thumb_dst)
                        _poster_vr = _vf_art(_poster_dst) if os.path.isfile(_poster_dst) else None
                        # Hash dedup: skip if valid poster already has identical content
                        _skip_poster_dedup = False
                        if _poster_vr and _poster_vr.valid:
                            _existing_vp = db.query(MediaAsset).filter(
                                MediaAsset.video_id == video_id,
                                MediaAsset.asset_type == "poster",
                                MediaAsset.status == "valid",
                                MediaAsset.file_hash == _poster_vr.file_hash,
                            ).first()
                            if _existing_vp and os.path.isfile(_existing_vp.file_path):
                                _skip_poster_dedup = True
                                # Clean up downloaded temp files
                                for _tmp_p in (_poster_dst, _thumb_dst):
                                    if os.path.isfile(_tmp_p):
                                        os.remove(_tmp_p)
                                # Also clean up any pending poster/thumb from earlier
                                # pipeline steps (e.g. AI/Wikipedia poster) and their
                                # temp files, since CoverArtArchive (authoritative)
                                # matches the existing valid poster.
                                for _cleanup_type in ("poster", "thumb"):
                                    _stale = db.query(MediaAsset).filter(
                                        MediaAsset.video_id == video_id,
                                        MediaAsset.asset_type == _cleanup_type,
                                        MediaAsset.status == "pending",
                                    ).all()
                                    for _s in _stale:
                                        if _s.file_path and os.path.isfile(_s.file_path):
                                            os.remove(_s.file_path)
                                    db.query(MediaAsset).filter(
                                        MediaAsset.video_id == video_id,
                                        MediaAsset.asset_type == _cleanup_type,
                                        MediaAsset.status == "pending",
                                    ).delete(synchronize_session="fetch")
                                if "poster" in updated_fields:
                                    updated_fields.remove("poster")
                                _append_job_log(job_id, "Poster unchanged (identical hash) - skipped")
                        if not _skip_poster_dedup:
                            for _vp_type, _vp_path in [("poster", _poster_dst), ("thumb", _thumb_dst)]:
                                db.query(MediaAsset).filter(
                                    MediaAsset.video_id == video_id,
                                    MediaAsset.asset_type == _vp_type,
                                    MediaAsset.status == "pending",
                                ).delete(synchronize_session="fetch")
                                db.add(MediaAsset(
                                    video_id=video_id, asset_type=_vp_type,
                                    file_path=_vp_path,
                                    source_url=_video_poster_url,
                                    provenance="artwork_pipeline",
                                    status="pending" if (_poster_vr and _poster_vr.valid) else "invalid",
                                    width=_poster_vr.width if _poster_vr and _poster_vr.valid else None,
                                    height=_poster_vr.height if _poster_vr and _poster_vr.valid else None,
                                    file_size_bytes=_poster_vr.file_size_bytes if _poster_vr and _poster_vr.valid else None,
                                    file_hash=_poster_vr.file_hash if _poster_vr and _poster_vr.valid else None,
                                    last_validated_at=datetime.now(timezone.utc),
                                ))
                            if "poster" not in updated_fields:
                                updated_fields.append("poster")
                            _append_job_log(job_id, f"Video poster created from {_video_poster_source}")

                db.commit()
                _set_pipeline_step(job_id, "Fetching artwork", "success")
            except Exception as e:
                db.rollback()
                _set_pipeline_step(job_id, "Fetching artwork", "failed")
                _append_job_log(job_id, f"Artist/album artwork error (non-fatal): {e}")
                logger.warning(f"Artist/album artwork failed for video {video_id}: {e}")

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # Common: Mark artwork as fetched in processing state
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        _artwork_fields = {"poster", "artist_artwork", "album_artwork"}
        if _artwork_fields & set(updated_fields):
            _set_processing_flag(db, video_item, "artwork_fetched", method="scrape")
            _safe_commit(db)

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # Common: Create AIMetadataResult for user review
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        if not ai_only:
            _update_job(job_id, current_step="Storing proposed changes for review", progress_percent=85)
            _set_pipeline_step(job_id, "Storing proposed changes", "in_progress")
            from app.ai.models import AIMetadataResult, AIProvider, AIResultStatus
            current_genres = [g.name for g in video_item.genres] if video_item.genres else []
            _model_label = (
                "ai_auto_analyse" if ai_auto_analyse
                else "musicbrainz_scrape" if scrape_musicbrainz
                else "wikipedia_scrape"
            )
            # YouTube source matching (if enabled)
            if find_source_video:
                _check_cancelled(job_id)
                _set_pipeline_step(job_id, "YouTube matching", "running")
                try:
                    from app.services.youtube_matcher import find_best_youtube_match, verify_youtube_link

                    _yt_artist = proposed.get("artist") or video_item.artist
                    _yt_title = proposed.get("title") or video_item.title
                    _yt_duration = (video_item.quality_signature.duration_seconds
                                    if video_item.quality_signature else None)
                    _yt_match = None

                    # Check existing YouTube source first
                    _existing_yt = ""
                    for _s in video_item.sources:
                        if _s.provider.value == "youtube" and _s.source_type == "video":
                            _existing_yt = _s.canonical_url or _s.original_url or ""
                            break

                    if _existing_yt:
                        _append_job_log(job_id, f"Verifying existing YouTube link: {_existing_yt}")
                        _yt_match = verify_youtube_link(
                            _existing_yt, _yt_artist, _yt_title,
                            duration_seconds=int(_yt_duration) if _yt_duration else None,
                        )
                        if _yt_match:
                            _append_job_log(job_id, f"Existing YouTube link verified (score={_yt_match.overall_score:.3f})")
                        else:
                            _append_job_log(job_id, "Existing YouTube link failed verification, searching...")

                    if not _yt_match:
                        _yt_match = find_best_youtube_match(
                            _yt_artist, _yt_title,
                            duration_seconds=int(_yt_duration) if _yt_duration else None,
                        )

                    if _yt_match:
                        proposed_source_list.append({
                            "provider": "youtube",
                            "source_video_id": _yt_match.video_id,
                            "original_url": _yt_match.url,
                            "source_type": "video",
                            "provenance": "scrape_metadata",
                        })
                        _append_job_log(job_id, f"YouTube match: {_yt_match.url} (score={_yt_match.overall_score:.3f})")
                        _set_pipeline_step(job_id, "YouTube matching", "success")
                    else:
                        _append_job_log(job_id, "No YouTube match found above threshold")
                        _set_pipeline_step(job_id, "YouTube matching", "skipped")
                except Exception as _yt_err:
                    _append_job_log(job_id, f"YouTube matching warning: {_yt_err}")
                    _set_pipeline_step(job_id, "YouTube matching", "warning")

            # Ensure all core fields are populated â€” use current values as
            # fallback so the comparison table always shows every field.
            _core_defaults = {
                "artist": video_item.artist,
                "title": video_item.title,
                "album": video_item.album,
                "year": video_item.year,
                "plot": video_item.plot,
                "genres": current_genres,
            }
            for _k, _v in _core_defaults.items():
                if _k not in proposed:
                    proposed[_k] = _v

            _all_proposed_keys = list(proposed.keys())
            if proposed_source_list:
                _all_proposed_keys.append("sources")
            _conf = 0.85 if ai_auto_analyse else 1.0
            _scrape_result = AIMetadataResult(
                video_id=video_id,
                provider=AIProvider.none,
                model_name=_model_label,
                status=AIResultStatus.complete,
                ai_artist=proposed.get("artist"),
                ai_title=proposed.get("title"),
                ai_album=proposed.get("album"),
                ai_year=proposed.get("year"),
                ai_plot=proposed.get("plot"),
                ai_genres=proposed.get("genres"),
                proposed_sources=proposed_source_list if proposed_source_list else None,
                confidence_score=_conf,
                field_scores={f: _conf
                              for f in proposed if f in ("artist", "title", "album", "year", "plot", "genres")},
                original_scraped={
                    "artist": video_item.artist,
                    "title": video_item.title,
                    "album": video_item.album,
                    "year": video_item.year,
                    "plot": video_item.plot,
                    "genres": current_genres,
                },
                change_summary=f"Proposed changes from {_model_label}: {', '.join(_all_proposed_keys)}" if proposed or proposed_source_list else f"No new metadata found from {_model_label}",
            )
            db.add(_scrape_result)
            _safe_commit(db)
            _set_pipeline_step(job_id, "Storing proposed changes", "success")
            _append_job_log(job_id, f"Proposed changes stored for review (result_id={_scrape_result.id})")

        # ══════════════════════════════════════════════════════════════
        # Common: Entity resolution (create/link Artist, Album, Track, Canonical Track)
        # ══════════════════════════════════════════════════════════════
        _scrape_artist = proposed.get("artist") or video_item.artist
        _scrape_title = proposed.get("title") or video_item.title
        _scrape_album = proposed.get("album") or video_item.album
        if _scrape_artist:
            try:
                _update_job(job_id, current_step="Resolving entities", progress_percent=88)
                _s_artist_entity = None
                _s_album_entity = None
                _s_track_entity = None
                _s_canonical_track = None

                _s_resolved_artist = resolve_artist(
                    _scrape_artist, mb_artist_id=video_item.mb_artist_id,
                )
                with db.begin_nested():
                    _s_artist_entity = get_or_create_artist(db, _scrape_artist, resolved=_s_resolved_artist)
                    save_revision(db, "artist", _s_artist_entity.id, "auto_import", "scrape_metadata")

                if _scrape_album and _s_artist_entity:
                    _s_resolved_album = resolve_album(
                        _scrape_artist, _scrape_album,
                        mb_release_id=video_item.mb_release_id,
                    )
                    with db.begin_nested():
                        _s_album_entity = get_or_create_album(db, _s_artist_entity, _scrape_album, resolved=_s_resolved_album)
                        save_revision(db, "album", _s_album_entity.id, "auto_import", "scrape_metadata")

                if _scrape_title and _s_artist_entity:
                    _s_resolved_track = resolve_track(
                        _scrape_artist, _scrape_title,
                        mb_recording_id=video_item.mb_recording_id,
                    )
                    with db.begin_nested():
                        _s_track_entity = get_or_create_track(db, _s_artist_entity, _s_album_entity, _scrape_title, resolved=_s_resolved_track)

                if _s_artist_entity:
                    _s_ct_params = {
                        "title": _scrape_title,
                        "year": proposed.get("year") or video_item.year,
                        "mb_recording_id": video_item.mb_recording_id,
                        "mb_release_id": video_item.mb_release_id,
                        "mb_release_group_id": video_item.mb_release_group_id,
                        "mb_artist_id": video_item.mb_artist_id,
                        "version_type": video_item.version_type or "normal",
                        "original_artist": None,
                        "original_title": None,
                        "genres": proposed.get("genres") or [g.name for g in video_item.genres] if video_item.genres else [],
                        "resolved_track": _s_resolved_track if _scrape_title else None,
                        "artist_entity": _s_artist_entity,
                        "album_entity": _s_album_entity,
                    }
                    with db.begin_nested():
                        _s_canonical_track, _ = get_or_create_canonical_track(db, **_s_ct_params)

                # Link video to entities
                if _s_artist_entity:
                    video_item.artist_entity_id = _s_artist_entity.id
                if _s_album_entity:
                    video_item.album_entity_id = _s_album_entity.id
                if _s_track_entity:
                    video_item.track_id = _s_track_entity.id
                if _s_canonical_track:
                    link_video_to_canonical_track(db, video_item, _s_canonical_track)

                if _s_track_entity or _s_canonical_track:
                    _set_processing_flag(db, video_item, "track_identified", method="scrape_metadata")
                    _set_processing_flag(db, video_item, "canonical_linked", method="scrape_metadata")
                db.commit()
                _set_pipeline_step(job_id, "Resolving entities", "success")
                _append_job_log(job_id, f"Entities resolved: artist={_s_artist_entity is not None}, album={_s_album_entity is not None}, canonical={_s_canonical_track is not None}")
            except Exception as _ent_e:
                db.rollback()
                _set_pipeline_step(job_id, "Resolving entities", "failed")
                _append_job_log(job_id, f"Entity resolution warning (non-fatal): {_ent_e}")
                logger.warning(f"Entity resolution in scrape_metadata failed for video {video_id}: {_ent_e}")

        # ══════════════════════════════════════════════════════════════
        # Common: Write NFO and Playarr XML sidecars
        # ══════════════════════════════════════════════════════════════
        if video_item.folder_path:
            _update_job(job_id, current_step="Writing sidecars", progress_percent=90)
            _nfo_artist = proposed.get("artist") or video_item.artist or ""
            _nfo_title = proposed.get("title") or video_item.title or ""
            _nfo_album = proposed.get("album") or video_item.album or ""
            _nfo_year = proposed.get("year") or video_item.year
            _nfo_plot = proposed.get("plot") or video_item.plot or ""
            _nfo_genres = proposed.get("genres") or [g.name for g in video_item.genres] if video_item.genres else []
            try:
                write_nfo_file(
                    video_item.folder_path,
                    artist=_nfo_artist,
                    title=_nfo_title,
                    album=_nfo_album,
                    year=_nfo_year,
                    genres=_nfo_genres,
                    plot=_nfo_plot,
                    source_url=getattr(video_item, 'source_url', '') or "",
                    resolution_label=getattr(video_item, 'resolution_label', '') or "",
                )
                _set_processing_flag(db, video_item, "nfo_exported", method="scrape_metadata")
                db.commit()
                _set_pipeline_step(job_id, "Writing NFO", "success")
                _append_job_log(job_id, "NFO sidecar written")
            except Exception as _nfo_e:
                db.rollback()
                _set_pipeline_step(job_id, "Writing NFO", "failed")
                _append_job_log(job_id, f"NFO write error (non-fatal): {_nfo_e}")

            try:
                from app.services.playarr_xml import write_playarr_xml
                _merge_existing_xml_quality(db, video_item,
                                            video_item.folder_path)
                db.refresh(video_item)
                write_playarr_xml(video_item, db)
                _set_processing_flag(db, video_item, "xml_exported", method="scrape_metadata")
                _set_pipeline_step(job_id, "Writing Playarr XML", "success")
                _append_job_log(job_id, "Playarr XML sidecar written")
            except Exception as _xml_e:
                db.rollback()
                _set_pipeline_step(job_id, "Writing Playarr XML", "failed")
                _append_job_log(job_id, f"Playarr XML write error (non-fatal): {_xml_e}")

            _safe_commit(db)

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # Common: Finalise
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        _check_cancelled(job_id)
        _save_metadata_snapshot(db, video_item, "metadata_scrape_complete")

        # Mark metadata_resolved if any metadata was proposed
        if proposed:
            _set_processing_flag(db, video_item, "metadata_resolved", method="scrape_metadata")

        # Mark description_generated if a plot was proposed or already exists
        if proposed.get("plot") or video_item.plot:
            from sqlalchemy.orm.attributes import flag_modified as _flag_mod
            _ps = dict(video_item.processing_state or {})
            _ps["description_generated"] = {
                "completed": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "method": "scrape_metadata",
                "version": "1.0",
            }
            video_item.processing_state = _ps
            _flag_mod(video_item, "processing_state")

        # Recompute Playarr content IDs (metadata may have changed)
        try:
            from app.services.content_id import compute_ids_for_video
            _ids = compute_ids_for_video(video_item)
            video_item.playarr_track_id = _ids["playarr_track_id"]
            video_item.playarr_video_id = _ids["playarr_video_id"]
        except Exception as _cid_e:
            _append_job_log(job_id, f"Content ID generation warning: {_cid_e}")

        # Deduplicate updated_fields for summary
        seen = set()
        unique_fields = []
        for f in updated_fields:
            if f not in seen:
                unique_fields.append(f)
                seen.add(f)

        if unique_fields:
            msg = f"Pending review: {', '.join(unique_fields)}"
        else:
            msg = "No new data found"
        _set_pipeline_step(job_id, "Finalising", "success")
        _update_job(job_id, status=JobStatus.complete, progress_percent=100,
                    current_step=msg, completed_at=datetime.now(timezone.utc))
        _safe_commit(db)
        _append_job_log(job_id, f"Metadata scrape complete. {msg}")

        # Queue normalize as a follow-up if requested
        if normalize_audio and video_item.file_path:
            try:
                norm_db = SessionLocal()
                try:
                    _norm_display2 = f"{video_item.artist} \u2013 {video_item.title} \u203a Normalize" if video_item.artist and video_item.title else None
                    norm_job = ProcessingJob(
                        job_type="normalize", status=JobStatus.queued,
                        video_id=video_id,
                        action_label="Normalize",
                        display_name=_norm_display2,
                    )
                    norm_db.add(norm_job)
                    norm_db.commit()
                    dispatch_task(normalize_task, job_id=norm_job.id, video_id=video_id)
                    _append_job_log(job_id, "Normalize queued as follow-up")
                finally:
                    norm_db.close()
            except Exception as ne:
                _append_job_log(job_id, f"Failed to queue normalize: {ne}")

    except JobCancelledError:
        db.rollback()
        clear_cancel(job_id)
        _update_job(job_id, status=JobStatus.cancelled,
                    error_message="Cancelled by user",
                    completed_at=datetime.now(timezone.utc))
        _append_job_log(job_id, "Job cancelled by user")
    except Exception as e:
        db.rollback()
        logger.error(f"Metadata scrape failed: {e}")
        _update_job(job_id, status=JobStatus.failed, error_message=str(e))
        _append_job_log(job_id, f"ERROR: {e}")
    finally:
        db.close()


# Keep old name as alias for backwards compatibility
scrape_wikipedia_task = scrape_metadata_task


def _collect_source_proposals(db, video_item, video_id, source_log, *,
                              metadata_source_urls=None, proposed_artist=None,
                              proposed_album=None, mb_album_rg_id=None):
    """Collect all source link proposals (MusicBrainz + Wikipedia) without writing to DB."""
    proposals = []
    proposals.extend(_collect_mb_source_proposals(db, video_item, video_id, source_log, mb_album_rg_id=mb_album_rg_id))
    import re as _re
    _wiki_single_url = (metadata_source_urls or {}).get("wikipedia")
    proposals.extend(_collect_wiki_source_proposals(
        db, video_item, video_id, source_log, _re,
        single_wiki_url=_wiki_single_url,
        proposed_artist=proposed_artist,
        proposed_album=proposed_album))
    return proposals


def _collect_mb_source_proposals(db, video_item, video_id, source_log, *, mb_album_rg_id=None):
    """Collect MusicBrainz source link proposals without writing to DB."""
    proposals = []

    # Single source — only when a release-group (actual single) exists
    if video_item.mb_release_group_id:
        _mb_src_id = video_item.mb_release_group_id
        mb_url = f"https://musicbrainz.org/release-group/{_mb_src_id}"
        existing = db.query(Source).filter(
            Source.video_id == video_id,
            Source.provider == SourceProvider.musicbrainz,
            Source.source_type.in_(["single", "recording"]),
        ).first()
        if not existing:
            proposals.append({
                "provider": "musicbrainz",
                "source_type": "single",
                "source_video_id": _mb_src_id,
                "original_url": mb_url,
                "provenance": "scraped",
            })
            source_log.append(f"MusicBrainz single source: {mb_url}")
        elif existing.source_video_id != _mb_src_id or "/recording/" in (existing.original_url or ""):
            # Existing source has different ID or is a stale recording URL needing upgrade
            proposals.append({
                "provider": "musicbrainz",
                "source_type": "single",
                "source_video_id": _mb_src_id,
                "original_url": mb_url,
                "provenance": "scraped",
                "_replaces_source_id": existing.id,
            })
            source_log.append(f"MusicBrainz single source updated: {existing.source_video_id} → {_mb_src_id}")

    # Artist source
    if video_item.mb_artist_id:
        _mb_art_url = f"https://musicbrainz.org/artist/{video_item.mb_artist_id}"
        _ex = db.query(Source).filter(
            Source.video_id == video_id,
            Source.provider == SourceProvider.musicbrainz,
            Source.source_type == "artist",
        ).first()
        if not _ex:
            proposals.append({
                "provider": "musicbrainz",
                "source_type": "artist",
                "source_video_id": video_item.mb_artist_id,
                "original_url": _mb_art_url,
                "provenance": "scraped",
            })
            source_log.append(f"MusicBrainz artist source: {_mb_art_url}")
        elif _ex.source_video_id != video_item.mb_artist_id:
            # Existing artist source has a different artist ID — propose update
            proposals.append({
                "provider": "musicbrainz",
                "source_type": "artist",
                "source_video_id": video_item.mb_artist_id,
                "original_url": _mb_art_url,
                "provenance": "scraped",
                "_replaces_source_id": _ex.id,
            })
            source_log.append(f"MusicBrainz artist source updated: {_ex.source_video_id} → {video_item.mb_artist_id}")

    # Album (release-group) source
    _mb_alb_rg_id = mb_album_rg_id
    if not _mb_alb_rg_id and video_item.mb_recording_id:
        try:
            _init_musicbrainz()
            _parent = _find_parent_album(video_item.mb_recording_id)
            if _parent:
                _mb_alb_rg_id = _parent.get("mb_album_release_group_id")
        except Exception:
            pass
    # Fallback: search release groups by album name (handles EPs)
    if not _mb_alb_rg_id and video_item.album and video_item.mb_artist_id:
        try:
            from app.scraper.metadata_resolver import _find_release_group_by_name
            _init_musicbrainz()
            _rg_by_name = _find_release_group_by_name(video_item.mb_artist_id, video_item.album)
            if _rg_by_name:
                _mb_alb_rg_id = _rg_by_name.get("mb_album_release_group_id")
        except Exception:
            pass
    if _mb_alb_rg_id:
        _mb_alb_url = f"https://musicbrainz.org/release-group/{_mb_alb_rg_id}"
        _ex = db.query(Source).filter(
            Source.video_id == video_id,
            Source.provider == SourceProvider.musicbrainz,
            Source.source_type == "album",
        ).first()
        if not _ex:
            proposals.append({
                "provider": "musicbrainz",
                "source_type": "album",
                "source_video_id": _mb_alb_rg_id,
                "original_url": _mb_alb_url,
                "provenance": "scraped",
            })
            source_log.append(f"MusicBrainz album source: {_mb_alb_url}")
        elif _ex.source_video_id != _mb_alb_rg_id:
            # Existing album source has a different release-group ID â€” propose update
            proposals.append({
                "provider": "musicbrainz",
                "source_type": "album",
                "source_video_id": _mb_alb_rg_id,
                "original_url": _mb_alb_url,
                "provenance": "scraped",
                "_replaces_source_id": _ex.id,
            })
            source_log.append(f"MusicBrainz album source updated: {_ex.source_video_id} â†’ {_mb_alb_rg_id}")

    return proposals


def _collect_wiki_source_proposals(db, video_item, video_id, source_log, _re,
                                   single_wiki_url=None, proposed_artist=None,
                                   proposed_album=None):
    """Collect Wikipedia artist, album, and single source proposals without writing to DB."""
    from app.services.source_validation import parse_multi_artist as _pma_wiki2
    _effective_artist = proposed_artist or video_item.artist
    _effective_album = proposed_album or video_item.album
    _wiki_primary, _ = _pma_wiki2(_effective_artist)
    proposals = []

    # Wikipedia single/song source (from unified pipeline's _source_urls)
    if single_wiki_url and "wikipedia.org" in single_wiki_url:
        _ex_single = db.query(Source).filter(
            Source.video_id == video_id,
            Source.provider == SourceProvider.wikipedia,
            Source.source_type.in_(["single", "recording"]),
        ).first()
        if not _ex_single:
            _wiki_single_page = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", single_wiki_url)
            proposals.append({
                "provider": "wikipedia",
                "source_type": "single",
                "source_video_id": _wiki_single_page,
                "original_url": single_wiki_url,
                "provenance": "scraped",
            })
            source_log.append(f"Wikipedia single source: {single_wiki_url}")
        else:
            _wiki_single_page = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", single_wiki_url)
            if _ex_single.source_video_id != _wiki_single_page:
                proposals.append({
                    "provider": "wikipedia",
                    "source_type": _ex_single.source_type,
                    "source_video_id": _wiki_single_page,
                    "original_url": single_wiki_url,
                    "provenance": "scraped",
                    "_replaces_source_id": _ex_single.id,
                })
                source_log.append(f"Wikipedia single source updated: {_ex_single.source_video_id} \u2192 {_wiki_single_page}")

    # Wikipedia artist source
    try:
        _wiki_art_url = search_wikipedia_artist(_wiki_primary)
        if _wiki_art_url:
            _ex = db.query(Source).filter(
                Source.video_id == video_id,
                Source.provider == SourceProvider.wikipedia,
                Source.source_type == "artist",
            ).first()
            if not _ex:
                _wiki_art_page = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", _wiki_art_url)
                proposals.append({
                    "provider": "wikipedia",
                    "source_type": "artist",
                    "source_video_id": _wiki_art_page,
                    "original_url": _wiki_art_url,
                    "provenance": "scraped",
                })
                source_log.append(f"Wikipedia artist source: {_wiki_art_url}")
            else:
                _wiki_art_page = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", _wiki_art_url)
                if _ex.source_video_id != _wiki_art_page:
                    proposals.append({
                        "provider": "wikipedia",
                        "source_type": "artist",
                        "source_video_id": _wiki_art_page,
                        "original_url": _wiki_art_url,
                        "provenance": "scraped",
                        "_replaces_source_id": _ex.id,
                    })
                    source_log.append(f"Wikipedia artist source updated: {_ex.source_video_id} \u2192 {_wiki_art_page}")
    except Exception as _e:
        logger.debug(f"Wikipedia artist search failed: {_e}")

    # Wikipedia album source
    if _effective_album:
        try:
            _wiki_alb_url = search_wikipedia_album(_wiki_primary, _effective_album)

            # Cross-verify: extract album link from single's Wikipedia infobox
            from app.services.metadata_resolver import extract_album_wiki_url_from_single
            from app.scraper.metadata_resolver import extract_wiki_infobox_links as _extract_xlinks
            from urllib.parse import unquote as _url_unquote
            _sw = single_wiki_url if (single_wiki_url and "wikipedia.org" in (single_wiki_url or "")) else None
            _infobox_alb_url = extract_album_wiki_url_from_single(_sw) if _sw else None
            if _infobox_alb_url:
                # Cover song protection: validate the infobox artist matches
                # our resolved artist before accepting the album cross-link.
                # For cover songs the first infobox links to the *original*
                # artist's album (e.g. Keith Whitley instead of Ronan Keating).
                _xlink_ok = True
                try:
                    _xlinks = _extract_xlinks(_sw) if _sw else {}
                    _xlink_artist_url = _xlinks.get("artist_url")
                    if _xlink_artist_url and _wiki_primary:
                        _link_page = _url_unquote(
                            _xlink_artist_url.rsplit("/wiki/", 1)[-1]
                        ).replace("_", " ").strip()
                        _lp = _link_page.lower()
                        _ra = _wiki_primary.lower().strip()
                        if not (_lp == _ra or _lp in _ra or _ra in _lp):
                            _xlink_ok = False
                            source_log.append(
                                f"Album wiki cross-link: infobox artist "
                                f"'{_link_page}' doesn't match resolved "
                                f"'{_wiki_primary}' (cover song?) "
                                f"-- discarding infobox album override")
                except Exception:
                    pass  # If validation fails, fall through safely

                if _xlink_ok and _wiki_alb_url != _infobox_alb_url:
                    source_log.append(
                        f"Album wiki cross-verified from single infobox: "
                        f"{_wiki_alb_url} â†’ {_infobox_alb_url}")
                if _xlink_ok:
                    _wiki_alb_url = _infobox_alb_url

            if _wiki_alb_url:
                _ex = db.query(Source).filter(
                    Source.video_id == video_id,
                    Source.provider == SourceProvider.wikipedia,
                    Source.source_type == "album",
                ).first()
                if not _ex:
                    _wiki_alb_page = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", _wiki_alb_url)
                    proposals.append({
                        "provider": "wikipedia",
                        "source_type": "album",
                        "source_video_id": _wiki_alb_page,
                        "original_url": _wiki_alb_url,
                        "provenance": "scraped",
                    })
                    source_log.append(f"Wikipedia album source: {_wiki_alb_url}")
                else:
                    _wiki_alb_page = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", _wiki_alb_url)
                    if _ex.source_video_id != _wiki_alb_page:
                        proposals.append({
                            "provider": "wikipedia",
                            "source_type": "album",
                            "source_video_id": _wiki_alb_page,
                            "original_url": _wiki_alb_url,
                            "provenance": "scraped",
                            "_replaces_source_id": _ex.id,
                        })
                        source_log.append(f"Wikipedia album source updated: {_ex.source_video_id} \u2192 {_wiki_alb_page}")
        except Exception as _e:
            logger.debug(f"Wikipedia album search failed: {_e}")

    return proposals


# ---------------------------------------------------------------------------
# Library export task
# ---------------------------------------------------------------------------

@celery_app.task(bind=True)
def library_export_task(self, job_id: int, mode: str = "skip_existing"):
    """Export NFOs, XMLs, and artwork for every video in the library."""
    from app.services.library_export import export_library

    _update_job(job_id, status=JobStatus.analyzing, started_at=datetime.now(timezone.utc),
                celery_task_id=getattr(getattr(self, 'request', None), 'id', None))

    db = SessionLocal()
    try:
        def _log(msg):
            _append_job_log(job_id, msg)

        def _progress(pct):
            _update_job(job_id, progress_percent=pct)

        totals = export_library(db, mode, _log, _progress)

        written = totals["nfo_written"] + totals["xml_written"] + totals["art_written"] + totals["entity_written"]
        _update_job(
            job_id,
            status=JobStatus.complete,
            progress_percent=100,
            current_step=f"Exported {written} files for {totals['total']} videos",
        )
    except Exception as exc:
        logger.error(f"Library export failed: {exc}", exc_info=True)
        _update_job(job_id, status=JobStatus.failed, current_step=str(exc)[:500])
    finally:
        db.close()


def _restore_entity_cached_artwork(
    db, entity_refs: dict, video_item, job_id: int
):
    """
    Restore entity-level CachedAsset records from XML sidecar data.

    For each entity (artist/album), if the XML contains cached_artwork
    entries with source URLs, download them into _PlayarrCache and register
    CachedAsset records. Skips if a valid record already exists for the
    entity+kind combination.
    """
    from app.metadata.models import CachedAsset
    from app.services.artwork_service import fetch_and_store_entity_asset

    for entity_type, ref_key, entity_id in [
        ("artist", "artist", getattr(video_item, "artist_entity_id", None)),
        ("album",  "album",  getattr(video_item, "album_entity_id", None)),
    ]:
        ref = entity_refs.get(ref_key)
        if not ref or not entity_id:
            continue
        cached_art = ref.get("cached_artwork", [])
        for art in cached_art:
            kind = art.get("kind")
            source_url = art.get("source_url")
            if not kind or not source_url:
                continue

            # Skip if a valid CachedAsset already exists
            existing = db.query(CachedAsset).filter(
                CachedAsset.entity_type == entity_type,
                CachedAsset.entity_id == entity_id,
                CachedAsset.kind == kind,
                CachedAsset.status == "valid",
            ).first()
            if existing:
                continue

            try:
                result = fetch_and_store_entity_asset(
                    source_url, entity_type, entity_id, kind,
                    provider=art.get("source_provider") or art.get("provenance") or "xml_import",
                )
                if result and result.success:
                    # Create or update CachedAsset record
                    ca = db.query(CachedAsset).filter(
                        CachedAsset.entity_type == entity_type,
                        CachedAsset.entity_id == entity_id,
                        CachedAsset.kind == kind,
                    ).first()
                    if not ca:
                        ca = CachedAsset(
                            entity_type=entity_type,
                            entity_id=entity_id,
                            kind=kind,
                            local_cache_path=result.path,
                            source_url=source_url,
                            file_hash=result.file_hash or art.get("file_hash"),
                            checksum=result.file_hash,
                            width=result.width,
                            height=result.height,
                            provenance=art.get("provenance") or "xml_import",
                            source_provider=art.get("source_provider") or "xml_import",
                            status="valid",
                        )
                        db.add(ca)
                    else:
                        ca.local_cache_path = result.path
                        ca.source_url = source_url
                        ca.file_hash = result.file_hash or art.get("file_hash")
                        ca.status = "valid"
                    db.flush()
                    _append_job_log(
                        job_id,
                        f"  Restored {entity_type} {kind} from cache"
                    )
            except Exception as e:
                logger.debug(
                    f"Could not restore {entity_type}/{entity_id} {kind}: {e}"
                )


# ---------------------------------------------------------------------------
# Sidecar processing-state verification (called during library scan)
# ---------------------------------------------------------------------------

def _verify_sidecar_processing_states(db, job_id: int) -> int:
    """Walk every tracked VideoItem and reconcile processing_state flags
    against the sidecar files that actually exist on disk.

    Returns the number of flags that were repaired.
    """
    from sqlalchemy.orm.attributes import flag_modified
    from app.services.nfo_parser import find_nfo_for_video
    from app.services.playarr_xml import find_playarr_xml
    from app.models import MediaAsset
    from app.ai.models import AISceneAnalysis

    videos = db.query(VideoItem).filter(
        VideoItem.folder_path.isnot(None),
    ).all()

    total = len(videos)
    repaired = 0
    batch_size = 50

    for idx, video in enumerate(videos):
        if idx % 100 == 0:
            _update_job(job_id, progress_percent=int((idx / max(total, 1)) * 100),
                        step=f"Verifying sidecars ({idx}/{total})")

        folder = video.folder_path
        if not folder or not os.path.isdir(folder):
            continue

        state = dict(video.processing_state or {})
        original_state = dict(state)
        _is_done = lambda step: state.get(step, {}).get("completed", False)

        now_iso = datetime.now(timezone.utc).isoformat()

        def _mark(step: str):
            state[step] = {
                "completed": True,
                "timestamp": now_iso,
                "method": "library_scan_verify",
                "version": "1.0",
            }

        # ── xml_exported: .playarr.xml exists on disk ──
        xml_path = find_playarr_xml(folder, video_file=video.file_path)
        if xml_path and not _is_done("xml_exported"):
            _mark("xml_exported")

        # ── nfo_exported: Kodi .nfo exists on disk ──
        if video.file_path:
            nfo_path = find_nfo_for_video(video.file_path)
            if nfo_path and not _is_done("nfo_exported"):
                _mark("nfo_exported")
            # Also check folder-name NFO pattern used by export_video()
            if not nfo_path:
                folder_name = os.path.basename(folder)
                alt_nfo = os.path.join(folder, f"{folder_name}.nfo")
                if os.path.isfile(alt_nfo) and not _is_done("nfo_exported"):
                    _mark("nfo_exported")

        # ── artwork_fetched: poster or thumb MediaAsset exists ──
        if not _is_done("artwork_fetched"):
            has_art = db.query(MediaAsset.id).filter(
                MediaAsset.video_id == video.id,
                MediaAsset.asset_type.in_(("poster", "thumb")),
                MediaAsset.status == "valid",
            ).first()
            if has_art:
                _mark("artwork_fetched")
            else:
                # Check disk directly for poster/thumb files
                video_stem = os.path.splitext(os.path.basename(video.file_path))[0] if video.file_path else None
                art_found = False
                for candidate in (
                    os.path.join(folder, "poster.jpg"),
                    os.path.join(folder, f"{video_stem}-poster.jpg") if video_stem else None,
                ):
                    if candidate and os.path.isfile(candidate):
                        art_found = True
                        break
                if art_found:
                    _mark("artwork_fetched")

        # ── thumbnail_selected: video_thumb MediaAsset exists ──
        if not _is_done("thumbnail_selected"):
            has_thumb = db.query(MediaAsset.id).filter(
                MediaAsset.video_id == video.id,
                MediaAsset.asset_type == "video_thumb",
            ).first()
            if has_thumb:
                _mark("thumbnail_selected")

        # ── scenes_analyzed: AISceneAnalysis record with status=complete ──
        if not _is_done("scenes_analyzed"):
            has_scene = db.query(AISceneAnalysis.id).filter(
                AISceneAnalysis.video_id == video.id,
                AISceneAnalysis.status == "complete",
            ).first()
            if has_scene:
                _mark("scenes_analyzed")

        # ── file_organized: file exists in library structure ──
        if not _is_done("file_organized"):
            if video.file_path and os.path.isfile(video.file_path):
                _mark("file_organized")

        # ── imported: video record exists (inherently true) ──
        if not _is_done("imported"):
            _mark("imported")

        # ── Commit if anything changed ──
        if state != original_state:
            changed_flags = [k for k in state if state.get(k) != original_state.get(k)]
            video.processing_state = state
            flag_modified(video, "processing_state")
            repaired += len(changed_flags)
            _append_job_log(
                job_id,
                f"Repaired {', '.join(changed_flags)} for {video.artist} - {video.title}",
            )

        # Commit in batches to avoid holding the DB lock too long
        if idx % batch_size == batch_size - 1:
            db.commit()

    # Final commit for remaining items
    db.commit()
    return repaired


# Library scan task
# ---------------------------------------------------------------------------

@celery_app.task(bind=True)
def library_scan_task(self, job_id: int, import_new: bool = True):
    """Scan library directory for untracked files.

    For each folder in the library that isn't already tracked by a VideoItem:
    1. Check for an NFO file and parse metadata from it.
    2. Check for a .playarr.xml sidecar and apply all metadata from it.
    3. Fall back to parsing the folder name (``Artist - Title [Resolution]``).
    4. Create a VideoItem with ``import_method='scanned'``.
    5. Analyse quality signature (resolution, codecs) via ffprobe.
    """
    from app.services.nfo_parser import find_nfo_for_video, parse_nfo_file
    from app.services.playarr_xml import find_playarr_xml, parse_playarr_xml
    from app.models import MediaAsset
    from app.pipeline.db_apply import _upsert_source
    from app.metadata.models import ArtistEntity, AlbumEntity, TrackEntity

    _update_job(job_id, status=JobStatus.analyzing, started_at=datetime.now(timezone.utc),
                celery_task_id=getattr(getattr(self, 'request', None), 'id', None))

    db = SessionLocal()
    try:
        entries = scan_library_directory()
        total = len(entries)
        _append_job_log(job_id, f"Found {total} folders in library")

        new_count = 0
        for i, entry in enumerate(entries):
            _update_job(job_id, progress_percent=int((i / max(total, 1)) * 100))

            # Check if already tracked — use samefile on Windows to
            # handle trailing-dot equivalence (e.g. "M.I.A." == "M.I.A")
            existing = db.query(VideoItem).filter(
                VideoItem.folder_path == entry["folder_path"]
            ).first()

            # Also check by file_path for extra safety
            if not existing:
                existing = db.query(VideoItem).filter(
                    VideoItem.file_path == entry["file_path"]
                ).first()

            if not existing and os.name == "nt":
                # Fallback: check all tracked folder_paths via samefile
                for (vid_fp,) in db.query(VideoItem.folder_path).all():
                    if vid_fp and os.path.isdir(vid_fp):
                        try:
                            if os.path.samefile(vid_fp, entry["folder_path"]):
                                existing = True
                                break
                        except (OSError, ValueError):
                            continue

            if existing:
                continue

            if import_new:
                artist = None
                title = None
                album = None
                year = None
                plot = None
                genres = []
                res_label = None

                # 1. Try NFO file first
                nfo_path = find_nfo_for_video(entry["file_path"])
                if nfo_path:
                    nfo = parse_nfo_file(nfo_path)
                    if nfo:
                        artist = nfo.artist
                        title = nfo.title
                        album = nfo.album
                        year = nfo.year
                        plot = nfo.plot
                        genres = nfo.genres or []
                        # Derive resolution from NFO stream details if available
                        if nfo.video_height:
                            res_label = derive_resolution_label(nfo.video_height)
                        _append_job_log(job_id, f"NFO found for {entry['folder_name']}: {artist} - {title}")

                # 1b. Check for .playarr.xml sidecar — overrides NFO
                xml_sidecar_data = None
                xml_path = find_playarr_xml(entry["folder_path"], video_file=entry["file_path"])
                if xml_path:
                    xml_sidecar_data = parse_playarr_xml(xml_path)
                    if xml_sidecar_data:
                        artist = xml_sidecar_data.get("artist") or artist
                        title = xml_sidecar_data.get("title") or title
                        album = xml_sidecar_data.get("album") or album
                        year = xml_sidecar_data.get("year") or year
                        plot = xml_sidecar_data.get("plot") or plot
                        genres = xml_sidecar_data.get("genres") or genres
                        res_label = xml_sidecar_data.get("resolution_label") or res_label
                        _append_job_log(job_id, f"XML sidecar found for {entry['folder_name']}: {artist} - {title}")

                # 2. Fall back to folder name
                if not artist or not title:
                    fn_artist, fn_title, fn_res = parse_folder_name(entry["folder_name"])
                    if not artist:
                        artist = fn_artist or "Unknown Artist"
                    if not title:
                        title = fn_title or entry["folder_name"]
                    if not res_label:
                        res_label = fn_res

                video_item = VideoItem(
                    artist=artist,
                    title=title,
                    album=album,
                    year=year,
                    plot=plot,
                    folder_path=entry["folder_path"],
                    file_path=entry["file_path"],
                    resolution_label=res_label,
                    file_size_bytes=os.path.getsize(entry["file_path"]) if os.path.isfile(entry["file_path"]) else None,
                    import_method="scanned",
                    song_rating=3,
                    video_rating=3,
                    review_status="needs_human_review",
                    review_category="scanned",
                    review_reason="Untracked file found in library (imported via scan)",
                )

                # If XML sidecar exists, apply additional metadata
                if xml_sidecar_data:
                    # Determine if this XML represents a fully-processed item
                    # that should NOT be flagged for review.
                    ps = xml_sidecar_data.get("processing_state") or {}
                    _completed = lambda step: ps.get(step, {}).get("completed", False)
                    xml_fully_processed = (
                        _completed("metadata_scraped")
                        and (_completed("ai_enriched") or _completed("metadata_resolved"))
                    )
                    # Check if the item was previously dismissed from review
                    xml_rh = xml_sidecar_data.get("review_history") or []
                    previously_dismissed = any(
                        h.get("action") == "dismissed" and h.get("category") == "scanned"
                        for h in xml_rh
                    )
                    if xml_fully_processed or previously_dismissed:
                        video_item.review_status = "none"
                        video_item.review_category = None
                        video_item.review_reason = None
                    else:
                        video_item.review_status = xml_sidecar_data.get("review_status") or "needs_human_review"
                        video_item.review_category = xml_sidecar_data.get("review_category") or "scanned"
                        video_item.review_reason = xml_sidecar_data.get("review_reason") or video_item.review_reason
                    video_item.review_history = xml_rh or None
                    video_item.dismissed_duplicate_ids = xml_sidecar_data.get("dismissed_duplicate_ids")
                    if xml_sidecar_data.get("rename_dismissed"):
                        video_item.rename_dismissed = True
                    video_item.mb_artist_id = xml_sidecar_data.get("mb_artist_id")
                    video_item.mb_recording_id = xml_sidecar_data.get("mb_recording_id")
                    video_item.mb_release_id = xml_sidecar_data.get("mb_release_id")
                    video_item.mb_release_group_id = xml_sidecar_data.get("mb_release_group_id")
                    video_item.version_type = xml_sidecar_data.get("version_type", "normal")
                    video_item.alternate_version_label = xml_sidecar_data.get("alternate_version_label")
                    video_item.original_artist = xml_sidecar_data.get("original_artist")
                    video_item.original_title = xml_sidecar_data.get("original_title")
                    video_item.audio_fingerprint = xml_sidecar_data.get("audio_fingerprint")
                    video_item.acoustid_id = xml_sidecar_data.get("acoustid_id")
                    video_item.processing_state = xml_sidecar_data.get("processing_state")
                    video_item.locked_fields = xml_sidecar_data.get("locked_fields")
                    video_item.exclude_from_editor_scan = xml_sidecar_data.get("exclude_from_editor_scan", False)
                    video_item.field_provenance = xml_sidecar_data.get("field_provenance")
                    video_item.related_versions = xml_sidecar_data.get("related_versions")
                    xml_import_method = xml_sidecar_data.get("import_method")
                    if xml_import_method:
                        video_item.import_method = xml_import_method
                    # Ratings
                    if xml_sidecar_data.get("song_rating_set"):
                        video_item.song_rating = xml_sidecar_data.get("song_rating", 3)
                        video_item.song_rating_set = True
                    if xml_sidecar_data.get("video_rating_set"):
                        video_item.video_rating = xml_sidecar_data.get("video_rating", 3)
                        video_item.video_rating_set = True
                db.add(video_item)
                db.flush()

                # Link genres
                for genre_name in genres:
                    genre_obj = _get_or_create_genre(db, genre_name)
                    if genre_obj not in video_item.genres:
                        video_item.genres.append(genre_obj)

                # Analyze quality
                _scan_qs = None  # Track QS object for XML override below
                try:
                    sig = extract_quality_signature(entry["file_path"])
                    qs = db.query(QualitySignature).filter(
                        QualitySignature.video_id == video_item.id
                    ).first()
                    if not qs:
                        qs = QualitySignature(video_id=video_item.id)
                        db.add(qs)
                    for k, v in sig.items():
                        if hasattr(qs, k):
                            setattr(qs, k, v)
                    video_item.resolution_label = derive_resolution_label(sig.get("height"))
                    _scan_qs = qs
                except Exception as e:
                    _append_job_log(job_id, f"Analysis failed for {entry['folder_name']}: {e}")

                # Apply XML quality overrides (loudness, audio codec, etc.)
                # NOTE: We reference _scan_qs directly instead of
                # video_item.quality_signature because the session uses
                # autoflush=False — the unflushed QualitySignature is
                # invisible to the lazy-loading relationship query.
                if xml_sidecar_data:
                    xml_q = xml_sidecar_data.get("quality", {})
                    if xml_q:
                        # Ensure QS exists even if ffprobe failed
                        if not _scan_qs:
                            _scan_qs = db.query(QualitySignature).filter(
                                QualitySignature.video_id == video_item.id
                            ).first()
                            if not _scan_qs:
                                _scan_qs = QualitySignature(video_id=video_item.id)
                                db.add(_scan_qs)
                        for qf in ("width", "height", "loudness_lufs", "fps", "video_codec", "video_bitrate",
                                    "hdr", "audio_codec", "audio_bitrate",
                                    "audio_sample_rate", "audio_channels",
                                    "container", "duration_seconds",
                                    "letterbox_scanned", "letterbox_detected",
                                    "letterbox_crop_w", "letterbox_crop_h",
                                    "letterbox_crop_x", "letterbox_crop_y",
                                    "letterbox_bar_top", "letterbox_bar_bottom",
                                    "letterbox_bar_left", "letterbox_bar_right"):
                            qv = xml_q.get(qf)
                            if qv is not None:
                                setattr(_scan_qs, qf, qv)

                    # Restore sources
                    xml_sources = xml_sidecar_data.get("sources", [])
                    for src_data in xml_sources:
                        _upsert_source(db, video_item.id, {
                            "provider": src_data.get("provider", ""),
                            "source_video_id": src_data.get("source_video_id", ""),
                            "original_url": src_data.get("original_url", ""),
                            "canonical_url": src_data.get("canonical_url", ""),
                            "source_type": src_data.get("source_type", "video"),
                            "provenance": src_data.get("provenance", "xml_import"),
                            "channel_name": src_data.get("channel_name"),
                            "platform_title": src_data.get("platform_title"),
                            "upload_date": src_data.get("upload_date"),
                        })

                    # Restore artwork references
                    xml_art = xml_sidecar_data.get("artwork", [])
                    for art in xml_art:
                        art_path = art.get("file_path")
                        if art_path and os.path.isfile(art_path):
                            db.add(MediaAsset(
                                video_id=video_item.id,
                                asset_type=art["asset_type"],
                                file_path=art_path,
                                source_url=art.get("source_url"),
                                provenance=art.get("provenance", "xml_import"),
                                source_provider=art.get("source_provider"),
                                file_hash=art.get("file_hash"),
                                status=art.get("status", "valid"),
                                width=art.get("width"),
                                height=art.get("height"),
                                last_validated_at=datetime.now(timezone.utc),
                            ))

                    # Restore scene analysis thumbnails from XML
                    xml_sa = xml_sidecar_data.get("scene_analysis")
                    if xml_sa and xml_sa.get("thumbnails"):
                        from app.ai.models import AISceneAnalysis, AIThumbnail
                        sa = AISceneAnalysis(
                            video_id=video_item.id,
                            status="complete",
                            total_scenes=xml_sa.get("total_scenes", 0),
                            duration_seconds=xml_sa.get("duration_seconds"),
                            scenes=None,
                            config=None,
                        )
                        db.add(sa)
                        db.flush()
                        thumb_dir = os.path.join(
                            get_settings().asset_cache_dir,
                            "thumbnails", str(video_item.id),
                        )
                        os.makedirs(thumb_dir, exist_ok=True)
                        for td in xml_sa["thumbnails"]:
                            old_path = td.get("file_path")
                            new_path = os.path.join(
                                thumb_dir,
                                f"thumb_{td['timestamp_sec']:.2f}.jpg",
                            )
                            # Move/copy the file to the new cache location
                            if old_path and os.path.isfile(old_path) and old_path != new_path:
                                import shutil
                                shutil.copy2(old_path, new_path)
                            final_path = new_path if os.path.isfile(new_path) else old_path
                            if final_path and os.path.isfile(final_path):
                                db.add(AIThumbnail(
                                    video_id=video_item.id,
                                    scene_analysis_id=sa.id,
                                    timestamp_sec=td["timestamp_sec"],
                                    file_path=final_path,
                                    score_sharpness=td.get("score_sharpness", 0),
                                    score_contrast=td.get("score_contrast", 0),
                                    score_color_variance=td.get("score_color_variance", 0),
                                    score_composition=td.get("score_composition", 0),
                                    score_overall=td.get("score_overall", 0),
                                    is_selected=td.get("is_selected", False),
                                    provenance=td.get("provenance", "xml_import"),
                                ))

                    # Restore entity references (artist, album, canonical track)
                    entity_refs = xml_sidecar_data.get("entity_refs", {})

                    # Artist entity — look up by mb_artist_id first, then by name
                    ar = entity_refs.get("artist")
                    if ar:
                        ae = None
                        if ar.get("mb_artist_id"):
                            ae = db.query(ArtistEntity).filter(
                                ArtistEntity.mb_artist_id == ar["mb_artist_id"]
                            ).first()
                        if not ae and ar.get("name"):
                            ae = db.query(ArtistEntity).filter(
                                ArtistEntity.canonical_name == ar["name"]
                            ).first()
                        if not ae and ar.get("name"):
                            ae = ArtistEntity(
                                canonical_name=ar["name"],
                                mb_artist_id=ar.get("mb_artist_id"),
                            )
                            db.add(ae)
                            db.flush()
                        if ae:
                            video_item.artist_entity_id = ae.id

                    # Album entity — look up by mb_release_id, then by title+artist
                    al = entity_refs.get("album")
                    if al:
                        ale = None
                        if al.get("mb_release_id"):
                            ale = db.query(AlbumEntity).filter(
                                AlbumEntity.mb_release_id == al["mb_release_id"]
                            ).first()
                        if not ale and al.get("title") and video_item.artist_entity_id:
                            ale = db.query(AlbumEntity).filter(
                                AlbumEntity.title == al["title"],
                                AlbumEntity.artist_id == video_item.artist_entity_id,
                            ).first()
                        if not ale and al.get("title"):
                            ale = AlbumEntity(
                                title=al["title"],
                                artist_id=video_item.artist_entity_id,
                                mb_release_id=al.get("mb_release_id"),
                                mb_release_group_id=al.get("mb_release_group_id"),
                            )
                            db.add(ale)
                            db.flush()
                        if ale:
                            video_item.album_entity_id = ale.id

                    # Canonical track — look up by mb_recording_id, then by title+artist
                    tr = entity_refs.get("track")
                    if tr:
                        te = None
                        if tr.get("mb_recording_id"):
                            te = db.query(TrackEntity).filter(
                                TrackEntity.mb_recording_id == tr["mb_recording_id"]
                            ).first()
                        if not te and tr.get("title") and video_item.artist_entity_id:
                            te = db.query(TrackEntity).filter(
                                TrackEntity.title == tr["title"],
                                TrackEntity.artist_id == video_item.artist_entity_id,
                            ).first()
                        if not te and tr.get("title"):
                            te = TrackEntity(
                                title=tr["title"],
                                artist_id=video_item.artist_entity_id,
                                album_id=video_item.album_entity_id,
                                mb_recording_id=tr.get("mb_recording_id"),
                                is_cover=tr.get("is_cover", False),
                                original_artist=tr.get("original_artist"),
                                original_title=tr.get("original_title"),
                            )
                            db.add(te)
                            db.flush()
                        if te:
                            video_item.track_id = te.id

                    # Restore entity cached artwork from XML
                    _restore_entity_cached_artwork(
                        db, entity_refs, video_item, job_id,
                    )

                # Fallback: discover thumb_*.jpg files in the video folder
                # if no AIThumbnail records were created from XML.
                from app.ai.models import AISceneAnalysis as _SA, AIThumbnail as _AT
                has_thumbs = db.query(_AT).filter(
                    _AT.video_id == video_item.id,
                ).first() is not None
                if not has_thumbs:
                    import re as _re
                    folder = entry["folder_path"]
                    thumb_files = sorted([
                        f for f in os.listdir(folder)
                        if _re.match(r"thumb_[\d.]+\.jpg$", f)
                    ])
                    if thumb_files:
                        sa = _SA(
                            video_id=video_item.id,
                            status="complete",
                            total_scenes=len(thumb_files),
                            duration_seconds=None,
                        )
                        db.add(sa)
                        db.flush()
                        thumb_dir = os.path.join(
                            get_settings().asset_cache_dir,
                            "thumbnails", str(video_item.id),
                        )
                        os.makedirs(thumb_dir, exist_ok=True)
                        for fn in thumb_files:
                            ts_match = _re.search(r"thumb_([\d.]+)\.jpg$", fn)
                            ts = float(ts_match.group(1)) if ts_match else 0.0
                            src = os.path.join(folder, fn)
                            dst = os.path.join(thumb_dir, fn)
                            if src != dst:
                                import shutil
                                shutil.copy2(src, dst)
                            db.add(_AT(
                                video_id=video_item.id,
                                scene_analysis_id=sa.id,
                                timestamp_sec=ts,
                                file_path=dst,
                                provenance="disk_discovery",
                            ))

                # Poster fallback: if no poster asset was created (XML missing
                # poster, wrong XML picked, or no XML at all), discover a poster
                # image directly from the folder.
                has_poster = db.query(MediaAsset).filter(
                    MediaAsset.video_id == video_item.id,
                    MediaAsset.asset_type == "poster",
                ).first() is not None
                if not has_poster:
                    folder = entry["folder_path"]
                    poster_path = None
                    # Prefer  <stem>-poster.jpg  matching the video filename
                    video_stem = os.path.splitext(os.path.basename(entry["file_path"]))[0]
                    candidate = os.path.join(folder, f"{video_stem}-poster.jpg")
                    if os.path.isfile(candidate):
                        poster_path = candidate
                    else:
                        # Try generic poster.jpg
                        candidate = os.path.join(folder, "poster.jpg")
                        if os.path.isfile(candidate):
                            poster_path = candidate
                        else:
                            # Try any file with -poster in the name
                            for fn in os.listdir(folder):
                                if fn.lower().endswith("-poster.jpg"):
                                    poster_path = os.path.join(folder, fn)
                                    break
                    if poster_path:
                        db.add(MediaAsset(
                            video_id=video_item.id,
                            asset_type="poster",
                            file_path=poster_path,
                            provenance="disk_discovery",
                            status="valid",
                            last_validated_at=datetime.now(timezone.utc),
                        ))

                # Commit each item individually to release the DB write lock.
                db.commit()

                # Set processing flags for scanned items — the file is already
                # in the library structure so these are inherently true.
                _set_processing_flag(db, video_item, "file_organized", method="library_scan")
                _set_processing_flag(db, video_item, "filename_checked", method="library_scan")
                _set_processing_flag(db, video_item, "imported", method="library_scan")
                if xml_sidecar_data:
                    _set_processing_flag(db, video_item, "xml_exported", method="library_scan")
                if nfo_path:
                    _set_processing_flag(db, video_item, "nfo_exported", method="library_scan")

                # Flag tracks with incomplete AI enrichment for the review queue
                ps = video_item.processing_state or {}
                _ps_done = lambda step: ps.get(step, {}).get("completed", False)
                ai_done = _ps_done("ai_enriched")
                scenes_done = _ps_done("scenes_analyzed")
                if not (ai_done and scenes_done):
                    _enrich_cat = "ai_partial" if (ai_done or scenes_done) else "ai_pending"
                    _missing = []
                    if not ai_done:
                        _missing.append("AI metadata")
                    if not scenes_done:
                        _missing.append("scene analysis")
                    video_item.review_status = "needs_human_review"
                    video_item.review_category = _enrich_cat
                    video_item.review_reason = f"Missing {', '.join(_missing)}"

                db.commit()
                new_count += 1
                _append_job_log(job_id, f"Imported: {artist} - {title}")

        _append_job_log(job_id, f"Scan complete. {new_count} new items imported.")

        # ── Phase 2: Verify sidecar processing states for existing videos ──
        _append_job_log(job_id, "Verifying sidecar processing states for existing videos...")
        _update_job(job_id, step="Verifying sidecars")
        repaired = _verify_sidecar_processing_states(db, job_id)
        if repaired:
            _append_job_log(job_id, f"Sidecar verification complete. {repaired} flags repaired.")
        else:
            _append_job_log(job_id, "Sidecar verification complete. All flags consistent.")

        _update_job(job_id, status=JobStatus.complete, progress_percent=100,
                    completed_at=datetime.now(timezone.utc))

    except Exception as e:
        db.rollback()
        logger.error(f"Library scan failed: {e}")
        _update_job(job_id, status=JobStatus.failed, error_message=str(e))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Duplicate scan task
# ---------------------------------------------------------------------------

def _normalize_for_dup(s: str) -> str:
    """Lower-case, strip articles, collapse whitespace for duplicate comparison."""
    import re as _re
    s = s.strip().lower()
    s = _re.sub(r"^(the|a|an)\s+", "", s)
    s = _re.sub(r"[^\w\s]", "", s)
    return _re.sub(r"\s+", " ", s).strip()


def _normalize_title_for_dup(title: str) -> str:
    """Normalize title: strip trailing parenthetical version labels like
    '(Alternate Version)', '(Live)', '(Clean Version)', etc."""
    import re as _re
    # Strip trailing parenthetical/bracketed suffixes that indicate versions
    stripped = _re.sub(r'\s*[\(\[](alternate|live|acoustic|clean|explicit|remix|remaster|censored|uncensored|extended|short|radio|bonus|demo|instrumental|karaoke|version|edit|deluxe|remastered|official|hd|hq|lyric|lyrics|official\s*video|music\s*video|official\s*music\s*video)(?:\s+\w+)*[\)\]]\s*$', '', title, flags=_re.IGNORECASE)
    return _normalize_for_dup(stripped)


@celery_app.task(bind=True)
def duplicate_scan_task(self, job_id: int, rescan_all: bool = False):
    """Scan library for potential duplicate video items.

    Detection tiers (in order of priority):
    1. Normalized artist + title (with version labels stripped)
    2. AcoustID identifier (same recording, exact audio match)
    3. Audio fingerprint similarity (fuzzy match, handles quality/length diffs)

    Args:
        rescan_all: If True, ignore previously dismissed duplicate pairs and
                    re-scan everything.  If False (default), honour
                    dismissed_duplicate_ids flags.
    """
    _update_job(job_id, status=JobStatus.analyzing, started_at=datetime.now(timezone.utc),
                celery_task_id=getattr(getattr(self, "request", None), "id", None))

    FINGERPRINT_SIMILARITY_THRESHOLD = 0.55

    db = SessionLocal()
    try:
        # ── Phase 0: optionally clear dismissed flags ─────────────────
        if rescan_all:
            cleared = 0
            for vi in db.query(VideoItem).filter(VideoItem.dismissed_duplicate_ids.isnot(None)).all():
                vi.dismissed_duplicate_ids = None
                cleared += 1
            if cleared:
                db.commit()
                _append_job_log(job_id, f"Rescan-all: cleared dismissed flags from {cleared} items")
        else:
            # Clean stale dismissed_duplicate_ids referencing deleted videos
            all_video_ids = set(
                r[0] for r in db.query(VideoItem.id).all()
            )
            stale_cleaned = 0
            for vi in db.query(VideoItem).filter(VideoItem.dismissed_duplicate_ids.isnot(None)).all():
                cleaned = [did for did in (vi.dismissed_duplicate_ids or []) if did in all_video_ids]
                if len(cleaned) != len(vi.dismissed_duplicate_ids or []):
                    vi.dismissed_duplicate_ids = cleaned if cleaned else None
                    stale_cleaned += 1
            if stale_cleaned:
                db.commit()
                _append_job_log(job_id, f"Cleaned stale dismissed IDs from {stale_cleaned} items")

        all_items = db.query(VideoItem).all()
        items_by_id = {vi.id: vi for vi in all_items}
        _append_job_log(job_id, f"Scanning {len(all_items)} items for duplicates")

        # ── Phase 1: name-based buckets ───────────────────────────────
        # Normalized artist + title (version labels stripped so "Never
        # Meant" and "Never Meant (Alternate Version)" land together).
        name_buckets: dict[str, list[VideoItem]] = {}
        for vi in all_items:
            if not vi.artist or not vi.title:
                continue
            key = f"{_normalize_for_dup(vi.artist)}||{_normalize_title_for_dup(vi.title)}"
            name_buckets.setdefault(key, []).append(vi)

        dup_groups = {k: v for k, v in name_buckets.items() if len(v) > 1}
        _append_job_log(job_id, f"Name match: {len(dup_groups)} groups")

        # ── Phase 2: acoustid_id buckets ──────────────────────────────
        acoustid_buckets: dict[str, list[VideoItem]] = {}
        for vi in all_items:
            if vi.acoustid_id:
                acoustid_buckets.setdefault(vi.acoustid_id, []).append(vi)

        acoustid_groups = {k: v for k, v in acoustid_buckets.items() if len(v) > 1}
        # Merge acoustid groups that aren't already covered by name buckets
        already_paired: set[frozenset[int]] = set()
        for items in dup_groups.values():
            ids = frozenset(vi.id for vi in items)
            already_paired.add(ids)

        acoustid_new = 0
        for aid, items in acoustid_groups.items():
            ids = frozenset(vi.id for vi in items)
            if ids not in already_paired:
                dup_key = f"__acoustid__{aid}"
                dup_groups[dup_key] = items
                already_paired.add(ids)
                acoustid_new += 1
        if acoustid_new:
            _append_job_log(job_id, f"AcoustID match: {acoustid_new} additional groups")

        # ── Phase 3: fingerprint similarity ───────────────────────────
        # For videos with stored fingerprints that aren't already in a
        # duplicate group, do pairwise comparison.
        fp_items = [vi for vi in all_items if vi.audio_fingerprint]
        grouped_ids = set()
        for items in dup_groups.values():
            for vi in items:
                grouped_ids.add(vi.id)

        ungrouped_fp = [vi for vi in fp_items if vi.id not in grouped_ids]
        fp_new = 0

        if len(ungrouped_fp) >= 2:
            try:
                from app.ai.fingerprint_service import fingerprint_similarity
                _append_job_log(job_id, f"Fingerprint comparison: checking {len(ungrouped_fp)} items")

                # Build similarity pairs via pairwise comparison
                # (O(n^2) but only on ungrouped fingerprinted items — typically small)
                fp_pair_groups: dict[int, set[int]] = {}  # union-find-like
                for i in range(len(ungrouped_fp)):
                    for j in range(i + 1, len(ungrouped_fp)):
                        sim = fingerprint_similarity(
                            ungrouped_fp[i].audio_fingerprint,
                            ungrouped_fp[j].audio_fingerprint,
                        )
                        if sim >= FINGERPRINT_SIMILARITY_THRESHOLD:
                            a_id, b_id = ungrouped_fp[i].id, ungrouped_fp[j].id
                            # Merge into same group
                            group_a = fp_pair_groups.get(a_id)
                            group_b = fp_pair_groups.get(b_id)
                            if group_a and group_b:
                                group_a.update(group_b)
                                for mid in group_b:
                                    fp_pair_groups[mid] = group_a
                            elif group_a:
                                group_a.add(b_id)
                                fp_pair_groups[b_id] = group_a
                            elif group_b:
                                group_b.add(a_id)
                                fp_pair_groups[a_id] = group_b
                            else:
                                new_group = {a_id, b_id}
                                fp_pair_groups[a_id] = new_group
                                fp_pair_groups[b_id] = new_group

                # Collect unique groups
                seen_groups: set[int] = set()
                for vid, members in fp_pair_groups.items():
                    group_id = id(members)
                    if group_id in seen_groups:
                        continue
                    seen_groups.add(group_id)
                    dup_key = f"__fingerprint__{group_id}"
                    dup_groups[dup_key] = [items_by_id[mid] for mid in members if mid in items_by_id]
                    fp_new += 1
            except Exception as fp_err:
                logger.warning(f"Fingerprint comparison failed: {fp_err}")
                _append_job_log(job_id, f"Fingerprint comparison error: {fp_err}")

        if fp_new:
            _append_job_log(job_id, f"Fingerprint similarity: {fp_new} additional groups")

        _append_job_log(job_id, f"Total: {len(dup_groups)} duplicate groups")

        # ── Phase 4: flag for review ──────────────────────────────────
        flagged = 0
        for key, items in dup_groups.items():
            for vi in items:
                other_ids = [x.id for x in items if x.id != vi.id]

                # Check dismissed_duplicate_ids — skip if ALL partners are dismissed
                if not rescan_all:
                    dismissed = set(vi.dismissed_duplicate_ids or [])
                    undismissed = [oid for oid in other_ids if oid not in dismissed]
                    if not undismissed:
                        continue
                else:
                    undismissed = other_ids

                # If already flagged as duplicate, skip
                if vi.review_status in ("needs_human_review", "needs_ai_review") and vi.review_category == "duplicate":
                    continue

                vi.review_status = "needs_human_review"
                vi.review_category = "duplicate"
                vi.review_reason = f"Potential duplicate of video ID(s): {', '.join(map(str, undismissed))}"
                flagged += 1

            if flagged % 50 == 0:
                db.commit()
                _update_job(job_id, progress_percent=min(95, int(flagged / max(len(dup_groups), 1) * 100)))

        db.commit()
        _append_job_log(job_id, f"Duplicate scan complete. {flagged} items flagged for review.")
        _update_job(job_id, status=JobStatus.complete, progress_percent=100,
                    completed_at=datetime.now(timezone.utc))

    except Exception as e:
        db.rollback()
        logger.error(f"Duplicate scan failed: {e}")
        _update_job(job_id, status=JobStatus.failed, error_message=str(e))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Metadata refresh task (new canonical metadata store)
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, max_retries=2)
def metadata_refresh_task(self, job_id: int, video_id: int, force: bool = False):
    """
    Re-resolve canonical entities for a single video and re-export Kodi outputs.

    If ``force=True``, re-query all providers regardless of existing confidence.
    Otherwise, only update entities with confidence < 0.85 or missing data.
    """
    _update_job(job_id, status=JobStatus.tagging, started_at=datetime.now(timezone.utc),
                celery_task_id=getattr(getattr(self, 'request', None), 'id', None),
                pipeline_steps=[])
    _append_job_log(job_id, f"Metadata refresh start (video_id={video_id}, force={force})")

    db = SessionLocal()
    try:
        video_item = db.query(VideoItem).get(video_id)
        if not video_item:
            _update_job(job_id, status=JobStatus.failed, error_message="Video not found")
            return

        artist = video_item.artist or "Unknown Artist"
        title = video_item.title or "Unknown Title"
        _update_job(job_id, display_name=f"{artist} \u2013 {title} \u203a Metadata Refresh",
                    current_step="Resolving artist", progress_percent=10)

        # --- Resolve artist entity ---
        resolved_artist = resolve_artist(
            artist, mb_artist_id=video_item.mb_artist_id,
        )
        artist_entity = get_or_create_artist(db, artist, resolved_artist)
        if artist_entity:
            save_revision(db, "artist", artist_entity.id, "metadata_refresh", "resolver")
            download_entity_assets("artist", artist_entity.id,
                                   resolved_artist.get("assets", {}))
        _set_pipeline_step(job_id, "entity_resolve", "success")
        _append_job_log(job_id, f"Artist entity: {artist_entity.canonical_name if artist_entity else '?'}")

        # --- Resolve album entity ---
        _update_job(job_id, current_step="Resolving album", progress_percent=30)
        album_entity = None
        if video_item.album:
            resolved_album = resolve_album(
                artist, video_item.album,
            )
            album_entity = get_or_create_album(db, artist_entity, video_item.album, resolved_album)
            if album_entity:
                save_revision(db, "album", album_entity.id, "metadata_refresh", "resolver")
                download_entity_assets("album", album_entity.id,
                                       resolved_album.get("assets", {}))
            _append_job_log(job_id, f"Album entity: {album_entity.title if album_entity else 'n/a'}")

        # --- Resolve track entity ---
        _update_job(job_id, current_step="Resolving track", progress_percent=50)
        resolved_track = resolve_track(
            artist, title,
            mb_recording_id=video_item.mb_recording_id,
        )
        track_entity = get_or_create_track(db, artist_entity, album_entity, title, resolved_track)

        # --- Link entities to video ---
        _old_artist_eid = video_item.artist_entity_id
        _old_album_eid = video_item.album_entity_id
        _old_track_id = video_item.track_id
        if artist_entity:
            video_item.artist_entity_id = artist_entity.id
        if album_entity:
            video_item.album_entity_id = album_entity.id
        if track_entity:
            video_item.track_id = track_entity.id

        # --- Kodi re-export ---
        _update_job(job_id, current_step="Kodi re-export", progress_percent=70)
        try:
            source_url = video_item.sources[0].canonical_url if video_item.sources else ""
            genres = [g.name for g in video_item.genres]
            if artist_entity:
                export_artist(db, artist_entity)
            if album_entity:
                export_album(db, album_entity)
            export_video_kodi(
                db, video_item.id, artist=artist, title=title,
                album=video_item.album or "", year=video_item.year,
                genres=genres, plot=video_item.plot or "",
                source_url=source_url, folder_path=video_item.folder_path or "",
                resolution_label=video_item.resolution_label or "",
            )
            _set_pipeline_step(job_id, "kodi_export", "success")
        except Exception as e:
            _append_job_log(job_id, f"Kodi export error: {e}")
            _set_pipeline_step(job_id, "kodi_export", "failed")

        db.commit()

        # Clean up orphaned entities if entity links changed during refresh
        try:
            from app.routers.library import cleanup_orphaned_entity
            if _old_artist_eid and _old_artist_eid != video_item.artist_entity_id:
                cleanup_orphaned_entity(db, "artist", _old_artist_eid)
            if _old_album_eid and _old_album_eid != video_item.album_entity_id:
                cleanup_orphaned_entity(db, "album", _old_album_eid)
            if _old_track_id and _old_track_id != video_item.track_id:
                cleanup_orphaned_entity(db, "track", _old_track_id)
            db.commit()
        except Exception as _orphan_exc:
            db.rollback()
            logger.warning(f"[Job {job_id}] Orphan entity cleanup warning: {_orphan_exc}")

        _update_job(job_id, status=JobStatus.complete, progress_percent=100,
                    current_step="Complete", completed_at=datetime.now(timezone.utc))
        _append_job_log(job_id, "Metadata refresh complete")

    except Exception as e:
        db.rollback()
        logger.error(f"Metadata refresh failed: {e}\n{traceback.format_exc()}")
        _update_job(job_id, status=JobStatus.failed, error_message=str(e),
                    completed_at=datetime.now(timezone.utc))
        _append_job_log(job_id, f"ERROR: {e}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Full Kodi export task
# ---------------------------------------------------------------------------

@celery_app.task(bind=True)
def kodi_export_task(self, job_id: int):
    """Re-export all Kodi NFO + artwork from the canonical metadata store."""
    _update_job(job_id, status=JobStatus.analyzing, started_at=datetime.now(timezone.utc),
                celery_task_id=getattr(getattr(self, 'request', None), 'id', None))
    _append_job_log(job_id, "Starting full Kodi export")

    db = SessionLocal()
    try:
        _update_job(job_id, current_step="Exporting all entities", progress_percent=10)
        counts = export_all_kodi()
        _append_job_log(job_id, f"Exported: {counts.get('artists', 0)} artists, "
                        f"{counts.get('albums', 0)} albums, {counts.get('videos', 0)} videos")

        # Clean stale exports
        _update_job(job_id, current_step="Cleaning stale exports", progress_percent=90)
        try:
            clean_stale_exports(db, "kodi")
            _append_job_log(job_id, "Stale exports cleaned")
        except Exception as e:
            _append_job_log(job_id, f"Stale cleanup warning: {e}")

        db.commit()
        _update_job(job_id, status=JobStatus.complete, progress_percent=100,
                    current_step="Complete", completed_at=datetime.now(timezone.utc))
        _append_job_log(job_id, "Full Kodi export complete")

    except Exception as e:
        db.rollback()
        logger.error(f"Kodi export failed: {e}")
        _update_job(job_id, status=JobStatus.failed, error_message=str(e),
                    completed_at=datetime.now(timezone.utc))
        _append_job_log(job_id, f"ERROR: {e}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Batch matching / resolve task
# ---------------------------------------------------------------------------

@celery_app.task(bind=True)
def batch_resolve_task(self, job_id: int, video_ids: list, force: bool = False):
    """Batch-resolve matching scores for multiple videos."""

    _update_job(job_id, status=JobStatus.analyzing, started_at=datetime.now(timezone.utc),
                celery_task_id=getattr(getattr(self, 'request', None), 'id', None),
                current_step="Batch resolve", progress_percent=5)
    _append_job_log(job_id, f"Starting batch resolve for {len(video_ids)} videos")

    db = SessionLocal()
    try:
        total = len(video_ids)
        changed = 0
        for idx, vid in enumerate(video_ids, 1):
            try:
                result = matching_resolve_video(db, vid, force=force)
                db.commit()
                if result.changed:
                    changed += 1
            except Exception as e:
                db.rollback()
                _append_job_log(job_id, f"Video {vid}: resolve error: {e}")

            pct = int(5 + (idx / total) * 90)
            _update_job(job_id, progress_percent=pct,
                        current_step=f"Resolved {idx}/{total}")

        _update_job(job_id, status=JobStatus.complete, progress_percent=100,
                    current_step="Complete", completed_at=datetime.now(timezone.utc))
        _append_job_log(job_id, f"Batch resolve complete: {total} processed, {changed} updated")

    except Exception as e:
        db.rollback()
        logger.error(f"Batch resolve failed: {e}")
        _update_job(job_id, status=JobStatus.failed, error_message=str(e),
                    completed_at=datetime.now(timezone.utc))
        _append_job_log(job_id, f"ERROR: {e}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Library Import tasks
# ---------------------------------------------------------------------------

@celery_app.task(bind=True)
def library_import_task(self, job_id: int):
    """
    Orchestrate a library import: read options from the parent job, create
    child jobs for each video file, and wait for them to complete.
    """
    _update_job(job_id, status=JobStatus.analyzing, started_at=datetime.now(timezone.utc),
                celery_task_id=getattr(getattr(self, 'request', None), 'id', None),
                current_step="Preparing import", progress_percent=5,
                pipeline_steps=[])
    _append_job_log(job_id, "Library import task started")

    db = SessionLocal()
    try:
        parent = db.query(ProcessingJob).get(job_id)
        if not parent or not parent.input_params:
            _update_job(job_id, status=JobStatus.failed, error_message="Missing import configuration")
            return

        params = parent.input_params
        file_paths = params.get("file_paths", [])
        options = params.get("options", {})

        if not file_paths:
            _update_job(job_id, status=JobStatus.failed, error_message="No files to import")
            return

        _append_job_log(job_id, f"Importing {len(file_paths)} videos (mode={options.get('mode', 'simple')})")

        dup_actions = params.get("duplicate_actions", {})

        # Create child jobs for each video file
        sub_job_ids = []
        for fp in file_paths:
            # Attach per-file duplicate action if user provided one
            child_params = {
                "file_path": fp,
                "directory": params.get("directory", ""),
                "options": options,
            }
            if fp in dup_actions:
                child_params["duplicate_action"] = dup_actions[fp]

            child = ProcessingJob(
                job_type="library_import_video",
                status=JobStatus.queued,
                display_name=os.path.basename(fp),
                action_label="Library Import",
                input_params=child_params,
            )
            db.add(child)
            db.flush()
            sub_job_ids.append(child.id)

        # Store sub-job IDs on parent
        from sqlalchemy.orm.attributes import flag_modified
        parent.input_params = {**params, "sub_job_ids": sub_job_ids}
        flag_modified(parent, "input_params")
        db.commit()

        _append_job_log(job_id, f"Created {len(sub_job_ids)} child jobs")

        # Dispatch child tasks â€” Celery workers handle concurrency natively.
        # In thread mode, use a ThreadPoolExecutor so children can overlap
        # their I/O-heavy phases (ffprobe, file copy, metadata resolution)
        # while the pipeline lock serialises only the short DB-write phases.
        from app.worker import _use_celery
        if _use_celery:
            for child_id in sub_job_ids:
                dispatch_task(library_import_video_task, job_id=child_id)
            # Dispatch the batch watcher
            dispatch_task(complete_batch_job_task, parent_job_id=job_id, sub_job_ids=sub_job_ids)
        else:
            # Parallel execution via ThreadPoolExecutor
            # SQLite supports only ONE writer at a time. Keep concurrency
            # low to avoid excessive write-lock contention between child
            # pipelines, the batch watcher, and deferred tasks.
            def _run_children():
                from concurrent.futures import ThreadPoolExecutor, as_completed
                max_workers = min(len(sub_job_ids), 3)
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    futures = {
                        pool.submit(library_import_video_task.run, job_id=child_id): child_id
                        for child_id in sub_job_ids
                    }
                    for future in as_completed(futures):
                        child_id = futures[future]
                        try:
                            future.result()
                        except Exception as exc:
                            logger.error(f"Child import {child_id} failed: {exc}", exc_info=True)

            def _run_watcher():
                try:
                    complete_batch_job_task.run(parent_job_id=job_id, sub_job_ids=sub_job_ids)
                except Exception as exc:
                    logger.error(f"Batch completion failed: {exc}", exc_info=True)

            import threading as _thr
            # Start workers and batch watcher concurrently so the parent
            # job's progress_percent and current_step update in real-time
            # as children complete (the watcher polls every 3 s).
            _thr.Thread(target=_run_children, daemon=True).start()
            _thr.Thread(target=_run_watcher, daemon=True).start()

    except Exception as e:
        db.rollback()
        logger.error(f"Library import failed: {e}", exc_info=True)
        _update_job(job_id, status=JobStatus.failed, error_message=str(e),
                    completed_at=datetime.now(timezone.utc))
        _append_job_log(job_id, f"ERROR: {e}")
    finally:
        db.close()


@celery_app.task(bind=True)
def library_import_video_task(self, job_id: int):
    """
    Import a single video file from an external library.
    Delegates to the staged pipeline (workspace -> mutation plan -> serial apply).
    """
    from app.pipeline_lib.stages import run_library_import_pipeline

    _update_job(job_id, status=JobStatus.analyzing, started_at=datetime.now(timezone.utc),
                celery_task_id=getattr(getattr(self, "request", None), "id", None))

    run_library_import_pipeline(job_id)
