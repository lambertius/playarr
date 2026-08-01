"""
Playlist API — CRUD for playlists and their entries.
"""
import logging
from datetime import datetime, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Playlist, PlaylistEntry, VideoItem

router = APIRouter(prefix="/api/playlists", tags=["Playlists"])
logger = logging.getLogger(__name__)


# ── Schemas ───────────────────────────────────────────────

class PlaylistCreate(BaseModel):
    name: str
    description: Optional[str] = None

class PlaylistUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None

class PlaylistEntryOut(BaseModel):
    id: int
    occurrence_id: str
    video_id: int
    position: int
    artist: str
    title: str
    album: str | None = None
    year: int | None = None
    has_poster: bool
    duration_seconds: float | None = None

    model_config = {"from_attributes": True}

class PlaylistOut(BaseModel):
    id: int
    stable_id: str
    revision: int
    name: str
    description: Optional[str] = None
    entry_count: int = 0
    created_at: str
    updated_at: str
    entries: List[PlaylistEntryOut] = []

    model_config = {"from_attributes": True}

class PlaylistSummary(BaseModel):
    id: int
    stable_id: str
    revision: int
    name: str
    description: Optional[str] = None
    entry_count: int = 0
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}

class AddEntryRequest(BaseModel):
    video_id: int

class AddMultipleRequest(BaseModel):
    video_ids: List[int]

class ReorderRequest(BaseModel):
    entry_ids: List[int]

class PlaylistBatchEdit(BaseModel):
    expected_revision: int
    ordered_occurrence_ids: List[str]
    removed_occurrence_ids: List[str] = Field(default_factory=list)

class SortRequest(BaseModel):
    field: Literal["artist", "title", "album", "year"]
    direction: Literal["asc", "desc"] = "asc"

class PlaylistMembership(BaseModel):
    playlist_id: int
    entry_id: int


# ── Helpers ───────────────────────────────────────────────

def _playlist_or_404(db: Session, playlist_id: int) -> Playlist:
    p = db.query(Playlist).options(
        joinedload(Playlist.entries).joinedload(PlaylistEntry.video_item).joinedload(VideoItem.quality_signature),
        joinedload(Playlist.entries).joinedload(PlaylistEntry.video_item).joinedload(VideoItem.media_assets),
    ).filter(Playlist.id == playlist_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return p

def _entry_out(entry: PlaylistEntry) -> PlaylistEntryOut:
    vi = entry.video_item
    qs = vi.quality_signature if vi else None
    has_poster = any(a.asset_type == "poster" for a in vi.media_assets) if vi else False
    return PlaylistEntryOut(
        id=entry.id,
        occurrence_id=entry.occurrence_id,
        video_id=entry.video_id,
        position=entry.position,
        artist=vi.artist if vi else "Unknown",
        title=vi.title if vi else "Unknown",
        album=vi.album if vi else None,
        year=vi.year if vi else None,
        has_poster=has_poster,
        duration_seconds=qs.duration_seconds if qs else None,
    )

def _playlist_out(p: Playlist) -> PlaylistOut:
    return PlaylistOut(
        id=p.id,
        stable_id=p.stable_id,
        revision=p.revision,
        name=p.name,
        description=p.description,
        entry_count=len(p.entries),
        created_at=p.created_at.isoformat(),
        updated_at=p.updated_at.isoformat(),
        entries=[_entry_out(e) for e in sorted(p.entries, key=lambda x: x.position)],
    )

def _playlist_summary(p: Playlist, count: int) -> PlaylistSummary:
    return PlaylistSummary(
        id=p.id,
        stable_id=p.stable_id,
        revision=p.revision,
        name=p.name,
        description=p.description,
        entry_count=count,
        created_at=p.created_at.isoformat(),
        updated_at=p.updated_at.isoformat(),
    )


# ── Endpoints ─────────────────────────────────────────────

@router.get("/", response_model=List[PlaylistSummary])
def list_playlists(db: Session = Depends(get_db)):
    """List all playlists with entry counts."""
    rows = (
        db.query(Playlist, func.count(PlaylistEntry.id).label("cnt"))
        .outerjoin(PlaylistEntry)
        .group_by(Playlist.id)
        .order_by(Playlist.name)
        .all()
    )
    return [_playlist_summary(p, cnt) for p, cnt in rows]


@router.post("/", response_model=PlaylistOut, status_code=201)
def create_playlist(data: PlaylistCreate, db: Session = Depends(get_db)):
    p = Playlist(name=data.name, description=data.description)
    db.add(p)
    db.commit()
    db.refresh(p)
    return _playlist_out(p)


@router.get("/{playlist_id}", response_model=PlaylistOut)
def get_playlist(playlist_id: int, db: Session = Depends(get_db)):
    return _playlist_out(_playlist_or_404(db, playlist_id))


@router.put("/{playlist_id}", response_model=PlaylistOut)
def update_playlist(playlist_id: int, data: PlaylistUpdate, db: Session = Depends(get_db)):
    p = _playlist_or_404(db, playlist_id)
    if data.name is not None:
        p.name = data.name
    if data.description is not None:
        p.description = data.description
    p.revision += 1
    db.commit()
    db.refresh(p)
    return _playlist_out(p)


@router.delete("/{playlist_id}", status_code=204)
def delete_playlist(playlist_id: int, db: Session = Depends(get_db)):
    p = db.query(Playlist).get(playlist_id)
    if not p:
        raise HTTPException(status_code=404, detail="Playlist not found")
    db.delete(p)
    db.commit()


@router.post("/{playlist_id}/entries", response_model=PlaylistEntryOut, status_code=201)
def add_entry(playlist_id: int, data: AddEntryRequest, db: Session = Depends(get_db)):
    p = _playlist_or_404(db, playlist_id)
    # Verify video exists
    vi = db.query(VideoItem).get(data.video_id)
    if not vi:
        raise HTTPException(status_code=404, detail="Video not found")
    # De-duplicate: a track can only appear once per playlist. If it's already
    # present, return the existing entry rather than creating a duplicate.
    existing = next((e for e in p.entries if e.video_id == data.video_id), None)
    if existing:
        return _entry_out(existing)
    # Next position
    max_pos = max((e.position for e in p.entries), default=-1)
    entry = PlaylistEntry(playlist_id=playlist_id, video_id=data.video_id, position=max_pos + 1)
    db.add(entry)
    p.revision += 1
    p.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(entry)
    return _entry_out(entry)


@router.post("/{playlist_id}/entries/batch", response_model=List[PlaylistEntryOut], status_code=201)
def add_entries_batch(playlist_id: int, data: AddMultipleRequest, db: Session = Depends(get_db)):
    p = _playlist_or_404(db, playlist_id)
    max_pos = max((e.position for e in p.entries), default=-1)
    existing_ids = {e.video_id for e in p.entries}
    results = []
    next_pos = max_pos + 1
    for vid in data.video_ids:
        # Skip duplicates (already in playlist or repeated within this request).
        if vid in existing_ids:
            continue
        vi = db.query(VideoItem).get(vid)
        if not vi:
            continue
        entry = PlaylistEntry(playlist_id=playlist_id, video_id=vid, position=next_pos)
        db.add(entry)
        results.append(entry)
        existing_ids.add(vid)
        next_pos += 1
    if results:
        p.updated_at = datetime.now(timezone.utc)
        p.revision += 1
    db.commit()
    for e in results:
        db.refresh(e)
    return [_entry_out(e) for e in results]


@router.delete("/{playlist_id}/entries/{entry_id}", status_code=204)
def remove_entry(playlist_id: int, entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(PlaylistEntry).filter(
        PlaylistEntry.id == entry_id,
        PlaylistEntry.playlist_id == playlist_id,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    playlist = db.query(Playlist).get(playlist_id)
    if playlist:
        playlist.updated_at = datetime.now(timezone.utc)
        playlist.revision += 1
    db.delete(entry)
    db.commit()


@router.put("/{playlist_id}/reorder", response_model=PlaylistOut)
def reorder_entries(playlist_id: int, data: ReorderRequest, db: Session = Depends(get_db)):
    """Reorder entries by providing an ordered list of entry IDs.

    Entries missing from the payload (e.g. added by another client while this
    one held a stale list) are appended after the provided ones, preserving
    their previous relative order, so positions never collide.
    """
    p = _playlist_or_404(db, playlist_id)
    id_to_entry = {e.id: e for e in p.entries}
    pos = 0
    seen: set[int] = set()
    for eid in data.entry_ids:
        entry = id_to_entry.get(eid)
        if entry is None or eid in seen:
            continue  # unknown/duplicate ids are ignored
        entry.position = pos
        seen.add(eid)
        pos += 1
    leftover = sorted((e for e in p.entries if e.id not in seen), key=lambda e: e.position)
    for entry in leftover:
        entry.position = pos
        pos += 1
    p.updated_at = datetime.now(timezone.utc)
    p.revision += 1
    db.commit()
    return _playlist_out(_playlist_or_404(db, playlist_id))


@router.put("/{playlist_id}/entries:batch-edit", response_model=PlaylistOut)
def batch_edit_entries(
    playlist_id: int,
    data: PlaylistBatchEdit,
    db: Session = Depends(get_db),
):
    """Atomically apply a playlist draft with optimistic revision checking."""
    p = _playlist_or_404(db, playlist_id)
    if p.revision != data.expected_revision:
        raise HTTPException(status_code=409, detail={
            "code": "stale_revision",
            "message": "The playlist changed in another browser. Reload before saving.",
            "operation_id": None,
            "retryable": True,
            "field_errors": {"expected_revision": f"current revision is {p.revision}"},
            "diagnostics_id": None,
            "current": _playlist_out(p).model_dump(mode="json"),
        })

    entries = {entry.occurrence_id: entry for entry in p.entries}
    removed = set(data.removed_occurrence_ids)
    ordered = data.ordered_occurrence_ids
    if len(ordered) != len(set(ordered)):
        raise HTTPException(status_code=422, detail="ordered_occurrence_ids contains duplicates")
    if not removed.issubset(entries):
        raise HTTPException(status_code=422, detail="removed_occurrence_ids contains unknown entries")
    expected_remaining = set(entries) - removed
    if set(ordered) != expected_remaining:
        missing = sorted(expected_remaining - set(ordered))
        unknown = sorted(set(ordered) - expected_remaining)
        raise HTTPException(status_code=422, detail={
            "code": "incomplete_playlist_draft",
            "message": "The final order must contain every non-removed occurrence exactly once.",
            "missing": missing,
            "unknown": unknown,
        })

    for occurrence_id in removed:
        db.delete(entries[occurrence_id])
    for position, occurrence_id in enumerate(ordered):
        entries[occurrence_id].position = position
    p.revision += 1
    p.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.expire_all()
    return _playlist_out(_playlist_or_404(db, playlist_id))


@router.put("/{playlist_id}/sort", response_model=PlaylistOut)
def sort_entries(playlist_id: int, data: SortRequest, db: Session = Depends(get_db)):
    """Reorganise the playlist by a track field (artist/title/year), A–Z or Z–A.

    This persists the new order by rewriting each entry's position.
    """
    p = _playlist_or_404(db, playlist_id)
    reverse = data.direction == "desc"

    def key(entry: PlaylistEntry):
        vi = entry.video_item
        if data.field == "year":
            # Unknown years always sort to the end regardless of direction.
            y = vi.year if (vi and vi.year is not None) else None
            missing = y is None
            return (missing if not reverse else not missing, y if y is not None else 0)
        value = getattr(vi, data.field, "") if vi else ""
        return (value or "").casefold()

    ordered = sorted(p.entries, key=key, reverse=reverse)
    for pos, entry in enumerate(ordered):
        entry.position = pos
    p.updated_at = datetime.now(timezone.utc)
    p.revision += 1
    db.commit()
    return _playlist_out(_playlist_or_404(db, playlist_id))


@router.get("/for-video/{video_id}", response_model=List[PlaylistMembership])
def playlists_for_video(video_id: int, db: Session = Depends(get_db)):
    """Return which playlists already contain the given video (+ the entry id).

    Powers the add-to-playlist toggle: the picker uses this to show a track as
    already added and to remove it on click instead of adding a duplicate.
    """
    rows = db.query(PlaylistEntry).filter(PlaylistEntry.video_id == video_id).all()
    return [
        PlaylistMembership(playlist_id=r.playlist_id, entry_id=r.id) for r in rows
    ]
