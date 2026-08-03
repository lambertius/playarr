import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models import Genre, GenreConsolidation, VideoItem
from app.routers.metadata import (
    ArtistConsolidationCreate,
    ArtistConsolidationTargetInput,
    ArtistConsolidationUpdate,
    create_artist_consolidation_v2,
    update_artist_consolidation_v2,
    artist_consolidation_options,
    get_mbid_stats,
)
from app.routers.genre_consolidations import (
    GenreConsolidationCreate, GenreConsolidationMemberInput,
    GenreConsolidationUpdate, create_genre_consolidation_v2,
    update_genre_consolidation_v2,
    delete_genre_consolidation_v2,
)
from app.services.consolidations import (
    consolidation_conflicts, genre_display_map, library_consolidation_manifest,
    migrate_legacy_genre_consolidations,
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
    conflict = next(item for item in consolidation_conflicts(db) if item["type"] == "same_mbid_different_name")
    assert {target["raw_name"] for target in conflict["targets"]} == {"The Name", "The-Name"}


def test_saved_artist_consolidation_resolves_diagnostic_and_options_are_searchable(monkeypatch):
    db = _db()
    db.add_all([
        VideoItem(artist="BeyoncÃ©", title="One", mb_artist_id="mbid-a"),
        VideoItem(artist="Beyonce", title="Two", mb_artist_id="mbid-a"),
    ])
    db.commit()
    monkeypatch.setattr("app.services.consolidations.write_library_consolidation_manifest", lambda _db: None)
    assert len(consolidation_conflicts(db)) == 1
    assert get_mbid_stats(db).artist_conflicts == 1
    options = artist_consolidation_options("Bey", db)
    assert {option["name"] for option in options} == {"BeyoncÃ©", "Beyonce"}
    create_artist_consolidation_v2(ArtistConsolidationCreate(
        mask_name="BeyoncÃ©",
        targets=[
            ArtistConsolidationTargetInput(raw_name="BeyoncÃ©", mb_artist_id="mbid-a"),
            ArtistConsolidationTargetInput(raw_name="Beyonce", mb_artist_id="mbid-a"),
        ],
        mbids=["mbid-a"],
    ), db)
    assert consolidation_conflicts(db) == []
    assert get_mbid_stats(db).artist_conflicts == 0


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


def test_legacy_genre_masks_migrate_to_editable_v2_aggregates():
    db = _db()
    master = Genre(name="Alternative Rock")
    alias = Genre(name="Alt Rock")
    db.add_all([master, alias])
    db.flush()
    alias.master_genre_id = master.id
    db.commit()

    assert migrate_legacy_genre_consolidations(db) == 1
    aggregate = db.query(GenreConsolidation).one()
    assert aggregate.mask_name == "Alternative Rock"
    assert {member.raw_name for member in aggregate.members} == {"Alternative Rock", "Alt Rock"}
    assert db.get(Genre, alias.id).master_genre_id is None
    # A restart is safe and does not duplicate the migrated consolidation.
    assert migrate_legacy_genre_consolidations(db) == 0
    assert db.query(GenreConsolidation).count() == 1
