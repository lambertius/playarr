from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models import Genre, VideoItem
from app.routers.genre_consolidations import (
    GenreConsolidationCreate,
    GenreConsolidationMemberInput,
    create_genre_consolidation_v2,
)
from app.routers.settings import list_genre_blacklist, update_genre_blacklist, GenreBlacklistUpdate


def _db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_manager_shows_only_populated_masks_and_counts_distinct_videos(monkeypatch):
    db = _db()
    alt_rock = Genre(name="Alt Rock")
    alternative = Genre(name="Alternative Rock Music")
    dance = Genre(name="Dance")
    unused = Genre(name="1990s")
    first = VideoItem(artist="Artist", title="First", genres=[alt_rock, alternative])
    second = VideoItem(artist="Artist", title="Second", genres=[alt_rock])
    third = VideoItem(artist="Artist", title="Third", genres=[dance])
    db.add_all([first, second, third, unused])
    db.commit()
    monkeypatch.setattr(
        "app.routers.genre_consolidations.write_library_consolidation_manifest",
        lambda _db: None,
    )
    create_genre_consolidation_v2(GenreConsolidationCreate(
        mask_name="Alternative Rock",
        target_genres=[
            GenreConsolidationMemberInput(raw_name="Alt Rock"),
            GenreConsolidationMemberInput(raw_name="Alternative Rock Music"),
        ],
    ), db)

    rows = list_genre_blacklist(db)

    assert [row.name for row in rows] == ["Alternative Rock", "Dance"]
    masked = rows[0]
    assert masked.video_count == 2  # the shared first video is not double-counted
    assert masked.alias_count == 1
    assert masked.genre_ids == sorted([alt_rock.id, alternative.id])
    assert all(row.name != "1990s" for row in rows)

    result = update_genre_blacklist(
        GenreBlacklistUpdate(genre_ids=masked.genre_ids, blacklisted=True), db,
    )
    assert result["updated"] == 2
    assert next(row for row in list_genre_blacklist(db) if row.name == "Alternative Rock").blacklisted
