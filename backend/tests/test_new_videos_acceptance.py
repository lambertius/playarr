"""Acceptance coverage for the reviewer-visible New Videos requirements."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - register shared tables
import app.new_videos.models  # noqa: F401 - register recommendation tables
from app.database import Base
from app.models import JobStatus, MutationCommand, ProcessingJob
from app.new_videos.models import (
    RecommendationSnapshot,
    SuggestedVideo,
    SuggestedVideoDismissal,
)
from app.new_videos.recommendation_ranker import RecommendationCandidate
from app.new_videos.recommendation_service import _diversity_rerank, _serialize_video
from app.new_videos.router import CartAddRequest, add_video
from app.new_videos.failed_additions import list_failed_additions, restore_failed_suggestion
from app.services.mutation_runtime import process_next_mutation


def _session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _candidate(artist: str, index: int):
    return (
        RecommendationCandidate(
            artist=artist,
            title=f"Title {index}",
            provider_video_id=f"video-{index}",
        ),
        1.0 - (index / 100),
    )


def test_diversity_cap_and_adjacency_when_pool_permits():
    ranked = [_candidate("Same Artist", index) for index in range(5)]
    ranked += [_candidate(f"Artist {index}", index + 10) for index in range(5)]

    visible = _diversity_rerank(ranked, limit=5)[:5]
    artists = [item[0].artist for item in visible]

    assert artists.count("Same Artist") <= 1
    assert all(left != right for left, right in zip(artists, artists[1:]))
    assert len(_diversity_rerank(ranked, limit=5)) == len(ranked)


def test_diversity_relaxes_cap_but_still_avoids_adjacency():
    ranked = [
        _candidate("A", 1),
        _candidate("A", 2),
        _candidate("A", 3),
        _candidate("B", 4),
        _candidate("B", 5),
    ]

    artists = [item[0].artist for item in _diversity_rerank(ranked, limit=5)[:5]]
    assert artists == ["A", "B", "A", "B", "A"]


def test_incomplete_provider_metadata_is_explicit_in_api_response():
    video = SuggestedVideo(
        id=1,
        provider="youtube",
        provider_video_id="incomplete",
        url="https://example.invalid/watch/incomplete",
        title="Known title",
        artist=None,
        channel=None,
        thumbnail_url=None,
        duration_seconds=None,
        category="new",
        metadata_json={
            "completeness_score": 0.333,
            "missing_fields": ["artist", "thumbnail_url", "channel", "duration_seconds"],
            "provider_errors": ["provider timed out"],
        },
    )

    payload = _serialize_video(video)
    assert payload["completeness_score"] == 0.333
    assert payload["missing_fields"] == [
        "artist", "thumbnail_url", "channel", "duration_seconds",
    ]
    assert payload["provider_errors"] == ["provider timed out"]


def test_quick_add_acknowledges_before_actor_commits_and_dispatches(monkeypatch):
    db = _session()
    current = SuggestedVideo(
        provider="youtube",
        provider_video_id="current",
        url="https://example.invalid/current",
        title="Current",
        artist="Artist A",
        category="new",
        recommendation_score=1.0,
    )
    replacement = SuggestedVideo(
        provider="youtube",
        provider_video_id="replacement",
        url="https://example.invalid/replacement",
        title="Replacement",
        artist="Artist B",
        category="new",
        recommendation_score=0.9,
    )
    db.add_all([current, replacement])
    db.flush()
    db.add(RecommendationSnapshot(category="new", payload_json=[current.id]))
    db.commit()

    dispatched = []

    def capture_dispatch(_task, **kwargs):
        # The externally visible work must exist before execution is accepted.
        assert db.get(ProcessingJob, kwargs["job_id"]) is not None
        assert db.query(SuggestedVideoDismissal).filter_by(
            suggested_video_id=current.id,
        ).one_or_none() is not None
        dispatched.append(kwargs)

    monkeypatch.setattr("app.worker.dispatch_task", capture_dispatch)
    request = CartAddRequest(
        suggested_video_id=current.id,
        idempotency_key="new-videos-add-current",
    )
    response = add_video(request, db)
    duplicate = add_video(request, db)

    assert response["status"] == "pending"
    assert duplicate["operation_id"] == response["operation_id"]
    assert db.query(MutationCommand).count() == 1
    assert db.query(ProcessingJob).count() == 0

    session_factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    assert process_next_mutation(session_factory) is True
    db.expire_all()

    command = db.get(MutationCommand, response["operation_id"])
    assert command.status == "succeeded"
    assert command.result_json["status"] == "importing"
    assert command.result_json["replacement"]["provider_video_id"] == "replacement"
    assert dispatched[0]["job_id"] == command.result_json["job_id"]


def test_failed_addition_can_be_retried_or_restored_to_feed():
    db = _session()
    suggestion = SuggestedVideo(
        provider="youtube", provider_video_id="failed", url="https://example.invalid/failed",
        title="Failed import", artist="Artist", category="new",
    )
    db.add(suggestion)
    db.flush()
    db.add(SuggestedVideoDismissal(
        suggested_video_id=suggestion.id, dismissal_type="permanent",
        reason="auto-dismissed on add", provider="youtube", provider_video_id="failed",
    ))
    job = ProcessingJob(
        job_type="import_url", status=JobStatus.failed, error_message="network unavailable",
        input_params={"suggested_video_id": suggestion.id},
    )
    db.add(job)
    db.commit()

    failed = list_failed_additions(db)
    assert failed[0]["job_id"] == job.id
    assert failed[0]["error"] == "network unavailable"
    restored = restore_failed_suggestion(db, job.id)
    assert restored["suggested_video_id"] == suggestion.id
    assert db.query(SuggestedVideoDismissal).count() == 0
