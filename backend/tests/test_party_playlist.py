"""
Party Mode playlist override — the server /party-mode endpoint must honour the
`partyPlaylist` preference so the chosen playlist plays everywhere Party Mode
runs (web, TV, Cast, and — the reason for this — the Kodi add-on, which reaches
Party Mode only through this endpoint).
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.routers.library as library  # registers the models it queries
import app.models  # noqa: F401 — ensure all tables (incl. metadata) are registered
from app.database import Base
from app.models import VideoItem, QualitySignature, Playlist, PlaylistEntry, AppSetting

# All of party_mode's query params passed explicitly as None so the function can
# be called directly (the FastAPI Query(None) defaults are Query objects, not None).
NULLS = dict(
    search=None, artist=None, album=None, genre=None, year=None, year_from=None,
    year_to=None, version_type=None, enrichment=None, song_rating=None,
    video_rating=None, exclude_version_types=None, exclude_artists=None,
    exclude_genres=None, exclude_albums=None, min_song_rating=None,
    min_video_rating=None, party_year=None,
)


def _session():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _seed(db):
    vids = []
    for i in range(1, 4):
        v = VideoItem(artist="Artist%d" % i, title="Title%d" % i)
        db.add(v)
        db.flush()
        db.add(QualitySignature(video_id=v.id, duration_seconds=180 + i))
        vids.append(v)
    db.commit()
    return vids


def _set_party_playlist(db, playlist_id):
    db.add(AppSetting(key="pref.partyPlaylist", value=json.dumps({"playlistId": playlist_id})))
    db.commit()


def test_playlist_override_plays_only_playlist_videos():
    db = _session()
    v1, v2, v3 = _seed(db)
    pl = Playlist(name="My Party")
    db.add(pl)
    db.flush()
    db.add(PlaylistEntry(playlist_id=pl.id, video_id=v1.id, position=0))
    db.add(PlaylistEntry(playlist_id=pl.id, video_id=v2.id, position=1))
    db.commit()
    _set_party_playlist(db, pl.id)

    result = library.party_mode(db=db, **NULLS)

    ids = sorted(t["videoId"] for t in result["tracks"])
    assert ids == sorted([v1.id, v2.id]), f"expected only playlist videos, got {ids}"
    assert result["total"] == 2
    # Response shape must match the normal party-mode response the clients expect.
    t0 = result["tracks"][0]
    assert {"videoId", "artist", "title", "hasPoster", "playCount", "duration"} <= set(t0)


def test_no_playlist_pref_falls_through_to_full_library():
    db = _session()
    v1, v2, v3 = _seed(db)
    # No partyPlaylist preference set.
    result = library.party_mode(db=db, **NULLS)
    ids = sorted(t["videoId"] for t in result["tracks"])
    assert ids == sorted([v1.id, v2.id, v3.id])


def test_empty_playlist_falls_through_to_full_library():
    db = _session()
    v1, v2, v3 = _seed(db)
    pl = Playlist(name="Empty")
    db.add(pl)
    db.flush()
    db.commit()
    _set_party_playlist(db, pl.id)  # playlist exists but has no entries

    result = library.party_mode(db=db, **NULLS)
    ids = sorted(t["videoId"] for t in result["tracks"])
    assert ids == sorted([v1.id, v2.id, v3.id]), "empty playlist should fall through"


def test_null_playlist_id_falls_through():
    db = _session()
    v1, v2, v3 = _seed(db)
    db.add(AppSetting(key="pref.partyPlaylist", value=json.dumps({"playlistId": None})))
    db.commit()
    result = library.party_mode(db=db, **NULLS)
    ids = sorted(t["videoId"] for t in result["tracks"])
    assert ids == sorted([v1.id, v2.id, v3.id])
