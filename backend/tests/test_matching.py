"""
Tests for the matching / confidence scoring subsystem.

Covers:
  - Normalization edge cases  (artist, title, album, comparison keys)
  - Featured artist extraction
  - Title qualifier extraction
  - Scoring features             (string_similarity, duration matching)
  - Hysteresis decision logic
  - Score classification & thresholds
  - Deterministic tie-breaking    (ScoredCandidate.sort_key)
  - Overall score computation
"""
import pytest
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

# ── Normalization tests ───────────────────────────────────────────────────

from app.matching.normalization import (
    normalize_artist_name,
    extract_featured_artists,
    normalize_title,
    extract_title_qualifiers,
    normalize_album,
    make_comparison_key,
)


class TestNormalizeArtistName:
    """Artist name cleaning."""

    def test_strip_topic_suffix(self):
        assert normalize_artist_name("Radiohead - Topic") == "Radiohead"

    def test_strip_topic_suffix_case_insensitive(self):
        assert normalize_artist_name("Foo Fighters - TOPIC") == "Foo Fighters"

    def test_collapse_whitespace(self):
        assert normalize_artist_name("  Foo   Fighters  ") == "Foo Fighters"

    def test_preserve_acdc(self):
        assert normalize_artist_name("AC/DC") == "AC/DC"

    def test_preserve_pink(self):
        assert normalize_artist_name("P!nk") == "P!nk"

    def test_preserve_mo(self):
        assert normalize_artist_name("MØ") == "MØ"

    def test_empty(self):
        assert normalize_artist_name("") == ""


class TestExtractFeaturedArtists:
    """Featured artist parsing."""

    def test_feat_dot(self):
        primary, featured = extract_featured_artists("Drake feat. Rihanna")
        assert primary == "Drake"
        assert featured == ["Rihanna"]

    def test_ft_dot(self):
        primary, featured = extract_featured_artists("Drake ft. Rihanna")
        assert primary == "Drake"
        assert featured == ["Rihanna"]

    def test_featuring(self):
        primary, featured = extract_featured_artists("Drake featuring Rihanna")
        assert primary == "Drake"
        assert featured == ["Rihanna"]

    def test_feat_multiple(self):
        primary, featured = extract_featured_artists("A feat. B & C")
        assert primary == "A"
        assert "B" in featured
        assert "C" in featured

    def test_ampersand_split(self):
        primary, featured = extract_featured_artists("Simon & Garfunkel")
        assert primary == "Simon"
        assert featured == ["Garfunkel"]

    def test_solo_artist(self):
        primary, featured = extract_featured_artists("Björk")
        assert primary == "Björk"
        assert featured == []

    def test_paren_feat(self):
        primary, featured = extract_featured_artists("A (feat. B)")
        assert primary == "A"
        assert featured == ["B"]


class TestNormalizeTitle:
    """Title noise stripping."""

    def test_strip_official_video(self):
        result = normalize_title("My Hero (Official Video)")
        assert "official" not in result.lower()
        assert "My Hero" in result

    def test_strip_official_music_video(self):
        result = normalize_title("My Song [Official Music Video]")
        assert "official" not in result.lower()

    def test_strip_hd(self):
        result = normalize_title("My Song (HD)")
        assert "HD" not in result

    def test_strip_4k(self):
        result = normalize_title("My Song [4K Upgrade]")
        assert "4K" not in result and "4k" not in result.lower()

    def test_strip_vevo(self):
        result = normalize_title("My Song (VEVO)")
        assert "VEVO" not in result

    def test_strip_explicit(self):
        result = normalize_title("My Song (Explicit)")
        assert "Explicit" not in result

    def test_preserve_live(self):
        """'Live' is a meaningful qualifier and should remain in the title base."""
        result = normalize_title("My Song (Live)")
        assert "Live" in result

    def test_preserve_acoustic(self):
        result = normalize_title("My Song (Acoustic)")
        assert "Acoustic" in result

    def test_remastered_in_brackets(self):
        result = normalize_title("My Song (Remastered)")
        assert "Remastered" not in result or "remastered" not in result.lower()

    def test_no_trailing_dash(self):
        result = normalize_title("My Song - Official Video")
        assert not result.endswith("-")

    def test_empty(self):
        assert normalize_title("") == ""


class TestExtractTitleQualifiers:
    """Qualifier extraction."""

    def test_live_qualifier(self):
        info = extract_title_qualifiers("Creep (Live at Glastonbury)")
        assert "live" in info["qualifiers"]

    def test_acoustic_qualifier(self):
        info = extract_title_qualifiers("Song (Acoustic Version)")
        assert "acoustic" in info["qualifiers"]

    def test_remix_qualifier(self):
        info = extract_title_qualifiers("Song (Remix)")
        assert "remix" in info["qualifiers"]

    def test_no_qualifiers(self):
        info = extract_title_qualifiers("Just a Song")
        assert info["qualifiers"] == set()

    def test_multiple_qualifiers(self):
        info = extract_title_qualifiers("Song (Live) (Acoustic)")
        assert "live" in info["qualifiers"]
        assert "acoustic" in info["qualifiers"]

    def test_official_video_not_qualifier(self):
        info = extract_title_qualifiers("Song (Official Video)")
        assert "official video" not in info["qualifiers"]

    def test_title_base_cleaned(self):
        info = extract_title_qualifiers("Song (Official Video)")
        assert "official" not in info["title_base"].lower()

    def test_demo_qualifier(self):
        info = extract_title_qualifiers("Song (Demo)")
        assert "demo" in info["qualifiers"]


class TestNormalizeAlbum:
    """Album normalisation."""

    def test_collapse_whitespace(self):
        assert normalize_album("  In  Rainbows  ") == "In Rainbows"

    def test_noop_normal(self):
        assert normalize_album("OK Computer") == "OK Computer"

    def test_empty(self):
        assert normalize_album("") == ""


class TestMakeComparisonKey:
    """Comparison key (aggressive normalisation)."""

    def test_acdc(self):
        assert make_comparison_key("AC/DC") == "acdc"

    def test_pink(self):
        assert make_comparison_key("P!nk") == "pnk"

    def test_bjork(self):
        assert make_comparison_key("Björk") == "bjork"

    def test_the_the(self):
        key = make_comparison_key("The The")
        assert key == "the the"

    def test_case_insensitive(self):
        assert make_comparison_key("FOO") == make_comparison_key("foo")

    def test_diacritics(self):
        assert make_comparison_key("Mötley Crüe") == "motley crue"

    def test_punctuation_stripped(self):
        assert make_comparison_key("blink-182") == "blink182"

    def test_empty(self):
        assert make_comparison_key("") == ""


# ── Scoring tests ─────────────────────────────────────────────────────────

from app.matching.scoring import (
    _jaro_winkler,
    _token_set_similarity,
    string_similarity,
    score_artist_candidate,
    score_recording_candidate,
    score_release_candidate,
    compute_overall_score,
    classify_score,
    MatchStatus,
    ScoredCandidate,
    ScoreBreakdown,
    THRESHOLDS,
)


class TestJaroWinkler:
    """Basic Jaro-Winkler properties."""

    def test_exact_match(self):
        assert _jaro_winkler("abc", "abc") == 1.0

    def test_empty_strings(self):
        assert _jaro_winkler("", "abc") == 0.0
        assert _jaro_winkler("abc", "") == 0.0

    def test_similar_strings(self):
        """Similar strings should produce high (> 0.8) similarity."""
        assert _jaro_winkler("radiohead", "radioheed") > 0.8

    def test_dissimilar_strings(self):
        """Completely different strings should be low."""
        assert _jaro_winkler("abc", "xyz") < 0.5


class TestTokenSetSimilarity:
    """Token-set (order independent) similarity."""

    def test_exact(self):
        assert _token_set_similarity("foo fighters", "foo fighters") == 1.0

    def test_reordered(self):
        """Same words, different order → 1.0."""
        assert _token_set_similarity("fighters foo", "foo fighters") == 1.0

    def test_partial(self):
        """The Foo Fighters vs Foo Fighters → high overlap."""
        sim = _token_set_similarity("the foo fighters", "foo fighters")
        assert sim > 0.6

    def test_disjoint(self):
        assert _token_set_similarity("aaa bbb", "ccc ddd") == 0.0


class TestStringSimilarity:
    """Combined string_similarity (max of JW and token-set)."""

    def test_exact(self):
        assert string_similarity("Foo Fighters", "Foo Fighters") == 1.0

    def test_case_insensitive(self):
        assert string_similarity("FOO FIGHTERS", "foo fighters") == 1.0

    def test_high_for_close(self):
        sim = string_similarity("Radiohead", "Radiohead!")
        assert sim > 0.9


class TestScoreArtistCandidate:
    """Artist feature scoring."""

    @dataclass
    class _FakeArtist:
        canonical_name: str = ""
        mbid: str = ""
        aliases: Optional[List[str]] = None
        disambiguation: str = ""
        provider: str = "musicbrainz"
        raw: Optional[Dict[str, Any]] = None

    def test_exact_match(self):
        c = self._FakeArtist(canonical_name="Radiohead")
        feats = score_artist_candidate(
            c,
            query_artist_key=make_comparison_key("Radiohead"),
            query_artist_display="Radiohead",
        )
        assert feats["f_artist_exact"] == 1.0
        assert feats["f_artist_similarity"] == 1.0

    def test_alias_match(self):
        c = self._FakeArtist(
            canonical_name="The Beatles",
            aliases=["Beatles"],
        )
        feats = score_artist_candidate(
            c,
            query_artist_key=make_comparison_key("Beatles"),
            query_artist_display="Beatles",
        )
        assert feats["f_artist_alias"] == 1.0

    def test_disambiguation_bonus(self):
        c = self._FakeArtist(
            canonical_name="Radiohead",
            disambiguation="English rock band",
        )
        feats = score_artist_candidate(
            c,
            query_artist_key=make_comparison_key("Radiohead"),
            query_artist_display="Radiohead",
        )
        assert feats["f_artist_disambiguation"] == 1.0

    def test_provider_prior_musicbrainz(self):
        c = self._FakeArtist(canonical_name="X", provider="musicbrainz")
        feats = score_artist_candidate(
            c,
            query_artist_key="x",
            query_artist_display="X",
        )
        assert feats["f_provider_prior"] == 1.0

    def test_provider_prior_other(self):
        c = self._FakeArtist(canonical_name="X", provider="wikipedia")
        feats = score_artist_candidate(
            c,
            query_artist_key="x",
            query_artist_display="X",
        )
        assert feats["f_provider_prior"] == 0.3


class TestScoreRecordingCandidate:
    """Recording feature scoring, especially duration matching."""

    @dataclass
    class _FakeRec:
        title: str = ""
        mbid: str = ""
        artist_name: str = ""
        artist_mbid: str = ""
        duration_seconds: Optional[float] = None
        provider: str = "musicbrainz"
        raw: Optional[Dict[str, Any]] = None

    def test_duration_exact(self):
        c = self._FakeRec(title="Song", duration_seconds=240.0)
        feats = score_recording_candidate(
            c,
            query_title_key="song",
            query_title_display="Song",
            query_qualifiers=set(),
            local_duration=240.0,
            resolved_artist_mbid=None,
            resolved_artist_key="artist",
        )
        assert feats["f_duration_match"] == 1.0

    def test_duration_within_5s(self):
        """±5 seconds → 1.0."""
        c = self._FakeRec(title="Song", duration_seconds=245.0)
        feats = score_recording_candidate(
            c,
            query_title_key="song",
            query_title_display="Song",
            query_qualifiers=set(),
            local_duration=240.0,
            resolved_artist_mbid=None,
            resolved_artist_key="artist",
        )
        assert feats["f_duration_match"] == 1.0

    def test_duration_at_30s(self):
        """±30 seconds → 0.0."""
        c = self._FakeRec(title="Song", duration_seconds=270.0)
        feats = score_recording_candidate(
            c,
            query_title_key="song",
            query_title_display="Song",
            query_qualifiers=set(),
            local_duration=240.0,
            resolved_artist_mbid=None,
            resolved_artist_key="artist",
        )
        assert feats["f_duration_match"] == 0.0

    def test_duration_linear_interpolation(self):
        """15s diff → about 0.6 (linear from 5→30)."""
        c = self._FakeRec(title="Song", duration_seconds=255.0)
        feats = score_recording_candidate(
            c,
            query_title_key="song",
            query_title_display="Song",
            query_qualifiers=set(),
            local_duration=240.0,
            resolved_artist_mbid=None,
            resolved_artist_key="artist",
        )
        # 15s diff: 1.0 - (15-5)/25 = 1.0 - 0.4 = 0.6
        assert abs(feats["f_duration_match"] - 0.6) < 0.01

    def test_duration_no_data(self):
        """Missing duration → neutral (0.5)."""
        c = self._FakeRec(title="Song", duration_seconds=None)
        feats = score_recording_candidate(
            c,
            query_title_key="song",
            query_title_display="Song",
            query_qualifiers=set(),
            local_duration=None,
            resolved_artist_mbid=None,
            resolved_artist_key="artist",
        )
        assert feats["f_duration_match"] == 0.5

    def test_qualifier_both_plain(self):
        """Both query and candidate are plain → 1.0."""
        c = self._FakeRec(title="Song")
        feats = score_recording_candidate(
            c,
            query_title_key="song",
            query_title_display="Song",
            query_qualifiers=set(),
            local_duration=None,
            resolved_artist_mbid=None,
            resolved_artist_key="artist",
        )
        assert feats["f_qualifier_match"] == 1.0

    def test_qualifier_mismatch(self):
        """Query wants 'live', candidate is plain → low score."""
        c = self._FakeRec(title="Song")
        feats = score_recording_candidate(
            c,
            query_title_key="song",
            query_title_display="Song",
            query_qualifiers={"live"},
            local_duration=None,
            resolved_artist_mbid=None,
            resolved_artist_key="artist",
        )
        assert feats["f_qualifier_match"] == 0.3

    def test_artist_consistency_mbid_match(self):
        c = self._FakeRec(title="Song", artist_mbid="abc-123")
        feats = score_recording_candidate(
            c,
            query_title_key="song",
            query_title_display="Song",
            query_qualifiers=set(),
            local_duration=None,
            resolved_artist_mbid="abc-123",
            resolved_artist_key="artist",
        )
        assert feats["f_artist_consistency"] == 1.0

    def test_artist_consistency_mbid_mismatch(self):
        c = self._FakeRec(title="Song", artist_mbid="abc-123")
        feats = score_recording_candidate(
            c,
            query_title_key="song",
            query_title_display="Song",
            query_qualifiers=set(),
            local_duration=None,
            resolved_artist_mbid="xyz-789",
            resolved_artist_key="artist",
        )
        assert feats["f_artist_consistency"] == 0.0


class TestScoreReleaseCandidate:
    """Release feature scoring."""

    @dataclass
    class _FakeRelease:
        title: str = ""
        mbid: str = ""
        artist_name: str = ""
        artist_mbid: str = ""
        year: Optional[int] = None
        provider: str = "musicbrainz"
        raw: Optional[Dict[str, Any]] = None

    def test_year_exact(self):
        c = self._FakeRelease(title="Album", year=1997)
        feats = score_release_candidate(
            c,
            query_album_key="album",
            query_album_display="Album",
            query_year=1997,
            resolved_artist_mbid=None,
            resolved_artist_key="artist",
        )
        assert feats["f_release_year_match"] == 1.0

    def test_year_off_by_2(self):
        c = self._FakeRelease(title="Album", year=1999)
        feats = score_release_candidate(
            c,
            query_album_key="album",
            query_album_display="Album",
            query_year=1997,
            resolved_artist_mbid=None,
            resolved_artist_key="artist",
        )
        # 1.0 - 2/5.0 = 0.6
        assert abs(feats["f_release_year_match"] - 0.6) < 0.01

    def test_year_off_by_5(self):
        c = self._FakeRelease(title="Album", year=2002)
        feats = score_release_candidate(
            c,
            query_album_key="album",
            query_album_display="Album",
            query_year=1997,
            resolved_artist_mbid=None,
            resolved_artist_key="artist",
        )
        assert feats["f_release_year_match"] == 0.0

    def test_year_unknown(self):
        c = self._FakeRelease(title="Album", year=None)
        feats = score_release_candidate(
            c,
            query_album_key="album",
            query_album_display="Album",
            query_year=None,
            resolved_artist_mbid=None,
            resolved_artist_key="artist",
        )
        assert feats["f_release_year_match"] == 0.5


# ── Hysteresis tests ─────────────────────────────────────────────────────

from app.matching.hysteresis import should_update_match, HYSTERESIS_DELTA


class TestShouldUpdateMatch:
    """Pure-function hysteresis decision tests."""

    def test_pinned_never_updates(self):
        assert should_update_match(
            old_score=60.0, new_score=95.0,
            old_mbid="a", new_mbid="b",
            old_mbid_still_present=True,
            is_user_pinned=True,
        ) is False

    def test_first_resolve(self):
        """old_score=0 → always accept (first resolve)."""
        assert should_update_match(
            old_score=0.0, new_score=50.0,
            old_mbid=None, new_mbid="new-mbid",
            old_mbid_still_present=True,
            is_user_pinned=False,
        ) is True

    def test_old_mbid_gone(self):
        """If old MBID no longer returned, accept the new one."""
        assert should_update_match(
            old_score=80.0, new_score=75.0,
            old_mbid="gone-mbid", new_mbid="new-mbid",
            old_mbid_still_present=False,
            is_user_pinned=False,
        ) is True

    def test_score_delta_sufficient(self):
        """Improvement >= delta → update."""
        assert should_update_match(
            old_score=70.0, new_score=78.0,
            old_mbid="a", new_mbid="b",
            old_mbid_still_present=True,
            is_user_pinned=False,
            delta=HYSTERESIS_DELTA,  # 8.0
        ) is True

    def test_score_delta_insufficient(self):
        """Improvement < delta → keep old."""
        assert should_update_match(
            old_score=70.0, new_score=77.0,
            old_mbid="a", new_mbid="b",
            old_mbid_still_present=True,
            is_user_pinned=False,
            delta=HYSTERESIS_DELTA,
        ) is False

    def test_score_decrease_keeps_old(self):
        """Score went down → keep old."""
        assert should_update_match(
            old_score=80.0, new_score=75.0,
            old_mbid="a", new_mbid="b",
            old_mbid_still_present=True,
            is_user_pinned=False,
        ) is False

    def test_custom_delta(self):
        """Custom delta override."""
        assert should_update_match(
            old_score=70.0, new_score=73.0,
            old_mbid="a", new_mbid="b",
            old_mbid_still_present=True,
            is_user_pinned=False,
            delta=3.0,
        ) is True

    def test_exact_delta_boundary(self):
        """Exactly at delta threshold → update."""
        assert should_update_match(
            old_score=70.0, new_score=78.0,
            old_mbid="a", new_mbid="b",
            old_mbid_still_present=True,
            is_user_pinned=False,
            delta=8.0,
        ) is True


# ── Classification tests ─────────────────────────────────────────────────

class TestClassifyScore:
    """Score → MatchStatus classification."""

    def test_matched_high(self):
        assert classify_score(90.0) == MatchStatus.MATCHED_HIGH

    def test_matched_high_boundary(self):
        assert classify_score(85.0) == MatchStatus.MATCHED_HIGH

    def test_matched_medium(self):
        assert classify_score(75.0) == MatchStatus.MATCHED_MEDIUM

    def test_matched_medium_boundary(self):
        assert classify_score(70.0) == MatchStatus.MATCHED_MEDIUM

    def test_needs_review(self):
        assert classify_score(60.0) == MatchStatus.NEEDS_REVIEW

    def test_needs_review_boundary(self):
        assert classify_score(50.0) == MatchStatus.NEEDS_REVIEW

    def test_unmatched(self):
        assert classify_score(30.0) == MatchStatus.UNMATCHED

    def test_unmatched_zero(self):
        assert classify_score(0.0) == MatchStatus.UNMATCHED

    def test_perfect(self):
        assert classify_score(100.0) == MatchStatus.MATCHED_HIGH


# ── Deterministic tie-breaking ────────────────────────────────────────────

class TestScoredCandidateSortKey:
    """Deterministic sort key for ScoredCandidate."""

    def test_higher_score_first(self):
        c1 = ScoredCandidate(entity_type="recording", score=80.0, canonical_name="A")
        c2 = ScoredCandidate(entity_type="recording", score=90.0, canonical_name="B")
        assert sorted([c1, c2], key=lambda x: x.sort_key())[0] == c2

    def test_mbid_preferred(self):
        """At same score, candidate with MBID wins."""
        c1 = ScoredCandidate(entity_type="recording", score=80.0,
                              candidate_id=None, canonical_name="A")
        c2 = ScoredCandidate(entity_type="recording", score=80.0,
                              candidate_id="mbid-123", canonical_name="B")
        assert sorted([c1, c2], key=lambda x: x.sort_key())[0] == c2

    def test_alphabetical_tiebreak(self):
        """At same score and both have MBIDs, sort alphabetically by MBID."""
        c1 = ScoredCandidate(entity_type="recording", score=80.0,
                              candidate_id="bbb", canonical_name="X")
        c2 = ScoredCandidate(entity_type="recording", score=80.0,
                              candidate_id="aaa", canonical_name="Y")
        assert sorted([c1, c2], key=lambda x: x.sort_key())[0] == c2

    def test_deterministic_with_many_candidates(self):
        """Sorting should be same regardless of input order."""
        cs = [
            ScoredCandidate(entity_type="r", score=80, candidate_id="ccc", canonical_name="C"),
            ScoredCandidate(entity_type="r", score=80, candidate_id="aaa", canonical_name="A"),
            ScoredCandidate(entity_type="r", score=90, candidate_id="bbb", canonical_name="B"),
        ]
        sorted1 = sorted(cs, key=lambda x: x.sort_key())
        sorted2 = sorted(reversed(cs), key=lambda x: x.sort_key())
        assert [c.candidate_id for c in sorted1] == [c.candidate_id for c in sorted2]


# ── Overall score computation ─────────────────────────────────────────────

class TestComputeOverallScore:
    """compute_overall_score composition tests."""

    def test_perfect_artist_recording_no_album(self):
        artist_f = {
            "f_artist_exact": 1.0, "f_artist_alias": 0.0,
            "f_artist_similarity": 1.0, "f_artist_disambiguation": 1.0,
            "f_provider_prior": 1.0,
        }
        rec_f = {
            "f_title_exact": 1.0, "f_title_similarity": 1.0,
            "f_qualifier_match": 1.0, "f_duration_match": 1.0,
            "f_artist_consistency": 1.0,
        }
        bd = compute_overall_score(artist_f, rec_f, has_album_data=False)
        # With f_artist_alias=0.0, artist category < 1.0, so overall < 100
        assert bd.overall_score > 90.0
        assert bd.status == MatchStatus.MATCHED_HIGH

    def test_zero_all(self):
        artist_f = {
            "f_artist_exact": 0.0, "f_artist_alias": 0.0,
            "f_artist_similarity": 0.0, "f_artist_disambiguation": 0.0,
            "f_provider_prior": 0.0,
        }
        rec_f = {
            "f_title_exact": 0.0, "f_title_similarity": 0.0,
            "f_qualifier_match": 0.0, "f_duration_match": 0.0,
            "f_artist_consistency": 0.0,
        }
        bd = compute_overall_score(artist_f, rec_f, has_album_data=False)
        assert bd.overall_score == 0.0
        assert bd.status == MatchStatus.UNMATCHED

    def test_cross_source_bonus(self):
        """Cross-source agreement adds up to 5 points."""
        artist_f = {
            "f_artist_exact": 1.0, "f_artist_alias": 0.0,
            "f_artist_similarity": 1.0, "f_artist_disambiguation": 0.0,
            "f_provider_prior": 1.0,
        }
        rec_f = {
            "f_title_exact": 1.0, "f_title_similarity": 1.0,
            "f_qualifier_match": 1.0, "f_duration_match": 0.5,
            "f_artist_consistency": 1.0,
        }
        bd_no_bonus = compute_overall_score(artist_f, rec_f, has_album_data=False,
                                             cross_source_agreement=0.0)
        bd_with_bonus = compute_overall_score(artist_f, rec_f, has_album_data=False,
                                               cross_source_agreement=1.0)
        diff = bd_with_bonus.overall_score - bd_no_bonus.overall_score
        assert abs(diff - 5.0) < 0.01

    def test_capped_at_100(self):
        """Perfect score + bonus should not exceed 100."""
        artist_f = {
            "f_artist_exact": 1.0, "f_artist_alias": 1.0,
            "f_artist_similarity": 1.0, "f_artist_disambiguation": 1.0,
            "f_provider_prior": 1.0,
        }
        rec_f = {
            "f_title_exact": 1.0, "f_title_similarity": 1.0,
            "f_qualifier_match": 1.0, "f_duration_match": 1.0,
            "f_artist_consistency": 1.0,
        }
        bd = compute_overall_score(artist_f, rec_f, has_album_data=False,
                                    cross_source_agreement=1.0)
        assert bd.overall_score <= 100.0

    def test_with_album_data(self):
        """Release weights are included when album data present."""
        artist_f = {
            "f_artist_exact": 1.0, "f_artist_alias": 0.0,
            "f_artist_similarity": 1.0, "f_artist_disambiguation": 0.0,
            "f_provider_prior": 1.0,
        }
        rec_f = {
            "f_title_exact": 1.0, "f_title_similarity": 1.0,
            "f_qualifier_match": 1.0, "f_duration_match": 1.0,
            "f_artist_consistency": 1.0,
        }
        rel_f = {
            "f_album_exact": 1.0, "f_album_similarity": 1.0,
            "f_release_year_match": 1.0, "f_album_artist_consistency": 1.0,
        }
        bd = compute_overall_score(artist_f, rec_f, rel_f, has_album_data=True)
        # With f_artist_alias=0 and f_artist_disambiguation=0, artist category < 1.0
        assert bd.overall_score > 90.0
        assert bd.category_scores["release"] > 0

    def test_weight_redistribution(self):
        """Without album data, artist+recording get 100% of weight."""
        artist_f = {"f_artist_exact": 0.5, "f_artist_alias": 0.0,
                     "f_artist_similarity": 0.5, "f_artist_disambiguation": 0.0,
                     "f_provider_prior": 0.5}
        rec_f = {"f_title_exact": 0.5, "f_title_similarity": 0.5,
                  "f_qualifier_match": 0.5, "f_duration_match": 0.5,
                  "f_artist_consistency": 0.5}
        bd = compute_overall_score(artist_f, rec_f, has_album_data=False)
        # All features at 0.5 → category scores ~0.5 (artist slightly less due to
        # feature weight distribution), overall ~47
        assert 44.0 < bd.overall_score < 52.0

    def test_breakdown_has_all_features(self):
        """Breakdown features dict should contain all feature names."""
        artist_f = {"f_artist_exact": 1.0, "f_artist_alias": 0.5,
                     "f_artist_similarity": 0.8, "f_artist_disambiguation": 0.0,
                     "f_provider_prior": 1.0}
        rec_f = {"f_title_exact": 1.0, "f_title_similarity": 0.9,
                  "f_qualifier_match": 1.0, "f_duration_match": 0.7,
                  "f_artist_consistency": 1.0}
        bd = compute_overall_score(artist_f, rec_f, has_album_data=False)
        assert "f_artist_exact" in bd.features
        assert "f_title_exact" in bd.features
        assert "f_cross_source_agreement" in bd.features


# ── Example JSON output shapes ────────────────────────────────────────────

class TestBreakdownSerialization:
    """ScoreBreakdown.to_dict() produces expected structure."""

    def test_structure(self):
        bd = ScoreBreakdown(
            features={"f_artist_exact": 1.0, "f_title_exact": 0.9},
            weighted_contributions={"f_artist_exact": 3.5, "f_title_exact": 4.95},
            category_scores={"artist": 0.85, "recording": 0.9},
            overall_score=87.5,
            status=MatchStatus.MATCHED_HIGH,
        )
        d = bd.to_dict()
        assert d["overall_score"] == 87.5
        assert d["status"] == "matched_high"
        assert "features" in d
        assert "weighted_contributions" in d
        assert "category_scores" in d

    def test_unmatched_example(self):
        bd = ScoreBreakdown(
            features={"f_artist_exact": 0.0, "f_title_exact": 0.0},
            overall_score=15.0,
            status=MatchStatus.UNMATCHED,
        )
        d = bd.to_dict()
        assert d["status"] == "unmatched"
        assert d["overall_score"] == 15.0


# ── Album sentinel sanitization tests ──────────────────────────────────

from app.services.source_validation import sanitize_album


class TestSanitizeAlbumSentinels:
    """Ensure sentinel/placeholder album values are ALWAYS rejected.

    User requirement: 'There should be no circumstance where Unknown is
    possible for the album.'  Every test scenario that returns a sentinel
    value is a failure.
    """

    # ── The core sentinel values that must always return None ──

    @pytest.mark.parametrize("value", [
        "Unknown", "unknown", "UNKNOWN", "  Unknown  ",
        "Unknown Album", "unknown album",
        "N/A", "n/a", "NA", "na",
        "None", "none", "NONE",
        "Null", "null", "NULL",
        "Nil", "nil",
        "No Album", "no album",
        "Untitled", "untitled",
        "TBD", "tbd",
        "Not Available", "not available",
        "Not Applicable", "not applicable",
        "-", "--", "\u2014", "?",
    ])
    def test_sentinel_returns_none(self, value):
        result = sanitize_album(value, title="Any Title")
        assert result is None, f"sanitize_album({value!r}) returned {result!r}, expected None"

    # ── Empty/None input ──

    def test_none_input(self):
        assert sanitize_album(None, title="Test") is None

    def test_empty_string(self):
        assert sanitize_album("", title="Test") is None or sanitize_album("", title="Test") == ""

    # ── Real album names must be preserved ──

    def test_real_album_preserved(self):
        assert sanitize_album("Lungs", title="Cosmic Love") == "Lungs"

    def test_real_album_with_spaces(self):
        assert sanitize_album("  Lungs  ", title="Cosmic Love") == "Lungs"

    # ── Single label patterns ──

    def test_single_label_matching_title(self):
        result = sanitize_album("Cosmic Love - Single", title="Cosmic Love")
        assert result is None

    def test_single_label_different_title(self):
        result = sanitize_album("Cosmic Love - Single", title="Dog Days Are Over")
        assert result is None  # All single labels cleared


class TestModelAlbumValidator:
    """Verify the SQLAlchemy @validates on VideoItem catches sentinels."""

    def test_unknown_blocked(self):
        from app.models import VideoItem
        v = VideoItem(artist="Test", title="Test", album="Unknown")
        assert v.album is None, "Model validator must block 'Unknown'"

    def test_real_album_allowed(self):
        from app.models import VideoItem
        v = VideoItem(artist="Test", title="Test", album="Lungs")
        assert v.album == "Lungs"

    @pytest.mark.parametrize("sentinel", [
        "Unknown", "N/A", "None", "Null", "Nil", "TBD",
        "No Album", "Not Available", "?",
    ])
    def test_all_sentinels_blocked_by_model(self, sentinel):
        from app.models import VideoItem
        v = VideoItem(artist="Test", title="Test")
        v.album = sentinel
        assert v.album is None, f"Model validator must block {sentinel!r}"
