from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import VideoItem
from app.routers.library import list_videos


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _list(db, page_size: int, page: int = 1):
    return list_videos(
        page=page,
        page_size=page_size,
        search=None,
        artist=None,
        album=None,
        album_entity_id=None,
        genre=None,
        year=None,
        year_from=None,
        year_to=None,
        version_type=None,
        review_status=None,
        enrichment=None,
        import_method=None,
        song_rating=None,
        video_rating=None,
        quality=None,
        sort_by="artist",
        sort_dir="asc",
        db=db,
    )


def test_zero_page_size_returns_the_whole_library():
    db = _db()
    db.add_all([
        VideoItem(artist="Charlie", title="Third"),
        VideoItem(artist="Alpha", title="First"),
        VideoItem(artist="Bravo", title="Second"),
    ])
    db.commit()

    response = _list(db, page_size=0, page=8)

    assert response.page == 1
    assert response.page_size == 0
    assert response.total == 3
    assert response.total_pages == 1
    assert [item.artist for item in response.items] == ["Alpha", "Bravo", "Charlie"]


def test_positive_page_size_still_paginates_the_library():
    db = _db()
    db.add_all([
        VideoItem(artist="Charlie", title="Third"),
        VideoItem(artist="Alpha", title="First"),
        VideoItem(artist="Bravo", title="Second"),
    ])
    db.commit()

    response = _list(db, page_size=2)

    assert response.page_size == 2
    assert response.total == 3
    assert response.total_pages == 2
    assert [item.artist for item in response.items] == ["Alpha", "Bravo"]
