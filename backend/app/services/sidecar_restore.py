"""Canonical Playarr sidecar restore mapper and two-pass rebuild service."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models import (
    ArtistConsolidation,
    ArtistConsolidationMbid,
    ArtistConsolidationTarget,
    MediaAsset,
    QualitySignature,
    Source,
    VideoItem,
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
    "entity_refs", "field_provenance",
    "version_type", "alternate_version_label", "original_artist", "original_title",
})
DERIVED_FIELDS = frozenset({"xml_version", "playarr_version", "content_hash", "exported_at"})
EXCLUDED_FIELDS = frozenset({
    # Scene analysis is a regenerable cache whose files may not survive a move.
    "scene_analysis",
    # Numeric relationships are v1 compatibility inputs and are never portable.
    "legacy_parent_video_id", "legacy_dismissed_duplicate_ids",
    # Report metadata describes parsing rather than library state.
    "validation_report",
    # These fields are forensic inputs only. Archive identity comes from the
    # archive manifest, and source timestamps are not written onto new rows.
    "archive_original_filename", "original_created_at", "original_updated_at",
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
    relative = data.get("relative_path")
    if relative:
        candidate = (library_root / relative).resolve()
        try:
            candidate.relative_to(library_root.resolve())
        except ValueError as exc:
            raise SidecarValidationError(f"relative_path escapes library root: {relative}") from exc
        return candidate
    stem = source.name.removesuffix(".playarr.xml")
    matches = [item for item in source.parent.glob(f"{stem}.*") if not item.name.endswith((".xml", ".nfo", ".bak"))]
    return matches[0] if matches else source.parent / stem


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


def apply_sidecar_data(
    db: Session,
    video: VideoItem,
    data: dict[str, Any],
    *,
    library_root: str | Path,
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

    db.query(Source).filter(Source.video_id == video.id).delete(synchronize_session=False)
    db.flush()
    for source_data in data.get("sources") or []:
        _upsert_source(db, video.id, source_data)

    db.query(MediaAsset).filter(MediaAsset.video_id == video.id).delete(synchronize_session=False)
    for asset in data.get("artwork") or []:
        db.add(MediaAsset(
            video_id=video.id,
            asset_type=asset.get("asset_type") or "other",
            file_path=asset.get("file_path") or "",
            source_url=asset.get("source_url"),
            provenance=asset.get("provenance") or "sidecar_restore",
            source_provider=asset.get("source_provider"),
            file_hash=asset.get("file_hash"),
            status=asset.get("status") or "valid",
            width=asset.get("width"), height=asset.get("height"),
            last_validated_at=datetime.now(timezone.utc),
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
) -> dict[str, Any]:
    """Rebuild an empty database using explicit validate/create and resolve passes."""
    report: dict[str, Any] = {
        "restored": [], "migrated": [], "ambiguous": [], "missing": [], "rejected": [],
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
        video = db.query(VideoItem).filter(VideoItem.playarr_video_id == ref).one_or_none()
        if video is None:
            video = VideoItem(artist=data.get("artist") or "Unknown Artist", title=data.get("title") or "Unknown Title")
            db.add(video)
            db.flush()
        apply_sidecar_data(db, video, data, library_root=library_root)
        db.flush()
        videos_by_ref[ref] = video
        if data.get("entity_stable_id"):
            videos_by_ref[data["entity_stable_id"]] = video
        report["restored"].append({"path": data["_source_path"], "playarr_video_id": ref})

    for data in parsed:
        video = videos_by_ref[data["playarr_video_id"]]
        for ref in apply_sidecar_relationships(db, video, data, videos_by_ref):
            report["missing"].append({
                "path": data["_source_path"], "relationship_ref": ref,
            })
    db.flush()
    report["counts"] = {key: len(value) for key, value in report.items() if isinstance(value, list)}
    return report
