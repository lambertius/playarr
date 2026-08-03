"""Single-consumer runtime for durable mutation commands."""
from __future__ import annotations

import logging
import threading

from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.database import SerializedSessionLocal
from app.services.mutation_coordinator import (
    claim_next_command,
    execute_command,
    recover_interrupted_commands,
)
from app.services.mutation_handlers import handler_registry

logger = logging.getLogger(__name__)
_consumer_lock = threading.Lock()


def recover_mutation_queue(session_factory: sessionmaker = SerializedSessionLocal) -> int:
    db = session_factory()
    try:
        return recover_interrupted_commands(db)
    finally:
        db.close()


def process_next_mutation(session_factory: sessionmaker = SerializedSessionLocal) -> bool:
    """Process one command; the non-blocking lock enforces one actor/process."""
    if not _consumer_lock.acquire(blocking=False):
        return False
    try:
        db = session_factory()
        try:
            command = claim_next_command(db)
        finally:
            db.close()
        if command is None:
            return False
        spec = handler_registry().get(command.command_type)
        if spec is None:
            def unsupported(_db, _command):
                raise ValueError(f"unsupported mutation type {command.command_type}")
            spec_handler = unsupported
        else:
            spec_handler = spec.handle
        try:
            completed = execute_command(session_factory, command.id, spec_handler)
        except Exception:
            logger.exception("Mutation %s failed", command.id)
            return True
        if spec and spec.after_commit and completed.result_json:
            try:
                spec.after_commit(completed.result_json)
            except Exception:
                logger.exception("Post-commit dispatch failed for mutation %s", command.id)
        return True
    finally:
        _consumer_lock.release()


def notify_mutation_worker() -> None:
    """Wake Redis mutation workers; the local actor polls without extra threads."""
    if get_settings().deployment_profile != "redis":
        return
    from app.worker import celery_app
    celery_app.send_task(
        "app.mutation_tasks.process_mutation_queue", queue="mutations",
    )
