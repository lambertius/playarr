"""
AI Metadata Service — Orchestrates AI-powered metadata enrichment.

Implements the complete two-phase workflow:

Phase 1 — Proposal Generation:
  1. Run pre-AI mismatch heuristics (cheap, no API cost)
  2. Optionally run audio fingerprinting
  3. Route to appropriate AI model (auto/manual)
  4. Call AI provider with enriched context
  5. Store proposal with confidence scores + provenance

Phase 2 — Diff Review & Application:
  1. Present comparison UI (scraped vs AI vs fingerprint)
  2. User selects fields to apply
  3. Create MetadataSnapshot before changes (undo support)
  4. Apply selected fields
  5. Optionally rename files

Also provides:
- ``compare_metadata()`` for the diff/comparison UI
- ``undo_enrichment()`` to restore previous metadata
- ``apply_ai_fields()`` for selective field application
"""
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.ai.models import AIMetadataResult, AIResultStatus, AIProvider as AIProviderEnum
from app.ai.model_router import ModelRouter, TaskType, get_model_router
from app.ai.mismatch_detector import detect_mismatches, MismatchReport
from app.ai.provider_factory import get_ai_provider
from app.models import VideoItem, Genre, MetadataSnapshot, MediaAsset, Source
from sqlalchemy.orm.attributes import flag_modified as _flag_modified

logger = logging.getLogger(__name__)


def _set_flag(db, video: VideoItem, step: str, *, method: str = "auto"):
    """Set a processing step flag on a video's processing_state."""
    state = dict(video.processing_state or {})
    state[step] = {
        "completed": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "version": "1.0",
    }
    video.processing_state = state
    _flag_modified(video, "processing_state")

# Minimum confidence to auto-apply a field (0.0–1.0)
AUTO_APPLY_THRESHOLD = 0.85

# All fields that can be enriched
ENRICHABLE_FIELDS = [
    "artist", "title", "album", "year", "genres", "plot",
    "director", "studio", "actors", "tags",
]


def enrich_video_metadata(
    db: Session,
    video_id: int,
    provider_name: Optional[str] = None,
    auto_apply: bool = False,
    force: bool = False,
    requested_fields: Optional[List[str]] = None,
    run_fingerprint: bool = False,
    skip_mismatch_check: bool = False,
    review_description_only: bool = False,
) -> Optional[AIMetadataResult]:
    """
    Run AI metadata enrichment for a video (Phase 1: Proposal Generation).

    Args:
        db: SQLAlchemy session
        video_id: VideoItem ID
        provider_name: Override AI provider (None = use settings)
        auto_apply: If True, auto-apply high-confidence fields
        force: If True, run even if a recent result exists
        requested_fields: Only enrich these fields (None = use global defaults)
        run_fingerprint: Also run audio fingerprinting
        skip_mismatch_check: Skip pre-AI heuristic validation
        review_description_only: Only review/edit the description to Kodi-friendly size

    Returns:
        The AIMetadataResult record, or None if AI is disabled/failed.
    """
    video = db.query(VideoItem).get(video_id)
    if not video:
        logger.error(f"Video {video_id} not found")
        return None

    # Check for existing recent result (skip if not forced)
    if not force:
        existing = (
            db.query(AIMetadataResult)
            .filter(
                AIMetadataResult.video_id == video_id,
                AIMetadataResult.status.in_([AIResultStatus.complete, AIResultStatus.accepted]),
            )
            .order_by(AIMetadataResult.created_at.desc())
            .first()
        )
        if existing:
            logger.info(f"AI result already exists for video {video_id}, skipping (use force=True)")
            return existing

    # Resolve enrichable fields
    active_fields = _resolve_enrichable_fields(db, requested_fields)

    # Build scraped metadata dict
    genres = [g.name for g in video.genres] if video.genres else []
    source_url = None
    channel_name = None
    platform_description = None
    platform_tags = None
    upload_date = None
    if video.sources:
        src = video.sources[0]
        source_url = src.canonical_url
        channel_name = src.channel_name
        platform_description = src.platform_description
        platform_tags = src.platform_tags
        upload_date = src.upload_date

    scraped = {
        "artist": video.artist,
        "title": video.title,
        "album": video.album,
        "year": video.year,
        "plot": video.plot,
        "genres": genres,
    }

    # Get duration from quality signature
    duration_seconds = None
    if video.quality_signature:
        duration_seconds = video.quality_signature.duration_seconds

    # Filename for extra context
    filename = None
    if video.file_path:
        filename = os.path.basename(video.file_path)

    # ── Step 1: Pre-AI mismatch detection ──
    mismatch_report = None
    if not skip_mismatch_check:
        mismatch_report = detect_mismatches(
            scraped=scraped,
            video_title=filename,
            channel_name=channel_name,
            duration_seconds=duration_seconds,
        )
        logger.info(
            f"Mismatch score for video {video_id}: {mismatch_report.overall_score:.2f} "
            f"(suspicious={mismatch_report.is_suspicious})"
        )

    # ── Step 2: Audio fingerprinting (optional) ──
    fingerprint_data = None
    if run_fingerprint and video.file_path and os.path.isfile(video.file_path):
        try:
            from app.ai.fingerprint_service import identify_track
            from app.config import get_settings
            settings = get_settings()
            fp_result = identify_track(
                file_path=video.file_path,
                ffmpeg_path=settings.resolved_ffmpeg,
            )
            fingerprint_data = fp_result.to_dict()
        except Exception as e:
            logger.warning(f"Fingerprint failed for video {video_id}: {e}")
            fingerprint_data = {"error": str(e)}

    # ── Step 3: Get AI provider with model routing ──
    router = get_model_router(provider_name)
    mismatch_score = mismatch_report.overall_score if mismatch_report else 0.0

    # Determine task type based on mismatch score
    task_type = TaskType.ENRICHMENT
    if mismatch_report and mismatch_report.is_suspicious:
        task_type = TaskType.CORRECTION

    model_selection = router.select_model(
        task_type=task_type,
        mismatch_score=mismatch_score,
    )

    # Create provider with selected model
    model_override = None if model_selection.model == "configured" else model_selection.model
    provider = get_ai_provider(provider_name, model=model_override)
    if not provider:
        logger.info("AI provider not configured or disabled")
        return None

    # ── Step 4: Create AI result record ──
    provider_enum = AIProviderEnum(provider.name) if provider.name in AIProviderEnum.__members__ else AIProviderEnum.openai
    ai_result = AIMetadataResult(
        video_id=video_id,
        provider=provider_enum,
        model_name=model_selection.model,
        model_task=model_selection.task_type,
        status=AIResultStatus.processing,
        original_scraped=scraped,
        requested_fields=active_fields,
        mismatch_score=mismatch_score,
        mismatch_signals=mismatch_report.to_dict() if mismatch_report else None,
        fingerprint_result=fingerprint_data,
    )
    db.add(ai_result)
    db.flush()

    try:
        # ── Step 5: Call AI provider ──
        # Build fingerprint hint from top match (if available)
        fingerprint_hint = None
        if fingerprint_data and not fingerprint_data.get("error"):
            matches = fingerprint_data.get("matches", [])
            if matches:
                top = matches[0]
                fingerprint_hint = {
                    "artist": top.get("artist", ""),
                    "title": top.get("title", ""),
                    "confidence": top.get("confidence", 0),
                }

        # Extract mismatch signal list for prompt context
        mismatch_signal_list = None
        if mismatch_report:
            mismatch_signal_list = [
                {"name": s.name, "score": s.score, "details": s.details, "weight": s.weight}
                for s in mismatch_report.signals
            ]

        # Load custom prompt overrides from settings
        from app.routers.ai import load_custom_prompts
        custom_prompts = load_custom_prompts(db)

        response = provider.enrich_metadata(
            scraped=scraped,
            video_filename=filename,
            source_url=source_url,
            duration_seconds=duration_seconds,
            channel_name=channel_name,
            upload_date=upload_date,
            mismatch_signals=mismatch_signal_list,
            fingerprint_hint=fingerprint_hint,
            review_description_only=review_description_only,
            platform_description=platform_description,
            platform_tags=platform_tags,
            custom_system_prompt=custom_prompts.get("system_prompt"),
            custom_enrichment_template=custom_prompts.get("enrichment_prompt"),
            custom_review_template=custom_prompts.get("review_prompt"),
        )

        # Store results (only for requested fields)
        if "artist" in active_fields:
            ai_result.ai_artist = response.artist
        if "title" in active_fields:
            ai_result.ai_title = response.title
        if "album" in active_fields:
            ai_result.ai_album = response.album
        if "year" in active_fields:
            ai_result.ai_year = response.year
        if "plot" in active_fields:
            ai_result.ai_plot = response.plot
        if "genres" in active_fields:
            ai_result.ai_genres = response.genres
        if "director" in active_fields:
            ai_result.ai_director = response.director
        if "studio" in active_fields:
            ai_result.ai_studio = response.studio
        if "tags" in active_fields:
            ai_result.ai_tags = response.tags

        ai_result.confidence_score = response.overall_confidence
        ai_result.field_scores = response.field_scores
        ai_result.raw_response = response.raw_response
        ai_result.prompt_used = response.prompt_used
        ai_result.tokens_used = response.tokens_used
        ai_result.model_name = response.model_name or model_selection.model
        ai_result.status = AIResultStatus.complete
        ai_result.completed_at = datetime.now(timezone.utc)

        # Store AI-generated change summary (from the model itself)
        ai_summary = response.change_summary or ""
        # Also generate our own diff-based summary
        diff_summary = _generate_change_summary(scraped, ai_result, response)
        ai_result.change_summary = ai_summary if ai_summary else diff_summary

        # Determine verification status using identity evidence
        identity = response.identity or {}
        mismatch_info = response.mismatch_info or {}

        if mismatch_info.get("is_mismatch"):
            ai_result.verification_status = False
        elif identity.get("evidence", {}).get("metadata_consistent"):
            ai_result.verification_status = _check_verification(scraped, response)
        else:
            ai_result.verification_status = _check_verification(scraped, response)

        # Merge AI identity/mismatch into mismatch_signals for the comparison UI
        existing_signals = ai_result.mismatch_signals or {}
        existing_signals["ai_identity"] = identity
        existing_signals["ai_mismatch"] = mismatch_info
        ai_result.mismatch_signals = existing_signals

        # Auto-apply high-confidence fields if requested
        if auto_apply:
            threshold = _get_auto_apply_threshold(db)
            # Pass mismatch flag so identity fields use a lower threshold
            _is_mismatch = bool(mismatch_info and mismatch_info.get("is_mismatch"))
            applied = _auto_apply_fields(
                db, video, ai_result, threshold, active_fields,
                is_mismatch=_is_mismatch,
            )
            if applied:
                ai_result.accepted_fields = applied
                ai_result.status = (
                    AIResultStatus.accepted
                    if set(applied) >= set(active_fields)
                    else AIResultStatus.partial
                )

        db.commit()
        logger.info(
            f"AI enrichment complete for video {video_id}: "
            f"confidence={response.overall_confidence:.2f}, "
            f"model={model_selection.model} ({model_selection.reason})"
        )
        return ai_result

    except Exception as e:
        ai_result.status = AIResultStatus.failed
        ai_result.error_message = str(e)
        ai_result.completed_at = datetime.now(timezone.utc)
        db.commit()
        logger.error(f"AI enrichment failed for video {video_id}: {e}")
        return ai_result


def _resolve_enrichable_fields(
    db: Session,
    requested: Optional[List[str]],
) -> List[str]:
    """Resolve which fields to enrich, using global defaults if not specified."""
    if requested:
        return [f for f in requested if f in ENRICHABLE_FIELDS]

    # Load global defaults from settings
    try:
        from app.models import AppSetting
        setting = db.query(AppSetting).filter(
            AppSetting.key == "ai_enrichable_fields",
            AppSetting.user_id.is_(None),
        ).first()
        if setting and setting.value:
            import json
            fields = json.loads(setting.value)
            return [f for f in fields if f in ENRICHABLE_FIELDS]
    except Exception:
        pass

    return ENRICHABLE_FIELDS.copy()


def _get_auto_apply_threshold(db: Session) -> float:
    """Get auto-apply threshold from settings."""
    try:
        from app.models import AppSetting
        setting = db.query(AppSetting).filter(
            AppSetting.key == "ai_auto_apply_threshold",
            AppSetting.user_id.is_(None),
        ).first()
        if setting:
            return float(setting.value)
    except Exception:
        pass
    return AUTO_APPLY_THRESHOLD


def _generate_change_summary(
    scraped: Dict[str, Any],
    ai_result: AIMetadataResult,
    response: Any,
) -> str:
    """Generate a human-readable summary of changes."""
    changes = []
    field_map = {
        "artist": (scraped.get("artist"), ai_result.ai_artist),
        "title": (scraped.get("title"), ai_result.ai_title),
        "album": (scraped.get("album"), ai_result.ai_album),
        "year": (scraped.get("year"), ai_result.ai_year),
    }

    for field, (old, new) in field_map.items():
        if new and str(old or "") != str(new):
            changes.append(f"{field}: '{old}' → '{new}'")

    if not changes:
        return "No metadata changes proposed."
    return f"Proposed {len(changes)} change(s): " + "; ".join(changes)


def _check_verification(scraped: Dict[str, Any], response: Any) -> bool:
    """Check if AI verified the existing metadata as correct."""
    # If high confidence and no changes, metadata is verified
    if response.overall_confidence < 0.5:
        return False

    scores = response.field_scores or {}
    key_fields = ["artist", "title"]
    for field in key_fields:
        old = str(scraped.get(field) or "")
        new = str(getattr(response, field, None) or "")
        if old and new and old.lower() != new.lower():
            return False

    return True


def _auto_apply_fields(
    db: Session,
    video: VideoItem,
    ai_result: AIMetadataResult,
    threshold: float,
    active_fields: List[str],
    is_mismatch: bool = False,
) -> List[str]:
    """
    Auto-apply AI fields that exceed the confidence threshold.

    When is_mismatch is True, identity fields (artist, title) use a lower
    threshold (0.5) to ensure AI corrections overwrite bad scraper data.

    Returns list of field names that were applied.
    """
    applied = []
    locked = video.locked_fields or []
    all_locked = "_all" in locked
    scores = ai_result.field_scores or {}

    # When AI detected a mismatch, use a lower bar for identity fields
    # so corrections are applied even at moderate confidence.
    identity_threshold = min(threshold, 0.5) if is_mismatch else threshold

    # When a field is currently empty, use a lower threshold — any AI data
    # is an improvement over nothing.
    EMPTY_FIELD_THRESHOLD = 0.5

    # Plot is the primary enrichment target — NFO plots are typically raw
    # Wikipedia excerpts.  Use a lower bar so AI-generated descriptions
    # replace them even at moderate confidence.
    PLOT_THRESHOLD = 0.6

    if (
        "artist" in active_fields
        and ai_result.ai_artist
        and not all_locked and "artist" not in locked
        and scores.get("artist", 0) >= identity_threshold
    ):
        video.artist = ai_result.ai_artist
        applied.append("artist")

    if (
        "title" in active_fields
        and ai_result.ai_title
        and not all_locked and "title" not in locked
        and scores.get("title", 0) >= identity_threshold
    ):
        video.title = ai_result.ai_title
        applied.append("title")

    if (
        "album" in active_fields
        and ai_result.ai_album
        and not all_locked and "album" not in locked
        and scores.get("album", 0) >= (EMPTY_FIELD_THRESHOLD if not video.album else threshold)
    ):
        # Guard: reject AI album suggestions that look like compilations
        # or soundtracks — these are almost always false positives.
        _ai_album_lower = ai_result.ai_album.lower()
        _COMPILATION_INDICATORS = (
            "original motion picture",
            "original soundtrack",
            "movie soundtrack",
            "various artists",
            " ost",
        )
        _is_compilation = any(ind in _ai_album_lower for ind in _COMPILATION_INDICATORS)

        # Guard: when Stage B already resolved an album from MusicBrainz,
        # don't let AI overwrite it with a different album (AI tends to
        # assign compilation/parent albums instead of the correct single).
        _mb_album_overwrite = bool(
            video.album
            and video.mb_release_id
            and ai_result.ai_album.lower() != video.album.lower()
        )

        if _is_compilation:
            logger.info(
                f"AI album rejected (compilation): '{ai_result.ai_album}' "
                f"for video {video.id}"
            )
        elif _mb_album_overwrite:
            logger.info(
                f"AI album rejected (MB album already set): '{ai_result.ai_album}' "
                f"vs MB '{video.album}' for video {video.id}"
            )
        else:
            video.album = ai_result.ai_album
            applied.append("album")

    if (
        "year" in active_fields
        and ai_result.ai_year
        and not all_locked and "year" not in locked
        and scores.get("year", 0) >= (EMPTY_FIELD_THRESHOLD if not video.year else threshold)
    ):
        video.year = ai_result.ai_year
        applied.append("year")

    if (
        "plot" in active_fields
        and ai_result.ai_plot
        and not all_locked and "plot" not in locked
        and scores.get("plot", 0) >= (EMPTY_FIELD_THRESHOLD if not video.plot else PLOT_THRESHOLD)
    ):
        video.plot = ai_result.ai_plot
        applied.append("plot")

    if (
        "genres" in active_fields
        and ai_result.ai_genres
        and not all_locked and "genres" not in locked
        and scores.get("genres", 0) >= (EMPTY_FIELD_THRESHOLD if not video.genres else threshold)
    ):
        from app.services.metadata_resolver import capitalize_genre
        video.genres.clear()
        for genre_name in ai_result.ai_genres[:5]:
            normalised = capitalize_genre(genre_name)
            genre = db.query(Genre).filter(Genre.name == normalised).first()
            if not genre:
                genre = Genre(name=normalised)
                db.add(genre)
                db.flush()
            video.genres.append(genre)
        applied.append("genres")

    # Re-run entity resolution when AI changed identity fields (artist/title/album)
    # OR when entity links are missing (e.g. entity_resolve failed during import).
    identity_changed = any(f in applied for f in ("artist", "title", "album"))
    needs_entity_resolve = identity_changed or (
        video.artist_entity_id is None or video.track_id is None
    )
    if needs_entity_resolve:
        try:
            from app.metadata.resolver import (
                resolve_artist, resolve_album, resolve_track,
                get_or_create_artist, get_or_create_album, get_or_create_track,
            )
            from app.metadata.revisions import save_revision

            resolved_artist = resolve_artist(
                video.artist,
                mb_artist_id=video.mb_artist_id,
            )
            artist_entity = get_or_create_artist(db, video.artist, resolved_artist)
            video.artist_entity_id = artist_entity.id
            save_revision(db, "artist", artist_entity.id, "ai_correction", "ai")

            if video.album:
                resolved_album_data = resolve_album(
                    video.artist, video.album,
                )
                album_entity = get_or_create_album(
                    db, artist_entity, video.album, resolved_album_data,
                )
                video.album_entity_id = album_entity.id
                save_revision(db, "album", album_entity.id, "ai_correction", "ai")
            else:
                album_entity = None
                video.album_entity_id = None

            resolved_track_data = resolve_track(
                video.artist, video.title,
                mb_recording_id=video.mb_recording_id,
            )
            track_entity = get_or_create_track(
                db, artist_entity, album_entity, video.title, resolved_track_data,
            )
            video.track_id = track_entity.id

            logger.info(
                f"Re-resolved entities after AI correction for video {video.id}: "
                f"artist={artist_entity.canonical_name}, "
                f"album={album_entity.title if album_entity else 'n/a'}, "
                f"track={track_entity.title}"
            )
        except Exception as e:
            logger.warning(f"Entity re-resolution after AI correction failed: {e}")

    return applied


def apply_ai_fields(
    db: Session,
    video_id: int,
    ai_result_id: int,
    fields: List[str],
    rename_files: bool = False,
) -> VideoItem:
    """
    Manually apply specific AI fields to a video (Phase 2).

    Creates a MetadataSnapshot before applying for undo support.

    Args:
        db: SQLAlchemy session
        video_id: VideoItem ID
        ai_result_id: AIMetadataResult ID
        fields: List of field names to apply
        rename_files: Also rename folder/files based on updated metadata

    Returns:
        Updated VideoItem
    """
    video = db.query(VideoItem).get(video_id)
    ai_result = db.query(AIMetadataResult).get(ai_result_id)

    if not video or not ai_result:
        raise ValueError("Video or AI result not found")

    # ── Create pre-change snapshot for undo ──
    genres = [g.name for g in video.genres] if video.genres else []
    snapshot_data = {
        "artist": video.artist,
        "title": video.title,
        "album": video.album,
        "year": video.year,
        "plot": video.plot,
        "genres": genres,
        "ai_result_id": ai_result_id,
    }
    snapshot = MetadataSnapshot(
        video_id=video_id,
        snapshot_data=snapshot_data,
        reason=f"pre_ai_apply:{ai_result_id}",
    )
    db.add(snapshot)

    locked = video.locked_fields or []
    all_locked = "_all" in locked

    # Derive the source provenance from the AI result's model_name.
    # model_name is set to e.g. "ai_auto_analyse", "musicbrainz_scrape",
    # "wikipedia_scrape", or a model identifier like "gpt-4o".
    _source_label_map = {
        "ai_auto_analyse": "ai",
        "musicbrainz_scrape": "musicbrainz",
        "wikipedia_scrape": "wikipedia",
    }
    _source_label = _source_label_map.get(
        ai_result.model_name, ai_result.model_name or "ai"
    )
    _manual_prov = f"manual+{_source_label}"

    applied_fields = []
    if "artist" in fields and ai_result.ai_artist and not all_locked and "artist" not in locked:
        video.artist = ai_result.ai_artist
        applied_fields.append("artist")
    if "title" in fields and ai_result.ai_title and not all_locked and "title" not in locked:
        video.title = ai_result.ai_title
        applied_fields.append("title")
    if "album" in fields and ai_result.ai_album and not all_locked and "album" not in locked:
        video.album = ai_result.ai_album
        applied_fields.append("album")
    if "year" in fields and ai_result.ai_year and not all_locked and "year" not in locked:
        video.year = ai_result.ai_year
        applied_fields.append("year")
    if "plot" in fields and ai_result.ai_plot and not all_locked and "plot" not in locked:
        video.plot = ai_result.ai_plot
        applied_fields.append("plot")
    if "genres" in fields and ai_result.ai_genres and not all_locked and "genres" not in locked:
        from app.services.metadata_resolver import capitalize_genre
        video.genres.clear()
        for genre_name in ai_result.ai_genres[:5]:
            normalised = capitalize_genre(genre_name)
            genre = db.query(Genre).filter(Genre.name == normalised).first()
            if not genre:
                genre = Genre(name=normalised)
                db.add(genre)
                db.flush()
            video.genres.append(genre)
        applied_fields.append("genres")

    # Update field_provenance for all applied fields
    if applied_fields:
        from sqlalchemy.orm.attributes import flag_modified as _fp_flag
        fp = dict(video.field_provenance or {})
        for f in applied_fields:
            fp[f] = _manual_prov
        video.field_provenance = fp
        _fp_flag(video, "field_provenance")

    # Re-run entity resolution when identity fields changed OR when entity
    # links are missing (e.g. entity_resolve failed during import).
    identity_changed = any(f in applied_fields for f in ("artist", "title", "album"))
    needs_entity_resolve = identity_changed or (
        video.artist_entity_id is None or video.track_id is None
    )
    if needs_entity_resolve:
        try:
            from app.metadata.resolver import (
                resolve_artist, resolve_album, resolve_track,
                get_or_create_artist, get_or_create_album, get_or_create_track,
            )
            from app.metadata.revisions import save_revision

            with db.begin_nested():  # savepoint so failures don't taint session
                resolved_artist = resolve_artist(
                    video.artist,
                    mb_artist_id=video.mb_artist_id,
                )
                artist_entity = get_or_create_artist(db, video.artist, resolved_artist)
                video.artist_entity_id = artist_entity.id
                save_revision(db, "artist", artist_entity.id, "ai_correction", "ai")

                if video.album:
                    resolved_album_data = resolve_album(
                        video.artist, video.album,
                    )
                    album_entity = get_or_create_album(
                        db, artist_entity, video.album, resolved_album_data,
                    )
                    video.album_entity_id = album_entity.id
                    save_revision(db, "album", album_entity.id, "ai_correction", "ai")
                else:
                    album_entity = None
                    video.album_entity_id = None

                resolved_track_data = resolve_track(
                    video.artist, video.title,
                    mb_recording_id=video.mb_recording_id,
                )
                track_entity = get_or_create_track(
                    db, artist_entity, album_entity, video.title, resolved_track_data,
                )
                video.track_id = track_entity.id

                logger.info(
                    f"Re-resolved entities after manual AI apply for video {video_id}: "
                    f"artist={artist_entity.canonical_name}"
                )
        except Exception as e:
            logger.warning(f"Entity re-resolution after manual AI apply failed: {e}")

    # ── Apply artwork (promote pending → valid, remove old) ──
    artwork_types = {"poster", "thumb", "artist_thumb", "album_thumb"}
    requested_artwork = artwork_types & set(fields)
    if requested_artwork:
        import os as _os
        import shutil as _shutil
        for art_type in requested_artwork:
            pending = (
                db.query(MediaAsset)
                .filter(
                    MediaAsset.video_id == video_id,
                    MediaAsset.asset_type == art_type,
                    MediaAsset.status == "pending",
                )
                .first()
            )
            if not pending:
                continue
            # Remove old valid asset of same type
            old_valid = (
                db.query(MediaAsset)
                .filter(
                    MediaAsset.video_id == video_id,
                    MediaAsset.asset_type == art_type,
                    MediaAsset.status == "valid",
                )
                .first()
            )
            if old_valid:
                # Only delete the old file when it's a DIFFERENT file from
                # the pending one.  Entity artwork (artist_thumb/album_thumb)
                # often points to the shared entity poster
                # (e.g. _artists/Name/poster.jpg) — deleting it would destroy
                # artwork for every video by that artist/album.
                _same_file = (
                    old_valid.file_path
                    and pending.file_path
                    and _os.path.normpath(old_valid.file_path) == _os.path.normpath(pending.file_path)
                )
                if not _same_file and old_valid.file_path and _os.path.isfile(old_valid.file_path):
                    try:
                        _os.remove(old_valid.file_path)
                    except OSError:
                        pass
                db.delete(old_valid)
            # Copy pending file to canonical per-video path.
            # Use copy (not move) so shared entity artwork is preserved.
            if pending.file_path and video.folder_path:
                folder_name = _os.path.basename(video.folder_path)
                canonical = _os.path.join(video.folder_path, f"{folder_name}-{art_type.replace('_', '-')}.jpg")
                if pending.file_path != canonical:
                    try:
                        if _os.path.isfile(pending.file_path):
                            _shutil.copy2(pending.file_path, canonical)
                        pending.file_path = canonical
                    except OSError:
                        pass
            pending.status = "valid"
            # Update provenance to reflect manual selection of a
            # source-specific artwork (e.g. "manual+wikipedia_scrape")
            _orig_art_prov = pending.provenance or _source_label
            if not _orig_art_prov.startswith("manual+"):
                pending.provenance = f"manual+{_orig_art_prov}"

    # ── Apply proposed source links ──
    source_field_prefix = "source:"
    source_fields_requested = [f for f in fields if f.startswith(source_field_prefix)]
    if source_fields_requested and hasattr(ai_result, 'proposed_sources') and ai_result.proposed_sources:
        from app.models import Source, SourceProvider as _SP
        from sqlalchemy.exc import IntegrityError as _IntegrityError

        requested_keys = set()
        for sf in source_fields_requested:
            # e.g. "source:musicbrainz:single" → ("musicbrainz", "single")
            parts = sf.split(":", 2)
            if len(parts) == 3:
                requested_keys.add((parts[1], parts[2]))

        for ps in ai_result.proposed_sources:
            _raw_st = ps.get("source_type") or "video"
            _ps_url = ps.get("original_url") or ps.get("url", "")
            _key = (ps["provider"], _raw_st)
            if _key not in requested_keys:
                continue
            # Check for existing source matching the provider + source_type.
            existing = db.query(Source).filter(
                Source.video_id == video_id,
                Source.provider == _SP(ps["provider"]),
                Source.source_type == ps.get("source_type", "video"),
            ).first()
            if existing:
                # If proposal replaces an outdated source, update it in-place
                if ps.get("_replaces_source_id") and existing.id == ps["_replaces_source_id"]:
                    existing.source_video_id = ps.get("source_video_id", "")
                    existing.original_url = _ps_url
                    existing.canonical_url = _ps_url
                continue
            try:
                with db.begin_nested():
                    db.add(Source(
                        video_id=video_id,
                        provider=_SP(ps["provider"]),
                        source_video_id=ps.get("source_video_id", ""),
                        original_url=_ps_url,
                        canonical_url=_ps_url,
                        source_type=ps.get("source_type", "video"),
                        provenance=ps.get("provenance", "scraped"),
                    ))
            except _IntegrityError:
                pass

    # Update AI result status
    text_fields_in_request = set(fields) - artwork_types - {f for f in fields if f.startswith("source:")}
    ai_result.accepted_fields = list(set((ai_result.accepted_fields or []) + list(text_fields_in_request) + source_fields_requested))
    all_fields = {"artist", "title", "album", "year", "plot", "genres"}
    if set(ai_result.accepted_fields or []) >= all_fields:
        ai_result.status = AIResultStatus.accepted
    else:
        ai_result.status = AIResultStatus.partial

    # Optionally rename files
    if rename_files:
        _rename_files_for_metadata(db, video)

    # Re-write NFO to reflect applied changes
    if video.folder_path:
        try:
            from app.services.file_organizer import write_nfo_file
            write_nfo_file(
                video.folder_path,
                artist=video.artist,
                title=video.title,
                album=video.album or "",
                year=video.year,
                genres=[g.name for g in video.genres],
                plot=video.plot or "",
                source_url=video.sources[0].canonical_url if video.sources else "",
                resolution_label=video.resolution_label or "",
            )
            _set_flag(db, video, "nfo_exported", method="ai_apply")
        except Exception as e:
            logger.warning(f"NFO rewrite after apply failed for video {video_id}: {e}")

    # Write Playarr XML sidecar
    if video.folder_path:
        try:
            from app.services.playarr_xml import write_playarr_xml
            write_playarr_xml(video, db)
            _set_flag(db, video, "xml_exported", method="ai_apply")
        except Exception as e:
            logger.warning(f"Playarr XML write after apply failed for video {video_id}: {e}")

    db.commit()
    return video


def undo_enrichment(
    db: Session,
    video_id: int,
    ai_result_id: int,
) -> Optional[VideoItem]:
    """
    Undo an AI enrichment by restoring from the MetadataSnapshot.

    Args:
        db: SQLAlchemy session
        video_id: VideoItem ID
        ai_result_id: AIMetadataResult ID to undo

    Returns:
        Updated VideoItem or None if no snapshot found.
    """
    video = db.query(VideoItem).get(video_id)
    if not video:
        return None

    # Find the snapshot created before this AI application
    snapshot = (
        db.query(MetadataSnapshot)
        .filter(
            MetadataSnapshot.video_id == video_id,
            MetadataSnapshot.reason == f"pre_ai_apply:{ai_result_id}",
        )
        .order_by(MetadataSnapshot.created_at.desc())
        .first()
    )

    if not snapshot:
        logger.warning(f"No snapshot found for undo: video={video_id}, ai_result={ai_result_id}")
        return None

    data = snapshot.snapshot_data

    # Restore metadata
    if "artist" in data:
        video.artist = data["artist"]
    if "title" in data:
        video.title = data["title"]
    if "album" in data:
        video.album = data.get("album")
    if "year" in data:
        video.year = data.get("year")
    if "plot" in data:
        video.plot = data.get("plot")
    if "genres" in data:
        from app.services.metadata_resolver import capitalize_genre
        video.genres.clear()
        for genre_name in data.get("genres", []):
            normalised = capitalize_genre(genre_name)
            genre = db.query(Genre).filter(Genre.name == normalised).first()
            if not genre:
                genre = Genre(name=normalised)
                db.add(genre)
                db.flush()
            video.genres.append(genre)

    # Delete any pending artwork (revert to existing valid artwork)
    # Only delete files that live in the video's own folder — entity
    # artwork (in _artists/ or _albums/) is shared and must not be removed.
    import os as _os
    pending_art = (
        db.query(MediaAsset)
        .filter(
            MediaAsset.video_id == video_id,
            MediaAsset.status == "pending",
        )
        .all()
    )
    _video_folder = video.folder_path
    for pa in pending_art:
        if pa.file_path and _os.path.isfile(pa.file_path):
            _is_per_video = (
                _video_folder
                and _os.path.normpath(pa.file_path).startswith(_os.path.normpath(_video_folder))
            )
            if _is_per_video:
                try:
                    _os.remove(pa.file_path)
                except OSError:
                    pass
        db.delete(pa)

    # Mark AI result as rejected
    ai_result = db.query(AIMetadataResult).get(ai_result_id)
    if ai_result:
        ai_result.status = AIResultStatus.rejected
        ai_result.accepted_fields = None

    db.commit()
    logger.info(f"Undo enrichment for video {video_id}, ai_result {ai_result_id}")
    return video


def compare_metadata(
    db: Session,
    video_id: int,
) -> Dict[str, Any]:
    """
    Build a comparison dict showing scraped vs AI metadata for a video.

    Returns enhanced comparison with mismatch report and fingerprint data.
    """
    video = db.query(VideoItem).get(video_id)
    if not video:
        return {"video_id": video_id, "scraped": {}, "ai": None, "fields": []}

    genres = [g.name for g in video.genres] if video.genres else []
    locked = video.locked_fields or []
    all_locked = "_all" in locked
    scraped = {
        "artist": video.artist,
        "title": video.title,
        "album": video.album,
        "year": video.year,
        "plot": video.plot,
        "genres": genres,
    }

    # Get latest AI result
    ai_result = (
        db.query(AIMetadataResult)
        .filter(
            AIMetadataResult.video_id == video_id,
            AIMetadataResult.status.in_([
                AIResultStatus.complete,
                AIResultStatus.accepted,
                AIResultStatus.partial,
            ]),
        )
        .order_by(AIMetadataResult.created_at.desc())
        .first()
    )

    if not ai_result:
        return {
            "video_id": video_id,
            "scraped": scraped,
            "ai": None,
            "ai_result_id": None,
            "fields": [],
        }

    ai_data = {
        "artist": ai_result.ai_artist,
        "title": ai_result.ai_title,
        "album": ai_result.ai_album,
        "year": ai_result.ai_year,
        "plot": ai_result.ai_plot,
        "genres": ai_result.ai_genres,
        "director": ai_result.ai_director,
        "studio": ai_result.ai_studio,
        "actors": ai_result.ai_actors,
        "tags": ai_result.ai_tags,
    }

    accepted_fields = set(ai_result.accepted_fields or [])
    scores = ai_result.field_scores or {}

    fields_comparison = []
    for field_name in ["artist", "title", "album", "year", "plot", "genres",
                       "director", "studio", "actors", "tags"]:
        scraped_val = scraped.get(field_name)
        ai_val = ai_data.get(field_name)

        # Determine if values differ
        if field_name == "genres":
            changed = set(scraped_val or []) != set(ai_val or [])
        elif field_name in ("actors", "tags"):
            changed = str(scraped_val or []) != str(ai_val or [])
        else:
            changed = str(scraped_val or "") != str(ai_val or "")

        fields_comparison.append({
            "field": field_name,
            "scraped_value": scraped_val,
            "ai_value": ai_val,
            "ai_confidence": scores.get(field_name, 0),
            "changed": changed,
            "accepted": field_name in accepted_fields,
            "locked": all_locked or field_name in locked,
        })

    # Build artwork comparison: pending changes + unchanged valid artwork
    artwork_updates = []
    _artwork_types_with_pending = set()
    pending_assets = (
        db.query(MediaAsset)
        .filter(
            MediaAsset.video_id == video_id,
            MediaAsset.status == "pending",
        )
        .all()
    )
    for pending in pending_assets:
        _artwork_types_with_pending.add(pending.asset_type)
        # Find the current valid asset of the same type (if any)
        current = (
            db.query(MediaAsset)
            .filter(
                MediaAsset.video_id == video_id,
                MediaAsset.asset_type == pending.asset_type,
                MediaAsset.status == "valid",
            )
            .first()
        )
        artwork_updates.append({
            "asset_type": pending.asset_type,
            "proposed_asset_id": pending.id,
            "proposed_source_url": pending.source_url,
            "current_asset_id": current.id if current else None,
            "current_source_url": current.source_url if current else None,
            "provenance": pending.provenance,
            "width": pending.width,
            "height": pending.height,
            "unchanged": False,
        })

    # Also include valid artwork that has no pending changes (unchanged)
    _display_types = ["artist_thumb", "album_thumb", "poster", "thumb"]
    for _atype in _display_types:
        if _atype in _artwork_types_with_pending:
            continue
        valid_asset = (
            db.query(MediaAsset)
            .filter(
                MediaAsset.video_id == video_id,
                MediaAsset.asset_type == _atype,
                MediaAsset.status == "valid",
            )
            .first()
        )
        if valid_asset:
            artwork_updates.append({
                "asset_type": _atype,
                "proposed_asset_id": None,
                "proposed_source_url": None,
                "current_asset_id": valid_asset.id,
                "current_source_url": valid_asset.source_url,
                "provenance": valid_asset.provenance,
                "width": valid_asset.width,
                "height": valid_asset.height,
                "unchanged": True,
            })

    # Collect confirmed source links from DB
    source_updates = []
    all_sources = (
        db.query(Source)
        .filter(Source.video_id == video_id)
        .all()
    )
    # Build a set of existing source keys for dedup
    _existing_source_keys = set()
    for src in all_sources:
        _existing_source_keys.add(f"{src.provider.value}:{src.source_type or 'video'}")
        source_updates.append({
            "provider": src.provider.value,
            "source_type": src.source_type,
            "original_url": src.original_url,
            "provenance": src.provenance,
            "pending": False,
        })

    # Add proposed source links from the AIMetadataResult (pending approval)
    proposed_sources = ai_result.proposed_sources if hasattr(ai_result, 'proposed_sources') else None

    # Fallback: dynamically generate proposals from MusicBrainz IDs when
    # the AIMetadataResult has no stored proposals (e.g. older results).
    # Only do this when the result involved MusicBrainz (not AI-only).
    if not proposed_sources and ai_result.model_name and "musicbrainz" in ai_result.model_name:
        proposed_sources = _generate_source_proposals_from_ids(db, video, video_id, _existing_source_keys)

    if proposed_sources:
        for ps in proposed_sources:
            _ps_url = ps.get("original_url") or ps.get("url", "")
            _key = f"{ps['provider']}:{ps.get('source_type') or 'video'}"
            if _key not in _existing_source_keys or ps.get("_replaces_source_id"):
                source_updates.append({
                    "provider": ps["provider"],
                    "source_type": ps.get("source_type"),
                    "original_url": _ps_url,
                    "provenance": ps.get("provenance"),
                    "pending": True,
                })

    return {
        "video_id": video_id,
        "scraped": scraped,
        "ai": ai_data,
        "ai_result_id": ai_result.id,
        "provider": ai_result.provider.value,
        "model": ai_result.model_name,
        "overall_confidence": ai_result.confidence_score,
        "status": ai_result.status.value,
        "created_at": ai_result.created_at.isoformat() if ai_result.created_at else None,
        "fields": fields_comparison,
        "mismatch_report": ai_result.mismatch_signals,
        "fingerprint_result": ai_result.fingerprint_result,
        "change_summary": ai_result.change_summary,
        "verification_status": ai_result.verification_status,
        "artwork_updates": artwork_updates,
        "source_updates": source_updates,
    }


def _generate_source_proposals_from_ids(
    db: Session,
    video: VideoItem,
    video_id: int,
    existing_keys: set,
) -> list:
    """Generate pending source proposals from MusicBrainz IDs on the video."""
    proposals = []

    if video.mb_recording_id:
        key = "musicbrainz:recording"
        if key not in existing_keys:
            proposals.append({
                "provider": "musicbrainz",
                "source_type": "single",
                "source_video_id": video.mb_recording_id,
                "original_url": f"https://musicbrainz.org/recording/{video.mb_recording_id}",
                "provenance": "scraped",
            })

    if video.mb_artist_id:
        key = "musicbrainz:artist"
        if key not in existing_keys:
            proposals.append({
                "provider": "musicbrainz",
                "source_type": "artist",
                "source_video_id": video.mb_artist_id,
                "original_url": f"https://musicbrainz.org/artist/{video.mb_artist_id}",
                "provenance": "scraped",
            })

    return proposals


def _rename_files_for_metadata(db: Session, video: VideoItem):
    """Rename video folder/file based on updated metadata (if enabled in settings)."""
    try:
        if not video.folder_path or not os.path.isdir(video.folder_path):
            return

        from app.services.file_organizer import build_folder_name
        from app.config import get_settings
        settings = get_settings()

        new_folder_name = build_folder_name(
            video.artist, video.title, video.resolution_label or "1080p",
        )
        old_folder = video.folder_path
        new_folder = os.path.join(os.path.dirname(old_folder), new_folder_name)

        if old_folder == new_folder:
            return

        if os.path.exists(new_folder):
            logger.warning(f"Target folder already exists: {new_folder}")
            return

        import shutil
        shutil.move(old_folder, new_folder)
        video.folder_path = new_folder

        # Update file paths
        if video.file_path:
            old_filename = os.path.basename(video.file_path)
            video.file_path = os.path.join(new_folder, old_filename)

        logger.info(f"Renamed: {old_folder} → {new_folder}")
    except Exception as e:
        logger.error(f"File rename failed: {e}")

