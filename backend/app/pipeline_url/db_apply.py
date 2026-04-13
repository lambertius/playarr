# AUTO-SEPARATED from pipeline/db_apply.py for pipeline_url pipeline
# This file is independent â€” changes here do NOT affect the other pipeline.
"""
Serial DB apply executor.

Consumes a mutation plan (dict) and applies ALL database writes in a
single short transaction.  Writes are funnelled through the centralised
``write_queue`` â€” only one DB write executes at a time across the entire
pipeline, making SQLite contention impossible by construction.

No network I/O, no time.sleep(), no ffmpeg work may occur here.
"""
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

from app.database import SessionLocal

logger = logging.getLogger(__name__)


def apply_mutation_plan(plan: dict) -> int:
    """Apply a mutation plan to the database via the write queue.

    Blocks until the single writer thread executes the plan.

    Returns:
        video_item.id of the created/updated VideoItem.

    Raises:
        Exception on permanent DB errors (caller should handle).
    """
    from app.pipeline_url.write_queue import db_write
    return db_write(lambda: _execute_plan(plan))


def _execute_plan(plan: dict) -> int:
    """Execute the plan inside a single DB session.  Runs in the writer thread."""
    from app.models import (
        VideoItem, QualitySignature, Source, MediaAsset, MetadataSnapshot,
        Genre, ProcessingJob, SourceProvider, JobStatus,
    )
    from app.pipeline_url.metadata.resolver import (
        get_or_create_artist, get_or_create_album, get_or_create_track,
    )
    from app.metadata.revisions import save_revision
    from app.pipeline_url.services.canonical_track import (
        get_or_create_canonical_track, link_video_to_canonical_track,
    )
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm.attributes import flag_modified

    job_id = plan["job_id"]
    v = plan.get("video", {})

    db = SessionLocal()
    try:
        # â”€â”€ 1. Authoritative duplicate check (TOCTOU defense) â”€â”€â”€â”€â”€â”€â”€â”€
        if v.get("action") == "create":
            existing = db.query(VideoItem).filter(
                VideoItem.artist.ilike(v.get("artist", "")),
                VideoItem.title.ilike(v.get("title", "")),
            ).first()
            if not existing:
                # Fallback: primary artist prefix match + title
                from app.scraper.source_validation import parse_multi_artist
                query_primary, _ = parse_multi_artist(v.get("artist", ""))
                qp_lower = query_primary.lower()
                title_matches = db.query(VideoItem).filter(
                    VideoItem.title.ilike(v.get("title", "")),
                ).all()
                for candidate in title_matches:
                    db_primary, _ = parse_multi_artist(candidate.artist or "")
                    dp_lower = db_primary.lower()
                    if dp_lower == qp_lower or qp_lower.startswith(dp_lower) or dp_lower.startswith(qp_lower):
                        existing = candidate
                        break
            if existing:
                existing_version = getattr(existing, "version_type", "normal") or "normal"
                incoming_version = plan.get("version_type", "normal") or "normal"

                # Different version types â†’ allow the import to proceed
                if existing_version != incoming_version:
                    logger.info(
                        f"[Job {job_id}] TOCTOU re-check found name match "
                        f"(id={existing.id}) but version differs "
                        f"(existing={existing_version}, incoming={incoming_version}), "
                        f"allowing import"
                    )
                else:
                    logger.info(f"[Job {job_id}] Duplicate found on apply re-check "
                                f"(id={existing.id}), skipping insert")
                    _mark_job_skipped(db, job_id, video_id=existing.id,
                                     reason=(
                                         f"Duplicate of '{existing.artist} - {existing.title}' "
                                         f"(id={existing.id})"
                                     ))
                    # Flag existing item for review
                    if existing.review_status in (None, "none"):
                        existing.review_status = "needs_human_review"
                        existing.review_reason = (
                            f"Duplicate import skipped (job {job_id})"
                        )
                    db.commit()
                    return existing.id

        # â”€â”€ 2. Create or update VideoItem â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if v.get("action") == "update" and v.get("existing_id"):
            video_item = db.query(VideoItem).get(v["existing_id"])
            if not video_item:
                raise ValueError(f"VideoItem {v['existing_id']} not found for update")
            _apply_video_fields(video_item, v, plan)
        else:
            video_item = VideoItem(
                artist=v.get("artist", "Unknown Artist"),
                title=v.get("title", "Unknown Title"),
                album=v.get("album", ""),
                year=v.get("year"),
                plot=v.get("plot", ""),
                folder_path=v.get("folder_path"),
                file_path=v.get("file_path"),
                resolution_label=v.get("resolution_label"),
                file_size_bytes=v.get("file_size_bytes"),
                song_rating=v.get("song_rating", 3),
                video_rating=v.get("video_rating", 3),
                review_status=plan.get("review_status", "none"),
                review_reason=plan.get("review_reason"),
                version_type=plan.get("version_type", "normal"),
                alternate_version_label=plan.get("alternate_version_label") or None,
                original_artist=plan.get("original_artist") or None,
                original_title=plan.get("original_title") or None,
                mb_artist_id=v.get("mb_artist_id"),
                mb_recording_id=v.get("mb_recording_id"),
                mb_release_id=v.get("mb_release_id"),
                mb_release_group_id=v.get("mb_release_group_id"),
                processing_state=v.get("processing_state") or {},
                import_method="url",
            )
            db.add(video_item)

        db.flush()  # get video_item.id

        # â”€â”€ 3. QualitySignature â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        qs_data = plan.get("quality_signature")
        if qs_data:
            if v.get("action") == "update":
                qs = db.query(QualitySignature).filter(
                    QualitySignature.video_id == video_item.id
                ).first()
                if qs:
                    for k, val in qs_data.items():
                        if hasattr(qs, k):
                            setattr(qs, k, val)
                else:
                    qs = QualitySignature(video_id=video_item.id)
                    for k, val in qs_data.items():
                        if hasattr(qs, k):
                            setattr(qs, k, val)
                    db.add(qs)
            else:
                qs = QualitySignature(video_id=video_item.id)
                for k, val in qs_data.items():
                    if hasattr(qs, k):
                        setattr(qs, k, val)
                db.add(qs)

        # â”€â”€ 4. Genres â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        genre_names = plan.get("genres", [])
        if genre_names:
            video_item.genres.clear()
            for gname in genre_names:
                g = _get_or_create_genre(db, gname)
                if g not in video_item.genres:
                    video_item.genres.append(g)

        # â”€â”€ 5. Sources â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        for src_data in plan.get("sources", []):
            _upsert_source(db, video_item.id, src_data)

        db.flush()

        # â”€â”€ 6. Entity resolution (DB-only, uses pre-resolved data) â”€â”€â”€
        artist_entity = None
        album_entity = None
        track_entity = None
        canonical_track = None

        ent = plan.get("entities", {})
        if ent.get("artist"):
            try:
                nested = db.begin_nested()
                artist_entity = get_or_create_artist(
                    db, ent["artist"]["name"],
                    resolved=ent["artist"].get("resolved"),
                )
                save_revision(db, "artist", artist_entity.id, "auto_import", "resolver")
                nested.commit()
            except Exception as e:
                logger.warning(f"[Job {job_id}] Artist entity creation: {e}")

        if ent.get("album") and artist_entity:
            try:
                nested = db.begin_nested()
                album_entity = get_or_create_album(
                    db, artist_entity, ent["album"]["title"],
                    resolved=ent["album"].get("resolved"),
                )
                save_revision(db, "album", album_entity.id, "auto_import", "resolver")
                nested.commit()
            except Exception as e:
                logger.warning(f"[Job {job_id}] Album entity creation: {e}")

        if ent.get("track") and artist_entity:
            try:
                nested = db.begin_nested()
                track_entity = get_or_create_track(
                    db, artist_entity, album_entity, ent["track"]["title"],
                    resolved=ent["track"].get("resolved"),
                )
                nested.commit()
            except Exception as e:
                logger.warning(f"[Job {job_id}] Track entity creation: {e}")

        if ent.get("canonical_track") and artist_entity:
            try:
                nested = db.begin_nested()
                ct_params = dict(ent["canonical_track"])
                ct_params["artist_entity"] = artist_entity
                ct_params["album_entity"] = album_entity
                canonical_track, _ct_created = get_or_create_canonical_track(db, **ct_params)
                nested.commit()
            except Exception as e:
                logger.warning(f"[Job {job_id}] Canonical track: {e}")

        # Inherit album from canonical track if missing
        if not album_entity and canonical_track and canonical_track.album_id:
            from app.metadata.models import AlbumEntity
            _ct_album = db.query(AlbumEntity).get(canonical_track.album_id)
            if _ct_album:
                album_entity = _ct_album
                if not video_item.album:
                    video_item.album = _ct_album.title

        # Link VideoItem to entities
        if artist_entity:
            video_item.artist_entity_id = artist_entity.id
        if album_entity:
            video_item.album_entity_id = album_entity.id
        if track_entity:
            video_item.track_id = track_entity.id
        if canonical_track:
            link_video_to_canonical_track(db, video_item, canonical_track)

        # Set entity-resolution flags based on actual DB success
        if track_entity or canonical_track:
            _entity_flags = plan.setdefault("processing_flags", {})
            _entity_flags["track_identified"] = "fingerprint"
            _entity_flags["canonical_linked"] = "canonical"

        # â”€â”€ 7. Media assets â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        for asset_data in plan.get("media_assets", []):
            _upsert_media_asset(db, video_item.id, asset_data)

        # â”€â”€ 8. Metadata snapshot â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        reason = plan.get("snapshot_reason")
        if reason:
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
                "mb_release_group_id": video_item.mb_release_group_id,
            }
            db.add(MetadataSnapshot(
                video_id=video_item.id,
                snapshot_data=snapshot_data,
                reason=reason,
            ))

        # â”€â”€ 9. Processing state flags â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        for step_name, method in plan.get("processing_flags", {}).items():
            state = dict(video_item.processing_state or {})
            state[step_name] = {
                "completed": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "method": method,
                "version": "1.0",
            }
            video_item.processing_state = state
            flag_modified(video_item, "processing_state")

        # â”€â”€ 10. Job linkage + terminal status â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        #  Mark the job complete INSIDE the apply transaction so it's
        #  atomic â€” no separate _coarse_update needed for the terminal
        #  status, eliminating the most contention-prone write.
        _has_deferred = bool(plan.get("deferred_tasks"))
        job = db.query(ProcessingJob).get(job_id)
        if job:
            job.video_id = video_item.id
            if _has_deferred:
                job.status = JobStatus.finalizing
                job.current_step = "Finalizing"
                job.progress_percent = 90
            else:
                job.status = JobStatus.complete
                job.current_step = "Import complete"
                job.progress_percent = 100
                job.completed_at = datetime.now(timezone.utc)
            steps = list(job.pipeline_steps or [])
            steps.append({"step": "Import complete", "status": "success"})
            job.pipeline_steps = steps
            flag_modified(job, "pipeline_steps")

        # â”€â”€ COMMIT â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        db.commit()
        logger.info(f"[Job {job_id}] Apply complete â€” video_id={video_item.id}")

        return video_item.id

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# â”€â”€ Helpers (DB-only, no network) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _apply_video_fields(video_item, v: dict, plan: dict) -> None:
    """Update VideoItem fields from plan, respecting locked_fields."""
    locked = video_item.locked_fields or []
    all_locked = "_all" in locked

    for field in ("artist", "title", "album", "year", "plot"):
        if not all_locked and field not in locked and v.get(field) is not None:
            setattr(video_item, field, v[field])

    # Always update these
    for field in ("folder_path", "file_path", "file_size_bytes", "resolution_label",
                  "mb_artist_id", "mb_recording_id", "mb_release_id", "mb_release_group_id"):
        if v.get(field) is not None:
            setattr(video_item, field, v[field])

    if not all_locked and "version_type" not in locked:
        video_item.version_type = plan.get("version_type", "normal")
    video_item.alternate_version_label = plan.get("alternate_version_label") or None
    video_item.original_artist = plan.get("original_artist") or None
    video_item.original_title = plan.get("original_title") or None

    # Don't re-flag items the user already reviewed/approved
    _new_review = plan.get("review_status", "none")
    if not (video_item.review_status == "reviewed" and _new_review == "needs_human_review"):
        video_item.review_status = _new_review
        video_item.review_reason = plan.get("review_reason")


def _get_or_create_genre(db, genre_name: str):
    """Get existing genre or create new one."""
    from app.models import Genre
    from app.scraper.metadata_resolver import capitalize_genre
    normalised = capitalize_genre(genre_name)
    genre = db.query(Genre).filter(Genre.name == normalised).first()
    if not genre:
        genre = Genre(name=normalised)
        db.add(genre)
        db.flush()
    return genre


def _upsert_source(db, video_id: int, src: dict) -> None:
    """Create a Source record, skipping duplicates."""
    from app.models import Source, SourceProvider
    from sqlalchemy.exc import IntegrityError

    provider_str = src.get("provider", "other")
    try:
        provider_enum = SourceProvider(provider_str)
    except ValueError:
        provider_enum = SourceProvider.other

    source_video_id = src.get("source_video_id", "")
    if not source_video_id:
        return

    # Check if this exact source already exists
    existing = db.query(Source).filter(
        Source.video_id == video_id,
        Source.provider == provider_enum,
        Source.source_video_id == source_video_id,
    ).first()
    if existing:
        # Update mutable fields
        for field in ("platform_title", "platform_description", "platform_tags",
                      "channel_name", "upload_date"):
            val = src.get(field)
            if val is not None:
                setattr(existing, field, val)
        return

    try:
        with db.begin_nested():
            db.add(Source(
                video_id=video_id,
                provider=provider_enum,
                source_video_id=source_video_id,
                original_url=src.get("original_url", ""),
                canonical_url=src.get("canonical_url", ""),
                source_type=src.get("source_type", "video"),
                provenance=src.get("provenance", "import"),
                channel_name=src.get("channel_name"),
                platform_title=src.get("platform_title"),
                platform_description=src.get("platform_description"),
                platform_tags=src.get("platform_tags"),
                upload_date=src.get("upload_date"),
            ))
    except IntegrityError:
        pass  # concurrent duplicate â€” safe to skip


def _upsert_media_asset(db, video_id: int, asset: dict) -> None:
    """Create or update a MediaAsset record."""
    from app.models import MediaAsset

    asset_type = asset.get("asset_type")
    file_path = asset.get("file_path")
    if not asset_type or not file_path:
        return

    # Delete existing asset of same type
    db.query(MediaAsset).filter(
        MediaAsset.video_id == video_id,
        MediaAsset.asset_type == asset_type,
    ).delete(synchronize_session="fetch")

    db.add(MediaAsset(
        video_id=video_id,
        asset_type=asset_type,
        file_path=file_path,
        source_url=asset.get("source_url"),
        provenance=asset.get("provenance", "import"),
        status=asset.get("status", "valid"),
        width=asset.get("width"),
        height=asset.get("height"),
        file_size_bytes=asset.get("file_size_bytes"),
        file_hash=asset.get("file_hash"),
        last_validated_at=datetime.now(timezone.utc),
        validation_error=asset.get("validation_error"),
    ))


def _mark_job_complete(db, job_id: int, video_id: int, step: str) -> None:
    """Mark a ProcessingJob as complete (used for duplicate-skip path)."""
    from app.models import ProcessingJob, JobStatus
    job = db.query(ProcessingJob).get(job_id)
    if job:
        job.status = JobStatus.complete
        job.video_id = video_id
        job.current_step = step
        job.progress_percent = 100
        job.completed_at = datetime.now(timezone.utc)


def _mark_job_skipped(db, job_id: int, video_id: int, reason: str) -> None:
    """Mark a ProcessingJob as skipped due to duplicate detection."""
    from app.models import ProcessingJob, JobStatus
    job = db.query(ProcessingJob).get(job_id)
    if job:
        job.status = JobStatus.skipped
        job.video_id = video_id
        job.current_step = f"Skipped: {reason[:200]}"
        job.progress_percent = 100
        job.completed_at = datetime.now(timezone.utc)
