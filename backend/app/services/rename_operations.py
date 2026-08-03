"""Expected-name orchestration built on the durable file-operation service."""
from __future__ import annotations

import os
from uuid import uuid4

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import FileOperation, MutationCommand, VideoItem
from app.services.file_operations import create_rename_operation, execute_file_operation
from app.services.file_organizer import build_folder_name, build_library_subpath
from app.services.mutation_coordinator import (
    CommandRequest,
    MutationPriority,
    enqueue_command,
)


def expected_rename_destination(video: VideoItem) -> tuple[str, str]:
    resolution = video.resolution_label or "1080p"
    base_name = build_folder_name(
        video.artist, video.title, resolution,
        version_type=video.version_type or "normal",
        alternate_version_label=video.alternate_version_label or "",
    )
    relative = build_library_subpath(
        video.artist, video.title, resolution, album=video.album or "",
        version_type=video.version_type or "normal",
        alternate_version_label=video.alternate_version_label or "",
    )
    return os.path.join(get_settings().library_dir, relative), base_name


def preview_expected_rename(db: Session, video: VideoItem) -> FileOperation:
    if not video.folder_path or not os.path.isdir(video.folder_path):
        raise FileNotFoundError("video has no valid folder on disk")
    destination, base_name = expected_rename_destination(video)
    same_folder = os.path.normcase(os.path.abspath(video.folder_path)) == os.path.normcase(
        os.path.abspath(destination)
    )
    current_name = os.path.splitext(os.path.basename(video.file_path or ""))[0]
    if same_folder and current_name == base_name:
        raise ValueError("filename already matches the expected pattern")
    return create_rename_operation(db, video, destination, base_name)


def enqueue_expected_rename(
    db: Session,
    video: VideoItem,
    *,
    actor_id: str | None = None,
    idempotency_key: str | None = None,
) -> tuple[FileOperation, MutationCommand, bool]:
    """Create the file journal and command in the caller's transaction.

    This is the internal equivalent of HTTP preview/commit.  It deliberately
    does not commit, allowing metadata, sidecar intent, review state, the file
    plan and the command to become visible atomically.
    """
    if not video.folder_path or not os.path.isdir(video.folder_path):
        raise FileNotFoundError("video has no valid folder on disk")
    destination, base_name = expected_rename_destination(video)
    same_folder = os.path.normcase(os.path.abspath(video.folder_path)) == os.path.normcase(
        os.path.abspath(destination)
    )
    current_name = os.path.splitext(os.path.basename(video.file_path or ""))[0]
    if same_folder and current_name == base_name:
        raise ValueError("filename already matches the expected pattern")
    operation = create_rename_operation(
        db, video, destination, base_name, commit=False,
    )
    command, created = enqueue_command(db, CommandRequest(
        command_type="file.rename.execute",
        entity_type="video",
        entity_stable_id=video.stable_id,
        expected_revision=int(video.revision or 1),
        actor_id=actor_id,
        priority=MutationPriority.INTERACTIVE,
        idempotency_key=(
            idempotency_key
            or f"file.rename.execute:{video.stable_id}:{uuid4()}"
        )[:200],
        payload={"file_operation_id": operation.id, "video_id": video.id},
    ))
    operation.command_id = command.id
    return operation, command, created


def notify_expected_rename() -> None:
    """Wake the configured mutation actor after the caller commits."""
    from app.services.mutation_runtime import notify_mutation_worker
    notify_mutation_worker()


def execute_expected_rename(db: Session, command: MutationCommand) -> dict:
    operation_id = str(command.payload_json["file_operation_id"])
    operation = db.get(FileOperation, operation_id)
    if operation is None or operation.entity_stable_id != command.entity_stable_id:
        raise LookupError("rename plan no longer exists or belongs to another video")
    if operation.expected_revision != command.expected_revision:
        raise ValueError("rename command revision differs from its preview")
    operation.command_id = command.id
    db.commit()
    result = execute_file_operation(db, operation.id)
    if result.status == "succeeded":
        video = db.query(VideoItem).filter(
            VideoItem.stable_id == command.entity_stable_id,
        ).one_or_none()
        if video and video.folder_path:
            try:
                from app.services.file_organizer import write_nfo_file
                write_nfo_file(
                    folder_path=video.folder_path,
                    artist=video.artist or "",
                    title=video.title or "",
                    album=video.album or "",
                    year=video.year,
                    genres=[genre.name for genre in (video.genres or [])],
                    plot=video.plot or "",
                    source_url=(video.sources[0].original_url if video.sources else ""),
                    resolution_label=video.resolution_label or "",
                    version_type=video.version_type or "normal",
                    alternate_version_label=video.alternate_version_label or "",
                    original_artist=video.original_artist or "",
                    original_title=video.original_title or "",
                )
            except Exception:
                # The Playarr sidecar is authoritative. NFO repair remains
                # discoverable through the next metadata reconciliation.
                pass
    return {
        "file_operation_id": result.id,
        "status": result.status,
        "video_id": operation.plan_json.get("video_id"),
    }
