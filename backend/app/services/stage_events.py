"""Persist structured import-stage events outside stage I/O."""
from __future__ import annotations

from typing import Any


def append_stage_event(
    job_id: int,
    stage: str,
    state: str,
    *,
    attempt: int,
    input_hash: str | None,
    output: dict[str, Any] | None,
    duration_ms: int | None,
    error: dict[str, Any] | None = None,
) -> None:
    from app.database import SessionLocal
    from app.models import JobEvent, ProcessingJob
    from app.pipeline_url.write_queue import db_write

    def persist() -> None:
        db = SessionLocal()
        try:
            job = db.get(ProcessingJob, job_id)
            db.add(JobEvent(
                job_id=job_id,
                operation_id=job.operation_id if job else None,
                stage=stage,
                state=state,
                attempt=attempt,
                input_hash=input_hash,
                output_json=output,
                duration_ms=duration_ms,
                error_json=error,
            ))
            db.commit()
        finally:
            db.close()

    db_write(persist)
