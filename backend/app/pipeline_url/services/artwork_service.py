# AUTO-SEPARATED from services/artwork_service.py for pipeline_url pipeline
# This file is independent — changes here do NOT affect the other pipeline.
"""
Artwork Service — Unified artwork acquisition, validation, and lifecycle.
========================================================================

This module is the **single source of truth** for all artwork operations in
Playarr.  Every code path that downloads, validates, persists, or invalidates
artwork MUST go through this service.

Guarantees:
- No non-image content is ever persisted as artwork.
- Corrupt / invalid assets are detected, deleted, and marked.
- Delete / redownload / reimport flows correctly invalidate stale assets.
- Provenance (source URL, resolved URL, provider, content-type) is always recorded.
- The entity asset cache is the canonical store; Kodi/export layout is derived.

Public Facade API (preferred entry points for all callers):
    fetch_and_store_entity_asset(...)       → ArtworkResult
    fetch_and_store_video_asset(...)        → ArtworkResult
    validate_and_store_upload(...)          → ArtworkResult
    derive_export_asset_from_cache(...)     → str | None
    validate_existing_asset(...)            → bool
    invalidate_asset(...)                   → None
    repair_asset(...)                       → RepairReport

Low-level (used by facade — callers should prefer the facade):
    download_and_validate(url, dest_path, ...)  → ArtworkResult
    validate_file(path)                         → ValidationResult
    resize_and_convert(...)                     → ValidationResult

Enforcement:
    - ORM @validates hooks on MediaAsset/CachedAsset reject records
      without required provenance fields.
    - Static tests scan the codebase for direct httpx.get / PIL / open()
      artwork writes outside this module.
    - Runtime: guarded_copy() must be used for artwork file copies.

Private internals (NEVER import from other modules):
    _detect_format_by_magic, _sha256, _sha256_bytes, _safe_delete,
    httpx.get (only called inside download_and_validate)
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import struct
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Tuple
from urllib.parse import unquote, quote

import httpx
from PIL import Image

logger = logging.getLogger(__name__)

_USER_AGENT = "Playarr/1.0 (artwork-fetcher; +https://github.com/playarr)"

# ---------------------------------------------------------------------------
# Magic byte signatures for common image formats
# ---------------------------------------------------------------------------
_MAGIC_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"RIFF", "webp"),   # RIFF....WEBP  (first 4 bytes, full check below)
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
]


# ---------------------------------------------------------------------------
# Wikimedia Commons URL normalization
# ---------------------------------------------------------------------------

# Matches Commons *file page* URLs like:
#   https://commons.wikimedia.org/wiki/File:Some_Image.jpg
_COMMONS_FILE_PAGE_RE = re.compile(
    r"^https?://commons\.wikimedia\.org/wiki/File:(.+)$", re.IGNORECASE
)

# Matches Wikimedia upload *thumb* URLs like:
#   https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/File.jpg/300px-File.jpg
_WIKIMEDIA_THUMB_RE = re.compile(
    r"^(https?://upload\.wikimedia\.org/wikipedia/\w+)/thumb/(.+?)/\d+px-[^/]+$",
    re.IGNORECASE,
)


def normalize_artwork_url(url: str) -> str:
    """Normalize a potential Wikimedia Commons URL into a direct image URL.

    Handles three cases:
    1. **Commons file page** (``/wiki/File:...``) → resolved via the
       MediaWiki action API to a direct ``upload.wikimedia.org`` URL.
    2. **Wikimedia thumb URL** (``/thumb/.../300px-...``) → stripped to
       the full-resolution original.
    3. **Everything else** → returned unchanged.

    The function is intentionally *synchronous* and cheap: case 1 makes
    a single lightweight JSON API call; cases 2–3 are pure string ops.
    """
    if not url:
        return url

    # Case 1: Commons file page URL → resolve via API
    m = _COMMONS_FILE_PAGE_RE.match(url)
    if m:
        filename = unquote(m.group(1))  # e.g. "Some_Image.jpg"
        resolved = _resolve_commons_file_url(filename)
        if resolved:
            logger.debug(f"Commons URL normalized: {url} → {resolved}")
            return resolved
        # If API resolution fails, leave the URL as-is and let
        # download_and_validate reject the HTML naturally.
        logger.warning(f"Commons URL resolution failed for {url}")
        return url

    # Case 2: thumb URL → full-resolution original
    # Exception: SVG source files — the thumb URL serves a rasterized PNG
    # which is what we want; the raw SVG cannot be processed as raster.
    m = _WIKIMEDIA_THUMB_RE.match(url)
    if m:
        base, path = m.group(1), m.group(2)
        if not path.lower().endswith(".svg"):
            full_url = f"{base}/{path}"
            logger.debug(f"Wikimedia thumb URL normalized: {url} → {full_url}")
            return full_url

    # Case 3: pass-through
    return url


def _resolve_commons_file_url(filename: str) -> Optional[str]:
    """Use the MediaWiki API to resolve a Commons filename to a direct URL.

    Calls ``https://commons.wikimedia.org/w/api.php?action=query&titles=File:<name>&prop=imageinfo&iiprop=url``
    which returns the full ``upload.wikimedia.org`` raw file URL.
    """
    api_url = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "titles": f"File:{filename}",
        "prop": "imageinfo",
        "iiprop": "url",
        "format": "json",
    }
    try:
        resp = httpx.get(
            api_url, params=params, timeout=10,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()
        pages = data.get("query", {}).get("pages", {})
        for _page_id, page in pages.items():
            imageinfo = page.get("imageinfo", [])
            if imageinfo:
                return imageinfo[0].get("url")
    except Exception as e:
        logger.warning(f"Commons API resolution failed for {filename}: {e}")
    return None


def _detect_format_by_magic(data: bytes) -> Optional[str]:
    """Detect image format from first bytes.  Returns format name or None."""
    for sig, fmt in _MAGIC_SIGNATURES:
        if data[:len(sig)] == sig:
            # Extra check for WebP: RIFF header is shared; bytes 8-12 must be "WEBP"
            if fmt == "webp" and data[8:12] != b"WEBP":
                continue
            return fmt
    return None


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """Result of validating a local image file."""
    valid: bool
    path: str
    width: int = 0
    height: int = 0
    format: str = ""          # jpeg|png|webp|gif
    file_size_bytes: int = 0
    file_hash: str = ""       # SHA-256
    error: str = ""


@dataclass
class ArtworkResult:
    """Result of a download-and-validate operation."""
    success: bool
    path: str = ""
    source_url: str = ""
    resolved_url: str = ""    # final URL after redirects
    content_type: str = ""    # HTTP Content-Type
    provider: str = ""
    width: int = 0
    height: int = 0
    format: str = ""          # jpeg|png|webp
    file_size_bytes: int = 0
    file_hash: str = ""       # SHA-256 of original download
    checksum: str = ""        # SHA-256 of final file (after resize)
    error: str = ""


# ---------------------------------------------------------------------------
# Core: SHA-256
# ---------------------------------------------------------------------------

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Core: validate a local image file
# ---------------------------------------------------------------------------

def validate_file(path: str) -> ValidationResult:
    """
    Validate that a file on disk is a real, decodable image.

    Checks:
    1. File exists and is non-empty.
    2. Magic bytes match a known image format.
    3. PIL can open and verify the image without error.
    4. Width/height are sane (> 0).

    Returns a ValidationResult. If invalid, .error explains why.
    """
    if not os.path.isfile(path):
        return ValidationResult(valid=False, path=path, error="File does not exist")

    file_size = os.path.getsize(path)
    if file_size == 0:
        return ValidationResult(valid=False, path=path, error="File is empty (0 bytes)")

    # Read first 16 bytes for magic detection
    with open(path, "rb") as f:
        header = f.read(16)

    detected_fmt = _detect_format_by_magic(header)
    if not detected_fmt:
        # Check if it looks like HTML/XML (common failure mode)
        if header.lstrip()[:1] in (b"<", b"{"):
            return ValidationResult(
                valid=False, path=path,
                error=f"File appears to be text/markup, not an image (first bytes: {header[:20]!r})"
            )
        return ValidationResult(
            valid=False, path=path,
            error=f"Unrecognized image format (magic bytes: {header[:8].hex()})"
        )

    # PIL verify
    try:
        with Image.open(path) as img:
            img.verify()  # raises if corrupt
    except Exception as e:
        return ValidationResult(
            valid=False, path=path,
            error=f"PIL verification failed: {e}"
        )

    # Re-open to get dimensions (verify() invalidates the image object)
    try:
        with Image.open(path) as img:
            w, h = img.size
    except Exception as e:
        return ValidationResult(
            valid=False, path=path,
            error=f"Cannot read image dimensions: {e}"
        )

    if w <= 0 or h <= 0:
        return ValidationResult(
            valid=False, path=path,
            error=f"Invalid dimensions: {w}x{h}"
        )

    file_hash = _sha256(path)
    return ValidationResult(
        valid=True, path=path,
        width=w, height=h,
        format=detected_fmt,
        file_size_bytes=file_size,
        file_hash=file_hash,
    )


# ---------------------------------------------------------------------------
# Core: resize and convert to JPEG
# ---------------------------------------------------------------------------

def resize_and_convert(
    path: str,
    max_width: int,
    max_height: int,
    output_format: str = "JPEG",
    quality: int = 90,
) -> ValidationResult:
    """
    Resize an image to fit within bounds and convert to JPEG.

    If the image is corrupt or cannot be opened, the file is DELETED
    and an invalid ValidationResult is returned.  This ensures no corrupt
    file remains on disk.

    Returns a ValidationResult for the final file.
    """
    try:
        with Image.open(path) as img:
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            w, h = img.size
            if w <= max_width and h <= max_height:
                # Already small enough — just resave as JPEG
                img.save(path, output_format, quality=quality)
            else:
                img.thumbnail((max_width, max_height), Image.LANCZOS)
                img.save(path, output_format, quality=quality)
            logger.debug(f"Resized/converted to {img.size}: {path}")
    except Exception as e:
        logger.error(f"Resize/convert failed for {path}: {e} — deleting corrupt file")
        _safe_delete(path)
        return ValidationResult(
            valid=False, path=path,
            error=f"Resize/convert failed: {e}"
        )

    return validate_file(path)


# ---------------------------------------------------------------------------
# Core: download, validate, resize, persist
# ---------------------------------------------------------------------------

def download_and_validate(
    url: str,
    dest_path: str,
    *,
    max_width: int = 1000,
    max_height: int = 1500,
    provider: str = "",
    overwrite: bool = False,
    timeout: int = 30,
) -> ArtworkResult:
    """
    Download an image from a URL, validate it, resize, and persist.

    This is the ONE function all artwork downloads must funnel through.

    Steps:
    1. HTTP GET with redirect following
    2. Require HTTP 200
    3. Require Content-Type starting with ``image/``
    4. Reject ``text/html``, ``application/xml``, etc.
    5. Verify magic bytes match a known image format
    6. Write to temp file, then PIL open+verify
    7. Resize/convert to JPEG
    8. Move to final destination
    9. Compute SHA-256 checksum

    If ANY step fails, no file is left at dest_path.

    Args:
        url: Remote image URL.
        dest_path: Final file path on disk.
        max_width/max_height: Resize bounds.
        provider: Source provider name for provenance.
        overwrite: If False and dest_path exists, skip download.
        timeout: HTTP timeout in seconds.

    Returns:
        ArtworkResult with success=True on valid image, or error details.
    """
    if not url:
        return ArtworkResult(success=False, error="No URL provided")

    # --- Normalize Wikimedia Commons URLs before download ---
    original_url = url
    url = normalize_artwork_url(url)
    if url != original_url:
        logger.info(f"Artwork URL normalized: {original_url} → {url}")

    if not overwrite and os.path.isfile(dest_path):
        # Existing file — validate it
        vr = validate_file(dest_path)
        if vr.valid:
            logger.debug(f"Artwork already exists and is valid: {dest_path}")
            return ArtworkResult(
                success=True, path=dest_path, source_url=url,
                width=vr.width, height=vr.height, format=vr.format,
                file_size_bytes=vr.file_size_bytes, file_hash=vr.file_hash,
                checksum=vr.file_hash, provider=provider,
            )
        else:
            # Existing file is corrupt — delete and re-download
            logger.warning(f"Existing artwork is invalid ({vr.error}), re-downloading: {dest_path}")
            _safe_delete(dest_path)

    # --- HTTP download ---
    resolved_url = ""
    content_type = ""
    try:
        headers = {"User-Agent": _USER_AGENT}
        resp = httpx.get(url, timeout=timeout, follow_redirects=True, headers=headers)
        resolved_url = str(resp.url)
        content_type = resp.headers.get("content-type", "")
    except Exception as e:
        logger.error(f"Artwork download HTTP error for {url}: {e}")
        return ArtworkResult(
            success=False, source_url=url, provider=provider,
            error=f"HTTP error: {e}",
        )

    # --- HTTP status check ---
    if resp.status_code != 200:
        logger.error(f"Artwork download failed: HTTP {resp.status_code} for {url}")
        return ArtworkResult(
            success=False, source_url=url, resolved_url=resolved_url,
            content_type=content_type, provider=provider,
            error=f"HTTP {resp.status_code}",
        )

    # --- Content-Type validation ---
    ct_lower = content_type.lower().split(";")[0].strip()
    if not ct_lower.startswith("image/"):
        logger.error(
            f"Artwork download rejected: Content-Type '{content_type}' is not an image "
            f"(url={url}, resolved={resolved_url})"
        )
        return ArtworkResult(
            success=False, source_url=url, resolved_url=resolved_url,
            content_type=content_type, provider=provider,
            error=f"Invalid Content-Type: {content_type}",
        )

    body = resp.content
    if not body or len(body) < 8:
        logger.error(f"Artwork download: empty or too-small response ({len(body)} bytes) from {url}")
        return ArtworkResult(
            success=False, source_url=url, resolved_url=resolved_url,
            content_type=content_type, provider=provider,
            error=f"Response too small: {len(body)} bytes",
        )

    # --- Magic byte validation ---
    detected_fmt = _detect_format_by_magic(body)
    if not detected_fmt:
        logger.error(
            f"Artwork download rejected: magic bytes don't match any image format "
            f"(first 16 bytes: {body[:16].hex()}, url={url})"
        )
        return ArtworkResult(
            success=False, source_url=url, resolved_url=resolved_url,
            content_type=content_type, provider=provider,
            error=f"Unrecognized magic bytes: {body[:8].hex()}",
        )

    # --- Write to temp file, then PIL verify ---
    original_hash = _sha256_bytes(body)
    dest_dir = os.path.dirname(dest_path)
    os.makedirs(dest_dir, exist_ok=True)

    # Use a temp file in the same directory to ensure atomic move
    fd, tmp_path = tempfile.mkstemp(dir=dest_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)

        # PIL verify on the temp file
        try:
            with Image.open(tmp_path) as img:
                img.verify()
        except Exception as e:
            logger.error(f"Artwork PIL verification failed for {url}: {e}")
            _safe_delete(tmp_path)
            return ArtworkResult(
                success=False, source_url=url, resolved_url=resolved_url,
                content_type=content_type, provider=provider,
                error=f"PIL verification failed: {e}",
            )

        # Move temp → dest
        if os.path.exists(dest_path):
            _safe_delete(dest_path)
        os.rename(tmp_path, dest_path)
        tmp_path = None  # Prevent cleanup in finally
    finally:
        if tmp_path and os.path.exists(tmp_path):
            _safe_delete(tmp_path)

    # --- Resize and convert ---
    vr = resize_and_convert(dest_path, max_width, max_height)
    if not vr.valid:
        return ArtworkResult(
            success=False, source_url=url, resolved_url=resolved_url,
            content_type=content_type, provider=provider,
            error=f"Post-resize validation failed: {vr.error}",
        )

    logger.info(
        f"Artwork saved: {dest_path} ({vr.width}x{vr.height}, {vr.file_size_bytes}B, "
        f"fmt={vr.format}, provider={provider}, url={url})"
    )

    return ArtworkResult(
        success=True,
        path=dest_path,
        source_url=url,
        resolved_url=resolved_url,
        content_type=content_type,
        provider=provider,
        width=vr.width,
        height=vr.height,
        format=vr.format,
        file_size_bytes=vr.file_size_bytes,
        file_hash=original_hash,
        checksum=vr.file_hash,
    )


# ---------------------------------------------------------------------------
# Asset invalidation
# ---------------------------------------------------------------------------

def invalidate_cached_asset(asset, reason: str, db=None):
    """
    Mark a CachedAsset as invalid, delete the corrupt file, record why.

    Args:
        asset: CachedAsset ORM object.
        reason: Human-readable reason for invalidation.
        db: Optional SQLAlchemy session. If not provided, changes are
            made but not committed.
    """
    logger.warning(f"Invalidating cached asset {asset.id} ({asset.entity_type}/{asset.entity_id}/{asset.kind}): {reason}")
    asset.status = "invalid"
    asset.validation_error = reason
    asset.last_validated_at = datetime.now(timezone.utc)

    # Delete the corrupt file
    if asset.local_cache_path and os.path.isfile(asset.local_cache_path):
        _safe_delete(asset.local_cache_path)

    if db:
        db.flush()


def invalidate_media_asset(asset, reason: str, db=None):
    """
    Mark a MediaAsset as invalid, delete the corrupt file, record why.
    """
    logger.warning(f"Invalidating media asset {asset.id} ({asset.asset_type}): {reason}")
    asset.status = "invalid"
    asset.validation_error = reason
    asset.last_validated_at = datetime.now(timezone.utc)

    if asset.file_path and os.path.isfile(asset.file_path):
        _safe_delete(asset.file_path)

    if db:
        db.flush()


# ---------------------------------------------------------------------------
# Validate an existing asset (on-disk + DB)
# ---------------------------------------------------------------------------

def validate_existing_cached_asset(asset, db=None) -> bool:
    """
    Validate an existing CachedAsset's file on disk.

    If invalid, marks the asset as invalid, deletes the file.
    Returns True if the asset is valid.
    """
    if not asset.local_cache_path or not os.path.isfile(asset.local_cache_path):
        invalidate_cached_asset(asset, "File missing from disk", db)
        return False

    vr = validate_file(asset.local_cache_path)
    if not vr.valid:
        invalidate_cached_asset(asset, vr.error, db)
        return False

    # Update metadata from validation
    asset.status = "valid"
    asset.width = vr.width
    asset.height = vr.height
    asset.file_size_bytes = vr.file_size_bytes
    asset.file_hash = vr.file_hash
    asset.last_validated_at = datetime.now(timezone.utc)
    asset.validation_error = None
    if db:
        db.flush()
    return True


def validate_existing_media_asset(asset, db=None) -> bool:
    """
    Validate an existing MediaAsset's file on disk.

    If invalid, marks the asset as invalid, deletes the file.
    Returns True if the asset is valid.
    """
    if not asset.file_path or not os.path.isfile(asset.file_path):
        invalidate_media_asset(asset, "File missing from disk", db)
        return False

    vr = validate_file(asset.file_path)
    if not vr.valid:
        invalidate_media_asset(asset, vr.error, db)
        return False

    asset.status = "valid"
    asset.width = vr.width
    asset.height = vr.height
    asset.file_size_bytes = vr.file_size_bytes
    asset.file_hash = vr.file_hash
    asset.last_validated_at = datetime.now(timezone.utc)
    asset.validation_error = None
    if db:
        db.flush()
    return True


# ---------------------------------------------------------------------------
# Delete / cleanup helpers
# ---------------------------------------------------------------------------

def _safe_delete(path: str):
    """Delete a file, ignoring errors."""
    try:
        if os.path.isfile(path):
            os.remove(path)
            logger.debug(f"Deleted: {path}")
    except Exception as e:
        logger.warning(f"Failed to delete {path}: {e}")


def delete_video_artwork(video_id: int, db):
    """
    Remove all artwork assets for a video.

    Called when deleting a track. Removes MediaAsset files from disk
    and deletes the DB records.
    """
    from app.models import MediaAsset

    assets = db.query(MediaAsset).filter(MediaAsset.video_id == video_id).all()
    for asset in assets:
        if asset.file_path and os.path.isfile(asset.file_path):
            _safe_delete(asset.file_path)
        db.delete(asset)
    db.flush()
    logger.info(f"Deleted {len(assets)} media assets for video {video_id}")


def delete_entity_cached_assets(entity_type: str, entity_id: int, db):
    """
    Remove all cached assets for an entity (artist/album).

    Called when an entity is orphaned (no more videos reference it).
    """
    from app.metadata.models import CachedAsset

    assets = db.query(CachedAsset).filter(
        CachedAsset.entity_type == entity_type,
        CachedAsset.entity_id == entity_id,
    ).all()
    for asset in assets:
        if asset.local_cache_path and os.path.isfile(asset.local_cache_path):
            _safe_delete(asset.local_cache_path)
        db.delete(asset)
    db.flush()

    # Also clean up the entity cache directory
    from app.config import get_settings
    settings = get_settings()
    cache_dir = settings.asset_cache_dir
    entity_dir = os.path.join(cache_dir, f"{entity_type}s", str(entity_id))
    if os.path.isdir(entity_dir):
        try:
            import shutil
            shutil.rmtree(entity_dir, ignore_errors=True)
            logger.info(f"Removed entity cache dir: {entity_dir}")
        except Exception as e:
            logger.warning(f"Failed to remove entity cache dir {entity_dir}: {e}")

    logger.info(f"Deleted {len(assets)} cached assets for {entity_type}/{entity_id}")


def invalidate_video_derived_assets(video_id: int, db):
    """
    Invalidate derived assets (preview thumbnails, video_thumb) for a
    video when the underlying media file changes (redownload).

    Preserves reusable entity-level artwork (artist/album) that didn't
    change.
    """
    from app.models import MediaAsset

    derived_types = {"video_thumb", "poster"}  # Types that depend on the video file
    assets = db.query(MediaAsset).filter(
        MediaAsset.video_id == video_id,
        MediaAsset.asset_type.in_(derived_types),
    ).all()
    for asset in assets:
        invalidate_media_asset(asset, "Video media replaced (redownload)", db)
    logger.info(f"Invalidated {len(assets)} derived assets for video {video_id}")


# ---------------------------------------------------------------------------
# Repair / bulk validation
# ---------------------------------------------------------------------------

@dataclass
class RepairReport:
    """Summary of a repair operation."""
    total_scanned: int = 0
    valid: int = 0
    invalid: int = 0
    missing: int = 0
    deleted: int = 0
    refetched: int = 0
    errors: list = field(default_factory=list)


def repair_cached_assets(db, refetch: bool = False, log_callback=None) -> RepairReport:
    """
    Scan all CachedAsset records, validate files, mark/delete invalid ones.

    Args:
        db: SQLAlchemy session.
        refetch: If True, attempt to re-download invalid assets from source_url.
        log_callback: Optional callable(message) for progress logging.

    Returns: RepairReport with counts.
    """
    from app.metadata.models import CachedAsset
    from app.metadata.providers.base import AssetCandidate

    report = RepairReport()

    def _log(msg):
        if log_callback:
            log_callback(msg)
        logger.info(msg)

    assets = db.query(CachedAsset).all()
    report.total_scanned = len(assets)
    _log(f"Scanning {len(assets)} cached assets...")

    for asset in assets:
        if not asset.local_cache_path or not os.path.isfile(asset.local_cache_path):
            report.missing += 1
            asset.status = "missing"
            asset.validation_error = "File not found on disk"
            asset.last_validated_at = datetime.now(timezone.utc)

            if refetch and asset.source_url:
                _log(f"  Refetching missing: {asset.entity_type}/{asset.entity_id}/{asset.kind}")
                result = download_and_validate(
                    asset.source_url,
                    asset.local_cache_path,
                    provider=asset.source_provider or asset.provenance or "",
                    overwrite=True,
                )
                if result.success:
                    asset.status = "valid"
                    asset.checksum = result.checksum
                    asset.file_hash = result.file_hash
                    asset.width = result.width
                    asset.height = result.height
                    asset.file_size_bytes = result.file_size_bytes
                    asset.content_type = result.content_type
                    asset.resolved_url = result.resolved_url
                    asset.validation_error = None
                    asset.last_validated_at = datetime.now(timezone.utc)
                    report.refetched += 1
                    _log(f"  Refetched OK: {asset.local_cache_path}")
                else:
                    _log(f"  Refetch failed: {result.error}")
            continue

        vr = validate_file(asset.local_cache_path)
        if vr.valid:
            report.valid += 1
            asset.status = "valid"
            asset.width = vr.width
            asset.height = vr.height
            asset.file_size_bytes = vr.file_size_bytes
            asset.file_hash = vr.file_hash
            asset.last_validated_at = datetime.now(timezone.utc)
            asset.validation_error = None
        else:
            report.invalid += 1
            _log(f"  Invalid: {asset.local_cache_path} — {vr.error}")
            _safe_delete(asset.local_cache_path)
            report.deleted += 1
            asset.status = "invalid"
            asset.validation_error = vr.error
            asset.last_validated_at = datetime.now(timezone.utc)

            if refetch and asset.source_url:
                _log(f"  Refetching invalid: {asset.entity_type}/{asset.entity_id}/{asset.kind}")
                result = download_and_validate(
                    asset.source_url,
                    asset.local_cache_path,
                    provider=asset.source_provider or asset.provenance or "",
                    overwrite=True,
                )
                if result.success:
                    asset.status = "valid"
                    asset.checksum = result.checksum
                    asset.file_hash = result.file_hash
                    asset.width = result.width
                    asset.height = result.height
                    asset.file_size_bytes = result.file_size_bytes
                    asset.content_type = result.content_type
                    asset.resolved_url = result.resolved_url
                    asset.validation_error = None
                    asset.last_validated_at = datetime.now(timezone.utc)
                    report.refetched += 1
                    _log(f"  Refetched OK: {asset.local_cache_path}")
                else:
                    _log(f"  Refetch failed: {result.error}")
                    report.errors.append(f"{asset.entity_type}/{asset.entity_id}/{asset.kind}: {result.error}")

    db.flush()
    _log(f"Repair complete: {report.valid} valid, {report.invalid} invalid, "
         f"{report.missing} missing, {report.deleted} deleted, {report.refetched} refetched")
    return report


def repair_media_assets(db, log_callback=None) -> RepairReport:
    """
    Scan all MediaAsset records, validate files, mark/delete invalid ones.
    """
    from app.models import MediaAsset

    report = RepairReport()

    def _log(msg):
        if log_callback:
            log_callback(msg)
        logger.info(msg)

    assets = db.query(MediaAsset).all()
    report.total_scanned = len(assets)
    _log(f"Scanning {len(assets)} media assets...")

    for asset in assets:
        if not asset.file_path or not os.path.isfile(asset.file_path):
            report.missing += 1
            asset.status = "missing"
            asset.validation_error = "File not found on disk"
            asset.last_validated_at = datetime.now(timezone.utc)
            continue

        vr = validate_file(asset.file_path)
        if vr.valid:
            report.valid += 1
            asset.status = "valid"
            asset.width = vr.width
            asset.height = vr.height
            asset.file_size_bytes = vr.file_size_bytes
            asset.file_hash = vr.file_hash
            asset.last_validated_at = datetime.now(timezone.utc)
            asset.validation_error = None
        else:
            report.invalid += 1
            _log(f"  Invalid: {asset.file_path} — {vr.error}")
            _safe_delete(asset.file_path)
            report.deleted += 1
            asset.status = "invalid"
            asset.validation_error = vr.error
            asset.last_validated_at = datetime.now(timezone.utc)

    db.flush()
    _log(f"Media asset repair: {report.valid} valid, {report.invalid} invalid, "
         f"{report.missing} missing, {report.deleted} deleted")
    return report


# ===========================================================================
# PUBLIC FACADE — Preferred entry points for all callers
# ===========================================================================


def fetch_and_store_entity_asset(
    url: str,
    entity_type: str,
    entity_id: int,
    kind: str,
    *,
    provider: str = "",
    overwrite: bool = False,
) -> ArtworkResult:
    """
    Download and validate artwork for an entity (artist/album/video cache).

    This is the primary entry point for entity-level artwork acquisition.
    Uses the centralized cache layout and size rules.
    """
    from app.config import get_settings

    _SIZE_RULES = {
        ("artist", "poster"):  (1000, 1500),
        ("artist", "fanart"):  (1920, 1080),
        ("album",  "poster"):  (1000, 1000),
        ("video",  "poster"):  (1000, 1500),
        ("video",  "thumb"):   (1280, 720),
    }

    settings = get_settings()
    cache_dir = settings.asset_cache_dir
    dest_dir = os.path.join(cache_dir, f"{entity_type}s", str(entity_id))
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, f"{kind}.jpg")

    max_w, max_h = _SIZE_RULES.get((entity_type, kind), (1000, 1000))
    return download_and_validate(
        url, dest_path,
        max_width=max_w, max_height=max_h,
        provider=provider, overwrite=overwrite,
    )


def fetch_and_store_video_asset(
    url: str,
    video_folder: str,
    folder_name: str,
    asset_type: str,
    *,
    provider: str = "",
    overwrite: bool = False,
) -> ArtworkResult:
    """
    Download and validate artwork for a video's Kodi-layout folder.

    Args:
        url: Remote image URL.
        video_folder: Absolute path to the video's folder on disk.
        folder_name: Base name for the file (e.g. "Artist - Title [1080p]").
        asset_type: "poster" | "thumb" | "fanart".
        provider: Source provider name.
        overwrite: Re-download even if file exists and is valid.
    """
    _SIZE_MAP = {
        "poster": (1000, 1500),
        "thumb":  (1280, 720),
        "fanart": (1920, 1080),
    }

    dest_path = os.path.join(video_folder, f"{folder_name}-{asset_type}.jpg")
    max_w, max_h = _SIZE_MAP.get(asset_type, (1000, 1500))
    return download_and_validate(
        url, dest_path,
        max_width=max_w, max_height=max_h,
        provider=provider, overwrite=overwrite,
    )


def validate_and_store_upload(
    file_bytes: bytes,
    dest_path: str,
    *,
    max_width: int = 1000,
    max_height: int = 1500,
) -> ArtworkResult:
    """
    Validate and persist a user-uploaded image file.

    Performs the same validation chain as download_and_validate but
    from in-memory bytes (no HTTP fetch).  Used by the upload endpoint.

    If validation fails, no file is left on disk.
    """
    if not file_bytes or len(file_bytes) < 8:
        return ArtworkResult(
            success=False, error=f"Upload too small: {len(file_bytes)} bytes",
        )

    # Magic byte check
    detected_fmt = _detect_format_by_magic(file_bytes)
    if not detected_fmt:
        return ArtworkResult(
            success=False,
            error=f"Unrecognized image format (magic bytes: {file_bytes[:8].hex()})",
        )

    original_hash = _sha256_bytes(file_bytes)
    dest_dir = os.path.dirname(dest_path)
    os.makedirs(dest_dir, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=dest_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file_bytes)

        # PIL verify
        try:
            with Image.open(tmp_path) as img:
                img.verify()
        except Exception as e:
            logger.error(f"Upload PIL verification failed: {e}")
            _safe_delete(tmp_path)
            return ArtworkResult(success=False, error=f"Invalid image: {e}")

        # Move to final dest
        if os.path.exists(dest_path):
            _safe_delete(dest_path)
        os.rename(tmp_path, dest_path)
        tmp_path = None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            _safe_delete(tmp_path)

    # Resize
    vr = resize_and_convert(dest_path, max_width, max_height)
    if not vr.valid:
        return ArtworkResult(
            success=False, error=f"Resize failed: {vr.error}",
        )

    return ArtworkResult(
        success=True,
        path=dest_path,
        provider="user_upload",
        width=vr.width,
        height=vr.height,
        format=vr.format,
        file_size_bytes=vr.file_size_bytes,
        file_hash=original_hash,
        checksum=vr.file_hash,
    )


def derive_export_asset_from_cache(
    cache_path: str,
    dest_path: str,
) -> Optional[str]:
    """
    Copy a validated cached asset to an export/Kodi-layout path.

    Validates the source file first.  If invalid, returns None and
    does not copy.  This ensures export paths never contain unvalidated
    artwork.

    Returns dest_path on success, None on failure.
    """
    vr = validate_file(cache_path)
    if not vr.valid:
        logger.warning(f"Cannot derive export asset — source invalid ({vr.error}): {cache_path}")
        return None

    dest_dir = os.path.dirname(dest_path)
    os.makedirs(dest_dir, exist_ok=True)
    try:
        shutil.copy2(cache_path, dest_path)
        logger.debug(f"Derived export asset: {cache_path} → {dest_path}")
        return dest_path
    except Exception as e:
        logger.error(f"Failed to derive export asset: {e}")
        return None


def guarded_copy(
    src_path: str,
    dest_path: str,
    *,
    validate_source: bool = True,
) -> Optional[str]:
    """
    Copy an artwork file with optional validation.

    All artwork file copies in the codebase should use this function
    instead of direct shutil.copy2.  When validate_source=True (default),
    the source is checked via validate_file() before copying.

    Returns dest_path on success, None on failure.
    """
    if validate_source:
        vr = validate_file(src_path)
        if not vr.valid:
            logger.warning(f"guarded_copy: source invalid ({vr.error}), skipping: {src_path}")
            return None

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    try:
        shutil.copy2(src_path, dest_path)
        return dest_path
    except Exception as e:
        logger.error(f"guarded_copy failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Provenance requirement helper
# ---------------------------------------------------------------------------

_REQUIRED_MEDIA_ASSET_FIELDS = {"status", "provenance"}
_REQUIRED_CACHED_ASSET_FIELDS = {"status"}


def check_media_asset_provenance(kwargs: dict) -> None:
    """
    Validate that a MediaAsset creation dict includes required provenance.

    Raises ValueError if required fields are missing.  Called from the
    ORM @validates hook and can be used by callers proactively.
    """
    missing = []
    for field in _REQUIRED_MEDIA_ASSET_FIELDS:
        if not kwargs.get(field):
            missing.append(field)
    if missing:
        logger.warning(
            f"MediaAsset created without required provenance fields: {missing}. "
            f"Provided: { {k: v for k, v in kwargs.items() if k in ('status', 'provenance', 'source_url')} }"
        )
