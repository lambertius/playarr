# AUTO-SEPARATED from services/duplicate_detection.py for pipeline_url pipeline
# This file is independent — changes here do NOT affect the other pipeline.
"""
Duplicate Detection Service — Enhanced duplicate detection using canonical tracks.

Detection priority:
1. canonical_track_id — same canonical track = same song (different versions OK)
2. provider + provider_video_id — same source URL
3. audio fingerprint (acoustid_id) — same audio content
4. Normalized artist + title (fallback, weakest signal)

The system distinguishes between:
- True duplicates: same source URL, same video
- Version duplicates: same song, different video (live, alternate, etc.)
- False positives: different songs that happen to share a title
"""
import logging
from typing import List, Optional, Dict, Any

from sqlalchemy.orm import Session

from app.models import VideoItem, Source
from app.pipeline_url.matching.normalization import make_comparison_key

logger = logging.getLogger(__name__)


class DuplicateMatch:
    """Represents a potential duplicate found in the library."""

    def __init__(
        self,
        video_id: int,
        match_type: str,  # "exact_source", "canonical_track", "fingerprint", "name_match"
        confidence: float,
        details: str = "",
    ):
        self.video_id = video_id
        self.match_type = match_type
        self.confidence = confidence
        self.details = details


def detect_duplicates(
    db: Session,
    *,
    provider: Optional[str] = None,
    provider_video_id: Optional[str] = None,
    canonical_track_id: Optional[int] = None,
    acoustid_id: Optional[str] = None,
    artist: Optional[str] = None,
    title: Optional[str] = None,
    exclude_video_id: Optional[int] = None,
) -> List[DuplicateMatch]:
    """
    Find potential duplicates in the library.

    Returns a list of DuplicateMatch objects, ordered by confidence (highest first).
    """
    matches: List[DuplicateMatch] = []
    seen_ids: set = set()

    if exclude_video_id:
        seen_ids.add(exclude_video_id)

    # 1. Exact source match (strongest — same provider + video ID)
    if provider and provider_video_id:
        source = db.query(Source).filter(
            Source.provider == provider,
            Source.source_video_id == provider_video_id,
        ).first()
        if source and source.video_id not in seen_ids:
            matches.append(DuplicateMatch(
                video_id=source.video_id,
                match_type="exact_source",
                confidence=1.0,
                details=f"Same source: {provider}/{provider_video_id}",
            ))
            seen_ids.add(source.video_id)

    # 2. Canonical track match (same song, potentially different version)
    if canonical_track_id:
        videos = db.query(VideoItem).filter(
            VideoItem.track_id == canonical_track_id,
        ).all()
        for v in videos:
            if v.id not in seen_ids:
                matches.append(DuplicateMatch(
                    video_id=v.id,
                    match_type="canonical_track",
                    confidence=0.9,
                    details=f"Same canonical track (id={canonical_track_id})",
                ))
                seen_ids.add(v.id)

    # 3. Fingerprint match (same audio content)
    if acoustid_id:
        videos = db.query(VideoItem).filter(
            VideoItem.acoustid_id == acoustid_id,
        ).all()
        for v in videos:
            if v.id not in seen_ids:
                matches.append(DuplicateMatch(
                    video_id=v.id,
                    match_type="fingerprint",
                    confidence=0.85,
                    details=f"Same audio fingerprint (acoustid={acoustid_id})",
                ))
                seen_ids.add(v.id)

    # 4. Name match (weakest — normalized artist + title)
    if artist and title:
        artist_key = make_comparison_key(artist)
        title_key = make_comparison_key(title)
        # Query by title first (indexed) then filter artist in Python
        candidates = db.query(VideoItem).filter(
            VideoItem.title.ilike(f"%{title[:50]}%"),
        ).all()
        for v in candidates:
            if v.id not in seen_ids:
                if (make_comparison_key(v.artist) == artist_key
                        and make_comparison_key(v.title) == title_key):
                    matches.append(DuplicateMatch(
                        video_id=v.id,
                        match_type="name_match",
                        confidence=0.6,
                        details=f"Name match: {v.artist} - {v.title}",
                    ))
                    seen_ids.add(v.id)

    # Sort by confidence descending
    matches.sort(key=lambda m: m.confidence, reverse=True)
    return matches


def is_true_duplicate(
    db: Session,
    *,
    provider: Optional[str] = None,
    provider_video_id: Optional[str] = None,
    canonical_track_id: Optional[int] = None,
    acoustid_id: Optional[str] = None,
) -> bool:
    """
    Quick check: is this an exact duplicate (same source)?

    Returns True only for exact source matches — not version duplicates.
    """
    if provider and provider_video_id:
        source = db.query(Source).filter(
            Source.provider == provider,
            Source.source_video_id == provider_video_id,
        ).first()
        if source:
            return True
    return False
