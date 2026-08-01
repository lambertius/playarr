from types import SimpleNamespace

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
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    write_archive_manifest(
        str(archived), str(current), str(library), video_id=video.id,
        video_stable_id=video.stable_id, artist="Artist", title="Title",
    )

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
