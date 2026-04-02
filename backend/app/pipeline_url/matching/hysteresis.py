# AUTO-SEPARATED from matching/hysteresis.py for pipeline_url pipeline
# This file is independent — changes here do NOT affect the other pipeline.
"""
Hysteresis & Pinning — Anti-flap logic for match stability.

Key rules
---------
1. **Pinning**: If a user explicitly chose a match via "Fix Match",
   ``is_user_pinned=True`` and the resolver never auto-changes it.
   Only an explicit ``unpin`` clears this.

2. **Score delta threshold**: On refresh, a new match only replaces
   the current match if ``new_score - old_score >= HYSTERESIS_DELTA``.
   Default Δ = 8 points (configurable).

3. **Invalidity override**: If the previously-matched MBID is no longer
   returned by any provider (e.g. merged/deleted), the new best match
   is accepted regardless of Δ.

All functions are pure (no DB access) for easy testing.
"""
from __future__ import annotations

from typing import Optional

__all__ = [
    "HYSTERESIS_DELTA",
    "should_update_match",
    "is_pinned",
]

# Configurable: minimum score improvement to replace current match
HYSTERESIS_DELTA: float = 8.0


def should_update_match(
    *,
    old_score: float,
    new_score: float,
    old_mbid: Optional[str],
    new_mbid: Optional[str],
    old_mbid_still_present: bool,
    is_user_pinned: bool,
    delta: float = HYSTERESIS_DELTA,
) -> bool:
    """
    Decide whether a new candidate should replace the current match.

    Returns ``True`` if the match should be updated.

    Decision tree:
    1. If user-pinned → never auto-update (return False)
    2. If old match has no score (first resolve) → accept (return True)
    3. If old MBID is no longer present → accept new (return True)
    4. If new_score - old_score >= delta → accept (return True)
    5. Otherwise → keep old match (return False)
    """
    # 1. Pinned → stable
    if is_user_pinned:
        return False

    # 2. First resolve (no previous score)
    if old_score <= 0:
        return True

    # 3. Old MBID gone from provider results
    if old_mbid and not old_mbid_still_present:
        return True

    # 4. Score delta
    if new_score - old_score >= delta:
        return True

    # 5. Keep existing
    return False


def is_pinned(video_id: int, db) -> bool:
    """Check if a video has a user-pinned match."""
    from app.pipeline_url.matching.models import UserPinnedMatch
    return db.query(UserPinnedMatch).filter(
        UserPinnedMatch.video_id == video_id,
    ).first() is not None
