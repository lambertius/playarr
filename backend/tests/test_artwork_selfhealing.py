"""
Tests for the self-healing artwork pipeline additions:

  - normalize_artwork_url(): Commons file-page → direct URL,
    thumb → full-resolution, pass-through
  - _resolve_commons_file_url(): MediaWiki API integration
  - Startup repair (light/full modes)
  - Library-scan repair (_validate_video_entity_artwork)
"""
import os
import tempfile
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from PIL import Image

from app.services.artwork_service import (
    normalize_artwork_url,
    _resolve_commons_file_url,
    _COMMONS_FILE_PAGE_RE,
    _WIKIMEDIA_THUMB_RE,
    validate_file,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jpeg(width: int = 100, height: int = 100) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), color="red").save(buf, "JPEG", quality=80)
    return buf.getvalue()


def _write_temp(content: bytes, suffix: str = ".jpg") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.write(fd, content)
    os.close(fd)
    return path


# =========================================================================
# Regex patterns
# =========================================================================

class TestCommonsFilePageRegex:

    def test_matches_standard_url(self):
        url = "https://commons.wikimedia.org/wiki/File:Cosmic_Love.jpg"
        assert _COMMONS_FILE_PAGE_RE.match(url)

    def test_matches_https(self):
        url = "https://commons.wikimedia.org/wiki/File:Foo.png"
        assert _COMMONS_FILE_PAGE_RE.match(url)

    def test_matches_http(self):
        url = "http://commons.wikimedia.org/wiki/File:Foo.png"
        assert _COMMONS_FILE_PAGE_RE.match(url)

    def test_captures_filename(self):
        url = "https://commons.wikimedia.org/wiki/File:Some_Image%20Name.jpg"
        m = _COMMONS_FILE_PAGE_RE.match(url)
        assert m.group(1) == "Some_Image%20Name.jpg"

    def test_no_match_upload_url(self):
        url = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Foo.jpg"
        assert _COMMONS_FILE_PAGE_RE.match(url) is None

    def test_no_match_other_domain(self):
        url = "https://en.wikipedia.org/wiki/File:Foo.jpg"
        assert _COMMONS_FILE_PAGE_RE.match(url) is None


class TestWikimediaThumbRegex:

    def test_matches_standard_thumb(self):
        url = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Foo.jpg/300px-Foo.jpg"
        m = _WIKIMEDIA_THUMB_RE.match(url)
        assert m is not None
        assert m.group(1) == "https://upload.wikimedia.org/wikipedia/commons"
        assert m.group(2) == "a/ab/Foo.jpg"

    def test_matches_en_thumb(self):
        url = "https://upload.wikimedia.org/wikipedia/en/thumb/x/y/Bar.png/200px-Bar.png"
        m = _WIKIMEDIA_THUMB_RE.match(url)
        assert m is not None

    def test_no_match_full_resolution(self):
        url = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Foo.jpg"
        assert _WIKIMEDIA_THUMB_RE.match(url) is None


# =========================================================================
# normalize_artwork_url()
# =========================================================================

class TestNormalizeArtworkUrl:

    def test_empty_url_passthrough(self):
        assert normalize_artwork_url("") == ""

    def test_none_passthrough(self):
        assert normalize_artwork_url(None) is None

    def test_non_wikimedia_passthrough(self):
        url = "https://coverartarchive.org/release/abc/front-500.jpg"
        assert normalize_artwork_url(url) == url

    def test_regular_upload_url_passthrough(self):
        url = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Foo.jpg"
        assert normalize_artwork_url(url) == url

    @patch("app.services.artwork_service._resolve_commons_file_url")
    def test_commons_file_page_resolved(self, mock_resolve):
        mock_resolve.return_value = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Cosmic_Love.jpg"
        url = "https://commons.wikimedia.org/wiki/File:Cosmic_Love.jpg"
        result = normalize_artwork_url(url)
        assert result == "https://upload.wikimedia.org/wikipedia/commons/a/ab/Cosmic_Love.jpg"
        mock_resolve.assert_called_once_with("Cosmic_Love.jpg")

    @patch("app.services.artwork_service._resolve_commons_file_url")
    def test_commons_file_page_url_decoded(self, mock_resolve):
        """Percent-encoded filenames should be decoded before API call."""
        mock_resolve.return_value = "https://upload.wikimedia.org/something.jpg"
        url = "https://commons.wikimedia.org/wiki/File:My%20Image%20Name.jpg"
        normalize_artwork_url(url)
        mock_resolve.assert_called_once_with("My Image Name.jpg")

    @patch("app.services.artwork_service._resolve_commons_file_url")
    def test_commons_file_page_api_failure_returns_original(self, mock_resolve):
        """If API resolution fails, return original URL rather than crashing."""
        mock_resolve.return_value = None
        url = "https://commons.wikimedia.org/wiki/File:Missing.jpg"
        assert normalize_artwork_url(url) == url

    def test_thumb_url_to_full_resolution(self):
        url = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Foo.jpg/300px-Foo.jpg"
        expected = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Foo.jpg"
        assert normalize_artwork_url(url) == expected

    def test_thumb_url_en_wikipedia(self):
        url = "https://upload.wikimedia.org/wikipedia/en/thumb/x/y/Bar.png/200px-Bar.png"
        expected = "https://upload.wikimedia.org/wikipedia/en/x/y/Bar.png"
        assert normalize_artwork_url(url) == expected


# =========================================================================
# _resolve_commons_file_url() — with mocked HTTP
# =========================================================================

class TestResolveCommonsFileUrl:

    @patch("app.services.artwork_service.httpx")
    def test_success(self, mock_httpx):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "query": {
                "pages": {
                    "12345": {
                        "imageinfo": [
                            {"url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Test.jpg"}
                        ]
                    }
                }
            }
        }
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        result = _resolve_commons_file_url("Test.jpg")
        assert result == "https://upload.wikimedia.org/wikipedia/commons/a/ab/Test.jpg"

        # Verify API was called with correct params
        call_kwargs = mock_httpx.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["titles"] == "File:Test.jpg"
        assert params["prop"] == "imageinfo"

    @patch("app.services.artwork_service.httpx")
    def test_missing_page(self, mock_httpx):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "query": {"pages": {"-1": {"missing": True}}}
        }
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        result = _resolve_commons_file_url("Nonexistent.jpg")
        assert result is None

    @patch("app.services.artwork_service.httpx")
    def test_http_error(self, mock_httpx):
        mock_httpx.get.side_effect = Exception("Connection refused")
        result = _resolve_commons_file_url("Whatever.jpg")
        assert result is None

    @patch("app.services.artwork_service.httpx")
    def test_empty_imageinfo(self, mock_httpx):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "query": {"pages": {"12345": {"imageinfo": []}}}
        }
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        result = _resolve_commons_file_url("NoImageInfo.jpg")
        assert result is None


# =========================================================================
# Startup repair function
# =========================================================================

class TestStartupArtworkRepair:
    """Tests _run_startup_artwork_repair from main.py."""

    def test_off_mode_skips(self):
        from app.main import _run_startup_artwork_repair
        # Should not raise, should not access DB
        _run_startup_artwork_repair("off")

    @patch("app.database.SessionLocal")
    def test_light_mode_no_suspects(self, mock_session_cls):
        from app.main import _run_startup_artwork_repair

        mock_db = MagicMock()
        mock_session_cls.return_value = mock_db
        mock_db.query.return_value.filter.return_value.all.return_value = []

        _run_startup_artwork_repair("light")
        # Should query, find nothing, close
        mock_db.close.assert_called()

    @patch("app.services.artwork_service.validate_file")
    @patch("app.database.SessionLocal")
    def test_light_mode_purges_corrupt(self, mock_session_cls, mock_validate):
        from app.main import _run_startup_artwork_repair

        # Create a fake corrupt file
        corrupt_path = _write_temp(b"<html>Not an image</html>")
        try:
            mock_db = MagicMock()
            mock_session_cls.return_value = mock_db

            fake_asset = MagicMock()
            fake_asset.local_cache_path = corrupt_path
            fake_asset.status = "valid"
            fake_asset.last_validated_at = None  # suspicious: never validated

            mock_db.query.return_value.filter.return_value.all.return_value = [fake_asset]

            mock_vr = MagicMock()
            mock_vr.valid = False
            mock_vr.error = "HTML content detected"
            mock_validate.return_value = mock_vr

            _run_startup_artwork_repair("light")

            assert fake_asset.status == "invalid"
            assert fake_asset.validation_error == "HTML content detected"
            mock_db.commit.assert_called()
        finally:
            if os.path.exists(corrupt_path):
                os.unlink(corrupt_path)

    @patch("app.services.artwork_service.repair_cached_assets")
    @patch("app.database.SessionLocal")
    def test_full_mode_calls_repair(self, mock_session_cls, mock_repair):
        from app.main import _run_startup_artwork_repair

        mock_db = MagicMock()
        mock_session_cls.return_value = mock_db
        mock_report = MagicMock()
        mock_repair.return_value = mock_report

        _run_startup_artwork_repair("full")

        mock_repair.assert_called_once()
        call_kwargs = mock_repair.call_args
        assert call_kwargs.kwargs.get("refetch") is True or (len(call_kwargs.args) > 1 and call_kwargs.args[1] is True)


# =========================================================================
# Corrupt file detection via validate_file (regression)
# =========================================================================

class TestCorruptCacheDetection:

    def test_html_as_jpg_detected(self):
        """HTML content saved as .jpg is rejected."""
        path = _write_temp(b"<!DOCTYPE html><html><head></head><body>Wikimedia</body></html>")
        try:
            vr = validate_file(path)
            assert not vr.valid
            assert vr.error  # Should have a descriptive error
        finally:
            os.unlink(path)

    def test_empty_file_detected(self):
        path = _write_temp(b"")
        try:
            vr = validate_file(path)
            assert not vr.valid
        finally:
            os.unlink(path)

    def test_valid_jpeg_accepted(self):
        path = _write_temp(_make_jpeg())
        try:
            vr = validate_file(path)
            assert vr.valid
            assert vr.width == 100
            assert vr.height == 100
        finally:
            os.unlink(path)

    def test_truncated_jpeg_detected(self):
        path = _write_temp(_make_jpeg()[:50])
        try:
            vr = validate_file(path)
            assert not vr.valid
        finally:
            os.unlink(path)


# =========================================================================
# _validate_video_entity_artwork (library-scan repair)
# =========================================================================

class TestValidateVideoEntityArtwork:

    @patch("app.services.artwork_service._safe_delete")
    @patch("app.services.artwork_service.validate_file")
    def test_purges_corrupt_artist_artwork(self, mock_validate, mock_delete):
        from app.tasks import _validate_video_entity_artwork

        corrupt_path = "/fake/cache/poster.jpg"

        mock_db = MagicMock()
        mock_video = MagicMock()
        mock_video.artist_entity_id = 42
        mock_video.album_entity_id = None

        fake_asset = MagicMock()
        fake_asset.local_cache_path = corrupt_path
        fake_asset.status = "valid"

        mock_db.query.return_value.filter.return_value.all.return_value = [fake_asset]

        mock_vr = MagicMock()
        mock_vr.valid = False
        mock_vr.error = "HTML content detected"
        mock_validate.return_value = mock_vr

        with patch("os.path.isfile", return_value=True):
            _validate_video_entity_artwork(mock_db, mock_video, job_id=1)

        assert fake_asset.status == "invalid"
        assert fake_asset.validation_error == "HTML content detected"
        mock_delete.assert_called_once_with(corrupt_path)
        mock_db.flush.assert_called()

    @patch("app.services.artwork_service._safe_delete")
    @patch("app.services.artwork_service.validate_file")
    def test_keeps_valid_artwork(self, mock_validate, mock_delete):
        from app.tasks import _validate_video_entity_artwork

        mock_db = MagicMock()
        mock_video = MagicMock()
        mock_video.artist_entity_id = 10
        mock_video.album_entity_id = None

        fake_asset = MagicMock()
        fake_asset.local_cache_path = "/fake/cache/poster.jpg"
        fake_asset.status = "valid"

        mock_db.query.return_value.filter.return_value.all.return_value = [fake_asset]

        mock_vr = MagicMock()
        mock_vr.valid = True
        mock_vr.width = 500
        mock_vr.height = 500
        mock_vr.file_size_bytes = 12345
        mock_vr.file_hash = "abc123"
        mock_validate.return_value = mock_vr

        with patch("os.path.isfile", return_value=True):
            _validate_video_entity_artwork(mock_db, mock_video, job_id=1)

        mock_delete.assert_not_called()
        mock_db.flush.assert_called()

    @patch("app.services.artwork_service.validate_file")
    def test_marks_missing_file(self, mock_validate):
        from app.tasks import _validate_video_entity_artwork

        mock_db = MagicMock()
        mock_video = MagicMock()
        mock_video.artist_entity_id = 5
        mock_video.album_entity_id = None

        fake_asset = MagicMock()
        fake_asset.local_cache_path = "/nonexistent/poster.jpg"
        fake_asset.status = "valid"

        mock_db.query.return_value.filter.return_value.all.return_value = [fake_asset]

        with patch("os.path.isfile", return_value=False):
            _validate_video_entity_artwork(mock_db, mock_video, job_id=1)

        assert fake_asset.status == "missing"
        mock_validate.assert_not_called()

    def test_no_entities_is_noop(self):
        from app.tasks import _validate_video_entity_artwork

        mock_db = MagicMock()
        mock_video = MagicMock()
        mock_video.artist_entity_id = None
        mock_video.album_entity_id = None

        _validate_video_entity_artwork(mock_db, mock_video, job_id=1)
        mock_db.query.assert_not_called()

    @patch("app.services.artwork_service._safe_delete")
    @patch("app.services.artwork_service.validate_file")
    def test_handles_both_artist_and_album(self, mock_validate, mock_delete):
        from app.tasks import _validate_video_entity_artwork

        mock_db = MagicMock()
        mock_video = MagicMock()
        mock_video.artist_entity_id = 1
        mock_video.album_entity_id = 2

        asset_a = MagicMock()
        asset_a.local_cache_path = "/a/poster.jpg"
        asset_a.status = "valid"

        asset_b = MagicMock()
        asset_b.local_cache_path = "/b/poster.jpg"
        asset_b.status = "valid"

        # Return different assets for each entity query
        mock_db.query.return_value.filter.return_value.all.side_effect = [
            [asset_a], [asset_b]
        ]

        mock_vr = MagicMock()
        mock_vr.valid = True
        mock_vr.width = 100
        mock_vr.height = 100
        mock_vr.file_size_bytes = 5000
        mock_vr.file_hash = "xyz"
        mock_validate.return_value = mock_vr

        with patch("os.path.isfile", return_value=True):
            _validate_video_entity_artwork(mock_db, mock_video, job_id=1)

        # validate_file called for both assets
        assert mock_validate.call_count == 2
        mock_db.flush.assert_called()
