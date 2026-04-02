"""
Canonical Track Service — Link/create canonical tracks for videos.

Core logic for the three-level metadata model (Artist → Canonical Track → Video).

Matching strategy:
1. If video has a MusicBrainz recording ID, look up by mb_recording_id.
2. If video has an AcoustID/fingerprint, look up by acoustid_id.
3. Fall back to artist + title matching with normalization.
4. Covers ALWAYS create separate canonical tracks (different performing artist).

AI token protection:
- If canonical_track.ai_verified is True, skip AI metadata verification.
- AI description generation is ALWAYS per-video (never cached at canonical level).
"""
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.metadata.models import ArtistEntity, AlbumEntity, TrackEntity, track_genres
from app.models import VideoItem, Genre
from app.matching.normalization import make_comparison_key

logger = logging.getLogger(__name__)


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
