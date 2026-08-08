"""Read-only API for durable operation state and backlog diagnostics."""
import subprocess
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import (
    ContributionOutbox,
    FileOperation,
    MutationCommand,
    ProcessingJob,
    SidecarOutbox,
)
from app.services.mutation_coordinator import backlog_stats
from app.services.sidecar_outbox import outbox_stats
from app.subprocess_utils import HIDE_WINDOW

router = APIRouter(prefix="/api/operations", tags=["Operations"])


def _tool_version(path_resolver, *args: str) -> dict:
    try:
        path = path_resolver()
        result = subprocess.run(
            [path, *args],
            capture_output=True,
            text=True,
            timeout=8,
            **HIDE_WINDOW,
        )
        line = (result.stdout or result.stderr or "").splitlines()[0][:300]
        return {"available": result.returncode == 0, "version": line}
    except Exception as exc:
        return {"available": False, "error": type(exc).__name__}


def _redis_health(settings) -> dict:
    if settings.deployment_profile != "redis":
        return {"configured": False, "reachable": None}
    try:
        import redis
        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        return {"configured": True, "reachable": bool(client.ping())}
    except Exception as exc:
        return {"configured": True, "reachable": False, "error": type(exc).__name__}


@router.get("/health")
def operation_health(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    from app.version import APP_VERSION
    from app.pipeline_url.write_queue import stats as cosmetic_write_stats
    from app.services.transaction_telemetry import stats as transaction_stats
    file_counts = {
        status: count
        for status, count in db.query(
            FileOperation.status, func.count(FileOperation.id),
        ).group_by(FileOperation.status).all()
    }
    database_retries = sum(
        max(0, (attempts or 0) - 1)
        for (attempts,) in db.query(MutationCommand.attempts).all()
    )
    if inspect(db.get_bind()).has_table("alembic_version"):
        schema_version = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    else:
        schema_version = "unversioned"
    redis_health = _redis_health(settings)
    latest_sidecar = db.query(SidecarOutbox).order_by(SidecarOutbox.completed_at.desc()).first()
    latest_file = db.query(FileOperation).order_by(FileOperation.completed_at.desc()).first()
    contribution_counts = {
        status: count
        for status, count in db.query(
            ContributionOutbox.status, func.count(ContributionOutbox.id),
        ).group_by(ContributionOutbox.status).all()
    }
    return {
        "app_version": APP_VERSION,
        "schema_version": schema_version,
        "sidecar_schema_version": 2,
        "deployment_profile": settings.deployment_profile,
        "worker_reachable": True if settings.deployment_profile == "single_process" else redis_health["reachable"],
        "redis": redis_health,
        "tools": {
            "ffmpeg": _tool_version(lambda: settings.resolved_ffmpeg, "-version"),
            "ffprobe": _tool_version(lambda: settings.resolved_ffprobe, "-version"),
            "yt_dlp": _tool_version(lambda: settings.resolved_ytdlp, "--version"),
        },
        "mutation_queue_limit": settings.mutation_queue_max,
        "mutations": backlog_stats(db),
        "cosmetic_writes": cosmetic_write_stats(),
        "sidecars": outbox_stats(db),
        "tmvdb_contributions": contribution_counts,
        "files": file_counts,
        "database_retry_count": database_retries,
        "write_transactions": transaction_stats(),
        "last_reconciliation": {
            "sidecar": {
                "status": latest_sidecar.status if latest_sidecar else "never",
                "completed_at": latest_sidecar.completed_at if latest_sidecar else None,
            },
            "file": {
                "status": latest_file.status if latest_file else "never",
                "completed_at": latest_file.completed_at if latest_file else None,
            },
        },
    }


@router.post("/migration/preflight")
def migration_preflight_report(db: Session = Depends(get_db)) -> dict:
    from app.services.migration_audit import migration_preflight
    return migration_preflight(db, create_backup=True)


@router.get("/migration/reconciliation")
def migration_reconciliation_report(db: Session = Depends(get_db)) -> dict:
    from app.services.migration_audit import post_migration_reconciliation
    return post_migration_reconciliation(db)


@router.get("/migration/status")
def migration_status_report() -> dict:
    """Return the persisted startup preflight/backup/reconciliation report."""
    from app.runtime_dirs import get_runtime_dirs
    from app.services.startup_migration import load_migration_report
    report_path = get_runtime_dirs().data_dir / "migration-status.json"
    report = load_migration_report(report_path)
    if report is None:
        raise HTTPException(status_code=404, detail={
            "code": "migration_report_not_found",
            "message": "No startup migration report has been recorded",
            "operation_id": None,
            "retryable": False,
            "field_errors": {},
            "diagnostics_id": None,
        })
    return report


@router.get("/sidecars/failed")
def failed_sidecars(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.query(SidecarOutbox).filter(
        SidecarOutbox.status == "failed",
    ).order_by(SidecarOutbox.created_at).all()
    return [{
        "id": row.id,
        "video_id": row.video_id,
        "entity_stable_id": row.entity_stable_id,
        "target_path": row.target_path,
        "entity_revision": row.entity_revision,
        "attempts": row.attempts,
        "error": row.error_json,
        "created_at": row.created_at,
    } for row in rows]


@router.post("/sidecars/{outbox_id}/retry")
def retry_sidecar(outbox_id: str, db: Session = Depends(get_db)) -> dict:
    row = db.get(SidecarOutbox, outbox_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Sidecar operation not found")
    if row.status not in {"failed", "retry"}:
        raise HTTPException(status_code=409, detail=f"Sidecar operation is {row.status}")
    row.status = "retry"
    row.attempts = 0
    row.error_json = None
    row.completed_at = None
    db.commit()
    return {"id": row.id, "status": row.status}


@router.post("/sidecars/{outbox_id}/cancel")
def cancel_sidecar(outbox_id: str, db: Session = Depends(get_db)) -> dict:
    row = db.get(SidecarOutbox, outbox_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Sidecar operation not found")
    if row.status not in {"pending", "retry", "failed"}:
        raise HTTPException(status_code=409, detail=f"Sidecar operation is {row.status}")
    row.status = "cancelled"
    row.error_json = {"code": "cancelled_by_user", "retryable": False}
    row.completed_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": row.id, "status": row.status}


@router.get("/{operation_id}")
def operation_status(operation_id: str, db: Session = Depends(get_db)) -> dict:
    command = db.get(MutationCommand, operation_id)
    if command is not None:
        linked_file = None
        if command.result_json and command.result_json.get("file_operation_id"):
            linked_file = db.get(FileOperation, command.result_json["file_operation_id"])
        effective_status = linked_file.status if linked_file is not None else command.status
        return {
            "operation_id": command.id,
            "request_id": command.request_id,
            "kind": "mutation",
            "operation_type": command.command_type,
            "entity_type": command.entity_type,
            "entity_stable_id": command.entity_stable_id,
            "status": effective_status,
            "attempts": command.attempts,
            "result": {
                **(command.result_json or {}),
                **({"file_operation": linked_file.plan_json} if linked_file else {}),
            } or None,
            "error": linked_file.error_json if linked_file else command.error_json,
            "created_at": command.created_at,
            "started_at": command.started_at,
            "completed_at": command.completed_at,
        }

    contribution = db.query(ContributionOutbox).filter(
        ContributionOutbox.operation_id == operation_id,
    ).one_or_none()
    if contribution is not None:
        return {
            "operation_id": contribution.operation_id,
            "request_id": contribution.request_id,
            "kind": "tmvdb_contribution",
            "operation_type": "tmvdb_push",
            "entity_type": "video",
            "entity_stable_id": None,
            "video_id": contribution.video_id,
            "status": contribution.status,
            "attempts": contribution.attempts,
            "max_attempts": contribution.max_attempts,
            "error": contribution.error_json,
            "created_at": contribution.created_at,
            "started_at": contribution.started_at,
            "completed_at": contribution.completed_at,
        }

    job = db.query(ProcessingJob).filter(ProcessingJob.operation_id == operation_id).one_or_none()
    if job is not None:
        return {
            "operation_id": job.operation_id,
            "request_id": job.request_id,
            "kind": "processing_job",
            "operation_type": job.job_type,
            "entity_type": "video" if job.video_id else None,
            "entity_stable_id": None,
            "video_id": job.video_id,
            "status": job.status.value if hasattr(job.status, "value") else job.status,
            "attempts": job.retry_count,
            "max_attempts": job.max_retries,
            "error": {"message": job.error_message} if job.error_message else None,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
        }

    file_operation = db.get(FileOperation, operation_id)
    if file_operation is not None:
        return {
            "operation_id": file_operation.id,
            "request_id": None,
            "kind": "file_operation",
            "operation_type": file_operation.operation_type,
            "entity_type": "video",
            "entity_stable_id": file_operation.entity_stable_id,
            "status": file_operation.status,
            "attempts": file_operation.current_step,
            "error": file_operation.error_json,
            "created_at": file_operation.created_at,
            "started_at": file_operation.started_at,
            "completed_at": file_operation.completed_at,
        }

    raise HTTPException(status_code=404, detail={
            "code": "operation_not_found",
            "message": "Operation was not found",
            "operation_id": operation_id,
            "retryable": False,
            "field_errors": {},
            "diagnostics_id": None,
        })
