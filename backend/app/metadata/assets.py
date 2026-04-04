"""
Central Asset Cache Manager
============================
Downloads, deduplicates, resizes, and stores artwork in a central cache.

Cache layout::

    <asset_cache_dir>/
        artists/<entity_id>/
            poster.jpg
            fanart.jpg
        albums/<entity_id>/
            poster.jpg
        videos/<entity_id>/
            poster.jpg
            thumb.jpg

Rules:
- Deduplicate by SHA-256 checksum (same image → same record).
- Reuse cached assets across exports.
- Generate size variants when needed (poster, thumb, fanart).
- Always convert to JPEG for Kodi compatibility.

Default sizing:
    artist poster:  1000×1500  (portrait)
    artist fanart:  1920×1080  (landscape)
    album  poster:  1000×1000  (square)
    video  poster:  1000×1500
    video  thumb:   1280×720   (landscape)
"""
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from app.config import get_settings
from app.database import SessionLocal
from app.metadata.models import CachedAsset
from app.metadata.providers.base import AssetCandidate
from app.services.artwork_service import (
    download_and_validate,
    validate_file,
    validate_existing_cached_asset,
    invalidate_cached_asset,
)

logger = logging.getLogger(__name__)

# Default max dimensions per (entity_type, kind)
_SIZE_RULES: Dict[tuple, tuple] = {
    ("artist", "poster"):  (1000, 1500),
    ("artist", "fanart"):  (1920, 1080),
    ("album",  "poster"):  (1000, 1000),
    ("video",  "poster"):  (1000, 1500),
    ("video",  "thumb"):   (1280, 720),
}
_DEFAULT_SIZE = (1000, 1000)


def _cache_dir() -> str:
    """Return and ensure the root asset cache directory."""
    d = get_settings().asset_cache_dir
    os.makedirs(d, exist_ok=True)
    return d


def _entity_cache_dir(entity_type: str, entity_id: int) -> str:
    """Return and ensure per-entity cache subdirectory."""
    d = os.path.join(_cache_dir(), f"{entity_type}s", str(entity_id))
    os.makedirs(d, exist_ok=True)
    return d


def _sha256(path: str) -> str:
    """Compute SHA-256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def download_asset(
    entity_type: str,
    entity_id: int,
    candidate: AssetCandidate,
    overwrite: bool = False,
) -> Optional[CachedAsset]:
    """
    Download a single asset candidate into the cache.

    Uses the unified artwork_service for validation.
    Returns a CachedAsset ORM object, or None on failure.
    Skips download if a matching *valid* record already exists
    (dedup by entity_type + entity_id + kind).
    """
    db = SessionLocal()
    try:
        # Check existing
        existing = db.query(CachedAsset).filter(
            CachedAsset.entity_type == entity_type,
            CachedAsset.entity_id == entity_id,
            CachedAsset.kind == candidate.kind,
        ).first()

        dest_dir = _entity_cache_dir(entity_type, entity_id)
        dest_path = os.path.join(dest_dir, f"{candidate.kind}.jpg")

        if existing and not overwrite:
            # Validate existing file if present
            if existing.local_cache_path and os.path.isfile(existing.local_cache_path):
                if getattr(existing, "status", "valid") == "valid":
                    # Quick validation: check it's actually an image
                    vr = validate_file(existing.local_cache_path)
                    if vr.valid:
                        logger.debug(f"Asset already cached and valid: {existing.local_cache_path}")
                        return existing
                    else:
                        logger.warning(f"Cached asset is corrupt ({vr.error}), re-downloading: {existing.local_cache_path}")
                        # Fall through to re-download
            elif existing and getattr(existing, "status", "valid") in ("invalid", "missing"):
                # Previously failed — retry download
                logger.info(f"Re-downloading previously invalid asset: {entity_type}/{entity_id}/{candidate.kind}")

        if not candidate.url:
            return None

        # Download with full validation via artwork_service
        max_w, max_h = _SIZE_RULES.get((entity_type, candidate.kind), _DEFAULT_SIZE)
        result = download_and_validate(
            candidate.url,
            dest_path,
            max_width=max_w,
            max_height=max_h,
            provider=candidate.provenance,
            overwrite=True,  # We already checked dedup above
        )

        if not result.success:
            logger.warning(f"Asset download/validation failed for {candidate.url}: {result.error}")
            if existing:
                existing.status = "invalid"
                existing.validation_error = result.error
                existing.last_validated_at = datetime.now(timezone.utc)
                _commit_with_retry(db)
            return None

        now = datetime.now(timezone.utc)
        if existing:
            existing.source_url = candidate.url
            existing.resolved_url = result.resolved_url
            existing.local_cache_path = dest_path
            existing.checksum = result.checksum
            existing.file_hash = result.file_hash
            existing.width = result.width
            existing.height = result.height
            existing.format = result.format or "jpeg"
            existing.content_type = result.content_type
            existing.file_size_bytes = result.file_size_bytes
            existing.provenance = candidate.provenance
            existing.source_provider = candidate.provenance
            existing.confidence = candidate.confidence
            existing.status = "valid"
            existing.validation_error = None
            existing.last_validated_at = now
            _commit_with_retry(db)
            return existing

        asset = CachedAsset(
            entity_type=entity_type,
            entity_id=entity_id,
            kind=candidate.kind,
            source_url=candidate.url,
            resolved_url=result.resolved_url,
            local_cache_path=dest_path,
            checksum=result.checksum,
            file_hash=result.file_hash,
            width=result.width,
            height=result.height,
            format=result.format or "jpeg",
            content_type=result.content_type,
            file_size_bytes=result.file_size_bytes,
            provenance=candidate.provenance,
            source_provider=candidate.provenance,
            confidence=candidate.confidence,
            status="valid",
            last_validated_at=now,
        )
        db.add(asset)
        _commit_with_retry(db)
        db.refresh(asset)
        return asset

    except Exception as e:
        logger.error(f"Asset download failed: {e}")
        db.rollback()
        return None
    finally:
        db.close()


def _commit_with_retry(db, max_attempts: int = 5):
    """Commit with retry for SQLite lock contention.

    After a rollback, newly-added (transient) objects are expunged from the
    session.  We snapshot ``db.new`` before each attempt and re-add them on
    retry so the subsequent commit still has pending changes.
    """
    import time
    for attempt in range(max_attempts):
        # Snapshot newly-added objects so we can re-add after rollback.
        pending_new = list(db.new)
        try:
            db.commit()
            return
        except Exception as exc:
            db.rollback()
            if "database is locked" in str(exc) and attempt < max_attempts - 1:
                logger.warning(f"DB commit locked, retry {attempt + 1}/{max_attempts}")
                # Re-add objects that were expunged by the rollback.
                for obj in pending_new:
                    if obj not in db:
                        db.add(obj)
                time.sleep(1.0 * (attempt + 1))
                continue
            logger.error(f"DB commit failed after retries: {exc}")
            raise


def download_entity_assets(
    entity_type: str,
    entity_id: int,
    candidates: Dict[str, AssetCandidate],
    overwrite: bool = False,
) -> Dict[str, Optional[CachedAsset]]:
    """
    Download all asset candidates for an entity.

    Args:
        entity_type: "artist"|"album"|"video"
        entity_id: DB primary key of the entity
        candidates: dict of kind → AssetCandidate (from resolver)
        overwrite: re-download even if cached

    Returns: dict of kind → CachedAsset (or None on failure)
    """
    results: Dict[str, Optional[CachedAsset]] = {}
    for kind, candidate in candidates.items():
        results[kind] = download_asset(entity_type, entity_id, candidate, overwrite=overwrite)
    return results


def get_cached_assets(entity_type: str, entity_id: int) -> List[CachedAsset]:
    """Return all cached assets for an entity."""
    db = SessionLocal()
    try:
        return db.query(CachedAsset).filter(
            CachedAsset.entity_type == entity_type,
            CachedAsset.entity_id == entity_id,
        ).all()
    finally:
        db.close()


def get_cached_asset_path(entity_type: str, entity_id: int, kind: str) -> Optional[str]:
    """Return the local file path for a specific cached asset, or None."""
    db = SessionLocal()
    try:
        asset = db.query(CachedAsset).filter(
            CachedAsset.entity_type == entity_type,
            CachedAsset.entity_id == entity_id,
            CachedAsset.kind == kind,
        ).first()
        if asset and os.path.isfile(asset.local_cache_path):
            return asset.local_cache_path
        return None
    finally:
        db.close()
