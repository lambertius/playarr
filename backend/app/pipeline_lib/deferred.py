# AUTO-SEPARATED from pipeline/deferred.py for pipeline_lib pipeline
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
- DB writes use _apply_lock (entity artwork) or SQLite busy_timeout +
  retry (scene analysis, matching, AI enrichment) â€” no serialised
  global queue bottleneck.
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

from app.pipeline_lib.workspace import ImportWorkspace
from app.pipeline_lib.db_apply import _apply_lock
from app.worker import GLOBAL_DEFERRED_SLOTS

logger = logging.getLogger(__name__)

_MAX_DB_RETRIES = 7


def _retry_delay(attempt: int) -> float:
    """Exponential backoff with jitter to de-synchronise retries."""
    base = 2 ** attempt
    return base + random.uniform(0, base * 0.5)


_DEFERRED_TIMEOUT = 300

# Global semaphore: limits concurrent deferred-task threads across ALL
# pipeline types.  Imported from worker.py so pipeline_url, pipeline_lib,
# and pipeline share a single pool â€” prevents SQLite write storms when
# multiple individual downloads overlap in their deferred phases.
# (was per-module Semaphore(6); now shared Semaphore(3) in worker.py)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  PUBLIC API
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def dispatch_deferred(video_id: int, tasks: List[str], ws: ImportWorkspace) -> None:
    """Execute deferred tasks with parallel I/O, serialised DB writes.

    All I/O (image downloads, preview generation, AI calls) runs in
    parallel via a per-video ThreadPoolExecutor.  DB writes are
    serialised through ``_apply_lock`` (entity artwork) or rely on
    SQLite WAL busy_timeout + retry (other tasks).

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

    # Tasks that must complete BEFORE entity_artwork / kodi_export run,
    # because they can reassign entity IDs (e.g. AI correction).
    _PHASE1_TASKS = {"ai_enrichment"}

    def _coordinator():
        completed_tasks = []
        failed_tasks = []
        try:
            phase1 = [t for t in tasks if t in _PHASE1_TASKS]
            phase2 = [t for t in tasks if t not in _PHASE1_TASKS]

            # Phase 1: run entity-mutating tasks first (serialised)
            for task_name in phase1:
                fn = _DISPATCH.get(task_name)
                if fn:
                    try:
                        _run_safe(fn, video_id, ws, task_name)
                        completed_tasks.append(task_name)
                    except Exception:
                        failed_tasks.append(task_name)

            # Phase 2: remaining tasks in parallel
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
            except FuturesTimeoutError:
                timed_out = [n for f, n in futures.items() if not f.done()]
                for n in timed_out:
                    ws.log(f"Deferred '{n}' timed out after {_DEFERRED_TIMEOUT}s", level="error")
                    failed_tasks.append(n)
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
        finally:
            _update_child_step(ws.job_id, "Import complete")
            ws.sync_logs_to_db()
            ws.cleanup_on_success()

    threading.Thread(
        target=_coordinator, daemon=True, name=f"deferred-{video_id}",
    ).start()


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  HELPERS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _update_child_step(job_id: int, step: str, _max_retries: int = 5):
    """Update a child job's current_step to reflect deferred progress.

    Retries with exponential backoff on SQLite "database is locked" errors.
    """
    import random as _rand
    import time as _time
    from app.database import CosmeticSessionLocal
    from app.models import ProcessingJob
    for _attempt in range(_max_retries):
        db = CosmeticSessionLocal()
        try:
            job = db.query(ProcessingJob).get(job_id)
            if job:
                job.current_step = step
                db.commit()
            return
        except Exception as e:
            db.rollback()
            if "database is locked" in str(e) and _attempt < _max_retries - 1:
                delay = 1 + (2 ** _attempt) + _rand.uniform(0, 1)
                logger.warning(
                    f"Job {job_id}: DB locked updating step='{step}', "
                    f"retry {_attempt + 1}/{_max_retries} in {delay:.1f}s"
                )
                _time.sleep(delay)
            else:
                logger.error(
                    f"_update_child_step failed for job {job_id} "
                    f"(step={step}) after {_attempt + 1} attempts: {e}"
                )
                return
        finally:
            db.close()


def _run_safe(fn, video_id: int, ws: ImportWorkspace, task_name: str):
    """Run a deferred task with error handling, gated by global semaphore."""
    GLOBAL_DEFERRED_SLOTS.acquire()
    try:
        fn(video_id, ws)
        ws.log(f"Deferred '{task_name}' completed")
    except Exception as e:
        logger.error(
            f"Deferred task '{task_name}' failed for video {video_id}: {e}",
            exc_info=True,
        )
        ws.log(f"Deferred '{task_name}' FAILED: {e}", level="error")
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
    from app.pipeline_lib.services.preview_generator import generate_preview

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
    """Run scene analysis (ffmpeg + DB writes).  Uses busy_timeout + retry."""
    from app.database import SessionLocal

    for _attempt in range(_MAX_DB_RETRIES + 1):
        db = SessionLocal()
        try:
            from app.pipeline_lib.ai.scene_analysis import analyze_scenes
            analyze_scenes(db, video_id)
            _mark_processing_state(db, video_id, "scenes_analyzed", method="scene_analysis")
            _mark_processing_state(db, video_id, "thumbnail_selected", method="scene_analysis")
            db.commit()
            return
        except ImportError:
            return
        except Exception as e:
            db.rollback()
            if "database is locked" in str(e) and _attempt < _MAX_DB_RETRIES:
                delay = _retry_delay(_attempt)
                ws.log(
                    f"Scene analysis: DB lock, retry {_attempt + 1}/{_MAX_DB_RETRIES} in {delay}s",
                    level="warning",
                )
                time.sleep(delay)
            else:
                ws.log(f"Scene analysis: {e}", level="warning")
                return
        finally:
            db.close()


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
        from app.pipeline_lib.metadata.assets import download_entity_assets
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
                            from app.pipeline_lib.metadata.resolver import get_or_create_album as _goca_c
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
                            db.flush()
                            ws.log(f"MB completion: album entity "
                                   f"'{_mb_c['album']}' (id={_alb_ent.id})")
                        db.flush()
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
                    db.flush()
                    ws.log(f"Album entity id={_alb.id} corrected: "
                           f"rg={_a_rg_1d}, release={_a_ri_1d}")
            except Exception as e:
                ws.log(f"Album entity correction: {e}", level="warning")

        # Propagate mb_artist_id to artist entity when missing
        if item.mb_artist_id and item.artist_entity and not item.artist_entity.mb_artist_id:
            item.artist_entity.mb_artist_id = item.mb_artist_id
            try:
                db.flush()
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
                    db.flush()
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

            db.flush()
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
            from app.pipeline_lib.services.artwork_manager import process_artist_album_artwork
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

        # â”€â”€ 2b. Persist entity artwork to DB (under _apply_lock) â”€â”€â”€â”€â”€â”€
        from app.pipeline_lib.services.artwork_service import validate_file
        for _attempt in range(_MAX_DB_RETRIES + 1):
            try:
                with _apply_lock:
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
                            continue
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
                        # Remove ALL prior assets for this slot (pending,
                        # valid, invalid) so re-imports always reflect the
                        # freshly-downloaded file dimensions.
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
                ws.log("Entity artwork pipeline complete")
                break
            except Exception as e:
                db.rollback()
                if "database is locked" in str(e) and _attempt < _MAX_DB_RETRIES:
                    delay = _retry_delay(_attempt)
                    ws.log(f"Entity artwork DB: lock, retry {_attempt + 1}/{_MAX_DB_RETRIES} in {delay}s",
                           level="warning")
                    time.sleep(delay)
                else:
                    ws.log(f"Entity artwork pipeline: {e}", level="warning")
                    break

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
                with _apply_lock:
                    db.commit()
        except Exception as e:
            db.rollback()
            ws.log(f"2c entity image fallback: {e}", level="warning")

        # ── 3. Poster upgrade from CoverArtArchive ───────────────────
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
                _skip = bool(
                    _existing_poster
                    and _existing_poster.file_path
                    and os.path.isfile(_existing_poster.file_path)
                    and _existing_poster.provenance == "artwork_pipeline"
                    and _same_source
                )
                if _skip:
                    ws.log("Poster upgrade: existing poster already from same CoverArtArchive source — keeping it")
                else:
                    _video_poster_url = _caa_url
                    _video_poster_source = "single_cover"
                    ws.log(f"Using single cover art for video poster")


            if _video_poster_url and item.folder_path:
                from app.scraper.metadata_resolver import download_image
                from app.pipeline_lib.services.artwork_service import guarded_copy, validate_file

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
                    for _poster_attempt in range(_MAX_DB_RETRIES + 1):
                        try:
                            with _apply_lock:
                                for vp_type, vp_path in [("poster", poster_dst), ("thumb", thumb_dst)]:
                                    # Delete ALL existing records of this type â€” not
                                    # just pending.  The skip check above already
                                    # decided the upgrade should proceed, so stale
                                    # library_source (or other) records must go.
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
                                    with _apply_lock:
                                        db.query(MediaAsset).filter(
                                            MediaAsset.video_id == video_id,
                                            MediaAsset.asset_type == _atype,
                                        ).update({"file_path": _final})
                                        db.commit()
                                except Exception:
                                    pass  # Still valid at pending path
                            ws.log(f"Video poster upgraded from {_video_poster_source}")
                            _poster_upgraded = True
                            break
                        except Exception as e:
                            db.rollback()
                            if "database is locked" in str(e) and _poster_attempt < _MAX_DB_RETRIES:
                                delay = _retry_delay(_poster_attempt)
                                ws.log(f"Poster upgrade DB: lock, retry {_poster_attempt + 1}/{_MAX_DB_RETRIES} in {delay}s",
                                       level="warning")
                                time.sleep(delay)
                            else:
                                ws.log(f"Poster upgrade: {e}", level="warning")
                                break
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
                _thumb_asset = db.query(MediaAsset).filter(
                    MediaAsset.video_id == video_id,
                    MediaAsset.asset_type == "thumb",
                ).first()
                if _thumb_asset and _thumb_asset.file_path and os.path.isfile(_thumb_asset.file_path):
                    import shutil
                    from app.pipeline_lib.services.artwork_service import validate_file
                    from app.pipeline_lib.services.file_organizer import build_folder_name
                    _folder_name = os.path.basename(item.folder_path)
                    _poster_dst = os.path.join(item.folder_path, f"{_folder_name}-poster.jpg")
                    shutil.copy2(_thumb_asset.file_path, _poster_dst)
                    _vr = validate_file(_poster_dst) if os.path.isfile(_poster_dst) else None
                    with _apply_lock:
                        db.add(MediaAsset(
                            video_id=video_id, asset_type="poster",
                            file_path=_poster_dst, source_url=None,
                            provenance="thumb_fallback",
                            status="valid" if (_vr and _vr.valid) else "invalid",
                            width=_vr.width if _vr and _vr.valid else None,
                            height=_vr.height if _vr and _vr.valid else None,
                            file_size_bytes=_vr.file_size_bytes if _vr and _vr.valid else None,
                            file_hash=_vr.file_hash if _vr and _vr.valid else None,
                            last_validated_at=datetime.now(timezone.utc),
                        ))
                        db.commit()
                    ws.log("Poster fallback: created from video thumbnail")
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
    from app.models import Source

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
    primary_artist, _ = parse_multi_artist(artist)

    new_sources = []

    # Wikipedia artist
    try:
        from app.scraper.metadata_resolver import search_wikipedia_artist
        wa_url = search_wikipedia_artist(primary_artist)
        if wa_url:
            page_id = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", wa_url)
            new_sources.append(Source(
                video_id=video_id, provider="wikipedia",
                source_video_id=page_id, original_url=wa_url,
                canonical_url=wa_url, source_type="artist",
                provenance="ai",
            ))
    except Exception as e:
        ws.log(f"Source re-resolve: artist wiki: {e}", level="warning")

    # Wikipedia single/song
    try:
        from app.scraper.metadata_resolver import search_wikipedia
        ws_url = search_wikipedia(title, primary_artist)
        if ws_url:
            page_id = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", ws_url)
            new_sources.append(Source(
                video_id=video_id, provider="wikipedia",
                source_video_id=page_id, original_url=ws_url,
                canonical_url=ws_url, source_type="single",
                provenance="ai",
            ))
    except Exception as e:
        ws.log(f"Source re-resolve: single wiki: {e}", level="warning")

    # Wikipedia album
    _resolved_wiki_album = False
    if album:
        try:
            from app.scraper.metadata_resolver import search_wikipedia_album
            wl_url = search_wikipedia_album(primary_artist, album)
            if wl_url:
                page_id = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", wl_url)
                new_sources.append(Source(
                    video_id=video_id, provider="wikipedia",
                    source_video_id=page_id, original_url=wl_url,
                    canonical_url=wl_url, source_type="album",
                    provenance="ai",
                ))
                _resolved_wiki_album = True
        except Exception as e:
            ws.log(f"Source re-resolve: album wiki: {e}", level="warning")

    # Restore previous Wikipedia album source if re-resolution failed
    # (AI-modified album names with edition suffixes often break search)
    if not _resolved_wiki_album and _saved_wiki_album_url:
        page_id = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", _saved_wiki_album_url)
        new_sources.append(Source(
            video_id=video_id, provider="wikipedia",
            source_video_id=page_id, original_url=_saved_wiki_album_url,
            canonical_url=_saved_wiki_album_url, source_type="album",
            provenance="ai",
        ))

    # --- Wikipedia â†’ MusicBrainz cross-reference ---
    # Extract MB release-group IDs from Wikipedia pages to populate the
    # album entity's mb_release_group_id when it is missing.
    try:
        from app.scraper.metadata_resolver import extract_wiki_infobox_links

        _wiki_mb_album_rgs: list[str] = []
        _xr_ws_url = locals().get("ws_url")
        _xr_wl_url = locals().get("wl_url")

        if _xr_ws_url:
            _single_links = extract_wiki_infobox_links(_xr_ws_url)
            _s_rgs = _single_links.get("mb_release_group_ids", [])
            if _s_rgs:
                ws.log(f"Wikiâ†’MB cross-ref: single page RGs={_s_rgs}")

        if _xr_wl_url:
            _album_links = extract_wiki_infobox_links(_xr_wl_url)
            _wiki_mb_album_rgs = _album_links.get("mb_release_group_ids", [])
            if _wiki_mb_album_rgs:
                ws.log(f"Wikiâ†’MB cross-ref: album page RGs={_wiki_mb_album_rgs}")

        if _wiki_mb_album_rgs:
            from app.models import VideoItem as _VIxr
            _xr_item = db.query(_VIxr).get(video_id)
            if _xr_item and _xr_item.album_entity:
                _alb_ent_rg = getattr(_xr_item.album_entity, "mb_release_group_id", None)
                if not _alb_ent_rg:
                    _item_single_rg = getattr(_xr_item, "mb_release_group_id", None)
                    for _cand_rg in _wiki_mb_album_rgs:
                        if _cand_rg != _item_single_rg:
                            _xr_item.album_entity.mb_release_group_id = _cand_rg
                            db.flush()
                            ws.log(f"Wikiâ†’MB cross-ref: populated album entity "
                                   f"mb_release_group_id={_cand_rg}")
                            break
    except Exception as e:
        ws.log(f"Source re-resolve: wikiâ†’mb cross-ref: {e}", level="warning")

    # --- Wikipedia: album tracklist â†’ single fallback ---
    _xr_ws2 = locals().get("ws_url")
    _xr_wl2 = locals().get("wl_url") or _saved_wiki_album_url
    if not _xr_ws2 and _xr_wl2:
        try:
            from app.scraper.metadata_resolver import extract_single_wiki_url_from_album
            _tl_url = extract_single_wiki_url_from_album(_xr_wl2, title)
            if _tl_url:
                ws.log(f"Wikipedia fallback: single from album tracklist â†’ {_tl_url}")
                page_id = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", _tl_url)
                new_sources.append(Source(
                    video_id=video_id, provider="wikipedia",
                    source_video_id=page_id, original_url=_tl_url,
                    canonical_url=_tl_url, source_type="single",
                    provenance="ai",
                ))
        except Exception as e:
            ws.log(f"Source re-resolve: album tracklist fallback: {e}", level="warning")

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

        # Create musicbrainz/album from the album entity's release group
        if "album" not in _saved_mb_sources and item.album_entity:
            _album_rg = getattr(item.album_entity, "mb_release_group_id", None)
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

    from app.pipeline_lib.services.file_organizer import build_folder_name, sanitize_filename

    version_type = item.version_type or "normal"
    alt_label = item.alternate_version_label or ""
    resolution = item.resolution_label or "1080p"

    new_folder_name = build_folder_name(
        item.artist, item.title, resolution,
        version_type=version_type,
        alternate_version_label=alt_label,
    )

    old_folder = item.folder_path
    old_folder_name = os.path.basename(old_folder)

    if old_folder_name == new_folder_name:
        return  # already correct

    library_dir = os.path.dirname(old_folder)
    new_folder = os.path.join(library_dir, new_folder_name)

    # Rename the folder
    try:
        os.rename(old_folder, new_folder)
    except OSError as e:
        ws.log(f"File rename failed: {e}", level="warning")
        return

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
    db.flush()

    ws.log(f"Renamed: '{old_folder_name}' â†’ '{new_folder_name}'")


def _deferred_ai_enrichment(video_id: int, ws: ImportWorkspace) -> None:
    """Run AI metadata enrichment."""
    from app.database import SessionLocal
    from app.models import VideoItem

    for _attempt in range(_MAX_DB_RETRIES + 1):
        db = SessionLocal()
        try:
            try:
                # Snapshot identity before AI enrichment
                item_before = db.query(VideoItem).get(video_id)
                if not item_before:
                    return
                _old_artist = item_before.artist or ""
                _old_title = item_before.title or ""
                _old_album = item_before.album or ""

                from app.pipeline_lib.ai.metadata_service import enrich_video_metadata
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

                db.commit()
                return
            except ImportError:
                return
            except Exception as e:
                db.rollback()
                if "database is locked" in str(e) and _attempt < _MAX_DB_RETRIES:
                    delay = _retry_delay(_attempt)
                    ws.log(f"AI enrichment: DB lock, retry {_attempt + 1}/{_MAX_DB_RETRIES} in {delay}s",
                           level="warning")
                    time.sleep(delay)
                else:
                    ws.log(f"AI enrichment: {e}", level="warning")
                    return
        finally:
            db.close()


def _deferred_matching(video_id: int, ws: ImportWorkspace) -> None:
    """Run matching resolution for the video."""
    from app.database import SessionLocal

    for _attempt in range(_MAX_DB_RETRIES + 1):
        db = SessionLocal()
        try:
            from app.pipeline_lib.matching.resolver import resolve_video
            resolve_video(db, video_id)
            db.commit()
            return
        except ImportError:
            return
        except Exception as e:
            db.rollback()
            if "database is locked" in str(e) and _attempt < _MAX_DB_RETRIES:
                delay = _retry_delay(_attempt)
                ws.log(f"Matching resolution: DB lock, retry {_attempt + 1}/{_MAX_DB_RETRIES} in {delay}s",
                       level="warning")
                time.sleep(delay)
            else:
                ws.log(f"Matching resolution: {e}", level="warning")
                return
        finally:
            db.close()


def _deferred_orphan_cleanup(video_id: int, ws: ImportWorkspace) -> None:
    """Clean up orphaned entities after import."""
    from app.database import SessionLocal
    from app.models import VideoItem
    from app.metadata.models import ArtistEntity, AlbumEntity

    for _attempt in range(_MAX_DB_RETRIES + 1):
        db = SessionLocal()
        try:
            # Remove album entities with zero linked videos
            orphan_albums = (
                db.query(AlbumEntity)
                .filter(~db.query(VideoItem).filter(VideoItem.album_entity_id == AlbumEntity.id).exists())
                .all()
            )
            for orphan in orphan_albums:
                ws.log(f"Removing orphan AlbumEntity: {orphan.title}")
                db.delete(orphan)

            # Remove artist entities with zero linked videos
            orphan_artists = (
                db.query(ArtistEntity)
                .filter(~db.query(VideoItem).filter(VideoItem.artist_entity_id == ArtistEntity.id).exists())
                .all()
            )
            for orphan in orphan_artists:
                ws.log(f"Removing orphan ArtistEntity: {orphan.canonical_name}")
                db.delete(orphan)

            db.commit()
            return
        except Exception as e:
            db.rollback()
            if "database is locked" in str(e) and _attempt < _MAX_DB_RETRIES:
                delay = _retry_delay(_attempt)
                ws.log(f"Orphan cleanup: DB lock, retry {_attempt + 1}/{_MAX_DB_RETRIES} in {delay}s",
                       level="warning")
                time.sleep(delay)
            else:
                ws.log(f"Orphan cleanup error: {e}", level="warning")
                return
        finally:
            db.close()


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
