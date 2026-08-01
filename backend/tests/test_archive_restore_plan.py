from types import SimpleNamespace
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models import FileOperation, JobStatus, ProcessingJob, VideoItem
from app.routers.settings import (
    ArchiveRestorePreviewRequest,
    RestoreArchiveRequest,
    archive_integrity_report,
    preview_archive_restore,
    restore_archive_item,
)
from app.routers.video_editor import write_archive_manifest
from app.services.request_context import reset_request_id, set_request_id


def _session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_restore_requires_persisted_preview_and_reports_integrity(tmp_path, monkeypatch):
    library = tmp_path / "library"
    current_dir = library / "Artist" / "Artist - Title"
    archive_dir = library / "_archive" / "Artist" / "Artist - Title"
    current_dir.mkdir(parents=True)
    archive_dir.mkdir(parents=True)
    current = current_dir / "Artist - Title.mkv"
    archived = archive_dir / "Artist - Title.mkv"
    current.write_bytes(b"new-current-file")
    archived.write_bytes(b"original-archive-file")

    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(get_all_library_dirs=lambda: [str(library)]),
    )
    db = _session()
    video = VideoItem(
        artist="Artist", title="Title", file_path=str(current), folder_path=str(current_dir),
        playarr_video_id="PVD-archive001",
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    write_archive_manifest(
        str(archived), str(current), str(library), video_id=video.id,
        playarr_video_id=video.playarr_video_id, operation_id="archive-operation-1",
        artist="Artist", title="Title",
    )
    manifest = json.loads((archive_dir / ".playarr-archive.json").read_text())
    assert manifest["playarr_video_id"] == video.playarr_video_id
    assert manifest["operation_id"] == "archive-operation-1"
    assert len(manifest["checksum_sha256"]) == 64
    assert "video_stable_id" not in manifest

    preview = preview_archive_restore(
        ArchiveRestorePreviewRequest(folder=str(archive_dir)), db,
    )
    assert preview["restore_eligible"] is True
    assert preview["current_exists"] is True
    assert preview["checksum_matches_manifest"] is True
    assert preview["conflict_choices"] == ["archive_current", "replace_current"]
    operation = db.get(FileOperation, preview["operation_id"])
    assert operation is not None and operation.status == "planned"

    with pytest.raises(HTTPException) as exc:
        restore_archive_item(RestoreArchiveRequest(folder=str(archive_dir)), db)
    assert exc.value.status_code == 409

    integrity = archive_integrity_report(db)
    assert integrity["checked"] == 1
    assert integrity["ok"] == 1
    assert integrity["deleted"] == 0


def test_archive_resolves_rebuilt_database_by_stable_id_not_reused_row_id(
    tmp_path, monkeypatch,
):
    library = tmp_path / "library"
    archive_dir = library / "_archive" / "Artist" / "Title"
    archive_dir.mkdir(parents=True)
    archived = archive_dir / "Title.mkv"
    archived.write_bytes(b"portable-original")
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(get_all_library_dirs=lambda: [str(library)]),
    )
    db = _session()
    original = VideoItem(
        stable_id="stable-portable-video", artist="Artist", title="Title",
        playarr_video_id="PVD-portable01",
        file_path=str(library / "old.mkv"), folder_path=str(library),
    )
    db.add(original)
    db.commit()
    original_numeric_id = original.id
    write_archive_manifest(
        str(archived), str(library / "old.mkv"), str(library),
        video_id=original.id, playarr_video_id=original.playarr_video_id,
        operation_id="portable-archive-operation",
    )

    db.delete(original)
    db.commit()
    unrelated = VideoItem(
        stable_id="unrelated", artist="Wrong", title="Video",
        file_path=str(library / "wrong.mkv"), folder_path=str(library),
    )
    rebuilt = VideoItem(
        stable_id="stable-portable-video", artist="Artist", title="Title",
        playarr_video_id="PVD-portable01",
        file_path=str(library / "rebuilt.mkv"), folder_path=str(library),
    )
    db.add_all([unrelated, rebuilt])
    db.commit()
    assert unrelated.id == original_numeric_id
    assert rebuilt.id != original_numeric_id

    preview = preview_archive_restore(
        ArchiveRestorePreviewRequest(folder=str(archive_dir)), db,
    )
    assert preview["video_id"] == rebuilt.id
    assert preview["playarr_video_id"] == rebuilt.playarr_video_id
    assert preview["archive_operation_id"] == "portable-archive-operation"


def test_processing_job_inherits_request_and_gets_operation_id():
    db = _session()
    token = set_request_id("req_test_123")
    try:
        job = ProcessingJob(job_type="test", status=JobStatus.queued)
        db.add(job)
        db.commit()
        db.refresh(job)
    finally:
        reset_request_id(token)

    assert job.request_id == "req_test_123"
    assert job.operation_id.startswith("op_")
