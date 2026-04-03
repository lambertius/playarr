# AUTO-SEPARATED from pipeline/workspace.py for pipeline_lib pipeline
# This file is independent — changes here do NOT affect the other pipeline.
"""
Import workspace manager.

Each import job gets a temporary workspace directory under data/workspaces/import_<job_id>/.
All intermediate results are written as JSON artifacts.  A stage ledger (status.json) tracks
which pipeline stages have completed, enabling crash-recovery and resumability.  An NDJSON log
(logs.ndjson) replaces per-step DB log writes.
"""
import json
import os
import shutil
import time
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_WORKSPACE_ROOT: Optional[str] = None


def _get_workspace_root() -> str:
    global _WORKSPACE_ROOT
    if _WORKSPACE_ROOT is None:
        from app.runtime_dirs import get_runtime_dirs
        _WORKSPACE_ROOT = str(get_runtime_dirs().workspace_dir)
    return _WORKSPACE_ROOT


class ImportWorkspace:
    """Per-import temporary workspace for staged pipeline artifacts."""

    def __init__(self, job_id: int):
        self.job_id = job_id
        self._root = os.path.join(_get_workspace_root(), f"import_{job_id}")
        os.makedirs(self._root, exist_ok=True)
        os.makedirs(self.media_dir, exist_ok=True)

    # ── Reset ─────────────────────────────────────────────────────────
    def reset(self) -> None:
        """Nuke and recreate workspace to purge ALL stale state.

        SQLite can recycle job IDs after deletions, so a new job may
        inherit a workspace directory populated by an old (deleted) job.
        Using rmtree + makedirs instead of selective file deletion for
        immunity to Windows/OneDrive file-lock issues.
        """
        if os.path.isdir(self._root):
            shutil.rmtree(self._root, ignore_errors=True)
        os.makedirs(self._root, exist_ok=True)
        os.makedirs(self.media_dir, exist_ok=True)
        # Also clear the per-job log file (written in append mode)
        try:
            from app.config import get_settings
            log_file = os.path.join(
                get_settings().log_dir, "jobs", f"{self.job_id}.log"
            )
            if os.path.isfile(log_file):
                os.remove(log_file)
        except Exception:
            pass

    # ── Path helpers ──────────────────────────────────────────────────
    @property
    def path(self) -> str:
        return self._root

    @property
    def media_dir(self) -> str:
        return os.path.join(self._root, "media")

    def artifact_path(self, name: str) -> str:
        if not name.endswith(".json"):
            name = f"{name}.json"
        return os.path.join(self._root, name)

    # ── Artifact I/O ──────────────────────────────────────────────────
    def write_artifact(self, name: str, data: Any) -> None:
        """Write a JSON-serializable artifact to the workspace."""
        path = self.artifact_path(name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str, ensure_ascii=False)
        # os.replace can fail on Windows/OneDrive when the target file is
        # temporarily locked by cloud sync.  Retry a few times.
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt < 4:
                    import time
                    time.sleep(0.2 * (attempt + 1))
                else:
                    raise

    def read_artifact(self, name: str) -> Any:
        """Read a JSON artifact.  Returns None if missing."""
        path = self.artifact_path(name)
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def has_artifact(self, name: str) -> bool:
        return os.path.isfile(self.artifact_path(name))

    # ── Stage ledger (status.json) ────────────────────────────────────
    def _read_status(self) -> dict:
        data = self.read_artifact("status")
        if data is None:
            data = {"stages": {}, "created_at": datetime.now(timezone.utc).isoformat()}
        return data

    def _write_status(self, data: dict) -> None:
        self.write_artifact("status", data)

    def update_stage(self, stage: str, status: str, **extra) -> None:
        """Record a stage transition in the ledger."""
        data = self._read_status()
        entry = data["stages"].get(stage, {})
        entry["status"] = status
        now = datetime.now(timezone.utc).isoformat()
        if status == "running" and "started_at" not in entry:
            entry["started_at"] = now
        if status in ("complete", "failed", "skipped"):
            entry["completed_at"] = now
        if extra:
            entry.update(extra)
        data["stages"][stage] = entry
        self._write_status(data)

    def is_stage_complete(self, stage: str) -> bool:
        data = self._read_status()
        return data.get("stages", {}).get(stage, {}).get("status") == "complete"

    def get_stage_status(self, stage: str) -> Optional[str]:
        data = self._read_status()
        return data.get("stages", {}).get(stage, {}).get("status")

    # ── NDJSON log ────────────────────────────────────────────────────
    def log(self, message: str, level: str = "info") -> None:
        """Append a structured log entry to logs.ndjson + per-job file.

        DB sync is deferred to ``sync_logs_to_db`` (called once at the
        end of the deferred-task coordinator) to eliminate write
        contention from logging.
        """
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "msg": message,
        }
        log_path = os.path.join(self._root, "logs.ndjson")
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:
            pass  # logging must never crash the pipeline

        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        log_line = f"[{ts}] {message}\n"

        # Write to per-job file log for backward compat
        try:
            from app.config import get_settings
            log_dir = os.path.join(get_settings().log_dir, "jobs")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, f"{self.job_id}.log"), "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception:
            pass

    def get_logs(self) -> list:
        """Read all NDJSON log entries."""
        log_path = os.path.join(self._root, "logs.ndjson")
        if not os.path.isfile(log_path):
            return []
        entries = []
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return entries

    def sync_logs_to_db(self) -> None:
        """Bulk-write per-job file log to ``ProcessingJob.log_text``.

        Called once by the deferred-task coordinator after all tasks
        finish.  Reads the accumulated per-job file log and writes it
        to the DB in a single transaction, eliminating the per-message
        DB write that was the primary source of write contention.
        """
        try:
            from app.config import get_settings
            log_file = os.path.join(
                get_settings().log_dir, "jobs", f"{self.job_id}.log"
            )
            if not os.path.isfile(log_file):
                return
            with open(log_file, "r", encoding="utf-8") as f:
                log_text = f.read()
            if not log_text:
                return

            from app.database import CosmeticSessionLocal
            from app.models import ProcessingJob

            db = CosmeticSessionLocal()
            try:
                job = db.query(ProcessingJob).get(self.job_id)
                if job:
                    job.log_text = log_text
                    db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()
        except Exception:
            pass  # logging sync must never crash the pipeline

    # ── Cleanup ───────────────────────────────────────────────────────
    def cleanup(self, force: bool = False) -> None:
        """Remove the workspace directory.

        By default, only cleans up if the import completed or failed.
        Use force=True to always remove.
        """
        if not os.path.isdir(self._root):
            return
        if not force:
            status = self._read_status()
            # Keep workspace if import is still in progress
            stages = status.get("stages", {})
            has_terminal = any(
                s.get("status") in ("complete", "failed")
                for s in stages.values()
                if s.get("status")
            )
            # Only clean up if at least one stage reached a terminal state
            # or the apply completed
            apply_done = stages.get("apply", {}).get("status") in ("complete", "failed")
            if not (apply_done or has_terminal):
                return
        try:
            shutil.rmtree(self._root, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Workspace cleanup failed for job {self.job_id}: {e}")

    def cleanup_on_success(self) -> None:
        """Remove workspace after successful import."""
        try:
            shutil.rmtree(self._root, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Workspace cleanup failed for job {self.job_id}: {e}")
