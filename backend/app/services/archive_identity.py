"""Portable archive-manifest identity and compatibility helpers."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import VideoItem


def manifest_video_stable_id(manifest: dict) -> str | None:
    return manifest.get("playarr_video_id") or manifest.get("video_stable_id")


def resolve_manifest_video(db: Session, manifest: dict) -> VideoItem | None:
    """Resolve stable identity first; numeric IDs are legacy-only diagnostics."""
    portable_id = manifest.get("playarr_video_id")
    if portable_id:
        video = db.query(VideoItem).filter(VideoItem.playarr_video_id == portable_id).one_or_none()
        if video is not None:
            return video
        # Compatibility for manifests accidentally labelled playarr_video_id
        # with the entity UUID by the preceding implementation.
        video = db.query(VideoItem).filter(VideoItem.stable_id == portable_id).one_or_none()
        if video is not None:
            return video
    legacy_stable_id = manifest.get("video_stable_id")
    if legacy_stable_id:
        return db.query(VideoItem).filter(VideoItem.stable_id == legacy_stable_id).one_or_none()
    numeric_id = manifest.get("video_id")
    return db.get(VideoItem, numeric_id) if numeric_id else None


def manifest_checksum(manifest: dict) -> tuple[str | None, str | None]:
    if manifest.get("checksum_sha256"):
        return "sha256", manifest["checksum_sha256"]
    if manifest.get("checksum_md5"):
        return "md5", manifest["checksum_md5"]
    return None, None
