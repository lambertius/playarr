"""Canonical Playarr sidecar restore mapper and two-pass rebuild service."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from uuid import uuid4
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models import (
    ArtistConsolidation,
    ArtistConsolidationMbid,
    ArtistConsolidationTarget,
    FileOperation,
    FieldProvenanceEvent,
    Genre,
    GenreConsolidation,
    GenreConsolidationMember,
    MediaAsset,
    MetadataSnapshot,
    NormalizationHistory,
    ContributionLog,
    VideoEditorQueueEntry,
    Playlist,
    PlaylistEntry,
    QualitySignature,
    Source,
    VideoItem,
    ReviewCase,
    ReviewCaseEdge,
    ReviewCaseItem,
    ReviewActionPlan,
)
from app.services.playarr_xml import parse_playarr_xml
from app.services.sidecar_store import SidecarValidationError, validate_playarr_sidecar


# Every parser output is deliberately classified. This registry is shared by
# the coverage test and the rebuild report so new fields cannot silently vanish.
RESTORED_FIELDS = frozenset({
    "entity_stable_id", "playarr_video_id", "playarr_track_id", "sidecar_revision",
    "entity_revision", "artist", "title", "album", "year", "plot", "genres",
    "mb_artist_id", "mb_recording_id", "mb_release_id", "mb_release_group_id",
    "mb_track_id", "video_phash", "artist_ids", "artist_consolidation", "sources",
    "quality", "artwork", "relative_path", "file_checksum", "resolution_label",
    "file_size_bytes", "import_method", "audio_fingerprint", "acoustid_id",
    "song_rating", "song_rating_set", "video_rating", "video_rating_set",
    "editor_edit_type", "processing_state",
    "exclude_from_editor_scan", "editor_crop_dismissed_evidence_hash", "locked_fields",
    "review_status", "review_reason", "review_category", "review_history",
    "dismissed_duplicate_refs", "rename_dismissed", "related_versions",
    "parent_video_ref", "canonical_provenance", "canonical_confidence",
    "entity_refs", "field_provenance", "provenance_events", "portable_state",
    "scene_analysis", "original_created_at", "original_updated_at",
    "version_type", "alternate_version_label", "original_artist", "original_title",
})
DERIVED_FIELDS = frozenset({"xml_version", "playarr_version", "content_hash", "exported_at"})
EXCLUDED_FIELDS = frozenset({
    # Numeric relationships are v1 compatibility inputs and are never portable.
    "legacy_parent_video_id", "legacy_dismissed_duplicate_ids",
    # Report metadata describes parsing rather than library state.
    "validation_report",
    # These fields are forensic inputs only. Archive identity comes from the
    # archive manifest, and source timestamps are not written onto new rows.
    "archive_original_filename",
})


def field_coverage(data: dict[str, Any]) -> dict[str, list[str]]:
    """Classify all parser fields and expose any unhandled additions."""
    keys = set(data)
    classified = RESTORED_FIELDS | DERIVED_FIELDS | EXCLUDED_FIELDS
    return {
        "restored": sorted(keys & RESTORED_FIELDS),
        "derived": sorted(keys & DERIVED_FIELDS),
        "excluded": sorted(keys & EXCLUDED_FIELDS),
        "unclassified": sorted(keys - classified),
    }


def parse_restore_document(path: str | Path) -> dict[str, Any]:
    """Validate and parse through the single canonical sidecar reader."""
    candidate = Path(path)
    validate_playarr_sidecar(candidate)
    data = parse_playarr_xml(str(candidate))
    if data is None:
        raise SidecarValidationError(f"unable to parse {candidate}")
    coverage = field_coverage(data)
    if coverage["unclassified"]:
        raise SidecarValidationError(
            "unclassified sidecar fields: " + ", ".join(coverage["unclassified"])
        )
    if not data.get("playarr_video_id"):
        raise SidecarValidationError("portable playarr_video_id is required for rebuild")
    data["_source_path"] = str(candidate)
    return data


def _video_path(data: dict[str, Any], library_root: Path) -> Path:
    source = Path(data["_source_path"])
    candidates: list[Path] = []
    stem = source.name.removesuffix(".playarr.xml")
    for item in source.parent.glob(f"{stem}.*"):
        if item.is_file() and item.suffix.lower() in {
            ".mkv", ".mp4", ".webm", ".avi", ".mov", ".mpg", ".mpeg", ".m4v",
        }:
            candidates.append(item.resolve())
    relative = data.get("relative_path")
    if relative:
        candidate = (library_root / relative).resolve()
        try:
            candidate.relative_to(library_root.resolve())
        except ValueError as exc:
            raise SidecarValidationError(f"relative_path escapes library root: {relative}") from exc
        if candidate.is_file() and candidate not in candidates:
            candidates.append(candidate)
    expected_size = data.get("file_size_bytes")
    if expected_size:
        candidates = [item for item in candidates if item.stat().st_size == int(expected_size)]
    expected_checksum = data.get("file_checksum")
    if expected_checksum:
        from app.services.content_id import compute_file_signature
        matched = [item for item in candidates if compute_file_signature(str(item)) == expected_checksum]
        if not matched:
            media_extensions = {".mkv", ".mp4", ".webm", ".avi", ".mov", ".mpg", ".mpeg", ".m4v"}
            for item in library_root.rglob("*"):
                if not item.is_file() or item.suffix.lower() not in media_extensions:
                    continue
                if expected_size and item.stat().st_size != int(expected_size):
                    continue
                resolved = item.resolve()
                if compute_file_signature(str(resolved)) == expected_checksum:
                    matched.append(resolved)
        if not matched:
            raise SidecarValidationError(
                f"media checksum mismatch for {source}: no candidate matches {expected_checksum}"
            )
        candidates = matched
    if not candidates:
        detail = "matching size" if expected_size else ""
        raise SidecarValidationError(f"media file is missing {detail} for {source}".replace("  ", " "))
    unique = {str(item): item for item in candidates}
    if len(unique) != 1:
        raise SidecarValidationError(
            f"ambiguous media files for {source}: {', '.join(sorted(unique))}"
        )
    return next(iter(unique.values()))


def _set_if_present(target: Any, data: dict[str, Any], fields: Iterable[str]) -> list[str]:
    changed: list[str] = []
    for field in fields:
        if field not in data:
            continue
        value = data[field]
        if getattr(target, field, None) != value:
            setattr(target, field, value)
            changed.append(field)
    return changed


def _parsed_time(value: Any) -> datetime | None:
    if not value or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _portable_file_path(folder: Path, value: str) -> str:
    path = Path(value)
    if path.is_absolute():
        return str(path)
    resolved = (folder / path).resolve()
    try:
        resolved.relative_to(folder.resolve())
    except ValueError as exc:
        raise SidecarValidationError(f"portable path escapes sidecar folder: {value}") from exc
    return str(resolved)


def _model_values(model: Any, payload: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    columns = {column.name: column for column in model.__table__.columns}
    for name, value in payload.items():
        column = columns.get(name)
        if column is None or name in {"id", "video_id"}:
            continue
        if value is not None and column.type.__class__.__name__ == "DateTime":
            value = _parsed_time(value)
        enum_class = getattr(column.type, "enum_class", None)
        if value is not None and enum_class is not None:
            value = enum_class(value)
        values[name] = value
    return values


def _restore_portable_state(
    db: Session,
    video: VideoItem,
    data: dict[str, Any],
    sidecar_folder: Path,
    *,
    replace_existing: bool,
) -> None:
    state = data.get("portable_state")
    legacy_scene = data.get("scene_analysis")
    if not state and not legacy_scene:
        return
    state = state or {}
    if state and int(state.get("schema_version") or 0) != 1:
        raise SidecarValidationError("unsupported portable_state schema version")
    attribution = state.get("attribution") or {}
    for field in (
        "field_provenance_users", "field_provenance_at", "field_verifications",
        "last_edited_by", "song_rating_by", "video_rating_by",
    ):
        if field in attribution:
            setattr(video, field, attribution[field])
    for field in ("song_rating_at", "video_rating_at"):
        if field in attribution:
            setattr(video, field, _parsed_time(attribution[field]))

    from app.ai.models import AIMetadataResult, AISceneAnalysis, AIThumbnail
    if "ai_metadata_results" in state:
        if replace_existing:
            db.query(AIMetadataResult).filter(AIMetadataResult.video_id == video.id).delete(synchronize_session=False)
        for payload in state.get("ai_metadata_results") or []:
            db.add(AIMetadataResult(video_id=video.id, **_model_values(AIMetadataResult, payload)))
    if "scene_analyses" in state or legacy_scene:
        if replace_existing:
            db.query(AIThumbnail).filter(AIThumbnail.video_id == video.id).delete(synchronize_session=False)
            db.query(AISceneAnalysis).filter(AISceneAnalysis.video_id == video.id).delete(synchronize_session=False)
        for payload in state.get("scene_analyses") or []:
            thumbnails = payload.get("thumbnails") or []
            scene = AISceneAnalysis(
                video_id=video.id,
                **_model_values(AISceneAnalysis, {key: value for key, value in payload.items() if key != "thumbnails"}),
            )
            db.add(scene)
            db.flush()
            for thumb_payload in thumbnails:
                values = _model_values(AIThumbnail, thumb_payload)
                stored_path = values.get("file_path")
                if stored_path and not Path(stored_path).is_absolute():
                    values["file_path"] = _portable_file_path(sidecar_folder, stored_path)
                db.add(AIThumbnail(
                    video_id=video.id, scene_analysis_id=scene.id, **values,
                ))

    for model, key in (
        (MetadataSnapshot, "metadata_snapshots"),
        (NormalizationHistory, "normalization_history"),
        (ContributionLog, "contribution_history"),
    ):
        if key in state:
            if replace_existing:
                db.query(model).filter(model.video_id == video.id).delete(synchronize_session=False)
            for payload in state.get(key) or []:
                db.add(model(video_id=video.id, **_model_values(model, payload)))

    if "editor_queue_entries" in state:
        if replace_existing:
            db.query(VideoEditorQueueEntry).filter(VideoEditorQueueEntry.video_id == video.id).delete(synchronize_session=False)
        for payload in state.get("editor_queue_entries") or []:
            db.add(VideoEditorQueueEntry(video_id=video.id, **_model_values(VideoEditorQueueEntry, payload)))

    from app.matching.models import MatchCandidate, MatchResult, NormalizationResult, UserPinnedMatch
    from sqlalchemy import inspect
    matching_available = inspect(db.connection()).has_table(MatchResult.__tablename__)
    matching_payload_present = any(state.get(key) for key in (
        "matching_results", "normalization_results", "pinned_matches",
    ))
    if matching_payload_present and not matching_available:
        raise SidecarValidationError("matching tables are unavailable for portable state restore")
    if "matching_results" in state and matching_available:
        if replace_existing:
            db.query(UserPinnedMatch).filter(UserPinnedMatch.video_id == video.id).delete(synchronize_session=False)
            db.query(MatchResult).filter(MatchResult.video_id == video.id).delete(synchronize_session=False)
            db.flush()
        for payload in state.get("matching_results") or []:
            candidates = payload.get("candidates") or []
            match = MatchResult(
                video_id=video.id,
                **_model_values(MatchResult, {key: value for key, value in payload.items() if key != "candidates"}),
            )
            db.add(match); db.flush()
            for candidate in candidates:
                db.add(MatchCandidate(match_result_id=match.id, **_model_values(MatchCandidate, candidate)))
    if "normalization_results" in state and matching_available:
        if replace_existing:
            db.query(NormalizationResult).filter(NormalizationResult.video_id == video.id).delete(synchronize_session=False)
        for payload in state.get("normalization_results") or []:
            db.add(NormalizationResult(video_id=video.id, **_model_values(NormalizationResult, payload)))
    if "pinned_matches" in state and matching_available:
        if replace_existing:
            db.query(UserPinnedMatch).filter(UserPinnedMatch.video_id == video.id).delete(synchronize_session=False)
        for payload in state.get("pinned_matches") or []:
            db.add(UserPinnedMatch(video_id=video.id, candidate_id=None, **_model_values(UserPinnedMatch, payload)))

    # v2 documents written before portable_state carried completed scene data
    # in the human-readable XML section. Preserve it as a restored analysis.
    if legacy_scene and not (state.get("scene_analyses") or []):
        scene = AISceneAnalysis(
            video_id=video.id, status="complete",
            total_scenes=int(legacy_scene.get("total_scenes") or 0),
            duration_seconds=legacy_scene.get("duration_seconds"),
            config={"restored_from": "legacy_scene_analysis"},
        )
        db.add(scene); db.flush()
        for thumb_payload in legacy_scene.get("thumbnails") or []:
            restored_thumb_path = thumb_payload.get("file_path") or ""
            if restored_thumb_path:
                restored_thumb_path = _portable_file_path(sidecar_folder, restored_thumb_path)
            db.add(AIThumbnail(
                video_id=video.id, scene_analysis_id=scene.id,
                timestamp_sec=float(thumb_payload.get("timestamp_sec") or 0),
                file_path=restored_thumb_path,
                score_sharpness=float(thumb_payload.get("score_sharpness") or 0),
                score_contrast=float(thumb_payload.get("score_contrast") or 0),
                score_color_variance=float(thumb_payload.get("score_color_variance") or 0),
                score_composition=float(thumb_payload.get("score_composition") or 0),
                score_overall=float(thumb_payload.get("score_overall") or 0),
                is_selected=bool(thumb_payload.get("is_selected")),
                provenance=thumb_payload.get("provenance") or "sidecar_restore",
            ))


def apply_sidecar_data(
    db: Session,
    video: VideoItem,
    data: dict[str, Any],
    *,
    library_root: str | Path,
    replace_existing: bool = True,
) -> list[str]:
    """Apply pass-1 scalar/entity state without committing the transaction."""
    root = Path(library_root).resolve()
    source = Path(data["_source_path"])
    media_path = _video_path(data, root)
    changed = _set_if_present(video, data, (
        "artist", "title", "album", "year", "plot", "resolution_label",
        "file_size_bytes", "file_checksum", "import_method", "audio_fingerprint",
        "acoustid_id", "mb_artist_id", "mb_recording_id", "mb_release_id",
        "mb_release_group_id", "mb_track_id", "version_type",
        "alternate_version_label", "original_artist", "original_title", "video_phash",
        "artist_ids", "processing_state", "locked_fields", "field_provenance",
        "exclude_from_editor_scan", "editor_crop_dismissed_evidence_hash",
        "editor_edit_type", "review_status", "review_reason", "review_category",
        "review_history", "rename_dismissed", "canonical_provenance",
        "canonical_confidence", "playarr_video_id", "playarr_track_id",
    ))
    if data.get("entity_stable_id") and video.stable_id != data["entity_stable_id"]:
        video.stable_id = data["entity_stable_id"]
        changed.append("stable_id")
    if data.get("entity_revision"):
        video.revision = max(1, int(data["entity_revision"]))
    if hasattr(video, "sidecar_revision") and data.get("sidecar_revision"):
        video.sidecar_revision = max(1, int(data["sidecar_revision"]))
    video.folder_path = str(source.parent)
    video.file_path = str(media_path)
    if data.get("original_created_at"):
        video.created_at = _parsed_time(data["original_created_at"]) or video.created_at
    if data.get("original_updated_at"):
        video.updated_at = _parsed_time(data["original_updated_at"]) or video.updated_at
    _restore_portable_state(db, video, data, source.parent, replace_existing=replace_existing)

    for payload in data.get("provenance_events") or []:
        event_id = payload.get("id")
        if not event_id or db.get(FieldProvenanceEvent, event_id):
            continue
        def _event_time(name: str):
            value = payload.get(name)
            if not value:
                return None
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (TypeError, ValueError):
                return None
        db.add(FieldProvenanceEvent(
            id=event_id, video_id=video.id, video_stable_id=video.stable_id,
            field_name=payload.get("field_name") or "unknown",
            event_type=payload.get("event_type") or "restored",
            actor_kind=payload.get("actor_kind") or "unknown",
            actor_id=payload.get("actor_id"), model_id=payload.get("model_id"),
            provider=payload.get("provider"), source_url=payload.get("source_url"),
            remote_id=payload.get("remote_id"), transformation=payload.get("transformation"),
            prior_value_hash=payload.get("prior_value_hash"),
            resulting_value_hash=payload.get("resulting_value_hash"),
            verification_json=payload.get("verification_json"),
            operation_id=payload.get("operation_id"),
            retrieved_at=_event_time("retrieved_at"),
            submitted_at=_event_time("submitted_at"),
            created_at=_event_time("created_at") or datetime.now(timezone.utc),
        ))

    if data.get("song_rating_set"):
        video.song_rating = int(data.get("song_rating", 3))
        video.song_rating_set = True
    if data.get("video_rating_set"):
        video.video_rating = int(data.get("video_rating", 3))
        video.video_rating_set = True

    from app.pipeline_lib.db_apply import _get_or_create_genre, _upsert_source
    video.genres.clear()
    for name in data.get("genres") or []:
        video.genres.append(_get_or_create_genre(db, name))

    quality_data = data.get("quality") or {}
    quality = db.query(QualitySignature).filter(QualitySignature.video_id == video.id).one_or_none()
    if quality_data and quality is None:
        quality = QualitySignature(video_id=video.id)
        db.add(quality)
    if quality is not None:
        for field, value in quality_data.items():
            if value is not None and hasattr(quality, field):
                setattr(quality, field, value)

    if replace_existing:
        db.query(Source).filter(Source.video_id == video.id).delete(synchronize_session=False)
        db.flush()
    for source_data in data.get("sources") or []:
        _upsert_source(db, video.id, source_data)

    if replace_existing:
        db.query(MediaAsset).filter(MediaAsset.video_id == video.id).delete(synchronize_session=False)
    for asset in data.get("artwork") or []:
        db.add(MediaAsset(
            video_id=video.id,
            asset_type=asset.get("asset_type") or "other",
            file_path=asset.get("file_path") or "",
            source_url=asset.get("source_url"),
            resolved_url=asset.get("resolved_url"),
            provenance=asset.get("provenance") or "sidecar_restore",
            source_provider=asset.get("source_provider"),
            content_type=asset.get("content_type"),
            file_hash=asset.get("file_hash"),
            status=asset.get("status") or "valid",
            validation_error=asset.get("validation_error"),
            file_size_bytes=asset.get("file_size_bytes"),
            crop_position=asset.get("crop_position"),
            width=asset.get("width"), height=asset.get("height"),
            last_validated_at=_parsed_time(asset.get("last_validated_at")) or datetime.now(timezone.utc),
            created_at=_parsed_time(asset.get("created_at")) or datetime.now(timezone.utc),
        ))

    _restore_entities(db, video, data.get("entity_refs") or {})
    return changed


def _restore_entities(db: Session, video: VideoItem, refs: dict[str, Any]) -> None:
    if not refs:
        return
    from app.pipeline_lib.metadata.resolver import (
        get_or_create_album, get_or_create_artist, get_or_create_track,
    )
    artist_ref = refs.get("artist") or {}
    if not artist_ref.get("name"):
        return
    artist = get_or_create_artist(db, artist_ref["name"], resolved=artist_ref)
    video.artist_entity_id = artist.id
    album = None
    album_ref = refs.get("album") or {}
    if album_ref.get("title"):
        album = get_or_create_album(db, artist, album_ref["title"], resolved=album_ref)
        video.album_entity_id = album.id
    track_ref = refs.get("track") or {}
    if track_ref.get("title"):
        track = get_or_create_track(db, artist, album, track_ref["title"], resolved=track_ref)
        video.track_id = track.id


def _restore_consolidation(db: Session, data: dict[str, Any]) -> None:
    payload = data.get("artist_consolidation") or {}
    stable_id = payload.get("stable_id")
    if not stable_id or not payload.get("mask_name"):
        return
    item = db.query(ArtistConsolidation).filter(ArtistConsolidation.stable_id == stable_id).one_or_none()
    if item is None:
        item = ArtistConsolidation(stable_id=stable_id, mask_name=payload["mask_name"])
        db.add(item)
        db.flush()
    item.mask_name = payload["mask_name"]
    item.revision = max(item.revision or 1, int(payload.get("revision") or 1))
    target_name = payload.get("raw_target_name")
    if target_name and not any(value.raw_name == target_name for value in item.targets):
        item.targets.append(ArtistConsolidationTarget(
            raw_name=target_name, provenance="sidecar_restore",
            mb_artist_id=payload.get("mb_artist_id"),
        ))
    mbid = payload.get("mb_artist_id")
    if mbid and not any(value.mb_artist_id == mbid for value in item.mbids):
        item.mbids.append(ArtistConsolidationMbid(mb_artist_id=mbid))


def apply_sidecar_relationships(
    db: Session,
    video: VideoItem,
    data: dict[str, Any],
    videos_by_ref: dict[str, VideoItem],
) -> list[str]:
    """Pass 2: resolve every portable relationship after all videos exist."""
    missing: list[str] = []
    parent_ref = data.get("parent_video_ref")
    if parent_ref:
        parent = videos_by_ref.get(parent_ref)
        if parent:
            video.parent_video_id = parent.id
        else:
            missing.append(parent_ref)

    dismissed: list[int] = []
    for ref in data.get("dismissed_duplicate_refs") or []:
        related = videos_by_ref.get(ref)
        if related:
            dismissed.append(related.id)
        else:
            missing.append(ref)
    video.dismissed_duplicate_ids = dismissed or None

    related_versions: list[dict[str, Any]] = []
    for relation in data.get("related_versions") or []:
        ref = relation.get("video_ref")
        related = videos_by_ref.get(ref)
        if related:
            related_versions.append({"video_id": related.id, "label": relation.get("label")})
        elif ref:
            missing.append(ref)
    video.related_versions = related_versions or None
    _restore_consolidation(db, data)
    return sorted(set(missing))


def rebuild_from_sidecars(
    db: Session,
    sidecars: Iterable[str | Path],
    *,
    library_root: str | Path,
    library_manifest: str | Path | None = None,
) -> dict[str, Any]:
    """Rebuild an empty database using explicit validate/create and resolve passes."""
    report: dict[str, Any] = {
        "restored": [], "migrated": [], "ambiguous": [], "missing": [], "rejected": [],
        "recovered": [], "incomplete": [],
    }
    parsed: list[dict[str, Any]] = []
    seen_refs: dict[str, str] = {}
    for path in sorted((Path(value) for value in sidecars), key=lambda value: str(value).casefold()):
        try:
            data = parse_restore_document(path)
            ref = data["playarr_video_id"]
            if ref in seen_refs:
                report["ambiguous"].append({"playarr_video_id": ref, "paths": [seen_refs[ref], str(path)]})
                continue
            seen_refs[ref] = str(path)
            parsed.append(data)
            if data.get("validation_report", {}).get("migrated"):
                report["migrated"].append(str(path))
        except Exception as exc:
            report["rejected"].append({"path": str(path), "reason": str(exc)})

    videos_by_ref: dict[str, VideoItem] = {}
    for data in parsed:
        ref = data["playarr_video_id"]
        try:
            with db.begin_nested():
                video = db.query(VideoItem).filter(VideoItem.playarr_video_id == ref).one_or_none()
                created = video is None
                if video is None:
                    video = VideoItem(artist=data.get("artist") or "Unknown Artist", title=data.get("title") or "Unknown Title")
                    db.add(video)
                    db.flush()
                apply_sidecar_data(
                    db, video, data, library_root=library_root,
                    replace_existing=not created,
                )
                db.flush()
        except Exception as exc:
            report["rejected"].append({"path": data["_source_path"], "reason": str(exc)})
            continue
        videos_by_ref[ref] = video
        if data.get("entity_stable_id"):
            videos_by_ref[data["entity_stable_id"]] = video
        report["restored"].append({"path": data["_source_path"], "playarr_video_id": ref})

    for data in parsed:
        video = videos_by_ref.get(data["playarr_video_id"])
        if video is None:
            continue
        for ref in apply_sidecar_relationships(db, video, data, videos_by_ref):
            report["missing"].append({
                "path": data["_source_path"], "relationship_ref": ref,
            })
    manifest_path = Path(library_manifest) if library_manifest else Path(library_root) / ".playarr-library-manifest.json"
    manifest_backup = manifest_path.with_name(manifest_path.name + ".bak")
    if manifest_path.is_file() or manifest_backup.is_file():
        try:
            from app.services.consolidations import load_library_manifest
            manifest, loaded_path = load_library_manifest(manifest_path)
            if loaded_path != manifest_path:
                report["recovered"].append({"path": str(loaded_path), "reason": "primary manifest unavailable or invalid"})
            _restore_library_manifest(db, manifest, videos_by_ref, report)
        except Exception as exc:
            report["rejected"].append({"path": str(manifest_path), "reason": str(exc)})
            report["incomplete"].append({"component": "library_manifest", "reason": "global state could not be restored"})
    else:
        report["incomplete"].append({"component": "library_manifest", "reason": "global state manifest is missing"})
    db.flush()
    report["counts"] = {
        key: len(value) for key, value in report.items()
        if isinstance(value, list) and (key not in {"recovered", "incomplete"} or value)
    }
    return report


def _restore_library_manifest(
    db: Session,
    manifest: dict[str, Any],
    videos_by_ref: dict[str, VideoItem],
    report: dict[str, Any],
) -> None:
    """Restore portable global aggregates after per-video pass 2."""
    version = int(manifest.get("schema_version") or 0)
    if version not in (1, 2):
        raise SidecarValidationError(f"unsupported library manifest schema {version}")
    for payload in manifest.get("artist_consolidations") or []:
        stable_id = payload.get("stable_id")
        if not stable_id or not payload.get("mask_name"):
            continue
        item = db.query(ArtistConsolidation).filter(ArtistConsolidation.stable_id == stable_id).one_or_none()
        if item is None:
            item = ArtistConsolidation(stable_id=stable_id, mask_name=payload["mask_name"])
            db.add(item); db.flush()
        item.mask_name = payload["mask_name"]
        item.revision = max(1, int(payload.get("revision") or 1))
        item.targets.clear(); item.mbids.clear()
        for target in payload.get("targets") or []:
            item.targets.append(ArtistConsolidationTarget(
                raw_name=target["raw_name"], provenance=target.get("provenance"),
                provenance_json=target.get("provenance_json"),
                mb_artist_id=target.get("mb_artist_id"),
            ))
        for mbid in payload.get("mbids") or []:
            item.mbids.append(ArtistConsolidationMbid(mb_artist_id=mbid))
    for payload in manifest.get("genre_consolidations") or []:
        mask = payload.get("mask_name")
        if not mask:
            continue
        stable_id = payload.get("stable_id") or str(uuid4())
        item = db.query(GenreConsolidation).filter(
            GenreConsolidation.stable_id == stable_id,
        ).one_or_none()
        if item is None:
            item = GenreConsolidation(stable_id=stable_id, mask_name=mask)
            db.add(item); db.flush()
        item.mask_name = mask
        item.revision = max(1, int(payload.get("revision") or 1))
        item.members.clear()
        for member in payload.get("target_genres") or []:
            if isinstance(member, str):
                name, provenance_json = member, None
            else:
                name = member.get("raw_name")
                provenance_json = member.get("provenance_json")
            if name:
                item.members.append(GenreConsolidationMember(
                    raw_name=name, provenance_json=provenance_json,
                ))
    for payload in manifest.get("playlists") or []:
        stable_id = payload.get("stable_id")
        if not stable_id:
            continue
        playlist = db.query(Playlist).filter(Playlist.stable_id == stable_id).one_or_none()
        if playlist is None:
            playlist = Playlist(stable_id=stable_id, name=payload.get("name") or "Restored playlist")
            db.add(playlist); db.flush()
        playlist.name = payload.get("name") or playlist.name
        playlist.description = payload.get("description")
        playlist.revision = max(1, int(payload.get("revision") or 1))
        playlist.entries.clear()
        for index, entry in enumerate(payload.get("entries") or []):
            video = videos_by_ref.get(entry.get("video_ref"))
            if video is None:
                report["missing"].append({"playlist": stable_id, "relationship_ref": entry.get("video_ref")})
                continue
            playlist.entries.append(PlaylistEntry(
                occurrence_id=entry.get("occurrence_id") or str(uuid4()), video_id=video.id,
                position=int(entry.get("position", index)),
            ))
    for payload in manifest.get("review_cases") or []:
        stable_id = payload.get("stable_id")
        if not stable_id:
            continue
        case = db.query(ReviewCase).filter(ReviewCase.stable_id == stable_id).one_or_none()
        if case is None:
            case = ReviewCase(
                stable_id=stable_id, category=payload.get("category") or "restored",
                trigger_code=payload.get("trigger_code") or "sidecar_restore",
                evidence_hash=payload.get("evidence_hash") or stable_id.replace("-", "")[:64],
                evidence_json=payload.get("evidence") or {},
            )
            db.add(case); db.flush()
        case.status = payload.get("status") or "open"
        case.revision = max(1, int(payload.get("revision") or 1))
        case.dismissed_evidence_hash = payload.get("dismissed_evidence_hash")
        case.items.clear(); case.edges.clear(); case.plans.clear()
        for item in payload.get("items") or []:
            video = videos_by_ref.get(item.get("video_ref"))
            case.items.append(ReviewCaseItem(
                video_id=video.id if video else None,
                video_stable_id=item.get("video_ref"), role=item.get("role") or "candidate",
                evidence_summary_json=item.get("evidence_summary") or {},
            ))
        for edge in payload.get("edges") or []:
            case.edges.append(ReviewCaseEdge(
                left_video_stable_id=edge["left_video_ref"],
                right_video_stable_id=edge["right_video_ref"],
                evidence_type=edge.get("evidence_type") or "restored",
                score=float(edge.get("score") or 0), evidence_hash=edge.get("evidence_hash") or "restored",
                evidence_json=edge.get("evidence") or {}, status=edge.get("status") or "open",
            ))
        for plan in payload.get("plans") or []:
            case.plans.append(ReviewActionPlan(
                id=plan.get("id") or str(uuid4()),
                expected_revision=int(plan.get("expected_revision") or case.revision),
                actions_json=plan.get("actions") or [],
                consequence_json=plan.get("consequences"),
                status=plan.get("status") or "draft",
                created_at=_parsed_time(plan.get("created_at")) or datetime.now(timezone.utc),
                committed_at=_parsed_time(plan.get("committed_at")),
            ))
    for payload in manifest.get("archive_operations") or []:
        operation_id = payload.get("operation_id")
        if not operation_id or db.get(FileOperation, operation_id):
            continue
        db.add(FileOperation(
            id=operation_id,
            entity_stable_id=payload.get("entity_id") or payload.get("playarr_video_id") or "unknown",
            operation_type=payload.get("operation_type") or "archive",
            status="historical", plan_json={
                "archive_relative_path": payload.get("archive_relative_path"),
                "checksum": payload.get("checksum"), "reason": payload.get("reason"),
                "restored_from_manifest": True,
            },
        ))
