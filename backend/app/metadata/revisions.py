"""
Metadata Revision Manager — Snapshot and rollback for any entity.

Supports:
- Automatic snapshots on import / refresh / manual edit
- Field-level "lock" flags to protect manual edits from overwrite
- Deterministic undo: restore the previous snapshot and re-export

Every mutation that changes entity metadata should call
``save_revision()`` first.  ``rollback()`` restores the most recent
previous state and returns the entity for the caller to commit.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.metadata.models import (
    MetadataRevision,
    ArtistEntity,
    AlbumEntity,
    TrackEntity,
)

logger = logging.getLogger(__name__)

# Maps entity_type string → ORM class
_ENTITY_CLASSES = {
    "artist": ArtistEntity,
    "album": AlbumEntity,
    "track": TrackEntity,
}

# Fields to snapshot per entity type
_SNAPSHOT_FIELDS: Dict[str, List[str]] = {
    "artist": [
        "canonical_name", "sort_name", "mb_artist_id", "country",
        "disambiguation", "biography", "aliases", "confidence", "needs_review",
    ],
    "album": [
        "title", "year", "release_date", "mb_release_id", "album_type",
        "confidence", "needs_review",
    ],
    "track": [
        "title", "mb_recording_id", "track_number", "duration_seconds",
        "confidence", "needs_review",
    ],
    # VideoItem snapshots are handled by the existing MetadataSnapshot model
    "video": [
        "artist", "title", "album", "year", "plot",
        "mb_artist_id", "mb_recording_id", "mb_release_id",
    ],
}


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def save_revision(
    db: Session,
    entity_type: str,
    entity_id: int,
    reason: str = "auto_import",
    provider: Optional[str] = None,
    entity: Optional[Any] = None,
) -> MetadataRevision:
    """
    Save a snapshot of current entity metadata.

    If *entity* is not provided, it will be fetched from DB.
    """
    if entity is None:
        cls = _ENTITY_CLASSES.get(entity_type)
        if cls:
            entity = db.query(cls).get(entity_id)
        if not entity:
            raise ValueError(f"Entity not found: {entity_type}#{entity_id}")

    fields_to_capture = _SNAPSHOT_FIELDS.get(entity_type, [])
    snapshot: Dict[str, Any] = {}
    for f in fields_to_capture:
        snapshot[f] = getattr(entity, f, None)

    # Capture genres if applicable
    if hasattr(entity, "genres"):
        snapshot["genres"] = [g.name for g in entity.genres]

    rev = MetadataRevision(
        entity_type=entity_type,
        entity_id=entity_id,
        fields=snapshot,
        provider=provider,
        reason=reason,
    )
    db.add(rev)
    db.flush()

    logger.info(f"Saved revision {rev.id} for {entity_type}#{entity_id} (reason={reason})")
    return rev


# ---------------------------------------------------------------------------
# List revisions
# ---------------------------------------------------------------------------

def list_revisions(
    db: Session,
    entity_type: str,
    entity_id: int,
    limit: int = 20,
) -> List[MetadataRevision]:
    """Return recent revisions for an entity, newest first."""
    return (
        db.query(MetadataRevision)
        .filter(
            MetadataRevision.entity_type == entity_type,
            MetadataRevision.entity_id == entity_id,
        )
        .order_by(MetadataRevision.created_at.desc())
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

def rollback(
    db: Session,
    entity_type: str,
    entity_id: int,
    revision_id: Optional[int] = None,
) -> Optional[Any]:
    """
    Rollback an entity to a previous revision.

    If *revision_id* is None, rolls back to the second-most-recent
    revision (i.e. undo the last change).

    Returns the updated entity, or None if rollback was not possible.
    """
    cls = _ENTITY_CLASSES.get(entity_type)
    if not cls:
        logger.error(f"Unknown entity type for rollback: {entity_type}")
        return None

    entity = db.query(cls).get(entity_id)
    if not entity:
        logger.error(f"Entity not found: {entity_type}#{entity_id}")
        return None

    if revision_id:
        rev = db.query(MetadataRevision).get(revision_id)
        if not rev or rev.entity_type != entity_type or rev.entity_id != entity_id:
            logger.error(f"Revision {revision_id} not valid for {entity_type}#{entity_id}")
            return None
    else:
        # Get second-most-recent revision
        revs = (
            db.query(MetadataRevision)
            .filter(
                MetadataRevision.entity_type == entity_type,
                MetadataRevision.entity_id == entity_id,
            )
            .order_by(MetadataRevision.created_at.desc())
            .limit(2)
            .all()
        )
        if len(revs) < 2:
            logger.warning(f"No previous revision to rollback for {entity_type}#{entity_id}")
            return None
        rev = revs[1]

    # Save current state as a new revision (so the rollback itself is undo-able)
    save_revision(db, entity_type, entity_id, reason="pre_rollback", entity=entity)

    # Restore fields
    fields = rev.fields or {}
    for key, value in fields.items():
        if key == "genres":
            continue  # handled separately
        if hasattr(entity, key):
            setattr(entity, key, value)

    # Restore genres
    if "genres" in fields and hasattr(entity, "genres"):
        from app.tasks import _get_or_create_genre
        entity.genres.clear()
        for g_name in fields["genres"]:
            entity.genres.append(_get_or_create_genre(db, g_name))

    logger.info(f"Rolled back {entity_type}#{entity_id} to revision {rev.id}")
    return entity
