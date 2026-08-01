"""Redacted structured traces built from the production metadata pipeline log."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import PurePath
from typing import Any
from uuid import uuid4

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.models import JobEvent


_SECRET_KEYS = re.compile(r"(api.?key|authorization|bearer|password|secret|token)", re.I)
_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def redact(value: Any, key: str = "") -> Any:
    if _SECRET_KEYS.search(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item, key) for item in value]
    if isinstance(value, str):
        if _WINDOWS_PATH.match(value) or value.startswith("/"):
            safe_name = PurePath(value.replace("\\", "/")).name
            return f"<redacted-path>/{safe_name}"
        return re.sub(
            r"(?i)(api[_-]?key|authorization|bearer|token)=([^&\s]+)",
            r"\1=<redacted>",
            value,
        )
    return value


def build_trace(
    *,
    policy: dict,
    input_summary: dict,
    metadata: dict,
    duration_ms: int,
    source_kind: str,
) -> tuple[str, list[dict]]:
    run_id = f"trace_{uuid4().hex[:26]}"
    safe_input = redact(input_summary)
    input_hash = hashlib.sha256(
        json.dumps(safe_input, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    logs = metadata.get("pipeline_log") or []
    sources = metadata.get("scraper_sources_used") or []
    failures = metadata.get("pipeline_failures") or []
    output_fields = sorted(
        key for key in (
            "artist", "title", "album", "year", "genres", "plot",
            "mb_artist_id", "mb_recording_id", "mb_release_id", "imdb_url",
        ) if metadata.get(key) not in (None, "", [])
    )
    events: list[dict] = [{
        "run_id": run_id,
        "step": "import.policy",
        "status": "succeeded",
        "input_hash": input_hash,
        "input": safe_input,
        "policy": redact(policy),
        "source_kind": source_kind,
        "provider": None,
        "output_fields": [],
        "decisions": [],
        "duration_ms": 0,
        "exception": None,
    }]
    stage_events: dict[str, dict] = {}
    decisions: list[dict] = []
    for raw in logs:
        line = redact(str(raw))
        if line.startswith("stage:"):
            parts = line.split(":", 3)
            stage = parts[1] if len(parts) > 1 else "unknown"
            state = parts[2] if len(parts) > 2 else "recorded"
            status = {
                "started": "running", "complete": "succeeded",
                "succeeded": "succeeded", "disabled": "skipped",
                "failed": "failed", "skipped": "skipped",
            }.get(state, "recorded")
            event = stage_events.setdefault(stage, {
                "run_id": run_id,
                "step": f"metadata.{stage}",
                "status": status,
                "input_hash": input_hash,
                "input": {},
                "policy": redact(policy),
                "source_kind": source_kind,
                "provider": None,
                "output_fields": [],
                "decisions": [],
                "duration_ms": None,
                "exception": None,
            })
            event["status"] = status
            if status == "failed":
                event["exception"] = {"message": parts[3] if len(parts) > 3 else "stage failed"}
        elif line.startswith("scraper:"):
            provider = line.split(":", 2)[1] if ":" in line else "unknown"
            decisions.append({"field": None, "action": "provider_result", "reason": line})
            stage_events.setdefault("scraper_fetch", {
                "run_id": run_id, "step": "metadata.scraper_fetch", "status": "succeeded",
                "input_hash": input_hash, "input": {}, "policy": redact(policy),
                "source_kind": source_kind, "provider": provider, "output_fields": [],
                "decisions": [], "duration_ms": None, "exception": None,
            })["provider"] = provider
        elif any(marker in line for marker in ("rejected", "discarded", "cleared", "change:")):
            decisions.append({"field": line.split(":", 2)[1] if ":" in line else None,
                              "action": "decision", "reason": line})
    for event in stage_events.values():
        if event["step"] == "metadata.scraper_fetch":
            event["decisions"] = decisions
        if event["status"] == "running":
            event["status"] = "succeeded"
    events.extend(stage_events.values())
    events.append({
        "run_id": run_id,
        "step": "metadata.result",
        "status": "failed" if failures else "succeeded",
        "input_hash": input_hash,
        "input": {},
        "policy": redact(policy),
        "source_kind": source_kind,
        "provider": ", ".join(sources) if sources else None,
        "output_fields": output_fields,
        "decisions": decisions,
        "duration_ms": duration_ms,
        "exception": redact(failures) if failures else None,
    })
    return run_id, events


def persist_trace(db: Session, run_id: str, events: list[dict]) -> None:
    for event in events:
        db.add(JobEvent(
            operation_id=run_id,
            stage=event["step"],
            state=event["status"],
            attempt=1,
            input_hash=event.get("input_hash"),
            output_json=event,
            duration_ms=event.get("duration_ms"),
            error_json=event.get("exception"),
        ))
    db.commit()


def diagnostic_bundle(db: Session, run_id: str) -> dict:
    rows = db.query(JobEvent).filter(
        JobEvent.operation_id == run_id,
    ).order_by(JobEvent.id.asc()).all()
    if not rows:
        raise LookupError(run_id)
    from app.version import APP_VERSION
    schema_version = "unversioned"
    if inspect(db.get_bind()).has_table("alembic_version"):
        schema_version = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    return redact({
        "bundle_schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "app_version": APP_VERSION,
        "database_schema": schema_version,
        "policy": (rows[0].output_json or {}).get("policy"),
        "events": [row.output_json for row in rows],
    })
