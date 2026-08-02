"""Upgrade preflight and post-migration logical reconciliation."""
from __future__ import annotations

import os
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    ArtistConsolidation,
    FileOperation,
    MutationCommand,
    Playlist,
    ReviewCase,
    SidecarOutbox,
    VideoItem,
)


def _sqlite_path(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite"):
        return None
    raw = database_url.split("///", 1)[-1]
    return Path(raw).expanduser().resolve()


def create_database_backup() -> str | None:
    source = _sqlite_path(get_settings().database_url)
    if source is None or not source.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = source.with_name(f"{source.stem}.preflight-{stamp}{source.suffix}.bak")
    source_connection = sqlite3.connect(str(source))
    destination_connection = sqlite3.connect(str(destination))
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()
    return str(destination)


def migration_preflight(db: Session, *, create_backup: bool = True) -> dict:
    backup_path = None
    backup_error = None
    if create_backup:
        try:
            backup_path = create_database_backup()
        except Exception as exc:
            backup_error = str(exc)
    if inspect(db.get_bind()).has_table("alembic_version"):
        schema_version = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    else:
        schema_version = "unversioned"
    videos = db.query(VideoItem).all()
    stable_ids = [video.stable_id for video in videos if video.stable_id]
    duplicate_ids = sorted(value for value, count in Counter(stable_ids).items() if count > 1)
    missing_ids = [video.id for video in videos if not video.stable_id]
    missing_files = [video.id for video in videos if video.file_path and not os.path.isfile(video.file_path)]
    orphan_files = missing_files  # DB-linked paths with no owner file; filesystem-wide deletion is never inferred.
    unwritable_sidecars = []
    missing_sidecars = []
    for video in videos:
        if not video.file_path:
            continue
        folder = Path(video.folder_path or Path(video.file_path).parent)
        if not os.access(folder, os.W_OK):
            unwritable_sidecars.append(video.id)
        expected = folder / f"{Path(video.file_path).stem}.playarr.xml"
        if not expected.is_file():
            missing_sidecars.append(video.id)
    pending = {
        "mutations": db.query(MutationCommand).filter(MutationCommand.status.in_(("pending", "running", "retry"))).count(),
        "sidecars": db.query(SidecarOutbox).filter(SidecarOutbox.status.in_(("pending", "running", "retry"))).count(),
        "files": db.query(FileOperation).filter(FileOperation.status.in_(("planned", "running", "rollback"))).count(),
    }
    critical = []
    if create_backup and _sqlite_path(get_settings().database_url) is not None and not backup_path:
        critical.append("database_backup_failed")
    if missing_ids:
        critical.append("missing_stable_ids")
    if duplicate_ids:
        critical.append("duplicate_stable_ids")
    if unwritable_sidecars:
        critical.append("sidecar_not_writable")
    return {
        "ready": not critical,
        "critical_failures": critical,
        "database_backup_path": backup_path,
        "database_backup_error": backup_error,
        "schema_version": schema_version,
        "sidecar_schema_version": 2,
        "missing_stable_id_video_ids": missing_ids,
        "duplicate_stable_ids": duplicate_ids,
        "unwritable_sidecar_video_ids": unwritable_sidecars,
        "missing_sidecar_video_ids": missing_sidecars,
        "orphan_file_video_ids": orphan_files,
        "pending_operations": pending,
    }


def post_migration_reconciliation(db: Session) -> dict:
    preflight = migration_preflight(db, create_backup=False)
    discrepancies = []
    for video_id in preflight["missing_stable_id_video_ids"]:
        discrepancies.append({"type": "missing_stable_id", "video_id": video_id, "repair": "assign_stable_id"})
    for stable_id in preflight["duplicate_stable_ids"]:
        discrepancies.append({"type": "duplicate_stable_id", "stable_id": stable_id, "repair": "review_identity_collision"})
    for video_id in preflight["orphan_file_video_ids"]:
        discrepancies.append({"type": "missing_file", "video_id": video_id, "repair": "relink_or_review"})
    for video_id in preflight["missing_sidecar_video_ids"]:
        discrepancies.append({"type": "missing_sidecar", "video_id": video_id, "repair": "enqueue_sidecar_rebuild"})
    from app.services.content_id import compute_file_signature
    from app.services.playarr_xml import parse_playarr_xml
    portable_ids = Counter(video.playarr_video_id for video in db.query(VideoItem).all() if video.playarr_video_id)
    for portable_id, count in portable_ids.items():
        if count > 1:
            discrepancies.append({
                "type": "duplicate_playarr_video_id", "playarr_video_id": portable_id,
                "count": count, "repair": "review_portable_identity_collision",
            })
    sidecar_versions = Counter()
    for video in db.query(VideoItem).all():
        if not video.playarr_video_id:
            discrepancies.append({
                "type": "missing_playarr_video_id", "video_id": video.id,
                "repair": "recompute_portable_identity",
            })
        if video.file_path and os.path.isfile(video.file_path) and video.file_checksum:
            actual = compute_file_signature(video.file_path)
            if actual != video.file_checksum:
                discrepancies.append({
                    "type": "file_checksum_mismatch", "video_id": video.id,
                    "expected": video.file_checksum, "actual": actual,
                    "repair": "review_changed_media",
                })
        if video.file_path:
            folder = Path(video.folder_path or Path(video.file_path).parent)
            sidecar_path = folder / f"{Path(video.file_path).stem}.playarr.xml"
            if sidecar_path.is_file():
                parsed = parse_playarr_xml(str(sidecar_path))
                if parsed is None:
                    discrepancies.append({
                        "type": "invalid_sidecar", "video_id": video.id,
                        "path": str(sidecar_path), "repair": "enqueue_sidecar_rebuild",
                    })
                else:
                    sidecar_versions[str(parsed.get("xml_version") or "v1_legacy")] += 1
                if parsed is not None and parsed.get("playarr_video_id") != video.playarr_video_id:
                    discrepancies.append({
                        "type": "sidecar_identity_mismatch", "video_id": video.id,
                        "sidecar_playarr_video_id": parsed.get("playarr_video_id"),
                        "repair": "review_sidecar_identity",
                    })
        if video.parent_video_id and db.get(VideoItem, video.parent_video_id) is None:
            discrepancies.append({
                "type": "missing_parent_relationship", "video_id": video.id,
                "parent_video_id": video.parent_video_id, "repair": "review_relationship",
            })

    for playlist in db.query(Playlist).all():
        positions = [entry.position for entry in playlist.entries]
        if positions != list(range(len(positions))):
            discrepancies.append({
                "type": "playlist_position_gap", "playlist_stable_id": playlist.stable_id,
                "positions": positions, "repair": "normalise_playlist_positions",
            })

    from app.services.archive_identity import resolve_manifest_video
    from app.routers.video_editor import _MANIFEST_NAME, _manifest_video_path
    for _library_root, archive_root in (
        (root, os.path.join(root, "_archive")) for root in get_settings().get_all_library_dirs()
    ):
        if not os.path.isdir(archive_root):
            continue
        for folder, _dirs, names in os.walk(archive_root):
            if _MANIFEST_NAME not in names:
                continue
            manifest_path = Path(folder) / _MANIFEST_NAME
            try:
                manifest = json.loads(manifest_path.read_text("utf-8"))
            except (OSError, ValueError) as exc:
                discrepancies.append({
                    "type": "invalid_archive_manifest", "path": str(manifest_path),
                    "error": str(exc), "repair": "review_archive_manifest",
                })
                continue
            if resolve_manifest_video(db, manifest) is None:
                discrepancies.append({
                    "type": "orphan_archive_link", "path": str(manifest_path),
                    "playarr_video_id": manifest.get("playarr_video_id"),
                    "repair": "relink_archive_by_identity",
                })
            if _manifest_video_path(folder, manifest) is None:
                discrepancies.append({
                    "type": "missing_archive_file", "path": str(manifest_path),
                    "repair": "review_archive_manifest",
                })
    empty_playlists = db.query(Playlist).filter(~Playlist.entries.any()).count()
    unresolved_reviews = db.query(ReviewCase).filter(ReviewCase.status == "open").count()
    return {
        "status": "complete" if not discrepancies else "discrepancies",
        "discrepancies": discrepancies,
        "relationship_checks": {
            "empty_playlists": empty_playlists,
            "artist_consolidations": db.query(ArtistConsolidation).count(),
            "unresolved_review_cases": unresolved_reviews,
            "videos": db.query(VideoItem).count(),
            "portable_video_ids": len(portable_ids),
        },
        "retry_actions": sorted({item["repair"] for item in discrepancies}),
        "migration_metrics": {
            "sidecars_read_by_schema": dict(sidecar_versions),
            "v1_read_supported": True,
            "v2_write_enabled": True,
            "v1_compatibility_fields_written": True,
        },
    }
