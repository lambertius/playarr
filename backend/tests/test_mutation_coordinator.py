"""Architecture release-gate tests for durable mutation admission."""
import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import JobStatus, MutationCommand, ProcessingJob, VideoItem
from app.services.mutation_coordinator import (
    CommandRequest,
    MutationPriority,
    MutationQueueFull,
    claim_next_command,
    enqueue_command,
    execute_command,
)
from app.services.mutation_handlers import apply_import_plan


def _sessions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'commands.db'}")
    MutationCommand.__table__.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _request(key: str, priority: MutationPriority = MutationPriority.INTERACTIVE):
    return CommandRequest(
        command_type="video.rating.update",
        entity_type="video",
        entity_stable_id="PVD-test",
        payload={"video_rating": 4},
        idempotency_key=key,
        expected_revision=1,
        priority=priority,
    )


def test_enqueue_is_idempotent(tmp_path):
    sessions = _sessions(tmp_path)
    db = sessions()
    first, created = enqueue_command(db, _request("same-key"))
    db.commit()
    second, created_again = enqueue_command(db, _request("same-key"))

    assert created is True
    assert created_again is False
    assert first.id == second.id
    assert db.query(MutationCommand).count() == 1


def test_interactive_command_claims_before_maintenance(tmp_path):
    sessions = _sessions(tmp_path)
    db = sessions()
    maintenance, _ = enqueue_command(
        db, _request("maintenance", MutationPriority.MAINTENANCE)
    )
    interactive, _ = enqueue_command(
        db, _request("interactive", MutationPriority.INTERACTIVE)
    )
    db.commit()

    claimed = claim_next_command(db)

    assert claimed.id == interactive.id
    assert claimed.id != maintenance.id
    assert claimed.status == "running"


def test_queue_is_bounded_but_retry_can_recover_original(tmp_path):
    sessions = _sessions(tmp_path)
    db = sessions()
    original, _ = enqueue_command(db, _request("original"), max_pending=1)
    db.commit()

    recovered, created = enqueue_command(db, _request("original"), max_pending=1)
    assert created is False
    assert recovered.id == original.id

    try:
        enqueue_command(db, _request("overflow"), max_pending=1)
    except MutationQueueFull as exc:
        assert "1/1" in str(exc)
    else:
        raise AssertionError("a full queue accepted a new mutation")


def test_execute_command_is_terminal_and_idempotent(tmp_path):
    sessions = _sessions(tmp_path)
    db = sessions()
    command, _ = enqueue_command(db, _request("execute"))
    db.commit()
    claim_next_command(db)
    calls = []

    def handler(_db, current):
        calls.append(current.id)

    first = execute_command(sessions, command.id, handler)
    second = execute_command(sessions, command.id, handler)

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert calls == [command.id]


def test_interactive_admission_under_500ms_with_four_imports_and_500_backlog(tmp_path):
    sessions = _sessions(tmp_path)
    db = sessions()
    for index in range(4):
        command, _ = enqueue_command(
            db, _request(f"active-import-{index}", MutationPriority.IMPORT),
            max_pending=1000,
        )
        command.status = "running"
    for index in range(496):
        enqueue_command(
            db, _request(f"background-{index}", MutationPriority.BACKGROUND),
            max_pending=1000,
        )
    db.commit()

    started = time.perf_counter()
    interactive, created = enqueue_command(
        db, _request("interactive-under-load"), max_pending=1000,
    )
    db.commit()
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert created is True
    assert elapsed_ms < 500
    assert claim_next_command(db).id == interactive.id


def test_database_lock_retries_are_internal_and_recorded(tmp_path, monkeypatch):
    sessions = _sessions(tmp_path)
    db = sessions()
    command, _ = enqueue_command(db, _request("locked-then-success"))
    db.commit()
    claim_next_command(db)
    calls = 0

    def handler(_db, _command):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise OperationalError("UPDATE", {}, Exception("database is locked"))
        return {"saved": True}

    monkeypatch.setattr("app.services.mutation_coordinator.time.sleep", lambda _delay: None)
    completed = execute_command(sessions, command.id, handler)

    assert completed.status == "succeeded"
    assert completed.result_json == {"saved": True}
    assert completed.attempts == 3
    assert completed.error_json is None


def test_exhausted_database_lock_has_structured_terminal_failure(tmp_path, monkeypatch):
    sessions = _sessions(tmp_path)
    db = sessions()
    command, _ = enqueue_command(db, _request("locked-terminal"))
    db.commit()
    claim_next_command(db)

    def locked(_db, _command):
        raise OperationalError("UPDATE", {}, Exception("database is locked"))

    monkeypatch.setattr("app.services.mutation_coordinator.time.sleep", lambda _delay: None)
    with pytest.raises(OperationalError):
        execute_command(sessions, command.id, locked, max_attempts=2)
    failed = sessions().get(MutationCommand, command.id)
    assert failed.status == "failed"
    assert failed.error_json["code"] == "database_locked"
    assert failed.error_json["retryable"] is True


def test_import_plan_is_applied_atomically_by_mutation_actor():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    db = sessions()
    job = ProcessingJob(job_type="import_url", status=JobStatus.analyzing)
    db.add(job)
    db.flush()
    plan = {
        "job_id": job.id,
        "import_type": "url",
        "video": {
            "action": "create", "artist": "Fixture Artist", "title": "Fixture Track",
            "folder_path": "library/Fixture Artist/Fixture Track",
            "file_path": "library/Fixture Artist/Fixture Track/Fixture Track.mp4",
        },
        "genres": [], "sources": [], "entities": {}, "media_assets": [],
        "processing_flags": {}, "version_type": "normal",
    }
    command, _ = enqueue_command(db, CommandRequest(
        command_type="import.plan.apply", entity_type="import_job",
        entity_stable_id=f"job:{job.id}", payload={"plan": plan},
        idempotency_key="fixture-import-plan", priority=MutationPriority.IMPORT,
    ))
    db.commit()

    completed = execute_command(sessions, command.id, apply_import_plan)

    verify = sessions()
    video = verify.query(VideoItem).one()
    persisted_job = verify.get(ProcessingJob, job.id)
    assert completed.status == "succeeded"
    assert completed.result_json == {"video_id": video.id}
    assert persisted_job.video_id == video.id
    assert persisted_job.status == JobStatus.complete
