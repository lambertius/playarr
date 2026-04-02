"""
Feedback Service — Records user interactions and computes feedback aggregates
for the FeedbackAdjuster.

This is the learning foundation. It stores raw events and provides
aggregate queries that the ranker uses to boost/penalize candidates.
"""
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.new_videos.models import RecommendationFeedback, SuggestedVideo

logger = logging.getLogger(__name__)

# Feedback event types
FEEDBACK_TYPES = {
    "viewed", "opened_source", "added", "added_to_cart",
    "dismissed", "permanently_dismissed", "imported",
    "import_failed", "removed_from_cart",
}


def record_feedback(
    db: Session,
    feedback_type: str,
    suggested_video_id: Optional[int] = None,
    provider: Optional[str] = None,
    provider_video_id: Optional[str] = None,
    artist: Optional[str] = None,
    category: Optional[str] = None,
    context: Optional[dict] = None,
) -> RecommendationFeedback:
    """Record a single feedback event."""
    if feedback_type not in FEEDBACK_TYPES:
        logger.warning(f"Unknown feedback type: {feedback_type}")

    fb = RecommendationFeedback(
        suggested_video_id=suggested_video_id,
        feedback_type=feedback_type,
        provider=provider,
        provider_video_id=provider_video_id,
        artist=artist,
        category=category,
        created_at=datetime.now(timezone.utc),
        context_json=context,
    )
    db.add(fb)
    db.flush()
    return fb


def get_artist_feedback_counts(db: Session) -> tuple[dict, dict]:
    """Return (artist_add_counts, artist_dismiss_counts) dicts.

    Keys are lowercased artist names. Values are event counts.
    """
    add_types = {"added", "added_to_cart", "imported"}
    dismiss_types = {"dismissed", "permanently_dismissed"}

    rows = (
        db.query(
            func.lower(RecommendationFeedback.artist),
            RecommendationFeedback.feedback_type,
            func.count(),
        )
        .filter(RecommendationFeedback.artist.isnot(None))
        .group_by(func.lower(RecommendationFeedback.artist), RecommendationFeedback.feedback_type)
        .all()
    )

    adds: dict[str, int] = defaultdict(int)
    dismisses: dict[str, int] = defaultdict(int)

    for artist, fb_type, cnt in rows:
        if fb_type in add_types:
            adds[artist] += cnt
        elif fb_type in dismiss_types:
            dismisses[artist] += cnt

    return dict(adds), dict(dismisses)


def get_category_feedback_counts(db: Session) -> tuple[dict, dict]:
    """Return (category_add_counts, category_dismiss_counts) dicts."""
    add_types = {"added", "added_to_cart", "imported"}
    dismiss_types = {"dismissed", "permanently_dismissed"}

    rows = (
        db.query(
            RecommendationFeedback.category,
            RecommendationFeedback.feedback_type,
            func.count(),
        )
        .filter(RecommendationFeedback.category.isnot(None))
        .group_by(RecommendationFeedback.category, RecommendationFeedback.feedback_type)
        .all()
    )

    adds: dict[str, int] = defaultdict(int)
    dismisses: dict[str, int] = defaultdict(int)

    for cat, fb_type, cnt in rows:
        if fb_type in add_types:
            adds[cat] += cnt
        elif fb_type in dismiss_types:
            dismisses[cat] += cnt

    return dict(adds), dict(dismisses)


def get_trusted_channels(db: Session, min_imports: int = 2) -> set[str]:
    """Return set of lowercased channel names that have led to successful imports.

    A channel is "trusted" if it has been the source for >=min_imports successful
    imports (feedback_type == 'imported').
    """
    rows = (
        db.query(func.lower(SuggestedVideo.channel), func.count())
        .join(RecommendationFeedback, RecommendationFeedback.suggested_video_id == SuggestedVideo.id)
        .filter(
            RecommendationFeedback.feedback_type == "imported",
            SuggestedVideo.channel.isnot(None),
        )
        .group_by(func.lower(SuggestedVideo.channel))
        .having(func.count() >= min_imports)
        .all()
    )
    return {r[0] for r in rows}
