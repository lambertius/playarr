"""Validation, consequence preview and recoverable execution for review plans."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.metadata.models import TrackEntity
from app.models import JobStatus, ProcessingJob, ReviewCase, VideoItem


SUPPORTED_ACTIONS = {
    "keep", "delete", "reclassify", "relink", "rescrape",
    "normalise", "dismiss", "no_change",
}
VERSION_TYPES = {
    "normal", "cover", "live", "alternate", "remix", "acoustic",
    "uncensored", "18+",
}


class ReviewPlanError(ValueError):
    def __init__(self, message: str, field: str):
        self.field = field
        super().__init__(message)


def _video_map(db: Session, case: ReviewCase) -> dict[str, VideoItem]:
    stable_ids = [item.video_stable_id for item in case.items]
    return {
        video.stable_id: video
        for video in db.query(VideoItem).filter(VideoItem.stable_id.in_(stable_ids)).all()
    }


def _companion_paths(video: VideoItem) -> list[str]:
    if not video.folder_path or not os.path.isdir(video.folder_path):
        return [video.file_path] if video.file_path and os.path.isfile(video.file_path) else []
    paths: list[str] = []
    for root, directories, files in os.walk(video.folder_path):
        directories.sort()
        for name in sorted(files):
            paths.append(os.path.join(root, name))
    return paths


def describe_plan(db: Session, case: ReviewCase, actions: list[dict]) -> dict[str, list]:
    videos = _video_map(db, case)
    consequences: dict[str, list] = {"metadata": [], "files": [], "relationships": [], "jobs": []}
    for index, action in enumerate(actions):
        action_type = action.get("type")
        field = f"actions.{index}"
        if action_type not in SUPPORTED_ACTIONS:
            raise ReviewPlanError(f"Unsupported review action {action_type!r}", f"{field}.type")
        target = action.get("video_stable_id")
        video = videos.get(target) if target else None
        if target and video is None:
            raise ReviewPlanError("Action targets a video outside this case", f"{field}.video_stable_id")
        if action_type in {"delete", "reclassify", "relink", "rescrape", "normalise"} and video is None:
            raise ReviewPlanError("Action requires a case video", f"{field}.video_stable_id")
        if action_type == "reclassify":
            version_type = action.get("version_type")
            if version_type not in VERSION_TYPES:
                raise ReviewPlanError("Invalid version type", f"{field}.version_type")
            consequences["metadata"].append({"video_stable_id": target, "field": "version_type", "old_value": video.version_type, "new_value": version_type})
        elif action_type == "delete":
            consequences["files"].append({"video_stable_id": target, "operation": "archive_then_delete", "paths": _companion_paths(video), "recoverable": True})
            consequences["relationships"].append({"video_stable_id": target, "operation": "remove_managed_video", "affected_review_cases": [case.stable_id]})
        elif action_type == "relink":
            track_id = action.get("canonical_track_id")
            if not isinstance(track_id, int) or db.get(TrackEntity, track_id) is None:
                raise ReviewPlanError("Canonical track does not exist", f"{field}.canonical_track_id")
            consequences["relationships"].append({"video_stable_id": target, "field": "canonical_track", "old_id": video.track_id, "new_id": track_id})
        elif action_type in {"rescrape", "normalise"}:
            consequences["jobs"].append({"video_stable_id": target, "job_type": "metadata_scrape" if action_type == "rescrape" else "normalize"})
        elif action_type in {"keep", "dismiss"}:
            consequences["relationships"].append({"case": case.stable_id, "result": "resolved" if action_type == "keep" else "dismissed"})
    return consequences


def _archive_delete(db: Session, video: VideoItem, command_id: str) -> str | None:
    paths = _companion_paths(video)
    if not paths:
        db.delete(video)
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    destination_root = Path(get_settings().archive_dir) / "review-delete" / video.stable_id / stamp
    source_root = Path(video.folder_path) if video.folder_path else Path(video.file_path).parent
    transitions = []
    for source in paths:
        try:
            relative = Path(source).relative_to(source_root)
        except ValueError:
            relative = Path(source).name
        transitions.append({"source": source, "destination": str(destination_root / relative)})
    main_destination = next((
        item["destination"] for item in transitions
        if video.file_path and os.path.normcase(item["source"]) == os.path.normcase(video.file_path)
    ), None)
    from app.services.file_operations import create_file_set_operation, execute_file_operation
    operation = create_file_set_operation(
        db, video, "delete", transitions,
        database_updates={"file_path": main_destination, "folder_path": str(destination_root)},
        metadata={"reason": "review_delete", "review_case_command": command_id},
        command_id=command_id,
    )
    execute_file_operation(db, operation.id)
    db.delete(video)
    return operation.id


def apply_plan(db: Session, case: ReviewCase, actions: list[dict], command_id: str) -> dict[str, list]:
    videos = _video_map(db, case)
    result: dict[str, list] = {"file_operation_ids": [], "job_ids": []}
    dispatches: list[dict] = []
    for action in actions:
        action_type = action.get("type")
        video = videos.get(action.get("video_stable_id"))
        if action_type == "delete" and video is not None:
            operation_id = _archive_delete(db, video, command_id)
            if operation_id:
                result["file_operation_ids"].append(operation_id)
        elif action_type == "reclassify" and video is not None:
            video.version_type = action["version_type"]
            video.revision += 1
        elif action_type == "relink" and video is not None:
            video.track_id = int(action["canonical_track_id"])
            video.revision += 1
        elif action_type in {"rescrape", "normalise"} and video is not None:
            job_type = "metadata_scrape" if action_type == "rescrape" else "normalize"
            job = ProcessingJob(
                video_id=video.id, job_type=job_type, status=JobStatus.queued,
                display_name=f"{video.artist} - {video.title}",
                action_label="Review rescrape" if action_type == "rescrape" else "Review normalise",
                operation_id=f"{command_id}:{len(result['job_ids'])}",
                input_params={"source": "review_case", "case_stable_id": case.stable_id},
            )
            db.add(job); db.flush()
            result["job_ids"].append(job.id)
            dispatches.append({"action_type": action_type, "job_id": job.id, "video_id": video.id})
    db.flush()
    result["dispatches"] = dispatches
    return result


def dispatch_plan_jobs(result: dict[str, list]) -> None:
    for descriptor in result.pop("dispatches", []):
        action_type = descriptor["action_type"]
        job_id = descriptor["job_id"]
        video_id = descriptor["video_id"]
        from app.worker import dispatch_task
        if action_type == "rescrape":
            from app.tasks import scrape_metadata_task
            dispatch_task(scrape_metadata_task, job_id=job_id, video_id=video_id)
        else:
            from app.tasks import normalize_task
            dispatch_task(normalize_task, job_id=job_id, video_id=video_id)
