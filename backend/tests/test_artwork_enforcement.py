"""
Static enforcement tests for the artwork pipeline.

These tests scan source files at import time to verify that no code outside
artwork_service.py performs raw HTTP image fetches, direct PIL writes, or
unvalidated file copies for artwork assets.  They exist to prevent future
regressions from re-introducing bypass paths.

Also includes ORM validator smoke-tests for MediaAsset and CachedAsset.
"""
import ast
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Source tree helpers
# ---------------------------------------------------------------------------
_BACKEND_ROOT = Path(__file__).resolve().parent.parent  # backend/
_APP_DIR = _BACKEND_ROOT / "app"

# Files that are *allowed* to contain the flagged patterns
_ARTWORK_SERVICE = str((_APP_DIR / "services" / "artwork_service.py").resolve())

# Collect all .py files under app/
def _py_files():
    for root, _dirs, files in os.walk(_APP_DIR):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Test 1: No raw httpx.get / httpx.AsyncClient.get for images outside service
# ---------------------------------------------------------------------------
# We look for httpx.get( or .get( after httpx import lines — a simple heuristic.
# The allowlist contains files that legitimately import httpx for non-artwork use.
_HTTPX_ALLOWLIST = {
    _ARTWORK_SERVICE,
    str((_APP_DIR / "services" / "metadata_resolver.py").resolve()),  # delegates to artwork_service
    str((_APP_DIR / "metadata" / "wikipedia.py").resolve()),  # scrapes article text, not images
    str((_APP_DIR / "metadata" / "musicbrainz.py").resolve()),  # API calls, not images
    str((_APP_DIR / "metadata" / "fanart_tv.py").resolve()),  # returns URLs, not bytes
    str((_APP_DIR / "metadata" / "theaudiodb.py").resolve()),  # returns URLs, not bytes
    str((_APP_DIR / "metadata" / "providers.py").resolve()),  # orchestrator
    # Non-artwork httpx usage (AI providers, scrapers, fingerprinting)
    str((_APP_DIR / "ai" / "fingerprint_service.py").resolve()),  # audio fingerprint API
    str((_APP_DIR / "ai" / "model_catalog.py").resolve()),  # Ollama model management
    str((_APP_DIR / "ai" / "source_resolution.py").resolve()),  # LLM API calls
    str((_APP_DIR / "ai" / "providers" / "claude_provider.py").resolve()),  # Claude API
    str((_APP_DIR / "ai" / "providers" / "gemini_provider.py").resolve()),  # Gemini API
    str((_APP_DIR / "ai" / "providers" / "local_provider.py").resolve()),  # local LLM API
    str((_APP_DIR / "ai" / "providers" / "openai_provider.py").resolve()),  # OpenAI API
    str((_APP_DIR / "services" / "ai_summary.py").resolve()),  # AI summary LLM calls
    str((_APP_DIR / "services" / "artist_album_scraper.py").resolve()),  # scrapes text metadata
    str((_APP_DIR / "metadata" / "providers" / "coverartarchive.py").resolve()),  # returns URLs
    str((_APP_DIR / "metadata" / "providers" / "wikipedia.py").resolve()),  # scrapes article text
}

_HTTPX_RE = re.compile(r"\bhttpx\.(get|post|Client|AsyncClient)\b", re.IGNORECASE)


class TestNoRawHttpxImageFetch:
    """Ensure no file outside the allowlist contains raw httpx fetch calls."""

    def test_no_httpx_outside_allowlist(self):
        violations = []
        for path in _py_files():
            resolved = str(Path(path).resolve())
            if resolved in _HTTPX_ALLOWLIST:
                continue
            src = _read(path)
            if "httpx" not in src:
                continue
            for i, line in enumerate(src.splitlines(), 1):
                if _HTTPX_RE.search(line) and not line.lstrip().startswith("#"):
                    violations.append(f"{path}:{i}: {line.strip()}")
        assert not violations, (
            "Raw httpx calls found outside artwork_service allowlist:\n"
            + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# Test 2: No direct PIL Image.save() for artwork outside service
# ---------------------------------------------------------------------------
# PIL save is allowed inside artwork_service (resize_and_convert) and in
# AI scene analysis (thumbnail extraction from video frames).

_PIL_SAVE_ALLOWLIST = {
    _ARTWORK_SERVICE,
    str((_APP_DIR / "ai" / "scene_analysis.py").resolve()),  # ffmpeg → PIL thumb extraction
}

_PIL_SAVE_RE = re.compile(r"\.save\(", re.IGNORECASE)


class TestNoPILSaveOutside:
    """Ensure .save() on PIL images doesn't happen outside the allowlist."""

    def test_no_pil_save_outside_allowlist(self):
        violations = []
        for path in _py_files():
            resolved = str(Path(path).resolve())
            if resolved in _PIL_SAVE_ALLOWLIST:
                continue
            src = _read(path)
            # Only flag files that actually import PIL
            if "PIL" not in src and "Image" not in src:
                continue
            for i, line in enumerate(src.splitlines(), 1):
                if _PIL_SAVE_RE.search(line) and "Image" in src and not line.lstrip().startswith("#"):
                    violations.append(f"{path}:{i}: {line.strip()}")
        assert not violations, (
            "Direct PIL .save() found outside artwork_service allowlist:\n"
            + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# Test 3: No shutil.copy/copy2 for artwork filenames outside service
# ---------------------------------------------------------------------------
# We flag shutil.copy/copy2 calls in files that deal with artwork paths
# (poster, thumb, fanart, banner, logo, landscape).

_SHUTIL_ALLOWLIST = {
    _ARTWORK_SERVICE,
}

_SHUTIL_RE = re.compile(r"\bshutil\.copy(2|file)?\s*\(")
_ARTWORK_FILENAME_RE = re.compile(
    r"(poster|thumb|fanart|banner|logo|landscape|clearart|clearlogo)\.(jpg|png|webp)",
    re.IGNORECASE,
)


class TestNoRawShutilCopy:
    """Ensure shutil.copy/copy2 is not used for artwork files outside the service."""

    def test_no_shutil_copy_for_artwork(self):
        violations = []
        for path in _py_files():
            resolved = str(Path(path).resolve())
            if resolved in _SHUTIL_ALLOWLIST:
                continue
            src = _read(path)
            if "shutil" not in src:
                continue
            lines = src.splitlines()
            for i, line in enumerate(lines, 1):
                if _SHUTIL_RE.search(line) and not line.lstrip().startswith("#"):
                    # Check surrounding context (±5 lines) for artwork filenames
                    context = "\n".join(lines[max(0, i - 6):i + 5])
                    if _ARTWORK_FILENAME_RE.search(context):
                        violations.append(f"{path}:{i}: {line.strip()}")
        assert not violations, (
            "Raw shutil.copy for artwork found outside artwork_service:\n"
            + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# Test 4: ORM validator on MediaAsset provenance (smoke test)
# ---------------------------------------------------------------------------

class TestMediaAssetValidator:
    """Verify that creating a MediaAsset without provenance triggers a log warning."""

    def test_provenance_warning_on_missing(self, caplog):
        from app.models import MediaAsset
        import logging
        with caplog.at_level(logging.WARNING):
            asset = MediaAsset(
                video_id=1,
                asset_type="poster",
                file_path="/tmp/test.jpg",
                # provenance intentionally set to None → triggers validator
                provenance=None,
            )
        assert any("provenance" in r.message.lower() for r in caplog.records), (
            "Expected a log warning when MediaAsset.provenance is None"
        )

    def test_no_warning_with_provenance(self, caplog):
        from app.models import MediaAsset
        import logging
        with caplog.at_level(logging.WARNING):
            asset = MediaAsset(
                video_id=1,
                asset_type="poster",
                file_path="/tmp/test.jpg",
                provenance="test",
            )
        prov_warnings = [r for r in caplog.records if "provenance" in r.message.lower()]
        assert len(prov_warnings) == 0, (
            "Got unexpected provenance warning when provenance was set"
        )


# ---------------------------------------------------------------------------
# Test 5: CachedAsset status validator smoke test
# ---------------------------------------------------------------------------

class TestCachedAssetValidator:
    """Verify that creating a CachedAsset with bogus status triggers a log warning."""

    def test_status_warning_on_bogus(self, caplog):
        from app.metadata.models import CachedAsset
        import logging
        with caplog.at_level(logging.WARNING):
            asset = CachedAsset(
                entity_type="artist",
                entity_id=1,
                kind="poster",
                source_provider="test",
                status="BOGUS_STATUS",
                local_cache_path="/tmp/test.jpg",
            )
        assert any("status" in r.message.lower() for r in caplog.records), (
            "Expected a log warning when CachedAsset.status is not recognized"
        )

    def test_no_warning_with_valid_status(self, caplog):
        from app.metadata.models import CachedAsset
        import logging
        with caplog.at_level(logging.WARNING):
            asset = CachedAsset(
                entity_type="artist",
                entity_id=1,
                kind="poster",
                source_provider="test",
                status="valid",
                local_cache_path="/tmp/test.jpg",
            )
        status_warnings = [r for r in caplog.records if "status" in r.message.lower()]
        assert len(status_warnings) == 0, (
            "Got unexpected status warning when status was 'valid'"
        )


# ---------------------------------------------------------------------------
# Test 6: download_image is deprecated
# ---------------------------------------------------------------------------

class TestDownloadImageDeprecated:
    """Verify that calling download_image raises a DeprecationWarning."""

    def test_deprecation_warning(self):
        import warnings
        from app.services.metadata_resolver import download_image

        # Mock artwork_service.download_and_validate to avoid actual HTTP
        with patch("app.services.artwork_service.download_and_validate") as mock_dl:
            mock_dl.return_value = MagicMock(valid=True, path="/tmp/fake.jpg")
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                download_image("http://example.com/test.jpg", "/tmp/test.jpg")
                dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
                assert len(dep_warnings) >= 1, (
                    "download_image should raise DeprecationWarning"
                )


# ---------------------------------------------------------------------------
# Test 7: artwork_service public API completeness
# ---------------------------------------------------------------------------

class TestArtworkServiceAPI:
    """Verify all expected public functions exist in artwork_service."""

    def test_public_api_exists(self):
        from app.services import artwork_service as svc
        expected = [
            "validate_file",
            "resize_and_convert",
            "download_and_validate",
            "invalidate_cached_asset",
            "invalidate_media_asset",
            "validate_existing_cached_asset",
            "validate_existing_media_asset",
            "delete_video_artwork",
            "delete_entity_cached_assets",
            "repair_cached_assets",
            "repair_media_assets",
            # Facade API
            "fetch_and_store_entity_asset",
            "fetch_and_store_video_asset",
            "validate_and_store_upload",
            "derive_export_asset_from_cache",
            "guarded_copy",
            "check_media_asset_provenance",
        ]
        missing = [name for name in expected if not hasattr(svc, name)]
        assert not missing, f"Missing from artwork_service public API: {missing}"
