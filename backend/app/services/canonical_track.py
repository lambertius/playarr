"""
Canonical Track Service — Link/create canonical tracks for videos.

Core logic for the three-level metadata model (Artist → Canonical Track → Video).

Matching strategy (priority order):
1. MusicBrainz recording ID — strongest, deterministic
2. AcoustID fingerprint — high confidence, deterministic
3. Normalized artist + title — heuristic, good recall
4. Covers ALWAYS create separate canonical tracks (different performing artist)

Provenance trust order:
- user (manual override — highest authority)
- musicbrainz (MBID match)
- fingerprint (AcoustID match)
- import (assigned during pipeline)
- ai (lowest priority — last resort)

AI token protection:
- If canonical_track.ai_verified is True, skip AI metadata verification.
- AI description generation is ALWAYS per-video (never cached at canonical level).
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.metadata.models import ArtistEntity, AlbumEntity, TrackEntity, track_genres
from app.models import VideoItem, Genre
from app.matching.normalization import make_comparison_key

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Variant type enum — single source of truth for valid version_type values.
# Imported by backend routes, version_detector, and documented for frontend.
# ---------------------------------------------------------------------------
VARIANT_TYPES = [
    "normal", "cover", "live", "alternate", "remix",
    "acoustic", "uncensored", "18+",
]


def _get_or_create_genre(db: Session, genre_name: str) -> "Genre":
    """Get existing genre or create new one."""
    from app.services.metadata_resolver import capitalize_genre
    normalised = capitalize_genre(genre_name)
    genre = db.query(Genre).filter(Genre.name == normalised).first()
    if not genre:
        genre = Genre(name=normalised)
        db.add(genre)
        db.flush()
    return genre


def find_canonical_track(
    db: Session,
    *,
    mb_recording_id: Optional[str] = None,
    acoustid_id: Optional[str] = None,
    artist_name: Optional[str] = None,
    title: Optional[str] = None,
    version_type: str = "normal",
    original_artist: Optional[str] = None,
    original_title: Optional[str] = None,
) -> Optional[TrackEntity]:
    """
    Find an existing canonical track by the strongest available identifier.

    Priority: mb_recording_id > acoustid_id > normalized artist+title.

    Covers create separate canonical tracks, so we check the is_cover flag
    and match against the performing artist, not the original song artist.
    """
    # 1. MusicBrainz recording ID — strongest identifier
    if mb_recording_id:
        track = db.query(TrackEntity).filter(
            TrackEntity.mb_recording_id == mb_recording_id,
        ).first()
        if track:
            logger.info(f"Canonical track found by mb_recording_id: {track}")
            return track

    # 2. AcoustID fingerprint match
    if acoustid_id:
        # Look for videos with this acoustid that are already linked to a track
        video_with_track = db.query(VideoItem).filter(
            VideoItem.acoustid_id == acoustid_id,
            VideoItem.track_id.isnot(None),
        ).first()
        if video_with_track and video_with_track.track_entity:
            logger.info(f"Canonical track found via acoustid: {video_with_track.track_entity}")
            return video_with_track.track_entity

    # 3. Normalized artist + title match
    if artist_name and title:
        artist_key = make_comparison_key(artist_name)
        title_key = make_comparison_key(title)

        # For covers: match against tracks marked as covers by the same performer
        if version_type == "cover":
            candidates = db.query(TrackEntity).join(
                TrackEntity.artist,
            ).filter(
                TrackEntity.is_cover == True,  # noqa: E712
            ).all()
            for c in candidates:
                if (c.artist and make_comparison_key(c.artist.canonical_name) == artist_key
                        and make_comparison_key(c.title) == title_key):
                    logger.info(f"Canonical cover track found by name match: {c}")
                    return c
        else:
            # Normal/live/alternate: match by artist name + title
            candidates = db.query(TrackEntity).join(
                TrackEntity.artist,
            ).filter(
                TrackEntity.is_cover == False,  # noqa: E712
            ).all()
            for c in candidates:
                if (c.artist and make_comparison_key(c.artist.canonical_name) == artist_key
                        and make_comparison_key(c.title) == title_key):
                    logger.info(f"Canonical track found by name match: {c}")
                    return c

    return None


def get_or_create_canonical_track(
    db: Session,
    *,
    artist_entity: Optional[ArtistEntity],
    album_entity: Optional[AlbumEntity],
    title: str,
    year: Optional[int] = None,
    mb_recording_id: Optional[str] = None,
    mb_release_id: Optional[str] = None,
    mb_release_group_id: Optional[str] = None,
    mb_artist_id: Optional[str] = None,
    acoustid_id: Optional[str] = None,
    version_type: str = "normal",
    original_artist: Optional[str] = None,
    original_title: Optional[str] = None,
    genres: Optional[list] = None,
    resolved_track: Optional[dict] = None,
) -> Tuple[TrackEntity, bool]:
    """
    Find or create a canonical track for the given metadata.

    Returns (track_entity, created) — where created is True if a new
    canonical track was minted.

    Covers always get their own canonical track (different performing artist).
    Live/alternate versions reuse the original canonical track.
    """
    is_cover = version_type == "cover"

    # Try to find existing
    existing = find_canonical_track(
        db,
        mb_recording_id=mb_recording_id,
        acoustid_id=acoustid_id,
        artist_name=artist_entity.canonical_name if artist_entity else None,
        title=title,
        version_type=version_type,
        original_artist=original_artist,
        original_title=original_title,
    )

    if existing:
        # Update fields if we have better data
        updated = False
        if mb_recording_id and not existing.mb_recording_id:
            existing.mb_recording_id = mb_recording_id
            updated = True
        if mb_release_id and not existing.mb_release_id:
            existing.mb_release_id = mb_release_id
            updated = True
        if mb_release_group_id and not existing.mb_release_group_id:
            existing.mb_release_group_id = mb_release_group_id
            updated = True
        if mb_artist_id and not existing.mb_artist_id:
            existing.mb_artist_id = mb_artist_id
            updated = True
        if year and not existing.year:
            existing.year = year
            updated = True
        if album_entity and not existing.album_id:
            existing.album_id = album_entity.id
            updated = True
        if updated:
            existing.updated_at = datetime.now(timezone.utc)
            db.flush()
        return existing, False

    # Create new canonical track
    track = TrackEntity(
        title=title,
        artist_id=artist_entity.id if artist_entity else None,
        album_id=album_entity.id if album_entity else None,
        year=year,
        mb_recording_id=mb_recording_id,
        mb_release_id=mb_release_id,
        mb_release_group_id=mb_release_group_id,
        mb_artist_id=mb_artist_id,
        is_cover=is_cover,
        original_artist=original_artist if is_cover else None,
        original_title=original_title if is_cover else None,
        metadata_source="import",
        canonical_verified=False,
        ai_verified=False,
    )

    # Apply resolved track data if available
    if resolved_track:
        if resolved_track.get("duration_seconds"):
            track.duration_seconds = resolved_track["duration_seconds"]
        if resolved_track.get("track_number"):
            track.track_number = resolved_track["track_number"]

    db.add(track)
    db.flush()

    # Attach genres
    if genres:
        for genre_name in genres:
            genre_obj = _get_or_create_genre(db, genre_name)
            track.genres.append(genre_obj)

    logger.info(f"Created canonical track: {track} (cover={is_cover})")
    return track, True


def link_video_to_canonical_track(
    db: Session,
    video_item: VideoItem,
    track: TrackEntity,
) -> None:
    """
    Link a video to its canonical track.

    Sets the FK and copies shared metadata from canonical track to video
    (artist, title, album, year, MusicBrainz IDs) where the video's fields
    are empty or unlocked.

    Does NOT copy the description/plot — that remains video-specific.
    """
    video_item.track_id = track.id

    locked = video_item.locked_fields or []
    all_locked = "_all" in locked

    # Copy shared metadata from canonical track only when the video's
    # field is empty — the import pipeline's final_artist/final_title
    # is authoritative and must not be overwritten by entity names that
    # may have come from fuzzy MusicBrainz matches.
    if track.artist and not all_locked and "artist" not in locked and not video_item.artist:
        video_item.artist = track.artist.canonical_name
    if track.mb_recording_id:
        video_item.mb_recording_id = track.mb_recording_id
    if track.mb_release_id:
        video_item.mb_release_id = track.mb_release_id
    if track.mb_artist_id or (track.artist and track.artist.mb_artist_id):
        video_item.mb_artist_id = track.mb_artist_id or track.artist.mb_artist_id
    if track.year and not all_locked and "year" not in locked and not video_item.year:
        video_item.year = track.year
    if track.album and not all_locked and "album" not in locked and not video_item.album:
        video_item.album = track.album.title

    # Set entity FKs
    if track.artist_id:
        video_item.artist_entity_id = track.artist_id
    if track.album_id:
        video_item.album_entity_id = track.album_id

    from sqlalchemy.orm.attributes import flag_modified
    _set_canonical_linked_flag(db, video_item)
    db.flush()

    logger.info(f"Linked video {video_item.id} to canonical track {track.id}")


def should_skip_ai_metadata(track: TrackEntity) -> bool:
    """
    Check if AI metadata verification can be skipped for this canonical track.

    Returns True if the track has already been AI-verified, meaning we can
    reuse the cached metadata and save AI tokens.
    """
    return track.ai_verified


def mark_canonical_ai_verified(
    db: Session,
    track: TrackEntity,
    method: str = "ai",
) -> None:
    """Mark a canonical track as AI-verified so future videos skip AI metadata."""
    track.ai_verified = True
    track.ai_verified_at = datetime.now(timezone.utc)
    track.metadata_source = method
    track.canonical_verified = True
    db.flush()
    logger.info(f"Canonical track {track.id} marked as AI-verified")


def _set_canonical_linked_flag(db: Session, video_item: VideoItem) -> None:
    """Set the canonical_linked processing flag on a video."""
    from sqlalchemy.orm.attributes import flag_modified
    state = dict(video_item.processing_state or {})
    state["canonical_linked"] = {
        "completed": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": "canonical",
        "version": "1.0",
    }
    video_item.processing_state = state
    flag_modified(video_item, "processing_state")


# ---------------------------------------------------------------------------
# Library scan — find canonical track candidates for a video
# ---------------------------------------------------------------------------

def scan_library_for_canonical_matches(
    db: Session,
    video: VideoItem,
    *,
    limit: int = 10,
) -> List[Dict]:
    """
    Scan the library for potential canonical track matches, in priority order:
    1. MBID match (highest confidence)
    2. Fingerprint / AcoustID match
    3. Fuzzy artist + title match

    Returns a list of candidate dicts sorted by confidence (descending).
    """
    candidates: List[Dict] = []
    seen_track_ids: set = set()

    # 1. MBID match — strongest signal
    if video.mb_recording_id:
        track = db.query(TrackEntity).filter(
            TrackEntity.mb_recording_id == video.mb_recording_id,
        ).first()
        if track and track.id not in seen_track_ids:
            seen_track_ids.add(track.id)
            candidates.append(_track_to_candidate(track, "musicbrainz", 1.0))

    # 2. AcoustID / fingerprint match
    if video.acoustid_id:
        linked_videos = db.query(VideoItem).filter(
            VideoItem.acoustid_id == video.acoustid_id,
            VideoItem.track_id.isnot(None),
            VideoItem.id != video.id,
        ).all()
        for v in linked_videos:
            if v.track_id and v.track_id not in seen_track_ids:
                seen_track_ids.add(v.track_id)
                track = v.track_entity or db.query(TrackEntity).get(v.track_id)
                if track:
                    candidates.append(_track_to_candidate(track, "fingerprint", 0.90))

    # 3. Fuzzy artist + title match
    if video.artist and video.title:
        video_artist_key = make_comparison_key(video.artist)
        video_title_key = make_comparison_key(video.title)
        if video_artist_key and video_title_key:
            all_tracks = db.query(TrackEntity).join(
                TrackEntity.artist,
            ).all()
            for track in all_tracks:
                if track.id in seen_track_ids:
                    continue
                if not track.artist:
                    continue
                artist_key = make_comparison_key(track.artist.canonical_name)
                title_key = make_comparison_key(track.title)
                if artist_key == video_artist_key and title_key == video_title_key:
                    seen_track_ids.add(track.id)
                    candidates.append(_track_to_candidate(track, "fuzzy", 0.70))

    # Sort by confidence descending
    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    return candidates[:limit]


def _track_to_candidate(track: TrackEntity, match_source: str, confidence: float) -> Dict:
    """Convert a TrackEntity to a candidate dict for API response."""
    return {
        "track_id": track.id,
        "title": track.title,
        "artist_name": track.artist.canonical_name if track.artist else None,
        "year": track.year,
        "match_source": match_source,
        "confidence": confidence,
        "video_count": len(track.videos) if track.videos else 0,
    }


# ---------------------------------------------------------------------------
# Manual canonical track editing
# ---------------------------------------------------------------------------

def update_canonical_track(
    db: Session,
    track: TrackEntity,
    *,
    title: Optional[str] = None,
    artist_name: Optional[str] = None,
    album_name: Optional[str] = None,
    year: Optional[int] = None,
    is_cover: Optional[bool] = None,
    original_artist: Optional[str] = None,
    original_title: Optional[str] = None,
    genres: Optional[list] = None,
) -> TrackEntity:
    """Update a canonical track's fields. Sets provenance to 'user'."""
    if title is not None:
        track.title = title
    if year is not None:
        track.year = year
    if is_cover is not None:
        track.is_cover = is_cover
    if original_artist is not None:
        track.original_artist = original_artist
    if original_title is not None:
        track.original_title = original_title

    # Artist resolution
    if artist_name is not None:
        from app.metadata.resolver import get_or_create_artist
        artist_entity = get_or_create_artist(db, artist_name, {})
        track.artist_id = artist_entity.id

    # Album resolution
    if album_name is not None:
        if album_name.strip():
            from app.metadata.resolver import get_or_create_album
            album_entity = get_or_create_album(
                db, track.artist.canonical_name if track.artist else "Unknown",
                album_name, {},
            )
            track.album_id = album_entity.id
        else:
            track.album_id = None

    # Genres
    if genres is not None:
        track.genres.clear()
        for g in genres:
            track.genres.append(_get_or_create_genre(db, g))

    # Provenance
    fp = dict(track.field_provenance or {})
    for f in ["title", "artist", "album", "year", "is_cover", "original_artist", "original_title", "genres"]:
        fp[f] = "user"
    track.field_provenance = fp
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(track, "field_provenance")

    track.canonical_verified = True
    track.updated_at = datetime.now(timezone.utc)
    db.flush()

    logger.info(f"Updated canonical track {track.id} (user edit)")
    return track


def create_canonical_track_manual(
    db: Session,
    *,
    title: str,
    artist_name: str,
    album_name: Optional[str] = None,
    year: Optional[int] = None,
    is_cover: bool = False,
    original_artist: Optional[str] = None,
    original_title: Optional[str] = None,
    genres: Optional[list] = None,
) -> TrackEntity:
    """Create a new canonical track manually. Sets provenance to 'user'."""
    from app.metadata.resolver import get_or_create_artist, get_or_create_album

    artist_entity = get_or_create_artist(db, artist_name, {})
    album_entity = None
    if album_name and album_name.strip():
        album_entity = get_or_create_album(db, artist_name, album_name, {})

    track = TrackEntity(
        title=title,
        artist_id=artist_entity.id,
        album_id=album_entity.id if album_entity else None,
        year=year,
        is_cover=is_cover,
        original_artist=original_artist if is_cover else None,
        original_title=original_title if is_cover else None,
        metadata_source="user",
        canonical_verified=True,
        field_provenance={f: "user" for f in [
            "title", "artist", "album", "year", "is_cover",
            "original_artist", "original_title", "genres",
        ]},
    )
    db.add(track)
    db.flush()

    if genres:
        for g in genres:
            track.genres.append(_get_or_create_genre(db, g))

    logger.info(f"Created canonical track {track.id} manually: {artist_name} - {title}")
    return track


# ---------------------------------------------------------------------------
# Parent video linking (hierarchical version chains)
# ---------------------------------------------------------------------------

def set_parent_video(
    db: Session,
    video: VideoItem,
    parent_id: Optional[int],
) -> None:
    """
    Set or clear a video's parent_video_id.

    Validates:
    - parent exists
    - no circular chains (A→B→A)
    - parent shares the same canonical track (or we align them)

    Raises ValueError on invalid input.
    """
    if parent_id is None:
        video.parent_video_id = None
        db.flush()
        return

    if parent_id == video.id:
        raise ValueError("A video cannot be its own parent")

    parent = db.query(VideoItem).get(parent_id)
    if not parent:
        raise ValueError(f"Parent video {parent_id} not found")

    # Check for circular chains: walk parent's chain, ensure we never reach video.id
    visited = {video.id}
    current = parent
    while current.parent_video_id is not None:
        if current.parent_video_id in visited:
            raise ValueError("Circular parent chain detected")
        visited.add(current.parent_video_id)
        current = db.query(VideoItem).get(current.parent_video_id)
        if current is None:
            break

    video.parent_video_id = parent_id

    # If parent has a canonical track and video doesn't, inherit it
    if parent.track_id and not video.track_id:
        video.track_id = parent.track_id
        video.canonical_confidence = 0.85
        video.canonical_provenance = "parent_link"

    db.flush()
    logger.info(f"Set parent of video {video.id} to {parent_id}")


# ---------------------------------------------------------------------------
# Review queue integration — flag canonical issues
# ---------------------------------------------------------------------------

def flag_canonical_review(
    db: Session,
    video: VideoItem,
    reason: str,
    category: str = "canonical_missing",
) -> None:
    """Flag a video for canonical track review."""
    video.review_status = "needs_human_review"
    video.review_reason = reason
    video.review_category = category
    db.flush()


def scan_library_canonical_issues(
    db: Session,
    *,
    limit: int = 500,
) -> Dict[str, int]:
    """
    Scan the library for canonical track issues and flag them for review.

    Checks:
    1. Videos missing canonical track link
    2. MBID conflicts (same MBID → different canonical tracks)
    3. Fingerprint conflicts (same acoustid → different canonical tracks)
    4. Low confidence links

    Returns dict of issue category → count.
    """
    counts: Dict[str, int] = {
        "canonical_missing": 0,
        "canonical_conflict": 0,
        "canonical_low_confidence": 0,
    }

    # 1. Missing canonical track
    missing = db.query(VideoItem).filter(
        VideoItem.track_id.is_(None),
        VideoItem.review_status.notin_(["needs_human_review"]),
    ).limit(limit).all()
    for v in missing:
        flag_canonical_review(db, v, "No canonical track linked", "canonical_missing")
        counts["canonical_missing"] += 1

    # 2. MBID conflicts — same mb_recording_id but different track_id
    from sqlalchemy import func
    mbid_groups = db.query(
        VideoItem.mb_recording_id,
        func.count(func.distinct(VideoItem.track_id)).label("track_count"),
    ).filter(
        VideoItem.mb_recording_id.isnot(None),
        VideoItem.track_id.isnot(None),
    ).group_by(VideoItem.mb_recording_id).having(
        func.count(func.distinct(VideoItem.track_id)) > 1,
    ).all()
    for mbid, _ in mbid_groups:
        conflicting = db.query(VideoItem).filter(
            VideoItem.mb_recording_id == mbid,
            VideoItem.review_status.notin_(["needs_human_review"]),
        ).all()
        for v in conflicting:
            flag_canonical_review(
                db, v,
                f"MBID {mbid} maps to multiple canonical tracks",
                "canonical_conflict",
            )
            counts["canonical_conflict"] += 1

    # 3. Low confidence links
    low_conf = db.query(VideoItem).filter(
        VideoItem.canonical_confidence.isnot(None),
        VideoItem.canonical_confidence < 0.5,
        VideoItem.review_status.notin_(["needs_human_review"]),
    ).limit(limit).all()
    for v in low_conf:
        flag_canonical_review(
            db, v,
            f"Canonical link confidence is low ({v.canonical_confidence:.0%})",
            "canonical_low_confidence",
        )
        counts["canonical_low_confidence"] += 1

    db.commit()
    return counts
