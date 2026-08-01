from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import VideoItem
from app.routers.library import update_video
from app.schemas import VideoItemUpdate


def test_video_updates_reject_stale_browser_state(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'revision.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    video = VideoItem(artist="Artist", title="Title", revision=1)
    db.add(video)
    db.commit()

    updated = update_video(
        video.id,
        VideoItemUpdate(expected_revision=1, song_rating=4, song_rating_set=True),
        db,
    )
    assert updated.revision == 2
    assert updated.song_rating == 4

    try:
        update_video(
            video.id,
            VideoItemUpdate(expected_revision=1, song_rating=1, song_rating_set=True),
            db,
        )
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail["code"] == "stale_revision"
        assert exc.detail["current_revision"] == 2
        assert exc.detail["current"]["song_rating"] == 4
    else:
        raise AssertionError("stale video mutation was accepted")

    assert db.get(VideoItem, video.id).song_rating == 4
