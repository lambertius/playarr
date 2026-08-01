"""Upgrade preflight and post-migration logical reconciliation."""
from __future__ import annotations

import os
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
    empty_playlists = db.query(Playlist).filter(~Playlist.entries.any()).count()
    unresolved_reviews = db.query(ReviewCase).filter(ReviewCase.status == "open").count()
    return {
        "status": "complete" if not discrepancies else "discrepancies",
        "discrepancies": discrepancies,
        "relationship_checks": {
            "empty_playlists": empty_playlists,
            "artist_consolidations": db.query(ArtistConsolidation).count(),
            "unresolved_review_cases": unresolved_reviews,
        },
        "retry_actions": sorted({item["repair"] for item in discrepancies}),
    }
