"""Crash-safe storage for authoritative JSON documents."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4


def _validate_json(path: Path) -> None:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("authoritative JSON root must be an object")


def atomic_write_json(path: str | Path, document: dict[str, Any]) -> Path:
    """Flush, validate and atomically replace a JSON document in-place."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    backup = destination.with_name(f"{destination.name}.bak")
    try:
        payload = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _validate_json(temporary)
        if destination.exists():
            _validate_json(destination)
            shutil.copy2(destination, backup)
            with backup.open("rb+") as stream:
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
