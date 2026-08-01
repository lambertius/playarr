"""FILE-001..004 acceptance tests for durable managed-file transitions."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.ai.models  # noqa: F401 - register optional thumbnail tables
import app.models  # noqa: F401
from app.database import Base
from app.models import FileOperation, MediaAsset, VideoItem
from app.routers.mutations import (
    RenameMutation,
    preview_video_rename,
    queue_video_rename,
)
from app.routers.operations import operation_status
from app.services import file_operations
from app.services.file_operations import (
    FilePlanCollision,
    cancel_file_operation,
    create_rename_operation,
    execute_file_operation,
    reconcile_file_operations,
)
from app.services.mutation_runtime import process_next_mutation


def _session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _video_tree(db, root: Path):
    old_folder = root / "Artist" / "Old Name [1080p]"
    old_folder.mkdir(parents=True)
    files = {
        "video": old_folder / "Old Name [1080p].mkv",
        "nfo": old_folder / "Old Name [1080p].nfo",
        "xml": old_folder / "Old Name [1080p].playarr.xml",
        "poster": old_folder / "Old Name [1080p]-poster.jpg",
        "cached": old_folder / "cache" / "frame-001.jpg",
    }
    files["cached"].parent.mkdir()
    for index, path in enumerate(files.values()):
        path.write_bytes(f"fixture-{index}-{path.name}".encode())
    video = VideoItem(
        stable_id="4ec8d753-c8d9-4ac4-8794-4ea4ae32e575",
        artist="Artist", title="New Name", revision=3,
        folder_path=str(old_folder), file_path=str(files["video"]),
    )
    db.add(video)
    db.flush()
    asset = MediaAsset(
        video_id=video.id, asset_type="poster", file_path=str(files["poster"]),
        provenance="test",
    )
    db.add(asset)
    db.commit()
    return video, asset, files


def test_plan_covers_all_companions_and_detects_collision(tmp_path, monkeypatch):
    db = _session()
    video, _, files = _video_tree(db, tmp_path)
    new_folder = tmp_path / "Artist" / "New Name [1080p]"
    monkeypatch.setattr(file_operations, "_stream_active", lambda _path: True)

    operation = create_rename_operation(
        db, video, str(new_folder), "New Name [1080p]",
    )
    plan = operation.plan_json

    assert plan["playarr_video_id"] == video.stable_id
    assert plan["active_stream_usage"] is True
    assert {step["role"] for step in plan["steps"]} >= {
        "video", "nfo", "playarr_sidecar", "artwork", "companion",
    }
    assert all(len(step["checksum"]) == 64 for step in plan["steps"])
    assert any(step["source"] == str(files["cached"]) for step in plan["steps"])

    collision = new_folder / "New Name [1080p].mkv"
    collision.parent.mkdir(parents=True)
    collision.write_bytes(b"unrelated")
    second = create_rename_operation(
        db, video, str(new_folder), "New Name [1080p]",
    )
    assert second.plan_json["collisions"][0]["destination"] == str(collision)
    with pytest.raises(FilePlanCollision):
        execute_file_operation(db, second.id)


def test_fault_at_each_step_is_journalled_and_startup_rolls_back(tmp_path):
    db = _session()
    video, _, files = _video_tree(db, tmp_path)
    new_folder = tmp_path / "Artist" / "New Name [1080p]"
    operation = create_rename_operation(
        db, video, str(new_folder), "New Name [1080p]",
    )

    def fail_after_second_move(point: str, index: int):
        if point == "after_move" and index == 1:
            raise RuntimeError("injected process stop")

    with pytest.raises(RuntimeError, match="injected process stop"):
        execute_file_operation(db, operation.id, fault=fail_after_second_move)

    db.expire_all()
    assert db.get(VideoItem, video.id).file_path == str(files["video"])
    assert db.get(FileOperation, operation.id).status == "reconciliation_required"
    result = reconcile_file_operations(db)
    assert result == {"recovered": 1, "unresolved": 0}
    assert all(path.is_file() for path in files.values())
    assert db.get(FileOperation, operation.id).status == "rolled_back"


def test_cross_volume_copy_is_verified_before_database_paths_change(tmp_path, monkeypatch):
    db = _session()
    video, asset, files = _video_tree(db, tmp_path)
    new_folder = tmp_path / "other-volume" / "New Name [1080p]"
    monkeypatch.setattr(file_operations, "_same_volume", lambda *_args: False)
    operation = create_rename_operation(
        db, video, str(new_folder), "New Name [1080p]",
    )

    result = execute_file_operation(db, operation.id)
    db.expire_all()
    moved_video = db.get(VideoItem, video.id)
    moved_asset = db.get(MediaAsset, asset.id)

    assert result.status == "succeeded"
    assert moved_video.revision == 4
    assert Path(moved_video.file_path).is_file()
    assert Path(moved_asset.file_path).is_file()
    assert not files["video"].exists()
    assert all(step["state"] == "moved" for step in result.plan_json["steps"])


def test_active_file_waits_without_blocking_and_can_be_cancelled(tmp_path, monkeypatch):
    db = _session()
    video, _, files = _video_tree(db, tmp_path)
    operation = create_rename_operation(
        db, video, str(tmp_path / "Artist" / "New Name [1080p]"),
        "New Name [1080p]",
    )
    monkeypatch.setattr(
        file_operations, "_ensure_released",
        lambda *_args: (_ for _ in ()).throw(file_operations.FileWaitingForRelease("busy")),
    )

    result = execute_file_operation(db, operation.id)
    assert result.status == "waiting_for_release"
    assert result.error_json["code"] == "waiting_for_release"
    assert files["video"].is_file()

    cancelled = cancel_file_operation(db, operation.id)
    assert cancelled.status == "cancelled"
    assert files["video"].is_file()


def test_rename_http_workflow_uses_preview_command_and_file_journal(tmp_path, monkeypatch):
    db = _session()
    video, _, _ = _video_tree(db, tmp_path)
    settings = SimpleNamespace(
        library_dir=str(tmp_path / "library"),
        library_naming_pattern="{artist} - {title} [{quality}]",
        library_folder_structure="{artist}/{file_folder}",
        mutation_queue_max=1000,
        deployment_profile="single_process",
    )
    monkeypatch.setattr("app.services.rename_operations.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.file_organizer.get_settings", lambda: settings)

    preview = preview_video_rename(video.id, db)
    accepted = queue_video_rename(
        video.id,
        RenameMutation(
            file_operation_id=preview["file_operation_id"],
            idempotency_key="rename-http-workflow",
        ),
        db,
    )
    assert accepted["status"] == "pending"

    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    assert process_next_mutation(factory) is True
    db.expire_all()
    status = operation_status(accepted["operation_id"], db)

    assert status["status"] == "succeeded"
    assert status["result"]["file_operation_id"] == preview["file_operation_id"]
    assert db.get(VideoItem, video.id).folder_path.startswith(settings.library_dir)
