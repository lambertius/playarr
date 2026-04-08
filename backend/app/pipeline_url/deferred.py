# AUTO-SEPARATED from pipeline/deferred.py for pipeline_url pipeline
# This file is independent â€” changes here do NOT affect the other pipeline.
"""
Stage D â€” deferred enrichment tasks.

These tasks run AFTER the core import (Stage C) completes.
They may touch the DB but are individually short-lived and
use their own sessions.  Failures here are non-fatal.

Architecture
============
- Per-video coordinator thread with internal ThreadPoolExecutor.
- I/O-heavy work (image downloads, ffmpeg, AI calls) runs in parallel.
- ALL DB writes are funnelled through the centralised write queue
  (``write_queue.db_write`` / ``db_write_soon``), making SQLite
  contention impossible by construction.
- ws.log() writes ONLY to files â€” no DB contention from logging.
"""
import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from typing import List

from app.pipeline_url.workspace import ImportWorkspace
from app.pipeline_url.write_queue import db_write, db_write_soon
from app.worker import GLOBAL_DEFERRED_SLOTS

logger = logging.getLogger(__name__)

_MAX_DB_RETRIES = 7


def _retry_delay(attempt: int) -> float:
    """Exponential backoff with jitter to de-synchronise retries."""
    base = 2 ** attempt
    return base + random.uniform(0, base * 0.5)


# Max wall-clock time (seconds) for all Phase 2 deferred tasks.  If any
# task hangs beyond this, the coordinator logs a timeout and proceeds to
# finalise the job â€” preventing permanent "Finalizing" UI hangs.
_DEFERRED_TIMEOUT = 300

# Global semaphore: limits concurrent deferred-task threads across ALL
# pipeline types.  Imported from worker.py so pipeline_url, pipeline_lib,
# and pipeline share a single pool â€” prevents SQLite write storms when
# multiple individual downloads overlap in their deferred phases.
# (was per-module Semaphore(6); now shared Semaphore(3) in worker.py)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  PUBLIC API
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def dispatch_deferred(video_id: int, tasks: List[str], ws: ImportWorkspace,
                      update_job_progress: bool = True) -> None:
    """Execute deferred tasks with parallel I/O, serialised DB writes.

    All I/O (image downloads, preview generation, AI calls) runs in
    parallel via a per-video ThreadPoolExecutor.  ALL DB writes are
    funnelled through the centralised ``write_queue``, making SQLite
    contention impossible by construction.

    ``ws.log()`` writes to files only â€” the coordinator syncs the
    accumulated log text to the DB once at the end, eliminating
    log-write contention entirely.
    """
    if not tasks:
        ws.log("No deferred tasks")
        ws.sync_logs_to_db()
        ws.cleanup_on_success()
        return

    ws.log(f"Dispatching deferred tasks: {tasks}")

    # Human-readable labels for deferred task names
    _TASK_LABELS = {
        "ai_enrichment": "AI enrichment",
        "preview": "Generating preview",
        "scene_analysis": "Analyzing scenes",
        "entity_artwork": "Fetching artwork",
        "kodi_export": "Exporting metadata",
        "matching": "Finding matches",
        "orphan_cleanup": "Cleanup",
    }

    # Tasks that must complete BEFORE entity_artwork / kodi_export run,
    # because they can reassign entity IDs (e.g. AI correction).
    _PHASE1_TASKS = {"ai_enrichment"}

    total_tasks = len(tasks)
    _update_progress = update_job_progress  # capture for closure

    def _coordinator():
        completed_tasks = []
        failed_tasks = []
        try:
            phase1 = [t for t in tasks if t in _PHASE1_TASKS]
            phase2 = [t for t in tasks if t not in _PHASE1_TASKS]

            def _bump_progress():
                done = len(completed_tasks) + len(failed_tasks)
                pct = 90 + int(10 * done / total_tasks) if total_tasks else 100
                return min(pct, 99)  # reserve 100 for truly complete

            # Phase 1: run entity-mutating tasks first (serialised)
            for task_name in phase1:
                fn = _DISPATCH.get(task_name)
                if fn:
                    label = _TASK_LABELS.get(task_name, task_name)
                    if _update_progress:
                        _update_child_step(ws.job_id, label, progress=_bump_progress())
                    try:
                        _run_safe(fn, video_id, ws, task_name)
                        completed_tasks.append(task_name)
                    except Exception:
                        failed_tasks.append(task_name)

            # Phase 2: remaining tasks in parallel
            if _update_progress:
                _update_child_step(ws.job_id, "Finalizing", progress=_bump_progress())
            pool = ThreadPoolExecutor(
                max_workers=4,
                thread_name_prefix=f"def-{video_id}",
            )
            futures = {}
            for task_name in phase2:
                fn = _DISPATCH.get(task_name)
                if fn:
                    futures[pool.submit(_run_safe, fn, video_id, ws, task_name)] = task_name
                else:
                    ws.log(f"Unknown deferred task: {task_name}", level="warning")
            try:
                for future in as_completed(futures, timeout=_DEFERRED_TIMEOUT):
                    task_name = futures[future]
                    try:
                        future.result()
                        completed_tasks.append(task_name)
                    except Exception:
                        failed_tasks.append(task_name)
                    if _update_progress:
                        _update_child_step(ws.job_id, "Finalizing", progress=_bump_progress())
            except FuturesTimeoutError:
                timed_out = [n for f, n in futures.items() if not f.done()]
                for n in timed_out:
                    ws.log(f"Deferred '{n}' timed out after {_DEFERRED_TIMEOUT}s", level="error")
                    failed_tasks.append(n)
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
        finally:
            # Rewrite XML sidecar after ALL deferred tasks so that
            # scene_analysis, entity cached artwork, and processing-state
            # flags (scenes_analyzed, thumbnail_selected) are persisted to
            # disk.  Without this, a library clear + re-scan loses the data
            # because the original XML was written before deferred tasks ran.
            try:
                from app.database import SessionLocal as _FinalXMLSession
                from app.models import VideoItem as _FinalXMLVideo
                from app.services.playarr_xml import write_playarr_xml as _final_write_xml
                _fdb = _FinalXMLSession()
                try:
                    _fv = _fdb.query(_FinalXMLVideo).get(video_id)
                    if _fv and _fv.folder_path and os.path.isdir(_fv.folder_path):
                        _final_write_xml(_fv, _fdb)

                    # Auto-clear review flags when the underlying issue
                    # has been resolved by the deferred tasks that just ran.
                    if _fv and _fv.review_status == "needs_human_review":
                        _rc = _fv.review_category
                        _ps = _fv.processing_state or {}
                        _flag_ok = lambda s: _ps.get(s, {}).get("completed", False)
                        _rr = _fv.review_reason or ""
                        _clear = False
                        if _rc in ("ai_partial", "ai_pending"):
                            _need_ai = "AI metadata" in _rr
                            _need_scenes = "scene analysis" in _rr
                            _clear = (not _need_ai or _flag_ok("ai_enriched")) and (not _need_scenes or _flag_ok("scenes_analyzed"))
                            if not (_need_ai or _need_scenes):
                                _clear = _flag_ok("ai_enriched")
                        elif _rc == "normalization":
                            _clear = _flag_ok("audio_normalized")
                        elif _rc == "scanned":
                            _clear = _flag_ok("metadata_scraped") or _flag_ok("metadata_resolved")
                        if _clear:
                            _fv.review_status = "none"
                            _fv.review_reason = None
                            _fv.review_category = None
                            _fdb.commit()
                            ws.log("Review flag cleared — underlying issue resolved")
                finally:
                    _fdb.close()
            except Exception as _xml_exc:
                logger.warning(f"Deferred XML sidecar rewrite failed for video {video_id}: {_xml_exc}")

            try:
                if _update_progress:
                    _update_child_step(ws.job_id, "Import complete", progress=100)
            except Exception as e:
                logger.error(f"Failed to set 'Import complete' for job {ws.job_id}: {e}")
            try:
                ws.sync_logs_to_db()
            except Exception as e:
                logger.error(f"Failed to sync logs for job {ws.job_id}: {e}")
            try:
                ws.cleanup_on_success()
            except Exception as e:
                logger.error(f"Failed cleanup for job {ws.job_id}: {e}")

    threading.Thread(
        target=_coordinator, daemon=True, name=f"deferred-{video_id}",
    ).start()


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  HELPERS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _update_child_step(job_id: int, step: str = None, progress: int = None,
                       display_name: str = None, _max_retries: int = 7):
    """Update a child job's current_step via the write queue (fire-and-forget)."""
    from app.database import CosmeticSessionLocal
    from app.models import ProcessingJob

    _step = step
    _progress = progress
    _display_name = display_name

    def _write():
        db = CosmeticSessionLocal()
        try:
            job = db.query(ProcessingJob).get(job_id)
            if job:
                if _step is not None:
                    job.current_step = _step
                if _progress is not None:
                    job.progress_percent = _progress
                if _display_name is not None:
                    job.display_name = _display_name
                db.commit()
        finally:
            db.close()

    # "Import complete" is the final step â€” block to guarantee it lands
    if step == "Import complete":
        try:
            db_write(_write)
        except Exception as e:
            logger.error(f"_update_child_step FAILED for job {job_id} (step={step}): {e}")
    else:
        db_write_soon(_write)


def _run_safe(fn, video_id: int, ws: ImportWorkspace, task_name: str):
    """Run a deferred task with error handling, gated by global semaphore."""
    GLOBAL_DEFERRED_SLOTS.acquire()
    try:
        fn(video_id, ws)
        try:
            ws.log(f"Deferred '{task_name}' completed")
        except Exception:
            pass
    except Exception as e:
        logger.error(
            f"Deferred task '{task_name}' failed for video {video_id}: {e}",
            exc_info=True,
        )
        try:
            ws.log(f"Deferred '{task_name}' FAILED: {e}", level="error")
        except Exception as log_err:
            logger.error(f"Failed to log task failure to workspace: {log_err}")
    finally:
        GLOBAL_DEFERRED_SLOTS.release()


def _mark_processing_state(db, video_id: int, step: str, *, method: str = "deferred"):
    """Set a processing_state flag on a VideoItem (caller must commit)."""
    from sqlalchemy.orm.attributes import flag_modified
    from app.models import VideoItem
    item = db.query(VideoItem).get(video_id)
    if not item:
        return
    state = dict(item.processing_state or {})
    state[step] = {
        "completed": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "version": "1.0",
    }
    item.processing_state = state
    flag_modified(item, "processing_state")


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  DEFERRED TASK IMPLEMENTATIONS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _deferred_preview(video_id: int, ws: ImportWorkspace) -> None:
    """Generate video preview clip."""
    from app.database import SessionLocal
    from app.models import VideoItem
    from app.pipeline_url.services.preview_generator import generate_preview

    db = SessionLocal()
    try:
        item = db.query(VideoItem).get(video_id)
        if not item or not item.file_path:
            return
        file_path = item.file_path
    finally:
        db.close()

    generate_preview(file_path, video_id=video_id)


def _deferred_scene_analysis(video_id: int, ws: ImportWorkspace) -> None:
    """Run scene analysis (ffmpeg I/O in caller thread, DB write via queue)."""
    from app.database import SessionLocal

    def _write():
        db = SessionLocal()
        try:
            from app.pipeline_url.ai.scene_analysis import analyze_scenes
            analyze_scenes(db, video_id)
            _mark_processing_state(db, video_id, "scenes_analyzed", method="scene_analysis")
            _mark_processing_state(db, video_id, "thumbnail_selected", method="scene_analysis")
            db.commit()

            # Persist scene thumbnails to the video folder so they survive
            # a library clear + re-scan cycle (fallback discovery).
            try:
                import os as _os
                from app.models import VideoItem
                from app.ai.models import AIThumbnail
                import shutil as _shutil

                video = db.query(VideoItem).get(video_id)
                if video and video.folder_path and _os.path.isdir(video.folder_path):
                    thumbs = db.query(AIThumbnail).filter(
                        AIThumbnail.video_id == video_id,
                    ).all()
                    for t in thumbs:
                        if t.file_path and _os.path.isfile(t.file_path):
                            dest = _os.path.join(video.folder_path, _os.path.basename(t.file_path))
                            if not _os.path.isfile(dest):
                                _shutil.copy2(t.file_path, dest)
            except Exception as exc:
                ws.log(f"Scene analysis persist-to-disk: {exc}", level="warning")
        finally:
            db.close()

    try:
        db_write(_write)
    except ImportError:
        pass
    except Exception as e:
        ws.log(f"Scene analysis: {e}", level="warning")


def _deferred_kodi_export(video_id: int, ws: ImportWorkspace) -> None:
    """Export Kodi-format NFO/artwork for video and its entities."""
    from app.database import SessionLocal
    from app.models import VideoItem

    db = SessionLocal()
    try:
        item = db.query(VideoItem).get(video_id)
        if not item:
            return

        from app.metadata.exporters.kodi import (
            export_video, export_artist, export_album,
        )

        try:
            # Build source_url from the primary video source
            _source_url = ""
            try:
                from app.models import Source
                _primary_src = (
                    db.query(Source)
                    .filter(Source.video_id == video_id, Source.source_type == "video")
                    .first()
                )
                if _primary_src:
                    _source_url = _primary_src.canonical_url or _primary_src.original_url or ""
            except Exception:
                pass

            export_video(
                db, video_id, item.artist or "", item.title or "",
                album=item.album or "",
                year=item.year,
                genres=[g.name for g in item.genres] if item.genres else [],
                plot=item.plot or "",
                source_url=_source_url,
                folder_path=item.folder_path,
                resolution_label=item.resolution_label or "",
            )
        except Exception as e:
            ws.log(f"Kodi video export: {e}", level="warning")

        if item.artist_entity:
            try:
                export_artist(db, item.artist_entity)
            except Exception:
                pass
        if item.album_entity:
            try:
                export_album(db, item.album_entity)
            except Exception:
                pass
    finally:
        db.close()


def _deferred_entity_artwork(video_id: int, ws: ImportWorkspace) -> None:
    """Download entity artwork via the full artwork pipeline + resolver assets."""
    import os
    from datetime import datetime, timezone
    from app.database import SessionLocal
    from app.models import VideoItem, MediaAsset

    db = SessionLocal()
    try:
        item = db.query(VideoItem).get(video_id)
        if not item:
            return

        # Refresh to pick up any entity reassignments from ai_enrichment
        # (which runs in Phase 1 before this task).
        db.refresh(item)

        # â”€â”€ 1. Download resolver asset candidates from workspace â”€â”€â”€â”€â”€
        entity_res = ws.read_artifact("entity_resolution") or {}
        from app.pipeline_url.metadata.assets import download_entity_assets
        from app.metadata.providers.base import AssetCandidate

        def _download_assets(entity_type, entity_id, resolved_data):
            assets_raw = (resolved_data or {}).get("assets") or {}
            if not assets_raw or not isinstance(assets_raw, dict):
                return
            candidates = {}
            for kind, info in assets_raw.items():
                url = info.get("url") if isinstance(info, dict) else str(info)
                if url:
                    candidates[kind] = AssetCandidate(
                        url=url,
                        kind=info.get("kind", kind) if isinstance(info, dict) else kind,
                    )
            if candidates:
                download_entity_assets(entity_type, entity_id, candidates)
                ws.log(f"Downloaded {len(candidates)} {entity_type} asset(s)")

        # Guard: skip Phase 1 resolver assets if AI enrichment changed the
        # artist/album identity â€” the workspace artifact URLs would belong
        # to the OLD entity and must NOT be saved to the NEW entity's cache.
        _res_artist_name = (entity_res.get("artist") or {}).get("name", "")
        _res_album_title = (entity_res.get("album") or {}).get("title", "")

        if item.artist_entity:
            _current_artist = item.artist_entity.canonical_name or ""
            if _res_artist_name and _current_artist.lower() != _res_artist_name.lower():
                ws.log(
                    f"Skipping stale resolver assets: artist changed "
                    f"'{_res_artist_name}' â†’ '{_current_artist}'"
                )
            else:
                resolved_artist = entity_res.get("artist", {}).get("resolved") or {}
                try:
                    _download_assets("artist", item.artist_entity.id, resolved_artist)
                except Exception as e:
                    ws.log(f"Artist asset download: {e}", level="warning")

        if item.album_entity:
            _current_album = item.album_entity.title or ""
            if _res_album_title and _current_album.lower() != _res_album_title.lower():
                ws.log(
                    f"Skipping stale resolver assets: album changed "
                    f"'{_res_album_title}' â†’ '{_current_album}'"
                )
            else:
                resolved_album = entity_res.get("album", {}).get("resolved") or {}
                try:
                    _download_assets("album", item.album_entity.id, resolved_album)
                except Exception as e:
                    ws.log(f"Album asset download: {e}", level="warning")

        # â”€â”€ 2. Full artwork pipeline (CAA, Wikipedia, MB scraper) â”€â”€â”€â”€
        metadata = ws.read_artifact("scraper_results") or {}
        from app.scraper.source_validation import parse_multi_artist
        # Prefer the artist entity's canonical name â€” parse_multi_artist
        # incorrectly splits band names containing "&" (e.g. "Iron & Wine"
        # becomes "Iron").
        if item.artist_entity and item.artist_entity.canonical_name:
            primary_artist = item.artist_entity.canonical_name
        else:
            primary_artist, _ = parse_multi_artist(item.artist or "")
        album_name = metadata.get("album") or item.album

        # â”€â”€ 1c. Complete incomplete MB resolution â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # When the initial search partially succeeded (has mb_recording_id
        # but missing mb_release_group_id), re-search to fill in the gaps.
        # This happens when Strategy 1 (_search_single_release_group) failed
        # and the recording fallback returned truncated results without the
        # release-group ID or parent album.
        if item.mb_recording_id and not item.mb_release_group_id:
            try:
                from app.scraper.metadata_resolver import (
                    search_musicbrainz as _mb_complete,
                    _normalize_for_compare as _nfc_c,
                    _tokens_overlap as _to_c,
                )
                from app.scraper.source_validation import parse_multi_artist as _pma_c
                _mb_c = _mb_complete(item.artist or "", item.title or "")
                if _mb_c.get("mb_recording_id") and _mb_c.get("mb_release_group_id"):
                    _primary_c, _ = _pma_c(item.artist or "")
                    _mb_n = _nfc_c(_mb_c["artist"]) if _mb_c.get("artist") else ""
                    _exp_n = _nfc_c(item.artist or "")
                    _pri_n = _nfc_c(_primary_c) if _primary_c else ""
                    if (_mb_n == _exp_n or _mb_n == _pri_n
                            or _to_c(_mb_c.get("artist", ""), item.artist or "", 0.4)
                            or (_primary_c and _to_c(_mb_c.get("artist", ""), _primary_c, 0.4))):
                        item.mb_release_group_id = _mb_c["mb_release_group_id"]
                        if _mb_c.get("album") and not item.album:
                            item.album = _mb_c["album"]
                            album_name = item.album
                        # Create or correct album entity
                        if _mb_c.get("album") and item.artist_entity:
                            from app.pipeline_url.metadata.resolver import get_or_create_album as _goca_c
                            _alb_resolved = {
                                "mb_release_id": _mb_c.get("mb_album_release_id"),
                                "mb_release_group_id": _mb_c.get("mb_album_release_group_id"),
                                "year": _mb_c.get("year"),
                            }
                            _alb_ent = _goca_c(db, item.artist_entity, _mb_c["album"],
                                               resolved=_alb_resolved)
                            if not item.album_entity or item.album_entity_id != _alb_ent.id:
                                item.album_entity_id = _alb_ent.id
                            # Correct album entity when it has the single's IDs
                            # instead of the parent album's IDs
                            _a_rg = _mb_c.get("mb_album_release_group_id")
                            _a_ri = _mb_c.get("mb_album_release_id")
                            if _a_rg and _alb_ent.mb_release_group_id != _a_rg:
                                _alb_ent.mb_release_group_id = _a_rg
                            if _a_ri and _alb_ent.mb_release_id != _a_ri:
                                _alb_ent.mb_release_id = _a_ri
                            db_write(lambda: db.commit())
                            ws.log(f"MB completion: album entity "
                                   f"'{_mb_c['album']}' (id={_alb_ent.id})")
                        db_write(lambda: db.commit())
                        ws.log(f"MB completion: filled mb_release_group_id="
                               f"{_mb_c['mb_release_group_id']}, "
                               f"album={_mb_c.get('album')}")
            except Exception as e:
                ws.log(f"MB completion: {e}", level="warning")

        # After 1c may have changed album_entity_id, expire the cached
        # relationship so 1d/1e/poster see the correct album entity.
        db.expire(item, ["album_entity"])

        # â”€â”€ 1d. Correct album entity that inherited the single's IDs â”€â”€
        # When the album entity's mb_release_group_id equals the track's
        # (single) mb_release_group_id, the entity was created from the
        # single's IDs instead of the parent album's.  Re-search MB to
        # obtain the correct album release-group and fix the entity.
        if (item.mb_release_group_id and item.album_entity
                and item.album_entity.mb_release_group_id
                and item.album_entity.mb_release_group_id == item.mb_release_group_id):
            try:
                from app.scraper.metadata_resolver import search_musicbrainz as _mb_1d
                _mb_1d_r = _mb_1d(item.artist or "", item.title or "")
                _a_rg_1d = _mb_1d_r.get("mb_album_release_group_id")
                _a_ri_1d = _mb_1d_r.get("mb_album_release_id")
                if _a_rg_1d and _a_rg_1d != item.mb_release_group_id:
                    _alb = item.album_entity
                    _alb.mb_release_group_id = _a_rg_1d
                    if _a_ri_1d:
                        _alb.mb_release_id = _a_ri_1d
                    db_write(lambda: db.commit())
                    ws.log(f"Album entity id={_alb.id} corrected: "
                           f"rg={_a_rg_1d}, release={_a_ri_1d}")
            except Exception as e:
                ws.log(f"Album entity correction: {e}", level="warning")

        # Propagate mb_artist_id to artist entity when missing
        if item.mb_artist_id and item.artist_entity and not item.artist_entity.mb_artist_id:
            item.artist_entity.mb_artist_id = item.mb_artist_id
            try:
                db_write(lambda: db.commit())
                ws.log(f"Propagated mb_artist_id to artist entity: {item.mb_artist_id}")
            except Exception:
                pass

        # â”€â”€ 1dâ€². Fill missing album entity mb_release_group_id â”€â”€â”€â”€â”€â”€
        # When the album entity exists but has no mb_release_group_id
        # (e.g. resolve_album didn't return it, or the musicbrainz:album
        # source was lost to a transient DB error), re-search MusicBrainz
        # and populate the album entity + ensure the source exists.
        if (item.mb_recording_id and item.album_entity
                and not item.album_entity.mb_release_group_id):
            try:
                from app.scraper.metadata_resolver import search_musicbrainz as _mb_1dp
                _mb_r = _mb_1dp(item.artist or "", item.title or "")
                _a_rg = _mb_r.get("mb_album_release_group_id")
                _a_ri = _mb_r.get("mb_album_release_id")
                if _a_rg:
                    _alb = item.album_entity
                    _alb.mb_release_group_id = _a_rg
                    if _a_ri and not _alb.mb_release_id:
                        _alb.mb_release_id = _a_ri
                    db_write(lambda: db.commit())
                    ws.log(f"1dâ€²: album entity id={_alb.id} filled: "
                           f"rg={_a_rg}, release={_a_ri}")
            except Exception as e:
                ws.log(f"1dâ€² album RG fill: {e}", level="warning")

        # â”€â”€ 1e. Ensure MB single/album Source records exist â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # _re_resolve_sources runs BEFORE 1c/1d, so when those blocks
        # fill mb_release_group_id the corresponding Source records are
        # never created.  Backfill them here, correcting source_type
        # if the RG was previously mis-categorised, and enforcing a
        # hard limit of one MB release-group source per category.
        try:
            from app.models import Source as _Src1e
            _single_rg = getattr(item, "mb_release_group_id", None)
            _album_rg = (getattr(item.album_entity, "mb_release_group_id", None)
                         if item.album_entity else None)

            # -- upsert single source
            if _single_rg:
                _exists = db.query(_Src1e).filter(
                    _Src1e.video_id == item.id,
                    _Src1e.provider == "musicbrainz",
                    _Src1e.source_video_id == _single_rg,
                ).first()
                if _exists:
                    if _exists.source_type != "single":
                        ws.log(f"1e: corrected MB source {_single_rg} from {_exists.source_type}â†’single")
                        _exists.source_type = "single"
                else:
                    db.add(_Src1e(
                        video_id=item.id, provider="musicbrainz",
                        source_video_id=_single_rg,
                        original_url=f"https://musicbrainz.org/release-group/{_single_rg}",
                        canonical_url=f"https://musicbrainz.org/release-group/{_single_rg}",
                        source_type="single", provenance="scraped",
                    ))
                    ws.log(f"1e: created MB single source rg={_single_rg}")

            # -- upsert album source (skip if same RG as single)
            if _album_rg and _album_rg != _single_rg:
                _exists_a = db.query(_Src1e).filter(
                    _Src1e.video_id == item.id,
                    _Src1e.provider == "musicbrainz",
                    _Src1e.source_video_id == _album_rg,
                ).first()
                if _exists_a:
                    if _exists_a.source_type != "album":
                        ws.log(f"1e: corrected MB source {_album_rg} from {_exists_a.source_type}â†’album")
                        _exists_a.source_type = "album"
                else:
                    db.add(_Src1e(
                        video_id=item.id, provider="musicbrainz",
                        source_video_id=_album_rg,
                        original_url=f"https://musicbrainz.org/release-group/{_album_rg}",
                        canonical_url=f"https://musicbrainz.org/release-group/{_album_rg}",
                        source_type="album", provenance="scraped",
                    ))
                    ws.log(f"1e: created MB album source rg={_album_rg}")

            # -- enforce max-one MB release-group source per category
            _valid_rg = {_single_rg, _album_rg} - {None}
            _extras = db.query(_Src1e).filter(
                _Src1e.video_id == item.id,
                _Src1e.provider == "musicbrainz",
                _Src1e.source_type.in_(["single", "album"]),
                ~_Src1e.source_video_id.in_(_valid_rg) if _valid_rg else _Src1e.id > 0,
            ).all()
            for _ex in _extras:
                ws.log(f"1e: removing stale MB {_ex.source_type} source {_ex.source_video_id}")
                db.delete(_ex)

            db_write(lambda: db.commit())
        except Exception as e:
            ws.log(f"1e source backfill: {e}", level="warning")

        mb_album_release_id = metadata.get("mb_album_release_id")
        mb_album_release_group_id = metadata.get("mb_album_release_group_id")

        # Eagerly pull IDs from the album entity so process_artist_album_artwork
        # uses the album's own release, not the video's single release.
        if not mb_album_release_id and item.album_entity:
            mb_album_release_id = getattr(item.album_entity, "mb_release_id", None)
        if not mb_album_release_group_id and item.album_entity:
            mb_album_release_group_id = getattr(item.album_entity, "mb_release_group_id", None)

        _wiki_album_url = (metadata.get("_source_urls") or {}).get("wikipedia_album")

        art_result = {}
        try:
            from app.pipeline_url.services.artwork_manager import process_artist_album_artwork
            art_result = process_artist_album_artwork(
                artist=primary_artist,
                album=album_name,
                mb_artist_id=item.mb_artist_id,
                mb_release_id=item.mb_release_id,
                mb_album_release_id=mb_album_release_id,
                mb_album_release_group_id=mb_album_release_group_id,
                log_callback=lambda msg: ws.log(msg),
                overwrite=False,
                wiki_album_url=_wiki_album_url,
            )
        except Exception as e:
            ws.log(f"Entity artwork download: {e}", level="warning")

        # â”€â”€ 2b. Persist entity artwork to DB (via write queue) â”€â”€â”€â”€â”€â”€â”€â”€
        from app.pipeline_url.services.artwork_service import validate_file
        try:
            def _write_entity_artwork():
                # Update entity image fields in DB
                if art_result.get("artist_image_url") and item.artist_entity:
                    if not item.artist_entity.artist_image:
                        item.artist_entity.artist_image = art_result["artist_image_url"]
                if art_result.get("album_image_url") and item.album_entity:
                    if not item.album_entity.cover_image:
                        item.album_entity.cover_image = art_result["album_image_url"]

                # Fallback: if artwork pipeline didn't produce artist/album
                # poster (e.g. feat. credit variant failed), use existing
                # CachedAsset from the linked entity.
                from app.metadata.models import CachedAsset as _CA2b
                _entity_map = [
                    ("artist_poster", "artist", item.artist_entity),
                    ("album_poster", "album", item.album_entity),
                ]
                for art_key, etype, entity_obj in _entity_map:
                    if art_result.get(art_key) and os.path.isfile(art_result[art_key]):
                        continue  # pipeline already produced it
                    if not entity_obj:
                        continue
                    _ca = db.query(_CA2b).filter(
                        _CA2b.entity_type == etype,
                        _CA2b.entity_id == entity_obj.id,
                        _CA2b.kind == "poster",
                        _CA2b.status == "valid",
                    ).first()
                    if _ca and _ca.local_cache_path and os.path.isfile(_ca.local_cache_path):
                        art_result[art_key] = _ca.local_cache_path
                        ws.log(f"2b: using existing {etype} CachedAsset poster for {art_key}")

                # Store entity artwork as MediaAsset records
                for art_key, asset_type in [("artist_poster", "artist_thumb"), ("album_poster", "album_thumb")]:
                    art_path = art_result.get(art_key)
                    if not art_path or not os.path.isfile(art_path):
                        continue
                    vr = validate_file(art_path)
                    db.query(MediaAsset).filter(
                        MediaAsset.video_id == video_id,
                        MediaAsset.asset_type == asset_type,
                    ).delete(synchronize_session="fetch")
                    db.add(MediaAsset(
                        video_id=video_id, asset_type=asset_type,
                        file_path=art_path, provenance="artwork_pipeline",
                        status="valid" if (vr and vr.valid) else "invalid",
                        width=vr.width if vr and vr.valid else None,
                        height=vr.height if vr and vr.valid else None,
                        file_size_bytes=vr.file_size_bytes if vr and vr.valid else None,
                        file_hash=vr.file_hash if vr and vr.valid else None,
                        last_validated_at=datetime.now(timezone.utc),
                    ))

                db.commit()
            db_write(_write_entity_artwork)
            ws.log("Entity artwork pipeline complete")
        except Exception as e:
            db.rollback()
            ws.log(f"Entity artwork pipeline: {e}", level="warning")

        # â”€â”€ 2c. Fallback: link entity image from CachedAsset when still NULL â”€
        # Section 2 (process_artist_album_artwork) may have failed or been
        # skipped, but Section 1 (download_entity_assets) may have already
        # persisted a valid CachedAsset.  Ensure entity image fields are set.
        try:
            from app.metadata.models import CachedAsset as _CA2c
            _needs_commit = False
            if item.artist_entity and not item.artist_entity.artist_image:
                _ca = db.query(_CA2c).filter(
                    _CA2c.entity_type == "artist",
                    _CA2c.entity_id == item.artist_entity.id,
                    _CA2c.kind == "poster",
                    _CA2c.status == "valid",
                ).first()
                if _ca and _ca.source_url:
                    item.artist_entity.artist_image = _ca.source_url
                    _needs_commit = True
                    ws.log(f"2c: linked artist_image from cached asset {_ca.id}")
            if item.album_entity and not item.album_entity.cover_image:
                _ca = db.query(_CA2c).filter(
                    _CA2c.entity_type == "album",
                    _CA2c.entity_id == item.album_entity.id,
                    _CA2c.kind == "poster",
                    _CA2c.status == "valid",
                ).first()
                if _ca and _ca.source_url:
                    item.album_entity.cover_image = _ca.source_url
                    _needs_commit = True
                    ws.log(f"2c: linked cover_image from cached asset {_ca.id}")
            if _needs_commit:
                db_write(lambda: db.commit())
        except Exception as e:
            db.rollback()
            ws.log(f"2c entity image fallback: {e}", level="warning")

        # â”€â”€ 3. Poster upgrade from CoverArtArchive â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # CoverArtArchive single/EP cover art is the highest-priority poster
        # source.  Only skip the upgrade when the poster was already fetched
        # from the *same* CAA URL — if the existing poster came from a
        # different release (e.g. a remix pressing instead of the canonical
        # single), allow the upgrade so the correct art replaces it.
        _existing_poster = db.query(MediaAsset).filter(
            MediaAsset.video_id == video_id,
            MediaAsset.asset_type == "poster",
        ).first()

        # Also check the album entity for MB release ID — scraper_results may
        # not have it for library imports or when metadata resolution added
        # the album after the scraper stage.
        if not mb_album_release_id and item.album_entity:
            mb_album_release_id = getattr(item.album_entity, "mb_release_id", None)
        if not mb_album_release_group_id and item.album_entity:
            mb_album_release_group_id = getattr(item.album_entity, "mb_release_group_id", None)

        _has_parent_album = bool(mb_album_release_id or mb_album_release_group_id or item.album_entity)
        _video_poster_url = None
        _video_poster_source = None
        _skip_poster_upgrade = False  # set below after URL comparison

        try:
            # Always call fetch_caa_artwork so we can compare the canonical
            # URL against whatever is already stored.
            from app.scraper.artwork_selection import fetch_caa_artwork
            _caa_url, _caa_source, _caa_art_type = fetch_caa_artwork(
                mb_release_id=item.mb_release_id,
                mb_release_group_id=item.mb_release_group_id,
                mb_album_release_group_id=mb_album_release_group_id,
            )
            if _caa_url and _caa_art_type == "poster":
                _existing_source = (
                    getattr(_existing_poster, "source_url", None)
                    or getattr(_existing_poster, "resolved_url", None)
                ) if _existing_poster else None
                _same_source = bool(
                    _existing_source and _caa_url
                    and _existing_source == _caa_url
                )
                _skip_poster_upgrade = bool(
                    _existing_poster
                    and _existing_poster.file_path
                    and os.path.isfile(_existing_poster.file_path)
                    and _existing_poster.provenance == "artwork_pipeline"
                    and _same_source
                )
                if _skip_poster_upgrade:
                    ws.log("Poster upgrade: existing poster already from same CoverArtArchive source — keeping it")
                else:
                    _video_poster_url = _caa_url
                    _video_poster_source = "single_cover"
                    ws.log(f"Using single cover art for video poster")
                    ws.log(f"Using single cover art for video poster")

            if _video_poster_url and item.folder_path:
                from app.scraper.metadata_resolver import download_image
                from app.pipeline_url.services.artwork_service import guarded_copy, validate_file

                folder_name = os.path.basename(item.folder_path)
                ts = int(datetime.now(timezone.utc).timestamp())
                poster_dst = os.path.join(item.folder_path, f"{folder_name}-poster-pending-{ts}.jpg")
                thumb_dst = os.path.join(item.folder_path, f"{folder_name}-thumb-pending-{ts}.jpg")

                poster_ok = False
                album_art_path = art_result.get("album_poster")
                if not _has_parent_album and album_art_path and os.path.isfile(album_art_path):
                    import shutil
                    shutil.copy2(album_art_path, poster_dst)
                    poster_ok = True
                else:
                    poster_ok = download_image(_video_poster_url, poster_dst)

                if poster_ok:
                    guarded_copy(poster_dst, thumb_dst)
                    vr = validate_file(poster_dst) if os.path.isfile(poster_dst) else None
                    _poster_upgraded = False
                    try:
                        def _write_poster():
                            for vp_type, vp_path in [("poster", poster_dst), ("thumb", thumb_dst)]:
                                db.query(MediaAsset).filter(
                                    MediaAsset.video_id == video_id,
                                    MediaAsset.asset_type == vp_type,
                                ).delete(synchronize_session="fetch")
                                db.add(MediaAsset(
                                    video_id=video_id, asset_type=vp_type,
                                    file_path=vp_path, source_url=_video_poster_url,
                                    provenance="artwork_pipeline",
                                    status="valid" if (vr and vr.valid) else "invalid",
                                    width=vr.width if vr and vr.valid else None,
                                    height=vr.height if vr and vr.valid else None,
                                    file_size_bytes=vr.file_size_bytes if vr and vr.valid else None,
                                    file_hash=vr.file_hash if vr and vr.valid else None,
                                    last_validated_at=datetime.now(timezone.utc),
                                ))
                            db.commit()
                        db_write(_write_poster)
                        # Finalize: rename pending â†’ final, update DB paths
                        poster_final = os.path.join(item.folder_path, f"{folder_name}-poster.jpg")
                        thumb_final = os.path.join(item.folder_path, f"{folder_name}-thumb.jpg")
                        for _pending, _final, _atype in [
                            (poster_dst, poster_final, "poster"),
                            (thumb_dst, thumb_final, "thumb"),
                        ]:
                            try:
                                if os.path.isfile(_final):
                                    os.remove(_final)
                                os.rename(_pending, _final)
                                db_write(lambda _a=_atype, _f=_final: (
                                    db.query(MediaAsset).filter(
                                        MediaAsset.video_id == video_id,
                                        MediaAsset.asset_type == _a,
                                    ).update({"file_path": _f}),
                                    db.commit(),
                                ))
                            except Exception:
                                pass  # Still valid at pending path
                        ws.log(f"Video poster upgraded from {_video_poster_source}")
                        _poster_upgraded = True
                    except Exception as e:
                        db.rollback()
                        ws.log(f"Poster upgrade: {e}", level="warning")
                    if not _poster_upgraded:
                        # Clean up pending files on failure
                        for _pf in (poster_dst, thumb_dst):
                            try:
                                if os.path.isfile(_pf):
                                    os.remove(_pf)
                            except OSError:
                                pass
        except Exception as e:
            db.rollback()
            ws.log(f"Poster upgrade: {e}", level="warning")

        # -- 3a. Replace scraper poster with thumb when no single cover found --
        # When _has_parent_album is True but the poster upgrade produced nothing
        # (mb_release_id is None or CAA had no cover), the existing poster from
        # the scraper stage *may* be album art (Wikipedia song pages sometimes
        # show the album cover as infobox image).  Only remove it if the
        # poster's source URL matches the album entity's cover image;
        # otherwise the scraper found genuine single/song artwork.
        if (not _skip_poster_upgrade and _has_parent_album
                and not _video_poster_url):
            try:
                _suspect_poster = db.query(MediaAsset).filter(
                    MediaAsset.video_id == video_id,
                    MediaAsset.asset_type == "poster",
                ).first()
                if (_suspect_poster
                        and getattr(_suspect_poster, "provenance", None) == "scraper"):
                    # Only delete if the poster is actually the album cover.
                    _album_cover_url = None
                    if item.album_entity:
                        _album_cover_url = getattr(item.album_entity, "cover_image", None)
                    _poster_src = getattr(_suspect_poster, "source_url", None) or ""
                    _is_album_art = (
                        _album_cover_url
                        and _poster_src
                        and _poster_src.rstrip("/") == _album_cover_url.rstrip("/")
                    )
                    if _is_album_art:
                        _suspect_thumb = db.query(MediaAsset).filter(
                            MediaAsset.video_id == video_id,
                            MediaAsset.asset_type == "thumb",
                            MediaAsset.provenance == "scraper",
                        ).first()
                        def _write_scraper_cleanup():
                            db.delete(_suspect_poster)
                            if _suspect_thumb:
                                db.delete(_suspect_thumb)
                            db.commit()
                        db_write(_write_scraper_cleanup)
                        ws.log("Removed scraper poster: matches album cover, "
                               "falling back to video thumbnail")
                    else:
                        ws.log("Keeping scraper poster: source differs from album cover")
            except Exception as e:
                db.rollback()
                ws.log(f"Poster scraper cleanup: {e}", level="warning")

        # ── 3a. Fallback: Wikipedia single cover image ─────────────────
        # If CoverArtArchive didn't produce a poster, try the Wikipedia
        # single/song cover image that was scraped earlier.
        try:
            _poster_after_caa = db.query(MediaAsset).filter(
                MediaAsset.video_id == video_id,
                MediaAsset.asset_type == "poster",
            ).first()
            _wiki_image_url = metadata.get("image_url")
            if not _poster_after_caa and _wiki_image_url and item and item.folder_path:
                from app.services.metadata_resolver import download_image
                from app.services.artwork_service import guarded_copy, validate_file

                folder_name = os.path.basename(item.folder_path)
                ts = int(datetime.now(timezone.utc).timestamp())
                _wp_poster = os.path.join(item.folder_path, f"{folder_name}-poster-pending-{ts}.jpg")
                _wp_thumb = os.path.join(item.folder_path, f"{folder_name}-thumb-pending-{ts}.jpg")

                if download_image(_wiki_image_url, _wp_poster):
                    guarded_copy(_wp_poster, _wp_thumb)
                    _wp_vr = validate_file(_wp_poster) if os.path.isfile(_wp_poster) else None
                    with _apply_lock:
                        for _wp_type, _wp_path in [("poster", _wp_poster), ("thumb", _wp_thumb)]:
                            db.query(MediaAsset).filter(
                                MediaAsset.video_id == video_id,
                                MediaAsset.asset_type == _wp_type,
                            ).delete(synchronize_session="fetch")
                            db.add(MediaAsset(
                                video_id=video_id, asset_type=_wp_type,
                                file_path=_wp_path, source_url=_wiki_image_url,
                                provenance="artwork_pipeline",
                                status="valid" if (_wp_vr and _wp_vr.valid) else "invalid",
                                width=_wp_vr.width if _wp_vr and _wp_vr.valid else None,
                                height=_wp_vr.height if _wp_vr and _wp_vr.valid else None,
                                file_size_bytes=_wp_vr.file_size_bytes if _wp_vr and _wp_vr.valid else None,
                                file_hash=_wp_vr.file_hash if _wp_vr and _wp_vr.valid else None,
                                last_validated_at=datetime.now(timezone.utc),
                            ))
                        db.commit()
                    # Rename pending -> final
                    for _wp_pend, _wp_final, _wp_atype in [
                        (_wp_poster, os.path.join(item.folder_path, f"{folder_name}-poster.jpg"), "poster"),
                        (_wp_thumb, os.path.join(item.folder_path, f"{folder_name}-thumb.jpg"), "thumb"),
                    ]:
                        try:
                            if os.path.isfile(_wp_final):
                                os.remove(_wp_final)
                            os.rename(_wp_pend, _wp_final)
                            with _apply_lock:
                                db.query(MediaAsset).filter(
                                    MediaAsset.video_id == video_id,
                                    MediaAsset.asset_type == _wp_atype,
                                ).update({"file_path": _wp_final})
                                db.commit()
                        except Exception:
                            pass
                    ws.log("Poster fallback: using Wikipedia single cover image")
                else:
                    for _f in (_wp_poster, _wp_thumb):
                        try:
                            if os.path.isfile(_f):
                                os.remove(_f)
                        except OSError:
                            pass
        except Exception as e:
            db.rollback()
            ws.log(f"Poster wiki fallback: {e}", level="warning")

        # â”€â”€ 3b. Fallback: create poster from video thumb if none exists â”€â”€
        # When no CoverArtArchive poster was fetched (e.g. mb_release_id is
        # None), use the existing library thumbnail as the poster so the
        # video isn't left with no poster asset at all.
        try:
            _poster_exists = db.query(MediaAsset).filter(
                MediaAsset.video_id == video_id,
                MediaAsset.asset_type == "poster",
            ).first()
            if not _poster_exists and item and item.folder_path:
                # Try "thumb" first, then fall back to "video_thumb" (scene analysis)
                _thumb_asset = db.query(MediaAsset).filter(
                    MediaAsset.video_id == video_id,
                    MediaAsset.asset_type == "thumb",
                ).first()
                _fallback_prov = "thumb_fallback"
                if not (_thumb_asset and _thumb_asset.file_path and os.path.isfile(_thumb_asset.file_path)):
                    _thumb_asset = db.query(MediaAsset).filter(
                        MediaAsset.video_id == video_id,
                        MediaAsset.asset_type == "video_thumb",
                    ).first()
                    _fallback_prov = "video_thumb_fallback"
                if _thumb_asset and _thumb_asset.file_path and os.path.isfile(_thumb_asset.file_path):
                    import shutil
                    from app.pipeline_url.services.artwork_service import validate_file
                    from app.pipeline_url.services.file_organizer import build_folder_name
                    _folder_name = os.path.basename(item.folder_path)
                    _poster_dst = os.path.join(item.folder_path, f"{_folder_name}-poster.jpg")
                    shutil.copy2(_thumb_asset.file_path, _poster_dst)
                    _vr = validate_file(_poster_dst) if os.path.isfile(_poster_dst) else None
                    def _write_thumb_fallback():
                        db.add(MediaAsset(
                            video_id=video_id, asset_type="poster",
                            file_path=_poster_dst, source_url=None,
                            provenance=_fallback_prov,
                            status="valid" if (_vr and _vr.valid) else "invalid",
                            width=_vr.width if _vr and _vr.valid else None,
                            height=_vr.height if _vr and _vr.valid else None,
                            file_size_bytes=_vr.file_size_bytes if _vr and _vr.valid else None,
                            file_hash=_vr.file_hash if _vr and _vr.valid else None,
                            last_validated_at=datetime.now(timezone.utc),
                        ))
                        db.commit()
                    db_write(_write_thumb_fallback)
                    ws.log(f"Poster fallback: created from {_fallback_prov}")
        except Exception as e:
            db.rollback()
            ws.log(f"Poster fallback: {e}", level="warning")
    finally:
        db.close()


def _re_resolve_sources(db, video_id: int, artist: str, title: str,
                        album: str, ws: ImportWorkspace) -> None:
    """Re-collect Wikipedia / MusicBrainz / IMDB sources after AI correction.

    Removes stale scraped sources for the *old* entity and creates new ones
    for the corrected identity.  YouTube/import sources are untouched.
    """
    import re as _re
    from app.models import Source, VideoItem

    # Collect stale scraped/ai sources. Keep import/matched/manual sources.
    stale = (
        db.query(Source)
        .filter(
            Source.video_id == video_id,
            Source.provenance.in_(("scraped", "ai")),
        )
        .all()
    )

    # Preserve MusicBrainz release-group IDs and Wikipedia album URLs from
    # existing sources â€” these are hard to reconstruct because the underlying
    # IDs aren't stored on the VideoItem model.
    _saved_mb_sources = {}   # source_type â†’ {id, url}
    _saved_wiki_album_url = None
    for s in stale:
        if s.provider == "musicbrainz" and "release-group" in (s.canonical_url or ""):
            _saved_mb_sources[s.source_type] = {
                "id": s.source_video_id, "url": s.canonical_url,
            }
        if s.provider == "wikipedia" and s.source_type == "album":
            _saved_wiki_album_url = s.canonical_url

    for s in stale:
        ws.log(f"Removing stale source: {s.provider}/{s.source_type} ({s.canonical_url})")
        db.delete(s)
    db.flush()

    from app.scraper.source_validation import parse_multi_artist
    # Prefer the artist entity's canonical name â€” parse_multi_artist
    # incorrectly splits band names containing "&" (e.g. "Iron & Wine").
    _vid_item = db.query(VideoItem).get(video_id) if video_id else None
    if _vid_item and _vid_item.artist_entity and _vid_item.artist_entity.canonical_name:
        primary_artist = _vid_item.artist_entity.canonical_name
    else:
        primary_artist, _ = parse_multi_artist(artist)

    new_sources = []

    # --- Wikipedia: independent search ---
    _wiki_artist_url = None
    _wiki_single_url = None
    _wiki_album_url = None

    try:
        from app.scraper.metadata_resolver import search_wikipedia_artist
        _wiki_artist_url = search_wikipedia_artist(primary_artist)
    except Exception as e:
        ws.log(f"Source re-resolve: artist wiki: {e}", level="warning")

    # MB→Wikidata→Wikipedia fallback for artist page
    if not _wiki_artist_url and getattr(item, 'mb_artist_id', None):
        try:
            from app.scraper.metadata_resolver import resolve_artist_wikipedia_via_mb
            _wiki_artist_url = resolve_artist_wikipedia_via_mb(item.mb_artist_id)
            if _wiki_artist_url:
                ws.log(f"Source re-resolve: artist wiki via MB→Wikidata: {_wiki_artist_url}")
        except Exception as e:
            ws.log(f"Source re-resolve: artist wiki MB→Wikidata: {e}", level="warning")

    try:
        from app.scraper.metadata_resolver import search_wikipedia
        _wiki_single_url = search_wikipedia(title, primary_artist)
    except Exception as e:
        ws.log(f"Source re-resolve: single wiki: {e}", level="warning")

    if album:
        try:
            from app.scraper.metadata_resolver import search_wikipedia_album
            _wiki_album_url = search_wikipedia_album(primary_artist, album)
        except Exception as e:
            ws.log(f"Source re-resolve: album wiki: {e}", level="warning")

    # --- Wikipedia: cross-link confirmation & fallback ---
    # Follow infobox links on scraped pages to confirm or discover related pages.
    # Single page â†’ album link + artist link; Album page â†’ artist link.
    try:
        from app.scraper.metadata_resolver import extract_wiki_infobox_links

        # Extract links from the single page (album + artist)
        _wiki_mb_single_rgs: list[str] = []
        _wiki_mb_album_rgs: list[str] = []
        if _wiki_single_url:
            _single_links = extract_wiki_infobox_links(_wiki_single_url)
            _linked_album = _single_links.get("album_url")
            _linked_artist = _single_links.get("artist_url")
            _wiki_mb_single_rgs = _single_links.get("mb_release_group_ids", [])

            # Cover song protection: validate the infobox artist matches
            # our resolved artist before accepting cross-links.  For cover
            # songs the first infobox links to the *original* artist/album.
            _xlink_ok = True
            if _linked_artist and primary_artist:
                from urllib.parse import unquote as _url_unquote
                _link_page = _url_unquote(
                    _linked_artist.rsplit("/wiki/", 1)[-1]
                ).replace("_", " ").strip()
                _lp = _link_page.lower()
                _ra = primary_artist.lower().strip()
                if not (_lp == _ra or _lp in _ra or _ra in _lp):
                    # Tier 1: alpha-only prefix check for band renames
                    # e.g. "The Jackson 5" / "The Jacksons" → "thejackson" / "thejacksons"
                    import re as _re_mod
                    _lp_alpha = _re_mod.sub(r'[^a-z]', '', _lp)
                    _ra_alpha = _re_mod.sub(r'[^a-z]', '', _ra)
                    _shorter = min(len(_lp_alpha), len(_ra_alpha))
                    _longer = max(len(_lp_alpha), len(_ra_alpha))
                    if (_shorter >= 6
                            and _longer > 0
                            and _shorter / _longer >= 0.8
                            and (_lp_alpha.startswith(_ra_alpha)
                                 or _ra_alpha.startswith(_lp_alpha))):
                        ws.log(
                            f"Wikipedia cross-link: name prefix match "
                            f"accepted ('{_link_page}' ≈ '{primary_artist}')")
                    else:
                        # Tier 2: check MusicBrainz aliases (authoritative)
                        _mb_aid = getattr(item, 'mb_artist_id', None)
                        _alias_matched = False
                        if _mb_aid:
                            try:
                                import musicbrainzngs
                                _ai = musicbrainzngs.get_artist_by_id(
                                    _mb_aid, includes=["aliases"]
                                )
                                import time as _time_mod
                                _time_mod.sleep(1.1)
                                _known = {_ai["artist"]["name"].lower().strip()}
                                for _al in _ai["artist"].get("alias-list", []):
                                    if _al.get("alias"):
                                        _known.add(_al["alias"].lower().strip())
                                if _lp in _known:
                                    _alias_matched = True
                                    ws.log(
                                        f"Wikipedia cross-link: '{_link_page}' "
                                        f"verified as MB alias of '{primary_artist}'")
                            except Exception:
                                pass
                        if not _alias_matched:
                            _xlink_ok = False
                            ws.log(
                                f"Wikipedia cross-link: infobox artist "
                                f"'{_link_page}' doesn't match resolved "
                                f"'{primary_artist}' (cover song?) "
                                f"-- discarding infobox cross-links")

            # Album: confirm or fallback
            if _linked_album and _xlink_ok:
                if _wiki_album_url:
                    if _wiki_album_url == _linked_album:
                        ws.log("Wikipedia cross-link: album confirmed by single page")
                    else:
                        ws.log(f"Wikipedia cross-link: single links to album {_linked_album}, "
                               f"overriding search result {_wiki_album_url}")
                        _wiki_album_url = _linked_album
                else:
                    ws.log(f"Wikipedia fallback: album from single page â†’ {_linked_album}")
                    _wiki_album_url = _linked_album

            # Tentative artist from single (album's artist link is checked below)
            if _linked_artist and not _wiki_artist_url and _xlink_ok:
                ws.log(f"Wikipedia fallback: artist from single page â†’ {_linked_artist}")
                _wiki_artist_url = _linked_artist

        # Extract artist link from album page (most authoritative source)
        if _wiki_album_url:
            _album_links = extract_wiki_infobox_links(_wiki_album_url)
            _linked_artist_from_album = _album_links.get("artist_url")
            _wiki_mb_album_rgs = _album_links.get("mb_release_group_ids", [])

            if _linked_artist_from_album:
                if _wiki_artist_url:
                    if _wiki_artist_url == _linked_artist_from_album:
                        ws.log("Wikipedia cross-link: artist confirmed by album page")
                    else:
                        ws.log(f"Wikipedia cross-link: album links to artist "
                               f"{_linked_artist_from_album}, overriding {_wiki_artist_url}")
                        _wiki_artist_url = _linked_artist_from_album
                else:
                    ws.log(f"Wikipedia fallback: artist from album page â†’ "
                           f"{_linked_artist_from_album}")
                    _wiki_artist_url = _linked_artist_from_album

        # --- MB / Wiki cross-reference ---
        # Use MB release-group IDs found on Wikipedia pages to populate
        # the album entity's mb_release_group_id when it is missing.
        # The single page's RG should match the item's mb_release_group_id;
        # the album page's RG is the parent album's release-group.
        from app.models import VideoItem as _VIxr
        _xr_item = db.query(_VIxr).get(video_id)
        if _xr_item:
            _item_single_rg = getattr(_xr_item, "mb_release_group_id", None)

            # Log MB RGs found on wiki single page
            if _wiki_mb_single_rgs:
                ws.log(f"Wikiâ†’MB cross-ref: single page RGs={_wiki_mb_single_rgs}")
            if _wiki_mb_album_rgs:
                ws.log(f"Wikiâ†’MB cross-ref: album page RGs={_wiki_mb_album_rgs}")

            # Album page RG â†’ populate album entity if missing
            if _wiki_mb_album_rgs and _xr_item.album_entity:
                _alb_ent_rg = getattr(_xr_item.album_entity, "mb_release_group_id", None)
                if not _alb_ent_rg:
                    # Use the first RG from the album page that differs
                    # from the item's single RG
                    for _cand_rg in _wiki_mb_album_rgs:
                        if _cand_rg != _item_single_rg:
                            _xr_item.album_entity.mb_release_group_id = _cand_rg
                            db.flush()
                            ws.log(f"Wikiâ†’MB cross-ref: populated album entity "
                                   f"mb_release_group_id={_cand_rg}")
                            break

    except Exception as e:
        ws.log(f"Source re-resolve: wikipedia cross-link: {e}", level="warning")

    # --- Wikipedia: album tracklist â†’ single fallback ---
    if not _wiki_single_url and _wiki_album_url:
        try:
            from app.scraper.metadata_resolver import extract_single_wiki_url_from_album
            _tl_url = extract_single_wiki_url_from_album(_wiki_album_url, title)
            if _tl_url:
                ws.log(f"Wikipedia fallback: single from album tracklist â†’ {_tl_url}")
                _wiki_single_url = _tl_url
        except Exception as e:
            ws.log(f"Source re-resolve: album tracklist fallback: {e}", level="warning")

    # --- Wikipedia: create sources from final URLs ---
    if _wiki_artist_url:
        page_id = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", _wiki_artist_url)
        new_sources.append(Source(
            video_id=video_id, provider="wikipedia",
            source_video_id=page_id, original_url=_wiki_artist_url,
            canonical_url=_wiki_artist_url, source_type="artist",
            provenance="ai",
        ))

    if _wiki_single_url:
        page_id = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", _wiki_single_url)
        new_sources.append(Source(
            video_id=video_id, provider="wikipedia",
            source_video_id=page_id, original_url=_wiki_single_url,
            canonical_url=_wiki_single_url, source_type="single",
            provenance="ai",
        ))

    _resolved_wiki_album = False
    if _wiki_album_url:
        page_id = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", _wiki_album_url)
        new_sources.append(Source(
            video_id=video_id, provider="wikipedia",
            source_video_id=page_id, original_url=_wiki_album_url,
            canonical_url=_wiki_album_url, source_type="album",
            provenance="ai",
        ))
        _resolved_wiki_album = True

    # Restore previous Wikipedia album source if all resolution attempts failed
    if not _resolved_wiki_album and _saved_wiki_album_url:
        page_id = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", _saved_wiki_album_url)
        new_sources.append(Source(
            video_id=video_id, provider="wikipedia",
            source_video_id=page_id, original_url=_saved_wiki_album_url,
            canonical_url=_saved_wiki_album_url, source_type="album",
            provenance="ai",
        ))

    # IMDB
    try:
        from app.scraper.metadata_resolver import search_imdb_music_video
        imdb_url = search_imdb_music_video(primary_artist, title)
        if imdb_url:
            m = _re.search(r"(tt\d+|nm\d+)", imdb_url)
            imdb_id = m.group(1) if m else imdb_url
            new_sources.append(Source(
                video_id=video_id, provider="imdb",
                source_video_id=imdb_id, original_url=imdb_url,
                canonical_url=imdb_url, source_type="video",
                provenance="ai",
            ))
    except Exception as e:
        ws.log(f"Source re-resolve: imdb: {e}", level="warning")

    # MusicBrainz â€” re-search under the corrected identity and update VideoItem
    from app.models import VideoItem
    item = db.query(VideoItem).get(video_id)
    if item:
        # Run a fresh MB search when the item has no MB recording ID.
        # This covers the case where the original import had mangled
        # artist/title parsing and MB search returned nothing.
        if not item.mb_recording_id:
            try:
                from app.scraper.metadata_resolver import (
                    search_musicbrainz, _normalize_for_compare, _tokens_overlap,
                )
                from app.scraper.source_validation import parse_multi_artist as _pma_mb
                mb = search_musicbrainz(artist, title)
                if mb.get("mb_recording_id"):
                    _primary_for_match, _ = _pma_mb(artist)
                    mb_norm = _normalize_for_compare(mb["artist"]) if mb.get("artist") else ""
                    exp_norm = _normalize_for_compare(artist)
                    primary_norm = _normalize_for_compare(_primary_for_match) if _primary_for_match else ""
                    if (mb_norm == exp_norm
                            or mb_norm == primary_norm
                            or _tokens_overlap(mb["artist"], artist, 0.4)
                            or (_primary_for_match and _tokens_overlap(mb["artist"], _primary_for_match, 0.4))):
                        item.mb_artist_id = mb.get("mb_artist_id")
                        item.mb_recording_id = mb.get("mb_recording_id")
                        item.mb_release_id = mb.get("mb_release_id")
                        item.mb_release_group_id = mb.get("mb_release_group_id")
                        if mb.get("album") and not item.album:
                            item.album = mb["album"]
                        if mb.get("year") and not item.year:
                            item.year = mb["year"]
                        db.flush()
                        ws.log(f"MusicBrainz re-resolved: artist={mb.get('mb_artist_id')}, "
                               f"recording={mb.get('mb_recording_id')}")
                    else:
                        ws.log(f"MusicBrainz re-resolve: artist mismatch "
                               f"'{mb.get('artist')}' vs '{artist}' â€” skipped")
            except Exception as e:
                ws.log(f"Source re-resolve: musicbrainz search: {e}", level="warning")

        # Reconstruct Source records from video MB IDs
        if item.mb_artist_id:
            new_sources.append(Source(
                video_id=video_id, provider="musicbrainz",
                source_video_id=item.mb_artist_id,
                original_url=f"https://musicbrainz.org/artist/{item.mb_artist_id}",
                canonical_url=f"https://musicbrainz.org/artist/{item.mb_artist_id}",
                source_type="artist", provenance="ai",
            ))

        # Restore preserved MusicBrainz release-group sources (single and album).
        # Also create them directly from VideoItem / album entity fields when
        # no stale source existed to preserve (e.g. initial import didn't
        # have MB IDs populated at source-collection time).
        # Track which release-group IDs we've already queued so we never
        # create two MB sources with the same source_video_id (UNIQUE constraint).
        _used_mb_rg_ids: set = set()
        for st, saved in _saved_mb_sources.items():
            _used_mb_rg_ids.add(saved["id"])
            new_sources.append(Source(
                video_id=video_id, provider="musicbrainz",
                source_video_id=saved["id"],
                original_url=saved["url"],
                canonical_url=saved["url"],
                source_type=st, provenance="ai",
            ))

        # Create musicbrainz/single from VideoItem.mb_release_group_id
        if item.mb_release_group_id and "single" not in _saved_mb_sources:
            new_sources.append(Source(
                video_id=video_id, provider="musicbrainz",
                source_video_id=item.mb_release_group_id,
                original_url=f"https://musicbrainz.org/release-group/{item.mb_release_group_id}",
                canonical_url=f"https://musicbrainz.org/release-group/{item.mb_release_group_id}",
                source_type="single", provenance="ai",
            ))
            _used_mb_rg_ids.add(item.mb_release_group_id)

        # Create musicbrainz/album from the album entity's release group.
        # When the entity has mb_release_id but no mb_release_group_id,
        # look up the release-group from MusicBrainz and populate it.
        if "album" not in _saved_mb_sources and item.album_entity:
            _album_rg = getattr(item.album_entity, "mb_release_group_id", None)
            if not _album_rg and getattr(item.album_entity, "mb_release_id", None):
                try:
                    import musicbrainzngs
                    _rel = musicbrainzngs.get_release_by_id(
                        item.album_entity.mb_release_id,
                        includes=["release-groups"],
                    )
                    _rg_data = _rel.get("release", {}).get("release-group", {})
                    _album_rg = _rg_data.get("id")
                    if _album_rg:
                        item.album_entity.mb_release_group_id = _album_rg
                        db.flush()
                        ws.log(f"Populated album entity mb_release_group_id={_album_rg}")
                except Exception as e:
                    ws.log(f"Album RG lookup from release: {e}", level="warning")
            if _album_rg and _album_rg not in _used_mb_rg_ids:
                new_sources.append(Source(
                    video_id=video_id, provider="musicbrainz",
                    source_video_id=_album_rg,
                    original_url=f"https://musicbrainz.org/release-group/{_album_rg}",
                    canonical_url=f"https://musicbrainz.org/release-group/{_album_rg}",
                    source_type="album", provenance="ai",
                ))
                _used_mb_rg_ids.add(_album_rg)
            elif _album_rg:
                ws.log(f"Skipping musicbrainz/album source: same release-group as single ({_album_rg})")

    for src in new_sources:
        # Avoid duplicates (unique constraint: video_id + provider + source_video_id)
        exists = db.query(Source).filter(
            Source.video_id == video_id,
            Source.provider == src.provider,
            Source.source_video_id == src.source_video_id,
        ).first()
        if not exists:
            db.add(src)
            ws.log(f"Re-resolved source: {src.provider}/{src.source_type} â†’ {src.canonical_url}")
        elif exists.source_type != src.source_type:
            exists.source_type = src.source_type
            ws.log(f"Updated source type: {src.provider}/{src.source_type} â†’ {src.canonical_url}")

    db.flush()


def _re_organize_file(db, video_id: int, ws: ImportWorkspace) -> None:
    """Rename the library folder/file to match AI-corrected artist + title."""
    from app.models import VideoItem

    item = db.query(VideoItem).get(video_id)
    if not item or not item.folder_path or not item.file_path:
        return
    if not os.path.isdir(item.folder_path):
        return

    from app.services.file_organizer import build_folder_name, build_library_subpath, sanitize_filename
    from app.config import get_settings

    version_type = item.version_type or "normal"
    alt_label = item.alternate_version_label or ""
    resolution = item.resolution_label or "1080p"
    if not item.resolution_label:
        item.resolution_label = resolution

    new_folder_name = build_folder_name(
        item.artist, item.title, resolution,
        version_type=version_type,
        alternate_version_label=alt_label,
    )

    old_folder = item.folder_path
    old_folder_name = os.path.basename(old_folder)

    # Build subpath using configured folder structure
    subpath = build_library_subpath(
        item.artist, item.title, resolution,
        album=item.album or "",
        version_type=version_type,
        alternate_version_label=alt_label,
    )
    settings = get_settings()
    library_dir = settings.library_dir
    new_folder = os.path.join(library_dir, subpath)

    if os.path.normpath(old_folder) == os.path.normpath(new_folder):
        return  # already correct

    # Ensure parent directories exist (for nested structures like Artist/VideoFolder)
    os.makedirs(os.path.dirname(new_folder), exist_ok=True)

    # Rename/move the folder
    try:
        os.rename(old_folder, new_folder)
    except OSError as e:
        ws.log(f"File rename failed: {e}", level="warning")
        return

    # Clean up empty parent directories left behind
    old_parent = os.path.dirname(old_folder)
    if old_parent != os.path.normpath(library_dir) and os.path.isdir(old_parent):
        try:
            os.rmdir(old_parent)  # only removes if empty
        except OSError:
            pass

    # Rename the video file inside the folder
    old_file = item.file_path
    old_filename = os.path.basename(old_file)
    ext = os.path.splitext(old_filename)[1]
    new_filename = f"{new_folder_name}{ext}"
    new_file = os.path.join(new_folder, new_filename)

    old_file_in_new_folder = os.path.join(new_folder, old_filename)
    if os.path.isfile(old_file_in_new_folder) and old_filename != new_filename:
        try:
            os.rename(old_file_in_new_folder, new_file)
        except OSError as e:
            ws.log(f"File rename (inner) failed: {e}", level="warning")
            new_file = old_file_in_new_folder

    # Rename auxiliary files (poster, thumb, NFO) that used the old folder name
    for entry in os.listdir(new_folder):
        entry_lower = entry.lower()
        # Skip the video file we already renamed
        if entry == new_filename or entry == old_filename:
            continue
        # Match old-name-based auxiliary files
        if entry_lower.endswith(('-poster.jpg', '-thumb.jpg', '.nfo')):
            # Determine the new auxiliary name
            if entry_lower.endswith('-poster.jpg'):
                new_aux = f"{new_folder_name}-poster.jpg"
            elif entry_lower.endswith('-thumb.jpg'):
                new_aux = f"{new_folder_name}-thumb.jpg"
            elif entry_lower.endswith('.nfo'):
                new_aux = f"{new_folder_name}.nfo"
            else:
                continue
            if entry != new_aux:
                try:
                    os.rename(
                        os.path.join(new_folder, entry),
                        os.path.join(new_folder, new_aux),
                    )
                except OSError:
                    pass

    # Update DB paths
    item.folder_path = new_folder
    item.file_path = new_file

    # Update media_assets that reference the old folder path
    from app.models import MediaAsset
    assets = db.query(MediaAsset).filter(
        MediaAsset.video_id == video_id,
    ).all()
    for asset in assets:
        if asset.file_path and old_folder in asset.file_path:
            old_asset_name = os.path.basename(asset.file_path)
            # Determine the new filename for this asset
            new_asset_name = old_asset_name
            al = old_asset_name.lower()
            if al.endswith('-poster.jpg'):
                new_asset_name = f"{new_folder_name}-poster.jpg"
            elif al.endswith('-thumb.jpg'):
                new_asset_name = f"{new_folder_name}-thumb.jpg"
            elif al.endswith('.nfo'):
                new_asset_name = f"{new_folder_name}.nfo"
            new_asset_path = os.path.join(new_folder, new_asset_name)
            if os.path.isfile(new_asset_path):
                asset.file_path = new_asset_path

    db.flush()

    ws.log(f"Renamed: '{old_folder_name}' â†’ '{new_folder_name}'")


def _deferred_ai_enrichment(video_id: int, ws: ImportWorkspace) -> None:
    """Run AI metadata enrichment (API call + DB write via queue)."""
    from app.database import SessionLocal
    from app.models import VideoItem

    def _write():
        db = SessionLocal()
        try:
            # Snapshot identity before AI enrichment
            item_before = db.query(VideoItem).get(video_id)
            if not item_before:
                return
            _old_artist = item_before.artist or ""
            _old_title = item_before.title or ""
            _old_album = item_before.album or ""

            from app.pipeline_url.ai.metadata_service import enrich_video_metadata
            result = enrich_video_metadata(db, video_id, auto_apply=True)
            if result is None:
                ws.log("AI enrichment skipped: no AI provider configured", level="warning")
                return
            _mark_processing_state(db, video_id, "ai_enriched", method="ai")
            # Check if a description/plot was generated
            item = db.query(VideoItem).get(video_id)
            if item and item.plot:
                _mark_processing_state(db, video_id, "description_generated", method="ai")

            # Check if AI changed identity fields â€” if so, re-resolve
            # sources and rename the library folder to match.
            _new_artist = (item.artist or "") if item else ""
            _new_title = (item.title or "") if item else ""
            _new_album = (item.album or "") if item else ""
            _identity_changed = (
                _old_artist.lower() != _new_artist.lower()
                or _old_title.lower() != _new_title.lower()
                or _old_album.lower() != _new_album.lower()
            )
            if _identity_changed and item:
                ws.log(
                    f"AI changed identity: "
                    f"artist='{_old_artist}'â†’'{_new_artist}', "
                    f"title='{_old_title}'â†’'{_new_title}', "
                    f"album='{_old_album}'â†’'{_new_album}'"
                )
                try:
                    _re_resolve_sources(
                        db, video_id, _new_artist, _new_title,
                        _new_album, ws,
                    )
                except Exception as e:
                    ws.log(f"Source re-resolution: {e}", level="warning")

                if _old_artist.lower() != _new_artist.lower() or _old_title.lower() != _new_title.lower():
                    try:
                        _re_organize_file(db, video_id, ws)
                    except Exception as e:
                        ws.log(f"File re-organize: {e}", level="warning")

                # Update job display name to reflect AI-corrected identity
                # Read action_label to preserve job type suffix
                from app.database import CosmeticSessionLocal as _DLabelSL
                from app.models import ProcessingJob as _DLabelPJ
                _dl_db = _DLabelSL()
                try:
                    _dl_job = _dl_db.query(_DLabelPJ).get(ws.job_id)
                    _dl_label = _dl_job.action_label if _dl_job else None
                finally:
                    _dl_db.close()
                _deferred_display = f"{_new_artist} \u2013 {_new_title}"
                if _dl_label:
                    _deferred_display = f"{_deferred_display} \u203a {_dl_label}"
                _update_child_step(
                    ws.job_id,
                    display_name=_deferred_display,
                )

            db.commit()
        except ImportError:
            pass
        except Exception as e:
            db.rollback()
            ws.log(f"AI enrichment: {e}", level="warning")
            # Flag the video for human review so it's easy to find
            try:
                from app.models import VideoItem
                from sqlalchemy.orm.attributes import flag_modified
                db2 = SessionLocal()
                try:
                    item = db2.query(VideoItem).get(video_id)
                    if item and (item.review_status or "none") == "none":
                        item.review_status = "needs_human_review"
                        item.review_reason = f"AI enrichment failed: {e}"
                        db2.commit()
                finally:
                    db2.close()
            except Exception:
                pass
        finally:
            db.close()

    try:
        db_write(_write)
    except Exception as e:
        ws.log(f"AI enrichment queue error: {e}", level="warning")


def _deferred_matching(video_id: int, ws: ImportWorkspace) -> None:
    """Run matching resolution for the video (via write queue)."""
    from app.database import SessionLocal

    def _write():
        db = SessionLocal()
        try:
            from app.pipeline_url.matching.resolver import resolve_video
            resolve_video(db, video_id)
            db.commit()
        except ImportError:
            pass
        except Exception as e:
            db.rollback()
            ws.log(f"Matching resolution: {e}", level="warning")
        finally:
            db.close()

    try:
        db_write(_write)
    except Exception as e:
        ws.log(f"Matching resolution queue error: {e}", level="warning")


def _deferred_orphan_cleanup(video_id: int, ws: ImportWorkspace) -> None:
    """Clean up orphaned entities after import (via write queue)."""
    from app.database import SessionLocal
    from app.models import VideoItem
    from app.metadata.models import ArtistEntity, AlbumEntity

    def _write():
        db = SessionLocal()
        try:
            orphan_albums = (
                db.query(AlbumEntity)
                .filter(~db.query(VideoItem).filter(VideoItem.album_entity_id == AlbumEntity.id).exists())
                .all()
            )
            for orphan in orphan_albums:
                ws.log(f"Removing orphan AlbumEntity: {orphan.title}")
                db.delete(orphan)

            orphan_artists = (
                db.query(ArtistEntity)
                .filter(~db.query(VideoItem).filter(VideoItem.artist_entity_id == ArtistEntity.id).exists())
                .all()
            )
            for orphan in orphan_artists:
                ws.log(f"Removing orphan ArtistEntity: {orphan.canonical_name}")
                db.delete(orphan)

            db.commit()
        except Exception as e:
            db.rollback()
            ws.log(f"Orphan cleanup error: {e}", level="warning")
        finally:
            db.close()

    try:
        db_write(_write)
    except Exception as e:
        ws.log(f"Orphan cleanup queue error: {e}", level="warning")


# Dispatch table
_DISPATCH = {
    "preview": _deferred_preview,
    "scene_analysis": _deferred_scene_analysis,
    "kodi_export": _deferred_kodi_export,
    "entity_artwork": _deferred_entity_artwork,
    "ai_enrichment": _deferred_ai_enrichment,
    "matching": _deferred_matching,
    "orphan_cleanup": _deferred_orphan_cleanup,
}
