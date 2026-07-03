"""
Tests for duplicate-scan normalizers (app.tasks).

Covers:
  - _normalize_for_dup       (case, articles, punctuation, accents)
  - _normalize_artist_for_dup (featuring-credit stripping, artist key)
  - _normalize_title_for_dup  (bracketed + dash-style version suffixes)

Real-world regression: 'Kernkraft 400' vs 'Kernkraft 400 (DJ Gius Remix)'
did not bucket together because the old regex only stripped a bracket whose
FIRST word was a whitelisted qualifier ('DJ' is not).  The qualifier is now
matched as a whole word anywhere inside the bracket, multiple trailing
bracket groups strip, and spaced-dash suffixes containing a qualifier strip.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.tasks import (
    _normalize_for_dup,
    _normalize_artist_for_dup,
    _normalize_title_for_dup,
)


# ── Base normalizer ────────────────────────────────────────────────────────

class TestNormalizeForDup:
    def test_lowercase_and_punctuation(self):
        assert _normalize_for_dup("AC/DC!") == "acdc"

    def test_leading_article_stripped(self):
        assert _normalize_for_dup("The Killers") == "killers"

    def test_accents_stripped(self):
        assert _normalize_for_dup("Beyoncé") == "beyonce"
        assert _normalize_for_dup("Björk") == "bjork"

    def test_whitespace_collapsed(self):
        assert _normalize_for_dup("  Foo   Fighters  ") == "foo fighters"


# ── Artist key ─────────────────────────────────────────────────────────────

class TestNormalizeArtistForDup:
    def test_feat_dot_stripped(self):
        assert _normalize_artist_for_dup("Zombie Nation feat. X") == "zombie nation"

    def test_ft_stripped(self):
        assert _normalize_artist_for_dup("Zombie Nation ft. Someone") == "zombie nation"

    def test_featuring_stripped(self):
        assert _normalize_artist_for_dup("Zombie Nation featuring The Others") == "zombie nation"

    def test_bracketed_feat_stripped(self):
        assert _normalize_artist_for_dup("Daft Punk (feat. Pharrell Williams)") == "daft punk"

    def test_ft_inside_word_not_stripped(self):
        # 'ft' inside 'Swift' must not trigger the featuring stripper
        assert _normalize_artist_for_dup("Taylor Swift") == "taylor swift"

    def test_accented_artist_matches_plain(self):
        assert _normalize_artist_for_dup("Beyoncé") == _normalize_artist_for_dup("Beyonce")


# ── Title key ──────────────────────────────────────────────────────────────

class TestNormalizeTitleForDup:
    # Regression: qualifier anywhere in the bracket, not just first word
    def test_dj_gius_remix(self):
        assert _normalize_title_for_dup("Kernkraft 400 (DJ Gius Remix)") == \
            _normalize_title_for_dup("Kernkraft 400")

    def test_year_remaster_bracket(self):
        assert _normalize_title_for_dup("Song (2009 Remaster)") == "song"

    def test_multi_word_remix_bracket(self):
        assert _normalize_title_for_dup("Kernkraft 400 (Sport Chant Stadium Remix)") == "kernkraft 400"

    # Multiple trailing bracket groups
    def test_multiple_bracket_groups(self):
        assert _normalize_title_for_dup("Song (Live) (Official Video)") == "song"

    # Dash-style suffixes — stripped only with a qualifier word
    def test_dash_radio_edit(self):
        assert _normalize_title_for_dup("Song - Radio Edit") == "song"

    def test_dash_live_at_wembley(self):
        assert _normalize_title_for_dup("Song - Live at Wembley") == "song"

    def test_dash_year_remaster(self):
        assert _normalize_title_for_dup("Song - 2009 Remaster") == "song"

    def test_dash_without_qualifier_not_stripped(self):
        # Legit dashed title must survive (only punctuation removed)
        assert _normalize_title_for_dup("Ebony - Ivory") == "ebony ivory"

    def test_bracket_without_qualifier_not_stripped(self):
        assert _normalize_title_for_dup("Song (Blue)") == "song blue"

    def test_original_whitelist_still_works(self):
        assert _normalize_title_for_dup("Never Meant (Alternate Version)") == "never meant"
        assert _normalize_title_for_dup("Never Meant (Live)") == "never meant"

    def test_never_strips_to_empty(self):
        # A title that IS a qualifier bracket keeps its content
        assert _normalize_title_for_dup("(Live)") == "live"

    def test_different_titles_do_not_collide(self):
        assert _normalize_title_for_dup("Kernkraft 400") != _normalize_title_for_dup("Kernkraft 500")

    def test_hyphenated_word_not_treated_as_dash_suffix(self):
        # No spaces around the hyphen → not a version suffix
        assert _normalize_title_for_dup("Re-Mix") == "remix"


# ── Bucket-key integration (mirrors duplicate_scan_task Phase 1) ──────────

class TestBucketKeys:
    @staticmethod
    def _key(artist, title):
        return f"{_normalize_artist_for_dup(artist)}||{_normalize_title_for_dup(title)}"

    def test_kernkraft_400_buckets_together(self):
        assert self._key("Zombie Nation", "Kernkraft 400") == \
            self._key("Zombie Nation", "Kernkraft 400 (DJ Gius Remix)")

    def test_featuring_artist_buckets_together(self):
        assert self._key("Zombie Nation feat. X", "Kernkraft 400") == \
            self._key("Zombie Nation", "Kernkraft 400")

    def test_different_artists_do_not_bucket(self):
        assert self._key("Zombie Nation", "Kernkraft 400") != \
            self._key("Scooter", "Kernkraft 400")
