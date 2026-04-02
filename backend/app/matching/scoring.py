"""
Scoring Engine — Feature computation and weighted confidence scoring.

Scoring model overview
======================
Each candidate (artist, recording, or release) is scored by computing a
vector of *features* (each 0.0–1.0), multiplying by weights, and
summing to produce a normalised 0–100 score.

Feature categories
------------------
**Artist features** (weight bucket: 30 % of overall)

| Feature                      | Description                                         |
|------------------------------|-----------------------------------------------------|
| f_artist_exact               | Exact match on comparison keys                      |
| f_artist_alias               | Matches any known alias                             |
| f_artist_similarity          | Jaro-Winkler distance on normalised names           |
| f_artist_disambiguation      | Bonus if disambiguation aligns (band vs DJ)         |
| f_provider_prior             | MusicBrainz candidates get small bonus              |

**Recording features** (weight bucket: 55 %)

| Feature                      | Description                                         |
|------------------------------|-----------------------------------------------------|
| f_title_exact                | Exact match on title keys                           |
| f_title_similarity           | Jaro-Winkler on title_base                          |
| f_qualifier_match            | Qualifiers (live/remix/…) agree                     |
| f_duration_match             | Closeness of duration (±5 s → 1.0 … ±30 s → 0)    |
| f_artist_consistency         | Candidate artist MBID matches resolved artist MBID  |

**Release features** (weight bucket: 15 % — redistributed to recording when no album data)

| Feature                      | Description                                         |
|------------------------------|-----------------------------------------------------|
| f_album_exact                | Exact match on album key                            |
| f_album_similarity           | Jaro-Winkler on album title                         |
| f_release_year_match         | Closeness of year  (±0 → 1.0, ±5 → 0)              |
| f_album_artist_consistency   | Album artist MBID agrees with resolved artist       |

**Cross-source**

| Feature                      | Description                                         |
|------------------------------|-----------------------------------------------------|
| f_cross_source_agreement     | Wikipedia-derived data agrees with MusicBrainz      |

Thresholds
----------
* ≥ 85 → MATCHED_HIGH   (auto-apply, full Kodi export)
* 70–84 → MATCHED_MEDIUM (auto-apply, mark review-optional)
* 50–69 → NEEDS_REVIEW   (no artist/album art export)
* < 50  → UNMATCHED      (keep raw strings)

All constants are exposed for configuration.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from app.matching.normalization import make_comparison_key

__all__ = [
    "MatchStatus",
    "ScoredCandidate",
    "ScoreBreakdown",
    "score_artist_candidate",
    "score_recording_candidate",
    "score_release_candidate",
    "compute_overall_score",
    "classify_score",
    "THRESHOLDS",
    "WEIGHTS",
]


# ── Enums & thresholds ───────────────────────────────────────────────────

class MatchStatus(str, Enum):
    MATCHED_HIGH = "matched_high"
    MATCHED_MEDIUM = "matched_medium"
    NEEDS_REVIEW = "needs_review"
    UNMATCHED = "unmatched"


# Configurable thresholds (0–100)
THRESHOLDS = {
    "matched_high": 85,
    "matched_medium": 70,
    "needs_review": 50,
}

# Category weight buckets  (must sum to 1.0)
WEIGHTS = {
    "artist": 0.30,
    "recording": 0.55,
    "release": 0.15,
}

# Feature weights *within* each category  (per-category sums normalised)
_ARTIST_FEATURE_WEIGHTS = {
    "f_artist_exact": 0.35,
    "f_artist_alias": 0.15,
    "f_artist_similarity": 0.30,
    "f_artist_disambiguation": 0.05,
    "f_provider_prior": 0.15,
}

_RECORDING_FEATURE_WEIGHTS = {
    "f_title_exact": 0.30,
    "f_title_similarity": 0.25,
    "f_qualifier_match": 0.10,
    "f_duration_match": 0.15,
    "f_artist_consistency": 0.20,
}

_RELEASE_FEATURE_WEIGHTS = {
    "f_album_exact": 0.30,
    "f_album_similarity": 0.25,
    "f_release_year_match": 0.25,
    "f_album_artist_consistency": 0.20,
}


# ── Score containers ─────────────────────────────────────────────────────

@dataclass
class ScoreBreakdown:
    """Full scoring breakdown for auditability."""
    features: Dict[str, float] = field(default_factory=dict)
    weighted_contributions: Dict[str, float] = field(default_factory=dict)
    category_scores: Dict[str, float] = field(default_factory=dict)  # artist / recording / release
    overall_score: float = 0.0
    status: MatchStatus = MatchStatus.UNMATCHED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "features": self.features,
            "weighted_contributions": self.weighted_contributions,
            "category_scores": self.category_scores,
            "overall_score": self.overall_score,
            "status": self.status.value,
        }


@dataclass
class ScoredCandidate:
    """A scored candidate with full breakdown, ready for ranking."""
    entity_type: str  # "artist" | "recording" | "release"
    candidate_id: Optional[str] = None  # MBID or synthetic id
    canonical_name: str = ""
    provider: str = ""
    score: float = 0.0
    breakdown: ScoreBreakdown = field(default_factory=ScoreBreakdown)
    raw: Optional[Dict[str, Any]] = None

    def sort_key(self):
        """Deterministic sort key: score desc → has_mbid desc → mbid asc → name asc."""
        has_mbid = 1 if self.candidate_id else 0
        return (
            -self.score,
            -has_mbid,
            self.candidate_id or "",
            self.canonical_name,
        )


# ── String similarity ────────────────────────────────────────────────────

def _jaro_winkler(s1: str, s2: str) -> float:
    """
    Jaro-Winkler similarity (0.0–1.0).  Prefers matching prefixes.

    Falls back to stdlib SequenceMatcher for robustness if strings
    are very short.
    """
    if not s1 or not s2:
        return 0.0
    if s1 == s2:
        return 1.0

    len1, len2 = len(s1), len(s2)
    max_dist = max(len1, len2) // 2 - 1
    if max_dist < 0:
        max_dist = 0

    s1_matches = [False] * len1
    s2_matches = [False] * len2

    matches = 0
    transpositions = 0

    for i in range(len1):
        start = max(0, i - max_dist)
        end = min(i + max_dist + 1, len2)
        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1

    jaro = (
        matches / len1
        + matches / len2
        + (matches - transpositions / 2) / matches
    ) / 3.0

    # Winkler bonus for common prefix (up to 4 chars)
    prefix = 0
    for i in range(min(4, len1, len2)):
        if s1[i] == s2[i]:
            prefix += 1
        else:
            break
    return jaro + prefix * 0.1 * (1 - jaro)


def _token_set_similarity(s1: str, s2: str) -> float:
    """
    Token-set similarity — order-independent word matching.

    Handles "Foo Fighters" vs "The Foo Fighters" gracefully.
    """
    t1 = set(s1.lower().split())
    t2 = set(s2.lower().split())
    if not t1 or not t2:
        return 0.0
    intersection = t1 & t2
    union = t1 | t2
    return len(intersection) / len(union) if union else 0.0


def string_similarity(s1: str, s2: str) -> float:
    """
    Combined string similarity taking the max of Jaro-Winkler and
    token-set ratio.  Operates on comparison keys.
    """
    k1 = make_comparison_key(s1)
    k2 = make_comparison_key(s2)
    jw = _jaro_winkler(k1, k2)
    ts = _token_set_similarity(k1, k2)
    return max(jw, ts)


# ── Feature computation ──────────────────────────────────────────────────

def score_artist_candidate(
    candidate,
    *,
    query_artist_key: str,
    query_artist_display: str,
) -> Dict[str, float]:
    """
    Compute artist feature vector for a single ``ArtistCandidate``.

    Returns dict of feature_name → value (0.0–1.0).
    """
    c_key = make_comparison_key(candidate.canonical_name)

    # f_artist_exact
    f_exact = 1.0 if c_key == query_artist_key else 0.0

    # f_artist_alias
    alias_keys = [make_comparison_key(a) for a in (candidate.aliases or [])]
    f_alias = 1.0 if query_artist_key in alias_keys else 0.0

    # f_artist_similarity
    f_sim = string_similarity(query_artist_display, candidate.canonical_name)

    # f_artist_disambiguation_bonus
    # Small bonus if disambiguation contains "band" or "group" (common for music)
    f_disambig = 0.0
    if candidate.disambiguation:
        d = candidate.disambiguation.lower()
        if any(kw in d for kw in ("band", "group", "musician", "singer", "rapper", "artist")):
            f_disambig = 1.0

    # f_provider_prior — prefer MusicBrainz
    f_prior = 1.0 if candidate.provider == "musicbrainz" else 0.3

    return {
        "f_artist_exact": f_exact,
        "f_artist_alias": f_alias,
        "f_artist_similarity": f_sim,
        "f_artist_disambiguation": f_disambig,
        "f_provider_prior": f_prior,
    }


def score_recording_candidate(
    candidate,
    *,
    query_title_key: str,
    query_title_display: str,
    query_qualifiers: Set[str],
    local_duration: Optional[float],
    resolved_artist_mbid: Optional[str],
    resolved_artist_key: str,
) -> Dict[str, float]:
    """Compute recording feature vector for a ``RecordingCandidate``."""
    c_key = make_comparison_key(candidate.title)

    # f_title_exact
    f_exact = 1.0 if c_key == query_title_key else 0.0

    # f_title_similarity
    f_sim = string_similarity(query_title_display, candidate.title)

    # f_qualifier_match  (1.0 if qualifiers fully agree, 0.5 if partial)
    # For now, check if candidate title hints at qualifiers (live, remix, …)
    c_lower = candidate.title.lower()
    candidate_quals = set()
    for q in ("live", "acoustic", "unplugged", "demo", "instrumental",
              "radio edit", "extended mix", "remix", "remaster", "remastered"):
        if q in c_lower:
            candidate_quals.add(q)

    if not query_qualifiers and not candidate_quals:
        f_qual = 1.0  # both "normal" versions
    elif query_qualifiers == candidate_quals:
        f_qual = 1.0
    elif query_qualifiers & candidate_quals:
        f_qual = 0.5
    elif query_qualifiers and not candidate_quals:
        f_qual = 0.3  # query has qualifiers, candidate is plain
    elif candidate_quals and not query_qualifiers:
        f_qual = 0.3  # candidate has qualifiers, query is plain
    else:
        f_qual = 0.0

    # f_duration_match  (within ±5 s → 1.0, ±30 s → 0.0, linear)
    f_dur = 0.5  # neutral if either side lacks data
    if local_duration and candidate.duration_seconds:
        diff = abs(local_duration - candidate.duration_seconds)
        if diff <= 5.0:
            f_dur = 1.0
        elif diff >= 30.0:
            f_dur = 0.0
        else:
            f_dur = 1.0 - (diff - 5.0) / 25.0

    # f_artist_consistency — candidate artist matches resolved artist
    f_aconsist = 0.5  # neutral default
    if resolved_artist_mbid and candidate.artist_mbid:
        f_aconsist = 1.0 if resolved_artist_mbid == candidate.artist_mbid else 0.0
    elif candidate.artist_name:
        f_aconsist = string_similarity(resolved_artist_key, make_comparison_key(candidate.artist_name))

    return {
        "f_title_exact": f_exact,
        "f_title_similarity": f_sim,
        "f_qualifier_match": f_qual,
        "f_duration_match": f_dur,
        "f_artist_consistency": f_aconsist,
    }


def score_release_candidate(
    candidate,
    *,
    query_album_key: str,
    query_album_display: str,
    query_year: Optional[int],
    resolved_artist_mbid: Optional[str],
    resolved_artist_key: str,
) -> Dict[str, float]:
    """Compute release feature vector for a ``ReleaseCandidate``."""
    c_key = make_comparison_key(candidate.title)

    # f_album_exact
    f_exact = 1.0 if c_key == query_album_key else 0.0

    # f_album_similarity
    f_sim = string_similarity(query_album_display, candidate.title)

    # f_release_year_match  (±0 → 1.0 … ±5 → 0.0)
    f_year = 0.5  # neutral if unknown
    if query_year and candidate.year:
        diff = abs(query_year - candidate.year)
        if diff == 0:
            f_year = 1.0
        elif diff >= 5:
            f_year = 0.0
        else:
            f_year = 1.0 - diff / 5.0

    # f_album_artist_consistency
    f_aconsist = 0.5
    if resolved_artist_mbid and candidate.artist_mbid:
        f_aconsist = 1.0 if resolved_artist_mbid == candidate.artist_mbid else 0.0
    elif candidate.artist_name:
        f_aconsist = string_similarity(resolved_artist_key, make_comparison_key(candidate.artist_name))

    return {
        "f_album_exact": f_exact,
        "f_album_similarity": f_sim,
        "f_release_year_match": f_year,
        "f_album_artist_consistency": f_aconsist,
    }


# ── Category → overall score ─────────────────────────────────────────────

def _weighted_category_score(
    features: Dict[str, float],
    feature_weights: Dict[str, float],
) -> float:
    """Weighted sum within a category (normalised to 0.0–1.0)."""
    total_weight = sum(feature_weights.values())
    if total_weight == 0:
        return 0.0
    score = sum(features.get(k, 0.0) * w for k, w in feature_weights.items())
    return score / total_weight


def compute_overall_score(
    artist_features: Dict[str, float],
    recording_features: Dict[str, float],
    release_features: Optional[Dict[str, float]] = None,
    *,
    cross_source_agreement: float = 0.0,
    has_album_data: bool = False,
    version_type: str = "normal",
) -> ScoreBreakdown:
    """
    Combine per-category features into a single 0–100 score with
    full breakdown.

    If ``has_album_data`` is False, the release weight bucket is
    redistributed proportionally to artist and recording.

    ``version_type`` adjusts scoring behaviour:
    - "cover": penalise artist-consistency so we don't merge with original
    - "live": boost qualifier-match importance
    - "alternate": boost title similarity, reduce duration weight
    """
    breakdown = ScoreBreakdown()

    # Compute category scores (0.0–1.0)
    cat_artist = _weighted_category_score(artist_features, _ARTIST_FEATURE_WEIGHTS)
    cat_recording = _weighted_category_score(recording_features, _RECORDING_FEATURE_WEIGHTS)
    cat_release = 0.0
    if release_features and has_album_data:
        cat_release = _weighted_category_score(release_features, _RELEASE_FEATURE_WEIGHTS)

    breakdown.category_scores = {
        "artist": round(cat_artist, 4),
        "recording": round(cat_recording, 4),
        "release": round(cat_release, 4),
    }

    # --- Version-type scoring adjustments ---
    # For covers: reduce artist weight (performer ≠ original artist) and
    # increase recording weight (the song itself matters more).
    # For live: boost recording weight to prioritise qualifier matching.
    # For alternate: reduce duration weight implicitly (different cuts).
    w_artist = WEIGHTS["artist"]
    w_recording = WEIGHTS["recording"]
    w_release = WEIGHTS["release"]

    if version_type == "cover":
        # Covers: artist identity less important, recording more important
        w_artist = 0.15
        w_recording = 0.70
        w_release = 0.15
    elif version_type == "live":
        # Live: recording identity (especially qualifier match) matters most
        w_artist = 0.25
        w_recording = 0.60
        w_release = 0.15

    if not has_album_data or not release_features:
        # Redistribute release weight proportionally
        total_ar = w_artist + w_recording
        w_artist = w_artist / total_ar
        w_recording = w_recording / total_ar
        w_release = 0.0

    # Weighted overall (0.0–1.0)
    raw_score = (
        cat_artist * w_artist
        + cat_recording * w_recording
        + cat_release * w_release
    )

    # Cross-source agreement bonus (max +5 points)
    bonus = cross_source_agreement * 5.0

    overall_100 = min(100.0, raw_score * 100.0 + bonus)

    # Collect all features
    all_features = {}
    all_features.update(artist_features)
    all_features.update(recording_features)
    if release_features:
        all_features.update(release_features)
    all_features["f_cross_source_agreement"] = cross_source_agreement
    breakdown.features = {k: round(v, 4) for k, v in all_features.items()}

    # Weighted contributions (for auditability)
    contributions = {}
    for k, v in artist_features.items():
        w_feat = _ARTIST_FEATURE_WEIGHTS.get(k, 0)
        contributions[k] = round(v * w_feat * w_artist * 100, 2)
    for k, v in recording_features.items():
        w_feat = _RECORDING_FEATURE_WEIGHTS.get(k, 0)
        contributions[k] = round(v * w_feat * w_recording * 100, 2)
    if release_features:
        for k, v in release_features.items():
            w_feat = _RELEASE_FEATURE_WEIGHTS.get(k, 0)
            contributions[k] = round(v * w_feat * w_release * 100, 2)
    contributions["f_cross_source_agreement"] = round(bonus, 2)
    breakdown.weighted_contributions = contributions

    breakdown.overall_score = round(overall_100, 2)
    breakdown.status = classify_score(overall_100)

    return breakdown


def classify_score(score: float) -> MatchStatus:
    """Map a 0–100 score to a MatchStatus enum."""
    if score >= THRESHOLDS["matched_high"]:
        return MatchStatus.MATCHED_HIGH
    elif score >= THRESHOLDS["matched_medium"]:
        return MatchStatus.MATCHED_MEDIUM
    elif score >= THRESHOLDS["needs_review"]:
        return MatchStatus.NEEDS_REVIEW
    else:
        return MatchStatus.UNMATCHED
