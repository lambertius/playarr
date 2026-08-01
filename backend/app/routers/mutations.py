"""Interactive mutation admission endpoints (ARCH-002, DB-001)."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import FileOperation, VideoItem
from app.services.mutation_api import accept_mutation, mutation_idempotency_key
from app.services.mutation_coordinator import CommandRequest, MutationPriority
from app.user_identity import get_instance_user_id

router = APIRouter(prefix="/api/library", tags=["Library mutations"])


class RatingMutation(BaseModel):
    expected_revision: int = Field(ge=1)
    song_rating: int | None = Field(default=None, ge=1, le=5)
    video_rating: int | None = Field(default=None, ge=1, le=5)
    idempotency_key: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def require_a_rating(self):
        if self.song_rating is None and self.video_rating is None:
            raise ValueError("song_rating or video_rating is required")
        return self


class RenameMutation(BaseModel):
    file_operation_id: str
    idempotency_key: str | None = Field(default=None, max_length=200)


@router.post("/{video_id}/rename-preview")
def preview_video_rename(video_id: int, db: Session = Depends(get_db)) -> dict:
    video = db.get(VideoItem, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    from app.services.rename_operations import preview_expected_rename
    try:
        operation = preview_expected_rename(db, video)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "file_operation_id": operation.id,
        "status": operation.status,
        "plan": operation.plan_json,
    }


@router.post("/{video_id}/rename-commit", status_code=status.HTTP_202_ACCEPTED)
def queue_video_rename(
    video_id: int,
    body: RenameMutation,
    db: Session = Depends(get_db),
) -> dict:
    video = db.get(VideoItem, video_id)
    operation = db.get(FileOperation, body.file_operation_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    if operation is None or operation.entity_stable_id != video.stable_id:
        raise HTTPException(status_code=404, detail="Rename preview not found")
    if operation.status != "planned":
        raise HTTPException(status_code=409, detail=f"Rename preview is {operation.status}")
    if operation.plan_json.get("collisions"):
        raise HTTPException(status_code=409, detail={
            "code": "file_collision",
            "message": "Resolve destination collisions and preview again",
            "collisions": operation.plan_json["collisions"],
        })
    return accept_mutation(db, CommandRequest(
        command_type="file.rename.execute",
        entity_type="video",
        entity_stable_id=video.stable_id,
        expected_revision=operation.expected_revision,
        actor_id=get_instance_user_id(db),
        priority=MutationPriority.INTERACTIVE,
        idempotency_key=mutation_idempotency_key(
            "file.rename.execute", video.stable_id, body.idempotency_key,
        ),
        payload={"file_operation_id": operation.id, "video_id": video.id},
    ))


@router.post("/{video_id}/rating", status_code=status.HTTP_202_ACCEPTED)
def queue_video_rating(
    video_id: int,
    body: RatingMutation,
    db: Session = Depends(get_db),
) -> dict:
    video = db.get(VideoItem, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return accept_mutation(db, CommandRequest(
        command_type="video.rating.update",
        entity_type="video",
        entity_stable_id=video.stable_id,
        expected_revision=body.expected_revision,
        actor_id=get_instance_user_id(db),
        priority=MutationPriority.INTERACTIVE,
        idempotency_key=mutation_idempotency_key(
            "video.rating.update", video.stable_id, body.idempotency_key,
        ),
        payload={
            "video_id": video.id,
            "song_rating": body.song_rating,
            "video_rating": body.video_rating,
        },
    ))
