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
    video.sidecar_revision = max(int(video.sidecar_revision or 0) + 1, video.revision)

    existing = db.query(SidecarOutbox).filter(
        SidecarOutbox.video_id == video.id,
        SidecarOutbox.entity_revision == video.sidecar_revision,
    ).one_or_none()
    if existing is not None:
        return existing

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

        from app.services.playarr_xml import parse_playarr_xml, write_playarr_xml
        path = write_playarr_xml(video, db)
        if not path:
            raise FileNotFoundError("video folder is unavailable")
        parsed = parse_playarr_xml(path)
        if parsed is None:
            raise ValueError("written sidecar could not be parsed")
        entry.target_path = path
        entry.content_hash = parsed.get("content_hash")
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
