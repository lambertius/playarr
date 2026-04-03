"""
Playarr — Music Video Manager
Main FastAPI application entry point.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.database import engine, Base
from app.config import get_settings
from app.version import APP_VERSION
from app.routers import library, jobs, playback, settings as settings_router
from app.routers import metadata as metadata_router
from app.routers import resolve as resolve_router
from app.routers import ai as ai_router
from app.routers import artwork as artwork_router
from app.routers import library_import as library_import_router
from app.routers import playlists as playlists_router
from app.routers import video_editor as video_editor_router
from app.routers import scraper_test as scraper_test_router
from app.routers import tmvdb as tmvdb_router
from app.new_videos import router as new_videos_router


def _apply_schema_upgrades(eng):
    """Add columns that don't exist yet (SQLite create_all won't alter existing tables)."""
    from sqlalchemy import text, inspect
    insp = inspect(eng)
    if "processing_jobs" not in insp.get_table_names():
        return  # Fresh install — create_all already handles it
    cols = {c["name"] for c in insp.get_columns("processing_jobs")}
    with eng.begin() as conn:
        if "display_name" not in cols:
            conn.execute(text("ALTER TABLE processing_jobs ADD COLUMN display_name VARCHAR(500)"))
        if "pipeline_steps" not in cols:
            conn.execute(text("ALTER TABLE processing_jobs ADD COLUMN pipeline_steps JSON"))
        if "action_label" not in cols:
            conn.execute(text("ALTER TABLE processing_jobs ADD COLUMN action_label VARCHAR(200)"))

    # VideoItem entity FK columns
    if "video_items" in insp.get_table_names():
        vi_cols = {c["name"] for c in insp.get_columns("video_items")}
        with eng.begin() as conn:
            if "artist_entity_id" not in vi_cols:
                conn.execute(text("ALTER TABLE video_items ADD COLUMN artist_entity_id INTEGER REFERENCES artists(id)"))
            if "album_entity_id" not in vi_cols:
                conn.execute(text("ALTER TABLE video_items ADD COLUMN album_entity_id INTEGER REFERENCES albums(id)"))
            if "track_id" not in vi_cols:
                conn.execute(text("ALTER TABLE video_items ADD COLUMN track_id INTEGER REFERENCES tracks(id)"))
            # Version detection columns
            if "version_type" not in vi_cols:
                conn.execute(text("ALTER TABLE video_items ADD COLUMN version_type VARCHAR(20) DEFAULT 'normal'"))
            if "alternate_version_label" not in vi_cols:
                conn.execute(text("ALTER TABLE video_items ADD COLUMN alternate_version_label VARCHAR(200)"))
            if "original_artist" not in vi_cols:
                conn.execute(text("ALTER TABLE video_items ADD COLUMN original_artist VARCHAR(500)"))
            if "original_title" not in vi_cols:
                conn.execute(text("ALTER TABLE video_items ADD COLUMN original_title VARCHAR(500)"))
            if "related_versions" not in vi_cols:
                conn.execute(text("ALTER TABLE video_items ADD COLUMN related_versions JSON"))
            if "review_status" not in vi_cols:
                conn.execute(text("ALTER TABLE video_items ADD COLUMN review_status VARCHAR(30) DEFAULT 'none'"))
            if "review_reason" not in vi_cols:
                conn.execute(text("ALTER TABLE video_items ADD COLUMN review_reason VARCHAR(500)"))
            if "review_category" not in vi_cols:
                conn.execute(text("ALTER TABLE video_items ADD COLUMN review_category VARCHAR(40)"))
            if "processing_state" not in vi_cols:
                conn.execute(text("ALTER TABLE video_items ADD COLUMN processing_state JSON"))

    # Matching subsystem tables — created by Base.metadata.create_all but
    # we need to ensure they exist for older DBs upgraded in-place.
    existing_tables = set(insp.get_table_names())
    matching_tables = ["match_results", "match_candidates", "normalization_results", "user_pinned_matches"]
    if any(t not in existing_tables for t in matching_tables):
        from app.matching.models import MatchResult, MatchCandidate, NormalizationResult, UserPinnedMatch  # noqa: F811
        Base.metadata.create_all(bind=eng, tables=[
            MatchResult.__table__, MatchCandidate.__table__,
            NormalizationResult.__table__, UserPinnedMatch.__table__,
        ])

    # AI subsystem tables
    ai_tables = ["ai_metadata_results", "ai_scene_analyses", "ai_thumbnails"]
    if any(t not in existing_tables for t in ai_tables):
        from app.ai.models import AIMetadataResult as AIMetaModel, AISceneAnalysis as AISceneModel, AIThumbnail as AIThumbModel  # noqa: F811
        Base.metadata.create_all(bind=eng, tables=[
            AIMetaModel.__table__, AISceneModel.__table__, AIThumbModel.__table__,
        ])

    # New Videos / recommendation subsystem tables
    nv_tables = ["suggested_videos", "suggested_video_dismissals", "suggested_video_cart_items",
                 "recommendation_snapshots", "recommendation_feedback"]
    if any(t not in existing_tables for t in nv_tables):
        from app.new_videos.models import (
            SuggestedVideo as _SV, SuggestedVideoDismissal as _SVD,
            SuggestedVideoCartItem as _SVC, RecommendationSnapshot as _RS,
            RecommendationFeedback as _RF,
        )
        Base.metadata.create_all(bind=eng, tables=[
            _SV.__table__, _SVD.__table__, _SVC.__table__,
            _RS.__table__, _RF.__table__,
        ])

    # Source provenance column
    if "sources" in existing_tables:
        src_cols = {c["name"] for c in insp.get_columns("sources")}
        if "provenance" not in src_cols:
            with eng.begin() as conn:
                conn.execute(text("ALTER TABLE sources ADD COLUMN provenance VARCHAR(50)"))

    # AI metadata results — new columns added for enhanced enrichment
    if "ai_metadata_results" in insp.get_table_names():
        ai_cols = {c["name"] for c in insp.get_columns("ai_metadata_results")}
        new_ai_cols = {
            "ai_director": "VARCHAR(500)",
            "ai_studio": "VARCHAR(500)",
            "ai_actors": "JSON",
            "ai_tags": "JSON",
            "verification_status": "BOOLEAN",
            "requested_fields": "JSON",
            "mismatch_score": "FLOAT",
            "mismatch_signals": "JSON",
            "fingerprint_result": "JSON",
            "model_task": "VARCHAR(100)",
            "change_summary": "TEXT",
            "dismissed_at": "DATETIME",
        }
        with eng.begin() as conn:
            for col_name, col_type in new_ai_cols.items():
                if col_name not in ai_cols:
                    conn.execute(text(f"ALTER TABLE ai_metadata_results ADD COLUMN {col_name} {col_type}"))

    # Migrate legacy AI settings: rename "openai_gpt4o" → "openai" etc.
    _migrate_ai_settings(eng)

    # "recording" is now a valid source_type — no longer coerce to "single".

    # Album entity — add cover_image column
    if "albums" in existing_tables:
        alb_cols = {c["name"] for c in insp.get_columns("albums")}
        if "cover_image" not in alb_cols:
            with eng.begin() as conn:
                conn.execute(text("ALTER TABLE albums ADD COLUMN cover_image VARCHAR(1000)"))

    # Asset validity & provenance columns (artwork pipeline hardening)
    if "cached_assets" in existing_tables:
        ca_cols = {c["name"] for c in insp.get_columns("cached_assets")}
        _new_ca = {
            "status": "VARCHAR(20) DEFAULT 'valid'",
            "content_type": "VARCHAR(100)",
            "source_provider": "VARCHAR(100)",
            "resolved_url": "VARCHAR(2000)",
            "validation_error": "TEXT",
            "last_validated_at": "DATETIME",
            "file_hash": "VARCHAR(64)",
        }
        with eng.begin() as conn:
            for col_name, col_type in _new_ca.items():
                if col_name not in ca_cols:
                    conn.execute(text(f"ALTER TABLE cached_assets ADD COLUMN {col_name} {col_type}"))

    if "media_assets" in existing_tables:
        ma_cols = {c["name"] for c in insp.get_columns("media_assets")}
        _new_ma = {
            "status": "VARCHAR(20) DEFAULT 'valid'",
            "content_type": "VARCHAR(100)",
            "source_provider": "VARCHAR(100)",
            "resolved_url": "VARCHAR(2000)",
            "validation_error": "TEXT",
            "last_validated_at": "DATETIME",
            "file_hash": "VARCHAR(64)",
            "width": "INTEGER",
            "height": "INTEGER",
            "file_size_bytes": "INTEGER",
        }
        with eng.begin() as conn:
            for col_name, col_type in _new_ma.items():
                if col_name not in ma_cols:
                    conn.execute(text(f"ALTER TABLE media_assets ADD COLUMN {col_name} {col_type}"))

    # Genre blacklist column
    if "genres" in existing_tables:
        genre_cols = {c["name"] for c in insp.get_columns("genres")}
        if "blacklisted" not in genre_cols:
            with eng.begin() as conn:
                conn.execute(text("ALTER TABLE genres ADD COLUMN blacklisted BOOLEAN DEFAULT 0"))

    # Field-level provenance columns — track which provider sourced each metadata field
    _provenance_tables = {
        "video_items": "video_items",
        "artists": "artists",
        "albums": "albums",
        "tracks": "tracks",
    }
    for tbl_name in _provenance_tables.values():
        if tbl_name in existing_tables:
            tbl_cols = {c["name"] for c in insp.get_columns(tbl_name)}
            if "field_provenance" not in tbl_cols:
                with eng.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {tbl_name} ADD COLUMN field_provenance JSON"))


def _migrate_ai_settings(eng):
    """
    One-time migration for legacy AI provider/model settings.

    Fixes:
    - provider values like "openai_gpt4o" → "openai"
    - Validates stored model IDs against the current catalog
    - Resets invalid models to provider defaults
    """
    from sqlalchemy.orm import Session as SASession
    from app.models import AppSetting
    from app.ai.model_catalog import validate_model_id, get_default_model

    PROVIDER_MIGRATIONS = {
        "openai_gpt4o": "openai",
        "openai_gpt4": "openai",
        "gemini_flash": "gemini",
        "gemini_pro": "gemini",
        "claude_sonnet": "claude",
        "claude_haiku": "claude",
    }

    with SASession(eng) as db:
        # Migrate provider name
        provider_setting = db.query(AppSetting).filter(
            AppSetting.key == "ai_provider",
            AppSetting.user_id.is_(None),
        ).first()

        if provider_setting and provider_setting.value in PROVIDER_MIGRATIONS:
            old = provider_setting.value
            provider_setting.value = PROVIDER_MIGRATIONS[old]
            db.commit()

        # Validate stored model IDs
        provider_name = provider_setting.value if provider_setting else None
        if provider_name and provider_name not in ("none", ""):
            model_keys = [
                "ai_model_default", "ai_model_fallback",
                "ai_model_metadata", "ai_model_verification", "ai_model_scene",
            ]
            rows = db.query(AppSetting).filter(
                AppSetting.key.in_(model_keys),
                AppSetting.user_id.is_(None),
            ).all()
            for row in rows:
                if row.value and not validate_model_id(provider_name, row.value):
                    default = get_default_model(provider_name)
                    row.value = default
            db.commit()



# Set up logging — console + persistent file
_log_fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Console handler (always present)
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_log_fmt)

# Root logger
logging.basicConfig(level=logging.INFO, handlers=[_console_handler])
logger = logging.getLogger("playarr")


def _setup_file_logging():
    """Add a RotatingFileHandler that persists across restarts.

    Called during lifespan startup so that settings (log_dir) are resolved.
    """
    from logging.handlers import RotatingFileHandler

    s = get_settings()
    log_file = os.path.join(s.log_dir, "playarr.log")
    os.makedirs(s.log_dir, exist_ok=True)

    fh = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB per file
        backupCount=5,               # keep 5 rotated files
        encoding="utf-8",
    )
    fh.setFormatter(_log_fmt)
    fh.setLevel(logging.INFO)

    # Attach to the root logger so all modules' log calls are captured
    logging.getLogger().addHandler(fh)

    # Also create jobs/ subdirectory for per-job logs
    os.makedirs(os.path.join(s.log_dir, "jobs"), exist_ok=True)

    logger.info(f"File logging enabled: {log_file}")


def _cleanup_stale_jobs():
    """Mark orphaned in-progress jobs as failed on startup.

    When the server restarts, any background-thread tasks that were running
    are killed.  Their ProcessingJob records stay in active status forever
    (zombie jobs).  This marks them as failed so the UI doesn't show
    perpetual spinners.
    """
    from sqlalchemy.orm import Session as SASession
    from app.models import ProcessingJob, JobStatus
    from datetime import datetime, timedelta, timezone

    ACTIVE_STATUSES = [
        JobStatus.queued, JobStatus.downloading, JobStatus.downloaded,
        JobStatus.remuxing, JobStatus.analyzing, JobStatus.normalizing,
        JobStatus.tagging, JobStatus.writing_nfo, JobStatus.asset_fetch,
    ]

    with SASession(engine) as db:
        stale = db.query(ProcessingJob).filter(
            ProcessingJob.status.in_(ACTIVE_STATUSES)
        ).all()
        if stale:
            for job in stale:
                job.status = JobStatus.failed
                job.error_message = "Server restarted while job was running"
                job.completed_at = datetime.now(timezone.utc)
                current_log = job.log_text or ""
                timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
                job.log_text = f"{current_log}[{timestamp}] Job marked as failed — server restarted while running\n"
            db.commit()
            logger.info(f"Marked {len(stale)} orphaned job(s) as failed on startup")

        # Purge completed/failed/cancelled jobs whose video was deleted
        # (video_id=NULL means the parent VideoItem no longer exists).
        # Only purge jobs completed >1 hour ago so that recently-failed
        # jobs (e.g. from a server restart) remain visible in the queue.
        _purge_cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        orphan_jobs = db.query(ProcessingJob).filter(
            ProcessingJob.video_id.is_(None),
            ProcessingJob.status.in_([
                JobStatus.complete, JobStatus.failed, JobStatus.cancelled,
                JobStatus.skipped,
            ]),
            ProcessingJob.completed_at < _purge_cutoff,
        ).all()
        if orphan_jobs:
            for job in orphan_jobs:
                db.delete(job)
            db.commit()
            logger.info(f"Purged {len(orphan_jobs)} orphan processing job(s)")

        # Fix completed jobs stuck in "Finalizing" state.
        # This happens when the deferred-task daemon thread was killed by a
        # server restart before it could update current_step to "Import complete".
        # Applies to import and rescan jobs (which use deferred tasks).
        stuck_finalizing = db.query(ProcessingJob).filter(
            ProcessingJob.status == JobStatus.complete,
            ProcessingJob.job_type.in_(["import", "rescan"]),
            ProcessingJob.current_step != "Import complete",
            ProcessingJob.current_step.isnot(None),
        ).all()
        if stuck_finalizing:
            for job in stuck_finalizing:
                job.current_step = "Import complete"
                job.progress_percent = 100
            db.commit()
            logger.info(f"Fixed {len(stuck_finalizing)} job(s) stuck in Finalizing state")


def _normalize_path_for_compare(p: str) -> str:
    """Normalize a path for comparison, stripping trailing dots from each component.

    On Windows, the filesystem silently strips trailing dots from directory
    names (e.g. ``Andrew W.K.`` and ``Andrew W.K`` resolve to the same dir).
    ``os.path.normpath`` does NOT strip these, so we do it manually.
    """
    p = os.path.normcase(os.path.normpath(p))
    if os.name == "nt":
        parts = p.split(os.sep)
        parts = [part.rstrip(".") if part else part for part in parts]
        p = os.sep.join(parts)
    return p


def _detect_untracked_library_files():
    """Log warnings for video files in the library not tracked by any VideoItem.

    Runs after directory settings are hydrated so all library paths are current.
    This is a read-only safety net — it does NOT modify anything, just alerts the
    operator so they can use Scan Library / Clean Library to fix things.
    """
    from sqlalchemy.orm import Session as SASession
    from app.models import VideoItem

    settings = get_settings()
    video_extensions = {".mkv", ".mp4", ".webm", ".avi", ".mov", ".mpg"}

    with SASession(engine) as db:
        tracked = set()
        for (fp,) in db.query(VideoItem.file_path).all():
            if fp:
                tracked.add(_normalize_path_for_compare(fp))

    untracked = []
    for library_dir in settings.get_all_library_dirs():
        if not os.path.isdir(library_dir):
            continue
        for root, dirs, files in os.walk(library_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".") and not d.startswith("_")]
            for fname in files:
                if os.path.splitext(fname)[1].lower() in video_extensions:
                    full = os.path.join(root, fname)
                    if _normalize_path_for_compare(full) not in tracked:
                        untracked.append(full)

    if untracked:
        logger.warning(
            f"Found {len(untracked)} untracked video file(s) in library — "
            "use Settings → Library → Scan Library to import them:"
        )
        for path in untracked:
            logger.warning(f"  Untracked: {path}")


def _purge_orphan_workspaces(engine):
    """Remove workspace directories whose job IDs no longer exist in the DB.

    Workspace directories are named ``import_{job_id}``.  When a job record is
    deleted from ``processing_jobs``, the workspace survives on disk.  If SQLite
    later recycles the ID, the stale workspace can poison the new job's pipeline
    (all stages appear complete, the stale mutation plan gets applied, etc.).

    This function is called once at startup and removes any workspace whose
    numeric ID doesn't match a current processing_jobs row.
    """
    import re
    import shutil
    from sqlalchemy.orm import Session as SASession
    from app.models import ProcessingJob

    ws_root = str(get_settings().workspace_dir)
    if not os.path.isdir(ws_root):
        return

    with SASession(engine) as db:
        active_ids = {row[0] for row in db.query(ProcessingJob.id).all()}

    _id_pattern = re.compile(r"^import_(\d+)$")
    removed = 0
    for entry in os.listdir(ws_root):
        m = _id_pattern.match(entry)
        if not m:
            continue
        ws_job_id = int(m.group(1))
        if ws_job_id not in active_ids:
            ws_path = os.path.join(ws_root, entry)
            try:
                shutil.rmtree(ws_path, ignore_errors=True)
                removed += 1
            except Exception:
                pass
    if removed:
        logger.info(f"Purged {removed} orphan workspace(s)")


# Max age (seconds) before a "Finalizing" job is considered stuck.
# Deferred timeout is 300s; allow generous margin.
_FINALIZING_WATCHDOG_MAX_AGE = 600  # 10 minutes
_FINALIZING_WATCHDOG_INTERVAL = 120  # check every 2 minutes


async def _finalizing_watchdog():
    """Periodically unstick jobs whose deferred threads died silently."""
    from sqlalchemy.orm import Session as SASession
    from app.models import ProcessingJob, JobStatus

    logger.info("Finalizing watchdog started (interval=%ds, max_age=%ds)",
                _FINALIZING_WATCHDOG_INTERVAL, _FINALIZING_WATCHDOG_MAX_AGE)

    while True:
        await asyncio.sleep(_FINALIZING_WATCHDOG_INTERVAL)
        try:
            # Use naive UTC to match the naive datetimes stored by SQLite
            cutoff = datetime.utcnow() - timedelta(seconds=_FINALIZING_WATCHDOG_MAX_AGE)
            with SASession(engine) as db:
                stuck = db.query(ProcessingJob).filter(
                    ProcessingJob.status == JobStatus.complete,
                    ProcessingJob.job_type.in_(["import", "rescan", "batch_rescan", "batch_import", "library_import"]),
                    ProcessingJob.current_step != "Import complete",
                    ProcessingJob.current_step.isnot(None),
                    ProcessingJob.completed_at < cutoff,
                ).all()
                if stuck:
                    for job in stuck:
                        logger.info("Watchdog: unsticking job #%d (step=%s, completed_at=%s)",
                                    job.id, job.current_step, job.completed_at)
                        job.current_step = "Import complete"
                        job.progress_percent = 100
                    db.commit()
                    logger.info(f"Watchdog: fixed {len(stuck)} job(s) stuck in Finalizing state")
        except Exception:
            logger.error("Finalizing watchdog cycle error", exc_info=True)


def _cleanup_fk_violations(eng):
    """Remove orphaned M2M association rows that violate FK constraints.

    These accumulate when FK enforcement was previously OFF (e.g., before
    PRAGMA foreign_keys=ON was added).  Without cleanup, creating new
    entities that reuse autoincrement IDs can trigger IntegrityErrors.
    """
    from sqlalchemy import text

    # (child_table, child_fk_col, parent_table)
    checks = [
        ("artist_genres", "artist_id", "artists"),
        ("artist_genres", "genre_id", "genres"),
        ("album_genres", "album_id", "albums"),
        ("album_genres", "genre_id", "genres"),
        ("track_genres", "track_id", "tracks"),
        ("track_genres", "genre_id", "genres"),
        ("video_genres", "video_id", "video_items"),
        ("video_genres", "genre_id", "genres"),
    ]

    total = 0
    with eng.begin() as conn:
        # Must disable FK enforcement to delete orphaned rows that reference
        # non-existent parents (otherwise DELETE itself may be blocked).
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        for child, fk_col, parent in checks:
            try:
                r = conn.execute(text(
                    f"DELETE FROM {child} WHERE {fk_col} NOT IN (SELECT id FROM {parent})"
                ))
                if r.rowcount:
                    total += r.rowcount
                    logger.info(f"FK cleanup: removed {r.rowcount} orphaned rows from {child} ({fk_col})")
            except Exception:
                pass  # table may not exist on fresh installs
        conn.execute(text("PRAGMA foreign_keys=ON"))

    if total:
        logger.info(f"FK cleanup: {total} total orphaned association rows removed")


def _run_startup_artwork_repair(mode: str):
    """Run automatic artwork cache repair on startup.

    Modes:
        off   — skip entirely (no disk I/O)
        light — validate only assets marked suspicious (status != "valid")
                or with no last_validated_at.  Fast on large caches.
        full  — validate every cached asset and re-download invalid ones.
                Thorough but slower; best for first run after upgrading.
    """
    if mode == "off":
        logger.info("Startup artwork repair: disabled (startup_repair_mode=off)")
        return

    from app.database import SessionLocal
    from app.metadata.models import CachedAsset
    from app.services.artwork_service import (
        repair_cached_assets,
        validate_file,
        _safe_delete,
    )

    db = SessionLocal()
    try:
        if mode == "light":
            # Only check assets that look suspicious
            suspects = db.query(CachedAsset).filter(
                (CachedAsset.status != "valid")
                | (CachedAsset.last_validated_at.is_(None))
            ).all()
            if not suspects:
                logger.info("Startup artwork repair (light): no suspicious assets found")
                db.close()
                return
            logger.info(f"Startup artwork repair (light): checking {len(suspects)} suspicious assets...")
            repaired = 0
            for asset in suspects:
                try:
                    if not asset.local_cache_path or not os.path.isfile(asset.local_cache_path):
                        asset.status = "missing"
                        asset.validation_error = "File not found on disk"
                        asset.last_validated_at = datetime.now(timezone.utc)
                        repaired += 1
                        continue
                    vr = validate_file(asset.local_cache_path)
                    if vr.valid:
                        asset.status = "valid"
                        asset.width = vr.width
                        asset.height = vr.height
                        asset.file_size_bytes = vr.file_size_bytes
                        asset.file_hash = vr.file_hash
                        asset.validation_error = None
                        asset.last_validated_at = datetime.now(timezone.utc)
                    else:
                        logger.warning(f"Startup repair: purging corrupt asset {asset.local_cache_path} ({vr.error})")
                        _safe_delete(asset.local_cache_path)
                        asset.status = "invalid"
                        asset.validation_error = vr.error
                        asset.last_validated_at = datetime.now(timezone.utc)
                        repaired += 1
                except Exception as e:
                    logger.warning(f"Startup repair: error checking asset {asset.id}: {e}")
            db.commit()
            logger.info(f"Startup artwork repair (light): checked {len(suspects)}, repaired {repaired}")

        elif mode == "full":
            logger.info("Startup artwork repair (full): scanning all cached assets...")
            report = repair_cached_assets(db, refetch=True, log_callback=logger.info)
            db.commit()
            logger.info(
                f"Startup artwork repair (full): {report.valid} valid, "
                f"{report.invalid} invalid, {report.missing} missing, "
                f"{report.deleted} deleted, {report.refetched} refetched"
            )
    except Exception as e:
        logger.error(f"Startup artwork repair failed (non-fatal): {e}")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


def _purge_orphan_cached_assets():
    """Remove CachedAsset records + files that reference entities no longer in the DB."""
    try:
        from app.database import SessionLocal
        from app.metadata.models import CachedAsset, ArtistEntity, AlbumEntity
        from app.models import VideoItem
        from app.services.artwork_service import _safe_delete

        db = SessionLocal()
        try:
            valid_artist_ids = {r[0] for r in db.query(ArtistEntity.id).all()}
            valid_album_ids = {r[0] for r in db.query(AlbumEntity.id).all()}
            valid_video_ids = {r[0] for r in db.query(VideoItem.id).all()}

            entity_lookup = {
                "artist": valid_artist_ids,
                "album": valid_album_ids,
                "video": valid_video_ids,
            }

            orphans = []
            for asset in db.query(CachedAsset).all():
                valid_ids = entity_lookup.get(asset.entity_type)
                if valid_ids is None:
                    continue  # unknown entity type — leave alone
                if asset.entity_id not in valid_ids:
                    orphans.append(asset)

            for asset in orphans:
                if asset.local_cache_path and os.path.isfile(asset.local_cache_path):
                    _safe_delete(asset.local_cache_path)
                db.delete(asset)

            if orphans:
                db.commit()
                logger.info(f"Purged {len(orphans)} orphan cached asset(s)")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Orphan cached asset cleanup failed: {e}")


def _purge_orphan_previews():
    """Remove preview files whose video IDs no longer exist or whose basename
    doesn't match the current video (handles ID recycling)."""
    try:
        from app.database import SessionLocal
        from app.models import VideoItem
        from app.services.preview_generator import purge_orphan_previews

        db = SessionLocal()
        try:
            basenames = {}
            for vid, fpath in db.query(VideoItem.id, VideoItem.file_path).all():
                if fpath:
                    basenames[vid] = os.path.splitext(os.path.basename(fpath))[0]
            purge_orphan_previews(basenames)
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Orphan preview cleanup failed: {e}")


def _hydrate_dir_settings_from_db(settings):
    """Load directory settings from the DB and apply them to the config singleton.

    This ensures user-configured paths (library_dir, archive_dir, etc.)
    survive server restarts instead of reverting to .env / defaults.
    """
    from app.database import SessionLocal
    from app.models import AppSetting

    _dir_keys = {"library_dir", "archive_dir", "library_source_dirs"}
    db = SessionLocal()
    try:
        rows = db.query(AppSetting).filter(
            AppSetting.key.in_(_dir_keys),
            AppSetting.user_id.is_(None),
        ).all()
        for row in rows:
            old = getattr(settings, row.key, None)
            setattr(settings, row.key, row.value)
            if old != row.value:
                logger.info(f"Settings: {row.key} overridden from DB: {row.value}")
    except Exception as e:
        logger.warning(f"Failed to hydrate dir settings from DB: {e}")
    finally:
        db.close()


def _stamp_db_version(eng):
    """
    Write the current APP_VERSION into the DB and warn if the database
    was last accessed by a newer version of Playarr.
    """
    from sqlalchemy.orm import Session as SASession
    from app.models import AppSetting

    with SASession(eng) as db:
        row = db.query(AppSetting).filter(
            AppSetting.key == "schema_version",
            AppSetting.user_id.is_(None),
        ).first()

        if row:
            db_version = row.value
            if _version_tuple(db_version) > _version_tuple(APP_VERSION):
                logger.warning(
                    f"DATABASE VERSION MISMATCH: DB was last used by Playarr v{db_version}, "
                    f"but this is v{APP_VERSION}. Data may have been written by a newer release. "
                    f"Upgrade Playarr to avoid potential issues."
                )
            row.value = APP_VERSION
        else:
            db.add(AppSetting(
                user_id=None,
                key="schema_version",
                value=APP_VERSION,
                value_type="string",
            ))
        db.commit()
    logger.info(f"Playarr v{APP_VERSION}")


def _version_tuple(v: str):
    """Parse '1.2.3' into (1, 2, 3) for comparison."""
    try:
        return tuple(int(x) for x in v.split("."))
    except (ValueError, AttributeError):
        return (0, 0, 0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown lifecycle."""
    # Startup
    from app.runtime_dirs import get_runtime_dirs, IS_DEV
    rdirs = get_runtime_dirs()

    mode_label = "DEVELOPMENT" if IS_DEV else "PRODUCTION"
    logger.info(f"Playarr starting up... (mode={mode_label})")

    s = get_settings()
    s.ensure_directories()

    # Enable persistent file logging now that directories exist
    _setup_file_logging()

    # Log runtime paths for diagnostics
    logger.info(f"  DB:       {rdirs.db_path}")
    logger.info(f"  Data:     {rdirs.data_dir}")
    logger.info(f"  Logs:     {rdirs.log_dir}")
    logger.info(f"  Cache:    {rdirs.cache_dir}")

    # First-run detection
    _is_first_run = not rdirs.db_path.exists()
    if _is_first_run:
        logger.info("First run detected — initializing database...")

    # Create tables (dev convenience; production uses Alembic)
    Base.metadata.create_all(bind=engine)

    # Add new columns to existing tables (SQLite doesn't do this via create_all)
    _apply_schema_upgrades(engine)

    # Stamp the current application version into the DB and check for
    # forward-compatibility (newer DB accessed by older app).
    _stamp_db_version(engine)

    # Mark orphaned in-progress jobs as failed (their threads died on restart)
    _cleanup_stale_jobs()

    # Purge workspace directories for job IDs that no longer exist in the DB
    _purge_orphan_workspaces(engine)

    # Clean orphaned association rows that violate FK constraints
    # (can accumulate when FK enforcement was previously OFF)
    _cleanup_fk_violations(engine)

    # Hydrate directory settings from DB so user-configured paths survive restarts
    _hydrate_dir_settings_from_db(s)
    s.ensure_directories()

    # Warn about video files in the library that aren't tracked in the DB
    _detect_untracked_library_files()

    # --- Automatic artwork cache repair ---
    # Purge old corrupt cached assets (HTML-as-jpg, zero-byte, etc.) so they
    # are not reused by imports, rescans, or entity re-resolution.
    _run_startup_artwork_repair(s.startup_repair_mode)

    # --- Purge orphan cached assets ---
    # CachedAssets referencing entities that no longer exist (e.g. after
    # all videos were deleted but entity cleanup was incomplete).
    _purge_orphan_cached_assets()

    # --- Purge orphan preview files ---
    # Preview files persist in the preview cache after videos are deleted.
    # Clean up any previews referencing video IDs that no longer exist.
    _purge_orphan_previews()

    logger.info(f"Library dir: {s.library_dir}")
    extra_dirs = s.get_all_library_dirs()[1:]
    if extra_dirs:
        logger.info(f"Additional source dirs: {extra_dirs}")
    logger.info(f"Archive dir: {s.archive_dir}")

    # Start background watchdog for stuck Finalizing jobs
    watchdog_task = asyncio.create_task(_finalizing_watchdog())

    yield

    # Shutdown
    watchdog_task.cancel()
    logger.info("Playarr shutting down.")


app = FastAPI(
    title="Playarr",
    description="Music Video Manager — Download, organize, normalize, and browse your music video library.",
    version=APP_VERSION,
    lifespan=lifespan,
)

# CORS for frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(library.router)
app.include_router(jobs.router)
app.include_router(playback.router)
app.include_router(settings_router.router)
app.include_router(metadata_router.router)
app.include_router(resolve_router.resolve_router)
app.include_router(resolve_router.review_router)
app.include_router(resolve_router.search_router)
app.include_router(resolve_router.export_router)
app.include_router(ai_router.router)
app.include_router(artwork_router.router)
app.include_router(library_import_router.router)
app.include_router(playlists_router.router)
app.include_router(video_editor_router.router)
app.include_router(scraper_test_router.router)
app.include_router(new_videos_router.router)
app.include_router(tmvdb_router.router)


@app.get("/api/health")
def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "playarr", "version": APP_VERSION}


@app.get("/api/version")
def get_version():
    """Return current app version and DB schema version."""
    from app.database import SessionLocal
    from app.models import AppSetting

    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(
            AppSetting.key == "schema_version",
            AppSetting.user_id.is_(None),
        ).first()
        db_version = row.value if row else APP_VERSION
        mismatch = _version_tuple(db_version) > _version_tuple(APP_VERSION)
        return {
            "app_version": APP_VERSION,
            "db_version": db_version,
            "version_mismatch": mismatch,
        }
    finally:
        db.close()


@app.get("/api/stats")
def get_stats():
    """Quick library statistics."""
    from app.database import SessionLocal
    from app.models import VideoItem, Genre, ProcessingJob, JobStatus

    db = SessionLocal()
    try:
        total_videos = db.query(VideoItem).count()
        total_genres = db.query(Genre).count()
        active_jobs = db.query(ProcessingJob).filter(
            ProcessingJob.status.in_([JobStatus.queued, JobStatus.downloading,
                                       JobStatus.downloaded, JobStatus.remuxing,
                                       JobStatus.analyzing, JobStatus.normalizing,
                                       JobStatus.tagging, JobStatus.writing_nfo,
                                       JobStatus.asset_fetch])
        ).count()
        failed_jobs = db.query(ProcessingJob).filter(
            ProcessingJob.status == JobStatus.failed
        ).count()

        return {
            "total_videos": total_videos,
            "total_genres": total_genres,
            "active_jobs": active_jobs,
            "failed_jobs": failed_jobs,
        }
    finally:
        db.close()


# ── Serve frontend SPA ────────────────────────────────────
# Search order: 1) bundled dist inside backend (installer), 2) repo-level frontend/dist (dev build)
_frontend_dist = None
_candidate_dirs = [
    Path(__file__).resolve().parent / "static" / "dist",          # bundled with backend
    Path(__file__).resolve().parent.parent / "static" / "dist",   # backend/static/dist
    Path(__file__).resolve().parent.parent.parent / "frontend" / "dist",  # repo layout
]
for _cand in _candidate_dirs:
    if _cand.is_dir() and (_cand / "index.html").is_file():
        _frontend_dist = _cand
        break

if _frontend_dist is not None:
    logger.info(f"Serving frontend from {_frontend_dist}")

    # Serve static assets (JS, CSS, images) under /assets
    _assets_dir = _frontend_dist / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="frontend-assets")

    _index_html = _frontend_dist / "index.html"

    # SPA fallback via middleware: non-API 404s serve index.html
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import Response as StarletteResponse

    class SPAFallbackMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response: StarletteResponse = await call_next(request)
            path = request.url.path
            if (
                response.status_code == 404
                and request.method == "GET"
                and not path.startswith("/api")
                and not path.startswith("/assets")
            ):
                return FileResponse(str(_index_html))
            return response

    app.add_middleware(SPAFallbackMiddleware)

    # Serve root-level static files (vite.svg, favicon.ico, etc.)
    @app.get("/vite.svg")
    async def serve_vite_svg():
        svg = _frontend_dist / "vite.svg"
        if svg.is_file():
            return FileResponse(str(svg))
        return FileResponse(str(_index_html))

    @app.get("/favicon.ico")
    async def serve_favicon():
        fav = _frontend_dist / "favicon.ico"
        if fav.is_file():
            return FileResponse(str(fav))
        svg = _frontend_dist / "vite.svg"
        if svg.is_file():
            return FileResponse(str(svg))
        return FileResponse(str(_index_html))

    # Root route — serves index.html directly (ensures / works without middleware)
    @app.get("/")
    async def serve_index():
        return FileResponse(str(_index_html))
else:
    logger.warning(
        "Frontend dist not found; UI will not be served. "
        "Build the frontend with 'npm run build' in the frontend/ directory, "
        "or set PLAYARR_DEV=1 and run the Vite dev server separately."
    )
