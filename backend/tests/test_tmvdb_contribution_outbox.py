"""Acceptance coverage for eligibility-gated, durable TMVDB contributions."""
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models import AppSetting, ContributionLog, ContributionOutbox, ReviewCase, VideoItem
from app.provenance import build_eligible_contribution
from app.routers.tmvdb import _pull_candidates
from app.services.contribution_outbox import enqueue_contribution, process_next_contribution


def _factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _video(db):
    video = VideoItem(
        artist="Machine Artist",
        title="Human Title",
        album="Locked Album",
        plot="Generated summary",
        file_path="C:/library/artist-title.mkv",
        field_provenance={
            "artist": "openai",
            "title": "manual",
            "album": "musicbrainz",
            "plot": "gemini",
        },
        field_provenance_users={"title": "reviewer-1"},
        locked_fields=["album"],
    )
    db.add(video)
    db.commit()
    return video


def test_only_human_confirmed_or_locked_fields_are_eligible():
    db = _factory()()
    video = _video(db)

    result = build_eligible_contribution(video, "instance-1")

    assert result["eligible_fields"] == ["album", "title"]
    assert result["eligibility"]["artist"]["reason"] == "ai_unverified"
    assert result["eligibility"]["plot"]["reason"] == "ai_unverified"
    assert set(result["submission"]["fields"]) == {"album", "title"}
    assert result["can_submit"] is True


def test_enqueue_is_idempotent_and_does_not_contact_provider(monkeypatch):
    factory = _factory()
    db = factory()
    video = _video(db)
    contacted = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal contacted
        contacted = True
        raise AssertionError("network delivery must not occur during acceptance")

    monkeypatch.setattr("app.metadata.providers.tmvdb.TMVDBProvider.push_track", fail_if_called)
    first, _eligibility, created = enqueue_contribution(db, video, "instance-1")
    db.commit()
    second, _eligibility, created_again = enqueue_contribution(db, video, "instance-1")

    assert created is True
    assert created_again is False
    assert first.id == second.id
    assert contacted is False
    assert db.query(ContributionOutbox).count() == 1


def test_background_delivery_transitions_to_submitted(monkeypatch):
    factory = _factory()
    db = factory()
    video = _video(db)
    db.add_all([
        AppSetting(key="tmvdb_enabled", value="true", value_type="bool"),
        AppSetting(key="tmvdb_api_key", value="test-key", value_type="string"),
    ])
    row, _eligibility, _created = enqueue_contribution(db, video, "instance-1")
    row_id = row.id
    db.commit()
    db.close()

    monkeypatch.setattr(
        "app.metadata.providers.tmvdb.TMVDBProvider.push_track",
        lambda _provider, envelope: {"id": "remote-42", "field_count": len(envelope["fields"])},
    )

    assert process_next_contribution(factory) == row_id
    check = factory()
    delivered = check.get(ContributionOutbox, row_id)
    assert delivered.status == "submitted"
    assert delivered.remote_id == "remote-42"
    assert delivered.attempts == 1
    assert check.query(ContributionLog).filter_by(payload_hash=delivered.payload_hash).count() == 1


def test_pull_materializes_conflicts_without_overwriting_local_values():
    db = _factory()()
    video = _video(db)
    remote = SimpleNamespace(
        fields={"title": "Remote Title", "artist": "Remote Artist"},
        field_provenance={"title": "tmvdb", "artist": "tmvdb"},
        confidence=0.91,
    )

    result = _pull_candidates(video, remote, db)

    assert video.title == "Human Title"
    assert result["review_case_id"]
    assert all(candidate["conflict"] for candidate in result["candidates"])
    case = db.query(ReviewCase).filter_by(category="tmvdb_conflict").one()
    assert case.status == "open"
    assert case.evidence_json["conflicts"][0]["proposed"]
