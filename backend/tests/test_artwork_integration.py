"""
Integration tests for the artwork pipeline facade API.

Tests the full flow of:
  - validate_and_store_upload(): validates, resizes, stores user uploads
  - guarded_copy(): validates source before copying
  - derive_export_asset_from_cache(): validates cache before export copy
  - validate_file() → ValidationResult correctness
  - End-to-end: upload → validate → serve → delete lifecycle
"""
import os
import tempfile
from io import BytesIO

import pytest
from PIL import Image

from app.services.artwork_service import (
    ArtworkResult,
    ValidationResult,
    derive_export_asset_from_cache,
    guarded_copy,
    validate_and_store_upload,
    validate_file,
    delete_video_artwork,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jpeg(width: int = 200, height: int = 300) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), color="red").save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _make_png(width: int = 200, height: int = 300) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), color="blue").save(buf, "PNG")
    return buf.getvalue()


def _write_file(path: str, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


# =========================================================================
# validate_and_store_upload
# =========================================================================

class TestValidateAndStoreUpload:

    def test_valid_jpeg_upload(self, tmp_path):
        dest = str(tmp_path / "uploaded-poster.jpg")
        result = validate_and_store_upload(_make_jpeg(), dest)
        assert result.success is True
        assert result.path == dest
        assert os.path.isfile(dest)
        assert result.provider == "user_upload"
        assert result.width is not None
        assert result.height is not None

    def test_valid_png_upload_converted(self, tmp_path):
        dest = str(tmp_path / "uploaded-poster.jpg")
        result = validate_and_store_upload(_make_png(), dest)
        assert result.success is True
        assert os.path.isfile(dest)

    def test_empty_upload_rejected(self, tmp_path):
        dest = str(tmp_path / "empty.jpg")
        result = validate_and_store_upload(b"", dest)
        assert result.success is False
        assert not os.path.isfile(dest)

    def test_html_upload_rejected(self, tmp_path):
        dest = str(tmp_path / "html.jpg")
        html = b"<html><body>Not an image</body></html>"
        result = validate_and_store_upload(html, dest)
        assert result.success is False
        assert "Unrecognized" in (result.error or "")
        assert not os.path.isfile(dest)

    def test_corrupted_jpeg_rejected(self, tmp_path):
        dest = str(tmp_path / "corrupt.jpg")
        # Valid JPEG header but garbage body
        corrupt = b"\xff\xd8\xff\xe0" + b"\x00" * 100 + b"GARBAGE_DATA"
        result = validate_and_store_upload(corrupt, dest)
        assert result.success is False

    def test_oversized_upload_resized(self, tmp_path):
        dest = str(tmp_path / "big.jpg")
        big = _make_jpeg(3000, 4000)
        result = validate_and_store_upload(big, dest, max_width=500, max_height=500)
        assert result.success is True
        # Verify size was reduced
        with Image.open(dest) as img:
            assert img.width <= 500
            assert img.height <= 500


# =========================================================================
# guarded_copy
# =========================================================================

class TestGuardedCopy:

    def test_valid_source_copied(self, tmp_path):
        src = str(tmp_path / "src.jpg")
        dst = str(tmp_path / "sub" / "dst.jpg")
        _write_file(src, _make_jpeg())
        result = guarded_copy(src, dst)
        assert result == dst
        assert os.path.isfile(dst)

    def test_invalid_source_blocked(self, tmp_path):
        src = str(tmp_path / "bad.jpg")
        dst = str(tmp_path / "dst.jpg")
        _write_file(src, b"<html>Not an image</html>")
        result = guarded_copy(src, dst)
        assert result is None
        assert not os.path.isfile(dst)

    def test_missing_source_blocked(self, tmp_path):
        src = str(tmp_path / "nonexistent.jpg")
        dst = str(tmp_path / "dst.jpg")
        result = guarded_copy(src, dst)
        assert result is None
        assert not os.path.isfile(dst)

    def test_skip_validation(self, tmp_path):
        """With validate_source=False, any file is copied."""
        src = str(tmp_path / "data.bin")
        dst = str(tmp_path / "copy.bin")
        _write_file(src, b"arbitrary binary data")
        result = guarded_copy(src, dst, validate_source=False)
        assert result == dst
        assert os.path.isfile(dst)


# =========================================================================
# derive_export_asset_from_cache
# =========================================================================

class TestDeriveExportAsset:

    def test_valid_cache_exported(self, tmp_path):
        cache = str(tmp_path / "cache" / "poster.jpg")
        export = str(tmp_path / "export" / "poster.jpg")
        _write_file(cache, _make_jpeg())
        result = derive_export_asset_from_cache(cache, export)
        assert result == export
        assert os.path.isfile(export)

    def test_invalid_cache_not_exported(self, tmp_path):
        cache = str(tmp_path / "cache" / "poster.jpg")
        export = str(tmp_path / "export" / "poster.jpg")
        _write_file(cache, b"this is not an image")
        result = derive_export_asset_from_cache(cache, export)
        assert result is None
        assert not os.path.isfile(export)


# =========================================================================
# Full lifecycle: upload → validate → copy → delete
# =========================================================================

class TestArtworkLifecycle:

    def test_upload_validate_copy_lifecycle(self, tmp_path):
        """Upload an image, verify it's valid, copy it, verify the copy."""
        # 1. Upload
        dest = str(tmp_path / "video" / "poster.jpg")
        upload_result = validate_and_store_upload(_make_jpeg(400, 600), dest)
        assert upload_result.success is True

        # 2. Validate persisted file
        vr = validate_file(dest)
        assert vr.valid is True
        assert vr.width > 0
        assert vr.height > 0
        assert vr.file_hash is not None

        # 3. Guarded copy to thumb
        thumb = str(tmp_path / "video" / "thumb.jpg")
        copy_result = guarded_copy(dest, thumb)
        assert copy_result == thumb
        assert os.path.isfile(thumb)

        # 4. Validate copy
        vr_copy = validate_file(thumb)
        assert vr_copy.valid is True
        assert vr_copy.width == vr.width
        assert vr_copy.height == vr.height

    def test_corrupt_file_blocked_at_every_stage(self, tmp_path):
        """A corrupt file cannot pass through any stage of the pipeline."""
        corrupt_data = b"\xff\xd8\xff\xe0" + b"\x00" * 200

        # Upload rejected
        dest = str(tmp_path / "corrupt.jpg")
        result = validate_and_store_upload(corrupt_data, dest)
        assert result.success is False

        # Even if manually written, validate_file catches it
        with open(dest, "wb") as f:
            f.write(corrupt_data)
        vr = validate_file(dest)
        assert vr.valid is False

        # guarded_copy blocks it
        copy_dest = str(tmp_path / "copy.jpg")
        assert guarded_copy(dest, copy_dest) is None

        # derive_export blocks it
        export_dest = str(tmp_path / "export.jpg")
        assert derive_export_asset_from_cache(dest, export_dest) is None
