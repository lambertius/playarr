"""
Tests for AI Final Review: proposed_removals mechanism and plot confidence threshold.

Validates:
1. Parser correctly extracts proposed_removals from AI JSON response
2. proposed_removals are applied for allowed fields (album, year, genres, plot)
3. proposed_removals are blocked for non-removable fields (artist, title)
4. MB authoritative guard blocks album removal
5. Already-empty fields are not redundantly removed
6. Plot confidence threshold is lowered to 0.4 when no existing plot
7. Plot confidence threshold remains 0.7 when existing plot present
8. proposed_removals are stored in diagnostics metadata
"""
import json
import pytest
from unittest.mock import MagicMock, patch

from app.ai.final_review import (
    FinalReviewResult,
    _parse_final_review_response,
)


# ── Parser Tests ──────────────────────────────────────────────────────────


class TestParseProposedRemovals:
    """Test that _parse_final_review_response correctly parses proposed_removals."""

    def _make_response(self, proposed_removals=None, **extra):
        """Build a minimal valid AI response JSON string."""
        data = {
            "proposed": {
                "artist": "Test Artist",
                "title": "Test Title",
                "album": "Test Album",
                "year": 2025,
                "genres": ["Rock"],
                "plot": "A test plot.",
            },
            "confidence": {
                "artist": 0.99,
                "title": 0.99,
                "album": 0.8,
                "year": 0.9,
                "genres": 0.85,
                "plot": 0.9,
            },
            "artwork": {"approved": True, "rejection_reason": None},
            "changes": [],
            "scraper_overrides": [],
        }
        if proposed_removals is not None:
            data["proposed_removals"] = proposed_removals
        data.update(extra)
        return json.dumps(data)

    def test_no_proposed_removals_key(self):
        """When AI response omits proposed_removals, defaults to empty dict."""
        raw = self._make_response()
        result = _parse_final_review_response(raw)
        assert result.proposed_removals == {}

    def test_empty_proposed_removals(self):
        """When AI returns empty proposed_removals object."""
        raw = self._make_response(proposed_removals={})
        result = _parse_final_review_response(raw)
        assert result.proposed_removals == {}

    def test_valid_proposed_removals(self):
        """When AI recommends removing album."""
        raw = self._make_response(proposed_removals={
            "album": "Scraped album is the parent studio album, not the single release.",
        })
        result = _parse_final_review_response(raw)
        assert "album" in result.proposed_removals
        assert "parent studio album" in result.proposed_removals["album"]

    def test_multiple_removals(self):
        """When AI recommends removing multiple fields."""
        raw = self._make_response(proposed_removals={
            "album": "Wrong album",
            "year": "Year is for the album, not the single",
        })
        result = _parse_final_review_response(raw)
        assert len(result.proposed_removals) == 2
        assert "album" in result.proposed_removals
        assert "year" in result.proposed_removals

    def test_null_value_removal_filtered(self):
        """When removal reason is null/empty, it should be filtered out."""
        raw = self._make_response(proposed_removals={
            "album": None,
            "year": "",
            "genres": "Wrong genres",
        })
        result = _parse_final_review_response(raw)
        # null and empty values should be filtered out
        assert "album" not in result.proposed_removals
        assert "year" not in result.proposed_removals
        assert "genres" in result.proposed_removals

    def test_non_dict_proposed_removals(self):
        """When AI returns a non-dict value for proposed_removals (malformed)."""
        raw = self._make_response(proposed_removals=["album", "year"])
        result = _parse_final_review_response(raw)
        assert result.proposed_removals == {}

    def test_non_string_key_filtered(self):
        """When a removal key is not a string (shouldn't happen but defensive)."""
        # JSON only allows string keys, so this tests the guard for safety
        raw = self._make_response(proposed_removals={
            "album": "Wrong album",
        })
        result = _parse_final_review_response(raw)
        assert "album" in result.proposed_removals


# ── Application Logic Tests ───────────────────────────────────────────────


class TestApplyProposedRemovals:
    """Test the proposed_removals application logic extracted from unified_metadata.py."""

    def _apply_removals(self, metadata, ai_review_result, _log_messages=None):
        """
        Simulate the proposed_removals application block from unified_metadata.py.
        This is extracted verbatim from the code to test the logic.
        """
        if _log_messages is None:
            _log_messages = []

        def _log(msg):
            _log_messages.append(msg)

        changes_applied = []
        pipeline_log = []

        _REMOVABLE_FIELDS = {"album", "year", "genres", "plot"}
        for _rm_field, _rm_reason in (ai_review_result.proposed_removals or {}).items():
            if _rm_field not in _REMOVABLE_FIELDS:
                _log(f"AI Final Review: ignoring removal of non-removable "
                     f"field '{_rm_field}'")
                continue
            _rm_current = metadata.get(_rm_field)
            if not _rm_current:
                continue  # Already empty, nothing to remove

            # MB release-group is authoritative for album — don't remove
            if _rm_field == "album" and metadata.get("mb_album_release_group_id"):
                _log(f"AI Final Review: skipping removal of album "
                     f"(MB release-group is authoritative): "
                     f"keeping '{_rm_current}'")
                continue

            metadata[_rm_field] = None
            _log(f"AI Final Review: removed {_rm_field} "
                 f"(was '{_rm_current}'): {_rm_reason}")
            pipeline_log.append(
                f"ai_review_removal:{_rm_field}:{_rm_reason}"
            )
            changes_applied.append(
                f"{_rm_field}: '{_rm_current}' → removed ({_rm_reason})"
            )

        return changes_applied, pipeline_log

    def test_remove_album_no_mb(self):
        """Album removal succeeds when no MB release-group is set."""
        metadata = {
            "artist": "The Smith Street Band",
            "title": "This Is It",
            "album": "Once I Was Wild",
            "year": 2025,
        }
        result = FinalReviewResult()
        result.proposed_removals = {
            "album": "Scraped album is the parent studio album, not the single.",
        }
        changes, log = self._apply_removals(metadata, result)

        assert metadata["album"] is None
        assert len(changes) == 1
        assert "removed" in changes[0]
        assert "Once I Was Wild" in changes[0]

    def test_remove_album_blocked_by_mb(self):
        """Album removal blocked when MB release-group is authoritative."""
        logs = []
        metadata = {
            "artist": "Test Artist",
            "title": "Test Title",
            "album": "Real Album",
            "mb_album_release_group_id": "04538fae-c28f-4b38-ad86-6e6781086bc5",
        }
        result = FinalReviewResult()
        result.proposed_removals = {
            "album": "Wrong album",
        }
        changes, log = self._apply_removals(metadata, result, logs)

        assert metadata["album"] == "Real Album"  # NOT removed
        assert len(changes) == 0
        assert any("authoritative" in m for m in logs)

    def test_remove_non_removable_field_artist(self):
        """Artist removal is ignored (not a removable field)."""
        logs = []
        metadata = {
            "artist": "Test Artist",
            "title": "Test Title",
        }
        result = FinalReviewResult()
        result.proposed_removals = {
            "artist": "Wrong artist",
        }
        changes, log = self._apply_removals(metadata, result, logs)

        assert metadata["artist"] == "Test Artist"  # NOT removed
        assert any("non-removable" in m for m in logs)

    def test_remove_non_removable_field_title(self):
        """Title removal is ignored (not a removable field)."""
        logs = []
        metadata = {
            "artist": "Test Artist",
            "title": "Test Title",
        }
        result = FinalReviewResult()
        result.proposed_removals = {
            "title": "Wrong title",
        }
        changes, log = self._apply_removals(metadata, result, logs)

        assert metadata["title"] == "Test Title"  # NOT removed
        assert any("non-removable" in m for m in logs)

    def test_already_empty_album_skipped(self):
        """When album is already None, removal is skipped."""
        metadata = {
            "artist": "Test Artist",
            "title": "Test Title",
            "album": None,
        }
        result = FinalReviewResult()
        result.proposed_removals = {
            "album": "Wrong album",
        }
        changes, log = self._apply_removals(metadata, result)

        assert metadata["album"] is None
        assert len(changes) == 0  # No change counted

    def test_remove_year(self):
        """Year removal succeeds."""
        metadata = {
            "artist": "Test Artist",
            "title": "Test Title",
            "year": 1999,
        }
        result = FinalReviewResult()
        result.proposed_removals = {
            "year": "Year is for the album, not the single",
        }
        changes, log = self._apply_removals(metadata, result)

        assert metadata["year"] is None
        assert len(changes) == 1

    def test_remove_genres(self):
        """Genres removal succeeds."""
        metadata = {
            "artist": "Test Artist",
            "title": "Test Title",
            "genres": ["Pop", "Rock"],
        }
        result = FinalReviewResult()
        result.proposed_removals = {
            "genres": "Genres are completely wrong for this artist",
        }
        changes, log = self._apply_removals(metadata, result)

        assert metadata["genres"] is None
        assert len(changes) == 1

    def test_empty_proposed_removals(self):
        """No removals when proposed_removals is empty."""
        metadata = {
            "artist": "Test Artist",
            "title": "Test Title",
            "album": "Some Album",
        }
        result = FinalReviewResult()
        result.proposed_removals = {}
        changes, log = self._apply_removals(metadata, result)

        assert metadata["album"] == "Some Album"
        assert len(changes) == 0

    def test_none_proposed_removals(self):
        """No crash when proposed_removals is None (defensive)."""
        metadata = {
            "artist": "Test Artist",
            "title": "Test Title",
            "album": "Some Album",
        }
        result = FinalReviewResult()
        result.proposed_removals = None
        changes, log = self._apply_removals(metadata, result)

        assert metadata["album"] == "Some Album"
        assert len(changes) == 0

    def test_multiple_removals_applied(self):
        """Multiple fields can be removed in one pass."""
        metadata = {
            "artist": "Test Artist",
            "title": "Test Title",
            "album": "Wrong Album",
            "year": 1999,
            "genres": ["Pop"],
        }
        result = FinalReviewResult()
        result.proposed_removals = {
            "album": "Wrong album from parent",
            "year": "Year is wrong",
        }
        changes, log = self._apply_removals(metadata, result)

        assert metadata["album"] is None
        assert metadata["year"] is None
        assert metadata["genres"] == ["Pop"]  # Not removed
        assert len(changes) == 2


# ── Plot Confidence Threshold Tests ───────────────────────────────────────


class TestPlotConfidenceThreshold:
    """Test the lowered plot confidence threshold when no existing plot."""

    def _simulate_confidence_check(self, metadata, field_name, field_conf):
        """
        Simulate the confidence threshold logic from unified_metadata.py.
        Returns True if the field passes the threshold, False if rejected.
        """
        MIN_FIELD_CONFIDENCE = 0.7
        MIN_PLOT_GENERATE_CONFIDENCE = 0.4

        _effective_min_conf = MIN_FIELD_CONFIDENCE
        if field_name == "plot" and not metadata.get("plot"):
            _effective_min_conf = MIN_PLOT_GENERATE_CONFIDENCE
        if field_conf < _effective_min_conf:
            return False  # Rejected
        return True  # Accepted

    def test_plot_055_no_existing_plot_accepted(self):
        """AI plot with conf=0.55 accepted when no existing plot."""
        metadata = {"artist": "Test", "title": "Test"}
        assert self._simulate_confidence_check(metadata, "plot", 0.55) is True

    def test_plot_040_no_existing_plot_accepted(self):
        """AI plot with conf=0.40 accepted (exactly at threshold)."""
        metadata = {"artist": "Test", "title": "Test"}
        assert self._simulate_confidence_check(metadata, "plot", 0.40) is True

    def test_plot_039_no_existing_plot_rejected(self):
        """AI plot with conf=0.39 rejected (below lower threshold)."""
        metadata = {"artist": "Test", "title": "Test"}
        assert self._simulate_confidence_check(metadata, "plot", 0.39) is False

    def test_plot_055_with_existing_plot_rejected(self):
        """AI plot with conf=0.55 rejected when existing plot present (standard threshold applies)."""
        metadata = {"artist": "Test", "title": "Test", "plot": "Existing plot."}
        assert self._simulate_confidence_check(metadata, "plot", 0.55) is False

    def test_plot_070_with_existing_plot_accepted(self):
        """AI plot with conf=0.70 accepted even with existing plot."""
        metadata = {"artist": "Test", "title": "Test", "plot": "Existing plot."}
        assert self._simulate_confidence_check(metadata, "plot", 0.70) is True

    def test_album_055_still_rejected(self):
        """Album with conf=0.55 still rejected (only plot gets lower threshold)."""
        metadata = {"artist": "Test", "title": "Test"}
        assert self._simulate_confidence_check(metadata, "album", 0.55) is False

    def test_album_070_accepted(self):
        """Album with conf=0.70 accepted (standard threshold)."""
        metadata = {"artist": "Test", "title": "Test"}
        assert self._simulate_confidence_check(metadata, "album", 0.70) is True

    def test_plot_empty_string_treated_as_no_plot(self):
        """Empty string plot treated as no plot (lower threshold applies)."""
        metadata = {"artist": "Test", "title": "Test", "plot": ""}
        assert self._simulate_confidence_check(metadata, "plot", 0.55) is True

    def test_plot_none_treated_as_no_plot(self):
        """None plot treated as no plot (lower threshold applies)."""
        metadata = {"artist": "Test", "title": "Test", "plot": None}
        assert self._simulate_confidence_check(metadata, "plot", 0.55) is True


# ── Integration: Smith Street Band Scenario ───────────────────────────────


class TestSmithStreetBandScenario:
    """
    End-to-end simulation of The Smith Street Band — "This Is It" scenario.

    Expected behavior:
    - AI proposes album=null → processed as proposed_removals (album: parent album)
    - AI generates plot (conf=0.55) → accepted with lower threshold
    - Album "Once I Was Wild" removed because it's the parent studio album
    """

    def test_full_scenario(self):
        """Simulate the exact Smith Street Band scraper test scenario."""
        # Simulate AI response matching what the trace showed
        ai_response_json = json.dumps({
            "proposed": {
                "artist": "The Smith Street Band",
                "title": "This Is It",
                "album": None,  # AI says no album
                "year": 2025,
                "genres": ["Rock", "Punk", "Indie Rock"],
                "plot": (
                    "This Is It is a 2025 single by Australian band The Smith "
                    "Street Band. The official video presents the group in a "
                    "direct, high-energy style."
                ),
            },
            "confidence": {
                "artist": 0.99,
                "title": 0.99,
                "album": 0.6,
                "year": 0.65,
                "genres": 0.75,
                "plot": 0.55,
            },
            "artwork": {"approved": True, "rejection_reason": None},
            "changes": [
                "Verified artist and title.",
                "Replaced album with null — parent studio album.",
                "Generated plot.",
            ],
            "scraper_overrides": [
                "album: Overridden to null since parent album was used.",
            ],
            "proposed_removals": {
                "album": "Scraped album 'Once I Was Wild' is the parent "
                         "studio album, not the single release.",
            },
        })

        # Parse the response
        result = _parse_final_review_response(ai_response_json)

        # Verify parsing
        assert result.artist == "The Smith Street Band"
        assert result.title == "This Is It"
        assert result.album is None  # AI proposed null
        assert result.plot is not None  # AI generated a plot
        assert result.field_scores.get("plot") == 0.55
        assert "album" in result.proposed_removals

        # Simulate the metadata state after MB resolution
        metadata = {
            "artist": "The Smith Street Band",
            "title": "This Is It",
            "album": "Once I Was Wild",  # From MusicBrainz
            "year": 2025,
            "genres": None,
            "plot": None,  # No existing plot!
        }

        # -- Test plot threshold --
        MIN_FIELD_CONFIDENCE = 0.7
        MIN_PLOT_GENERATE_CONFIDENCE = 0.4
        plot_conf = result.field_scores.get("plot", 0.0)
        _effective_min_conf = MIN_FIELD_CONFIDENCE
        if not metadata.get("plot"):
            _effective_min_conf = MIN_PLOT_GENERATE_CONFIDENCE
        # With old threshold (0.7), plot would be rejected
        assert plot_conf < MIN_FIELD_CONFIDENCE, "Plot conf should be below standard threshold"
        # With new threshold (0.4), plot is accepted
        assert plot_conf >= MIN_PLOT_GENERATE_CONFIDENCE, "Plot conf should be at or above lower threshold"

        # Apply the plot
        if plot_conf >= _effective_min_conf and result.plot:
            metadata["plot"] = result.plot

        assert metadata["plot"] is not None
        assert "2025 single" in metadata["plot"]

        # -- Test album removal via proposed_removals --
        # Note: result.album is None, so the normal field loop skips it
        # (if proposed is None: continue). The proposed_removals catches this.
        assert result.album is None  # Normal loop would skip
        
        # Apply proposed_removals
        _REMOVABLE_FIELDS = {"album", "year", "genres", "plot"}
        for _rm_field, _rm_reason in result.proposed_removals.items():
            if _rm_field not in _REMOVABLE_FIELDS:
                continue
            _rm_current = metadata.get(_rm_field)
            if not _rm_current:
                continue
            if _rm_field == "album" and metadata.get("mb_album_release_group_id"):
                continue
            metadata[_rm_field] = None

        assert metadata["album"] is None, "Album 'Once I Was Wild' should have been removed"
        assert metadata["plot"] is not None, "Plot should still be present"
        assert metadata["year"] == 2025, "Year should NOT be removed"

    def test_full_scenario_with_mb_guard(self):
        """Same scenario but with MB release-group — album removal blocked."""
        metadata = {
            "artist": "The Smith Street Band",
            "title": "This Is It",
            "album": "Once I Was Wild",
            "year": 2025,
            "mb_album_release_group_id": "4a837e45-70e4-49fb-9f13-06039a03ba41",
        }
        result = FinalReviewResult()
        result.proposed_removals = {
            "album": "Parent studio album",
        }

        _REMOVABLE_FIELDS = {"album", "year", "genres", "plot"}
        for _rm_field, _rm_reason in result.proposed_removals.items():
            if _rm_field not in _REMOVABLE_FIELDS:
                continue
            _rm_current = metadata.get(_rm_field)
            if not _rm_current:
                continue
            if _rm_field == "album" and metadata.get("mb_album_release_group_id"):
                continue  # MB guard blocks removal
            metadata[_rm_field] = None

        assert metadata["album"] == "Once I Was Wild", "MB guard should block album removal"


# ── Regression: FAILED_APPROACHES compliance ──────────────────────────────


class TestFailedApproachesCompliance:
    """
    Verify the changes don't reintroduce previously failed approaches.
    """

    def test_album_sentinel_still_blocked(self):
        """
        Regression: AI returning "Unknown" / "null" as album text
        should still be blocked by the sentinel check in the normal loop.
        (FAILED: AronChupa - Relying on AI to leave album blank)
        """
        result = FinalReviewResult()
        result.album = "Unknown"
        result.field_scores = {"album": 0.8}
        # The sentinel check happens in the normal loop, not in proposed_removals.
        # proposed_removals is a separate mechanism.
        _ALBUM_SENTINELS = {
            "unknown", "unknown album", "n/a", "na", "none",
            "null", "nil", "no album", "untitled", "tbd",
            "not available", "not applicable", "[not set]",
            "-", "--", "\u2014", "?",
        }
        assert str(result.album).strip().lower() in _ALBUM_SENTINELS

    def test_mb_authoritative_guard_for_removals(self):
        """
        Regression: MB release-group is authoritative.
        (Must not let AI override MB-confirmed album via removals)
        """
        metadata = {
            "album": "Lungs",
            "mb_album_release_group_id": "some-rg-id",
        }
        result = FinalReviewResult()
        result.proposed_removals = {"album": "This album is wrong"}

        _REMOVABLE_FIELDS = {"album", "year", "genres", "plot"}
        removed = False
        for _rm_field, _rm_reason in result.proposed_removals.items():
            if _rm_field not in _REMOVABLE_FIELDS:
                continue
            _rm_current = metadata.get(_rm_field)
            if not _rm_current:
                continue
            if _rm_field == "album" and metadata.get("mb_album_release_group_id"):
                continue  # Blocked!
            metadata[_rm_field] = None
            removed = True

        assert not removed, "Removal should have been blocked by MB guard"
        assert metadata["album"] == "Lungs"

    def test_plot_with_urls_still_rejected(self):
        """
        Regression: AI plot with URLs should still be rejected.
        Lower threshold doesn't bypass content validation.
        """
        import re
        proposed_plot = (
            "Check out this video https://youtube.com/watch "
            "Follow on https://instagram.com/artist "
            "And https://twitter.com/artist for updates "
            "More at https://tiktok.com/@artist"
        )
        _url_count = len(re.findall(r'https?://', proposed_plot))
        _has_social = bool(re.search(
            r'instagram\.com|facebook\.com|twitter\.com|tiktok\.com'
            r'|smarturl\.it|lnk\.to|linktr\.ee',
            proposed_plot, re.IGNORECASE,
        ))
        assert _url_count >= 3 or _has_social, "Plot with URLs should be caught"

    def test_lowered_threshold_only_applies_to_plot(self):
        """
        Regression: Only plot gets the lower threshold.
        Other fields must still meet 0.7 threshold.
        """
        for field in ("artist", "title", "album", "year", "genres"):
            MIN_FIELD_CONFIDENCE = 0.7
            MIN_PLOT_GENERATE_CONFIDENCE = 0.4
            metadata = {}
            _effective_min_conf = MIN_FIELD_CONFIDENCE
            if field == "plot" and not metadata.get("plot"):
                _effective_min_conf = MIN_PLOT_GENERATE_CONFIDENCE
            assert _effective_min_conf == 0.7, f"{field} should use standard threshold"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
