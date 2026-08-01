"""MIG-001/MIG-003 startup migration orchestration.

The migration boundary deliberately operates on SQLite metadata before ORM
models are queried. This keeps an older database inspectable even when the
current model expects columns that have not been added yet.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import Engine, inspect, text
from sqlalchemy.orm import Session


class MigrationBlockedError(RuntimeError):
    """Raised when preflight cannot preserve the existing database safely."""

    def __init__(self, report: dict[str, Any]):
        self.report = report
        failures = ", ".join(report.get("critical_failures", [])) or "unknown"
        super().__init__(f"database migration blocked by preflight: {failures}")


class MigrationFailedError(RuntimeError):
    """Raised after a failed migration has restored the original database."""

    def __init__(self, report: dict[str, Any]):
        self.report = report
        super().__init__(report.get("error", "database migration failed"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _database_path(engine: Engine) -> Path | None:
    if engine.dialect.name != "sqlite":
        return None
    database = engine.url.database
    if not database or database == ":memory:":
        return None
    return Path(database).expanduser().resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _table_columns(engine: Engine, table_name: str) -> set[str]:
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _scalar(engine: Engine, statement: str, default: Any = None) -> Any:
    try:
        with engine.connect() as connection:
            value = connection.execute(text(statement)).scalar()
        return default if value is None else value
    except Exception:
        return default


def collect_raw_preflight(engine: Engine) -> dict[str, Any]:
    """Inspect an old schema without instantiating current ORM entities."""
    path = _database_path(engine)
    tables = set(inspect(engine).get_table_names())
    integrity = _scalar(engine, "PRAGMA integrity_check", "not_checked") if path else "not_applicable"
    schema_version = (
        _scalar(engine, "SELECT version_num FROM alembic_version", "unversioned")
        if "alembic_version" in tables else "unversioned"
    )
    application_version = "unversioned"
    setting_columns = _table_columns(engine, "settings")
    if {"key", "value"} <= setting_columns:
        application_version = _scalar(
            engine, "SELECT value FROM settings WHERE key = 'schema_version' LIMIT 1", "unversioned",
        )

    missing_ids: list[int] = []
    duplicate_ids: list[str] = []
    missing_files: list[int] = []
    missing_sidecars: list[int] = []
    unwritable_sidecars: list[int] = []
    video_columns = _table_columns(engine, "video_items")
    if {"id", "stable_id"} <= video_columns:
        with engine.connect() as connection:
            missing_ids = [
                row[0] for row in connection.execute(text(
                    "SELECT id FROM video_items WHERE stable_id IS NULL OR trim(stable_id) = ''"
                ))
            ]
            duplicate_ids = [
                row[0] for row in connection.execute(text(
                    "SELECT stable_id FROM video_items "
                    "WHERE stable_id IS NOT NULL AND trim(stable_id) <> '' "
                    "GROUP BY stable_id HAVING count(*) > 1"
                ))
            ]
    if "id" in video_columns and ({"file_path", "folder_path"} & video_columns):
        selected = ["id"] + [name for name in ("file_path", "folder_path") if name in video_columns]
        with engine.connect() as connection:
            rows = connection.execute(text(f"SELECT {', '.join(selected)} FROM video_items")).mappings()
            for row in rows:
                file_value = row.get("file_path")
                folder_value = row.get("folder_path")
                if file_value and not Path(file_value).is_file():
                    missing_files.append(row["id"])
                if not file_value:
                    continue
                folder = Path(folder_value) if folder_value else Path(file_value).parent
                if folder.exists() and not os.access(folder, os.W_OK):
                    unwritable_sidecars.append(row["id"])
                expected = folder / f"{Path(file_value).stem}.playarr.xml"
                if not expected.is_file():
                    missing_sidecars.append(row["id"])

    pending: dict[str, int] = {}
    for label, table_name, states in (
        ("mutations", "mutation_commands", ("pending", "running", "retry")),
        ("sidecars", "sidecar_outbox", ("pending", "running", "retry")),
        ("files", "file_operations", ("planned", "running", "rollback", "waiting_for_release")),
    ):
        if table_name not in tables:
            pending[label] = 0
            continue
        quoted = ",".join(f"'{state}'" for state in states)
        pending[label] = int(_scalar(
            engine, f"SELECT count(*) FROM {table_name} WHERE status IN ({quoted})", 0,
        ))

    critical: list[str] = []
    if integrity not in ("ok", "not_applicable"):
        critical.append("database_integrity_failed")
    if duplicate_ids:
        critical.append("duplicate_stable_ids")
    if unwritable_sidecars:
        critical.append("sidecar_not_writable")
    return {
        "checked_at": _utc_now(),
        "database_path": str(path) if path else None,
        "schema_version_before": schema_version,
        "application_version_before": application_version,
        "integrity_check": integrity,
        "missing_stable_id_video_ids": missing_ids,
        "duplicate_stable_ids": duplicate_ids,
        "missing_file_video_ids": missing_files,
        "missing_sidecar_video_ids": missing_sidecars,
        "unwritable_sidecar_video_ids": unwritable_sidecars,
        "pending_operations": pending,
        "critical_failures": critical,
    }


def create_consistent_backup(database_path: Path, backup_dir: Path) -> Path:
    """Create and validate a SQLite online backup, including WAL contents."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"{database_path.stem}.pre-v2-{stamp}-{uuid4().hex[:8]}.db.bak"
    source_connection = sqlite3.connect(str(database_path))
    destination_connection = sqlite3.connect(str(destination))
    try:
        source_connection.backup(destination_connection)
        result = destination_connection.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"backup integrity check failed: {result}")
    finally:
        destination_connection.close()
        source_connection.close()
    return destination


def restore_backup(engine: Engine, backup_path: Path, database_path: Path) -> None:
    """Atomically restore a validated backup after closing pooled connections."""
    engine.dispose()
    temporary = database_path.with_name(f".{database_path.name}.{uuid4().hex}.restore")
    try:
        shutil.copy2(backup_path, temporary)
        connection = sqlite3.connect(str(temporary))
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            connection.close()
        if result != "ok":
            raise RuntimeError(f"restore copy integrity check failed: {result}")
        os.replace(temporary, database_path)
        for suffix in ("-wal", "-shm"):
            Path(f"{database_path}{suffix}").unlink(missing_ok=True)
    finally:
        temporary.unlink(missing_ok=True)


def _write_report(report_path: Path | None, report: dict[str, Any]) -> None:
    if report_path is None:
        return
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_name(f".{report_path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, report_path)


def run_startup_migration(
    engine: Engine,
    *,
    create_schema: Callable[[], None],
    apply_upgrades: Callable[[], None],
    stamp_version: Callable[[], None],
    backup_dir: Path,
    report_path: Path | None = None,
    reconcile: Callable[[Session], dict[str, Any]] | None = None,
    target_version: str | None = None,
) -> dict[str, Any]:
    """Back up, migrate, reconcile and restore on any failed upgrade."""
    database_path = _database_path(engine)
    existed = bool(database_path and database_path.is_file() and database_path.stat().st_size > 0)
    preflight = collect_raw_preflight(engine) if existed else {
        "checked_at": _utc_now(),
        "database_path": str(database_path) if database_path else None,
        "schema_version_before": "new",
        "application_version_before": "new",
        "integrity_check": "not_applicable",
        "missing_stable_id_video_ids": [],
        "duplicate_stable_ids": [],
        "missing_file_video_ids": [],
        "missing_sidecar_video_ids": [],
        "unwritable_sidecar_video_ids": [],
        "pending_operations": {"mutations": 0, "sidecars": 0, "files": 0},
        "critical_failures": [],
    }
    report: dict[str, Any] = {
        "status": "preflight",
        "started_at": _utc_now(),
        "preflight": preflight,
        "database_backup_path": None,
        "database_backup_sha256": None,
        "original_database_sha256": _sha256(database_path) if existed and database_path else None,
    }
    migration_required = not existed or target_version is None or (
        preflight.get("application_version_before") != target_version
    )
    report["target_version"] = target_version
    report["migration_required"] = migration_required
    if not migration_required:
        report.update(status="not_required", completed_at=_utc_now())
        return report

    backup_path: Path | None = None
    if existed and database_path:
        try:
            backup_path = create_consistent_backup(database_path, backup_dir)
            report["database_backup_path"] = str(backup_path)
            report["database_backup_sha256"] = _sha256(backup_path)
        except Exception as exc:
            preflight["critical_failures"].append("database_backup_failed")
            report["backup_error"] = f"{type(exc).__name__}: {exc}"

    if preflight["critical_failures"]:
        report.update(status="blocked", completed_at=_utc_now())
        _write_report(report_path, report)
        raise MigrationBlockedError(report)

    try:
        create_schema()
        apply_upgrades()
        stamp_version()
        reconciliation = None
        if reconcile is not None:
            with Session(engine) as session:
                reconciliation = reconcile(session)
        report.update(
            status=(
                "complete_with_discrepancies"
                if reconciliation and reconciliation.get("status") == "discrepancies"
                else "complete"
            ),
            reconciliation=reconciliation,
            completed_at=_utc_now(),
        )
        _write_report(report_path, report)
        return report
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["restored_from_backup"] = False
        if backup_path is not None and database_path is not None:
            try:
                restore_backup(engine, backup_path, database_path)
                report["restored_from_backup"] = True
            except Exception as restore_exc:
                report["restore_error"] = f"{type(restore_exc).__name__}: {restore_exc}"
        report.update(status="failed_restored" if report["restored_from_backup"] else "failed", completed_at=_utc_now())
        _write_report(report_path, report)
        raise MigrationFailedError(report) from exc


def load_migration_report(report_path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
