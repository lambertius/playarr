"""Expected-name orchestration built on the durable file-operation service."""
from __future__ import annotations

import os

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import FileOperation, MutationCommand, VideoItem
from app.services.file_operations import create_rename_operation, execute_file_operation
from app.services.file_organizer import build_folder_name, build_library_subpath


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
    return {
        "file_operation_id": result.id,
        "status": result.status,
        "video_id": operation.plan_json.get("video_id"),
    }
