"""Durable planner, journal, executor and reconciler for managed library files.

Filesystem changes cannot share a transaction with SQLite.  This service
therefore records every source, staging and destination path before touching
disk, checkpoints every move, and changes database paths only after the whole
file set is installed.  An interrupted operation is rolled back from its
journal at startup.
"""
from __future__ import annotations

import copy
import errno
import hashlib
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import FileOperation, MediaAsset, VideoItem
from app.services.mutation_coordinator import StaleRevisionError


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".webm", ".avi", ".mov", ".mpg", ".mpeg"}
ARTWORK_SUFFIXES = (
    "-album-thumb", "-artist-thumb", "-poster", "-fanart", "-thumb",
    "-banner", "-landscape", "-clearart", "-clearlogo", "-discart",
)
RECOVERABLE_STATUSES = {"running", "reconciliation_required"}


class FilePlanCollision(RuntimeError):
    def __init__(self, collisions: list[dict]):
        self.collisions = collisions
        super().__init__(f"file plan has {len(collisions)} collision(s)")


class FileWaitingForRelease(RuntimeError):
    pass


class FileOperationFailed(RuntimeError):
    pass


def _transient_file_lock(exc: BaseException) -> bool:
    if not isinstance(exc, OSError):
        return False
    winerror = getattr(exc, "winerror", None)
    if winerror is not None:
        return winerror in {32, 33}
    return (
        getattr(exc, "errno", None) in {errno.EBUSY, errno.EAGAIN}
        or "being used by another process" in str(exc).lower()
        or "sharing violation" in str(exc).lower()
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _path_key(value: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(value)))


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _nearest_existing(path: str) -> str:
    candidate = os.path.abspath(path)
    while not os.path.exists(candidate):
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
    return candidate


def _same_volume(source: str, destination: str) -> bool:
    source_drive = os.path.splitdrive(os.path.abspath(source))[0].lower()
    destination_drive = os.path.splitdrive(os.path.abspath(destination))[0].lower()
    if source_drive or destination_drive:
        return source_drive == destination_drive
    try:
        return os.stat(_nearest_existing(source)).st_dev == os.stat(
            _nearest_existing(destination)
        ).st_dev
    except OSError:
        return True


def _role_for(path: str, video_path: str | None) -> str:
    if video_path and _path_key(path) == _path_key(video_path):
        return "video"
    lower = os.path.basename(path).lower()
    extension = os.path.splitext(lower)[1]
    if lower == ".playarr-archive.json":
        return "archive_manifest"
    if lower.endswith(".playarr.xml") or extension == ".xml":
        return "playarr_sidecar"
    if extension == ".nfo":
        return "nfo"
    if any(lower.rsplit(extension, 1)[0].endswith(suffix) for suffix in ARTWORK_SUFFIXES):
        return "artwork"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    return "companion"


def _renamed_file(name: str, new_base_name: str, role: str) -> str:
    lower = name.lower()
    extension = os.path.splitext(name)[1]
    stem = name[:-len(extension)] if extension else name
    if lower == ".playarr-archive.json":
        return name
    if lower.endswith(".playarr.xml"):
        return f"{new_base_name}.playarr.xml"
    if role in {"video", "nfo", "playarr_sidecar"}:
        return f"{new_base_name}{extension}"
    for suffix in ARTWORK_SUFFIXES:
        if stem.lower().endswith(suffix):
            return f"{new_base_name}{suffix}{extension}"
    return name


def _stream_active(path: str) -> bool:
    from app.routers.playback import is_streaming_file
    return is_streaming_file(path)


def _request_playback_release(path: str) -> int:
    from app.routers.playback import kill_streams_for_file
    return kill_streams_for_file(path)


def plan_video_rename(
    video: VideoItem,
    new_folder: str,
    new_base_name: str,
    *,
    operation_id: str | None = None,
) -> dict:
    """Calculate an exact, immutable companion-file plan without mutating disk."""
    if not video.folder_path or not os.path.isdir(video.folder_path):
        raise FileNotFoundError("video has no library folder on disk")
    operation_id = operation_id or str(uuid4())
    old_folder = os.path.abspath(video.folder_path)
    new_folder = os.path.abspath(new_folder)
    steps: list[dict] = []
    collisions: list[dict] = []
    path_updates: dict[str, str] = {}

    for root, directories, files in os.walk(old_folder):
        directories.sort()
        for filename in sorted(files):
            source = os.path.join(root, filename)
            relative_parent = os.path.relpath(root, old_folder)
            role = _role_for(source, video.file_path)
            destination_name = _renamed_file(filename, new_base_name, role)
            destination = os.path.join(
                new_folder,
                "" if relative_parent == "." else relative_parent,
                destination_name,
            )
            source_key = _path_key(source)
            destination_key = _path_key(destination)
            same_logical_path = source_key == destination_key
            if os.path.exists(destination) and not same_logical_path:
                collisions.append({
                    "source": source,
                    "destination": destination,
                    "reason": "destination_exists",
                })
            temporary = os.path.join(
                os.path.dirname(destination),
                f".{os.path.basename(destination)}.playarr-{operation_id}.tmp",
            )
            step = {
                "index": len(steps),
                "role": role,
                "source": source,
                "destination": destination,
                "temporary": temporary,
                "checksum": _sha256(source),
                "size_bytes": os.path.getsize(source),
                "state": "pending",
                "case_only": same_logical_path and source != destination,
                "cross_volume": not _same_volume(source, destination),
            }
            steps.append(step)
            path_updates[source_key] = destination

    if not steps:
        raise FileNotFoundError("video folder contains no files to move")
    return {
        "version": 2,
        "operation_id": operation_id,
        "operation_type": "rename",
        "playarr_video_id": video.stable_id,
        "video_id": video.id,
        "expected_revision": int(video.revision or 1),
        "old_folder": old_folder,
        "new_folder": new_folder,
        "new_base_name": new_base_name,
        "steps": steps,
        "path_updates": path_updates,
        "collisions": collisions,
        "case_only": any(step["case_only"] for step in steps),
        "cross_volume": any(step["cross_volume"] for step in steps),
        "active_stream_usage": bool(video.file_path and _stream_active(video.file_path)),
    }


def create_rename_operation(
    db: Session,
    video: VideoItem,
    new_folder: str,
    new_base_name: str,
    *,
    commit: bool = True,
) -> FileOperation:
    operation_id = str(uuid4())
    plan = plan_video_rename(
        video, new_folder, new_base_name, operation_id=operation_id,
    )
    operation = FileOperation(
        id=operation_id,
        entity_stable_id=video.stable_id,
        operation_type="rename",
        status="planned",
        expected_revision=int(video.revision or 1),
        plan_json=plan,
        rollback_json={
            "old_folder": plan["old_folder"],
            "new_folder": plan["new_folder"],
            "steps": [
                {"source": step["source"], "destination": step["destination"]}
                for step in plan["steps"]
            ],
        },
    )
    db.add(operation)
    if commit:
        db.commit()
        db.refresh(operation)
    else:
        db.flush()
    return operation


def create_file_set_operation(
    db: Session,
    video: VideoItem,
    operation_type: str,
    transitions: list[dict],
    *,
    database_updates: dict | None = None,
    metadata: dict | None = None,
    command_id: str | None = None,
) -> FileOperation:
    """Persist an immutable archive/replace/restore/delete file-set plan."""
    if operation_type not in {"archive", "replace", "restore", "delete"}:
        raise ValueError(f"unsupported managed file operation {operation_type}")
    operation_id = str(uuid4())
    source_keys = {_path_key(item["source"]) for item in transitions}
    steps, collisions = [], []
    for index, transition in enumerate(transitions):
        source = os.path.abspath(transition["source"])
        destination = os.path.abspath(transition["destination"])
        if not os.path.isfile(source):
            raise FileNotFoundError(source)
        same_path = _path_key(source) == _path_key(destination)
        # A destination which is another step's source will be vacated first.
        if os.path.exists(destination) and not same_path and _path_key(destination) not in source_keys:
            collisions.append({"source": source, "destination": destination, "reason": "destination_exists"})
        steps.append({
            "index": index,
            "role": transition.get("role") or _role_for(source, video.file_path),
            "source": source,
            "destination": destination,
            "temporary": os.path.join(
                os.path.dirname(destination),
                f".{os.path.basename(destination)}.playarr-{operation_id}.tmp",
            ),
            "checksum": _sha256(source),
            "size_bytes": os.path.getsize(source),
            "state": "pending",
            "case_only": same_path and source != destination,
            "cross_volume": not _same_volume(source, destination),
        })
    plan = {
        "version": 2, "operation_id": operation_id,
        "operation_type": operation_type, "playarr_video_id": video.playarr_video_id,
        "video_id": video.id, "expected_revision": int(video.revision or 1),
        "steps": steps, "collisions": collisions,
        "database_updates": database_updates or {}, **(metadata or {}),
    }
    operation = FileOperation(
        id=operation_id, command_id=command_id, entity_stable_id=video.stable_id,
        operation_type=operation_type, status="planned",
        expected_revision=int(video.revision or 1), plan_json=plan,
        rollback_json={"steps": [
            {"source": step["source"], "destination": step["destination"]}
            for step in steps
        ]},
    )
    db.add(operation); db.commit(); db.refresh(operation)
    return operation


def _save_step(db: Session, operation: FileOperation, index: int, state: str) -> None:
    plan = copy.deepcopy(operation.plan_json)
    plan["steps"][index]["state"] = state
    operation.plan_json = plan
    operation.current_step = index
    db.commit()
    db.refresh(operation)


def _ensure_released(path: str, timeout_seconds: float) -> None:
    _request_playback_release(path)
    deadline = time.monotonic() + timeout_seconds
    while _stream_active(path) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _stream_active(path):
        raise FileWaitingForRelease(f"playback still holds {path}")


def _install_step(
    db: Session,
    operation: FileOperation,
    index: int,
    *,
    fault: Callable[[str, int], None] | None,
) -> None:
    step = operation.plan_json["steps"][index]
    source = step["source"]
    destination = step["destination"]
    temporary = step["temporary"]
    state = step["state"]
    if state == "moved" and os.path.isfile(destination):
        if _sha256(destination) != step["checksum"]:
            raise FileOperationFailed(f"destination checksum changed: {destination}")
        return
    if step["role"] == "video" and state == "pending":
        _ensure_released(source, 1.0)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    if os.path.exists(destination) and _path_key(source) != _path_key(destination):
        raise FilePlanCollision([{
            "source": source, "destination": destination,
            "reason": "destination_appeared_after_preview",
        }])

    if step["cross_volume"]:
        if state == "pending":
            shutil.copy2(source, temporary)
            if _sha256(temporary) != step["checksum"]:
                raise FileOperationFailed(f"staged checksum mismatch: {source}")
            _save_step(db, operation, index, "staged_copy")
            state = "staged_copy"
            if fault:
                fault("after_stage", index)
        if state == "staged_copy":
            os.replace(temporary, destination)
            _save_step(db, operation, index, "installed_copy")
            state = "installed_copy"
        if state == "installed_copy":
            os.remove(source)
            _save_step(db, operation, index, "moved")
    else:
        if state == "pending":
            os.replace(source, temporary)
            _save_step(db, operation, index, "staged")
            state = "staged"
            if fault:
                fault("after_stage", index)
        if state == "staged":
            os.replace(temporary, destination)
            _save_step(db, operation, index, "moved")
    if fault:
        fault("after_move", index)


def _apply_database_paths(db: Session, operation: FileOperation) -> VideoItem:
    plan = operation.plan_json
    video = db.query(VideoItem).filter(
        VideoItem.stable_id == operation.entity_stable_id
    ).one_or_none()
    if video is None:
        raise LookupError("video no longer exists")
    current_revision = int(video.revision or 1)
    if operation.expected_revision != current_revision:
        raise StaleRevisionError(operation.expected_revision or 0, current_revision)
    if operation.operation_type == "rename":
        updates = plan["path_updates"]
        if video.file_path and _path_key(video.file_path) in updates:
            video.file_path = updates[_path_key(video.file_path)]
        video.folder_path = plan["new_folder"]
        for asset in db.query(MediaAsset).filter(MediaAsset.video_id == video.id).all():
            key = _path_key(asset.file_path)
            if key in updates:
                asset.file_path = updates[key]
        try:
            from app.ai.models import AIThumbnail
            for thumbnail in db.query(AIThumbnail).filter(AIThumbnail.video_id == video.id).all():
                key = _path_key(thumbnail.file_path)
                if key in updates:
                    thumbnail.file_path = updates[key]
        except (ImportError, LookupError):
            pass
    else:
        allowed = {"file_path", "folder_path", "file_size_bytes", "file_checksum", "editor_edit_type"}
        for field, value in (plan.get("database_updates") or {}).items():
            if field not in allowed:
                raise FileOperationFailed(f"unsupported database update {field}")
            setattr(video, field, value)
    video.revision = current_revision + 1
    from app.services.sidecar_outbox import schedule_sidecar_write
    schedule_sidecar_write(db, video, operation_id=operation.command_id)
    return video


def execute_file_operation(
    db: Session,
    operation_id: str,
    *,
    fault: Callable[[str, int], None] | None = None,
) -> FileOperation:
    """Execute a persisted plan and checkpoint every filesystem transition."""
    operation = db.get(FileOperation, operation_id)
    if operation is None:
        raise LookupError(f"unknown file operation {operation_id}")
    if operation.status == "succeeded":
        return operation
    if operation.status not in {"planned", "waiting_for_release"}:
        raise FileOperationFailed(f"operation is {operation.status}, not executable")
    collisions = (operation.plan_json or {}).get("collisions", [])
    if collisions:
        raise FilePlanCollision(collisions)
    previous_attempts = int((operation.error_json or {}).get("attempts") or 0)
    operation.status = "running"
    operation.started_at = operation.started_at or _utcnow()
    operation.error_json = None
    db.commit()
    db.refresh(operation)
    try:
        for index in range(len(operation.plan_json["steps"])):
            _install_step(db, operation, index, fault=fault)
        if fault:
            fault("before_database", len(operation.plan_json["steps"]))
        _apply_database_paths(db, operation)
        operation.status = "succeeded"
        operation.current_step = len(operation.plan_json["steps"])
        operation.completed_at = _utcnow()
        operation.error_json = None
        db.commit()
        db.refresh(operation)
        old_folder = operation.plan_json.get("old_folder")
        if old_folder:
            try:
                os.removedirs(old_folder)
            except OSError:
                pass
        return operation
    except FileWaitingForRelease as exc:
        db.rollback()
        operation = db.get(FileOperation, operation_id)
        attempts = previous_attempts + 1
        operation.status = "waiting_for_release"
        operation.error_json = {
            "code": "waiting_for_release", "message": str(exc), "retryable": True,
            "attempts": attempts,
            "retry_after": time.time() + min(60.0, 0.5 * (2 ** min(attempts, 7))),
        }
        db.commit()
        return operation
    except Exception as exc:
        db.rollback()
        operation = db.get(FileOperation, operation_id)
        if _transient_file_lock(exc):
            attempts = previous_attempts + 1
            operation.status = "waiting_for_release"
            operation.error_json = {
                "code": "external_file_lock",
                "message": str(exc),
                "retryable": True,
                "attempts": attempts,
                "retry_after": time.time() + min(60.0, 0.5 * (2 ** min(attempts, 7))),
            }
            db.commit()
            return operation
        operation.status = "reconciliation_required"
        operation.error_json = {
            "code": "file_operation_interrupted",
            "message": str(exc),
            "retryable": True,
        }
        db.commit()
        raise


def _rollback_operation(db: Session, operation: FileOperation) -> bool:
    plan = copy.deepcopy(operation.plan_json)
    errors: list[str] = []
    for step in reversed(plan.get("steps", [])):
        source, destination, temporary = (
            step["source"], step["destination"], step["temporary"]
        )
        try:
            os.makedirs(os.path.dirname(source), exist_ok=True)
            if os.path.isfile(temporary) and not os.path.exists(source):
                shutil.move(temporary, source)
            elif os.path.isfile(destination) and not os.path.exists(source):
                shutil.move(destination, source)
            elif os.path.isfile(destination) and os.path.isfile(source):
                if _sha256(destination) == step["checksum"]:
                    os.remove(destination)
            step["state"] = "rolled_back"
        except OSError as exc:
            errors.append(f"{source}: {exc}")
    operation.plan_json = plan
    operation.completed_at = _utcnow()
    if errors:
        operation.status = "manual_attention"
        operation.error_json = {
            "code": "file_rollback_failed", "message": "; ".join(errors),
            "retryable": True,
        }
    else:
        operation.status = "rolled_back"
        operation.error_json = None
    db.commit()
    return not errors


def reconcile_file_operations(db: Session) -> dict[str, int]:
    """Roll back operations interrupted before their database transition."""
    recovered = 0
    unresolved = 0
    operations = db.query(FileOperation).filter(
        FileOperation.status.in_(RECOVERABLE_STATUSES)
    ).order_by(FileOperation.created_at.asc()).all()
    for operation in operations:
        if _rollback_operation(db, operation):
            recovered += 1
        else:
            unresolved += 1
    return {"recovered": recovered, "unresolved": unresolved}


def retry_waiting_file_operation(db: Session) -> bool:
    operation = db.query(FileOperation).filter(
        FileOperation.status == "waiting_for_release"
    ).order_by(FileOperation.created_at.asc()).first()
    if operation is None:
        return False
    retry_after = float((operation.error_json or {}).get("retry_after") or 0)
    if retry_after > time.time():
        return False
    execute_file_operation(db, operation.id)
    return True


def cancel_file_operation(db: Session, operation_id: str) -> FileOperation:
    operation = db.get(FileOperation, operation_id)
    if operation is None:
        raise LookupError(f"unknown file operation {operation_id}")
    if operation.status not in {"planned", "waiting_for_release", "reconciliation_required"}:
        raise FileOperationFailed(f"operation is {operation.status}, not cancellable")
    if operation.status == "planned":
        operation.status = "cancelled"
        operation.completed_at = _utcnow()
        db.commit()
    else:
        _rollback_operation(db, operation)
        operation.status = "cancelled"
        db.commit()
    db.refresh(operation)
    return operation
