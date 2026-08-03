"""Synchronise portable manifests into a bounded, SQL-queryable catalogue."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.durability_models import ArchiveCatalogEntry


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def sync_archive_catalog(db: Session) -> int:
    from app.config import get_settings
    from app.routers.video_editor import _VIDEO_EXTS, _manifest_video_path, _read_folder_manifest
    from app.services.archive_identity import manifest_video_stable_id, resolve_manifest_video
    seen: set[str] = set()
    for library_root in get_settings().get_all_library_dirs():
        archive_root = os.path.join(library_root, "_archive")
        if not os.path.isdir(archive_root):
            continue
        for root, _directories, names in os.walk(archive_root):
            manifest = _read_folder_manifest(root) or {}
            video_path = _manifest_video_path(root, manifest) if manifest else None
            if not video_path:
                video_path = next((os.path.join(root, name) for name in sorted(names) if os.path.splitext(name)[1].lower() in _VIDEO_EXTS), None)
            if not video_path:
                continue
            seen.add(root)
            video = resolve_manifest_video(db, manifest)
            relative = manifest.get("original_relative_path")
            original_path = video.file_path if video else (os.path.join(library_root, relative) if relative else None)
            stable_id = manifest_video_stable_id(manifest)
            integrity = "orphaned_owner" if manifest.get("video_id") and not video else "ok" if manifest and stable_id else "legacy_manifest" if manifest else "missing_manifest"
            entry = db.query(ArchiveCatalogEntry).filter(ArchiveCatalogEntry.folder == root).one_or_none()
            if entry is None:
                entry = ArchiveCatalogEntry(folder=root, path=video_path)
                db.add(entry)
            entry.path = video_path; entry.reason = manifest.get("archive_reason", "edit")
            entry.artist = manifest.get("artist", ""); entry.title = manifest.get("title", "")
            entry.video_id = video.id if video else None; entry.video_stable_id = stable_id
            entry.operation_id = manifest.get("operation_id"); entry.original_path = original_path
            entry.archived_at = _parse_time(manifest.get("archived_at"))
            entry.file_size_bytes = manifest.get("file_size_bytes", 0) or (os.path.getsize(video_path) if os.path.isfile(video_path) else 0)
            entry.checksum_md5 = manifest.get("checksum_md5"); entry.checksum_sha256 = manifest.get("checksum_sha256")
            entry.manifest_schema_version = manifest.get("schema_version", 1 if manifest else None)
            entry.restore_eligible = os.path.isfile(video_path); entry.integrity_status = integrity
            entry.last_seen_at = datetime.now(timezone.utc).replace(tzinfo=None)
    stale = db.query(ArchiveCatalogEntry)
    if seen:
        stale = stale.filter(ArchiveCatalogEntry.folder.notin_(seen))
    stale.delete(synchronize_session=False)
    db.commit()
    return len(seen)


def query_archive_catalog(
    db: Session, *, reason: str | None, search: str | None,
    video_id: int | None = None, page: int, page_size: int,
) -> dict:
    query = db.query(ArchiveCatalogEntry)
    if video_id is not None:
        query = query.filter(ArchiveCatalogEntry.video_id == video_id)
    if reason and reason != "all":
        if reason == "orphaned":
            query = query.filter(ArchiveCatalogEntry.integrity_status == "orphaned_owner")
        else:
            query = query.filter(ArchiveCatalogEntry.reason == reason)
    if search:
        term = f"%{search.strip()}%"
        query = query.filter(or_(ArchiveCatalogEntry.artist.ilike(term), ArchiveCatalogEntry.title.ilike(term)))
    total = query.count()
    rows = query.order_by(ArchiveCatalogEntry.archived_at.desc(), ArchiveCatalogEntry.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    count_rows = db.query(ArchiveCatalogEntry.reason, func.count(ArchiveCatalogEntry.id)).group_by(ArchiveCatalogEntry.reason).all()
    counts = {key: value for key, value in count_rows}
    counts["all"] = db.query(ArchiveCatalogEntry).count()
    counts["orphaned"] = db.query(ArchiveCatalogEntry).filter(ArchiveCatalogEntry.integrity_status == "orphaned_owner").count()
    items = [{
        "path": row.path, "folder": row.folder, "reason": row.reason,
        "artist": row.artist, "title": row.title, "video_id": row.video_id,
        "archived_at": row.archived_at.isoformat() if row.archived_at else "",
        "file_size_bytes": row.file_size_bytes, "original_path": row.original_path,
        "checksum_md5": row.checksum_md5, "checksum_sha256": row.checksum_sha256,
        "playarr_video_id": row.video_stable_id, "operation_id": row.operation_id,
        "manifest_schema_version": row.manifest_schema_version,
        "restore_eligible": row.restore_eligible, "integrity_status": row.integrity_status,
    } for row in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size, "total_pages": max(1, (total + page_size - 1) // page_size), "reason_counts": counts}
