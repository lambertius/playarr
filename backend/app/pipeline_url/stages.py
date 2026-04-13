# AUTO-SEPARATED from pipeline/stages.py for pipeline_url pipeline
# This file is independent â€” changes here do NOT affect the other pipeline.
"""
URL import pipeline orchestrator.

Public entry point:
  run_url_import_pipeline(job_id, url, **opts) â€” for import_video_task

Pipeline stages:
  Stage A â€” coarse DB status update (milliseconds)
  Stage B â€” workspace build (parallel, no locks, no DB writes)
  Stage C â€” serial DB apply (short transaction under _apply_lock)
  Stage D â€” deferred enrichment tasks dispatched as background work
"""
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Dict, Optional

from app.pipeline_url.workspace import ImportWorkspace
from app.pipeline_url.mutation_plan import build_plan_from_workspace
from app.pipeline_url.db_apply import apply_mutation_plan

logger = logging.getLogger(__name__)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  PUBLIC API
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def run_url_import_pipeline(job_id: int, url: str, **opts) -> None:
    """Full staged pipeline for a URL import."""
    from app.models import JobStatus
    from app.worker import clear_cancel, JobCancelledError

    ws = ImportWorkspace(job_id)
    try:
        ws.reset()  # clear stale artifacts from recycled job IDs
        _coarse_update(job_id, JobStatus.downloading, step="Starting import", progress=5)
        ws.write_artifact("input", {
            "import_type": "url",
            "url": url,
            "mode": "advanced",
            **opts,
        })
        ws.log(f"URL import started: {url}")

        # Stage B
        _url_stage_b(ws, job_id, url, opts)
        ws.sync_logs_to_db()

        # Stage C
        ws.update_stage("apply", "running")
        _coarse_update(job_id, JobStatus.analyzing, step="Applying to database", progress=85)
        plan = ws.read_artifact("mutation_plan")
        video_id = apply_mutation_plan(plan)
        ws.update_stage("apply", "complete")
        ws.write_artifact("apply_result", {"video_id": video_id})
        ws.log(f"Applied to DB â€” video_id={video_id}")
        # Update display name to resolved artist - title with job type
        _artist_d = plan['video'].get('artist', '')
        _title_d = plan['video'].get('title', '')
        _display = f"{_artist_d} \u2013 {_title_d}"
        # Read action_label from job to include job type in display name
        from app.database import CosmeticSessionLocal as _LabelSL
        from app.models import ProcessingJob as _LabelPJ
        _label_db = _LabelSL()
        try:
            _label_job = _label_db.query(_LabelPJ).get(job_id)
            if _label_job and _label_job.action_label:
                _display = f"{_display} \u203a {_label_job.action_label}"
        finally:
            _label_db.close()
        _coarse_update(job_id, display_name=_display)

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
                    from app.tasks import _set_processing_flag
                    _set_processing_flag(_xml_db, _xml_video, "xml_exported", method="url_import")
                    _xml_db.commit()
                    ws.log("Playarr XML sidecar written")
            finally:
                _xml_db.close()
        except Exception as e:
            ws.log(f"Playarr XML write warning: {e}", level="warning")

        # Terminal status is set atomically inside apply_mutation_plan.
        ws.log("Import complete")

        # Stage D (cleanup handled by dispatch_deferred)
        from app.pipeline_url.deferred import dispatch_deferred
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
                       progress=100)
        # Link the skipped job to the existing video so UI can show poster/link
        if dup.existing_video_id:
            from app.database import CosmeticSessionLocal as _SkipSL
            _skip_db = _SkipSL()
            try:
                _skip_job = _skip_db.query(ProcessingJob).get(job_id)
                if _skip_job:
                    _skip_job.video_id = dup.existing_video_id
                    _skip_db.commit()
            finally:
                _skip_db.close()
        ws.sync_logs_to_db()
        ws.cleanup_on_success()
    except Exception as e:
        logger.error(f"[Job {job_id}] URL import failed: {e}", exc_info=True)
        _coarse_update(job_id, JobStatus.failed, error=str(e)[:2000])
        ws.log(f"FAILED: {e}", level="error")
        ws.sync_logs_to_db()
    finally:
        clear_cancel(job_id)
        _ensure_terminal(job_id)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  STAGE B â€” URL IMPORT
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _url_stage_b(ws: ImportWorkspace, job_id: int, url: str, opts: dict) -> None:
    """Build workspace for a URL import.  NO locks, NO DB writes."""
    from app.worker import is_cancelled, JobCancelledError
    from app.pipeline_url.services.downloader import download_video
    from app.services.downloader import extract_metadata_from_ytdlp

    def _check():
        if is_cancelled(job_id):
            raise JobCancelledError(f"Job {job_id} cancelled")

    # B0: Quick exact-URL duplicate check
    _step_check_url_duplicate(ws, url)
    _check()

    # B1: Identify provider
    _step_identify_provider(ws, url)
    _check()
    provider_data = ws.read_artifact("provider")
    canonical = provider_data["canonical_url"]

    # B2: Check for existing + quality upgrade
    _step_check_existing(ws, provider_data)
    _check()

    # B3: Download
    _step_download(ws, job_id, url, opts)
    _check()
    download_data = ws.read_artifact("download")
    downloaded_file = download_data["file_path"]

    # B4: Analyze media
    _step_analyze_media(ws, downloaded_file)
    _check()
    ffprobe = ws.read_artifact("ffprobe") or {}

    # B5: Extract yt-dlp metadata
    if download_data.get("info_dict"):
        ytdlp_meta = extract_metadata_from_ytdlp(download_data["info_dict"])
        ws.write_artifact("ytdlp_metadata", ytdlp_meta or {})

    # B6: Resolve metadata
    ytdlp_meta = ws.read_artifact("ytdlp_metadata") or {}
    artist, title = _determine_artist_title_from_ytdlp(
        ytdlp_meta, opts.get("artist_override"), opts.get("title_override"),
        downloaded_file
    )
    identity = {"artist": artist, "title": title}
    ws.write_artifact("parsed_identity", identity)

    _coarse_update(job_id, step="Resolving metadata", progress=60)
    _step_resolve_metadata_url(ws, artist, title, canonical, ytdlp_meta, ffprobe, opts)
    _check()

    # Inject AI failures into the job record so the frontend can display them
    _ai_fail_entries = ws.read_artifact("ai_failures") or []
    if _ai_fail_entries:
        try:
            from app.pipeline_url.write_queue import db_write_soon
            from app.models import ProcessingJob as _PJ
            from sqlalchemy.orm.attributes import flag_modified as _flag_mod
            from app.database import CosmeticSessionLocal

            _captured_failures = list(_ai_fail_entries)

            def _inject_ai_failures():
                _ai_db = CosmeticSessionLocal()
                try:
                    _ai_job = _ai_db.query(_PJ).get(job_id)
                    if _ai_job:
                        steps = list(_ai_job.pipeline_steps or [])
                        for f in _captured_failures:
                            steps.append({
                                "step": f.get("description", "AI error"),
                                "status": "failed",
                                "type": "ai_error",
                                "code": f.get("code", ""),
                            })
                        _ai_job.pipeline_steps = steps
                        _flag_mod(_ai_job, "pipeline_steps")
                        _ai_db.commit()
                finally:
                    _ai_db.close()

            db_write_soon(_inject_ai_failures)
        except Exception as _ai_inject_err:
            ws.log(f"Could not inject AI failure tags into job record: "
                   f"{_ai_inject_err}", level="warning")
            ws.write_artifact("ai_inject_failed", True)

    metadata = ws.read_artifact("scraper_results") or {}
    final_artist = metadata.get("artist") or artist
    final_title = metadata.get("title") or title

    # B7: Version detection
    _step_detect_version_url(ws, final_artist, final_title, ffprobe,
                             ytdlp_meta, downloaded_file, opts)
    _check()

    version = ws.read_artifact("version_detection") or {}
    # Apply cover corrections to final artist/title
    if version.get("version_type") == "cover":
        if version.get("performing_artist"):
            final_artist = version["performing_artist"]
            metadata["artist"] = final_artist
        if version.get("detected_title"):
            final_title = version["detected_title"]
            metadata["title"] = final_title
        if metadata != (ws.read_artifact("scraper_results") or {}):
            ws.write_artifact("scraper_results", metadata)

    resolution_label = _derive_resolution(ffprobe.get("height"))

    # B7b: Enhanced duplicate check (version-aware, after metadata resolution)
    _step_duplicate_check(ws, final_artist, final_title,
                          version.get("version_type", "normal"))
    _check()

    # If possible duplicate detected, merge review flags into version artifact
    dup_check = ws.read_artifact("duplicate_check") or {}
    if dup_check.get("is_possible_duplicate"):
        version["review_status"] = "needs_human_review"
        version["needs_review"] = True
        _dup_reason = dup_check.get("reason", "Possible duplicate")
        existing_reason = version.get("review_reason") or ""
        version["review_reason"] = (
            f"{existing_reason}; {_dup_reason}" if existing_reason
            else _dup_reason
        )
        ws.write_artifact("version_detection", version)

    # B8: Organize file
    existing_data = ws.read_artifact("existing_check") or {}
    existing_folder = existing_data.get("existing_folder")
    _step_organize_file_url(ws, downloaded_file, final_artist, final_title,
                            resolution_label, version, existing_folder)
    _check()
    organized = ws.read_artifact("organized") or {}
    new_file = organized.get("new_file")
    if not new_file:
        raise RuntimeError("Organized artifact missing file path")
    new_folder = organized.get("new_folder", "")

    # B9: Normalize audio
    _step_normalize_audio(ws, new_file, opts)
    _check()

    # B10: Write NFO
    _step_write_nfo_url(ws, new_folder, final_artist, final_title, metadata,
                        canonical, resolution_label, version)

    # B11: Fetch poster
    _step_fetch_artwork_url(ws, new_folder, final_artist, final_title,
                            resolution_label, metadata, ytdlp_meta,
                            download_data.get("info_dict"))

    _coarse_update(job_id, step="Resolving entities", progress=75,
                   display_name=f"{final_artist} \u2013 {final_title} \u203a Advanced Import")
    ws.sync_logs_to_db()

    # B12: Entity resolution
    _step_resolve_entities(ws, final_artist, final_title, metadata, opts)
    _check()

    # B13: Source links
    _step_collect_source_links(ws, final_artist, final_title, metadata, opts)

    # Store URL import-specific data in input for mutation plan builder
    input_data = ws.read_artifact("input") or {}
    input_data.update({
        "canonical_url": canonical,
        "provider": provider_data.get("provider"),
        "provider_video_id": provider_data.get("video_id"),
        "existing_video_id": existing_data.get("existing_video_id"),
        "channel_name": ytdlp_meta.get("uploader") or ytdlp_meta.get("channel"),
        "platform_title": ytdlp_meta.get("title"),
        "platform_description": (ytdlp_meta.get("description") or "")[:3000],
        "platform_tags": ytdlp_meta.get("tags"),
        "upload_date": ytdlp_meta.get("upload_date"),
    })
    ws.write_artifact("input", input_data)

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

    # B14: Build mutation plan
    _step_build_mutation_plan(ws)
    ws.log("Workspace build complete")


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  INDIVIDUAL STEPS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _step_analyze_media(ws: ImportWorkspace, file_path: str) -> None:
    """Extract quality signature and measure loudness."""
    if ws.is_stage_complete("analyze_media"):
        return
    ws.update_stage("analyze_media", "running")

    from app.pipeline_url.services.media_analyzer import extract_quality_signature, measure_loudness

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


def _step_organize_file_url(ws: ImportWorkspace, downloaded_file: str,
                            artist: str, title: str, resolution_label: str,
                            version: dict, existing_folder: Optional[str]) -> None:
    """Organize a downloaded file into the library."""
    if ws.is_stage_complete("organize_file"):
        return
    ws.update_stage("organize_file", "running")

    from app.pipeline_url.services.file_organizer import organize_file

    new_folder, new_file = organize_file(
        downloaded_file, artist, title, resolution_label,
        existing_folder=existing_folder,
        version_type=version.get("version_type", "normal"),
        alternate_version_label=version.get("alternate_version_label", ""),
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


def _step_normalize_audio(ws: ImportWorkspace, file_path: str, options: dict) -> None:
    """Normalize audio if requested."""
    normalize = options.get("normalize_audio", False) or options.get("normalize", False)
    if not normalize:
        ws.update_stage("normalize_audio", "skipped")
        return
    if ws.is_stage_complete("normalize_audio"):
        return
    ws.update_stage("normalize_audio", "running")

    from app.pipeline_url.services.normalizer import normalize_video

    try:
        before, after, gain = normalize_video(file_path)
        if before is not None:
            ws.write_artifact("normalized", {
                "before_lufs": before,
                "after_lufs": after,
                "gain_db": gain,
            })
            ws.log(f"Normalized: {before:.1f} â†’ {after:.1f} LUFS")
            ws.update_stage("normalize_audio", "complete")
        else:
            ws.update_stage("normalize_audio", "skipped")
    except Exception as e:
        ws.log(f"Normalization error: {e}", level="warning")
        ws.update_stage("normalize_audio", "failed")


def _step_write_nfo_url(ws, folder, artist, title, metadata, canonical,
                        resolution_label, version) -> None:
    """Write NFO for URL import."""
    if ws.is_stage_complete("nfo_write"):
        return
    ws.update_stage("nfo_write", "running")

    from app.pipeline_url.services.file_organizer import write_nfo_file

    try:
        write_nfo_file(
            folder, artist=artist, title=title,
            album=metadata.get("album", ""),
            year=metadata.get("year"),
            genres=metadata.get("genres", []),
            plot=metadata.get("plot", ""),
            source_url=canonical,
            resolution_label=resolution_label,
            version_type=version.get("version_type", "normal"),
            alternate_version_label=version.get("alternate_version_label", ""),
            original_artist=version.get("original_artist", ""),
            original_title=version.get("original_title", ""),
        )
        ws.update_stage("nfo_write", "complete")
    except Exception as e:
        ws.log(f"NFO write warning: {e}", level="warning")
        ws.update_stage("nfo_write", "failed")


def _step_ytdlp_metadata(ws: ImportWorkspace) -> None:
    """Fetch yt-dlp metadata from a matched YouTube source."""
    yt = ws.read_artifact("youtube_match")
    if not yt or not yt.get("url"):
        return
    if ws.has_artifact("ytdlp_metadata"):
        return

    from app.services.downloader import get_available_formats, extract_metadata_from_ytdlp

    try:
        _formats, info = get_available_formats(yt["url"])
        meta = extract_metadata_from_ytdlp(info) if info else {}
        ws.write_artifact("ytdlp_metadata", meta)
    except Exception as e:
        ws.log(f"yt-dlp metadata warning: {e}", level="warning")


def _step_resolve_metadata_url(ws: ImportWorkspace, artist: str, title: str,
                               canonical: str, ytdlp_meta: dict, ffprobe: dict,
                               opts: dict) -> None:
    """Metadata resolution for URL imports."""
    if ws.is_stage_complete("resolve_metadata"):
        return
    ws.update_stage("resolve_metadata", "running")

    _skip_ai = not (opts.get("ai_auto_analyse", False) or opts.get("ai_auto_fallback", False))
    _skip_wiki = not (opts.get("scrape", True) or opts.get("ai_auto_analyse", False))
    _skip_mb = not (opts.get("scrape_musicbrainz", True) or opts.get("ai_auto_analyse", False))

    from app.scraper.unified_metadata import resolve_metadata_unified
    from app.database import SessionLocal

    _db = SessionLocal()
    try:
        metadata = resolve_metadata_unified(
            artist=artist, title=title,
            db=_db,
            source_url=canonical,
            platform_title=ytdlp_meta.get("title", ""),
            channel_name=ytdlp_meta.get("uploader") or ytdlp_meta.get("channel") or "",
            platform_description=(ytdlp_meta.get("description") or "")[:3000],
            platform_tags=ytdlp_meta.get("tags") or [],
            upload_date=ytdlp_meta.get("upload_date") or "",
            filename=os.path.basename(ws.read_artifact("download", ).get("file_path", "")),
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

    if metadata.get("plot"):
        try:
            from app.pipeline_url.services.ai_summary import generate_ai_summary
            summary = generate_ai_summary(metadata["plot"], source_url=canonical)
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


def _step_detect_version_url(ws, artist, title, ffprobe, ytdlp_meta,
                             downloaded_file, opts) -> None:
    """Version detection for URL imports with richer signals."""
    if ws.is_stage_complete("detect_version"):
        return
    ws.update_stage("detect_version", "running")

    from app.pipeline_url.matching.version_detector import detect_version_type
    from app.database import SessionLocal
    from app.models import VideoItem

    db = SessionLocal()
    try:
        _existing_items = []
        _lib_dupes = db.query(VideoItem).filter(
            VideoItem.title.ilike(f"%{title[:50]}%")
        ).all()
        for vi in _lib_dupes:
            _existing_items.append({
                "id": vi.id, "artist": vi.artist, "title": vi.title,
                "version_type": getattr(vi, "version_type", "normal"),
            })
    finally:
        db.close()

    try:
        raw_title = ytdlp_meta.get("title", "")
        vc = detect_version_type(
            filename=os.path.basename(downloaded_file) if downloaded_file else "",
            source_title=raw_title,
            uploader=ytdlp_meta.get("uploader") or ytdlp_meta.get("channel") or "",
            description=(ytdlp_meta.get("description") or "")[:2000],
            parsed_artist=artist, parsed_title=title,
            fingerprint_artist="", fingerprint_title="",
            scraped_artist=artist, scraped_title=title,
            duration_seconds=ffprobe.get("duration_seconds"),
            existing_library_items=_existing_items or None,
            hint_cover=opts.get("hint_cover", False),
            hint_live=opts.get("hint_live", False),
            hint_alternate=opts.get("hint_alternate", False),
            hint_uncensored=opts.get("hint_uncensored", False),
            hint_alternate_label=opts.get("hint_alternate_label", ""),
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


def _step_duplicate_check(ws: ImportWorkspace, artist: str, title: str,
                          version_type: str) -> None:
    """Version-aware duplicate check after metadata + version detection.

    - Same artist+title, same version_type â†’ hard duplicate â†’ raise _DuplicateSkip
    - Same artist+title, different version_type â†’ possible duplicate â†’ flag for review
    """
    if ws.is_stage_complete("duplicate_check"):
        return
    ws.update_stage("duplicate_check", "running")

    from app.database import SessionLocal
    from app.models import VideoItem
    from app.scraper.source_validation import parse_multi_artist

    incoming_version = version_type or "normal"
    db = SessionLocal()
    try:
        # Same artist+title match (mirroring TOCTOU check logic)
        existing = db.query(VideoItem).filter(
            VideoItem.artist.ilike(artist),
            VideoItem.title.ilike(title),
        ).first()
        if not existing:
            query_primary, _ = parse_multi_artist(artist)
            qp_lower = query_primary.lower()
            title_matches = db.query(VideoItem).filter(
                VideoItem.title.ilike(title),
            ).all()
            for candidate in title_matches:
                db_primary, _ = parse_multi_artist(candidate.artist or "")
                dp_lower = db_primary.lower()
                if (dp_lower == qp_lower
                        or qp_lower.startswith(dp_lower)
                        or dp_lower.startswith(qp_lower)):
                    existing = candidate
                    break

        if not existing:
            ws.write_artifact("duplicate_check", {
                "is_duplicate": False,
                "is_possible_duplicate": False,
            })
            ws.update_stage("duplicate_check", "complete")
            return

        # Guard: if the matched item's file is missing, it's a zombie record — ignore it
        if existing.file_path and not os.path.exists(existing.file_path):
            ws.log(f"Ignoring zombie record id={existing.id} (file missing: {existing.file_path})")
            ws.write_artifact("duplicate_check", {
                "is_duplicate": False,
                "is_possible_duplicate": False,
            })
            ws.update_stage("duplicate_check", "complete")
            return

        existing_version = getattr(existing, "version_type", "normal") or "normal"

        # Different version types â†’ possible duplicate, proceed but flag for review
        if existing_version != incoming_version:
            ws.log(
                f"Possible duplicate: existing id={existing.id} "
                f"(version={existing_version}) vs incoming (version={incoming_version}). "
                f"Proceeding with import, flagging for review."
            )
            ws.write_artifact("duplicate_check", {
                "is_duplicate": False,
                "is_possible_duplicate": True,
                "existing_video_id": existing.id,
                "existing_artist": existing.artist,
                "existing_title": existing.title,
                "existing_version_type": existing_version,
                "incoming_version_type": incoming_version,
                "reason": (
                    f"Possible duplicate of '{existing.artist} - {existing.title}' "
                    f"(id={existing.id}, existing: {existing_version}, "
                    f"new: {incoming_version})"
                ),
            })
            ws.update_stage("duplicate_check", "complete")
            return

        # Same version type â†’ hard duplicate â†’ skip
        ws.log(f"Duplicate detected: '{existing.artist} - {existing.title}' "
               f"(id={existing.id}, version={existing_version}), skipping")
        ws.update_stage("duplicate_check", "complete")
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


def _step_resolve_entities(ws: ImportWorkspace, artist: str, title: str,
                           metadata: dict, options: dict) -> None:
    """Resolve artist/album/track entities via network (NO DB writes)."""
    if ws.is_stage_complete("resolve_entities"):
        return
    ws.update_stage("resolve_entities", "running")

    opts = options.get("options", options)
    _skip_mb = not (opts.get("scrape_musicbrainz", True) or opts.get("ai_auto_analyse", False))
    _skip_wiki = not (opts.get("scrape", True) or opts.get("ai_auto_analyse", False))

    from app.pipeline_url.metadata.resolver import resolve_artist, resolve_album, resolve_track

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

    # When no album title was resolved, use _find_parent_album() -- the same
    # function resolve_metadata_unified() uses -- to find the parent Album
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
                # Convert AssetCandidate objects to dicts
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
    """Collect source URLs (IMDB, Wikipedia, MusicBrainz) â€” all network I/O."""
    if ws.is_stage_complete("collect_sources"):
        return
    ws.update_stage("collect_sources", "running")

    opts = options.get("options", options)
    _skip_wiki = not (opts.get("scrape", True) or opts.get("ai_auto_analyse", False))
    _skip_mb = not (opts.get("scrape_musicbrainz", True) or opts.get("ai_auto_analyse", False))

    import re as _re
    links = {}

    # Wikipedia link (from scraper results) â€” classify by actual page type
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
        # Release group found â†’ confirmed single/EP
        links["musicbrainz_single"] = {
            "provider": "musicbrainz", "id": mb_rg,
            "url": f"https://musicbrainz.org/release-group/{mb_rg}",
            "source_type": "single", "provenance": "scraped",
        }
    # When no single release group exists (only a recording ID),
    # do NOT create a source â€” the recording ID is preserved on
    # the VideoItem but there is no meaningful MB page to link.

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

    # Wikipedia artist/album â€” prefer URLs already discovered by unified pipeline
    _pipeline_urls = metadata.get("_source_urls", {})
    if not _skip_wiki:
        # â”€â”€ Artist â”€â”€
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

        # â”€â”€ Album â”€â”€
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

        # â”€â”€ Single â”€â”€
        if "wikipedia_single" not in links:
            _ws_url = _pipeline_urls.get("wikipedia")
            if not _ws_url:
                try:
                    from app.scraper.metadata_resolver import search_wikipedia
                    _ws_url = search_wikipedia(title, metadata.get("primary_artist") or artist)
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


def _step_fetch_artwork_url(ws, folder, artist, title, resolution_label,
                            metadata, ytdlp_meta, info_dict) -> None:
    """Fetch poster for URL imports (scraper or YouTube thumbnail)."""
    if ws.is_stage_complete("fetch_artwork"):
        return
    ws.update_stage("fetch_artwork", "running")

    from app.scraper.metadata_resolver import download_image
    from app.pipeline_url.services.file_organizer import build_folder_name

    assets = []
    image_url = metadata.get("image_url")
    # Don't use album cover art as video poster â€” fall back to YouTube thumb.
    if metadata.get("wiki_page_type") == "album":
        image_url = None
    if not image_url and info_dict:
        from app.services.downloader import get_best_thumbnail_url
        image_url = get_best_thumbnail_url(info_dict)

    if image_url and folder:
        folder_name = build_folder_name(artist, title, resolution_label)
        poster_path = os.path.join(folder, f"{folder_name}-poster.jpg")
        if download_image(image_url, poster_path):
            assets.append({
                "asset_type": "poster",
                "file_path": poster_path,
                "source_url": image_url,
                "provenance": "scraper" if metadata.get("image_url") else "youtube_thumb",
            })

    ws.write_artifact("artwork_results", {"assets": assets})
    ws.update_stage("fetch_artwork", "complete")


def _step_build_mutation_plan(ws: ImportWorkspace) -> None:
    """Build the mutation plan from all workspace artifacts."""
    ws.update_stage("build_plan", "running")
    plan = build_plan_from_workspace(ws)
    ws.write_artifact("mutation_plan", plan)
    ws.update_stage("build_plan", "complete")


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  URL IMPORT â€” PROVIDER + DOWNLOAD STEPS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _step_check_url_duplicate(ws: ImportWorkspace, url: str) -> None:
    """Quick exact-URL duplicate check before any heavy processing."""
    if ws.is_stage_complete("check_url_duplicate"):
        return
    ws.update_stage("check_url_duplicate", "running")

    from app.database import SessionLocal
    from app.models import Source

    db = SessionLocal()
    try:
        existing = db.query(Source).filter(
            Source.original_url == url,
        ).first()
        if existing and existing.video_item:
            item = existing.video_item
            # Guard: if the matched item's file is missing, it's a zombie — ignore
            if item.file_path and not os.path.exists(item.file_path):
                ws.log(f"Ignoring zombie record id={item.id} (file missing: {item.file_path})")
            else:
                ws.log(f"Exact URL already imported: '{item.artist} - {item.title}' (id={item.id})")
                ws.update_stage("check_url_duplicate", "complete")
                raise _DuplicateSkip(
                    existing_video_id=item.id,
                    match_type="exact_url",
                    reason=(
                        f"URL already imported as "
                        f"'{item.artist} - {item.title}' (id={item.id})"
                    ),
                )
    finally:
        db.close()

    ws.update_stage("check_url_duplicate", "complete")


def _step_identify_provider(ws: ImportWorkspace, url: str) -> None:
    """Identify source provider and canonicalize URL."""
    if ws.is_stage_complete("identify_provider"):
        return
    ws.update_stage("identify_provider", "running")

    from app.services.url_utils import identify_provider, canonicalize_url

    provider, video_id = identify_provider(url)
    canonical = canonicalize_url(provider, video_id)
    ws.write_artifact("provider", {
        "provider": provider.value if hasattr(provider, "value") else str(provider),
        "video_id": video_id,
        "canonical_url": canonical,
        "original_url": url,
    })
    ws.log(f"Provider: {provider}, ID: {video_id}")
    ws.update_stage("identify_provider", "complete")


def _step_check_existing(ws: ImportWorkspace, provider_data: dict) -> None:
    """Check for existing item + quality upgrade (read-only DB)."""
    if ws.is_stage_complete("check_existing"):
        return
    ws.update_stage("check_existing", "running")

    from app.database import SessionLocal
    from app.models import Source, VideoItem

    provider_str = provider_data.get("provider")
    video_id = provider_data.get("video_id")

    db = SessionLocal()
    try:
        existing_source = db.query(Source).filter(
            Source.provider == provider_str,
            Source.source_video_id == video_id,
        ).first()

        result = {"has_existing": False}
        if existing_source and existing_source.video_item:
            item = existing_source.video_item
            # Guard: if the matched item's file is missing, treat as no match
            if item.file_path and not os.path.exists(item.file_path):
                ws.log(f"Ignoring zombie record id={item.id} (file missing: {item.file_path})")
                ws.write_artifact("existing_check", result)
                ws.update_stage("check_existing", "complete")
                return
            result["has_existing"] = True
            result["existing_video_id"] = item.id
            result["existing_folder"] = item.folder_path
            result["existing_artist"] = item.artist
            result["existing_title"] = item.title

            # Quality comparison
            from app.services.downloader import get_available_formats
            try:
                formats, _info = get_available_formats(provider_data["original_url"])
                current_sig = {}
                if item.quality_signature:
                    qs = item.quality_signature
                    current_sig = {
                        "height": qs.height, "video_bitrate": qs.video_bitrate,
                        "fps": qs.fps, "hdr": qs.hdr,
                    }
                from app.pipeline_url.services.media_analyzer import compare_quality
                if not compare_quality(current_sig, formats):
                    ws.log("No higher quality available, skipping")
                    ws.update_stage("check_existing", "complete")
                    raise _DuplicateSkip(
                        existing_video_id=item.id,
                        match_type="exact_source",
                        reason=(
                            f"Same source (already imported as "
                            f"'{item.artist} - {item.title}', id={item.id}), "
                            f"no quality upgrade available"
                        ),
                    )
                ws.log("Higher quality available, proceeding with download")
            except _DuplicateSkip:
                raise
            except Exception as e:
                ws.log(f"Quality check warning: {e}, proceeding anyway", level="warning")

        ws.write_artifact("existing_check", result)
    finally:
        db.close()

    ws.update_stage("check_existing", "complete")


def _step_download(ws: ImportWorkspace, job_id: int, url: str, opts: dict) -> None:
    """Download video with retry + format fallback."""
    if ws.is_stage_complete("download"):
        return
    ws.update_stage("download", "running")

    from app.pipeline_url.services.downloader import download_video
    from app.services.retry_policy import decide_retry, should_auto_retry, MAX_ATTEMPTS
    from app.services.telemetry import telemetry_store
    from app.worker import is_cancelled, JobCancelledError
    from app.database import SessionLocal
    import time

    format_spec = opts.get("format_spec")
    downloaded_file = None
    info_dict = {}
    current_attempt = 0
    last_error = ""

    telemetry_store.create(job_id)

    # Simple progress callback â€” only updates telemetry, no DB writes
    def progress_cb(pct, msg, metrics=None):
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

    temp_dir = tempfile.mkdtemp(prefix="playarr_dl_")

    while current_attempt < MAX_ATTEMPTS:
        current_attempt += 1
        strategy_name = "best" if current_attempt == 1 else ""

        if current_attempt > 1:
            decision = decide_retry(current_attempt - 1, last_error)
            if not decision.should_retry:
                break
            format_spec = decision.format_spec
            strategy_name = decision.strategy_name
            ws.log(f"Retry {current_attempt}/{MAX_ATTEMPTS}: {decision.reason}")
            time.sleep(decision.backoff_seconds)
            if is_cancelled(job_id):
                raise JobCancelledError(f"Job {job_id} cancelled")

        telemetry_store.start_attempt(
            job_id, current_attempt,
            strategy=strategy_name or "best",
            reason=last_error[:200] if current_attempt > 1 else "initial",
            format_spec=format_spec or "auto",
        )

        try:
            db = SessionLocal()
            try:
                from app.tasks import _get_setting_str
                container = _get_setting_str(db, "preferred_container", "mkv")
                _res_pref = _get_setting_str(db, "nv_preferred_resolution", "max")
                max_height = int(_res_pref) if _res_pref.isdigit() else None
            finally:
                db.close()

            downloaded_file, info_dict = download_video(
                url, temp_dir,
                format_spec=format_spec,
                progress_callback=progress_cb,
                cancel_check=lambda: (
                    (_ for _ in ()).throw(JobCancelledError(f"Job {job_id} cancelled"))
                    if is_cancelled(job_id) else None
                ),
                container=container,
                max_height=max_height,
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
            ws.log(f"Download attempt {current_attempt} failed: {e}", level="warning")
            if not should_auto_retry(last_error):
                ws.log("Non-recoverable error, not retrying")
                break

    if not downloaded_file or not os.path.isfile(downloaded_file):
        telemetry_store.remove(job_id)
        raise RuntimeError(f"Download failed after {current_attempt} attempt(s): {last_error}")

    ws.write_artifact("download", {
        "file_path": downloaded_file,
        "info_dict": info_dict,
        "attempts": current_attempt,
    })
    ws.log(f"Downloaded: {downloaded_file}")
    ws.update_stage("download", "complete")


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  HELPERS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

class _DuplicateSkip(Exception):
    """Raised to cleanly exit the pipeline when a duplicate is detected."""
    def __init__(self, existing_video_id: int = None, match_type: str = "",
                 reason: str = ""):
        self.existing_video_id = existing_video_id
        self.match_type = match_type
        self.reason = reason
        super().__init__(reason)


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
    """Guarantee the job reaches a terminal status (via write queue)."""
    from app.pipeline_url.write_queue import db_write
    from app.database import CosmeticSessionLocal
    from app.models import ProcessingJob, JobStatus

    _TERMINAL = {"complete", "failed", "cancelled", "skipped", "finalizing"}

    def _write():
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
        finally:
            db.close()

    try:
        db_write(_write)
    except Exception as e:
        logger.error(f"_ensure_terminal FAILED for job {job_id}: {e}")


def _coarse_update(job_id: int, status_enum=None, step: str = None,
                   progress: int = None, error: str = None,
                   display_name: str = None) -> None:
    """Minimal DB status update via the centralised write queue."""
    from app.pipeline_url.write_queue import db_write, db_write_soon
    from app.database import CosmeticSessionLocal
    from app.models import ProcessingJob, JobStatus
    from datetime import datetime, timezone
    from sqlalchemy.orm.attributes import flag_modified

    is_terminal = (
        status_enum is not None
        and hasattr(status_enum, "value")
        and status_enum.value in ("failed", "complete", "cancelled", "skipped", "finalizing")
    )

    # Capture values for the closure (avoid late-binding surprises)
    _status = status_enum
    _step = step
    _progress = progress
    _error = error
    _display_name = display_name

    def _write():
        db = CosmeticSessionLocal()
        try:
            job = db.query(ProcessingJob).get(job_id)
            if not job:
                return
            if _status is not None:
                job.status = _status
            if _step is not None:
                job.current_step = _step
                steps = list(job.pipeline_steps or [])
                steps.append({"step": _step, "status": "success"})
                job.pipeline_steps = steps
                flag_modified(job, "pipeline_steps")
            if _progress is not None:
                job.progress_percent = _progress
            if _display_name is not None:
                job.display_name = _display_name
            if _error is not None:
                job.error_message = _error
                job.completed_at = datetime.now(timezone.utc)
            if _status and hasattr(_status, "value") and _status.value in ("complete", "skipped"):
                job.completed_at = datetime.now(timezone.utc)
            db.commit()
        finally:
            db.close()

    if is_terminal:
        db_write(_write)       # blocking â€” must land before pipeline continues
    else:
        db_write_soon(_write)  # fire-and-forget cosmetic update


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


def _determine_artist_title_from_ytdlp(ytdlp_meta: dict,
                                       artist_override: Optional[str],
                                       title_override: Optional[str],
                                       downloaded_file: str):
    """Extract artist/title from yt-dlp metadata (URL import)."""
    from app.scraper.metadata_resolver import (
        extract_artist_title, clean_title,
        _detect_artist_title_swap, _clean_ytdlp_artist,
        extract_featuring_credit,
    )

    raw_title = ytdlp_meta.get("title", "")
    parsed_artist, parsed_title = extract_artist_title(raw_title)

    uploader = ytdlp_meta.get("uploader", "") or ""
    channel = ytdlp_meta.get("channel", "") or ""

    # Swap detection: cross-reference against uploader/channel
    parsed_artist, parsed_title = _detect_artist_title_swap(
        parsed_artist, parsed_title, uploader, channel,
    )

    # Validate yt-dlp artist field (reject channel names)
    yt_artist = _clean_ytdlp_artist(
        ytdlp_meta.get("artist", ""), uploader, channel,
    )

    if artist_override:
        artist = artist_override
    elif yt_artist:
        artist = clean_title(yt_artist)
    elif parsed_artist:
        artist = parsed_artist
    else:
        artist = uploader or channel or ""

    if title_override:
        title = title_override
    elif ytdlp_meta.get("track"):
        title = clean_title(ytdlp_meta["track"])
    elif parsed_title:
        title = parsed_title
    else:
        title = clean_title(raw_title) if raw_title else ""

    # Strip duplicated artist prefix from title
    if artist and title:
        for sep in [" - ", " \u2014 ", " \u2013 ", " : "]:
            prefix = artist + sep
            if title.lower().startswith(prefix.lower()):
                title = title[len(prefix):].strip()
                break

    # Extract featuring credits from title and merge into artist
    if title:
        title, feat_credit = extract_featuring_credit(title)
        if feat_credit and artist and feat_credit.lower() not in artist.lower():
            artist = f"{artist}; {feat_credit}"

    return artist or "Unknown Artist", title or os.path.splitext(os.path.basename(downloaded_file))[0]

