"""Crash-safe storage primitives for authoritative Playarr sidecars.

The database remains the transactional source of truth while the application is
running.  Sidecars are the portable recovery representation, so a failed write
must never replace the last complete document with a partial one.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Callable
from uuid import uuid4
from xml.etree.ElementTree import parse


class SidecarValidationError(ValueError):
    """Raised when a document cannot be used as an authoritative sidecar."""


def sidecar_content_hash(payload: bytes) -> str:
    """Return the portable, explicitly-labelled digest used by sidecar v2."""
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def validate_playarr_sidecar(path: Path) -> None:
    """Validate the minimum identity contract for a Playarr XML sidecar."""
    try:
        root = parse(path).getroot()
    except Exception as exc:  # ElementTree exposes several parser/IO errors
        raise SidecarValidationError(f"invalid XML: {exc}") from exc

    if root.tag != "playarr":
        raise SidecarValidationError("root element must be <playarr>")

    schema_version = root.get("schemaVersion") or root.get("version") or "1"
    if schema_version not in {"1", "2"}:
        raise SidecarValidationError(
            f"unsupported sidecar schema version {schema_version!r}"
        )

    identity = root.find("identity")
    if identity is None or not (identity.findtext("artist") or "").strip():
        raise SidecarValidationError("identity.artist is required")
    if not (identity.findtext("title") or "").strip():
        raise SidecarValidationError("identity.title is required")

    if schema_version == "2":
        portable = root.find("portable_identity")
        video_id = root.get("playarrVideoId")
        if portable is not None:
            video_id = video_id or portable.get("videoId")
        entity_id = root.get("entityId") or (portable.get("entityId") if portable is not None else None)
        if not entity_id:
            raise SidecarValidationError("sidecar v2 requires entityId")
        if not video_id:
            raise SidecarValidationError("sidecar v2 requires playarrVideoId")
        required_attributes = (
            "playarrVersion",
            "sidecarRevision",
            "entityRevision",
            "generatedAt",
            "contentHash",
        )
        missing = [name for name in required_attributes if not root.get(name)]
        if missing:
            raise SidecarValidationError(
                "sidecar v2 missing attributes: " + ", ".join(missing)
            )


def _fsync_directory(directory: Path) -> None:
    """Best-effort directory sync after an atomic replace.

    Windows does not consistently permit opening a directory for fsync.  The
    file itself is always flushed; unsupported directory syncing is tolerated.
    """
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_sidecar(
    path: str | Path,
    payload: bytes,
    *,
    validator: Callable[[Path], None] = validate_playarr_sidecar,
) -> Path:
    """Validate and atomically replace a sidecar on the same filesystem.

    A validated previous document is retained as ``<name>.bak``.  Temporary
    files are unique and removed on every failure path.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{uuid4().hex}.tmp"
    )
    backup = destination.with_name(f"{destination.name}.bak")

    try:
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

        validator(temporary)

        if destination.exists():
            # Never preserve a corrupt file as the recovery copy.
            validator(destination)
            shutil.copy2(destination, backup)
            with backup.open("rb+") as stream:
                stream.flush()
                os.fsync(stream.fileno())

        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
        return destination
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            # The authoritative destination was never changed if replace did
            # not complete; reconciliation can report an undeletable temp.
            pass
