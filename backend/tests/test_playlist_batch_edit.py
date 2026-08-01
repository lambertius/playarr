"""Acceptance tests for PL-002 optimistic atomic playlist edits."""
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - register the complete metadata
from app.database import Base
from app.models import Playlist, PlaylistEntry, VideoItem
from app.routers.playlists import PlaylistBatchEdit, batch_edit_entries


def _seed():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    playlist = Playlist(name="Draft test")
    db.add(playlist)
    db.flush()
    entries = []
    for index in range(4):
        video = VideoItem(artist=f"Artist {index}", title=f"Title {index}")
        db.add(video)
        db.flush()
        entry = PlaylistEntry(
            playlist_id=playlist.id,
            video_id=video.id,
            position=index,
        )
        db.add(entry)
        entries.append(entry)
    db.commit()
    return db, playlist, entries


def test_batch_edit_reorders_and_removes_in_one_revision():
    db, playlist, entries = _seed()
    response = batch_edit_entries(
        playlist.id,
        PlaylistBatchEdit(
            expected_revision=1,
            ordered_occurrence_ids=[
                entries[3].occurrence_id,
                entries[0].occurrence_id,
                entries[2].occurrence_id,
            ],
            removed_occurrence_ids=[entries[1].occurrence_id],
        ),
        db,
    )

    assert response.revision == 2
    assert [entry.occurrence_id for entry in response.entries] == [
        entries[3].occurrence_id,
        entries[0].occurrence_id,
        entries[2].occurrence_id,
    ]
    assert db.get(PlaylistEntry, entries[1].id) is None


def test_stale_browser_cannot_overwrite_newer_playlist():
    db, playlist, entries = _seed()
    playlist.revision = 3
    db.commit()

    with pytest.raises(HTTPException) as caught:
        batch_edit_entries(
            playlist.id,
            PlaylistBatchEdit(
                expected_revision=1,
                ordered_occurrence_ids=[entry.occurrence_id for entry in entries],
            ),
            db,
        )

    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "stale_revision"
    assert caught.value.detail["current"]["revision"] == 3


def test_incomplete_draft_is_rejected_without_partial_write():
    db, playlist, entries = _seed()

    with pytest.raises(HTTPException) as caught:
        batch_edit_entries(
            playlist.id,
            PlaylistBatchEdit(
                expected_revision=1,
                ordered_occurrence_ids=[entries[0].occurrence_id],
            ),
            db,
        )

    assert caught.value.status_code == 422
    db.expire_all()
    assert db.get(Playlist, playlist.id).revision == 1
    assert db.query(PlaylistEntry).count() == 4
