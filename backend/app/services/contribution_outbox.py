"""Durable TMVDB submission acceptance and bounded background delivery."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.models import AppSetting, ContributionLog, ContributionOutbox, VideoItem
from app.provenance import build_eligible_contribution
from app.services.request_context import current_request_id, new_operation_id


def enqueue_contribution(
    db: Session,
    video: VideoItem,
    instance_user_id: str,
    *,
    force: bool = False,
) -> tuple[ContributionOutbox | None, dict, bool]:
    eligibility = build_eligible_contribution(video, instance_user_id)
    if not eligibility["can_submit"]:
        return None, eligibility, False
    envelope = eligibility["submission"]
    payload_hash = envelope["payload_hash"]
    base_key = f"tmvdb:{video.stable_id}:{payload_hash}"
    idempotency_key = f"{base_key}:force:{uuid4().hex}" if force else base_key
    existing = db.query(ContributionOutbox).filter(
        ContributionOutbox.idempotency_key == idempotency_key,
    ).one_or_none()
    if existing:
        return existing, eligibility, False
    row = ContributionOutbox(
        id=str(uuid4()),
        video_id=video.id,
        operation_id=new_operation_id(),
        request_id=current_request_id(),
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
        envelope_json=envelope,
        eligibility_json={
            "fields": eligibility["eligibility"],
            "eligible_fields": eligibility["eligible_fields"],
            "excluded_fields": eligibility["excluded_fields"],
        },
        status="pending",
    )
    db.add(row)
    from app.services.provenance_events import record_field_event
    for field, state in envelope.get("fields", {}).items():
        record_field_event(
            db, video, field, event_type="submission_queued", actor_kind="instance",
            actor_id=instance_user_id, prior_value=state.get("value"),
            resulting_value=state.get("value"), provider="tmvdb",
            transformation="eligibility_snapshot", operation_id=row.operation_id,
        )
    db.flush()
    return row, eligibility, True


def process_next_contribution(session_factory: sessionmaker) -> str | None:
    db = session_factory()
    try:
        row = (
            db.query(ContributionOutbox)
            .filter(ContributionOutbox.status.in_(("pending", "retry")))
            .order_by(ContributionOutbox.created_at.asc())
            .first()
        )
        if row is None:
            return None
        row.status = "running"
        row.started_at = datetime.now(timezone.utc)
        row.attempts += 1
        row_id = row.id
        envelope = dict(row.envelope_json)
        video_id = row.video_id
        db.commit()

        settings = {
            setting.key: setting.value for setting in db.query(AppSetting).filter(
                AppSetting.key.in_(("tmvdb_enabled", "tmvdb_api_key")),
                AppSetting.user_id.is_(None),
            ).all()
        }
        db.rollback()  # no database transaction remains open during HTTP I/O
        if settings.get("tmvdb_enabled") != "true" or not settings.get("tmvdb_api_key"):
            raise RuntimeError("TMVDB is not configured")
        from app.metadata.providers.tmvdb import TMVDBProvider
        result = TMVDBProvider(api_key=settings["tmvdb_api_key"]).push_track(envelope)
        if not result:
            raise RuntimeError("TMVDB returned no submission result")

        row = db.get(ContributionOutbox, row_id)
        row.status = "submitted"
        row.remote_id = str(result.get("id")) if result.get("id") is not None else None
        row.response_json = result
        row.error_json = None
        row.completed_at = datetime.now(timezone.utc)
        video = db.get(VideoItem, video_id) if video_id else None
        db.add(ContributionLog(
            video_id=video_id,
            instance_user_id=envelope.get("instance_user_id"),
            target="tmvdb",
            operation="push",
            playarr_track_id=video.playarr_track_id if video else envelope.get("identity", {}).get("playarr_track_id"),
            playarr_video_id=video.playarr_video_id if video else envelope.get("identity", {}).get("playarr_video_id"),
            payload_hash=row.payload_hash,
            status="submitted",
            remote_id=row.remote_id,
            response=result,
        ))
        if video:
            from app.services.provenance_events import record_field_event
            submitted_at = datetime.now(timezone.utc)
            for field, state in envelope.get("fields", {}).items():
                record_field_event(
                    db, video, field, event_type="submitted", actor_kind="instance",
                    actor_id=envelope.get("instance_user_id"),
                    prior_value=state.get("value"), resulting_value=state.get("value"),
                    provider="tmvdb", remote_id=row.remote_id,
                    transformation="tmvdb_push", operation_id=row.operation_id,
                    submitted_at=submitted_at,
                )
        db.commit()
        return row_id
    except Exception as exc:
        db.rollback()
        if "row_id" not in locals():
            raise
        row = db.get(ContributionOutbox, row_id)
        if row:
            row.status = "retry" if row.attempts < row.max_attempts else "failed"
            row.error_json = {
                "code": "tmvdb_submit_failed",
                "message": str(exc),
                "retryable": row.attempts < row.max_attempts,
            }
            if row.status == "failed":
                row.completed_at = datetime.now(timezone.utc)
            db.commit()
        return row_id
    finally:
        db.close()
