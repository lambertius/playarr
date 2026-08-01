from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models import VideoItem
from app.routers.video_editor import (
    EditorQueueAddRequest,
    EditorQueueRemoveRequest,
    EditorQueueSettingsPatch,
    add_editor_queue_entries,
    get_editor_queue_state,
    patch_editor_queue_settings,
    remove_editor_queue_entries,
)


def _seed():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    videos = [VideoItem(artist="Artist", title=f"Track {index}") for index in range(3)]
    db.add_all(videos)
    db.commit()
    return db, videos


def test_editor_queue_is_durable_idempotent_and_settings_are_merged():
    db, videos = _seed()
    state = add_editor_queue_entries(
        EditorQueueAddRequest(video_ids=[videos[0].id, videos[1].id], source="scan"), db,
    )
    occurrence = state["entries"][0]["occurrence_id"]

    state = add_editor_queue_entries(
        EditorQueueAddRequest(video_ids=[videos[0].id], source="manual"), db,
    )
    assert len(state["entries"]) == 2
    assert state["entries"][0]["occurrence_id"] == occurrence
    assert state["entries"][0]["source"] == "manual"

    patch_editor_queue_settings(
        videos[0].id, EditorQueueSettingsPatch(patch={"profile": "source_fidelity"}), db,
    )
    state = patch_editor_queue_settings(
        videos[0].id, EditorQueueSettingsPatch(patch={"crf": 18}), db,
    )
    assert state["entries"][0]["settings"] == {"profile": "source_fidelity", "crf": 18}

    # A fresh read/session sees the same server records; no browser cache is involved.
    assert get_editor_queue_state(db)["entries"][0]["occurrence_id"] == occurrence


def test_remove_is_scoped_to_requested_entries():
    db, videos = _seed()
    add_editor_queue_entries(EditorQueueAddRequest(video_ids=[v.id for v in videos]), db)
    state = remove_editor_queue_entries(EditorQueueRemoveRequest(video_ids=[videos[1].id]), db)
    assert [entry["video_id"] for entry in state["entries"]] == [videos[0].id, videos[2].id]
