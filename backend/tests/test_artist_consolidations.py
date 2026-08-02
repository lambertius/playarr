import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models import VideoItem
from app.routers.metadata import (
    ArtistConsolidationCreate,
    ArtistConsolidationTargetInput,
    ArtistConsolidationUpdate,
    create_artist_consolidation_v2,
    update_artist_consolidation_v2,
)
from app.routers.genre_consolidations import (
    GenreConsolidationCreate, GenreConsolidationMemberInput,
    GenreConsolidationUpdate, create_genre_consolidation_v2,
    update_genre_consolidation_v2,
    delete_genre_consolidation_v2,
)
from app.services.consolidations import (
    consolidation_conflicts, genre_display_map, library_consolidation_manifest,
)


def _db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_artist_aggregate_can_start_without_mbid_then_attach_multiple(monkeypatch):
    db = _db()
    video = VideoItem(artist="P!nk", title="Try", mb_artist_id=None)
    db.add(video)
    db.commit()
    monkeypatch.setattr("app.services.consolidations.write_library_consolidation_manifest", lambda _db: None)

    created = create_artist_consolidation_v2(
        ArtistConsolidationCreate(
            mask_name="Pink",
            targets=[ArtistConsolidationTargetInput(raw_name="P!nk", provenance="source_tag")],
        ),
        db,
    )
    assert created["mbids"] == []

    updated = update_artist_consolidation_v2(
        created["stable_id"],
        ArtistConsolidationUpdate(
            expected_revision=created["revision"],
            mask_name="Pink",
            targets=[
                ArtistConsolidationTargetInput(
                    raw_name="P!nk", provenance="source_tag",
                    mb_artist_id="11111111-1111-1111-1111-111111111111",
                ),
            ],
            mbids=["22222222-2222-2222-2222-222222222222"],
        ),
        db,
    )
    assert updated["mbids"] == [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    ]
    # Consolidation masks display; it does not destroy the source value.
    assert db.get(VideoItem, video.id).artist == "P!nk"
    assert library_consolidation_manifest(db)["artist_consolidations"][0]["targets"][0]["raw_name"] == "P!nk"


def test_artist_update_has_optimistic_revision(monkeypatch):
    db = _db()
    monkeypatch.setattr("app.services.consolidations.write_library_consolidation_manifest", lambda _db: None)
    created = create_artist_consolidation_v2(ArtistConsolidationCreate(mask_name="Mask"), db)
    with pytest.raises(HTTPException) as caught:
        update_artist_consolidation_v2(
            created["stable_id"],
            ArtistConsolidationUpdate(expected_revision=0, mask_name="Stale"),
            db,
        )
    assert caught.value.status_code == 409


def test_conflict_service_covers_same_mbid_and_multi_mbid_identity():
    db = _db()
    db.add_all([
        VideoItem(artist="The Name", title="One", mb_artist_id="mbid-a"),
        VideoItem(artist="The-Name", title="Two", mb_artist_id="mbid-a"),
        VideoItem(artist="The Name", title="Three", mb_artist_id="mbid-b"),
    ])
    db.commit()
    kinds = {item["type"] for item in consolidation_conflicts(db)}
    assert "same_mbid_different_name" in kinds
    assert "multiple_mbid_identity" in kinds


def test_genre_aggregate_masks_without_rewriting_source_tags(monkeypatch):
    db = _db()
    monkeypatch.setattr("app.routers.genre_consolidations.write_library_consolidation_manifest", lambda _db: None)
    created = create_genre_consolidation_v2(GenreConsolidationCreate(
        mask_name="Hip Hop",
        target_genres=[
            GenreConsolidationMemberInput(raw_name="Hip-Hop", provenance_json={"provider": "nfo"}),
            GenreConsolidationMemberInput(raw_name="hiphop", provenance_json={"provider": "tmvdb"}),
        ],
    ), db)
    assert genre_display_map(db)["Hip-Hop"] == "Hip Hop"
    assert created["target_genres"][0]["provenance_json"]["provider"] == "nfo"

    updated = update_genre_consolidation_v2(
        created["stable_id"], GenreConsolidationUpdate(
            expected_revision=created["revision"], mask_name="Hip Hop / Rap",
            target_genres=[GenreConsolidationMemberInput(raw_name="Hip-Hop")],
        ), db,
    )
    assert updated["revision"] == created["revision"] + 1
    assert library_consolidation_manifest(db)["genre_consolidations"][0]["stable_id"] == created["stable_id"]
    delete_genre_consolidation_v2(created["stable_id"], updated["revision"], db)
    assert "Hip-Hop" not in genre_display_map(db)
