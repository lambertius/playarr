# AUTO-SEPARATED from pipeline/stages.py for pipeline_lib pipeline
# This file is independent — changes here do NOT affect the other pipeline.
"""
Library import pipeline orchestrator.

Public entry point:
  run_library_import_pipeline(job_id) — for library_import_video_task

Pipeline stages:
  Stage A — coarse DB status update (milliseconds)
  Stage B — workspace build (parallel, no locks, no DB writes)
  Stage C — serial DB apply (short transaction under _apply_lock)
  Stage D — deferred enrichment tasks dispatched as background work
"""
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Dict, Optional

from app.pipeline_lib.workspace import ImportWorkspace
from app.pipeline_lib.mutation_plan import build_plan_from_workspace
from app.pipeline_lib.db_apply import apply_mutation_plan, TocTouDuplicateError

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ═══════════════════════════════════════════════════════════════════════

def run_library_import_pipeline(job_id: int) -> None:
    """Full staged pipeline for a library file import."""
    from app.models import JobStatus
    from app.worker import clear_cancel, JobCancelledError

    ws = ImportWorkspace(job_id)
    try:
        ws.reset()  # clear stale artifacts from recycled job IDs
        # Stage A — coarse status
        params = _load_job_params(job_id)
        if params is None:
            _coarse_update(job_id, JobStatus.failed, error="Missing job configuration")
            return

        ws.write_artifact("input", {**params, "import_type": "library"})
        _basename = os.path.basename(params.get("file_path", ""))
        _mode = (params.get("options") or {}).get("mode", "simple")
        _mode_label = "Advanced Import" if _mode == "advanced" else "Import"
        _coarse_update(job_id, JobStatus.analyzing, step="Starting import", progress=5,
                       display_name=f"{_basename} \u203a {_mode_label}")
        ws.log(f"Library import started: {params.get('file_path', '?')}")

        # Stage B — workspace build (NO lock, NO DB writes)
        _library_stage_b(ws, job_id, params)
        ws.sync_logs_to_db()

        # Stage C — apply mutation plan (short serialised DB transaction)
        ws.update_stage("apply", "running")
        _coarse_update(job_id, JobStatus.analyzing, step="Applying to database", progress=85)
        plan = ws.read_artifact("mutation_plan")
        video_id = apply_mutation_plan(plan)
        ws.update_stage("apply", "complete")
        ws.write_artifact("apply_result", {"video_id": video_id})
        ws.log(f"Applied to DB — video_id={video_id}")

        # Write Playarr XML sidecar
        try:
            from app.services.playarr_xml import write_playarr_xml
            from app.database import SessionLocal as _XMLSessionLocal
            from app.models import VideoItem as _XVI
            _xml_db = _XMLSessionLocal()
            try:
                _xml_video = _xml_db.query(_XVI).get(video_id)
                if _xml_video:
                    write_playarr_xml(_xml_video, _xml_db)
                    ws.log("Playarr XML sidecar written")
            finally:
                _xml_db.close()
        except Exception as e:
            ws.log(f"Playarr XML write warning: {e}", level="warning")

        # Terminal status is set atomically inside apply_mutation_plan.
        ws.log("Import complete")

        # Stage D — deferred tasks (cleanup handled by dispatch_deferred)
        from app.pipeline_lib.deferred import dispatch_deferred
        dispatch_deferred(video_id, plan.get("deferred_tasks", []), ws)

    except JobCancelledError:
        _coarse_update(job_id, JobStatus.cancelled, error="Cancelled by user")
        ws.log("Import cancelled", level="warning")
        ws.sync_logs_to_db()
    except _DuplicateSkip as dup:
        _dup_reason = dup.reason or "Suspected duplicate"
        ws.log(f"Skipped: {_dup_reason}")
        _coarse_update(job_id, JobStatus.skipped,
                       step=f"Skipped: {_dup_reason[:200]}",
                       progress=100,
                       video_id=dup.existing_video_id)
        if dup.existing_video_id:
            _flag_existing_for_duplicate_review(
                dup.existing_video_id, job_id, _dup_reason)
        ws.sync_logs_to_db()
        ws.cleanup_on_success()
    except TocTouDuplicateError as toctou:
        # Stage C detected a duplicate AFTER Stage B placed files in
        # the library.  Clean up the organized files to avoid orphans.
        _toctou_reason = toctou.reason or "TOCTOU duplicate"
        ws.log(f"TOCTOU duplicate — cleaning up: {_toctou_reason}")
        _cleanup_organized_artifacts(ws)
        _coarse_update(job_id, JobStatus.skipped,
                       step=f"Skipped: {_toctou_reason[:200]}",
                       progress=100,
                       video_id=toctou.existing_video_id)
        if toctou.existing_video_id:
            _flag_existing_for_duplicate_review(
                toctou.existing_video_id, job_id, _toctou_reason)
        ws.sync_logs_to_db()
        ws.cleanup_on_success()
    except Exception as e:
        logger.error(f"[Job {job_id}] Library import failed: {e}", exc_info=True)
        _coarse_update(job_id, JobStatus.failed, error=str(e)[:2000])
        ws.log(f"FAILED: {e}", level="error")
        ws.sync_logs_to_db()
    finally:
        clear_cancel(job_id)
        _ensure_terminal(job_id)


# ═══════════════════════════════════════════════════════════════════════
#  STAGE B — LIBRARY IMPORT
# ═══════════════════════════════════════════════════════════════════════

def _library_stage_b(ws: ImportWorkspace, job_id: int, params: dict) -> None:
    """Build workspace for a library file import.  NO locks, NO DB writes."""
    from app.worker import is_cancelled, JobCancelledError

    source_path = params["file_path"]
    options = params.get("options") or {}
    mode = options.get("mode", "simple")

    def _check():
        if is_cancelled(job_id):
            raise JobCancelledError(f"Job {job_id} cancelled")

    # B1: Parse identity
    _step_parse_identity(ws, source_path, options)
    _check()
    identity = ws.read_artifact("parsed_identity")
    artist = identity["artist"]
    title = identity["title"]

    _coarse_update(job_id, step="Analyzing media", progress=15,
                   display_name=f"{artist} \u2013 {title} \u203a Library Import")

    # B2: Analyze media
    _step_analyze_media(ws, source_path)
    _check()
    ffprobe = ws.read_artifact("ffprobe") or {}

    # B3: Duplicate pre-check (respects user duplicate action if provided)
    dup_action = params.get("duplicate_action")
    _step_duplicate_precheck(ws, artist, title, dup_action)
    _check()

    resolution_label = _derive_resolution(ffprobe.get("height"))

    # B4: Organize file
    _step_organize_file(ws, source_path, artist, title, resolution_label, options)
    _check()
    organized = ws.read_artifact("organized") or {}
    new_file = organized.get("new_file")
    new_folder = organized.get("new_folder", "")

    # B5: Copy artwork from source directory
    _step_copy_artwork(ws, source_path, new_folder, new_file)

    # B6: Normalize audio
    _step_normalize_audio(ws, new_file, options)
    _check()

    # B7: Write NFO
    _step_write_nfo(ws, new_folder, identity, resolution_label, params)

    # ── Trusted Playarr XML shortcut ─────────────────────────────────
    # When Trust Existing or Trust & Review found a valid .playarr.xml,
    # populate workspace artifacts from the XML and skip scraping entirely.
    xml_data = ws.read_artifact("playarr_xml_data")
    if xml_data:
        ws.log("Using trusted Playarr XML metadata — skipping scraping")
        _coarse_update(job_id, step="Using existing metadata", progress=70)

        # Build scraper_results from XML identity + MB IDs
        scraper_results = {
            "artist": xml_data.get("artist") or artist,
            "title": xml_data.get("title") or title,
            "album": xml_data.get("album") or "",
            "year": xml_data.get("year"),
            "plot": xml_data.get("plot") or "",
            "genres": xml_data.get("genres") or [],
            "mb_artist_id": xml_data.get("mb_artist_id"),
            "mb_recording_id": xml_data.get("mb_recording_id"),
            "mb_release_id": xml_data.get("mb_release_id"),
            "mb_release_group_id": xml_data.get("mb_release_group_id"),
        }
        ws.write_artifact("scraper_results", scraper_results)

        # Version detection from XML
        version_det = {
            "version_type": xml_data.get("version_type", "normal"),
            "alternate_version_label": xml_data.get("alternate_version_label", ""),
            "original_artist": xml_data.get("original_artist"),
            "original_title": xml_data.get("original_title"),
            "review_status": xml_data.get("review_status", "none"),
            "review_reason": xml_data.get("review_reason"),
        }
        ws.write_artifact("version_detection", version_det)

        # Source links from XML
        xml_sources = xml_data.get("sources") or []
        source_links = {}
        for i, src in enumerate(xml_sources):
            key = f"{src.get('provider', 'source')}_{i}"
            source_links[key] = {
                "provider": src.get("provider", "other"),
                "id": src.get("source_video_id", ""),
                "url": src.get("original_url") or src.get("canonical_url", ""),
                "source_type": src.get("source_type", "video"),
                "provenance": "playarr_xml",
                "channel_name": src.get("channel_name"),
                "platform_title": src.get("platform_title"),
                "upload_date": src.get("upload_date"),
            }
        ws.write_artifact("source_links", source_links)

        # Entity refs from XML
        xml_entity_refs = xml_data.get("entity_refs") or {}
        entity_res = {}
        if xml_entity_refs.get("artist"):
            entity_res["artist"] = {
                "name": xml_entity_refs["artist"].get("name", artist),
                "resolved": {"mb_artist_id": xml_entity_refs["artist"].get("mb_artist_id")},
            }
        if xml_entity_refs.get("album"):
            entity_res["album"] = {
                "title": xml_entity_refs["album"].get("title", ""),
                "resolved": {
                    "mb_release_id": xml_entity_refs["album"].get("mb_release_id"),
                    "mb_release_group_id": xml_entity_refs["album"].get("mb_release_group_id"),
                },
            }
        if xml_entity_refs.get("track"):
            tr = xml_entity_refs["track"]
            entity_res["track"] = {
                "title": tr.get("title", title),
                "resolved": {
                    "mb_recording_id": tr.get("mb_recording_id"),
                    "is_cover": tr.get("is_cover", False),
                    "original_artist": tr.get("original_artist"),
                    "original_title": tr.get("original_title"),
                },
            }
        ws.write_artifact("entity_resolution", entity_res)

        # Artwork from XML (reference existing files)
        xml_artwork = xml_data.get("artwork") or []
        artwork_assets = []
        for art in xml_artwork:
            fp = art.get("file_path")
            if fp and os.path.isfile(fp):
                artwork_assets.append({
                    "asset_type": art.get("asset_type", "poster"),
                    "file_path": fp,
                    "source_url": art.get("source_url"),
                    "provenance": art.get("provenance") or "playarr_xml",
                    "source_provider": art.get("source_provider"),
                    "file_hash": art.get("file_hash"),
                })
        ws.write_artifact("artwork_results", {"assets": artwork_assets})

        # Processing state from XML
        xml_processing = xml_data.get("processing_state") or {}
        ws.write_artifact("xml_processing_state", xml_processing)

        # Ratings from XML
        if xml_data.get("song_rating_set") or xml_data.get("video_rating_set"):
            ws.write_artifact("xml_ratings", {
                "song_rating": xml_data.get("song_rating"),
                "video_rating": xml_data.get("video_rating"),
            })

        # Locked fields from XML — persisted so future operations respect them
        xml_locked = xml_data.get("locked_fields")
        if xml_locked:
            ws.write_artifact("xml_locked_fields", xml_locked)

        # Mark metadata stages as complete (they were done before, in the XML)
        ws.update_stage("resolve_metadata", "complete")
        ws.update_stage("fetch_artwork", "complete")

        # B15: Build mutation plan
        _step_build_mutation_plan(ws)
        ws.log("Workspace build complete (trusted XML)")
        return

    if mode == "advanced":
        # B8: YouTube match
        duration = ffprobe.get("duration_seconds")
        _step_youtube_match(ws, artist, title, duration, params)
        _check()

        # B9: Fetch yt-dlp metadata
        _step_ytdlp_metadata(ws)

        # B10: Resolve metadata (AI + Wikipedia + MusicBrainz)
        _coarse_update(job_id, step="Resolving metadata", progress=55)
        _step_resolve_metadata(ws, artist, title, identity, ffprobe, params)
        _check()

        # Inject AI failures into the job record so the frontend can display them
        _ai_fail_entries = ws.read_artifact("ai_failures") or []
        if _ai_fail_entries:
            try:
                import time as _ai_time
                from app.models import ProcessingJob as _PJ
                from sqlalchemy.orm.attributes import flag_modified as _flag_mod
                from app.database import CosmeticSessionLocal
                _ai_injected = False
                for _ai_attempt in range(3):
                    _ai_db = CosmeticSessionLocal()
                    try:
                        _ai_job = _ai_db.query(_PJ).get(job_id)
                        if _ai_job:
                            steps = list(_ai_job.pipeline_steps or [])
                            for f in _ai_fail_entries:
                                steps.append({
                                    "step": f.get("description", "AI error"),
                                    "status": "failed",
                                    "type": "ai_error",
                                    "code": f.get("code", ""),
                                })
                            _ai_job.pipeline_steps = steps
                            _flag_mod(_ai_job, "pipeline_steps")
                            _ai_db.commit()
                        _ai_injected = True
                        break
                    except Exception:
                        _ai_db.rollback()
                        if _ai_attempt < 2:
                            _ai_time.sleep(2)
                    finally:
                        _ai_db.close()
                if not _ai_injected:
                    raise RuntimeError("All retries exhausted")
            except Exception as _ai_inject_err:
                ws.log(f"Could not inject AI failure tags into job record: "
                       f"{_ai_inject_err}", level="warning")
                ws.write_artifact("ai_inject_failed", True)

        metadata = ws.read_artifact("scraper_results") or {}
        final_artist = metadata.get("artist") or artist
        final_title = metadata.get("title") or title

        # B11: Detect version
        _step_detect_version(ws, final_artist, final_title, ffprobe, params)
        _check()

        version = ws.read_artifact("version_detection") or {}

        # B11b: Version-aware duplicate re-check (advanced mode only)
        # The B3 precheck was conservative — now we have version info.
        dup_check = ws.read_artifact("duplicate_check") or {}
        if dup_check.get("is_possible_duplicate"):
            incoming_version = version.get("version_type", "normal") or "normal"
            existing_version = dup_check.get("existing_version_type", "normal")
            if incoming_version == existing_version:
                # Version detection resolved to same type → hard duplicate
                _eid = dup_check["existing_video_id"]
                ws.log(f"Version-aware re-check: both {incoming_version}, "
                       f"confirmed duplicate (id={_eid}), skipping")
                raise _DuplicateSkip(
                    existing_video_id=_eid,
                    match_type="name_match",
                    reason=(
                        f"Duplicate of existing item (id={_eid}, "
                        f"both version={incoming_version})"
                    ),
                )
            # Still different versions → keep the review flag
            version["review_status"] = "needs_human_review"
            version["needs_review"] = True
            _dup_reason = dup_check.get("reason", "Possible duplicate")
            existing_reason = version.get("review_reason") or ""
            version["review_reason"] = (
                f"{existing_reason}; {_dup_reason}" if existing_reason
                else _dup_reason
            )
            ws.write_artifact("version_detection", version)

        # B12: Entity resolution
        _coarse_update(job_id, step="Resolving entities", progress=75)
        _step_resolve_entities(ws, final_artist, final_title, metadata, params)
        _check()

        # B13: Source links
        _step_collect_source_links(ws, final_artist, final_title, metadata, params)

        # B14: Fetch artwork
        _step_fetch_artwork(ws, new_folder, final_artist, final_title,
                            resolution_label, metadata)

    # If AI failure tags couldn't be written to the job record, flag the
    # video for review so unvalidated metadata doesn't slip through silently.
    if ws.read_artifact("ai_inject_failed"):
        version = ws.read_artifact("version_detection") or {}
        version["review_status"] = "needs_human_review"
        version["needs_review"] = True
        _ai_reason = "AI review failed and could not record failure — metadata rescan recommended"
        existing_reason = version.get("review_reason") or ""
        version["review_reason"] = (
            f"{existing_reason}; {_ai_reason}" if existing_reason
            else _ai_reason
        )
        ws.write_artifact("version_detection", version)

    # B15: Build mutation plan
    _step_build_mutation_plan(ws)
    ws.log("Workspace build complete")


# ═══════════════════════════════════════════════════════════════════════
#  INDIVIDUAL STEPS
# ═══════════════════════════════════════════════════════════════════════

def _step_parse_identity(ws: ImportWorkspace, source_path: str, options: dict) -> None:
    """Parse artist/title/album from Playarr XML, NFO, or filename."""
    if ws.is_stage_complete("parse_identity"):
        return
    ws.update_stage("parse_identity", "running")

    from app.pipeline_lib.services.nfo_parser import find_nfo_for_video, parse_nfo_file
    from app.pipeline_lib.services.filename_parser import parse_filename

    artist = title = album = None
    year = None
    genres = []
    plot = ""
    source_url = ""
    confidence = 0.0

    # Check for existing .playarr.xml (used by Trust Existing / Trust & Review,
    # or automatically in in_place mode)
    review_mode = options.get("review_mode", "skip")
    file_handling = options.get("file_handling", "copy")
    if review_mode in ("basic", "advanced") or file_handling == "in_place":
        from app.services.playarr_xml import find_playarr_xml, parse_playarr_xml
        source_dir = os.path.dirname(source_path)
        xml_path = find_playarr_xml(source_dir)
        if xml_path:
            xml_data = parse_playarr_xml(xml_path)
            if xml_data and xml_data.get("artist") and xml_data.get("title"):
                ws.write_artifact("playarr_xml_data", xml_data)
                artist = xml_data["artist"]
                title = xml_data["title"]
                album = xml_data.get("album")
                year = xml_data.get("year")
                genres = xml_data.get("genres") or []
                plot = xml_data.get("plot") or ""
                confidence = 1.0
                ws.log(f"Playarr XML trusted: {artist} - {title}")

                ws.write_artifact("parsed_identity", {
                    "artist": artist,
                    "title": title,
                    "album": album,
                    "year": year,
                    "genres": genres,
                    "plot": plot,
                    "source_url": "",
                    "confidence": confidence,
                })
                ws.update_stage("parse_identity", "complete")
                return

    nfo_path = find_nfo_for_video(source_path)
    if nfo_path:
        parsed = parse_nfo_file(nfo_path)
        if parsed:
            artist = parsed.artist
            title = parsed.title
            album = parsed.album
            year = parsed.year
            genres = parsed.genres or []
            plot = parsed.plot or ""
            source_url = parsed.source_url or ""
            confidence = 0.9
            ws.log(f"NFO parsed: {artist} - {title}")

    if not artist or not title:
        custom_regex = options.get("custom_regex")
        basename = os.path.basename(source_path)
        parsed_fn = parse_filename(basename, custom_pattern=custom_regex)
        if not artist:
            artist = parsed_fn.artist
        if not title:
            title = parsed_fn.title
        if not year and parsed_fn.year:
            year = parsed_fn.year
        confidence = max(confidence, 0.5)
        ws.log(f"Filename parsed: {artist} - {title}")

    if not artist:
        artist = "Unknown Artist"
    if not title:
        title = os.path.splitext(os.path.basename(source_path))[0]

    ws.write_artifact("parsed_identity", {
        "artist": artist,
        "title": title,
        "album": album,
        "year": year,
        "genres": genres,
        "plot": plot,
        "source_url": source_url,
        "confidence": confidence,
    })
    ws.update_stage("parse_identity", "complete")


def _step_analyze_media(ws: ImportWorkspace, file_path: str) -> None:
    """Extract quality signature and measure loudness."""
    if ws.is_stage_complete("analyze_media"):
        return
    ws.update_stage("analyze_media", "running")

    from app.pipeline_lib.services.media_analyzer import extract_quality_signature, measure_loudness

    sig = {}
    try:
        sig = extract_quality_signature(file_path)
    except Exception as e:
        ws.log(f"Quality analysis warning: {e}", level="warning")

    ws.write_artifact("ffprobe", sig)

    loudness = None
    try:
        loudness = measure_loudness(file_path)
    except Exception as e:
        ws.log(f"Loudness measurement warning: {e}", level="warning")

    ws.write_artifact("loudness", {"lufs": loudness})
    ws.update_stage("analyze_media", "complete")


def _step_duplicate_precheck(ws: ImportWorkspace, artist: str, title: str,
                             duplicate_action: Optional[dict] = None) -> None:
    """Read-only duplicate check (WAL safe without lock).

    If ``duplicate_action`` is provided (from user wizard choice), it overrides
    the default behaviour:
      - ``skip``        → raise _DuplicateSkip immediately
      - ``overwrite``   → flag for overwrite (existing item will be replaced)
      - ``keep_both``   → proceed with forced version_type from user
      - ``review_later``→ proceed but flag for human review
    """
    if ws.is_stage_complete("duplicate_precheck"):
        return

    # If the user already chose an action in the wizard, honour it
    if duplicate_action:
        action = duplicate_action.get("action", "skip")
        ws.log(f"User duplicate action: {action}")
        if action == "skip":
            ws.update_stage("duplicate_precheck", "complete")
            raise _DuplicateSkip(
                existing_video_id=None,
                match_type="user_skip",
                reason="User chose to skip this duplicate",
            )
        elif action == "overwrite":
            ws.write_artifact("duplicate_check", {
                "is_duplicate": False,
                "user_action": "overwrite",
                "overwrite_existing": True,
            })
            ws.update_stage("duplicate_precheck", "complete")
            return
        elif action == "keep_both":
            forced_version = duplicate_action.get("version_type", "alternate")
            ws.write_artifact("duplicate_check", {
                "is_duplicate": False,
                "user_action": "keep_both",
                "forced_version_type": forced_version,
            })
            ws.update_stage("duplicate_precheck", "complete")
            return
        elif action == "review_later":
            # Mark the existing library item for human review, then skip
            # the import entirely (the user can act from the review tab).
            from app.database import SessionLocal as _SL
            from app.models import VideoItem as _VI
            _db = _SL()
            try:
                _existing = _db.query(_VI).filter(
                    _VI.artist.ilike(artist),
                    _VI.title.ilike(title),
                ).first()
                if _existing and getattr(_existing, "review_status", "none") in (None, "none"):
                    _existing.review_status = "needs_human_review"
                    _existing.review_reason = "User deferred duplicate to review"
                    _db.commit()
                    ws.log(f"Marked existing video id={_existing.id} for review")
            finally:
                _db.close()
            ws.update_stage("duplicate_precheck", "complete")
            raise _DuplicateSkip(
                existing_video_id=None,
                match_type="user_review_later",
                reason="User deferred this duplicate to review",
            )

    from app.database import SessionLocal
    from app.models import VideoItem
    from app.scraper.source_validation import parse_multi_artist

    db = SessionLocal()
    try:
        existing = db.query(VideoItem).filter(
            VideoItem.artist.ilike(artist),
            VideoItem.title.ilike(title),
        ).first()
        if not existing:
            # Fallback: primary artist prefix match + title
            query_primary, _ = parse_multi_artist(artist)
            qp_lower = query_primary.lower()
            title_matches = db.query(VideoItem).filter(
                VideoItem.title.ilike(title),
            ).all()
            for candidate in title_matches:
                db_primary, _ = parse_multi_artist(candidate.artist or "")
                dp_lower = db_primary.lower()
                if dp_lower == qp_lower or qp_lower.startswith(dp_lower) or dp_lower.startswith(qp_lower):
                    existing = candidate
                    break
        if existing:
            existing_version = getattr(existing, "version_type", "normal") or "normal"

            # If the existing item has a non-normal version (e.g. 'live'),
            # the incoming file might be a different version → possible duplicate.
            # Proceed with import but flag for review.
            if existing_version != "normal":
                ws.log(
                    f"Possible duplicate: existing id={existing.id} "
                    f"(version={existing_version}). "
                    f"Proceeding with import, flagging for review."
                )
                ws.write_artifact("duplicate_check", {
                    "is_duplicate": False,
                    "is_possible_duplicate": True,
                    "existing_video_id": existing.id,
                    "existing_artist": existing.artist,
                    "existing_title": existing.title,
                    "existing_version_type": existing_version,
                    "reason": (
                        f"Possible duplicate of '{existing.artist} - {existing.title}' "
                        f"(id={existing.id}, existing: {existing_version})"
                    ),
                })
                ws.update_stage("duplicate_precheck", "complete")
                return  # proceed with import

            # Same version type (both normal) → hard duplicate → skip
            ws.log(f"Duplicate found (id={existing.id}, version={existing_version}), skipping")
            ws.update_stage("duplicate_precheck", "complete")
            raise _DuplicateSkip(
                existing_video_id=existing.id,
                match_type="name_match",
                reason=(
                    f"Duplicate of '{existing.artist} - {existing.title}' "
                    f"(id={existing.id}, version={existing_version})"
                ),
            )
    finally:
        db.close()

    ws.update_stage("duplicate_precheck", "complete")


def _step_organize_file(ws: ImportWorkspace, source_path: str,
                        artist: str, title: str, resolution_label: str,
                        options: dict) -> None:
    """Copy or move the source file into the library, or register in place."""
    if ws.is_stage_complete("organize_file"):
        return
    ws.update_stage("organize_file", "running")

    file_handling = options.get("file_handling", "copy")

    # ── In-place mode: register files where they already are ─────────
    if file_handling == "in_place":
        folder = os.path.dirname(source_path)
        file_size = os.path.getsize(source_path) if os.path.isfile(source_path) else 0
        ws.write_artifact("organized", {
            "new_folder": folder,
            "new_file": source_path,
            "resolution_label": resolution_label,
            "file_size_bytes": file_size,
        })
        ws.log(f"In-place: {folder}")
        ws.update_stage("organize_file", "complete")
        return

    from app.pipeline_lib.services.file_organizer import organize_file, build_folder_name, build_library_subpath
    from app.config import get_settings

    file_handling = options.get("file_handling", "copy")
    custom_dest = options.get("custom_destination")
    target_dir = custom_dest if file_handling in ("copy_to", "move_to") else None
    is_move = file_handling in ("move", "move_to")

    # ── Filesystem duplicate pre-check ───────────────────────────────
    # Before any file copy/move, check if the target folder already
    # contains a video file.  If so, skip the import → review queue.
    settings = get_settings()
    library_dir = target_dir or settings.library_dir
    folder_name = build_folder_name(artist, title, resolution_label)
    subpath = build_library_subpath(artist, title, resolution_label)
    target_folder = os.path.join(library_dir, subpath)

    # Check if user requested overwrite / keep_both via the duplicate wizard
    dup_check = ws.read_artifact("duplicate_check") or {}
    user_dup_action = dup_check.get("user_action")

    if os.path.isdir(target_folder):
        _VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.webm', '.mov', '.flv', '.wmv'}
        existing_videos = [
            f for f in os.listdir(target_folder)
            if os.path.splitext(f)[1].lower() in _VIDEO_EXTS
        ]
        if existing_videos:
            if user_dup_action == "overwrite":
                # User chose overwrite → remove existing video files
                for ev in existing_videos:
                    ev_path = os.path.join(target_folder, ev)
                    os.remove(ev_path)
                    ws.log(f"Overwrite: removed existing {ev}")
            elif user_dup_action == "keep_both":
                # User chose keep_both → allow coexistence, skip clash check
                ws.log(f"Keep both: allowing import alongside {len(existing_videos)} existing file(s)")
            else:
                existing_video_id = _find_video_by_folder(target_folder)
                clash_files = ", ".join(existing_videos[:3])
                ws.log(
                    f"Import clash: target folder already contains video file(s): "
                    f"{target_folder} ({clash_files})"
                )
                ws.update_stage("organize_file", "skipped")
                raise _DuplicateSkip(
                    existing_video_id=existing_video_id,
                    match_type="filesystem_clash",
                    reason=(
                        f"Import clash: folder '{folder_name}' already contains "
                        f"video file(s): {clash_files}"
                    ),
                )

    # ── Proceed with file organization ───────────────────────────────
    try:
        if is_move:
            new_folder, new_file = organize_file(
                source_path, artist, title, resolution_label, target_dir=target_dir,
            )
        else:
            temp_dir = tempfile.mkdtemp(prefix="playarr_import_")
            temp_file = os.path.join(temp_dir, os.path.basename(source_path))
            shutil.copy2(source_path, temp_file)
            new_folder, new_file = organize_file(
                temp_file, artist, title, resolution_label, target_dir=target_dir,
            )
            try:
                os.rmdir(temp_dir)
            except OSError:
                pass
    except FileExistsError as e:
        # Defense-in-depth: organize_file refused a collision
        if not is_move and 'temp_dir' in locals():
            shutil.rmtree(temp_dir, ignore_errors=True)
        existing_video_id = _find_video_by_folder(target_folder)
        ws.log(f"Import clash (file-level): {e}", level="warning")
        ws.update_stage("organize_file", "skipped")
        raise _DuplicateSkip(
            existing_video_id=existing_video_id,
            match_type="filesystem_clash",
            reason=str(e),
        )

    file_size = os.path.getsize(new_file) if os.path.isfile(new_file) else 0

    ws.write_artifact("organized", {
        "new_folder": new_folder,
        "new_file": new_file,
        "resolution_label": resolution_label,
        "file_size_bytes": file_size,
    })
    ws.log(f"Organized to: {new_folder}")
    ws.update_stage("organize_file", "complete")


def _step_copy_artwork(ws: ImportWorkspace, source_path: str,
                       new_folder: str, new_file: str) -> None:
    """Copy poster/thumb from source directory (or reference in place)."""
    if ws.is_stage_complete("copy_artwork"):
        return
    ws.update_stage("copy_artwork", "running")

    from app.pipeline_lib.services.nfo_parser import find_artwork_for_video

    input_data = ws.read_artifact("input") or {}
    file_handling = (input_data.get("options") or {}).get("file_handling", "copy")

    artwork = find_artwork_for_video(source_path)
    assets = []
    for art_type, art_path in artwork.items():
        if art_path and os.path.isfile(art_path):
            if file_handling == "in_place":
                # Reference existing artwork directly — no copy needed
                assets.append({
                    "asset_type": art_type,
                    "file_path": art_path,
                    "source_url": "",
                    "provenance": "library_source",
                })
                ws.log(f"Found {art_type}: {os.path.basename(art_path)}")
            else:
                ext = os.path.splitext(art_path)[1]
                basename = os.path.splitext(os.path.basename(new_file))[0]
                dest = os.path.join(new_folder, f"{basename}-{art_type}{ext}")
            try:
                shutil.copy2(art_path, dest)
                assets.append({
                    "asset_type": art_type,
                    "file_path": dest,
                    "source_url": "",
                    "provenance": "library_source",
                })
                ws.log(f"Copied {art_type}: {os.path.basename(dest)}")
            except Exception as e:
                ws.log(f"Artwork copy failed ({art_type}): {e}", level="warning")

    ws.write_artifact("artwork_source", {"assets": assets})
    ws.update_stage("copy_artwork", "complete")


def _step_normalize_audio(ws: ImportWorkspace, file_path: str, options: dict) -> None:
    """Normalize audio if requested."""
    normalize = options.get("normalize_audio", False) or options.get("normalize", False)
    if not normalize:
        ws.update_stage("normalize_audio", "skipped")
        return
    if ws.is_stage_complete("normalize_audio"):
        return
    ws.update_stage("normalize_audio", "running")

    from app.pipeline_lib.services.normalizer import normalize_video

    try:
        before, after, gain = normalize_video(file_path)
        if before is not None:
            ws.write_artifact("normalized", {
                "before_lufs": before,
                "after_lufs": after,
                "gain_db": gain,
            })
            ws.log(f"Normalized: {before:.1f} → {after:.1f} LUFS")
            ws.update_stage("normalize_audio", "complete")
        else:
            ws.update_stage("normalize_audio", "skipped")
    except Exception as e:
        ws.log(f"Normalization error: {e}", level="warning")
        ws.update_stage("normalize_audio", "failed")


def _step_write_nfo(ws: ImportWorkspace, folder: str, identity: dict,
                    resolution_label: str, params: dict) -> None:
    """Write initial NFO file (skipped in in_place mode if NFO already exists)."""
    if ws.is_stage_complete("nfo_write"):
        return

    # In in_place mode, don't overwrite an existing NFO
    file_handling = (params.get("options") or {}).get("file_handling", "copy")
    if file_handling == "in_place":
        from app.pipeline_lib.services.nfo_parser import find_nfo_for_video
        source_path = params.get("file_path", "")
        if source_path and find_nfo_for_video(source_path):
            ws.log("NFO already exists, skipping write (in-place mode)")
            ws.update_stage("nfo_write", "skipped")
            return

    ws.update_stage("nfo_write", "running")

    from app.pipeline_lib.services.file_organizer import write_nfo_file

    try:
        write_nfo_file(
            folder,
            artist=identity.get("artist", ""),
            title=identity.get("title", ""),
            album=identity.get("album", ""),
            year=identity.get("year"),
            genres=identity.get("genres", []),
            plot=identity.get("plot", ""),
            source_url=identity.get("source_url", ""),
            resolution_label=resolution_label,
        )
        ws.update_stage("nfo_write", "complete")
    except Exception as e:
        ws.log(f"NFO write warning: {e}", level="warning")
        ws.update_stage("nfo_write", "failed")


def _step_youtube_match(ws: ImportWorkspace, artist: str, title: str,
                        duration: Optional[float], options: dict) -> None:
    """Find best YouTube match for a library-imported video."""
    opts = options.get("options", options)
    # In advanced mode, always search for YouTube source unless explicitly disabled
    mode = opts.get("mode", "simple")
    if mode != "advanced" and not opts.get("find_source_video", False):
        ws.update_stage("youtube_match", "skipped")
        return
    if ws.is_stage_complete("youtube_match"):
        return
    ws.update_stage("youtube_match", "running")

    from app.pipeline_lib.services.youtube_matcher import find_best_youtube_match

    try:
        match = find_best_youtube_match(artist, title, duration_seconds=int(duration) if duration else None)
        if match:
            ws.write_artifact("youtube_match", {
                "url": match.url,
                "video_id": getattr(match, "video_id", ""),
                "canonical_url": getattr(match, "canonical_url", match.url),
                "title": getattr(match, "title", ""),
                "channel": getattr(match, "channel", ""),
                "score": match.overall_score,
            })
            ws.log(f"YouTube match: {match.url} (score={match.overall_score:.2f})")
        ws.update_stage("youtube_match", "complete")
    except Exception as e:
        ws.log(f"YouTube match error: {e}", level="warning")
        ws.update_stage("youtube_match", "failed")


def _step_ytdlp_metadata(ws: ImportWorkspace) -> None:
    """Fetch yt-dlp metadata from a matched YouTube source."""
    yt = ws.read_artifact("youtube_match")
    if not yt or not yt.get("url"):
        return
    if ws.has_artifact("ytdlp_metadata"):
        return

    from app.pipeline_lib.services.downloader import get_available_formats, extract_metadata_from_ytdlp

    try:
        _formats, info = get_available_formats(yt["url"])
        meta = extract_metadata_from_ytdlp(info) if info else {}
        ws.write_artifact("ytdlp_metadata", meta)
    except Exception as e:
        ws.log(f"yt-dlp metadata warning: {e}", level="warning")


def _step_resolve_metadata(ws: ImportWorkspace, artist: str, title: str,
                           identity: dict, ffprobe: dict, options: dict) -> None:
    """Run unified metadata resolution (AI + MB + Wikipedia)."""
    if ws.is_stage_complete("resolve_metadata"):
        return
    ws.update_stage("resolve_metadata", "running")

    opts = options.get("options", options)
    _skip_ai = not (opts.get("ai_auto_analyse", False) or opts.get("ai_auto_fallback", False))
    _skip_wiki = not (opts.get("scrape_wikipedia", True) or opts.get("ai_auto_analyse", False))
    _skip_mb = not (opts.get("scrape_musicbrainz", True) or opts.get("ai_auto_analyse", False))

    from app.scraper.unified_metadata import resolve_metadata_unified
    from app.database import SessionLocal
    ytdlp_meta = ws.read_artifact("ytdlp_metadata") or {}

    _db = SessionLocal()
    try:
        metadata = resolve_metadata_unified(
            artist=artist,
            title=title,
            db=_db,
            source_url=identity.get("source_url", ""),
            platform_title=ytdlp_meta.get("title", ""),
            channel_name=ytdlp_meta.get("uploader") or ytdlp_meta.get("channel") or "",
            platform_description=(ytdlp_meta.get("description") or "")[:3000],
            platform_tags=ytdlp_meta.get("tags") or [],
            upload_date=ytdlp_meta.get("upload_date") or "",
            duration_seconds=ffprobe.get("duration_seconds"),
            ytdlp_metadata=ytdlp_meta or None,
            skip_wikipedia=_skip_wiki,
            skip_musicbrainz=_skip_mb,
            skip_ai=_skip_ai,
        )
    except Exception as e:
        ws.log(f"Metadata resolution warning: {e}", level="warning")
        metadata = {"artist": artist, "title": title, "genres": [], "plot": None}
    finally:
        _db.close()

    # AI summary
    if metadata.get("plot"):
        try:
            from app.pipeline_lib.services.ai_summary import generate_ai_summary
            summary = generate_ai_summary(metadata["plot"],
                                          source_url=identity.get("source_url", ""))
            if summary:
                metadata["plot"] = summary
            else:
                ws.log("AI summary returned empty — raw scraped text kept as plot", level="warning")
        except Exception as e:
            ws.log(f"AI summary generation failed: {e}", level="warning")

    ws.write_artifact("scraper_results", metadata)
    # Persist AI failures separately so the pipeline can inject them into the job record
    _ai_failures = [f for f in metadata.get("pipeline_failures", [])
                     if f.get("code", "").startswith("AI_")]
    if _ai_failures:
        ws.write_artifact("ai_failures", _ai_failures)
    ws.update_stage("resolve_metadata", "complete")
    ws.log(f"Metadata resolved: {metadata.get('artist')} - {metadata.get('title')}")


def _step_detect_version(ws: ImportWorkspace, artist: str, title: str,
                         ffprobe: dict, options: dict) -> None:
    """Detect version type (normal, cover, live, alternate)."""
    if ws.is_stage_complete("detect_version"):
        return
    ws.update_stage("detect_version", "running")

    from app.pipeline_lib.matching.version_detector import detect_version_type

    try:
        identity = ws.read_artifact("parsed_identity") or {}
        organized = ws.read_artifact("organized") or {}
        source_path = (ws.read_artifact("input") or {}).get("file_path", "")

        vc = detect_version_type(
            filename=os.path.basename(organized.get("new_file") or source_path),
            source_title="",
            uploader="",
            description="",
            parsed_artist=artist,
            parsed_title=title,
            fingerprint_artist="",
            fingerprint_title="",
            scraped_artist=artist,
            scraped_title=title,
            duration_seconds=ffprobe.get("duration_seconds"),
        )
        ws.write_artifact("version_detection", {
            "version_type": vc.version_type,
            "alternate_version_label": vc.alternate_version_label,
            "original_artist": vc.original_artist,
            "original_title": vc.original_title,
            "needs_review": vc.needs_review,
            "review_reason": vc.review_reason,
            "confidence": vc.confidence,
            "performing_artist": getattr(vc, "performing_artist", None),
            "detected_title": getattr(vc, "detected_title", None),
            "review_status": ("needs_human_review" if vc.needs_review else "none"),
        })
        ws.update_stage("detect_version", "complete")
    except Exception as e:
        ws.log(f"Version detection error: {e}", level="warning")
        ws.write_artifact("version_detection", {"version_type": "normal"})
        ws.update_stage("detect_version", "failed")


def _step_resolve_entities(ws: ImportWorkspace, artist: str, title: str,
                           metadata: dict, options: dict) -> None:
    """Resolve artist/album/track entities via network (NO DB writes)."""
    if ws.is_stage_complete("resolve_entities"):
        return
    ws.update_stage("resolve_entities", "running")

    opts = options.get("options", options)
    _skip_mb = not (opts.get("scrape_musicbrainz", True) or opts.get("ai_auto_analyse", False))
    _skip_wiki = not (opts.get("scrape_wikipedia", True) or opts.get("ai_auto_analyse", False))

    from app.pipeline_lib.metadata.resolver import resolve_artist, resolve_album, resolve_track

    resolved_artist = {}
    resolved_album = {}
    resolved_track = {}

    try:
        resolved_artist = resolve_artist(
            artist,
            mb_artist_id=metadata.get("mb_artist_id"),
            skip_musicbrainz=_skip_mb,
            skip_wikipedia=_skip_wiki,
        )
    except Exception as e:
        ws.log(f"Artist resolution warning: {e}", level="warning")

    album_title = metadata.get("album")

    # When no album title was resolved, use _find_parent_album() — the same
    # function resolve_metadata_unified() uses — to find the parent Album
    # release group from the recording ID.  This ensures entity resolution
    # uses an identical code path to the scraper tester / unified pipeline.
    if not album_title and not _skip_mb:
        _rec_id = metadata.get("mb_recording_id")
        if _rec_id:
            try:
                from app.scraper.metadata_resolver import _find_parent_album, _init_musicbrainz
                _init_musicbrainz()
                _parent = _find_parent_album(_rec_id)
                if _parent and _parent.get("album"):
                    album_title = _parent["album"]
                    metadata["album"] = album_title
                    if _parent.get("mb_album_release_id"):
                        metadata["mb_album_release_id"] = _parent["mb_album_release_id"]
                    if _parent.get("mb_album_release_group_id"):
                        metadata["mb_album_release_group_id"] = _parent["mb_album_release_group_id"]
                    ws.log(f"Album resolved from _find_parent_album: '{album_title}'")
                else:
                    ws.log("No parent album found via _find_parent_album")
            except Exception as e:
                ws.log(f"_find_parent_album failed: {e}", level="warning")

        # Fallback: browse artist's release groups for a matching track
        if not album_title and metadata.get("mb_artist_id"):
            try:
                from app.scraper.metadata_resolver import _find_album_by_artist_browse, _init_musicbrainz
                _init_musicbrainz()
                _artist_album = _find_album_by_artist_browse(metadata["mb_artist_id"], title)
                if _artist_album and _artist_album.get("album"):
                    album_title = _artist_album["album"]
                    metadata["album"] = album_title
                    if _artist_album.get("mb_album_release_id"):
                        metadata["mb_album_release_id"] = _artist_album["mb_album_release_id"]
                    if _artist_album.get("mb_album_release_group_id"):
                        metadata["mb_album_release_group_id"] = _artist_album["mb_album_release_group_id"]
                    ws.log(f"Album resolved from artist browse fallback: '{album_title}'")
                else:
                    ws.log("No album found via artist browse fallback")
            except Exception as e:
                ws.log(f"Artist browse album fallback failed: {e}", level="warning")

    if album_title:
        # Sanitize before entity resolution
        try:
            from app.scraper.source_validation import sanitize_album
            album_title = sanitize_album(album_title, title=title)
        except Exception:
            pass
        try:
            resolved_album = resolve_album(
                artist, album_title,
                mb_release_id=metadata.get("mb_album_release_id"),
                skip_musicbrainz=_skip_mb,
                skip_wikipedia=_skip_wiki,
            )
        except Exception as e:
            ws.log(f"Album resolution warning: {e}", level="warning")

    # Propagate the parent-album release-group ID from the MusicBrainz
    # search into the resolved album dict so get_or_create_album can
    # set it on the AlbumEntity.  resolve_album() doesn't return this
    # field because it resolves by mb_release_id, not release-group.
    if resolved_album and metadata.get("mb_album_release_group_id"):
        resolved_album.setdefault("mb_release_group_id", metadata["mb_album_release_group_id"])

    try:
        resolved_track = resolve_track(
            artist, title,
            mb_recording_id=metadata.get("mb_recording_id"),
            skip_musicbrainz=_skip_mb,
            skip_wikipedia=_skip_wiki,
        )
    except Exception as e:
        ws.log(f"Track resolution warning: {e}", level="warning")

    # Build canonical track params
    version = ws.read_artifact("version_detection") or {}
    canonical_params = {
        "title": title,
        "year": metadata.get("year"),
        "mb_recording_id": metadata.get("mb_recording_id"),
        "mb_release_id": metadata.get("mb_release_id"),
        "mb_release_group_id": metadata.get("mb_release_group_id"),
        "mb_artist_id": metadata.get("mb_artist_id"),
        "version_type": version.get("version_type", "normal"),
        "original_artist": version.get("original_artist"),
        "original_title": version.get("original_title"),
        "genres": metadata.get("genres"),
        "resolved_track": resolved_track or None,
    }

    # Make resolved dicts JSON-serializable
    def _clean(d):
        if not isinstance(d, dict):
            return d
        cleaned = {}
        for k, v in d.items():
            if k == "assets":
                cleaned[k] = {ak: {"url": getattr(av, "url", str(av)),
                                    "kind": getattr(av, "kind", ak)}
                               for ak, av in (v or {}).items()} if isinstance(v, dict) else v
            elif hasattr(v, "__dict__") and not isinstance(v, (str, int, float, bool, list, dict)):
                cleaned[k] = str(v)
            else:
                cleaned[k] = v
        return cleaned

    ws.write_artifact("entity_resolution", {
        "artist": {"name": artist, "resolved": _clean(resolved_artist)},
        "album": {"title": album_title, "resolved": _clean(resolved_album)} if album_title else {},
        "track": {"title": title, "resolved": _clean(resolved_track)},
        "canonical_track": canonical_params,
    })
    ws.update_stage("resolve_entities", "complete")
    ws.log(f"Entities resolved: artist={resolved_artist.get('canonical_name', artist)}")


def _step_collect_source_links(ws: ImportWorkspace, artist: str, title: str,
                               metadata: dict, options: dict) -> None:
    """Collect source URLs (IMDB, Wikipedia, MusicBrainz) — all network I/O."""
    if ws.is_stage_complete("collect_sources"):
        return
    ws.update_stage("collect_sources", "running")

    opts = options.get("options", options)
    _skip_wiki = not (opts.get("scrape_wikipedia", True) or opts.get("ai_auto_analyse", False))
    _skip_mb = not (opts.get("scrape_musicbrainz", True) or opts.get("ai_auto_analyse", False))

    import re as _re
    links = {}

    # Wikipedia link (from scraper results) — classify by actual page type
    wiki_url = metadata.get("source_url")
    if wiki_url and "wikipedia.org" in wiki_url:
        page_id = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", wiki_url)
        scraper_sources = metadata.get("scraper_sources_used", [])
        prov = "ai" if any("wikipedia:ai" in s for s in scraper_sources) else "scraped"
        wiki_page_type = metadata.get("wiki_page_type", "single")
        if wiki_page_type in ("unrelated", "disambiguation"):
            pass  # Don't store disambiguation/unrelated pages as sources
        else:
            if wiki_page_type == "album":
                wiki_source_type = "album"
                wiki_key = "wikipedia_album"
            elif wiki_page_type == "artist":
                wiki_source_type = "artist"
                wiki_key = "wikipedia_artist"
            else:
                wiki_source_type = "single"
                wiki_key = "wikipedia_single"
            links[wiki_key] = {
                "provider": "wikipedia", "id": page_id, "url": wiki_url,
                "source_type": wiki_source_type, "provenance": prov,
            }

    # IMDB (always attempt — independent of wiki/MB toggles)
    if not metadata.get("imdb_url"):
        try:
            from app.scraper.metadata_resolver import search_imdb_music_video
            imdb_url = search_imdb_music_video(artist, title)
            if imdb_url:
                metadata["imdb_url"] = imdb_url
        except Exception:
            pass
    if metadata.get("imdb_url"):
        m = _re.search(r"(tt\d+|nm\d+)", metadata["imdb_url"])
        links["imdb"] = {
            "provider": "imdb", "id": m.group(1) if m else metadata["imdb_url"],
            "url": metadata["imdb_url"], "source_type": "video",
            "provenance": "scraped",
        }

    # MusicBrainz single / recording
    mb_rg = metadata.get("mb_release_group_id")
    mb_rec = metadata.get("mb_recording_id")
    if mb_rg:
        links["musicbrainz_single"] = {
            "provider": "musicbrainz", "id": mb_rg,
            "url": f"https://musicbrainz.org/release-group/{mb_rg}",
            "source_type": "single", "provenance": "scraped",
        }

    # MusicBrainz artist
    if metadata.get("mb_artist_id"):
        links["musicbrainz_artist"] = {
            "provider": "musicbrainz", "id": metadata["mb_artist_id"],
            "url": f"https://musicbrainz.org/artist/{metadata['mb_artist_id']}",
            "source_type": "artist", "provenance": "scraped",
        }

    # MusicBrainz album release-group
    mb_album_rg = metadata.get("mb_album_release_group_id")
    if mb_album_rg:
        links["musicbrainz_album"] = {
            "provider": "musicbrainz", "id": mb_album_rg,
            "url": f"https://musicbrainz.org/release-group/{mb_album_rg}",
            "source_type": "album", "provenance": "scraped",
        }

    # Wikipedia artist/album — prefer URLs already discovered by unified pipeline
    _pipeline_urls = metadata.get("_source_urls", {})
    if not _skip_wiki:
        # ── Artist ──
        wa_url = _pipeline_urls.get("wikipedia_artist")
        if not wa_url:
            try:
                from app.scraper.metadata_resolver import search_wikipedia_artist
                wa_url = search_wikipedia_artist(metadata.get("primary_artist") or artist)
                if not wa_url:
                    from app.scraper.metadata_resolver import extract_artist_wiki_url_from_page
                    _fallback_page = wiki_url if (wiki_url and "wikipedia.org" in (wiki_url or "")) else None
                    if _fallback_page:
                        wa_url = extract_artist_wiki_url_from_page(_fallback_page)
                        if wa_url:
                            ws.log(f"Artist wiki URL from single/album infobox fallback: {wa_url}")
            except Exception:
                pass
        if wa_url:
            page_id = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", wa_url)
            links["wikipedia_artist"] = {
                "provider": "wikipedia", "id": page_id, "url": wa_url,
                "source_type": "artist", "provenance": "scraped",
            }
        elif "wikipedia_artist" in links:
            ws.log(f"Removing unvalidated AI wikipedia_artist: {links['wikipedia_artist']['url']}")
            del links["wikipedia_artist"]

        # ── Album ──
        wl_url = _pipeline_urls.get("wikipedia_album")
        album_name = metadata.get("album")
        if not wl_url and album_name:
            try:
                from app.scraper.metadata_resolver import (
                    search_wikipedia_album, extract_album_wiki_url_from_single,
                )
                wl_url = search_wikipedia_album(artist, album_name)
                single_wiki = wiki_url if (wiki_url and "wikipedia.org" in (wiki_url or "")) else None
                infobox_url = extract_album_wiki_url_from_single(single_wiki) if single_wiki else None
                if infobox_url:
                    wl_url = infobox_url
            except Exception:
                pass
        if wl_url:
            page_id = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", wl_url)
            links["wikipedia_album"] = {
                "provider": "wikipedia", "id": page_id, "url": wl_url,
                "source_type": "album", "provenance": "scraped",
            }

        # ── Single ──
        if "wikipedia_single" not in links:
            _ws_url = _pipeline_urls.get("wikipedia")
            if not _ws_url and not metadata.get("_wiki_single_rejected"):
                try:
                    from app.scraper.metadata_resolver import (
                        search_wikipedia, scrape_wikipedia_page,
                        detect_article_mismatch,
                    )
                    _ws_url = search_wikipedia(title, metadata.get("primary_artist") or artist)
                    if _ws_url:
                        _wiki_data = scrape_wikipedia_page(_ws_url)
                        _mismatch = detect_article_mismatch(
                            _wiki_data,
                            metadata.get("primary_artist") or artist,
                            title,
                        )
                        if _mismatch:
                            ws.log(f"Wikipedia single search mismatch: {_mismatch}. Discarding {_ws_url}")
                            _ws_url = None
                except Exception:
                    pass
            if _ws_url:
                page_id = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", _ws_url)
                links["wikipedia_single"] = {
                    "provider": "wikipedia", "id": page_id, "url": _ws_url,
                    "source_type": "single", "provenance": "scraped",
                }

        # Fallback: extract single wiki URL from album tracklist
        if "wikipedia_single" not in links and "wikipedia_album" in links:
            try:
                from app.scraper.metadata_resolver import extract_single_wiki_url_from_album
                _album_url = links["wikipedia_album"]["url"]
                ws_url = extract_single_wiki_url_from_album(_album_url, title)
                if ws_url:
                    ws.log(f"Single wiki URL from album tracklist fallback: {ws_url}")
                    page_id = _re.sub(r"^https?://en\.wikipedia\.org/wiki/", "", ws_url)
                    links["wikipedia_single"] = {
                        "provider": "wikipedia", "id": page_id, "url": ws_url,
                        "source_type": "single", "provenance": "scraped",
                    }
            except Exception:
                pass

    ws.write_artifact("source_links", links)
    ws.update_stage("collect_sources", "complete")


def _step_fetch_artwork(ws: ImportWorkspace, folder: str, artist: str, title: str,
                        resolution_label: str, metadata: dict) -> None:
    """Fetch poster from scraper results."""
    if ws.is_stage_complete("fetch_artwork"):
        return
    ws.update_stage("fetch_artwork", "running")

    from app.scraper.metadata_resolver import download_image
    from app.pipeline_lib.services.file_organizer import build_folder_name

    assets = []
    image_url = metadata.get("image_url")
    # Don't use album cover art as video poster — album art belongs in
    # album_thumb, not the video poster.
    if metadata.get("wiki_page_type") == "album":
        image_url = None
    if image_url and folder:
        folder_name = build_folder_name(artist, title, resolution_label)
        poster_path = os.path.join(folder, f"{folder_name}-poster.jpg")
        if download_image(image_url, poster_path):
            assets.append({
                "asset_type": "poster",
                "file_path": poster_path,
                "source_url": image_url,
                "provenance": "scraper",
            })

    ws.write_artifact("artwork_results", {"assets": assets})
    ws.update_stage("fetch_artwork", "complete")


def _step_build_mutation_plan(ws: ImportWorkspace) -> None:
    """Build the mutation plan from all workspace artifacts."""
    ws.update_stage("build_plan", "running")
    plan = build_plan_from_workspace(ws)
    ws.write_artifact("mutation_plan", plan)
    ws.update_stage("build_plan", "complete")


# ═══════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════

class _DuplicateSkip(Exception):
    """Raised to cleanly exit the pipeline when a duplicate is detected."""
    def __init__(self, existing_video_id: int = None, match_type: str = "",
                 reason: str = ""):
        self.existing_video_id = existing_video_id
        self.match_type = match_type
        self.reason = reason
        super().__init__(reason)


def _find_video_by_folder(folder_path: str) -> Optional[int]:
    """Look up a VideoItem by its folder_path for duplicate flagging."""
    from app.database import SessionLocal
    from app.models import VideoItem

    db = SessionLocal()
    try:
        item = db.query(VideoItem).filter(
            VideoItem.folder_path == folder_path,
        ).first()
        return item.id if item else None
    except Exception:
        return None
    finally:
        db.close()


def _flag_existing_for_duplicate_review(existing_video_id: int,
                                        job_id: int,
                                        reason: str) -> None:
    """Flag an existing library item for review when a duplicate import is skipped."""
    from app.database import SessionLocal
    from app.models import VideoItem

    db = SessionLocal()
    try:
        item = db.query(VideoItem).get(existing_video_id)
        if item and item.review_status in (None, "none"):
            item.review_status = "needs_human_review"
            item.review_reason = (
                f"Duplicate import skipped (job {job_id}): {reason}"
            )[:500]
            db.commit()
            logger.info(f"Flagged video {existing_video_id} for duplicate review")
    except Exception as e:
        db.rollback()
        logger.warning(f"Failed to flag video {existing_video_id} for review: {e}")
    finally:
        db.close()


def _cleanup_organized_artifacts(ws: ImportWorkspace) -> None:
    """Remove files placed in the library during Stage B when the import
    is being rolled back (e.g. TOCTOU duplicate detected in Stage C)."""
    organized = ws.read_artifact("organized")
    if not organized:
        return

    # Remove the organized video file
    new_file = organized.get("new_file")
    if new_file and os.path.isfile(new_file):
        try:
            os.remove(new_file)
            ws.log(f"Cleaned up organized file: {os.path.basename(new_file)}")
        except Exception as e:
            ws.log(f"Failed to clean up {new_file}: {e}", level="warning")

    # Remove copied artwork assets
    artwork_source = ws.read_artifact("artwork_source") or {}
    for asset in artwork_source.get("assets", []):
        art_path = asset.get("file_path")
        if art_path and os.path.isfile(art_path):
            try:
                os.remove(art_path)
            except Exception:
                pass

    # Remove artwork fetched during scraping
    artwork_results = ws.read_artifact("artwork_results") or {}
    for asset in artwork_results.get("assets", []):
        art_path = asset.get("file_path")
        if art_path and os.path.isfile(art_path):
            try:
                os.remove(art_path)
            except Exception:
                pass

    # Remove NFO file
    new_folder = organized.get("new_folder", "")
    if new_folder and os.path.isdir(new_folder):
        for f in os.listdir(new_folder):
            if f.endswith(".nfo"):
                try:
                    os.remove(os.path.join(new_folder, f))
                except Exception:
                    pass

    # Remove folder only if now empty
    if new_folder and os.path.isdir(new_folder):
        try:
            os.rmdir(new_folder)  # Only removes if empty
            ws.log(f"Cleaned up empty folder: {os.path.basename(new_folder)}")
        except OSError:
            pass  # Folder not empty, leave it


def _load_job_params(job_id: int) -> Optional[dict]:
    """Load ProcessingJob params from DB."""
    from app.database import SessionLocal
    from app.models import ProcessingJob
    db = SessionLocal()
    try:
        job = db.query(ProcessingJob).get(job_id)
        if not job or not job.input_params:
            return None
        return dict(job.input_params)
    finally:
        db.close()


def _ensure_terminal(job_id: int) -> None:
    """Guarantee the job reaches a terminal status."""
    import time
    from app.database import CosmeticSessionLocal
    from app.models import ProcessingJob, JobStatus

    _TERMINAL = {"complete", "failed", "cancelled", "skipped"}

    for attempt in range(20):
        db = CosmeticSessionLocal()
        try:
            job = db.query(ProcessingJob).get(job_id)
            if not job or job.status in _TERMINAL or (hasattr(job.status, 'value') and job.status.value in _TERMINAL):
                return
            job.status = JobStatus.failed
            if not job.error_message:
                job.error_message = "Pipeline exited without setting terminal status"
            if not job.completed_at:
                job.completed_at = datetime.now(timezone.utc)
            db.commit()
            logger.warning(f"[Job {job_id}] _ensure_terminal forced status to failed")
            return
        except Exception as e:
            db.rollback()
            if attempt < 19:
                time.sleep(min(1 + attempt, 10))
                logger.debug(f"_ensure_terminal retry {attempt+1}/20 for job {job_id}: {e}")
            else:
                logger.error(f"_ensure_terminal FAILED after 20 attempts for job {job_id}: {e}")
        finally:
            db.close()


def _coarse_update(job_id: int, status_enum=None, step: str = None,
                   progress: int = None, error: str = None,
                   display_name: str = None, video_id: int = None) -> None:
    """Minimal DB status update.  Called only at key milestones."""
    import time
    from app.database import CosmeticSessionLocal
    from app.models import ProcessingJob, JobStatus
    from datetime import datetime, timezone
    from sqlalchemy.orm.attributes import flag_modified

    is_terminal = (
        status_enum is not None
        and hasattr(status_enum, "value")
        and status_enum.value in ("failed", "complete", "cancelled", "skipped")
    )
    max_attempts = 10 if is_terminal else 3

    for attempt in range(max_attempts):
        db = CosmeticSessionLocal()
        try:
            job = db.query(ProcessingJob).get(job_id)
            if not job:
                return
            if status_enum is not None:
                job.status = status_enum
            if step is not None:
                job.current_step = step
                steps = list(job.pipeline_steps or [])
                steps.append({"step": step, "status": "success"})
                job.pipeline_steps = steps
                flag_modified(job, "pipeline_steps")
            if progress is not None:
                job.progress_percent = progress
            if display_name is not None:
                job.display_name = display_name
            if error is not None:
                job.error_message = error
                job.completed_at = datetime.now(timezone.utc)
            if video_id is not None:
                job.video_id = video_id
            if status_enum and hasattr(status_enum, "value") and status_enum.value in ("complete", "skipped"):
                job.completed_at = datetime.now(timezone.utc)
            db.commit()
            return
        except Exception as e:
            db.rollback()
            if attempt < max_attempts - 1:
                delay = min(1 + attempt * 2, 8)
                logger.warning(f"Coarse status update retry {attempt+1}/{max_attempts} for job {job_id}: {e}")
                time.sleep(delay)
            else:
                logger.error(f"Coarse status update FAILED after {max_attempts} attempts for job {job_id}: {e}")
        finally:
            db.close()


def _get_job_status(name: str):
    """Get a JobStatus enum by name."""
    from app.models import JobStatus
    return getattr(JobStatus, name)


def _derive_resolution(height: Optional[int]) -> str:
    """Derive resolution label from pixel height."""
    if not height:
        return ""
    if height >= 2160:
        return "2160p"
    if height >= 1440:
        return "1440p"
    if height >= 1080:
        return "1080p"
    if height >= 720:
        return "720p"
    if height >= 480:
        return "480p"
    if height >= 360:
        return "360p"
    return f"{height}p"
