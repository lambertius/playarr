"""
Metadata API — Manage canonical entities, refresh metadata, export, undo.

Endpoints:
    GET  /api/metadata/artists              — list artist entities
    GET  /api/metadata/artists/{id}         — get artist detail
    GET  /api/metadata/albums               — list album entities
    GET  /api/metadata/albums/{id}          — get album detail
    GET  /api/metadata/tracks               — list track entities
    POST /api/metadata/refresh/{video_id}   — force refresh for one video
    POST /api/metadata/refresh-all          — force refresh entire library
    POST /api/metadata/refresh-missing      — refresh only low-confidence / missing
    POST /api/metadata/export               — full Kodi re-export
    POST /api/metadata/export/{video_id}    — export single video + its artist/album
    POST /api/metadata/undo/{entity_type}/{entity_id} — undo last refresh
    GET  /api/metadata/revisions/{entity_type}/{entity_id} — list revisions
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import VideoItem, ProcessingJob, JobStatus
from app.metadata.models import (
    ArtistEntity, AlbumEntity, TrackEntity, MetadataRevision, ExportManifest,
)
from app.metadata.revisions import list_revisions, rollback, save_revision
from app.metadata.exporters.kodi import export_all, export_artist, export_album, export_video

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/metadata", tags=["Metadata"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ArtistOut(BaseModel):
    id: int
    canonical_name: str
    sort_name: Optional[str] = None
    mb_artist_id: Optional[str] = None
    country: Optional[str] = None
    origin: Optional[str] = None
    disambiguation: Optional[str] = None
    biography: Optional[str] = None
    artist_image: Optional[str] = None
    confidence: float = 1.0
    needs_review: bool = False
    genres: List[str] = []

    class Config:
        from_attributes = True


class AlbumOut(BaseModel):
    id: int
    title: str
    artist_id: Optional[int] = None
    artist_name: Optional[str] = None
    year: Optional[int] = None
    release_date: Optional[str] = None
    mb_release_id: Optional[str] = None
    album_type: Optional[str] = None
    confidence: float = 1.0
    needs_review: bool = False
    genres: List[str] = []

    class Config:
        from_attributes = True


class TrackOut(BaseModel):
    id: int
    title: str
    artist_id: Optional[int] = None
    artist_name: Optional[str] = None
    album_id: Optional[int] = None
    album_name: Optional[str] = None
    year: Optional[int] = None
    mb_recording_id: Optional[str] = None
    mb_release_id: Optional[str] = None
    mb_artist_id: Optional[str] = None
    track_number: Optional[int] = None
    duration_seconds: Optional[float] = None
    artwork_album: Optional[str] = None
    artwork_single: Optional[str] = None
    canonical_verified: bool = False
    metadata_source: Optional[str] = None
    ai_verified: bool = False
    ai_verified_at: Optional[str] = None
    is_cover: bool = False
    original_artist: Optional[str] = None
    original_title: Optional[str] = None
    confidence: float = 1.0
    needs_review: bool = False
    video_count: int = 0
    genres: List[str] = []

    class Config:
        from_attributes = True


class RevisionOut(BaseModel):
    id: int
    entity_type: str
    entity_id: int
    fields: dict
    provider: Optional[str] = None
    reason: str
    created_at: str

    class Config:
        from_attributes = True


class ExportResult(BaseModel):
    artists: int = 0
    albums: int = 0
    videos: int = 0
    message: str = ""


class RefreshResult(BaseModel):
    job_id: Optional[int] = None
    message: str = ""


# ---------------------------------------------------------------------------
# Artist endpoints
# ---------------------------------------------------------------------------

@router.get("/artists", response_model=List[ArtistOut])
def list_artists(
    limit: int = Query(200, ge=1, le=1000),
    needs_review: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    q = db.query(ArtistEntity)
    if needs_review is not None:
        q = q.filter(ArtistEntity.needs_review == needs_review)
    q = q.order_by(ArtistEntity.canonical_name).limit(limit)
    results = []
    for a in q.all():
        results.append(ArtistOut(
            id=a.id, canonical_name=a.canonical_name,
            sort_name=a.sort_name, mb_artist_id=a.mb_artist_id,
            country=a.country, disambiguation=a.disambiguation,
            biography=a.biography, confidence=a.confidence,
            needs_review=a.needs_review,
            genres=[g.name for g in a.genres],
        ))
    return results


@router.get("/artists/{artist_id}", response_model=ArtistOut)
def get_artist(artist_id: int, db: Session = Depends(get_db)):
    a = db.query(ArtistEntity).get(artist_id)
    if not a:
        raise HTTPException(404, "Artist not found")
    return ArtistOut(
        id=a.id, canonical_name=a.canonical_name,
        sort_name=a.sort_name, mb_artist_id=a.mb_artist_id,
        country=a.country, disambiguation=a.disambiguation,
        biography=a.biography, confidence=a.confidence,
        needs_review=a.needs_review,
        genres=[g.name for g in a.genres],
    )


# ---------------------------------------------------------------------------
# Album endpoints
# ---------------------------------------------------------------------------

@router.get("/albums", response_model=List[AlbumOut])
def list_albums(
    limit: int = Query(200, ge=1, le=1000),
    needs_review: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    q = db.query(AlbumEntity)
    if needs_review is not None:
        q = q.filter(AlbumEntity.needs_review == needs_review)
    q = q.order_by(AlbumEntity.title).limit(limit)
    results = []
    for al in q.all():
        artist_name = al.artist.canonical_name if al.artist else None
        results.append(AlbumOut(
            id=al.id, title=al.title, artist_id=al.artist_id,
            artist_name=artist_name, year=al.year,
            release_date=al.release_date, mb_release_id=al.mb_release_id,
            album_type=al.album_type, confidence=al.confidence,
            needs_review=al.needs_review,
            genres=[g.name for g in al.genres],
        ))
    return results


@router.get("/albums/{album_id}", response_model=AlbumOut)
def get_album(album_id: int, db: Session = Depends(get_db)):
    al = db.query(AlbumEntity).get(album_id)
    if not al:
        raise HTTPException(404, "Album not found")
    artist_name = al.artist.canonical_name if al.artist else None
    return AlbumOut(
        id=al.id, title=al.title, artist_id=al.artist_id,
        artist_name=artist_name, year=al.year,
        release_date=al.release_date, mb_release_id=al.mb_release_id,
        album_type=al.album_type, confidence=al.confidence,
        needs_review=al.needs_review,
        genres=[g.name for g in al.genres],
    )


# ---------------------------------------------------------------------------
# Track endpoints
# ---------------------------------------------------------------------------

@router.get("/tracks", response_model=List[TrackOut])
def list_tracks(
    limit: int = Query(200, ge=1, le=1000),
    ai_verified: Optional[bool] = None,
    is_cover: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    """List canonical tracks with filtering."""
    q = db.query(TrackEntity)
    if ai_verified is not None:
        q = q.filter(TrackEntity.ai_verified == ai_verified)
    if is_cover is not None:
        q = q.filter(TrackEntity.is_cover == is_cover)
    q = q.order_by(TrackEntity.title).limit(limit)
    results = []
    for t in q.all():
        results.append(TrackOut(
            id=t.id, title=t.title,
            artist_id=t.artist_id,
            artist_name=t.artist.canonical_name if t.artist else None,
            album_id=t.album_id,
            album_name=t.album.title if t.album else None,
            year=t.year,
            mb_recording_id=t.mb_recording_id,
            mb_release_id=t.mb_release_id,
            mb_artist_id=t.mb_artist_id,
            track_number=t.track_number,
            duration_seconds=t.duration_seconds,
            artwork_album=t.artwork_album,
            artwork_single=t.artwork_single,
            canonical_verified=t.canonical_verified,
            metadata_source=t.metadata_source,
            ai_verified=t.ai_verified,
            ai_verified_at=t.ai_verified_at.isoformat() if t.ai_verified_at else None,
            is_cover=t.is_cover,
            original_artist=t.original_artist,
            original_title=t.original_title,
            confidence=t.confidence,
            needs_review=t.needs_review,
            video_count=len(t.videos) if t.videos else 0,
            genres=[g.name for g in t.genres],
        ))
    return results


@router.get("/tracks/{track_id}", response_model=TrackOut)
def get_track(track_id: int, db: Session = Depends(get_db)):
    """Get canonical track detail with linked video count."""
    t = db.query(TrackEntity).get(track_id)
    if not t:
        raise HTTPException(404, "Track not found")
    return TrackOut(
        id=t.id, title=t.title,
        artist_id=t.artist_id,
        artist_name=t.artist.canonical_name if t.artist else None,
        album_id=t.album_id,
        album_name=t.album.title if t.album else None,
        year=t.year,
        mb_recording_id=t.mb_recording_id,
        mb_release_id=t.mb_release_id,
        mb_artist_id=t.mb_artist_id,
        track_number=t.track_number,
        duration_seconds=t.duration_seconds,
        artwork_album=t.artwork_album,
        artwork_single=t.artwork_single,
        canonical_verified=t.canonical_verified,
        metadata_source=t.metadata_source,
        ai_verified=t.ai_verified,
        ai_verified_at=t.ai_verified_at.isoformat() if t.ai_verified_at else None,
        is_cover=t.is_cover,
        original_artist=t.original_artist,
        original_title=t.original_title,
        confidence=t.confidence,
        needs_review=t.needs_review,
        video_count=len(t.videos) if t.videos else 0,
        genres=[g.name for g in t.genres],
    )


@router.get("/tracks/{track_id}/videos", response_model=List[dict])
def get_track_videos(track_id: int, db: Session = Depends(get_db)):
    """List all videos linked to a canonical track."""
    t = db.query(TrackEntity).get(track_id)
    if not t:
        raise HTTPException(404, "Track not found")
    return [
        {
            "id": v.id,
            "artist": v.artist,
            "title": v.title,
            "version_type": v.version_type,
            "alternate_version_label": v.alternate_version_label,
            "resolution_label": v.resolution_label,
            "file_path": v.file_path,
        }
        for v in t.videos
    ]


# ---------------------------------------------------------------------------
# Refresh metadata
# ---------------------------------------------------------------------------

@router.post("/refresh/{video_id}", response_model=RefreshResult)
def refresh_single(video_id: int, db: Session = Depends(get_db)):
    """Force-refresh metadata for a single video (resolves entities + re-exports)."""
    from app.tasks import metadata_refresh_task
    from app.worker import dispatch_task

    item = db.query(VideoItem).get(video_id)
    if not item:
        raise HTTPException(404, "Video not found")

    job = ProcessingJob(
        job_type="metadata_refresh",
        status=JobStatus.queued,
        video_id=video_id,
        display_name=f"{item.artist} \u2013 {item.title} \u203a Metadata Refresh",
        action_label="Metadata Refresh",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    dispatch_task(metadata_refresh_task, job_id=job.id, video_id=video_id, force=True)
    return RefreshResult(job_id=job.id, message="Metadata refresh queued")


@router.post("/refresh-all", response_model=RefreshResult)
def refresh_all(db: Session = Depends(get_db)):
    """Force-refresh metadata for all videos."""
    from app.tasks import metadata_refresh_task, complete_batch_job_task
    from app.worker import dispatch_task

    ids = [v.id for v in db.query(VideoItem.id).all()]
    parent = ProcessingJob(
        job_type="batch_metadata_refresh",
        status=JobStatus.analyzing,
        display_name=f"Refresh all ({len(ids)} videos)",
        action_label="Batch Metadata Refresh",
    )
    db.add(parent)
    db.commit()
    db.refresh(parent)

    # Pre-fetch display names for children
    _refresh_vids = db.query(VideoItem).filter(VideoItem.id.in_(ids)).all()
    _refresh_names = {v.id: f"{v.artist} \u2013 {v.title}" for v in _refresh_vids if v.artist and v.title}
    sub_ids = []
    for vid in ids:
        _rn = _refresh_names.get(vid)
        child = ProcessingJob(
            job_type="metadata_refresh", status=JobStatus.queued, video_id=vid,
            action_label="Metadata Refresh",
            display_name=f"{_rn} \u203a Metadata Refresh" if _rn else None,
        )
        db.add(child)
        db.flush()
        sub_ids.append(child.id)
        dispatch_task(metadata_refresh_task, job_id=child.id, video_id=vid, force=True)

    db.commit()
    dispatch_task(complete_batch_job_task, parent_job_id=parent.id, sub_job_ids=sub_ids)
    return RefreshResult(job_id=parent.id, message=f"Queued refresh for {len(ids)} videos")


@router.post("/refresh-missing", response_model=RefreshResult)
def refresh_missing(db: Session = Depends(get_db)):
    """Refresh metadata only for videos lacking entity links or with low confidence."""
    from app.tasks import metadata_refresh_task, complete_batch_job_task
    from app.worker import dispatch_task

    # Videos without entity links
    vids = db.query(VideoItem).filter(
        (VideoItem.artist_entity_id.is_(None)) | (VideoItem.track_id.is_(None))
    ).all()

    parent = ProcessingJob(
        job_type="batch_metadata_refresh",
        status=JobStatus.analyzing,
        display_name=f"Refresh missing ({len(vids)} videos)",
        action_label="Batch Metadata Refresh",
    )
    db.add(parent)
    db.commit()
    db.refresh(parent)

    sub_ids = []
    for v in vids:
        child = ProcessingJob(
            job_type="metadata_refresh", status=JobStatus.queued, video_id=v.id,
            action_label="Metadata Refresh",
            display_name=f"{v.artist} \u2013 {v.title} \u203a Metadata Refresh" if v.artist and v.title else None,
        )
        db.add(child)
        db.flush()
        sub_ids.append(child.id)
        dispatch_task(metadata_refresh_task, job_id=child.id, video_id=v.id, force=False)

    db.commit()
    dispatch_task(complete_batch_job_task, parent_job_id=parent.id, sub_job_ids=sub_ids)
    return RefreshResult(job_id=parent.id, message=f"Queued refresh for {len(vids)} videos")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@router.post("/export", response_model=ExportResult)
def full_export(db: Session = Depends(get_db)):
    """Full re-export of all Kodi NFO + artwork."""
    from app.tasks import kodi_export_task
    from app.worker import dispatch_task

    job = ProcessingJob(
        job_type="kodi_export",
        status=JobStatus.queued,
        display_name="Full Kodi export",
        action_label="Kodi Export",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    dispatch_task(kodi_export_task, job_id=job.id)
    return ExportResult(message=f"Export queued (job {job.id})")


@router.post("/export/{video_id}", response_model=ExportResult)
def export_single(video_id: int, db: Session = Depends(get_db)):
    """Export Kodi outputs for a single video and its linked artist/album."""
    item = db.query(VideoItem).get(video_id)
    if not item:
        raise HTTPException(404, "Video not found")

    files_written = 0

    # Export video
    source_url = item.sources[0].canonical_url if item.sources else ""
    genres = [g.name for g in item.genres]
    written = export_video(
        db, item.id, artist=item.artist, title=item.title,
        album=item.album or "", year=item.year,
        genres=genres, plot=item.plot or "",
        source_url=source_url, folder_path=item.folder_path,
        resolution_label=item.resolution_label or "",
    )
    files_written += len(written)

    # Export linked artist
    if item.artist_entity_id:
        artist_ent = db.query(ArtistEntity).get(item.artist_entity_id)
        if artist_ent:
            written = export_artist(db, artist_ent)
            files_written += len(written)

    # Export linked album
    if item.album_entity_id:
        album_ent = db.query(AlbumEntity).get(item.album_entity_id)
        if album_ent:
            written = export_album(db, album_ent)
            files_written += len(written)

    db.commit()
    return ExportResult(
        videos=1,
        artists=1 if item.artist_entity_id else 0,
        albums=1 if item.album_entity_id else 0,
        message=f"Exported {files_written} files",
    )


# ---------------------------------------------------------------------------
# Revisions & Undo
# ---------------------------------------------------------------------------

@router.get("/revisions/{entity_type}/{entity_id}", response_model=List[RevisionOut])
def get_revisions(entity_type: str, entity_id: int, db: Session = Depends(get_db)):
    revs = list_revisions(db, entity_type, entity_id)
    return [RevisionOut(
        id=r.id, entity_type=r.entity_type, entity_id=r.entity_id,
        fields=r.fields, provider=r.provider, reason=r.reason,
        created_at=r.created_at.isoformat(),
    ) for r in revs]


@router.post("/undo/{entity_type}/{entity_id}")
def undo_refresh(entity_type: str, entity_id: int, db: Session = Depends(get_db)):
    """Undo the last metadata refresh, restoring previous state + re-exporting."""
    entity = rollback(db, entity_type, entity_id)
    if not entity:
        raise HTTPException(400, "No previous revision to restore")

    # Re-export
    if entity_type == "artist":
        export_artist(db, entity)
    elif entity_type == "album":
        export_album(db, entity)

    db.commit()
    return {"detail": f"Rolled back {entity_type}#{entity_id}", "entity_id": entity_id}


# ---------------------------------------------------------------------------
# Artist Consolidation — detect & fix conflicting artist names via MBID
# ---------------------------------------------------------------------------

class ArtistConflict(BaseModel):
    mb_artist_id: str
    names: list  # list of {name, video_count}
    total_videos: int

class ArtistConsolidateRequest(BaseModel):
    mb_artist_id: str
    canonical_name: str


def _primary_artist(name: str) -> str:
    """Extract the primary artist (first name before ';') from a possibly multi-artist string."""
    return name.split(";")[0].strip() if name else name


@router.get("/artist-conflicts", response_model=List[ArtistConflict])
def detect_artist_conflicts(db: Session = Depends(get_db)):
    """
    Find artists that share the same MusicBrainz ID but have different names
    in the library. These are candidates for name consolidation.

    Multi-artist entries like "Sigrid; Bring Me the Horizon" are compared by
    their primary artist (before the semicolon) so featuring collaborations
    are NOT flagged as conflicts with the solo artist name.
    """
    from sqlalchemy import func, distinct

    # Group video_items by mb_artist_id where it's not null
    rows = (
        db.query(
            VideoItem.mb_artist_id,
            VideoItem.artist,
            func.count(VideoItem.id).label("cnt"),
        )
        .filter(VideoItem.mb_artist_id.isnot(None))
        .group_by(VideoItem.mb_artist_id, VideoItem.artist)
        .all()
    )

    # Group by mb_artist_id, keyed by *primary* artist name (before ";")
    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for mb_id, artist_name, cnt in rows:
        groups[mb_id].append({"name": artist_name, "video_count": cnt})

    # Only flag as conflicts when the *primary* artist names differ.
    # "Sigrid" vs "Sigrid; Bring Me the Horizon" share primary "Sigrid" → NOT a conflict.
    # '"Weird Al" Yankovic' vs 'Weird Al Yankovic' → different primaries → IS a conflict.
    conflicts = []
    for mb_id, entries in groups.items():
        if len(entries) <= 1:
            continue
        primary_names = {_primary_artist(e["name"]).lower() for e in entries}
        if len(primary_names) > 1:
            conflicts.append(ArtistConflict(
                mb_artist_id=mb_id,
                names=entries,
                total_videos=sum(e["video_count"] for e in entries),
            ))

    conflicts.sort(key=lambda c: c.total_videos, reverse=True)
    return conflicts


@router.post("/artist-consolidate")
def consolidate_artist(body: ArtistConsolidateRequest, db: Session = Depends(get_db)):
    """
    Apply a canonical name to all videos sharing the given MBID.
    For multi-artist entries (containing ";"), only the primary artist portion
    is replaced so featured collaborations are preserved.
    Also updates the ArtistEntity canonical_name if it exists.
    """
    videos = (
        db.query(VideoItem)
        .filter(VideoItem.mb_artist_id == body.mb_artist_id)
        .all()
    )
    updated = 0
    for video in videos:
        parts = [p.strip() for p in video.artist.split(";")] if video.artist else []
        if len(parts) > 1:
            # Replace only the primary artist, keep featured artists
            parts[0] = body.canonical_name
            new_name = "; ".join(parts)
        else:
            new_name = body.canonical_name
        if video.artist != new_name:
            video.artist = new_name
            updated += 1

    # Update the ArtistEntity canonical_name as well
    artist_ent = (
        db.query(ArtistEntity)
        .filter(ArtistEntity.mb_artist_id == body.mb_artist_id)
        .first()
    )
    if artist_ent:
        artist_ent.canonical_name = body.canonical_name

    db.commit()
    return {"updated": updated, "mb_artist_id": body.mb_artist_id, "canonical_name": body.canonical_name}


# ---------------------------------------------------------------------------
# MBID Statistics — overview for metadata manager dashboard
# ---------------------------------------------------------------------------

class MbidStats(BaseModel):
    total_videos: int
    with_artist_id: int
    with_recording_id: int
    with_release_id: int
    with_release_group_id: int
    with_track_id: int
    with_any_mbid: int
    artist_conflicts: int
    with_playarr_video_id: int = 0
    with_playarr_track_id: int = 0


@router.get("/mbid-stats", response_model=MbidStats)
def get_mbid_stats(db: Session = Depends(get_db)):
    """Summary statistics for MusicBrainz ID coverage across the library."""
    from sqlalchemy import func, or_

    total = db.query(func.count(VideoItem.id)).scalar() or 0
    with_artist = db.query(func.count(VideoItem.id)).filter(VideoItem.mb_artist_id.isnot(None)).scalar() or 0
    with_recording = db.query(func.count(VideoItem.id)).filter(VideoItem.mb_recording_id.isnot(None)).scalar() or 0
    with_release = db.query(func.count(VideoItem.id)).filter(VideoItem.mb_release_id.isnot(None)).scalar() or 0
    with_rg = db.query(func.count(VideoItem.id)).filter(VideoItem.mb_release_group_id.isnot(None)).scalar() or 0
    with_track = db.query(func.count(VideoItem.id)).filter(VideoItem.mb_track_id.isnot(None)).scalar() or 0
    with_any = db.query(func.count(VideoItem.id)).filter(
        or_(
            VideoItem.mb_artist_id.isnot(None),
            VideoItem.mb_recording_id.isnot(None),
            VideoItem.mb_release_id.isnot(None),
            VideoItem.mb_release_group_id.isnot(None),
            VideoItem.mb_track_id.isnot(None),
        )
    ).scalar() or 0

    # Playarr content IDs
    with_pvid = db.query(func.count(VideoItem.id)).filter(VideoItem.playarr_video_id.isnot(None)).scalar() or 0
    with_ptid = db.query(func.count(VideoItem.id)).filter(VideoItem.playarr_track_id.isnot(None)).scalar() or 0

    # Count conflicts
    conflict_count = 0
    groups = (
        db.query(VideoItem.mb_artist_id, func.count(func.distinct(VideoItem.artist)))
        .filter(VideoItem.mb_artist_id.isnot(None))
        .group_by(VideoItem.mb_artist_id)
        .having(func.count(func.distinct(VideoItem.artist)) > 1)
        .all()
    )
    conflict_count = len(groups)

    return MbidStats(
        total_videos=total,
        with_artist_id=with_artist,
        with_recording_id=with_recording,
        with_release_id=with_release,
        with_release_group_id=with_rg,
        with_track_id=with_track,
        with_any_mbid=with_any,
        artist_conflicts=conflict_count,
        with_playarr_video_id=with_pvid,
        with_playarr_track_id=with_ptid,
    )
