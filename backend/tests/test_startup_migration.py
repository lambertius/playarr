from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Playlist, PlaylistEntry, VideoItem
from app.services.migration_audit import post_migration_reconciliation

from app.services.startup_migration import (
    MigrationBlockedError,
    MigrationFailedError,
    collect_raw_preflight,
    run_startup_migration,
)


def _legacy_engine(tmp_path):
    path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE video_items (id INTEGER PRIMARY KEY, stable_id VARCHAR(36), "
            "file_path VARCHAR(500), folder_path VARCHAR(500))"
        ))
        connection.execute(text(
            "INSERT INTO video_items (id, stable_id) VALUES (1, 'stable-one')"
        ))
    return engine, path


def test_successful_startup_creates_backup_and_report(tmp_path):
    engine, path = _legacy_engine(tmp_path)
    report_path = tmp_path / "migration-status.json"

    def add_revision():
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE video_items ADD COLUMN revision INTEGER DEFAULT 1"
            ))

    report = run_startup_migration(
        engine,
        create_schema=lambda: None,
        apply_upgrades=add_revision,
        stamp_version=lambda: None,
        backup_dir=tmp_path / "backups",
        report_path=report_path,
        reconcile=lambda _session: {"status": "complete", "discrepancies": []},
    )

    assert report["status"] == "complete"
    assert path.is_file()
    assert report["database_backup_path"]
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == "complete"


def test_failed_upgrade_restores_original_database(tmp_path):
    engine, path = _legacy_engine(tmp_path)

    def fail_after_write():
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO video_items (id, stable_id) VALUES (2, 'new')"))
        raise RuntimeError("injected migration fault")

    with pytest.raises(MigrationFailedError) as caught:
        run_startup_migration(
            engine,
            create_schema=lambda: None,
            apply_upgrades=fail_after_write,
            stamp_version=lambda: None,
            backup_dir=tmp_path / "backups",
            report_path=tmp_path / "migration-status.json",
        )

    assert caught.value.report["status"] == "failed_restored"
    assert caught.value.report["restored_from_backup"] is True
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM video_items").fetchone()[0] == 1


def test_duplicate_stable_ids_block_before_schema_changes(tmp_path):
    engine, _path = _legacy_engine(tmp_path)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO video_items (id, stable_id) VALUES (2, 'stable-one')"))
    called = []

    with pytest.raises(MigrationBlockedError) as caught:
        run_startup_migration(
            engine,
            create_schema=lambda: called.append("schema"),
            apply_upgrades=lambda: called.append("upgrade"),
            stamp_version=lambda: called.append("stamp"),
            backup_dir=tmp_path / "backups",
        )

    assert called == []
    assert "duplicate_stable_ids" in caught.value.report["preflight"]["critical_failures"]
    assert collect_raw_preflight(engine)["duplicate_stable_ids"] == ["stable-one"]


def test_reconciliation_reports_exact_file_sidecar_playlist_and_archive_repairs(tmp_path, monkeypatch):
    library = tmp_path / "library"
    folder = library / "Artist" / "Track"
    archive = library / "_archive" / "orphan"
    folder.mkdir(parents=True)
    archive.mkdir(parents=True)
    media = folder / "Track.mp4"
    media.write_bytes(b"changed-media")
    (folder / "Track.playarr.xml").write_text(
        '<playarr version="2"><portable_identity videoId="PVD-other" />'
        '<identity><artist>A</artist><title>T</title></identity></playarr>',
        encoding="utf-8",
    )
    (archive / ".playarr-archive.json").write_text(json.dumps({
        "schema_version": 2, "playarr_video_id": "PVD-orphan",
        "operation_id": "op-orphan", "archived_filename": "missing.mp4",
    }), encoding="utf-8")
    monkeypatch.setattr(
        "app.services.migration_audit.get_settings",
        lambda: SimpleNamespace(
            database_url="sqlite://", get_all_library_dirs=lambda: [str(library)],
        ),
    )
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        video = VideoItem(
            artist="A", title="T", folder_path=str(folder), file_path=str(media),
            playarr_video_id="PVD-primary", file_checksum="0" * 64,
        )
        playlist = Playlist(name="Gapped")
        db.add_all([video, playlist])
        db.flush()
        playlist.entries.append(PlaylistEntry(video_id=video.id, position=2))
        db.commit()

        report = post_migration_reconciliation(db)

    kinds = {item["type"] for item in report["discrepancies"]}
    assert report["status"] == "discrepancies"
    assert {
        "file_checksum_mismatch", "sidecar_identity_mismatch",
        "playlist_position_gap", "orphan_archive_link", "missing_archive_file",
    } <= kinds
    assert "review_changed_media" in report["retry_actions"]
    assert "relink_archive_by_identity" in report["retry_actions"]
