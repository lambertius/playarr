"""Short domain handlers executed only by the mutation actor."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models import JobStatus, MutationCommand, ProcessingJob, VideoItem
from app.services.mutation_coordinator import StaleRevisionError


MutationHandler = Callable[[Session, MutationCommand], dict[str, Any] | None]
AfterCommit = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class MutationHandlerSpec:
    handle: MutationHandler
    after_commit: AfterCommit | None = None


def update_video_rating(db: Session, command: MutationCommand) -> dict[str, Any]:
    payload = command.payload_json
    video = db.get(VideoItem, int(payload["video_id"]))
    if video is None or video.stable_id != command.entity_stable_id:
        raise LookupError("video no longer exists or stable identity changed")
    current_revision = int(video.revision or 1)
    if command.expected_revision != current_revision:
        raise StaleRevisionError(command.expected_revision or 0, current_revision)
    now = datetime.now(timezone.utc)
    if payload.get("song_rating") is not None:
        video.song_rating = int(payload["song_rating"])
        video.song_rating_set = True
        video.song_rating_by = command.actor_id
        video.song_rating_at = now
    if payload.get("video_rating") is not None:
        video.video_rating = int(payload["video_rating"])
        video.video_rating_set = True
        video.video_rating_by = command.actor_id
        video.video_rating_at = now
    video.revision = current_revision + 1
    from app.services.sidecar_outbox import schedule_sidecar_write
    schedule_sidecar_write(db, video, operation_id=command.id)
    return {"video_id": video.id, "revision": video.revision}


def dismiss_new_video(db: Session, command: MutationCommand) -> dict[str, Any]:
    from app.new_videos import feedback_service, recommendation_service
    from app.new_videos.models import SuggestedVideo, SuggestedVideoDismissal
    payload = command.payload_json
    suggestion = db.get(SuggestedVideo, int(payload["suggested_video_id"]))
    if suggestion is None:
        raise LookupError("suggested video no longer exists")
    dismissal = SuggestedVideoDismissal(
        suggested_video_id=suggestion.id,
        dismissal_type=payload["dismissal_type"],
        reason=payload.get("reason"),
        provider=suggestion.provider,
        provider_video_id=suggestion.provider_video_id,
    )
    db.add(dismissal)
    feedback_service.record_feedback(
        db,
        feedback_type=(
            "permanently_dismissed"
            if payload["dismissal_type"] == "permanent" else "dismissed"
        ),
        suggested_video_id=suggestion.id,
        provider=suggestion.provider,
        provider_video_id=suggestion.provider_video_id,
        artist=suggestion.artist,
        category=suggestion.category,
    )
    db.flush()
    category_feed = recommendation_service.get_feed(db)["categories"][suggestion.category]["videos"]
    return {
        "status": "dismissed",
        "type": payload["dismissal_type"],
        "replacement": category_feed[-1] if category_feed else None,
        "exhausted": not bool(category_feed),
    }


def add_new_video(db: Session, command: MutationCommand) -> dict[str, Any]:
    from app.new_videos import feedback_service, recommendation_service
    from app.new_videos.models import SuggestedVideo, SuggestedVideoDismissal
    suggestion = db.get(SuggestedVideo, int(command.payload_json["suggested_video_id"]))
    if suggestion is None:
        raise LookupError("suggested video no longer exists")
    job = ProcessingJob(
        job_type="import_url",
        status=JobStatus.queued,
        input_url=suggestion.url,
        display_name=(
            f"{suggestion.artist} - {suggestion.title} - New Videos Quick Add"
            if suggestion.artist and suggestion.title else suggestion.url
        ),
        action_label="New Videos quick add",
        operation_id=command.id,
        input_params={
            "suggested_video_id": suggestion.id,
            "provider": suggestion.provider,
            "provider_video_id": suggestion.provider_video_id,
            "category": suggestion.category,
            "normalize": True,
            "scrape": True,
            "scrape_musicbrainz": True,
        },
    )
    db.add(job)
    db.flush()
    db.add(SuggestedVideoDismissal(
        suggested_video_id=suggestion.id,
        dismissal_type="permanent",
        reason="auto-dismissed on add",
        provider=suggestion.provider,
        provider_video_id=suggestion.provider_video_id,
    ))
    feedback_service.record_feedback(
        db, feedback_type="added", suggested_video_id=suggestion.id,
        provider=suggestion.provider, provider_video_id=suggestion.provider_video_id,
        artist=suggestion.artist, category=suggestion.category,
    )
    db.flush()
    category_feed = recommendation_service.get_feed(db)["categories"][suggestion.category]["videos"]
    return {
        "status": "importing",
        "job_id": job.id,
        "url": suggestion.url,
        "category": suggestion.category,
        "replacement": category_feed[-1] if category_feed else None,
        "exhausted": not bool(category_feed),
    }


def dispatch_new_video_import(result: dict[str, Any]) -> None:
    from app.tasks import import_video_task
    from app.worker import dispatch_task
    dispatch_task(
        import_video_task, job_id=result["job_id"], url=result["url"],
        normalize=True, scrape=True, scrape_musicbrainz=True,
    )


def apply_import_plan(db: Session, command: MutationCommand) -> dict[str, Any]:
    """Apply the canonical Stage C plan inside the mutation actor transaction."""
    from app.pipeline.db_apply import TocTouDuplicateError, _execute_plan

    try:
        plan = dict(command.payload_json["plan"])
        plan["operation_id"] = command.id
        video_id = _execute_plan(plan, db)
    except TocTouDuplicateError as exc:
        return {
            "duplicate": True,
            "existing_video_id": exc.existing_video_id,
            "reason": exc.reason,
        }
    return {"video_id": video_id}


def handler_registry() -> dict[str, MutationHandlerSpec]:
    from app.services.rename_operations import execute_expected_rename
    return {
        "video.rating.update": MutationHandlerSpec(update_video_rating),
        "new_videos.dismiss": MutationHandlerSpec(dismiss_new_video),
        "new_videos.add": MutationHandlerSpec(add_new_video, dispatch_new_video_import),
        "file.rename.execute": MutationHandlerSpec(execute_expected_rename),
        "import.plan.apply": MutationHandlerSpec(apply_import_plan),
    }
