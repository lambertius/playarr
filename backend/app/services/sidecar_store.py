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
from xml.etree.ElementTree import Element, fromstring, parse, tostring


class SidecarValidationError(ValueError):
    """Raised when a document cannot be used as an authoritative sidecar."""


def sidecar_content_hash(payload: bytes) -> str:
    """Return the portable, explicitly-labelled digest used by sidecar v2."""
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def sidecar_root_hash(root: Element) -> str:
    """Hash the parser-normalised document with the digest removed.

    XML 1.0 normalises literal CRLF text to LF while parsing.  Hashing a tree
    populated directly from database strings therefore produced a digest that
    could differ from the same document immediately after it was written and
    parsed.  A serialize/parse round-trip makes the digest portable and stable.
    """
    claimed = root.attrib.pop("contentHash", None)
    try:
        payload = tostring(root, encoding="utf-8", xml_declaration=True)
        normalised_root = fromstring(payload)
        normalised_payload = tostring(
            normalised_root, encoding="utf-8", xml_declaration=True,
        )
        return sidecar_content_hash(normalised_payload)
    finally:
        if claimed is not None:
            root.set("contentHash", claimed)


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
        claimed_hash = root.get("contentHash")
        computed_hash = sidecar_root_hash(root)
        if claimed_hash != computed_hash:
            raise SidecarValidationError(
                f"sidecar contentHash mismatch: expected {claimed_hash}, computed {computed_hash}"
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
            try:
                validator(destination)
            except SidecarValidationError:
                pass
            else:
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
