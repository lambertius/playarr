"""
Recommendation Ranker — Composites trust, popularity, trend, and feedback
signals into a final recommendation_score for each candidate.

Architecture:
  - RecommendationCandidate: lightweight data object for a candidate video.
  - RecommendationRanker: stateless scorer that produces recommendation_score.
  - FeedbackAdjuster: adjusts scores using historical user feedback.

The ranker is deliberately simple — it's a weighted linear combination that
can be replaced with a learned model later without changing the interface.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional
from app.new_videos.trust_scoring import TrustResult

logger = logging.getLogger(__name__)

# Default weight profile — tuned for "high-quality official music videos"
DEFAULT_WEIGHTS = {
    "trust": 0.40,
    "popularity": 0.25,
    "trend": 0.15,
    "feedback": 0.10,
    "freshness": 0.10,
}

# Category-specific weight overrides
CATEGORY_WEIGHTS = {
    "new": {"trust": 0.35, "popularity": 0.10, "trend": 0.20, "feedback": 0.10, "freshness": 0.25},
    "popular": {"trust": 0.35, "popularity": 0.40, "trend": 0.05, "feedback": 0.10, "freshness": 0.10},
    "rising": {"trust": 0.30, "popularity": 0.10, "trend": 0.40, "feedback": 0.10, "freshness": 0.10},
    "by_artist": {"trust": 0.40, "popularity": 0.25, "trend": 0.05, "feedback": 0.15, "freshness": 0.15},
    "taste": {"trust": 0.35, "popularity": 0.15, "trend": 0.10, "feedback": 0.25, "freshness": 0.15},
    "famous": {"trust": 0.30, "popularity": 0.45, "trend": 0.00, "feedback": 0.10, "freshness": 0.15},
}


@dataclass
class RecommendationCandidate:
    """Input to the ranker — one candidate music video."""
    provider: str = "youtube"
    provider_video_id: str = ""
    url: str = ""
    title: str = ""
    artist: str = ""
    album: Optional[str] = None
    channel: str = ""
    thumbnail_url: str = ""
    duration_seconds: Optional[int] = None
    release_date: Optional[str] = None
    view_count: Optional[int] = None
    category: str = "popular"
    description: Optional[str] = None
    metadata: Optional[dict] = None

    # Pre-computed scores (some may be set by the source strategy)
    trust_result: Optional[TrustResult] = None
    popularity_score: float = 0.0
    trend_score: float = 0.0
    freshness_score: float = 0.0

    # Filled by FeedbackAdjuster
    feedback_adjustment: float = 0.0

    # Explainability
    reasons: list = field(default_factory=list)


class RecommendationRanker:
    """Scores candidates using weighted signal combination.

    Usage:
        ranker = RecommendationRanker()
        for candidate in candidates:
            score = ranker.score(candidate)
    """

    def __init__(self, weight_overrides: Optional[dict] = None):
        self._weight_overrides = weight_overrides or {}

    def _weights_for(self, category: str) -> dict:
        base = CATEGORY_WEIGHTS.get(category, DEFAULT_WEIGHTS).copy()
        base.update(self._weight_overrides)
        return base

    def score(self, candidate: RecommendationCandidate) -> float:
        """Compute final recommendation_score for a candidate."""
        w = self._weights_for(candidate.category)
        trust = candidate.trust_result.score if candidate.trust_result else 0.5
        pop = candidate.popularity_score
        trend = candidate.trend_score
        fb = candidate.feedback_adjustment
        fresh = candidate.freshness_score

        score = (
            w["trust"] * trust
            + w["popularity"] * pop
            + w["trend"] * trend
            + w["feedback"] * fb
            + w["freshness"] * fresh
        )
        return max(0.0, min(1.0, round(score, 4)))


class FeedbackAdjuster:
    """Adjusts candidate scores using historical user feedback.

    Takes aggregated feedback stats (per-artist or per-category) and produces
    a feedback_adjustment value between -0.3 and +0.3.

    This is the seam where a future ML model plugs in — it just needs to
    implement `adjust(candidate) -> float`.
    """

    def __init__(self, artist_add_counts: Optional[dict] = None,
                 artist_dismiss_counts: Optional[dict] = None,
                 category_add_counts: Optional[dict] = None,
                 category_dismiss_counts: Optional[dict] = None,
                 trusted_channels: Optional[set] = None):
        self._artist_adds = artist_add_counts or {}
        self._artist_dismisses = artist_dismiss_counts or {}
        self._cat_adds = category_add_counts or {}
        self._cat_dismisses = category_dismiss_counts or {}
        self._trusted_channels = trusted_channels or set()

    def adjust(self, candidate: RecommendationCandidate) -> float:
        """Return feedback adjustment for a candidate (-0.3 to +0.3)."""
        adj = 0.0
        artist_key = (candidate.artist or "").lower().strip()
        cat = candidate.category

        # Artist-level signal
        a_adds = self._artist_adds.get(artist_key, 0)
        a_dismisses = self._artist_dismisses.get(artist_key, 0)
        if a_adds > 0:
            adj += min(0.15, a_adds * 0.03)
        if a_dismisses > 2:
            adj -= min(0.15, a_dismisses * 0.03)

        # Category-level signal
        c_adds = self._cat_adds.get(cat, 0)
        c_dismisses = self._cat_dismisses.get(cat, 0)
        if c_adds > 0:
            adj += min(0.10, c_adds * 0.02)
        if c_dismisses > 3:
            adj -= min(0.10, c_dismisses * 0.02)

        # Trusted channel bonus
        channel_norm = (candidate.channel or "").lower().strip()
        if channel_norm and channel_norm in self._trusted_channels:
            adj += 0.05
            candidate.reasons.append("Previously trusted channel")

        return max(-0.3, min(0.3, round(adj, 4)))
