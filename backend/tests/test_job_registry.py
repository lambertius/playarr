"""Queue taxonomy is a backend contract, not duplicated client heuristics."""
from app.models import JobStatus
from app.services.job_registry import job_category, known_categories, status_group


def test_requested_queue_source_taxonomy():
    assert job_category("import_url") == "download"
    assert job_category("playlist_import") == "download"
    assert job_category("redownload") == "download"
    assert job_category("library_import") == "import"
    assert job_category("library_import_video") == "import"
    assert job_category("video_editor_scan") == "video_editor"
    assert job_category("video_editor_encode") == "video_editor"
    assert job_category("metadata_scrape") == "scraper"
    assert job_category("metadata_refresh") == "scraper"
    assert job_category("rescan") == "scraper"
    # Processing steps without a scraper origin remain visible under All rather
    # than being misleadingly labelled as scraper work.
    assert job_category("normalize") == "system"
    assert job_category("new_videos_refresh") == "system"
    assert set(known_categories()) == {
        "download", "import", "video_editor", "scraper", "system",
    }


def test_requested_queue_status_taxonomy():
    assert status_group(JobStatus.queued) == "active"
    assert status_group(JobStatus.downloading) == "active"
    assert status_group(JobStatus.complete) == "complete"
    assert status_group(JobStatus.failed) == "failed"
    assert status_group(JobStatus.cancelled) == "cancelled"
    assert status_group(JobStatus.skipped) == "skipped"
