"""Acceptance coverage for backend-owned queue classification and paging."""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models import JobStatus, ProcessingJob
from app.routers.jobs import clear_history, list_jobs_page, preview_clear_history
from app.routers.tools import ytdlp_update
from app.services.job_registry import job_category, status_group


def _session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_registry_does_not_misclassify_unknown_jobs_as_downloads():
    assert job_category("video_editor_encode") == "video_editor"
    assert job_category("metadata_refresh") == "scraper"
    assert job_category("future_maintenance_action") == "system"
    assert status_group(JobStatus.normalizing) == "active"
    assert status_group(JobStatus.cancelling) == "active"
    assert status_group(JobStatus.failed) == "failed"


def test_page_is_bounded_counted_and_server_filtered():
    db = _session()
    now = datetime.now(timezone.utc)
    for index in range(125):
        db.add(ProcessingJob(
            job_type="import_url" if index % 2 == 0 else "video_editor_encode",
            status=JobStatus.complete,
            display_name=f"Track {index}",
            created_at=now,
        ))
    db.commit()

    result = list_jobs_page(
        status_group="complete",
        job_category="video_editor",
        search=None,
        date_from=None,
        date_to=None,
        sort_by="date_added",
        sort_dir="desc",
        page=2,
        page_size=20,
        db=db,
    )

    assert len(result.items) == 20
    assert result.total == 62
    assert result.total_pages == 4
    assert result.status_counts["complete"] == 125
    assert result.category_counts["video_editor"] == 62
    assert all(item.job_category == "video_editor" for item in result.items)
    assert all(item.status_group == "complete" for item in result.items)


def test_history_preview_and_clear_never_include_active_jobs():
    db = _session()
    db.add_all([
        ProcessingJob(job_type="import_url", status=JobStatus.downloading),
        ProcessingJob(job_type="import_url", status=JobStatus.complete),
        ProcessingJob(job_type="import_url", status=JobStatus.failed),
    ])
    db.commit()

    preview = preview_clear_history(
        status_group=None,
        job_category="download",
        search=None,
        date_from=None,
        date_to=None,
        db=db,
    )
    assert preview["count"] == 2

    cleared = clear_history(
        status=None,
        job_type=None,
        status_group=None,
        job_category="download",
        search=None,
        date_from=None,
        date_to=None,
        db=db,
    )
    assert cleared["deleted"] == 2
    assert db.query(ProcessingJob).one().status == JobStatus.downloading

    try:
        preview_clear_history(
            status_group="active",
            job_category=None,
            search=None,
            date_from=None,
            date_to=None,
            db=db,
        )
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("active queue history preview should be rejected")


def test_ytdlp_update_is_accepted_as_a_job_before_dispatch(monkeypatch):
    db = _session()
    accepted = []

    def capture_dispatch(_task, **kwargs):
        job = db.get(ProcessingJob, kwargs["job_id"])
        assert job is not None
        assert job.status == JobStatus.queued
        accepted.append(job.id)

    monkeypatch.setattr("app.worker.dispatch_task", capture_dispatch)
    response = ytdlp_update(db)

    assert response["status"] == "queued"
    assert response["job_id"] == accepted[0]
    assert db.get(ProcessingJob, response["job_id"]).job_type == "ytdlp_update"
