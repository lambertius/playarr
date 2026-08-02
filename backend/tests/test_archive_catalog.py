"""Acceptance coverage for the SQL-indexed archive projection."""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.durability_models import ArchiveCatalogEntry
from app.services.archive_catalog import query_archive_catalog, sync_archive_catalog


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_archive_filters_and_pages_are_bounded_in_sql():
    db = _session()
    for index in range(125):
        db.add(ArchiveCatalogEntry(
            folder=f"C:/archive/{index}", path=f"C:/archive/{index}/video.mkv",
            reason="crop" if index % 2 else "edit", artist=f"Artist {index}",
            title="Needle" if index == 51 else "Track", archived_at=datetime(2026, 1, 1),
            integrity_status="ok", last_seen_at=datetime(2026, 1, 1),
        ))
    db.commit()

    page = query_archive_catalog(db, reason="crop", search=None, page=2, page_size=20)
    assert page["total"] == 62
    assert len(page["items"]) == 20
    assert page["total_pages"] == 4
    assert all(item["reason"] == "crop" for item in page["items"])
    searched = query_archive_catalog(db, reason=None, search="Needle", page=1, page_size=20)
    assert searched["total"] == 1


def test_catalog_sync_removes_missing_entries_without_deleting_archive_files(tmp_path, monkeypatch):
    db = _session()
    library = tmp_path / "library"
    archive = library / "_archive" / "Artist" / "Track"
    archive.mkdir(parents=True)
    video = archive / "video.mkv"
    video.write_bytes(b"fixture")
    (archive / ".playarr-archive.json").write_text(
        '{"schema_version":2,"playarr_video_id":"portable-1","archived_filename":"video.mkv","archive_reason":"crop","artist":"Artist","title":"Track"}',
        encoding="utf-8",
    )

    class Settings:
        def get_all_library_dirs(self):
            return [str(library)]

    monkeypatch.setattr("app.config.get_settings", lambda: Settings())
    assert sync_archive_catalog(db) == 1
    assert db.query(ArchiveCatalogEntry).one().reason == "crop"

    video.unlink()
    assert sync_archive_catalog(db) == 0
    assert db.query(ArchiveCatalogEntry).count() == 0
    assert archive.is_dir()
