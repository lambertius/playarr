"""
Tests for the artwork service — unified artwork acquisition, validation, and
lifecycle management.

Covers:
  - validate_file(): accepts real images, rejects HTML, corrupt data, empty files
  - _detect_format_by_magic(): JPEG, PNG, WebP, GIF detection
  - resize_and_convert(): happy path + destructive behaviour on corrupt input
  - download_and_validate(): end-to-end with mocked HTTP (content-type, magic
    bytes, HTML payloads, redirect provenance)
  - invalidation bookkeeping (cached & media assets)
  - Regression: Wikimedia HTML → never persisted as .jpg
"""
import hashlib
import os
import struct
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional
from unittest.mock import MagicMock, patch
import pytest

from PIL import Image

from app.services.artwork_service import (
    ArtworkResult,
    ValidationResult,
    _detect_format_by_magic,
    _sha256,
    download_and_validate,
    invalidate_cached_asset,
    invalidate_media_asset,
    resize_and_convert,
    validate_file,
)


# ---------------------------------------------------------------------------
# Helpers — generate minimal valid images in memory
# ---------------------------------------------------------------------------

def _make_jpeg(width: int = 100, height: int = 100) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), color="red").save(buf, "JPEG", quality=80)
    return buf.getvalue()

def _make_png(width: int = 100, height: int = 100) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), color="blue").save(buf, "PNG")
    return buf.getvalue()

def _make_webp(width: int = 50, height: int = 50) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), color="green").save(buf, "WEBP")
    return buf.getvalue()

def _make_gif(width: int = 10, height: int = 10) -> bytes:
    buf = BytesIO()
    Image.new("P", (width, height)).save(buf, "GIF")
    return buf.getvalue()


# =========================================================================
# _detect_format_by_magic
# =========================================================================

class TestDetectFormatByMagic:

    def test_jpeg(self):
        assert _detect_format_by_magic(_make_jpeg()[:16]) == "jpeg"

    def test_png(self):
        assert _detect_format_by_magic(_make_png()[:16]) == "png"

    def test_webp(self):
        data = _make_webp()
        assert _detect_format_by_magic(data[:16]) == "webp"

    def test_gif87a(self):
        assert _detect_format_by_magic(b"GIF87a" + b"\x00" * 10) == "gif"

    def test_gif89a(self):
        assert _detect_format_by_magic(_make_gif()[:16]) == "gif"

    def test_html_rejected(self):
        assert _detect_format_by_magic(b"<!DOCTYPE html>") is None

    def test_json_rejected(self):
        assert _detect_format_by_magic(b'{"error":"not found"}') is None

    def test_random_bytes_rejected(self):
        assert _detect_format_by_magic(b"\x00\x01\x02\x03\x04\x05\x06\x07") is None

    def test_riff_non_webp_rejected(self):
        # RIFF header but not WEBP
        data = b"RIFF" + b"\x00\x00\x00\x00" + b"AVI " + b"\x00" * 4
        assert _detect_format_by_magic(data) is None


# =========================================================================
# validate_file
# =========================================================================

class TestValidateFile:

    def test_valid_jpeg(self, tmp_path):
        p = str(tmp_path / "test.jpg")
        with open(p, "wb") as f:
            f.write(_make_jpeg(200, 150))
        vr = validate_file(p)
        assert vr.valid is True
        assert vr.width == 200
        assert vr.height == 150
        assert vr.format == "jpeg"
        assert vr.file_size_bytes > 0
        assert len(vr.file_hash) == 64  # SHA-256 hex

    def test_valid_png(self, tmp_path):
        p = str(tmp_path / "test.png")
        with open(p, "wb") as f:
            f.write(_make_png(80, 60))
        vr = validate_file(p)
        assert vr.valid is True
        assert vr.format == "png"
        assert vr.width == 80

    def test_nonexistent_file(self):
        vr = validate_file("/no/such/file.jpg")
        assert vr.valid is False
        assert "does not exist" in vr.error

    def test_empty_file(self, tmp_path):
        p = str(tmp_path / "empty.jpg")
        with open(p, "wb") as f:
            pass  # 0 bytes
        vr = validate_file(p)
        assert vr.valid is False
        assert "empty" in vr.error.lower()

    def test_html_file_rejected(self, tmp_path):
        p = str(tmp_path / "fake.jpg")
        with open(p, "w") as f:
            f.write("<!DOCTYPE html><html><body>Not an image</body></html>")
        vr = validate_file(p)
        assert vr.valid is False
        assert "text/markup" in vr.error.lower() or "magic" in vr.error.lower()

    def test_json_file_rejected(self, tmp_path):
        p = str(tmp_path / "data.jpg")
        with open(p, "w") as f:
            f.write('{"error": "not found", "status": 404}')
        vr = validate_file(p)
        assert vr.valid is False

    def test_truncated_jpeg_rejected(self, tmp_path):
        """JPEG header but truncated data → PIL should reject."""
        data = _make_jpeg()
        p = str(tmp_path / "truncated.jpg")
        with open(p, "wb") as f:
            f.write(data[:50])  # Only first 50 bytes — too short
        vr = validate_file(p)
        assert vr.valid is False

    def test_random_binary_rejected(self, tmp_path):
        p = str(tmp_path / "random.bin")
        with open(p, "wb") as f:
            f.write(os.urandom(1024))
        vr = validate_file(p)
        assert vr.valid is False


# =========================================================================
# resize_and_convert
# =========================================================================

class TestResizeAndConvert:

    def test_resize_large_image(self, tmp_path):
        p = str(tmp_path / "big.jpg")
        with open(p, "wb") as f:
            f.write(_make_jpeg(2000, 3000))
        vr = resize_and_convert(p, max_width=500, max_height=750)
        assert vr.valid is True
        assert vr.width <= 500
        assert vr.height <= 750

    def test_small_image_kept(self, tmp_path):
        p = str(tmp_path / "small.jpg")
        with open(p, "wb") as f:
            f.write(_make_jpeg(100, 100))
        vr = resize_and_convert(p, max_width=500, max_height=500)
        assert vr.valid is True
        assert vr.width == 100
        assert vr.height == 100

    def test_corrupt_file_deleted(self, tmp_path):
        """Corrupt data → resize deletes the file (destructive failure)."""
        p = str(tmp_path / "corrupt.jpg")
        with open(p, "wb") as f:
            f.write(b"\xff\xd8\xff" + os.urandom(100))  # JPEG header + junk
        vr = resize_and_convert(p, max_width=500, max_height=500)
        assert vr.valid is False
        assert not os.path.exists(p), "Corrupt file should be deleted"

    def test_png_converted_to_jpeg(self, tmp_path):
        p = str(tmp_path / "input.png")
        with open(p, "wb") as f:
            f.write(_make_png(100, 100))
        vr = resize_and_convert(p, max_width=500, max_height=500, output_format="JPEG")
        assert vr.valid is True
        assert vr.format == "jpeg"


# =========================================================================
# download_and_validate (mocked HTTP)
# =========================================================================

def _make_httpx_response(
    status_code: int = 200,
    content: bytes = b"",
    content_type: str = "image/jpeg",
    url: str = "https://example.com/image.jpg",
):
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.headers = {"content-type": content_type}
    resp.url = url
    return resp


class TestDownloadAndValidate:

    def test_success_jpeg(self, tmp_path):
        dest = str(tmp_path / "poster.jpg")
        jpeg_data = _make_jpeg(300, 300)

        with patch("app.services.artwork_service.httpx.get") as mock_get:
            mock_get.return_value = _make_httpx_response(
                content=jpeg_data,
                content_type="image/jpeg",
            )
            result = download_and_validate(
                "https://example.com/image.jpg", dest, provider="test",
            )

        assert result.success is True
        assert os.path.isfile(dest)
        assert result.provider == "test"
        assert result.width > 0
        assert len(result.file_hash) == 64

    def test_html_content_type_rejected(self, tmp_path):
        dest = str(tmp_path / "poster.jpg")

        with patch("app.services.artwork_service.httpx.get") as mock_get:
            mock_get.return_value = _make_httpx_response(
                content=b"<!DOCTYPE html><html></html>",
                content_type="text/html; charset=utf-8",
            )
            result = download_and_validate(
                "https://example.com/page.html", dest,
            )

        assert result.success is False
        assert "Content-Type" in result.error
        assert not os.path.isfile(dest)

    def test_html_body_with_image_content_type_rejected(self, tmp_path):
        """Server lies about content-type but body is HTML → magic byte check rejects."""
        dest = str(tmp_path / "poster.jpg")

        with patch("app.services.artwork_service.httpx.get") as mock_get:
            mock_get.return_value = _make_httpx_response(
                content=b"<!DOCTYPE html><html><body>Error</body></html>",
                content_type="image/jpeg",
            )
            result = download_and_validate(
                "https://example.com/image.jpg", dest,
            )

        assert result.success is False
        assert "magic" in result.error.lower()
        assert not os.path.isfile(dest)

    def test_http_404_rejected(self, tmp_path):
        dest = str(tmp_path / "poster.jpg")

        with patch("app.services.artwork_service.httpx.get") as mock_get:
            mock_get.return_value = _make_httpx_response(
                status_code=404, content=b"Not Found",
                content_type="text/plain",
            )
            result = download_and_validate(
                "https://example.com/missing.jpg", dest,
            )

        assert result.success is False
        assert "404" in result.error

    def test_empty_url_rejected(self, tmp_path):
        dest = str(tmp_path / "poster.jpg")
        result = download_and_validate("", dest)
        assert result.success is False
        assert "No URL" in result.error

    def test_http_exception_caught(self, tmp_path):
        dest = str(tmp_path / "poster.jpg")

        with patch("app.services.artwork_service.httpx.get") as mock_get:
            mock_get.side_effect = httpx.ConnectError("connection refused")
            result = download_and_validate(
                "https://example.com/image.jpg", dest,
            )

        assert result.success is False
        assert "HTTP error" in result.error

    def test_existing_valid_file_skipped(self, tmp_path):
        """If dest already has a valid image and overwrite=False, skip download."""
        dest = str(tmp_path / "poster.jpg")
        with open(dest, "wb") as f:
            f.write(_make_jpeg(100, 100))

        with patch("app.services.artwork_service.httpx.get") as mock_get:
            result = download_and_validate(
                "https://example.com/image.jpg", dest, overwrite=False,
            )
            mock_get.assert_not_called()

        assert result.success is True

    def test_existing_corrupt_file_re_downloaded(self, tmp_path):
        """If dest has corrupt data and overwrite=False, re-download."""
        dest = str(tmp_path / "poster.jpg")
        with open(dest, "w") as f:
            f.write("<!DOCTYPE html><html></html>")

        jpeg_data = _make_jpeg(100, 100)
        with patch("app.services.artwork_service.httpx.get") as mock_get:
            mock_get.return_value = _make_httpx_response(
                content=jpeg_data, content_type="image/jpeg",
            )
            result = download_and_validate(
                "https://example.com/image.jpg", dest, overwrite=False,
            )
            mock_get.assert_called_once()

        assert result.success is True
        assert os.path.isfile(dest)

    def test_overwrite_replaces_valid_file(self, tmp_path):
        """overwrite=True forces re-download even if valid."""
        dest = str(tmp_path / "poster.jpg")
        with open(dest, "wb") as f:
            f.write(_make_jpeg(50, 50))
        old_hash = _sha256(dest)

        new_data = _make_jpeg(200, 200)
        with patch("app.services.artwork_service.httpx.get") as mock_get:
            mock_get.return_value = _make_httpx_response(
                content=new_data, content_type="image/jpeg",
            )
            result = download_and_validate(
                "https://example.com/image.jpg", dest, overwrite=True,
            )
            mock_get.assert_called_once()

        assert result.success is True

    def test_png_download_accepted(self, tmp_path):
        dest = str(tmp_path / "art.png")
        png_data = _make_png(100, 100)

        with patch("app.services.artwork_service.httpx.get") as mock_get:
            mock_get.return_value = _make_httpx_response(
                content=png_data, content_type="image/png",
            )
            result = download_and_validate(
                "https://example.com/art.png", dest,
            )

        assert result.success is True

    def test_provenance_recorded(self, tmp_path):
        """resolved_url, content_type, and provider are tracked."""
        dest = str(tmp_path / "poster.jpg")
        jpeg_data = _make_jpeg(100, 100)

        with patch("app.services.artwork_service.httpx.get") as mock_get:
            mock_get.return_value = _make_httpx_response(
                content=jpeg_data,
                content_type="image/jpeg",
                url="https://cdn.example.com/final.jpg",
            )
            result = download_and_validate(
                "https://example.com/redirect.jpg", dest, provider="musicbrainz",
            )

        assert result.resolved_url == "https://cdn.example.com/final.jpg"
        assert result.content_type == "image/jpeg"
        assert result.provider == "musicbrainz"


# =========================================================================
# Invalidation
# =========================================================================

class TestInvalidation:

    def test_invalidate_cached_asset_deletes_file(self, tmp_path):
        p = str(tmp_path / "artist_thumb.jpg")
        with open(p, "wb") as f:
            f.write(_make_jpeg())

        asset = MagicMock()
        asset.id = 42
        asset.entity_type = "artist"
        asset.entity_id = 1
        asset.kind = "thumb"
        asset.local_cache_path = p
        asset.status = "valid"

        invalidate_cached_asset(asset, "test invalidation")

        assert asset.status == "invalid"
        assert asset.validation_error == "test invalidation"
        assert not os.path.exists(p), "File should be deleted on invalidation"

    def test_invalidate_media_asset_deletes_file(self, tmp_path):
        p = str(tmp_path / "poster.jpg")
        with open(p, "wb") as f:
            f.write(_make_jpeg())

        asset = MagicMock()
        asset.id = 99
        asset.asset_type = "poster"
        asset.video_id = 5
        asset.file_path = p
        asset.status = "valid"

        invalidate_media_asset(asset, "corrupt data")

        assert asset.status == "invalid"
        assert asset.validation_error == "corrupt data"
        assert not os.path.exists(p)


# =========================================================================
# Regression tests
# =========================================================================

class TestRegressions:
    """Reproduce known failures from production and verify they're now caught."""

    def test_wikimedia_html_redirect_never_saved(self, tmp_path):
        """
        Wikimedia Commons sometimes returns 200 + text/html for disambiguation
        or redirect pages instead of an actual image.  The old pipeline saved
        this as poster.jpg.  Now it must be rejected.
        """
        dest = str(tmp_path / "poster.jpg")
        html = (
            b'<!DOCTYPE html>\n<html><head><title>File:Cover.jpg</title></head>'
            b'<body>This is Not an Image</body></html>'
        )

        with patch("app.services.artwork_service.httpx.get") as mock_get:
            mock_get.return_value = _make_httpx_response(
                content=html,
                content_type="text/html; charset=utf-8",
                url="https://commons.wikimedia.org/wiki/File:Cover.jpg",
            )
            result = download_and_validate(
                "https://commons.wikimedia.org/wiki/File:Cover.jpg", dest,
                provider="wikipedia",
            )

        assert result.success is False
        assert not os.path.isfile(dest)

    def test_server_claims_image_but_sends_xml(self, tmp_path):
        """
        Some CDNs return XML error pages with a misleading content-type.
        Magic bytes catch this.
        """
        dest = str(tmp_path / "art.jpg")
        xml_body = b'<?xml version="1.0"?><Error><Code>NoSuchKey</Code></Error>'

        with patch("app.services.artwork_service.httpx.get") as mock_get:
            mock_get.return_value = _make_httpx_response(
                content=xml_body,
                content_type="image/jpeg",  # Lie!
            )
            result = download_and_validate(
                "https://cdn.example.com/cover.jpg", dest,
            )

        assert result.success is False
        assert not os.path.isfile(dest)

    def test_corrupt_cached_poster_replaced_on_reimport(self, tmp_path):
        """
        A previously cached corrupt poster.jpg is replaced when reimport
        triggers download_and_validate with overwrite=False (because validate_file
        detects corruption and forces re-download).
        """
        dest = str(tmp_path / "poster.jpg")
        # Write corrupt "image" (actually HTML)
        with open(dest, "w") as f:
            f.write("<html><body>Error</body></html>")

        good_jpeg = _make_jpeg(300, 300)
        with patch("app.services.artwork_service.httpx.get") as mock_get:
            mock_get.return_value = _make_httpx_response(
                content=good_jpeg, content_type="image/jpeg",
            )
            result = download_and_validate(
                "https://example.com/poster.jpg", dest, overwrite=False,
            )

        assert result.success is True
        # Verify it's a real image now
        vr = validate_file(dest)
        assert vr.valid is True
        assert vr.format == "jpeg"


# Need httpx for test_http_exception_caught
import httpx
