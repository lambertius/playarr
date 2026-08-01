"""SIDE-001/SIDE-004 recovery and portable-relationship acceptance."""
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import SidecarOutbox, VideoItem
from app.services.playarr_xml import parse_playarr_xml, write_playarr_xml
from app.services.sidecar_outbox import process_next_sidecar


def _sessions(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'sidecar.db').as_posix()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_committed_mutation_is_repaired_after_process_restart(tmp_path: Path):
    sessions = _sessions(tmp_path)
    folder = tmp_path / "Artist" / "Track"
    folder.mkdir(parents=True)
    media = folder / "Artist - Track.mp4"
    media.write_bytes(b"fixture-media")

    db = sessions()
    video = VideoItem(
        artist="Artist", title="Track", folder_path=str(folder), file_path=str(media),
        playarr_video_id="PVD-primary000", playarr_track_id="PTR-track00000",
    )
    related = VideoItem(
        artist="Artist", title="Track (Live)", version_type="live",
        folder_path=str(folder), file_path=str(media),
        playarr_video_id="PVD-related00", playarr_track_id="PTR-related00",
    )
    db.add_all([video, related])
    db.flush()
    video.dismissed_duplicate_ids = [related.id]
    video.related_versions = [{"video_id": related.id, "label": "live"}]

    planned_path = write_playarr_xml(video, db, operation_id="op-sidecar")
    assert planned_path and not Path(planned_path).exists()
    db.commit()
    outbox_id = db.query(SidecarOutbox.id).one()[0]
    db.close()

    # A new session/actor simulates startup after the DB commit but before I/O.
    assert process_next_sidecar(sessions) is True
    check = sessions()
    outbox = check.get(SidecarOutbox, outbox_id)
    assert outbox.status == "complete"
    assert Path(outbox.target_path).is_file()
    payload = Path(outbox.target_path).read_text("utf-8")
    assert "dismissed_duplicate_ids" not in payload
    assert f">{related.id}<" not in payload
    assert "PVD-related00" in payload
    parsed = parse_playarr_xml(outbox.target_path)
    assert parsed["dismissed_duplicate_refs"] == ["PVD-related00"]
    assert parsed["related_versions"] == [{
        "video_ref": "PVD-related00", "legacy_video_id": None, "label": "live",
    }]
    check.close()


def test_uncommitted_outbox_never_materialises_sidecar(tmp_path: Path):
    sessions = _sessions(tmp_path)
    folder = tmp_path / "rollback"
    folder.mkdir()
    media = folder / "track.mp4"
    media.write_bytes(b"fixture-media")
    db = sessions()
    video = VideoItem(artist="A", title="T", folder_path=str(folder), file_path=str(media))
    db.add(video)
    db.flush()
    planned_path = write_playarr_xml(video, db)
    db.rollback()
    db.close()

    assert process_next_sidecar(sessions) is False
    assert planned_path and not Path(planned_path).exists()
