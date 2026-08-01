"""HTTP admission helpers for the durable mutation boundary."""
from __future__ import annotations

from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.mutation_coordinator import (
    CommandRequest,
    MutationQueueFull,
    enqueue_command,
)
from app.services.request_context import current_request_id


def mutation_idempotency_key(
    command_type: str,
    entity_stable_id: str,
    supplied: str | None,
) -> str:
    if supplied and supplied.strip():
        return supplied.strip()[:200]
    request_id = current_request_id() or str(uuid4())
    return f"{command_type}:{entity_stable_id}:{request_id}"[:200]


def accept_mutation(db: Session, request: CommandRequest) -> dict:
    """Commit admission quickly and wake the configured mutation worker."""
    try:
        command, created = enqueue_command(
            db, request, max_pending=get_settings().mutation_queue_max,
        )
        db.commit()
    except MutationQueueFull as exc:
        db.rollback()
        raise HTTPException(status_code=429, detail={
            "code": "mutation_queue_full",
            "message": str(exc),
            "operation_id": None,
            "retryable": True,
            "field_errors": {},
            "diagnostics_id": None,
        }) from exc
    from app.services.mutation_runtime import notify_mutation_worker
    notify_mutation_worker()
    return {
        "operation_id": command.id,
        "status": command.status,
        "created": created,
    }
