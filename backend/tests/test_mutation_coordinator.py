"""Architecture release-gate tests for durable mutation admission."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import MutationCommand
from app.services.mutation_coordinator import (
    CommandRequest,
    MutationPriority,
    MutationQueueFull,
    claim_next_command,
    enqueue_command,
    execute_command,
)


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
