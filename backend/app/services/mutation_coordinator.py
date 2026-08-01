"""Durable mutation command boundary.

This module owns command acceptance, idempotency, prioritisation and bounded
retry policy. Domain handlers remain in their own services and are registered
by command type by the worker process.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.models import MutationCommand


class MutationPriority(IntEnum):
    INTERACTIVE = 10
    IMPORT = 50
    BACKGROUND = 70
    MAINTENANCE = 90


class MutationQueueFull(RuntimeError):
    pass


class StaleRevisionError(RuntimeError):
    def __init__(self, expected: int, current: int):
        self.expected = expected
        self.current = current
        super().__init__(f"stale revision: expected {expected}, current {current}")


@dataclass(frozen=True)
class CommandRequest:
    command_type: str
    entity_type: str
    entity_stable_id: str
    payload: dict[str, Any]
    idempotency_key: str
    expected_revision: int | None = None
    actor_id: str | None = None
    priority: MutationPriority = MutationPriority.INTERACTIVE


def enqueue_command(
    db: Session,
    request: CommandRequest,
    *,
    max_pending: int = 1000,
) -> tuple[MutationCommand, bool]:
    """Persist an idempotent command in the caller's transaction.

    Returns ``(command, created)``. Repeated HTTP attempts return the original
    operation even when the queue is currently at capacity.
    """
    existing = db.query(MutationCommand).filter(
        MutationCommand.idempotency_key == request.idempotency_key
    ).one_or_none()
    if existing is not None:
        return existing, False

    pending = db.query(func.count(MutationCommand.id)).filter(
        MutationCommand.status.in_(("pending", "running"))
    ).scalar() or 0
    if pending >= max_pending:
        raise MutationQueueFull(
            f"mutation backlog is full ({pending}/{max_pending})"
        )

    command = MutationCommand(
        id=str(uuid4()),
        idempotency_key=request.idempotency_key,
        command_type=request.command_type,
        entity_type=request.entity_type,
        entity_stable_id=request.entity_stable_id,
        expected_revision=request.expected_revision,
        actor_id=request.actor_id,
        priority=int(request.priority),
        payload_json=request.payload,
        status="pending",
    )
    db.add(command)
    db.flush()
    return command, True


def claim_next_command(db: Session) -> MutationCommand | None:
    """Claim the highest-priority oldest command.

    The mutation worker is deliberately single-consumer in the SQLite profile.
    Redis deployments route this function to the dedicated mutation worker.
    """
    command = (
        db.query(MutationCommand)
        .filter(MutationCommand.status == "pending")
        .order_by(MutationCommand.priority.asc(), MutationCommand.created_at.asc())
        .first()
    )
    if command is None:
        return None
    command.status = "running"
    command.started_at = datetime.now(timezone.utc)
    command.attempts += 1
    db.commit()
    db.refresh(command)
    return command


Handler = Callable[[Session, MutationCommand], dict[str, Any] | None]


def execute_command(
    session_factory: sessionmaker,
    command_id: str,
    handler: Handler,
    *,
    max_attempts: int = 5,
) -> MutationCommand:
    """Execute a short domain mutation with bounded SQLITE_BUSY retries."""
    attempt = 0
    while True:
        attempt += 1
        db = session_factory()
        try:
            command = db.get(MutationCommand, command_id)
            if command is None:
                raise LookupError(f"unknown mutation command {command_id}")
            if command.status == "succeeded":
                return command

            output = handler(db, command) or {}
            command.status = "succeeded"
            command.completed_at = datetime.now(timezone.utc)
            command.error_json = None
            command.result_json = output or None
            db.commit()
            db.refresh(command)
            return command
        except OperationalError as exc:
            db.rollback()
            locked = "locked" in str(exc).lower() or "busy" in str(exc).lower()
            if not locked or attempt >= max_attempts:
                _mark_failed(session_factory, command_id, exc, retryable=locked)
                raise
            _record_database_retry(session_factory, command_id, exc, attempt)
            time.sleep(min(0.5, 0.025 * (2 ** (attempt - 1))) + random.uniform(0, 0.025))
        except StaleRevisionError as exc:
            db.rollback()
            _mark_failed(
                session_factory, command_id, exc, retryable=False,
                code="stale_revision",
                extra={"expected_revision": exc.expected, "current_revision": exc.current},
            )
            raise
        except Exception as exc:
            db.rollback()
            _mark_failed(session_factory, command_id, exc, retryable=False)
            raise
        finally:
            db.close()


def _mark_failed(
    session_factory: sessionmaker,
    command_id: str,
    exc: Exception,
    *,
    retryable: bool,
    code: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    db = session_factory()
    try:
        command = db.get(MutationCommand, command_id)
        if command is not None:
            command.status = "failed"
            command.completed_at = datetime.now(timezone.utc)
            command.error_json = {
                "code": code or ("database_locked" if retryable else "mutation_failed"),
                "message": str(exc),
                "retryable": retryable,
                **(extra or {}),
            }
            db.commit()
    finally:
        db.close()


def _record_database_retry(
    session_factory: sessionmaker,
    command_id: str,
    exc: OperationalError,
    attempt: int,
) -> None:
    db = session_factory()
    try:
        command = db.get(MutationCommand, command_id)
        if command is not None:
            command.attempts += 1
            command.error_json = {
                "code": "database_locked_retry",
                "message": str(exc),
                "retryable": True,
                "attempt": attempt,
            }
            db.commit()
    finally:
        db.close()


def backlog_stats(db: Session) -> dict[str, Any]:
    pending_query = db.query(MutationCommand).filter(
        MutationCommand.status.in_(("pending", "running"))
    )
    pending = pending_query.count()
    created = pending_query.with_entities(func.min(MutationCommand.created_at)).scalar()
    now = datetime.now(timezone.utc)
    oldest_age = 0.0
    if created:
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        oldest_age = max(0.0, (now - created).total_seconds())
    return {"pending": pending, "oldest_age_seconds": round(oldest_age, 3)}


def recover_interrupted_commands(db: Session) -> int:
    """Return commands abandoned by a stopped actor to the durable queue."""
    recovered = db.query(MutationCommand).filter(
        MutationCommand.status == "running"
    ).update({
        MutationCommand.status: "pending",
        MutationCommand.started_at: None,
        MutationCommand.error_json: {
            "code": "worker_restarted",
            "message": "Mutation worker stopped before recording a terminal result",
            "retryable": True,
        },
    }, synchronize_session=False)
    db.commit()
    return recovered
