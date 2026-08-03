"""Durable database-to-sidecar reconciliation worker."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.models import SidecarOutbox, VideoItem
from app.services.content_id import compute_ids_for_video

logger = logging.getLogger(__name__)


def schedule_sidecar_write(
    db: Session,
    video: VideoItem,
    *,
    operation_id: str | None = None,
) -> SidecarOutbox:
    """Append an outbox row in the same transaction as a video mutation."""
    ids = compute_ids_for_video(video)
    if not video.playarr_video_id:
        video.playarr_video_id = ids["playarr_video_id"]
    if not video.playarr_track_id:
        video.playarr_track_id = ids["playarr_track_id"]
    video.revision = max(1, int(video.revision or 1))
    existing = (
        db.query(SidecarOutbox)
        .filter(
            SidecarOutbox.video_id == video.id,
            SidecarOutbox.status.in_(("pending", "retry")),
        )
        .order_by(SidecarOutbox.created_at.desc())
        .first()
    )
    if existing is not None:
        video.sidecar_revision = max(int(video.sidecar_revision or 0), video.revision)
        existing.entity_revision = video.sidecar_revision
        existing.entity_stable_id = video.playarr_video_id
        existing.operation_id = operation_id or existing.operation_id
        return existing

    video.sidecar_revision = max(int(video.sidecar_revision or 0) + 1, video.revision)

    target_path = None
    if video.folder_path:
        base = (
            os.path.splitext(os.path.basename(video.file_path))[0]
            if video.file_path else os.path.basename(video.folder_path)
        )
        target_path = str(Path(video.folder_path) / f"{base}.playarr.xml")
    entry = SidecarOutbox(
        id=str(uuid4()),
        operation_id=operation_id,
        video_id=video.id,
        entity_stable_id=video.playarr_video_id,
        target_path=target_path,
        entity_revision=video.sidecar_revision,
        status="pending",
    )
    db.add(entry)
    db.flush()
    return entry


def process_next_sidecar(session_factory: sessionmaker) -> bool:
    """Claim and reconcile one sidecar. Returns False when no work exists."""
    db = session_factory()
    try:
        entry = (
            db.query(SidecarOutbox)
            .filter(SidecarOutbox.status.in_(("pending", "retry")))
            .order_by(SidecarOutbox.created_at.asc())
            .first()
        )
        if entry is None:
            return False
        entry.status = "running"
        entry.attempts += 1
        entry_id = entry.id
        db.commit()
    finally:
        db.close()

    db = session_factory()
    try:
        entry = db.get(SidecarOutbox, entry_id)
        video = db.get(VideoItem, entry.video_id) if entry else None
        if entry is None or video is None:
            raise LookupError("sidecar outbox entity no longer exists")
        if video.playarr_video_id != entry.entity_stable_id:
            raise ValueError("sidecar stable identity changed before reconciliation")

        from app.services.playarr_xml import parse_playarr_xml, _write_playarr_xml_now
        path = _write_playarr_xml_now(video, db)
        if not path:
            raise FileNotFoundError("video folder is unavailable")
        parsed = parse_playarr_xml(path)
        if parsed is None:
            raise ValueError("written sidecar could not be parsed")
        entry.target_path = path
        entry.content_hash = parsed.get("content_hash")
        from app.services.consolidations import write_library_consolidation_manifest
        write_library_consolidation_manifest(db)
        entry.status = "complete"
        entry.completed_at = datetime.now(timezone.utc)
        entry.error_json = None
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        failed = db.get(SidecarOutbox, entry_id)
        if failed is not None:
            failed.status = "retry" if failed.attempts < 5 else "failed"
            failed.error_json = {
                "code": "sidecar_write_failed",
                "message": str(exc),
                "retryable": failed.attempts < 5,
            }
            db.commit()
        logger.exception("Sidecar reconciliation failed for outbox %s", entry_id)
        return True
    finally:
        db.close()


def outbox_stats(db: Session) -> dict[str, int]:
    rows = db.query(SidecarOutbox.status).all()
    result: dict[str, int] = {}
    for (status,) in rows:
        result[status] = result.get(status, 0) + 1
    return result


def schedule_stale_sidecars(db: Session) -> int:
    """Repair missing outbox intent from revision or schema drift.

    A sidecar can have the current entity revision while still using an older
    document schema.  Startup reconciliation must therefore compare both; this
    is what makes an in-place application upgrade also migrate the portable
    library representation without requiring a metadata edit for every video.
    """
    from app.services.playarr_xml import (
        PLAYARR_XML_VERSION,
        find_playarr_xml,
        parse_playarr_xml,
    )

    scheduled = 0
    for video in db.query(VideoItem).filter(VideoItem.folder_path.isnot(None)).all():
        latest = db.query(SidecarOutbox).filter(
            SidecarOutbox.video_id == video.id,
        ).order_by(SidecarOutbox.created_at.desc()).first()
        if latest is not None and latest.status in {"pending", "retry", "running", "failed", "cancelled"}:
            continue
        actual_revision = 0
        actual_schema_version = None
        path = find_playarr_xml(video.folder_path, video_file=video.file_path)
        if path:
            try:
                parsed = parse_playarr_xml(path) or {}
                actual_revision = int(parsed.get("entity_revision") or 0)
                actual_schema_version = str(parsed.get("xml_version") or "1")
            except Exception:
                actual_revision = 0
                actual_schema_version = None
        if (
            actual_revision < int(video.revision or 1)
            or actual_schema_version != PLAYARR_XML_VERSION
        ):
            schedule_sidecar_write(db, video)
            scheduled += 1
    if scheduled:
        db.commit()
    return scheduled
