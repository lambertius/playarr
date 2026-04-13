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

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File as FastAPIFile
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

    parent.input_params = {"sub_job_ids": sub_ids}
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

    parent.input_params = {"sub_job_ids": sub_ids}
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
    Persists changes to XML sidecars for library clear/rescan durability.
    """
    from app.services.playarr_xml import write_playarr_xml

    videos = (
        db.query(VideoItem)
        .filter(VideoItem.mb_artist_id == body.mb_artist_id)
        .all()
    )
    updated = 0
    updated_videos = []
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
            updated_videos.append(video)

    # Update the ArtistEntity canonical_name as well
    artist_ent = (
        db.query(ArtistEntity)
        .filter(ArtistEntity.mb_artist_id == body.mb_artist_id)
        .first()
    )
    if artist_ent:
        artist_ent.canonical_name = body.canonical_name

    db.commit()

    # Persist to XML sidecars so the choice survives library clear + rescan
    for video in updated_videos:
        try:
            write_playarr_xml(video, db)
        except Exception as e:
            logger.warning(f"XML sidecar write failed for video {video.id}: {e}")

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
    with_complete: int = 0
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

    # Complete = has artist + recording + (release or release_group)
    with_complete = db.query(func.count(VideoItem.id)).filter(
        VideoItem.mb_artist_id.isnot(None),
        VideoItem.mb_recording_id.isnot(None),
        or_(
            VideoItem.mb_release_id.isnot(None),
            VideoItem.mb_release_group_id.isnot(None),
        ),
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
        with_complete=with_complete,
        artist_conflicts=conflict_count,
        with_playarr_video_id=with_pvid,
        with_playarr_track_id=with_ptid,
    )


# ---------------------------------------------------------------------------
# Genre Consolidation — map variant genres to a master genre
# ---------------------------------------------------------------------------

class GenreConflict(BaseModel):
    master_genre: str
    master_genre_id: int
    aliases: list  # list of {id, name, video_count}
    total_videos: int
    blacklisted: bool = False


class GenreConsolidateRequest(BaseModel):
    alias_genre_ids: List[int]
    master_genre_id: int


class GenreUnconsolidateRequest(BaseModel):
    genre_id: int


class GenreConsolidateManualRequest(BaseModel):
    alias_genre_ids: List[int]
    master_genre_name: str


class GenreAddToTileRequest(BaseModel):
    genre_id: int
    master_genre_id: int


class GenreBlacklistTileRequest(BaseModel):
    master_genre_id: int
    blacklisted: bool


class GenreSuggestion(BaseModel):
    master_name: str
    master_id: int
    aliases: list  # list of {id, name, video_count}


@router.get("/genre-consolidations", response_model=List[GenreConflict])
def list_genre_consolidations(db: Session = Depends(get_db)):
    """List all active genre consolidations (genres mapped to a master)."""
    from app.models import Genre, video_genres
    from sqlalchemy import func

    # Find all genres that have a master_genre_id set
    aliases = (
        db.query(Genre)
        .filter(Genre.master_genre_id.isnot(None))
        .all()
    )
    if not aliases:
        return []

    # Group by master_genre_id
    from collections import defaultdict
    groups: dict[int, list] = defaultdict(list)
    for alias in aliases:
        groups[alias.master_genre_id].append(alias)

    results = []
    for master_id, alias_list in groups.items():
        master = db.query(Genre).get(master_id)
        if not master:
            continue
        alias_out = []
        total = 0
        for a in alias_list:
            cnt = (
                db.query(func.count(video_genres.c.video_id))
                .filter(video_genres.c.genre_id == a.id)
                .scalar() or 0
            )
            alias_out.append({"id": a.id, "name": a.name, "video_count": cnt})
            total += cnt
        # Include master's own count
        master_cnt = (
            db.query(func.count(video_genres.c.video_id))
            .filter(video_genres.c.genre_id == master.id)
            .scalar() or 0
        )
        total += master_cnt
        # A tile is blacklisted if the master genre is blacklisted
        results.append(GenreConflict(
            master_genre=master.name,
            master_genre_id=master.id,
            aliases=alias_out,
            total_videos=total,
            blacklisted=bool(master.blacklisted),
        ))

    results.sort(key=lambda c: c.total_videos, reverse=True)
    return results


@router.post("/genre-consolidate")
def consolidate_genres(body: GenreConsolidateRequest, db: Session = Depends(get_db)):
    """
    Map one or more genre variants to a master genre.
    The alias genres remain in the DB but their master_genre_id points
    to the canonical genre. Display logic uses the master name instead.
    """
    from app.models import Genre

    master = db.query(Genre).get(body.master_genre_id)
    if not master:
        raise HTTPException(404, "Master genre not found")

    # Don't allow a genre to be its own master
    alias_ids = [gid for gid in body.alias_genre_ids if gid != master.id]
    updated = 0
    for gid in alias_ids:
        genre = db.query(Genre).get(gid)
        if genre:
            genre.master_genre_id = master.id
            updated += 1

    db.commit()
    return {"updated": updated, "master_genre_id": master.id, "master_name": master.name}


@router.post("/genre-consolidate-manual")
def consolidate_genres_manual(body: GenreConsolidateManualRequest, db: Session = Depends(get_db)):
    """
    Map genre variants to a master genre by name. Creates the master
    genre if it doesn't exist yet.
    """
    from app.models import Genre

    name = body.master_genre_name.strip()
    if not name:
        raise HTTPException(400, "Master genre name cannot be empty")

    # Get or create the master genre
    master = db.query(Genre).filter(Genre.name == name).first()
    if not master:
        master = Genre(name=name, blacklisted=False)
        db.add(master)
        db.flush()

    alias_ids = [gid for gid in body.alias_genre_ids if gid != master.id]
    updated = 0
    for gid in alias_ids:
        genre = db.query(Genre).get(gid)
        if genre:
            genre.master_genre_id = master.id
            updated += 1

    db.commit()
    return {"updated": updated, "master_genre_id": master.id, "master_name": master.name}


@router.post("/genre-unconsolidate")
def unconsolidate_genre(body: GenreUnconsolidateRequest, db: Session = Depends(get_db)):
    """Remove a genre from its master mapping, restoring it as independent."""
    from app.models import Genre

    genre = db.query(Genre).get(body.genre_id)
    if not genre:
        raise HTTPException(404, "Genre not found")
    genre.master_genre_id = None
    db.commit()
    return {"genre_id": genre.id, "name": genre.name}


@router.get("/genre-suggestions", response_model=List[GenreSuggestion])
def suggest_genre_consolidations(db: Session = Depends(get_db)):
    """
    Auto-detect genre variants that should be consolidated.
    Uses regex normalization and fuzzy matching to group similar genres.
    """
    import re
    from app.models import Genre, video_genres
    from sqlalchemy import func

    genres = (
        db.query(
            Genre.id, Genre.name,
            func.count(video_genres.c.video_id).label("cnt"),
        )
        .outerjoin(video_genres, Genre.id == video_genres.c.genre_id)
        .filter(Genre.master_genre_id.is_(None))
        .group_by(Genre.id, Genre.name)
        .all()
    )

    if not genres:
        return []

    def _normalize(name: str) -> str:
        """Normalize a genre name for comparison."""
        s = name.lower().strip()
        # Remove punctuation: dots, dashes, underscores, apostrophes
        s = re.sub(r"[.\-_'\"]+", " ", s)
        # Collapse whitespace
        s = re.sub(r"\s+", " ", s).strip()
        # Common abbreviation expansions
        s = re.sub(r"\balt\b\.?", "alternative", s)
        s = re.sub(r"\belec\b\.?", "electronic", s)
        s = re.sub(r"\bexp\b\.?", "experimental", s)
        s = re.sub(r"\bprog\b\.?", "progressive", s)
        s = re.sub(r"\br&b\b", "rnb", s)
        s = re.sub(r"\br ?n ?b\b", "rnb", s)
        s = re.sub(r"\bhip ?hop\b", "hiphop", s)
        s = re.sub(r"\blo ?fi\b", "lofi", s)
        s = re.sub(r"\bsynth ?pop\b", "synthpop", s)
        s = re.sub(r"\bsynth ?wave\b", "synthwave", s)
        s = re.sub(r"\bpost ?punk\b", "postpunk", s)
        s = re.sub(r"\bpost ?rock\b", "postrock", s)
        s = re.sub(r"\bpost ?grunge\b", "postgrunge", s)
        s = re.sub(r"\bthrash ?metal\b", "thrashmetal", s)
        s = re.sub(r"\bdeath ?metal\b", "deathmetal", s)
        s = re.sub(r"\bblack ?metal\b", "blackmetal", s)
        s = re.sub(r"\bheavy ?metal\b", "heavymetal", s)
        s = re.sub(r"\bnu ?metal\b", "numetal", s)
        s = re.sub(r"\bindie ?rock\b", "indierock", s)
        s = re.sub(r"\bindie ?pop\b", "indiepop", s)
        s = re.sub(r"\bgart? ?rock\b", "garagerock", s)
        s = re.sub(r"\bdream ?pop\b", "dreampop", s)
        s = re.sub(r"\bshoe ?gaze\b", "shoegaze", s)
        # Remove trailing/leading "music"
        s = re.sub(r"\bmusic\b", "", s).strip()
        # Remove spaces for final comparison
        s = s.replace(" ", "")
        return s

    # Group by normalized key
    from collections import defaultdict
    norm_groups: dict[str, list] = defaultdict(list)
    for gid, gname, cnt in genres:
        key = _normalize(gname)
        norm_groups[key].append({"id": gid, "name": gname, "video_count": cnt})

    suggestions = []
    for key, entries in norm_groups.items():
        if len(entries) <= 1:
            continue
        # Pick the entry with the most videos as the suggested master
        entries.sort(key=lambda e: e["video_count"], reverse=True)
        master = entries[0]
        aliases = entries[1:]
        suggestions.append(GenreSuggestion(
            master_name=master["name"],
            master_id=master["id"],
            aliases=aliases,
        ))

    suggestions.sort(key=lambda s: sum(a["video_count"] for a in s.aliases), reverse=True)
    return suggestions


@router.get("/genre-map")
def get_genre_map(db: Session = Depends(get_db)):
    """Return a mapping of alias genre names → master genre names.
    Used by the frontend to display consolidated genre names."""
    from app.models import Genre

    aliases = (
        db.query(Genre)
        .filter(Genre.master_genre_id.isnot(None))
        .all()
    )
    mapping = {}
    for alias in aliases:
        master = db.query(Genre).get(alias.master_genre_id)
        if master:
            mapping[alias.name] = master.name
    return mapping


@router.get("/genre-search")
def search_genres(q: str = "", exclude_tile: Optional[int] = None, db: Session = Depends(get_db)):
    """Search genres by name substring for autofill. Returns up to 15 matches.
    Excludes genres already in the specified consolidation tile."""
    from app.models import Genre, video_genres
    from sqlalchemy import func

    if not q or len(q) < 1:
        return []

    query = (
        db.query(
            Genre.id, Genre.name, Genre.master_genre_id,
            func.count(video_genres.c.video_id).label("cnt"),
        )
        .outerjoin(video_genres, Genre.id == video_genres.c.genre_id)
        .filter(Genre.name.ilike(f"%{q}%"))
        .group_by(Genre.id, Genre.name, Genre.master_genre_id)
        .order_by(Genre.name)
        .limit(15)
        .all()
    )

    results = []
    for gid, gname, master_gid, cnt in query:
        # Skip genres already assigned to the tile being edited
        if exclude_tile is not None and master_gid == exclude_tile:
            continue
        # Skip genres that ARE masters of other tiles (they are tile headers, not addable)
        if exclude_tile is not None and gid == exclude_tile:
            continue
        results.append({
            "id": gid,
            "name": gname,
            "video_count": cnt,
            "already_consolidated": master_gid is not None,
        })
    return results


@router.post("/genre-add-to-tile")
def add_genre_to_tile(body: GenreAddToTileRequest, db: Session = Depends(get_db)):
    """Add a genre as an alias to an existing consolidation tile."""
    from app.models import Genre

    master = db.query(Genre).get(body.master_genre_id)
    if not master:
        raise HTTPException(404, "Master genre not found")

    genre = db.query(Genre).get(body.genre_id)
    if not genre:
        raise HTTPException(404, "Genre not found")

    if genre.id == master.id:
        raise HTTPException(400, "Cannot add master genre as its own alias")

    # If genre is currently a master of other genres, re-point those to the new master
    sub_aliases = db.query(Genre).filter(Genre.master_genre_id == genre.id).all()
    for sa in sub_aliases:
        sa.master_genre_id = master.id

    genre.master_genre_id = master.id
    db.commit()
    return {"genre_id": genre.id, "name": genre.name, "master_genre_id": master.id, "master_name": master.name}


@router.post("/genre-blacklist-tile")
def blacklist_genre_tile(body: GenreBlacklistTileRequest, db: Session = Depends(get_db)):
    """Blacklist or whitelist an entire consolidation tile (master + all aliases)."""
    from app.models import Genre

    master = db.query(Genre).get(body.master_genre_id)
    if not master:
        raise HTTPException(404, "Master genre not found")

    # Update master
    master.blacklisted = body.blacklisted
    updated = 1

    # Update all aliases
    aliases = db.query(Genre).filter(Genre.master_genre_id == master.id).all()
    for alias in aliases:
        alias.blacklisted = body.blacklisted
        updated += 1

    db.commit()
    return {"updated": updated, "master_genre_id": master.id, "blacklisted": body.blacklisted}


@router.post("/genre-create-tile")
def create_genre_tile(body: GenreConsolidateManualRequest, db: Session = Depends(get_db)):
    """Create a new consolidation tile with a master name and optional aliases."""
    from app.models import Genre

    name = body.master_genre_name.strip()
    if not name:
        raise HTTPException(400, "Master genre name cannot be empty")

    # Get or create the master genre
    master = db.query(Genre).filter(Genre.name == name).first()
    if not master:
        master = Genre(name=name, blacklisted=False)
        db.add(master)
        db.flush()

    # Ensure master is not itself an alias
    if master.master_genre_id is not None:
        master.master_genre_id = None

    alias_ids = [gid for gid in body.alias_genre_ids if gid != master.id]
    updated = 0
    for gid in alias_ids:
        genre = db.query(Genre).get(gid)
        if genre:
            genre.master_genre_id = master.id
            updated += 1

    db.commit()
    return {"updated": updated, "master_genre_id": master.id, "master_name": master.name}


# ---------------------------------------------------------------------------
# Artwork Manager — entity-level artwork stats and management
# ---------------------------------------------------------------------------

class ArtworkVideoStats(BaseModel):
    total: int
    with_poster: int
    poster_from_source: int
    poster_from_thumb: int
    with_thumbnail: int
    with_artist_thumb: int
    with_album_thumb: int


class ArtworkEntityStats(BaseModel):
    total: int
    with_art: int
    with_source: int
    missing_with_source: int
    missing_no_source: int


class ArtworkStats(BaseModel):
    videos: ArtworkVideoStats
    artists: ArtworkEntityStats
    albums: ArtworkEntityStats


class ArtworkChildVideo(BaseModel):
    id: int
    title: str
    artist: Optional[str] = None


class ArtworkEntityRow(BaseModel):
    id: int
    name: str
    entity_type: str  # "artist" | "album"
    has_art: bool
    art_path: Optional[str] = None
    has_source: bool
    source_providers: List[str] = []
    video_count: int = 0
    category: str  # "filled" | "missing" | "unavailable"
    provenance: Optional[str] = None
    children: List[ArtworkChildVideo] = []
    created_at: Optional[str] = None
    mb_id: Optional[str] = None
    parent_artist_name: Optional[str] = None
    crop_position: Optional[str] = None

    class Config:
        from_attributes = True


class ArtworkEntitiesResponse(BaseModel):
    items: List[ArtworkEntityRow]
    total: int
    page: int
    per_page: int


class ArtworkRepairRequest(BaseModel):
    entity_type: str  # "artist" | "album"
    entity_ids: List[int]


class EntitySourceRow(BaseModel):
    id: Optional[int] = None
    provider: str
    source_type: str
    url: str
    provenance: Optional[str] = None


class EntitySourcesResponse(BaseModel):
    entity_type: str
    entity_id: int
    mb_id: Optional[str] = None
    sources: List[EntitySourceRow] = []


class EntitySourceUpdate(BaseModel):
    entity_type: str  # "artist" | "album"
    entity_id: int
    mb_id: Optional[str] = None
    wiki_url: Optional[str] = None


@router.get("/artwork-stats", response_model=ArtworkStats)
def get_artwork_stats(db: Session = Depends(get_db)):
    """Artwork fill statistics across the library."""
    from sqlalchemy import func, or_
    from app.models import MediaAsset, Source, SourceProvider
    from app.ai.models import AIThumbnail
    from app.metadata.models import CachedAsset

    # --- Video-level stats ---
    total_videos = db.query(func.count(VideoItem.id)).filter(
        VideoItem.file_path.isnot(None)
    ).scalar() or 0

    with_poster = db.query(func.count(func.distinct(MediaAsset.video_id))).filter(
        MediaAsset.asset_type == "poster", MediaAsset.status == "valid",
    ).scalar() or 0

    _THUMB_PROVENANCES = ("thumb_fallback", "video_thumb_fallback", "youtube_thumb")
    poster_from_thumb = db.query(func.count(func.distinct(MediaAsset.video_id))).filter(
        MediaAsset.asset_type == "poster",
        MediaAsset.status == "valid",
        MediaAsset.provenance.in_(_THUMB_PROVENANCES),
    ).scalar() or 0
    poster_from_source = with_poster - poster_from_thumb

    with_thumb = db.query(func.count(func.distinct(AIThumbnail.video_id))).filter(
        AIThumbnail.is_selected == True,  # noqa: E712
    ).scalar() or 0

    with_artist_thumb = db.query(func.count(func.distinct(MediaAsset.video_id))).filter(
        MediaAsset.asset_type == "artist_thumb", MediaAsset.status == "valid",
    ).scalar() or 0

    with_album_thumb = db.query(func.count(func.distinct(MediaAsset.video_id))).filter(
        MediaAsset.asset_type == "album_thumb", MediaAsset.status == "valid",
    ).scalar() or 0

    # --- Artist-level stats ---
    total_artists = db.query(func.count(ArtistEntity.id)).scalar() or 0

    artist_ids_mbid = set(
        r[0] for r in db.query(ArtistEntity.id).filter(ArtistEntity.mb_artist_id.isnot(None)).all()
    )
    artist_ids_source = set(
        r[0] for r in (
            db.query(VideoItem.artist_entity_id)
            .join(Source, Source.video_id == VideoItem.id)
            .filter(
                VideoItem.artist_entity_id.isnot(None),
                Source.source_type == "artist",
                Source.provider.in_([SourceProvider.wikipedia, SourceProvider.musicbrainz]),
            )
            .distinct()
            .all()
        ) if r[0]
    )
    artists_with_source_ids = artist_ids_mbid | artist_ids_source

    artists_with_art_ids = set(
        r[0] for r in db.query(CachedAsset.entity_id).filter(
            CachedAsset.entity_type == "artist",
            CachedAsset.kind == "poster",
            CachedAsset.status == "valid",
        ).all()
    )
    # Include artists that have art via MediaAsset (artist_thumb)
    ma_artist_art_ids = set(
        r[0] for r in (
            db.query(VideoItem.artist_entity_id)
            .join(MediaAsset, MediaAsset.video_id == VideoItem.id)
            .filter(
                VideoItem.artist_entity_id.isnot(None),
                MediaAsset.asset_type == "artist_thumb",
                MediaAsset.status == "valid",
            )
            .distinct()
            .all()
        ) if r[0]
    )
    artists_with_art_ids = artists_with_art_ids | ma_artist_art_ids

    artists_with_art = len(artists_with_art_ids)
    artists_missing_with_source = len(artists_with_source_ids - artists_with_art_ids)
    artists_missing_no_source = total_artists - len(artists_with_art_ids) - artists_missing_with_source

    # --- Album-level stats ---
    total_albums = db.query(func.count(AlbumEntity.id)).scalar() or 0

    album_ids_mbid = set(
        r[0] for r in db.query(AlbumEntity.id).filter(
            or_(AlbumEntity.mb_release_id.isnot(None), AlbumEntity.mb_release_group_id.isnot(None))
        ).all()
    )
    album_ids_source = set(
        r[0] for r in (
            db.query(VideoItem.album_entity_id)
            .join(Source, Source.video_id == VideoItem.id)
            .filter(
                VideoItem.album_entity_id.isnot(None),
                Source.source_type.in_(["album", "single"]),
                Source.provider.in_([SourceProvider.wikipedia, SourceProvider.musicbrainz]),
            )
            .distinct()
            .all()
        ) if r[0]
    )
    albums_with_source_ids = album_ids_mbid | album_ids_source

    albums_with_art_ids = set(
        r[0] for r in db.query(CachedAsset.entity_id).filter(
            CachedAsset.entity_type == "album",
            CachedAsset.kind == "poster",
            CachedAsset.status == "valid",
        ).all()
    )
    # Include albums that have art via MediaAsset (album_thumb)
    ma_album_art_ids = set(
        r[0] for r in (
            db.query(VideoItem.album_entity_id)
            .join(MediaAsset, MediaAsset.video_id == VideoItem.id)
            .filter(
                VideoItem.album_entity_id.isnot(None),
                MediaAsset.asset_type == "album_thumb",
                MediaAsset.status == "valid",
            )
            .distinct()
            .all()
        ) if r[0]
    )
    albums_with_art_ids = albums_with_art_ids | ma_album_art_ids

    albums_with_art = len(albums_with_art_ids)
    albums_missing_with_source = len(albums_with_source_ids - albums_with_art_ids)
    albums_missing_no_source = total_albums - len(albums_with_art_ids) - albums_missing_with_source

    return ArtworkStats(
        videos=ArtworkVideoStats(
            total=total_videos,
            with_poster=with_poster,
            poster_from_source=poster_from_source,
            poster_from_thumb=poster_from_thumb,
            with_thumbnail=with_thumb,
            with_artist_thumb=with_artist_thumb,
            with_album_thumb=with_album_thumb,
        ),
        artists=ArtworkEntityStats(
            total=total_artists,
            with_art=artists_with_art,
            with_source=len(artists_with_source_ids),
            missing_with_source=artists_missing_with_source,
            missing_no_source=max(0, artists_missing_no_source),
        ),
        albums=ArtworkEntityStats(
            total=total_albums,
            with_art=albums_with_art,
            with_source=len(albums_with_source_ids),
            missing_with_source=albums_missing_with_source,
            missing_no_source=max(0, albums_missing_no_source),
        ),
    )


@router.get("/artwork-entities", response_model=ArtworkEntitiesResponse)
def list_artwork_entities(
    entity_type: str = Query("artist", description="artist or album"),
    status: Optional[str] = Query(None, description="filled, missing, or unavailable"),
    search: Optional[str] = Query(None, description="Search by name (case-insensitive)"),
    sort: Optional[str] = Query("name_asc", description="name_asc, name_desc, date_asc, date_desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List artist or album entities with their artwork status, children, and search/sort."""
    import os
    from sqlalchemy import func
    from app.models import Source, SourceProvider, MediaAsset
    from app.metadata.models import CachedAsset

    if entity_type == "artist":
        all_entities = db.query(ArtistEntity).all()

        art_map: dict[int, CachedAsset] = {}
        for ca in db.query(CachedAsset).filter(
            CachedAsset.entity_type == "artist",
            CachedAsset.kind == "poster",
            CachedAsset.status == "valid",
        ).all():
            art_map[ca.entity_id] = ca

        # MediaAsset fallback: artist_thumb per entity via video linkage
        ma_art_map: dict[int, str] = {}  # entity_id -> file_path
        ma_prov_map: dict[int, str] = {}
        ma_crop_map: dict[int, str | None] = {}
        for row in (
            db.query(VideoItem.artist_entity_id, MediaAsset.file_path, MediaAsset.provenance, MediaAsset.crop_position)
            .join(MediaAsset, MediaAsset.video_id == VideoItem.id)
            .filter(
                VideoItem.artist_entity_id.isnot(None),
                MediaAsset.asset_type == "artist_thumb",
                MediaAsset.status == "valid",
            )
            .all()
        ):
            eid = row[0]
            if eid and eid not in ma_art_map:
                ma_art_map[eid] = row[1]
                ma_prov_map[eid] = row[2]
                ma_crop_map[eid] = row[3]

        source_map: dict[int, set[str]] = {}
        for row in (
            db.query(VideoItem.artist_entity_id, Source.provider)
            .join(Source, Source.video_id == VideoItem.id)
            .filter(
                VideoItem.artist_entity_id.isnot(None),
                Source.source_type == "artist",
            )
            .distinct()
            .all()
        ):
            if row[0]:
                source_map.setdefault(row[0], set()).add(row[1].value if hasattr(row[1], 'value') else str(row[1]))

        # Children: videos linked to each artist entity
        children_map: dict[int, list[ArtworkChildVideo]] = {}
        for vi in (
            db.query(VideoItem.id, VideoItem.title, VideoItem.artist, VideoItem.artist_entity_id)
            .filter(VideoItem.artist_entity_id.isnot(None))
            .order_by(VideoItem.title)
            .all()
        ):
            children_map.setdefault(vi[3], []).append(
                ArtworkChildVideo(id=vi[0], title=vi[1] or "Untitled", artist=vi[2])
            )

        rows: list[ArtworkEntityRow] = []
        for ent in all_entities:
            ca = art_map.get(ent.id)
            ca_valid = ca is not None and ca.local_cache_path and os.path.isfile(ca.local_cache_path)
            ma_path = ma_art_map.get(ent.id)
            ma_valid = ma_path is not None and os.path.isfile(ma_path)
            has_art = ca_valid or ma_valid
            art_path = (ca.local_cache_path if ca_valid else ma_path) if has_art else None
            prov = (ca.provenance if ca_valid else ma_prov_map.get(ent.id)) if has_art else None
            crop = (ca.crop_position if ca_valid else ma_crop_map.get(ent.id)) if has_art else None
            providers = list(source_map.get(ent.id, set()))
            has_mbid = bool(ent.mb_artist_id)
            has_source = has_mbid or len(providers) > 0
            if has_mbid and "musicbrainz" not in providers:
                providers.append("musicbrainz")

            if has_art:
                category = "filled"
            elif has_source:
                category = "missing"
            else:
                category = "unavailable"

            kids = children_map.get(ent.id, [])

            rows.append(ArtworkEntityRow(
                id=ent.id,
                name=ent.canonical_name,
                entity_type="artist",
                has_art=has_art,
                art_path=art_path,
                has_source=has_source,
                source_providers=providers,
                video_count=len(kids),
                category=category,
                provenance=prov,
                children=kids,
                created_at=ent.created_at.isoformat() if ent.created_at else None,
                mb_id=ent.mb_artist_id,
                crop_position=crop,
            ))

    elif entity_type == "album":
        all_entities = db.query(AlbumEntity).all()

        art_map = {}
        for ca in db.query(CachedAsset).filter(
            CachedAsset.entity_type == "album",
            CachedAsset.kind == "poster",
            CachedAsset.status == "valid",
        ).all():
            art_map[ca.entity_id] = ca

        # MediaAsset fallback: album_thumb per entity via video linkage
        ma_art_map: dict[int, str] = {}
        ma_prov_map: dict[int, str] = {}
        ma_crop_map: dict[int, str | None] = {}
        for row in (
            db.query(VideoItem.album_entity_id, MediaAsset.file_path, MediaAsset.provenance, MediaAsset.crop_position)
            .join(MediaAsset, MediaAsset.video_id == VideoItem.id)
            .filter(
                VideoItem.album_entity_id.isnot(None),
                MediaAsset.asset_type == "album_thumb",
                MediaAsset.status == "valid",
            )
            .all()
        ):
            eid = row[0]
            if eid and eid not in ma_art_map:
                ma_art_map[eid] = row[1]
                ma_prov_map[eid] = row[2]
                ma_crop_map[eid] = row[3]

        source_map = {}
        for row in (
            db.query(VideoItem.album_entity_id, Source.provider)
            .join(Source, Source.video_id == VideoItem.id)
            .filter(
                VideoItem.album_entity_id.isnot(None),
                Source.source_type.in_(["album", "single"]),
            )
            .distinct()
            .all()
        ):
            if row[0]:
                source_map.setdefault(row[0], set()).add(row[1].value if hasattr(row[1], 'value') else str(row[1]))

        # Children: videos linked to each album entity
        children_map: dict[int, list[ArtworkChildVideo]] = {}
        for vi in (
            db.query(VideoItem.id, VideoItem.title, VideoItem.artist, VideoItem.album_entity_id)
            .filter(VideoItem.album_entity_id.isnot(None))
            .order_by(VideoItem.title)
            .all()
        ):
            children_map.setdefault(vi[3], []).append(
                ArtworkChildVideo(id=vi[0], title=vi[1] or "Untitled", artist=vi[2])
            )

        artist_names: dict[int, str] = {}
        for a in db.query(ArtistEntity).all():
            artist_names[a.id] = a.canonical_name

        rows = []
        for ent in all_entities:
            ca = art_map.get(ent.id)
            ca_valid = ca is not None and ca.local_cache_path and os.path.isfile(ca.local_cache_path)
            ma_path = ma_art_map.get(ent.id)
            ma_valid = ma_path is not None and os.path.isfile(ma_path)
            has_art = ca_valid or ma_valid
            art_path = (ca.local_cache_path if ca_valid else ma_path) if has_art else None
            prov = (ca.provenance if ca_valid else ma_prov_map.get(ent.id)) if has_art else None
            crop = (ca.crop_position if ca_valid else ma_crop_map.get(ent.id)) if has_art else None
            providers = list(source_map.get(ent.id, set()))
            has_mbid = bool(ent.mb_release_id or ent.mb_release_group_id)
            has_source = has_mbid or len(providers) > 0
            if has_mbid and "musicbrainz" not in providers:
                providers.append("musicbrainz")

            if has_art:
                category = "filled"
            elif has_source:
                category = "missing"
            else:
                category = "unavailable"

            artist_name = artist_names.get(ent.artist_id, "") if ent.artist_id else ""
            display_name = f"{artist_name} — {ent.title}" if artist_name else ent.title
            kids = children_map.get(ent.id, [])

            rows.append(ArtworkEntityRow(
                id=ent.id,
                name=display_name,
                entity_type="album",
                has_art=has_art,
                art_path=art_path,
                has_source=has_source,
                source_providers=providers,
                video_count=len(kids),
                category=category,
                provenance=prov,
                children=kids,
                created_at=ent.created_at.isoformat() if ent.created_at else None,
                mb_id=ent.mb_release_id or ent.mb_release_group_id,
                parent_artist_name=artist_name or None,
                crop_position=crop,
            ))
    elif entity_type == "poster":
        # Video-level poster art (uses MediaAsset, not CachedAsset)
        from app.models import MediaAsset
        all_videos = db.query(VideoItem).all()

        # Build map of video_id -> MediaAsset for valid posters
        poster_map: dict[int, MediaAsset] = {}
        for ma in db.query(MediaAsset).filter(
            MediaAsset.asset_type == "poster",
            MediaAsset.status == "valid",
        ).all():
            poster_map[ma.video_id] = ma

        rows = []
        for vi in all_videos:
            ma = poster_map.get(vi.id)
            has_art = ma is not None and ma.file_path and os.path.isfile(ma.file_path)

            if has_art:
                category = "filled"
            else:
                category = "missing"

            display_name = f"{vi.artist or 'Unknown'} — {vi.title or 'Untitled'}"

            rows.append(ArtworkEntityRow(
                id=vi.id,
                name=display_name,
                entity_type="poster",
                has_art=has_art,
                art_path=ma.file_path if ma else None,
                has_source=True,
                source_providers=[],
                video_count=1,
                category=category,
                provenance=ma.provenance if ma else None,
                children=[],
                created_at=vi.created_at.isoformat() if vi.created_at else None,
                mb_id=None,
                crop_position=ma.crop_position if ma else None,
            ))
    else:
        raise HTTPException(400, "entity_type must be 'artist', 'album', or 'poster'")

    # Filter by status
    if status:
        rows = [r for r in rows if r.category == status]

    # Search filter
    if search:
        search_lower = search.lower()
        rows = [r for r in rows if search_lower in r.name.lower()]

    # Sort
    if sort == "name_desc":
        rows.sort(key=lambda r: r.name.lower(), reverse=True)
    elif sort == "date_asc":
        rows.sort(key=lambda r: r.created_at or "")
    elif sort == "date_desc":
        rows.sort(key=lambda r: r.created_at or "", reverse=True)
    else:  # name_asc (default)
        rows.sort(key=lambda r: r.name.lower())

    total = len(rows)
    start = (page - 1) * per_page
    page_rows = rows[start:start + per_page]

    return ArtworkEntitiesResponse(items=page_rows, total=total, page=page, per_page=per_page)


@router.post("/artwork-bulk-repair")
def artwork_bulk_repair(
    body: ArtworkRepairRequest,
    db: Session = Depends(get_db),
):
    """Repair missing entity artwork by trying disk/CachedAsset/sibling strategies.

    For each entity:
    1. Check if CachedAsset already valid — skip.
    2. Look for art on disk in shared folders.
    3. Copy from sibling MediaAsset with same entity.
    """
    import os
    from app.models import MediaAsset
    from app.metadata.models import CachedAsset
    from app.pipeline_lib.services.artwork_service import validate_file as _vf
    from app.pipeline_lib.services.artwork_manager import (
        _safe_name, get_artists_dir, get_albums_dir,
    )

    repaired = 0
    already_ok = 0
    still_missing = 0
    entity_type = body.entity_type

    if entity_type == "artist":
        entities = db.query(ArtistEntity).filter(ArtistEntity.id.in_(body.entity_ids)).all()
    elif entity_type == "album":
        entities = db.query(AlbumEntity).filter(AlbumEntity.id.in_(body.entity_ids)).all()
    else:
        raise HTTPException(400, "entity_type must be 'artist' or 'album'")

    for ent in entities:
        ca = db.query(CachedAsset).filter(
            CachedAsset.entity_type == entity_type,
            CachedAsset.entity_id == ent.id,
            CachedAsset.kind == "poster",
            CachedAsset.status == "valid",
        ).first()
        if ca and ca.local_cache_path and os.path.isfile(ca.local_cache_path):
            already_ok += 1
            continue

        art_path = None

        # Strategy 1: Check shared folder on disk
        try:
            if entity_type == "artist":
                name = ent.canonical_name
                candidate = os.path.join(get_artists_dir(), _safe_name(name), "poster.jpg")
                if os.path.isfile(candidate):
                    art_path = candidate
            elif entity_type == "album":
                artist_ent = db.query(ArtistEntity).get(ent.artist_id) if ent.artist_id else None
                artist_name = artist_ent.canonical_name if artist_ent else ""
                if artist_name:
                    candidate = os.path.join(
                        get_albums_dir(), _safe_name(artist_name),
                        _safe_name(ent.title), "poster.jpg",
                    )
                    if os.path.isfile(candidate):
                        art_path = candidate
        except Exception:
            pass

        # Strategy 2: Check any existing MediaAsset for this entity's videos
        if not art_path:
            asset_type = "artist_thumb" if entity_type == "artist" else "album_thumb"
            filter_col = VideoItem.artist_entity_id if entity_type == "artist" else VideoItem.album_entity_id
            sibling = (
                db.query(MediaAsset.file_path)
                .join(VideoItem, MediaAsset.video_id == VideoItem.id)
                .filter(
                    filter_col == ent.id,
                    MediaAsset.asset_type == asset_type,
                    MediaAsset.status == "valid",
                )
                .first()
            )
            if sibling and sibling[0] and os.path.isfile(sibling[0]):
                art_path = sibling[0]

        # Strategy 3: CachedAsset exists but file missing — mark stale
        if not art_path and ca and ca.local_cache_path:
            ca.status = "missing"

        if art_path:
            vr = _vf(art_path)
            if ca:
                ca.local_cache_path = art_path
                ca.status = "valid" if (vr and vr.valid) else "invalid"
                if vr and vr.valid:
                    ca.width = vr.width
                    ca.height = vr.height
                    ca.file_size_bytes = vr.file_size_bytes
                    ca.checksum = vr.file_hash
            else:
                db.add(CachedAsset(
                    entity_type=entity_type,
                    entity_id=ent.id,
                    kind="poster",
                    local_cache_path=art_path,
                    provenance="artwork_repair",
                    status="valid" if (vr and vr.valid) else "invalid",
                    width=vr.width if vr and vr.valid else None,
                    height=vr.height if vr and vr.valid else None,
                    file_size_bytes=vr.file_size_bytes if vr and vr.valid else None,
                    checksum=vr.file_hash if vr and vr.valid else None,
                ))
            repaired += 1
        else:
            still_missing += 1

    db.commit()
    return {
        "status": "repaired",
        "repaired": repaired,
        "already_ok": already_ok,
        "still_missing": still_missing,
        "total": len(entities),
    }


# ---------------------------------------------------------------------------
# Entity artwork image serving, upload, delete
# ---------------------------------------------------------------------------

@router.get("/entity-artwork/{entity_type}/{entity_id}")
def get_entity_artwork(
    entity_type: str,
    entity_id: int,
    db: Session = Depends(get_db),
):
    """Serve entity poster artwork image for display in the artwork manager."""
    import os
    from fastapi.responses import FileResponse
    from app.metadata.models import CachedAsset

    if entity_type not in ("artist", "album", "poster"):
        raise HTTPException(400, "entity_type must be 'artist', 'album', or 'poster'")

    if entity_type == "poster":
        # Video poster — served from MediaAsset
        from app.models import MediaAsset
        ma = db.query(MediaAsset).filter(
            MediaAsset.video_id == entity_id,
            MediaAsset.asset_type == "poster",
            MediaAsset.status == "valid",
        ).first()
        if not ma or not ma.file_path or not os.path.isfile(ma.file_path):
            raise HTTPException(404, "No artwork found")
        return FileResponse(
            ma.file_path,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    ca = db.query(CachedAsset).filter(
        CachedAsset.entity_type == entity_type,
        CachedAsset.entity_id == entity_id,
        CachedAsset.kind == "poster",
        CachedAsset.status == "valid",
    ).first()

    if ca and ca.local_cache_path and os.path.isfile(ca.local_cache_path):
        return FileResponse(
            ca.local_cache_path,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    # Fallback: check MediaAsset (artist_thumb / album_thumb) via video linkage
    from app.models import MediaAsset, VideoItem
    asset_type = "artist_thumb" if entity_type == "artist" else "album_thumb"
    id_col = VideoItem.artist_entity_id if entity_type == "artist" else VideoItem.album_entity_id
    ma = (
        db.query(MediaAsset)
        .join(VideoItem, VideoItem.id == MediaAsset.video_id)
        .filter(
            id_col == entity_id,
            MediaAsset.asset_type == asset_type,
            MediaAsset.status == "valid",
        )
        .first()
    )
    if ma and ma.file_path and os.path.isfile(ma.file_path):
        return FileResponse(
            ma.file_path,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    raise HTTPException(404, "No artwork found")


@router.put("/entity-artwork/{entity_type}/{entity_id}")
async def upload_entity_artwork(
    entity_type: str,
    entity_id: int,
    file: UploadFile = FastAPIFile(...),
    db: Session = Depends(get_db),
):
    """Upload / replace artwork for an artist or album entity."""
    import os
    from datetime import datetime, timezone
    from app.metadata.models import CachedAsset
    from app.pipeline_lib.services.artwork_manager import (
        _safe_name, get_artists_dir, get_albums_dir,
    )
    from app.pipeline_lib.services.artwork_service import validate_and_store_upload, validate_file

    if entity_type not in ("artist", "album", "poster"):
        raise HTTPException(400, "entity_type must be 'artist', 'album', or 'poster'")

    if entity_type == "artist":
        ent = db.query(ArtistEntity).get(entity_id)
        if not ent:
            raise HTTPException(404, "Artist entity not found")
        dest_dir = os.path.join(get_artists_dir(), _safe_name(ent.canonical_name))
    elif entity_type == "poster":
        vi = db.query(VideoItem).get(entity_id)
        if not vi:
            raise HTTPException(404, "Video not found")
        from app.config import get_settings
        dest_dir = os.path.join(get_settings().library_dir, "_PlayarrCache", "videos", str(vi.id))
    else:
        ent = db.query(AlbumEntity).get(entity_id)
        if not ent:
            raise HTTPException(404, "Album entity not found")
        artist_ent = db.query(ArtistEntity).get(ent.artist_id) if ent.artist_id else None
        artist_name = artist_ent.canonical_name if artist_ent else "Unknown Artist"
        dest_dir = os.path.join(
            get_albums_dir(), _safe_name(artist_name), _safe_name(ent.title),
        )

    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, "poster.jpg")

    # Read upload bytes and validate
    file_bytes = await file.read()
    result = validate_and_store_upload(file_bytes, dest_path)
    if not result.success:
        raise HTTPException(400, f"Invalid image: {result.error}")

    now = datetime.now(timezone.utc)
    vr = validate_file(dest_path)

    if entity_type == "poster":
        # Upsert MediaAsset for video poster
        from app.models import MediaAsset
        ma = db.query(MediaAsset).filter(
            MediaAsset.video_id == entity_id,
            MediaAsset.asset_type == "poster",
        ).first()
        if ma:
            ma.file_path = dest_path
            ma.status = "valid"
            ma.provenance = "manual_upload"
            ma.width = vr.width if vr and vr.valid else None
            ma.height = vr.height if vr and vr.valid else None
            ma.file_size_bytes = vr.file_size_bytes if vr and vr.valid else None
            ma.file_hash = vr.file_hash if vr and vr.valid else None
            ma.last_validated_at = now
        else:
            ma = MediaAsset(
                video_id=entity_id,
                asset_type="poster",
                file_path=dest_path,
                provenance="manual_upload",
                status="valid",
                width=vr.width if vr and vr.valid else None,
                height=vr.height if vr and vr.valid else None,
                file_size_bytes=vr.file_size_bytes if vr and vr.valid else None,
                file_hash=vr.file_hash if vr and vr.valid else None,
                last_validated_at=now,
            )
            db.add(ma)
    else:
        # Upsert CachedAsset for artist/album
        ca = db.query(CachedAsset).filter(
            CachedAsset.entity_type == entity_type,
            CachedAsset.entity_id == entity_id,
            CachedAsset.kind == "poster",
        ).first()
        if ca:
            ca.local_cache_path = dest_path
            ca.status = "valid"
            ca.provenance = "manual_upload"
            ca.width = vr.width if vr and vr.valid else None
            ca.height = vr.height if vr and vr.valid else None
            ca.file_size_bytes = vr.file_size_bytes if vr and vr.valid else None
            ca.checksum = vr.file_hash if vr and vr.valid else None
            ca.last_validated_at = now
        else:
            ca = CachedAsset(
                entity_type=entity_type,
                entity_id=entity_id,
                kind="poster",
                local_cache_path=dest_path,
                provenance="manual_upload",
                status="valid",
                width=vr.width if vr and vr.valid else None,
                height=vr.height if vr and vr.valid else None,
                file_size_bytes=vr.file_size_bytes if vr and vr.valid else None,
                checksum=vr.file_hash if vr and vr.valid else None,
                last_validated_at=now,
            )
            db.add(ca)

    db.commit()
    return {"detail": "Artwork uploaded", "entity_type": entity_type, "entity_id": entity_id}


@router.delete("/entity-artwork/{entity_type}/{entity_id}")
def delete_entity_artwork(
    entity_type: str,
    entity_id: int,
    db: Session = Depends(get_db),
):
    """Delete artwork for an artist or album entity."""
    import os
    from app.metadata.models import CachedAsset

    if entity_type not in ("artist", "album", "poster"):
        raise HTTPException(400, "entity_type must be 'artist', 'album', or 'poster'")

    if entity_type == "poster":
        from app.models import MediaAsset
        ma = db.query(MediaAsset).filter(
            MediaAsset.video_id == entity_id,
            MediaAsset.asset_type == "poster",
        ).first()
        if not ma:
            raise HTTPException(404, "No artwork record found")
        if ma.file_path and os.path.isfile(ma.file_path):
            try:
                os.remove(ma.file_path)
            except OSError:
                pass
        db.delete(ma)
        db.commit()
        return {"detail": "Artwork deleted", "entity_type": entity_type, "entity_id": entity_id}

    ca = db.query(CachedAsset).filter(
        CachedAsset.entity_type == entity_type,
        CachedAsset.entity_id == entity_id,
        CachedAsset.kind == "poster",
    ).first()

    if not ca:
        raise HTTPException(404, "No artwork record found")

    # Remove file from disk
    if ca.local_cache_path and os.path.isfile(ca.local_cache_path):
        try:
            os.remove(ca.local_cache_path)
        except OSError:
            pass

    db.delete(ca)
    db.commit()
    return {"detail": "Artwork deleted", "entity_type": entity_type, "entity_id": entity_id}


class CropPositionUpdate(BaseModel):
    crop_position: Optional[str] = None  # CSS object-position e.g. "50% 30%"


@router.patch("/entity-artwork/{entity_type}/{entity_id}/crop")
def update_entity_crop(
    entity_type: str,
    entity_id: int,
    body: CropPositionUpdate,
    db: Session = Depends(get_db),
):
    """Update crop position for entity artwork."""
    import re
    from app.metadata.models import CachedAsset

    if entity_type not in ("artist", "album", "poster"):
        raise HTTPException(400, "entity_type must be 'artist', 'album', or 'poster'")

    # Validate crop_position format (CSS object-position values)
    if body.crop_position is not None:
        if not re.match(r"^\d{1,3}%\s+\d{1,3}%$", body.crop_position):
            raise HTTPException(400, "crop_position must be like '50% 30%'")

    if entity_type == "poster":
        from app.models import MediaAsset
        ma = db.query(MediaAsset).filter(
            MediaAsset.video_id == entity_id,
            MediaAsset.asset_type == "poster",
            MediaAsset.status == "valid",
        ).first()
        if not ma:
            raise HTTPException(404, "No artwork record found")
        ma.crop_position = body.crop_position
        db.commit()
        return {"detail": "Crop updated", "crop_position": body.crop_position}

    # artist/album: try CachedAsset first, then MediaAsset fallback
    ca = db.query(CachedAsset).filter(
        CachedAsset.entity_type == entity_type,
        CachedAsset.entity_id == entity_id,
        CachedAsset.kind == "poster",
        CachedAsset.status == "valid",
    ).first()
    if ca:
        ca.crop_position = body.crop_position
        db.commit()
        return {"detail": "Crop updated", "crop_position": body.crop_position}

    # MediaAsset fallback via video linkage
    from app.models import MediaAsset
    asset_type = "artist_thumb" if entity_type == "artist" else "album_thumb"
    entity_col = VideoItem.artist_entity_id if entity_type == "artist" else VideoItem.album_entity_id
    ma = (
        db.query(MediaAsset)
        .join(VideoItem, MediaAsset.video_id == VideoItem.id)
        .filter(entity_col == entity_id, MediaAsset.asset_type == asset_type, MediaAsset.status == "valid")
        .first()
    )
    if not ma:
        raise HTTPException(404, "No artwork record found")
    ma.crop_position = body.crop_position
    db.commit()
    return {"detail": "Crop updated", "crop_position": body.crop_position}


# ---------------------------------------------------------------------------
# Entity source management (MB ID + Wikipedia URL)
# ---------------------------------------------------------------------------

@router.get("/entity-sources/{entity_type}/{entity_id}", response_model=EntitySourcesResponse)
def get_entity_sources(
    entity_type: str,
    entity_id: int,
    db: Session = Depends(get_db),
):
    """Get current MusicBrainz ID and Wikipedia source URLs for an entity."""
    from app.models import Source, SourceProvider

    if entity_type not in ("artist", "album"):
        raise HTTPException(400, "entity_type must be 'artist' or 'album'")

    mb_id = None
    if entity_type == "artist":
        ent = db.query(ArtistEntity).get(entity_id)
        if not ent:
            raise HTTPException(404, "Artist entity not found")
        mb_id = ent.mb_artist_id
    else:
        ent = db.query(AlbumEntity).get(entity_id)
        if not ent:
            raise HTTPException(404, "Album entity not found")
        mb_id = ent.mb_release_id or ent.mb_release_group_id

    # Find Wikipedia Source records linked through videos
    filter_col = VideoItem.artist_entity_id if entity_type == "artist" else VideoItem.album_entity_id
    source_type_match = "artist" if entity_type == "artist" else "album"

    wiki_sources = (
        db.query(Source)
        .join(VideoItem, Source.video_id == VideoItem.id)
        .filter(
            filter_col == entity_id,
            Source.provider == SourceProvider.wikipedia,
            Source.source_type == source_type_match,
        )
        .all()
    )

    sources: list[EntitySourceRow] = []
    seen_urls: set[str] = set()
    for ws in wiki_sources:
        url = ws.canonical_url or ws.original_url or ""
        if url and url not in seen_urls:
            seen_urls.add(url)
            sources.append(EntitySourceRow(
                id=ws.id,
                provider="wikipedia",
                source_type=ws.source_type,
                url=url,
                provenance=ws.provenance,
            ))

    return EntitySourcesResponse(
        entity_type=entity_type,
        entity_id=entity_id,
        mb_id=mb_id,
        sources=sources,
    )


@router.put("/entity-sources")
def update_entity_sources(
    body: EntitySourceUpdate,
    db: Session = Depends(get_db),
):
    """Update MusicBrainz ID and/or Wikipedia source URL for an entity.

    For Wikipedia URLs, updates (or creates) Source records on all videos
    linked to this entity.
    """
    from app.models import Source, SourceProvider

    entity_type = body.entity_type
    entity_id = body.entity_id

    if entity_type not in ("artist", "album"):
        raise HTTPException(400, "entity_type must be 'artist' or 'album'")

    # Update MB ID on entity
    if entity_type == "artist":
        ent = db.query(ArtistEntity).get(entity_id)
        if not ent:
            raise HTTPException(404, "Artist entity not found")
        if body.mb_id is not None:
            ent.mb_artist_id = body.mb_id or None
    else:
        ent = db.query(AlbumEntity).get(entity_id)
        if not ent:
            raise HTTPException(404, "Album entity not found")
        if body.mb_id is not None:
            # Store as release group ID (preferred for artwork)
            ent.mb_release_group_id = body.mb_id or None

    # Update Wikipedia Source records across all linked videos
    if body.wiki_url is not None:
        filter_col = VideoItem.artist_entity_id if entity_type == "artist" else VideoItem.album_entity_id
        source_type_val = "artist" if entity_type == "artist" else "album"

        video_ids = [
            r[0] for r in db.query(VideoItem.id).filter(filter_col == entity_id).all()
        ]

        if body.wiki_url:
            # Extract Wikipedia page slug for source_video_id
            from urllib.parse import urlparse, unquote
            parsed = urlparse(body.wiki_url)
            wiki_page_id = unquote(parsed.path.rsplit("/", 1)[-1]) if parsed.path else body.wiki_url

            # Upsert Wikipedia source on each video
            for vid in video_ids:
                existing = db.query(Source).filter(
                    Source.video_id == vid,
                    Source.provider == SourceProvider.wikipedia,
                    Source.source_type == source_type_val,
                ).first()
                if existing:
                    existing.canonical_url = body.wiki_url
                    existing.original_url = body.wiki_url
                    existing.source_video_id = wiki_page_id
                    existing.provenance = "manual"
                else:
                    db.add(Source(
                        video_id=vid,
                        provider=SourceProvider.wikipedia,
                        source_type=source_type_val,
                        source_video_id=wiki_page_id,
                        original_url=body.wiki_url,
                        canonical_url=body.wiki_url,
                        provenance="manual",
                    ))
        else:
            # Clear Wikipedia sources
            for vid in video_ids:
                db.query(Source).filter(
                    Source.video_id == vid,
                    Source.provider == SourceProvider.wikipedia,
                    Source.source_type == source_type_val,
                ).delete()

    db.commit()
    return {"detail": "Sources updated", "entity_type": entity_type, "entity_id": entity_id}
