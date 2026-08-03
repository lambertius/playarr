"""
Library API — CRUD and search for video items.
"""
import json
import math
import os
import random
import shutil
import logging
import time
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy.orm.attributes import flag_modified
from app.database import get_db
from app.models import VideoItem, Genre, MediaAsset, video_genres, PlaybackHistory, QualitySignature
from app.metadata.models import ArtistEntity, AlbumEntity, TrackEntity
from app.schemas import (
    VideoItemOut, VideoItemSummary, VideoItemUpdate,
    PaginatedResponse, MetadataSnapshotOut,
    CanonicalTrackCreate, CanonicalTrackUpdate, CanonicalTrackOut,
    SetParentVideoRequest, ArtistEntityOut, ProcessingStateOut,
    SourceOut, SourceCreate, SourceUpdate,
)
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/library", tags=["Library"])
# Quality bucket boundaries (upper limits, inclusive) — resolutions in-between
# round UP to the next bucket.  e.g. 944p → 1080p bucket.
QUALITY_BUCKETS = [
    (360, "360p"),
    (480, "480p"),
    (720, "720p"),
    (1080, "1080p"),
    (1440, "2K"),
    (999999, "4K"),
]
QUALITY_LABELS = [label for _, label in QUALITY_BUCKETS]
AI_ENRICHMENT_STEPS = ("ai_enriched", "scenes_analyzed")


def _enrichment_lifecycle(
    processing_state: dict | None,
    review_category: str | None = None,
) -> tuple[str, list[str], list[str], list[str], list[dict], str | None, str | None, list[str]]:
    """Return the canonical AI lifecycle and its explainable detail fields.

    Only AI enrichment and scene analysis belong in this status.  Previously,
    unrelated completed pipeline steps could make an item appear "Partial".
    """
    state = processing_state or {}
    requested_steps: list[str] = []
    completed_steps: list[str] = []
    incomplete_steps: list[str] = []
    failed_steps: list[dict] = []
    running = False
    queued = False
    stale = False
    provider = None
    model = None
    timestamps: list[str] = []
    for step in AI_ENRICHMENT_STEPS:
        if step not in state:
            continue
        requested_steps.append(step)
        raw = state.get(step)
        if isinstance(raw, bool):
            raw = {"completed": raw}
        elif not isinstance(raw, dict):
            raw = {}
        if raw.get("completed") is True:
            completed_steps.append(step)
        else:
            incomplete_steps.append(step)
        step_state = str(raw.get("status") or raw.get("state") or "").lower()
        running = running or step_state == "running"
        queued = queued or step_state == "queued"
        stale = stale or step_state == "stale" or raw.get("stale") is True
        error = raw.get("error") or raw.get("error_message")
        if error and raw.get("completed") is not True:
            failed_steps.append({
                "step": step,
                "code": raw.get("error_code") or "step_failed",
                "message": str(error),
            })
        provider = provider or raw.get("provider")
        model = model or raw.get("model")
        for key in ("completed_at", "updated_at", "started_at", "attempted_at"):
            if raw.get(key):
                timestamps.append(str(raw[key]))

    if running:
        lifecycle = "running"
    elif queued:
        lifecycle = "queued"
    elif stale:
        lifecycle = "stale"
    elif requested_steps and not incomplete_steps:
        lifecycle = "complete"
    elif failed_steps and not completed_steps:
        lifecycle = "failed"
    elif requested_steps:
        lifecycle = "partial"
    elif review_category in {"ai_partial", "ai_pending"}:
        # Legacy review flags can survive an interrupted write of processing
        # state. Keep them actionable until a review scan reconciles the row.
        lifecycle = "partial"
    else:
        lifecycle = "not_requested"
    return (
        lifecycle, requested_steps, completed_steps, incomplete_steps,
        failed_steps, provider, model, timestamps,
    )


def _enrichment_sql_state():
    """Build SQL expressions with the same precedence as `_enrichment_lifecycle`."""
    ps = VideoItem.processing_state
    ai_done = func.coalesce(func.json_extract(ps, "$.ai_enriched.completed"), 0) == 1
    sc_done = func.coalesce(func.json_extract(ps, "$.scenes_analyzed.completed"), 0) == 1
    ai_present = func.json_type(ps, "$.ai_enriched").is_not(None)
    sc_present = func.json_type(ps, "$.scenes_analyzed").is_not(None)
    ai_state = func.coalesce(func.json_extract(ps, "$.ai_enriched.status"), "")
    sc_state = func.coalesce(func.json_extract(ps, "$.scenes_analyzed.status"), "")
    ai_error = func.coalesce(
        func.json_extract(ps, "$.ai_enriched.error"),
        func.json_extract(ps, "$.ai_enriched.error_message"),
    )
    sc_error = func.coalesce(
        func.json_extract(ps, "$.scenes_analyzed.error"),
        func.json_extract(ps, "$.scenes_analyzed.error_message"),
    )
    is_running = or_(ai_state == "running", sc_state == "running")
    is_queued = or_(ai_state == "queued", sc_state == "queued")
    is_stale = or_(
        ai_state == "stale", sc_state == "stale",
        func.coalesce(func.json_extract(ps, "$.ai_enriched.stale"), 0) == 1,
        func.coalesce(func.json_extract(ps, "$.scenes_analyzed.stale"), 0) == 1,
    )
    review_incomplete = func.coalesce(VideoItem.review_category, "").in_(("ai_partial", "ai_pending"))
    has_requested = or_(ai_present, sc_present)
    has_completed = or_(ai_done, sc_done)
    all_completed = and_(
        has_requested,
        or_(~ai_present, ai_done),
        or_(~sc_present, sc_done),
    )
    has_incomplete = or_(and_(ai_present, ~ai_done), and_(sc_present, ~sc_done))
    has_error = or_(ai_error.is_not(None), sc_error.is_not(None))
    settled = and_(~is_running, ~is_queued, ~is_stale)
    return {
        "running": is_running,
        "queued": and_(~is_running, is_queued),
        "stale": and_(~is_running, ~is_queued, is_stale),
        "partial": and_(
            settled,
            or_(and_(has_requested, has_incomplete, or_(has_completed, ~has_error)),
                and_(~has_requested, review_incomplete)),
        ),
        "failed": and_(settled, has_requested, has_error, ~has_completed),
        "complete": and_(settled, all_completed),
        "not_requested": and_(settled, ~has_requested, ~review_incomplete),
    }


def _apply_enrichment_filter(query, enrichment: str):
    states = _enrichment_sql_state()
    canonical = {"enriched": "complete", "pending": "not_requested"}.get(enrichment, enrichment)
    condition = states.get(canonical)
    return query.filter(condition) if condition is not None else query


def _enrichment_sort_expression():
    states = _enrichment_sql_state()
    return case(
        (states["queued"], 1),
        (states["running"], 2),
        (states["partial"], 3),
        (states["complete"], 4),
        (states["failed"], 5),
        (states["stale"], 6),
        else_=0,
    )


def _height_to_quality_bucket(height: int | None) -> str | None:
    """Map a pixel height to the simplified quality bucket label."""
    if height is None or height <= 0:
        return None
    for upper, label in QUALITY_BUCKETS:
        if height <= upper:
            return label
    return "4K"


def _quality_bucket_range(bucket: str) -> tuple[int, int] | None:
    """Return the (min_height, max_height] range for a quality bucket label."""
    prev_upper = 0
    for upper, label in QUALITY_BUCKETS:
        if label == bucket:
            return (prev_upper + 1, upper)
        prev_upper = upper
    return None


# "Party like it's…" era weighting.  A video's weight halves for every
# ERA_HALFLIFE_YEARS it sits before the target year, giving a gradual fall-off
# so era-appropriate tracks cluster at the front of the queue.  Videos with no
# known year stay in the pool but at a low fixed weight.
ERA_HALFLIFE_YEARS = 10.0
ERA_NULL_YEAR_WEIGHT = 0.05


def _era_weight(year: Optional[int], target_year: int) -> float:
    """Weight a video by how close its year is to *target_year* (older = lower).

    Future videos are filtered out before this runs, so distance is clamped to
    ``>= 0``.  Unknown years get a small constant weight.
    """
    if year is None:
        return ERA_NULL_YEAR_WEIGHT
    distance = max(0, target_year - year)
    return 0.5 ** (distance / ERA_HALFLIFE_YEARS)


def _weighted_shuffle(tracks: list[dict], era_weights: Optional[dict[int, float]] = None) -> list[dict]:
    """Weighted shuffle: items with higher playCount are pushed toward the end.

    Base weight = 1 / (1 + playCount).  At each step we pick from the remaining
    items using these weights, so unplayed tracks are strongly favoured for
    early positions.  When *era_weights* is supplied (the "party like it's…"
    feature), each track's weight is multiplied by its era weight so videos
    closest to the target year are favoured for the front of the queue.
    """
    remaining = list(tracks)
    result: list[dict] = []
    while remaining:
        weights = []
        for t in remaining:
            w = 1.0 / (1 + t.get("playCount", 0))
            if era_weights is not None:
                w *= era_weights.get(t["videoId"], 1.0)
            weights.append(w)
        # random.choices returns a list; we pick one item per iteration
        chosen = random.choices(remaining, weights=weights, k=1)[0]
        result.append(chosen)
        remaining.remove(chosen)
    return result


@router.get("/", response_model=PaginatedResponse)
def list_videos(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: Optional[str] = None,
    artist: Optional[str] = None,
    album: Optional[str] = None,
    album_entity_id: Optional[int] = Query(None, description="Filter by album entity ID"),
    genre: Optional[str] = None,
    year: Optional[int] = None,
    year_from: Optional[int] = Query(None, description="Filter by year >= this value"),
    year_to: Optional[int] = Query(None, description="Filter by year <= this value"),
    version_type: Optional[str] = Query(None, description="Filter by version type: normal, cover, live, alternate, uncensored"),
    review_status: Optional[str] = Query(None, description="Filter by review status: none, needs_human_review, needs_ai_review, reviewed"),
    enrichment: Optional[str] = Query(None, description="Filter by AI lifecycle: not_requested, queued, running, partial, complete, failed, stale"),
    import_method: Optional[str] = Query(None, description="Filter by import method: url, import, scanned"),
    song_rating: Optional[int] = Query(None, description="Filter by song rating value"),
    video_rating: Optional[int] = Query(None, description="Filter by video rating value"),
    quality: Optional[str] = Query(None, description="Filter by quality bucket: 360p, 480p, 720p, 1080p, 2K, 4K"),
    sort_by: Optional[str] = Query(None, pattern="^(artist|title|album|year|quality|enrichment|created_at|updated_at)$"),
    sort_dir: Optional[str] = Query(None, pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
):
    """List video items with pagination, search, and filters."""
    query = db.query(VideoItem)

    # Filters
    if search:
        term = f"%{search}%"
        query = query.filter(
            or_(
                VideoItem.artist.ilike(term),
                VideoItem.title.ilike(term),
                VideoItem.album.ilike(term),
            )
        )

    if artist:
        query = query.filter(VideoItem.artist.ilike(f"%{artist}%"))

    if album:
        query = query.filter(VideoItem.album.ilike(f"%{album}%"))

    if album_entity_id:
        query = query.filter(VideoItem.album_entity_id == album_entity_id)

    if year:
        query = query.filter(VideoItem.year == year)

    if year_from:
        query = query.filter(VideoItem.year >= year_from)

    if year_to:
        query = query.filter(VideoItem.year <= year_to)

    if genre:
        query = query.join(VideoItem.genres).filter(Genre.name.ilike(f"%{genre}%"))

    if version_type:
        query = query.filter(VideoItem.version_type == version_type)

    if review_status:
        query = query.filter(VideoItem.review_status == review_status)

    if enrichment:
        query = _apply_enrichment_filter(query, enrichment)

    if import_method:
        if import_method == "scanned":
            query = query.filter(VideoItem.import_method == "scanned")
        elif import_method == "url":
            query = query.filter(VideoItem.import_method == "url")
        elif import_method == "import":
            query = query.filter(VideoItem.import_method == "import")

    if song_rating is not None:
        query = query.filter(VideoItem.song_rating == song_rating)

    if video_rating is not None:
        query = query.filter(VideoItem.video_rating == video_rating)

    if quality:
        qr = _quality_bucket_range(quality)
        if qr:
            query = (
                query.join(QualitySignature, QualitySignature.video_id == VideoItem.id)
                .filter(QualitySignature.height >= qr[0], QualitySignature.height <= qr[1])
            )

    # Total count
    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 1

    # Sorting — when the client omits sort, fall back to the saved library
    # preference so fresh browser, TV and Cast clients order alike.
    if sort_by is None or sort_dir is None:
        from app.routers.preferences import get_preference
        _lib = get_preference(db, "library", {}) or {}
        sort_by = sort_by or _lib.get("sort") or "artist"
        sort_dir = sort_dir or _lib.get("dir") or "asc"
    if sort_by not in ("artist", "title", "album", "year", "quality", "enrichment", "created_at", "updated_at"):
        sort_by = "artist"
    if sort_by == "quality":
        if not quality:
            query = query.outerjoin(QualitySignature, QualitySignature.video_id == VideoItem.id)
        sort_col = QualitySignature.height
    elif sort_by == "enrichment":
        sort_col = _enrichment_sort_expression()
    else:
        sort_col = getattr(VideoItem, sort_by, VideoItem.artist)
    if sort_dir == "desc":
        sort_col = sort_col.desc()
    query = query.order_by(sort_col)

    # Pagination
    items = query.options(selectinload(VideoItem.quality_signature)).offset((page - 1) * page_size).limit(page_size).all()

    # Convert to summary models with an explainable enrichment lifecycle.
    summaries = []
    for item in items:
        has_poster = any(a.asset_type == "poster" for a in item.media_assets)
        ps = item.processing_state or {}
        (
            lifecycle, requested_steps, completed_steps, incomplete_steps,
            failed_steps, provider, model, timestamps,
        ) = _enrichment_lifecycle(
            ps, item.review_category,
        )
        summaries.append(VideoItemSummary(
            id=item.id,
            stable_id=item.stable_id,
            artist=item.artist,
            title=item.title,
            album=item.album,
            year=item.year,
            resolution_label=item.resolution_label,
            has_poster=has_poster,
            version_type=item.version_type or "normal",
            review_status=item.review_status or "none",
            enrichment_status=lifecycle,
            enrichment_detail={
                "state": lifecycle,
                "requested_steps": requested_steps,
                "completed_steps": completed_steps,
                "incomplete_steps": incomplete_steps,
                "failed_steps": failed_steps,
                "provider": provider,
                "model": model,
                "last_run_at": max(timestamps) if timestamps else None,
                "stale_reason": item.review_reason if lifecycle == "stale" else None,
            },
            import_method=item.import_method,
            duration_seconds=item.quality_signature.duration_seconds if item.quality_signature else None,
            created_at=item.created_at,
        ))

    return PaginatedResponse(
        items=summaries,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


def _get_blacklisted_genre_names(db: Session) -> set:
    """Return a set of blacklisted genre names."""
    rows = db.query(Genre.name).filter(Genre.blacklisted == True).all()  # noqa: E712
    return {r[0] for r in rows}


def _is_genre_blacklisted(db: Session, genre_name: str) -> bool:
    """Check if a genre is blacklisted."""
    genre = db.query(Genre).filter(Genre.name == genre_name).first()
    return bool(genre and genre.blacklisted)


def _get_genre_display_map(db: Session) -> dict:
    """Return a dict mapping alias genre names → master genre names."""
    from app.services.consolidations import genre_display_map
    return genre_display_map(db)


def _apply_facet_filters(query, *, version_type=None, artist=None,
                         year_from=None, year_to=None,
                         song_rating=None, video_rating=None,
                         genre=None, quality=None, search=None):
    """Apply common browse-page filters to a VideoItem query."""
    if search:
        term = f"%{search}%"
        query = query.filter(
            or_(VideoItem.artist.ilike(term), VideoItem.title.ilike(term), VideoItem.album.ilike(term))
        )
    if version_type:
        query = query.filter(VideoItem.version_type == version_type)
    if artist:
        query = query.filter(VideoItem.artist.ilike(f"%{artist}%"))
    if year_from is not None:
        query = query.filter(VideoItem.year >= year_from)
    if year_to is not None:
        query = query.filter(VideoItem.year <= year_to)
    if song_rating is not None:
        query = query.filter(VideoItem.song_rating == song_rating)
    if video_rating is not None:
        query = query.filter(VideoItem.video_rating == video_rating)
    if genre:
        query = query.join(VideoItem.genres).filter(Genre.name.ilike(f"%{genre}%"))
    if quality:
        qr = _quality_bucket_range(quality)
        if qr:
            query = (
                query.join(QualitySignature, QualitySignature.video_id == VideoItem.id)
                .filter(QualitySignature.height >= qr[0], QualitySignature.height <= qr[1])
            )
    return query


@router.get("/party-mode")
def party_mode(
    search: Optional[str] = None,
    artist: Optional[str] = None,
    album: Optional[str] = None,
    genre: Optional[str] = None,
    year: Optional[int] = None,
    year_from: Optional[int] = Query(None),
    year_to: Optional[int] = Query(None),
    version_type: Optional[str] = None,
    enrichment: Optional[str] = None,
    song_rating: Optional[int] = None,
    video_rating: Optional[int] = None,
    # Exclusion params (comma-separated lists)
    exclude_version_types: Optional[str] = Query(None, description="Comma-separated version types to exclude"),
    exclude_artists: Optional[str] = Query(None, description="Comma-separated artist names to exclude"),
    exclude_genres: Optional[str] = Query(None, description="Comma-separated genre names to exclude"),
    exclude_albums: Optional[str] = Query(None, description="Comma-separated album names to exclude"),
    min_song_rating: Optional[int] = Query(None, description="Minimum song rating (inclusive)"),
    min_video_rating: Optional[int] = Query(None, description="Minimum video rating (inclusive)"),
    party_year: Optional[int] = Query(None, description="\"Party like it's…\" target year: exclude videos newer than this and weight the queue toward this era"),
    playlist_id: Optional[int] = None,
    use_saved_playlist: bool = True,
    db: Session = Depends(get_db),
):
    """Return all matching video IDs shuffled randomly for party mode queue."""
    from app.routers.preferences import get_preference

    # An explicit start-panel playlist wins. Saved defaults are consulted only
    # when the caller allows it; neither path bypasses audience filters.
    _pl_pref = get_preference(db, "partyPlaylist", {}) or {}
    saved_playlist_id = _pl_pref.get("playlistId") if isinstance(_pl_pref, dict) else None
    selected_playlist_id = playlist_id if playlist_id is not None else (saved_playlist_id if use_saved_playlist else None)
    if selected_playlist_id is not None:
        from app.models import PlaylistEntry
        # Retain the established safe fallback: deleting every occurrence from
        # a saved playlist must not turn Party Mode into an unexplained empty
        # screen. Audience filters below still apply to the fallback library.
        has_entries = db.query(PlaylistEntry.id).filter(
            PlaylistEntry.playlist_id == selected_playlist_id,
        ).first()
        if not has_entries:
            selected_playlist_id = None

    query = db.query(VideoItem.id, VideoItem.artist, VideoItem.title, VideoItem.version_type, VideoItem.year, QualitySignature.duration_seconds).outerjoin(QualitySignature, QualitySignature.video_id == VideoItem.id)
    if selected_playlist_id is not None:
        query = (
            query.join(PlaylistEntry, PlaylistEntry.video_id == VideoItem.id)
            .add_columns(PlaylistEntry.occurrence_id)
            .filter(PlaylistEntry.playlist_id == selected_playlist_id)
            .order_by(PlaylistEntry.position)
        )

    # --- Inclusion filters (same as list_videos) ---
    if search:
        term = f"%{search}%"
        query = query.filter(
            or_(VideoItem.artist.ilike(term), VideoItem.title.ilike(term), VideoItem.album.ilike(term))
        )
    if artist:
        query = query.filter(VideoItem.artist.ilike(f"%{artist}%"))
    if album:
        query = query.filter(VideoItem.album.ilike(f"%{album}%"))
    if year:
        query = query.filter(VideoItem.year == year)
    if year_from:
        query = query.filter(VideoItem.year >= year_from)
    if year_to:
        query = query.filter(VideoItem.year <= year_to)
    if genre:
        query = query.join(VideoItem.genres).filter(Genre.name.ilike(f"%{genre}%"))
    if version_type:
        query = query.filter(VideoItem.version_type == version_type)
    if song_rating is not None:
        query = query.filter(VideoItem.song_rating == song_rating)
    if video_rating is not None:
        query = query.filter(VideoItem.video_rating == video_rating)

    # --- Exclusion filters ---
    # When the client omits an exclusion param, fall back to the saved
    # party-mode preference so every supported playback surface inherits
    # the filters configured in the web UI.  An explicit param always wins.
    from app.routers.preferences import get_preference
    _saved = get_preference(db, "partyExclusions", {}) or {}

    # "Party like it's…" — an explicit party_year always wins; otherwise fall
    # back to the saved partyEra preference so TV and Cast inherit it too.
    eff_party_year = party_year
    if eff_party_year is None:
        _era = get_preference(db, "partyEra", {}) or {}
        if _era.get("enabled") and _era.get("year"):
            try:
                eff_party_year = int(_era["year"])
            except (TypeError, ValueError):
                eff_party_year = None

    if eff_party_year is not None:
        # Never play anything past the target year; unknown years stay in the
        # pool (we can't confirm they're newer) but get a low era weight below.
        query = query.filter(or_(VideoItem.year <= eff_party_year, VideoItem.year.is_(None)))

    def _csv(val):
        return [v.strip() for v in val.split(",") if v.strip()]

    ex_version_types = _csv(exclude_version_types) if exclude_version_types else list(_saved.get("version_types") or [])
    if not exclude_version_types and _saved.get("exclude_adult", True) and "18+" not in ex_version_types:
        ex_version_types.append("18+")
    ex_artists = _csv(exclude_artists) if exclude_artists else list(_saved.get("artists") or [])
    ex_genres = _csv(exclude_genres) if exclude_genres else list(_saved.get("genres") or [])
    ex_albums = _csv(exclude_albums) if exclude_albums else list(_saved.get("albums") or [])
    eff_min_song = min_song_rating if min_song_rating is not None else _saved.get("min_song_rating")
    eff_min_video = min_video_rating if min_video_rating is not None else _saved.get("min_video_rating")

    if ex_version_types:
        query = query.filter(~VideoItem.version_type.in_(ex_version_types))
    for a in ex_artists:
        query = query.filter(~VideoItem.artist.ilike(f"%{a}%"))
    if ex_genres:
        from sqlalchemy import select
        excluded_ids = (
            select(video_genres.c.video_id)
            .join(Genre, Genre.id == video_genres.c.genre_id)
            .where(Genre.name.in_(ex_genres))
        )
        query = query.filter(~VideoItem.id.in_(excluded_ids))
    for alb in ex_albums:
        query = query.filter(~VideoItem.album.ilike(f"%{alb}%"))
    if eff_min_song is not None:
        query = query.filter(
            or_(VideoItem.song_rating >= eff_min_song, VideoItem.song_rating.is_(None))
        )
    if eff_min_video is not None:
        query = query.filter(
            or_(VideoItem.video_rating >= eff_min_video, VideoItem.video_rating.is_(None))
        )

    items = query.all()
    # Check which have posters
    poster_ids = set(
        row[0] for row in db.query(
            MediaAsset.video_id
        ).filter(MediaAsset.asset_type == "poster").all()
    )

    # Fetch play counts from playback_history for weighted shuffle
    video_ids = [item.id for item in items]
    play_counts: dict[int, int] = {}
    if video_ids:
        rows = (
            db.query(PlaybackHistory.video_id, func.count(PlaybackHistory.id))
            .filter(PlaybackHistory.video_id.in_(video_ids))
            .group_by(PlaybackHistory.video_id)
            .all()
        )
        play_counts = {vid: cnt for vid, cnt in rows}

    tracks = [
        {
            **({"queueEntryId": item.occurrence_id} if selected_playlist_id is not None else {}),
            "videoId": item.id,
            "artist": item.artist,
            "title": item.title,
            "hasPoster": item.id in poster_ids,
            "playCount": play_counts.get(item.id, 0),
            "duration": item.duration_seconds,
        }
        for item in items
    ]

    # When "party like it's…" is active, bias the queue toward the target era:
    # videos closest to the target year are favoured for the front, with a
    # gradual fall-off the further back they go.
    era_weights = None
    if eff_party_year is not None:
        era_weights = {item.id: _era_weight(item.year, eff_party_year) for item in items}

    # Weighted shuffle: tracks with higher play counts get lower weight
    # (less likely to appear early in the queue).
    # Weight = 1 / (1 + play_count) so unplayed tracks have weight 1.0,
    # a track played 12 times has weight ~0.077.
    tracks = _weighted_shuffle(tracks, era_weights=era_weights)
    return {"tracks": tracks, "total": len(tracks)}


@router.get("/artists", response_model=List[dict])
def list_artists(
    search: Optional[str] = None,
    version_type: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    song_rating: Optional[int] = None,
    video_rating: Optional[int] = None,
    genre: Optional[str] = None,
    quality: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List all unique artists with video count and video IDs.

    Multi-artist entries (semicolon-separated) are split so each individual
    artist gets its own row with the correct aggregate count and video IDs.
    """
    query = (
        db.query(
            VideoItem.id,
            VideoItem.artist,
        )
    )
    query = _apply_facet_filters(query, version_type=version_type,
                                 year_from=year_from, year_to=year_to,
                                 song_rating=song_rating, video_rating=video_rating,
                                 genre=genre, quality=quality, search=search)
    rows = query.all()

    # Split semicolon-separated artists into individual buckets
    from collections import defaultdict
    artist_map: dict[str, set[int]] = defaultdict(set)
    for vid_id, artist_name in rows:
        if not artist_name:
            continue
        parts = [p.strip() for p in artist_name.split(";")]
        for part in parts:
            if part:
                artist_map[part].add(vid_id)

    results = [
        {"artist": name, "count": len(ids), "video_ids": sorted(ids)}
        for name, ids in artist_map.items()
    ]
    results.sort(key=lambda r: r["artist"])
    return results


@router.get("/years", response_model=List[dict])
def list_years(
    search: Optional[str] = None,
    version_type: Optional[str] = None,
    artist: Optional[str] = None,
    song_rating: Optional[int] = None,
    video_rating: Optional[int] = None,
    genre: Optional[str] = None,
    quality: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List all unique years with video count and video IDs."""
    query = (
        db.query(
            VideoItem.year,
            func.count(VideoItem.id),
            func.group_concat(VideoItem.id),
        )
        .filter(VideoItem.year.isnot(None))
    )
    query = _apply_facet_filters(query, version_type=version_type,
                                 artist=artist,
                                 song_rating=song_rating, video_rating=video_rating,
                                 genre=genre, quality=quality, search=search)
    results = query.group_by(VideoItem.year).order_by(VideoItem.year.desc()).all()
    return [
        {"year": r[0], "count": r[1], "video_ids": [int(x) for x in r[2].split(",")] if r[2] else []}
        for r in results
    ]


@router.get("/genres", response_model=List[dict])
def list_genres(
    search: Optional[str] = None,
    version_type: Optional[str] = None,
    artist: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    song_rating: Optional[int] = None,
    video_rating: Optional[int] = None,
    include_blacklisted: bool = False,
    quality: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List all genres with video count and video IDs."""
    base = db.query(VideoItem.id).select_from(VideoItem)
    base = _apply_facet_filters(base, version_type=version_type,
                                artist=artist,
                                year_from=year_from, year_to=year_to,
                                song_rating=song_rating, video_rating=video_rating,
                                quality=quality, search=search)
    filtered_ids = base.subquery()
    query = (
        db.query(
            Genre.name,
            func.count(video_genres.c.video_id),
            func.group_concat(video_genres.c.video_id),
        )
        .join(video_genres, Genre.id == video_genres.c.genre_id)
        .filter(video_genres.c.video_id.in_(db.query(filtered_ids.c.id)))
    )
    if not include_blacklisted:
        query = query.filter(Genre.blacklisted == False)  # noqa: E712
    results = (
        query
        .group_by(Genre.name)
        .order_by(Genre.name)
        .all()
    )
    # Apply genre consolidation: merge alias counts into master genre
    genre_map = _get_genre_display_map(db)
    merged: dict[str, dict] = {}
    for r in results:
        raw_name = r[0]
        display_name = genre_map.get(raw_name, raw_name)
        vid_ids = [int(x) for x in r[2].split(",")] if r[2] else []
        if display_name in merged:
            merged[display_name]["count"] += r[1]
            merged[display_name]["video_ids"].extend(vid_ids)
        else:
            merged[display_name] = {"genre": display_name, "count": r[1], "video_ids": vid_ids}
    # Deduplicate video_ids
    for entry in merged.values():
        entry["video_ids"] = list(set(entry["video_ids"]))
        entry["count"] = len(entry["video_ids"])
    return sorted(merged.values(), key=lambda e: e["genre"])


@router.get("/albums", response_model=List[dict])
def list_albums(
    search: Optional[str] = None,
    version_type: Optional[str] = None,
    artist: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    song_rating: Optional[int] = None,
    video_rating: Optional[int] = None,
    genre: Optional[str] = None,
    quality: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List all unique albums with video count and video IDs.

    Groups by album_entity_id when available (disambiguates same-named
    albums from different artists).  Falls back to album string for
    videos that don't have an entity link.
    """
    # --- entity-linked albums ---
    eq = (
        db.query(
            VideoItem.album,
            VideoItem.album_entity_id,
            ArtistEntity.canonical_name.label("artist_name"),
            func.count(VideoItem.id),
            func.group_concat(VideoItem.id),
        )
        .join(AlbumEntity, VideoItem.album_entity_id == AlbumEntity.id)
        .outerjoin(ArtistEntity, AlbumEntity.artist_id == ArtistEntity.id)
        .filter(VideoItem.album_entity_id.isnot(None))
    )
    eq = _apply_facet_filters(eq, version_type=version_type, artist=artist,
                              year_from=year_from, year_to=year_to,
                              song_rating=song_rating, video_rating=video_rating,
                              genre=genre, quality=quality, search=search)
    entity_rows = eq.group_by(VideoItem.album_entity_id).order_by(VideoItem.album).all()

    results = []
    for r in entity_rows:
        results.append({
            "album": r[0],
            "album_entity_id": r[1],
            "artist": r[2],
            "count": r[3],
            "video_ids": [int(x) for x in r[4].split(",")] if r[4] else [],
        })

    # --- fallback: videos with album string but no entity link ---
    fq = (
        db.query(
            VideoItem.album,
            func.count(VideoItem.id),
            func.group_concat(VideoItem.id),
        )
        .filter(
            VideoItem.album.isnot(None),
            VideoItem.album != "",
            VideoItem.album_entity_id.is_(None),
        )
    )
    fq = _apply_facet_filters(fq, version_type=version_type, artist=artist,
                              year_from=year_from, year_to=year_to,
                              song_rating=song_rating, video_rating=video_rating,
                              genre=genre, quality=quality, search=search)
    fallback_rows = fq.group_by(VideoItem.album).order_by(VideoItem.album).all()

    for r in fallback_rows:
        results.append({
            "album": r[0],
            "album_entity_id": None,
            "artist": None,
            "count": r[1],
            "video_ids": [int(x) for x in r[2].split(",")] if r[2] else [],
        })

    results.sort(key=lambda x: (x["album"] or "").lower())
    return results


@router.get("/song-ratings", response_model=List[dict])
def list_song_ratings(
    search: Optional[str] = None,
    version_type: Optional[str] = None,
    artist: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    genre: Optional[str] = None,
    quality: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List all song ratings with video count and video IDs."""
    query = (
        db.query(
            VideoItem.song_rating,
            func.count(VideoItem.id),
            func.group_concat(VideoItem.id),
        )
        .filter(VideoItem.song_rating.isnot(None))
    )
    query = _apply_facet_filters(query, version_type=version_type,
                                 artist=artist,
                                 year_from=year_from, year_to=year_to,
                                 genre=genre, quality=quality, search=search)
    results = query.group_by(VideoItem.song_rating).order_by(VideoItem.song_rating.desc()).all()
    return [
        {"rating": r[0], "count": r[1], "video_ids": [int(x) for x in r[2].split(",")] if r[2] else []}
        for r in results
    ]


@router.get("/video-ratings", response_model=List[dict])
def list_video_ratings(
    search: Optional[str] = None,
    version_type: Optional[str] = None,
    artist: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    genre: Optional[str] = None,
    quality: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List all video ratings with video count and video IDs."""
    query = (
        db.query(
            VideoItem.video_rating,
            func.count(VideoItem.id),
            func.group_concat(VideoItem.id),
        )
        .filter(VideoItem.video_rating.isnot(None))
    )
    query = _apply_facet_filters(query, version_type=version_type,
                                 artist=artist,
                                 year_from=year_from, year_to=year_to,
                                 genre=genre, quality=quality, search=search)
    results = query.group_by(VideoItem.video_rating).order_by(VideoItem.video_rating.desc()).all()
    return [
        {"rating": r[0], "count": r[1], "video_ids": [int(x) for x in r[2].split(",")] if r[2] else []}
        for r in results
    ]


@router.get("/quality-buckets", response_model=List[dict])
def list_quality_buckets(
    search: Optional[str] = None,
    version_type: Optional[str] = None,
    artist: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    song_rating: Optional[int] = None,
    video_rating: Optional[int] = None,
    genre: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List quality buckets (360p..4K) with video count and IDs."""
    from sqlalchemy import case, literal

    # Build a CASE expression that maps QualitySignature.height -> bucket label
    bucket_expr = case(
        *[(QualitySignature.height <= upper, literal(label)) for upper, label in QUALITY_BUCKETS],
        else_=literal("4K"),
    ).label("bucket")

    query = (
        db.query(
            bucket_expr,
            func.count(VideoItem.id),
            func.group_concat(VideoItem.id),
        )
        .join(QualitySignature, QualitySignature.video_id == VideoItem.id)
        .filter(QualitySignature.height.isnot(None), QualitySignature.height > 0)
    )
    query = _apply_facet_filters(query, version_type=version_type,
                                 artist=artist,
                                 year_from=year_from, year_to=year_to,
                                 song_rating=song_rating, video_rating=video_rating,
                                 genre=genre, search=search)
    results = query.group_by("bucket").all()

    # Sort by the defined bucket order
    order = {label: idx for idx, (_, label) in enumerate(QUALITY_BUCKETS)}
    rows = [
        {
            "quality": r[0],
            "count": r[1],
            "video_ids": [int(x) for x in r[2].split(",")] if r[2] else [],
        }
        for r in results
    ]
    rows.sort(key=lambda b: order.get(b["quality"], 999))
    return rows


# ─── Orphan folder detection & cleanup ────────────────────

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".webm", ".avi", ".mov", ".mpg"}
SKIP_DIRS = {"_artists", "_albums", "archive"}


def _is_under_managed_dirs(file_path: str, all_dirs: list[str]) -> bool:
    """Return True if file_path is under any of the managed library directories."""
    norm_path = os.path.normcase(os.path.normpath(file_path))
    for d in all_dirs:
        norm_dir = os.path.normcase(os.path.normpath(d))
        if norm_path.startswith(norm_dir + os.sep) or norm_path == norm_dir:
            return True
    return False


def _folder_size(path: str) -> int:
    """Return total size in bytes of all files in a folder (non-recursive is fine for flat video dirs)."""
    total = 0
    try:
        for entry in os.scandir(path):
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
    except OSError:
        pass
    return total


def _folder_files(path: str) -> List[str]:
    """Return list of filenames in a folder."""
    try:
        return [e.name for e in os.scandir(path) if e.is_file(follow_symlinks=False)]
    except OSError:
        return []


@router.get("/orphans")
def detect_orphans(db: Session = Depends(get_db)):
    """
    Scan all library directories for folders that have no corresponding
    VideoItem in the database (orphaned on-disk content).
    """
    from app.config import get_settings
    settings = get_settings()
    all_dirs = settings.get_all_library_dirs()

    # Build a set of tracked folder paths (case-insensitive on Windows)
    tracked = set()
    for (fp,) in db.query(VideoItem.folder_path).all():
        if fp:
            tracked.add(os.path.normcase(os.path.normpath(fp)))

    orphans = []
    for library_dir in all_dirs:
        if not os.path.isdir(library_dir):
            continue
        for root, dirs, files in os.walk(library_dir):
            # Skip hidden/internal directories and archive folders
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS
                       and not d.startswith(".") and not d.startswith("_")]
            if os.path.normcase(os.path.normpath(root)) in tracked:
                continue
            video_files = [f for f in files
                           if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS]
            if not video_files:
                continue

            all_files = _folder_files(root)
            orphans.append({
                "folder_path": root,
                "folder_name": os.path.basename(root),
                "size_bytes": _folder_size(root),
                "file_count": len(all_files),
                "has_video": True,
                "files": all_files[:20],
            })

    from app.services.review_cases import sync_orphan_review_cases
    sync_orphan_review_cases(db, orphans)
    db.commit()
    return {"orphans": orphans}


class OrphanCleanRequest(BaseModel):
    folder_paths: List[str]
    mode: str = "delete"  # "delete" or "archive"
    force_permanent: bool = False  # True = confirmed permanent delete for network paths


@router.post("/orphans/clean")
def clean_orphans(body: OrphanCleanRequest, db: Session = Depends(get_db)):
    """
    Remove or archive orphaned folders that are not tracked in the database.
    Only folders inside the library directory and not tracked in DB are allowed.
    """
    from app.config import get_settings
    from app.safe_delete import safe_delete, NetworkDeleteError
    settings = get_settings()
    all_library_dirs = [
        os.path.normcase(os.path.normpath(d))
        for d in settings.get_all_library_dirs()
    ]

    # Build tracked set to prevent accidentally removing tracked videos
    tracked = set()
    for (fp,) in db.query(VideoItem.folder_path).all():
        if fp:
            tracked.add(os.path.normcase(os.path.normpath(fp)))

    results = []
    for fp in body.folder_paths:
        norm = os.path.normcase(os.path.normpath(fp))
        name = os.path.basename(fp)

        # Safety: must be inside a known library dir and not tracked
        if not any(norm.startswith(ld) for ld in all_library_dirs):
            results.append({"folder": fp, "status": "skipped", "reason": "Not inside library directory"})
            continue
        if norm in tracked:
            results.append({"folder": fp, "status": "skipped", "reason": "Folder is tracked in database"})
            continue
        if not os.path.isdir(fp):
            results.append({"folder": fp, "status": "skipped", "reason": "Folder does not exist"})
            continue

        # Determine the _archive dir for the library root containing this folder
        _orphan_archive = settings.archive_dir  # default
        for _lr in settings.get_all_library_dirs():
            _nr = os.path.normcase(os.path.normpath(_lr))
            if norm.startswith(_nr + os.sep):
                _orphan_archive = os.path.join(_lr, "_archive")
                break

        try:
            if body.mode == "archive":
                dest = os.path.join(_orphan_archive, name)
                if os.path.exists(dest):
                    # Add timestamp suffix to avoid collisions
                    dest = f"{dest}_{int(time.time())}"
                os.makedirs(_orphan_archive, exist_ok=True)
                shutil.move(fp, dest)
                results.append({"folder": fp, "status": "archived", "destination": dest})
            else:
                # Send to recycle bin (or permanent delete if force_permanent)
                try:
                    safe_delete(fp, force_permanent=body.force_permanent)
                    results.append({"folder": fp, "status": "deleted"})
                except NetworkDeleteError:
                    results.append({
                        "folder": fp,
                        "status": "network_confirm_required",
                        "reason": "This folder is on a network location where the recycle bin is unavailable. Deletion would be permanent.",
                    })
        except Exception as exc:
            logger.exception(f"Error cleaning orphan folder: {fp}")
            results.append({"folder": fp, "status": "error", "reason": str(exc)})

    return {"results": results}


# ─── Library health: stale entries + orphan files ─────────

@router.get("/health")
def library_health(db: Session = Depends(get_db)):
    """
    Check library health — find DB entries with missing files (stale),
    DB entries whose file is outside all configured library dirs (unmanaged),
    and on-disk folders not tracked in the DB (orphans).
    """
    from app.config import get_settings
    settings = get_settings()
    all_dirs = settings.get_all_library_dirs()

    # --- Stale entries: DB records whose file is missing on disk ---
    # --- Unmanaged entries: file exists but outside all library dirs ---
    stale_items = []
    unmanaged_items = []
    all_videos = db.query(VideoItem).all()
    for v in all_videos:
        if not v.file_path:
            stale_items.append({
                "id": v.id,
                "artist": v.artist,
                "title": v.title,
                "file_path": v.file_path,
                "folder_path": v.folder_path,
            })
        elif not os.path.isfile(v.file_path):
            stale_items.append({
                "id": v.id,
                "artist": v.artist,
                "title": v.title,
                "file_path": v.file_path,
                "folder_path": v.folder_path,
            })
        elif not _is_under_managed_dirs(v.file_path, all_dirs):
            unmanaged_items.append({
                "id": v.id,
                "artist": v.artist,
                "title": v.title,
                "file_path": v.file_path,
                "folder_path": v.folder_path,
            })

    # --- Orphan folders: on disk but not in DB ---
    tracked = set()
    for (fp,) in db.query(VideoItem.folder_path).all():
        if fp:
            tracked.add(os.path.normcase(os.path.normpath(fp)))

    orphan_folders = []
    for library_dir in all_dirs:
        if not os.path.isdir(library_dir):
            continue
        for root, dirs, files in os.walk(library_dir):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS
                       and not d.startswith(".") and not d.startswith("_")]
            if os.path.normcase(os.path.normpath(root)) in tracked:
                continue
            video_files = [f for f in files
                           if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS]
            if not video_files:
                continue

            all_files = _folder_files(root)
            orphan_folders.append({
                "folder_path": root,
                "folder_name": os.path.basename(root),
                "size_bytes": _folder_size(root),
                "file_count": len(all_files),
                "has_video": True,
            })

    # --- Redundant files: tracked folders with extra/mismatched sidecars ---
    redundant_items = _detect_redundant_files(all_videos)

    # --- Stale archives: _archive folders with missing video files ---
    stale_archives = _detect_stale_archives(all_dirs)

    return {
        "stale_count": len(stale_items),
        "stale_items": stale_items,
        "unmanaged_count": len(unmanaged_items),
        "unmanaged_items": unmanaged_items,
        "orphan_count": len(orphan_folders),
        "orphan_folders": orphan_folders,
        "redundant_count": len(redundant_items),
        "redundant_items": redundant_items,
        "stale_archive_count": len(stale_archives),
        "stale_archives": stale_archives,
    }


def _detect_stale_archives(all_dirs: list[str]) -> list[dict]:
    """Find _archive sub-folders whose video file has been deleted/moved."""
    from app.routers.video_editor import _MANIFEST_NAME, _VIDEO_EXTS

    stale: list[dict] = []
    for lib_root in all_dirs:
        archive_dir = os.path.join(lib_root, "_archive")
        if not os.path.isdir(archive_dir):
            continue
        for entry in os.scandir(archive_dir):
            if not entry.is_dir():
                continue
            folder = entry.path
            # Check if the folder still has a video file
            has_video = any(
                os.path.splitext(f.name)[1].lower() in _VIDEO_EXTS
                for f in os.scandir(folder)
                if f.is_file()
            )
            if has_video:
                continue
            # Read manifest for display info
            meta: dict = {}
            manifest_path = os.path.join(folder, _MANIFEST_NAME)
            if os.path.isfile(manifest_path):
                try:
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except (json.JSONDecodeError, OSError):
                    pass
            stale.append({
                "folder": folder,
                "folder_name": entry.name,
                "artist": meta.get("artist", ""),
                "title": meta.get("title", ""),
                "archived_at": meta.get("archived_at", ""),
                "size_bytes": sum(
                    f.stat().st_size for f in os.scandir(folder) if f.is_file()
                ),
            })
    return stale


def _detect_redundant_files(all_videos: list) -> list:
    """Find duplicate/mismatched sidecar files in tracked video folders.

    For each tracked video, the *correct* stem-based files are:
      ``<stem>.playarr.xml``, ``<stem>.nfo``, ``<stem>-poster.jpg``,
      ``<stem>-thumb.jpg``, ``<stem>-album-thumb.jpg``, ``poster.jpg``,
      and thumbnail extracts (``thumb_*.jpg``).

    Anything else with a sidecar-like extension that doesn't match the
    tracked video's stem is flagged as redundant.
    """
    IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}

    redundant_items: list[dict] = []

    # Group videos by folder — only folders with a single tracked video
    # should be checked (multi-video folders are a different concern).
    folder_to_videos: dict[str, list] = {}
    for v in all_videos:
        if v.folder_path and v.file_path and os.path.isdir(v.folder_path):
            key = os.path.normcase(os.path.normpath(v.folder_path))
            folder_to_videos.setdefault(key, []).append(v)

    for _norm_folder, vids in folder_to_videos.items():
        if len(vids) != 1:
            continue
        v = vids[0]
        folder = v.folder_path
        video_stem = os.path.splitext(os.path.basename(v.file_path))[0]
        video_stem_lower = video_stem.lower()

        # Build set of "expected" filenames (case-insensitive on Windows)
        expected_lower = {
            os.path.basename(v.file_path).lower(),           # the video itself
            f"{video_stem_lower}.playarr.xml",
            f"{video_stem_lower}.nfo",
            f"{video_stem_lower}-poster.jpg",
            f"{video_stem_lower}-thumb.jpg",
            f"{video_stem_lower}-album-thumb.jpg",
            "poster.jpg",
        }

        extra_files: list[dict] = []
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue

        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                continue
            name = entry.name
            name_lower = name.lower()

            # Always keep the video file, generic poster, and thumb extracts
            if name_lower in expected_lower:
                continue
            if name_lower.startswith("thumb_") and name_lower.endswith(".jpg"):
                continue

            # Detect sidecar-like files that don't match the video stem
            reason = None
            if name_lower.endswith(".playarr.xml"):
                reason = "Mismatched XML sidecar"
            elif name_lower.endswith(".nfo"):
                reason = "Mismatched NFO file"
            elif name_lower.endswith("-poster.jpg") or name_lower.endswith("-poster.jpeg"):
                reason = "Mismatched poster"
            elif name_lower.endswith("-thumb.jpg") or name_lower.endswith("-thumb.jpeg"):
                reason = "Mismatched thumbnail"
            elif name_lower.endswith("-album-thumb.jpg") or name_lower.endswith("-album-thumb.jpeg"):
                reason = "Mismatched album thumbnail"
            elif name_lower.endswith(".srt") or name_lower.endswith(".vtt"):
                # Subtitle files that don't match — leave them alone
                pass

            if reason:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                extra_files.append({
                    "file_name": name,
                    "file_path": entry.path,
                    "reason": reason,
                    "size_bytes": size,
                })

        if extra_files:
            redundant_items.append({
                "video_id": v.id,
                "artist": v.artist,
                "title": v.title,
                "folder_path": folder,
                "video_stem": video_stem,
                "files": extra_files,
                "total_size_bytes": sum(f["size_bytes"] for f in extra_files),
            })

    return redundant_items


class CleanStaleRequest(BaseModel):
    video_ids: List[int]


@router.post("/clean-stale")
def clean_stale_entries(body: CleanStaleRequest, db: Session = Depends(get_db)):
    """
    Remove library entries whose files no longer exist on disk or are
    outside all configured library directories.
    """
    from app.config import get_settings
    all_dirs = get_settings().get_all_library_dirs()

    results = []
    for vid in body.video_ids:
        item = db.get(VideoItem, vid)
        if not item:
            results.append({"id": vid, "status": "skipped", "reason": "Not found"})
            continue
        # Allow removal if file is missing OR outside all managed dirs
        file_exists = item.file_path and os.path.isfile(item.file_path)
        file_managed = item.file_path and _is_under_managed_dirs(item.file_path, all_dirs)
        if file_exists and file_managed:
            results.append({"id": vid, "status": "skipped", "reason": "File still exists in library"})
            continue

        try:
            _cleanup_orphaned_child_rows(db, [vid])
            _delete_video_cached_assets(db, [vid])

            artist_names = {item.artist} if item.artist else set()
            album_keys = {(item.artist, item.album)} if item.artist and item.album else set()
            artist_entity_ids = {item.artist_entity_id} if item.artist_entity_id else set()
            album_entity_ids = {item.album_entity_id} if item.album_entity_id else set()
            track_ids = {item.track_id} if item.track_id else set()

            db.delete(item)
            db.flush()

            _cleanup_orphaned_entity_folders(
                db, artist_names, album_keys, artist_entity_ids, album_entity_ids, track_ids,
            )

            results.append({"id": vid, "status": "removed"})
        except Exception as e:
            logger.error(f"Failed to clean video {vid}: {e}", exc_info=True)
            db.rollback()
            results.append({"id": vid, "status": "error", "reason": str(e)})

    db.commit()
    return {"results": results, "removed": sum(1 for r in results if r["status"] == "removed")}


class CleanRedundantRequest(BaseModel):
    file_paths: List[str]


@router.post("/clean-redundant")
def clean_redundant_files(body: CleanRedundantRequest, db: Session = Depends(get_db)):
    """Delete redundant/mismatched sidecar files from tracked library folders.

    Safety: each file must be inside a tracked video folder and must NOT be
    the tracked video file itself.
    """
    from app.config import get_settings
    all_library_dirs = [
        os.path.normcase(os.path.normpath(d))
        for d in get_settings().get_all_library_dirs()
    ]

    # Build tracked set: folder_path -> file_path
    tracked_folders: dict[str, str] = {}
    for v in db.query(VideoItem).all():
        if v.folder_path and v.file_path:
            tracked_folders[os.path.normcase(os.path.normpath(v.folder_path))] = \
                os.path.normcase(os.path.normpath(v.file_path))

    results = []
    for fp in body.file_paths:
        norm = os.path.normcase(os.path.normpath(fp))
        parent = os.path.normcase(os.path.normpath(os.path.dirname(fp)))

        # Safety: must be inside a library dir
        if not any(norm.startswith(ld + os.sep) or norm.startswith(ld) for ld in all_library_dirs):
            results.append({"file": fp, "status": "skipped", "reason": "Not inside library directory"})
            continue
        # Must be inside a tracked folder
        if parent not in tracked_folders:
            results.append({"file": fp, "status": "skipped", "reason": "Parent folder not tracked"})
            continue
        # Must NOT be the video file itself
        if norm == tracked_folders[parent]:
            results.append({"file": fp, "status": "skipped", "reason": "Cannot delete tracked video file"})
            continue
        if not os.path.isfile(fp):
            results.append({"file": fp, "status": "skipped", "reason": "File does not exist"})
            continue

        try:
            from app.safe_delete import safe_delete, NetworkDeleteError
            try:
                safe_delete(fp)
            except NetworkDeleteError:
                safe_delete(fp, force_permanent=True)
            # Also remove any MediaAsset records pointing at this file
            db.query(MediaAsset).filter(
                MediaAsset.file_path == fp,
            ).delete(synchronize_session="fetch")
            results.append({"file": fp, "status": "deleted"})
        except Exception as exc:
            logger.warning(f"Failed to delete redundant file {fp}: {exc}")
            results.append({"file": fp, "status": "error", "reason": str(exc)})

    db.commit()
    return {
        "results": results,
        "deleted": sum(1 for r in results if r["status"] == "deleted"),
    }


@router.get("/{video_id}/nav")
def get_video_nav(
    video_id: int,
    sort_by: str = Query("artist", pattern="^(artist|title|year|created_at|updated_at)$"),
    sort_dir: str = Query("asc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
):
    """Get prev/next/random navigation IDs for a video, respecting sort order."""
    import random as _random

    current = db.query(VideoItem).filter(VideoItem.id == video_id).first()
    if not current:
        raise HTTPException(status_code=404, detail="Video not found")

    col = getattr(VideoItem, sort_by, VideoItem.artist)
    cur_val = getattr(current, sort_by, None)
    is_desc = sort_dir == "desc"

    # Build prev/next filters using (sort_col, id) as composite key.
    # "prev" = the item that appears just before `current` in the sorted list.
    # "next" = the item that appears just after `current`.
    if is_desc:
        # Descending: prev has a HIGHER sort value (or same value + higher id)
        prev_filter = or_(
            col > cur_val,
            (col == cur_val) & (VideoItem.id > current.id),
        )
        prev_order = [col.asc(), VideoItem.id.asc()]   # closest higher value = asc then pick first
        next_filter = or_(
            col < cur_val,
            (col == cur_val) & (VideoItem.id < current.id),
        )
        next_order = [col.desc(), VideoItem.id.desc()]  # closest lower value = desc then pick first
    else:
        # Ascending: prev has a LOWER sort value (or same value + lower id)
        prev_filter = or_(
            col < cur_val,
            (col == cur_val) & (VideoItem.id < current.id),
        )
        prev_order = [col.desc(), VideoItem.id.desc()]
        next_filter = or_(
            col > cur_val,
            (col == cur_val) & (VideoItem.id > current.id),
        )
        next_order = [col.asc(), VideoItem.id.asc()]

    prev_item = (
        db.query(VideoItem.id)
        .filter(prev_filter)
        .order_by(*prev_order)
        .first()
    )
    next_item = (
        db.query(VideoItem.id)
        .filter(next_filter)
        .order_by(*next_order)
        .first()
    )

    # Random: pick any other video
    total = db.query(func.count(VideoItem.id)).scalar() or 0
    random_id = None
    if total > 1:
        offset = _random.randint(0, total - 2)
        rand_item = db.query(VideoItem.id).filter(
            VideoItem.id != video_id
        ).offset(offset).limit(1).first()
        random_id = rand_item[0] if rand_item else None

    return {
        "prev_id": prev_item[0] if prev_item else None,
        "next_id": next_item[0] if next_item else None,
        "random_id": random_id,
    }


@router.get("/{video_id}", response_model=VideoItemOut)
def get_video(video_id: int, db: Session = Depends(get_db)):
    """Get full details of a video item, including canonical track metadata."""
    item = (
        db.query(VideoItem)
        .options(
            joinedload(VideoItem.sources),
            joinedload(VideoItem.quality_signature),
            joinedload(VideoItem.genres),
            joinedload(VideoItem.media_assets),
            joinedload(VideoItem.track_entity).joinedload(TrackEntity.artist),
            joinedload(VideoItem.track_entity).joinedload(TrackEntity.album),
            joinedload(VideoItem.track_entity).selectinload(TrackEntity.genres),
            joinedload(VideoItem.track_entity).selectinload(TrackEntity.videos),
        )
        .filter(VideoItem.id == video_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Video not found")

    # Build response with canonical track data
    response = VideoItemOut.model_validate(item)
    # Filter out blacklisted genres and apply genre consolidation
    genre_map = _get_genre_display_map(db)
    filtered_genres = []
    seen_names = set()
    for g in response.genres:
        if _is_genre_blacklisted(db, g.name):
            continue
        display_name = genre_map.get(g.name, g.name)
        if display_name not in seen_names:
            seen_names.add(display_name)
            g.name = display_name
            filtered_genres.append(g)
    response.genres = filtered_genres
    response.canonical_track_id = item.track_id
    response.processing_state = item.processing_state
    response.exclude_from_editor_scan = item.exclude_from_editor_scan

    # Check if archived original exists (extension-agnostic)
    from app.config import get_settings as _get_settings
    from app.routers.video_editor import find_archive_file
    _cfg = _get_settings()
    if item.file_path:
        response.has_archive = find_archive_file(
            item.file_path, _cfg.library_dir, _cfg.archive_dir,
            video_id=item.id,
            playarr_video_id=item.playarr_video_id,
            expected_artist=item.artist,
            expected_title=item.title,
        ) is not None

    if item.track_entity:
        track = item.track_entity
        blacklisted_names = _get_blacklisted_genre_names(db)
        response.canonical_track = CanonicalTrackOut(
            id=track.id,
            title=track.title,
            artist_id=track.artist_id,
            artist_name=track.artist.canonical_name if track.artist else None,
            album=track.album.title if track.album else None,
            album_id=track.album_id,
            year=track.year,
            genres=[
                {"id": g.id, "name": genre_map.get(g.name, g.name)}
                for g in track.genres
                if g.name not in blacklisted_names
            ],
            mb_recording_id=track.mb_recording_id,
            mb_release_id=track.mb_release_id,
            mb_artist_id=track.mb_artist_id,
            artwork_album=track.artwork_album,
            artwork_single=track.artwork_single,
            canonical_verified=track.canonical_verified,
            metadata_source=track.metadata_source,
            ai_verified=track.ai_verified,
            ai_verified_at=track.ai_verified_at,
            is_cover=track.is_cover,
            original_artist=track.original_artist,
            original_title=track.original_title,
            video_count=len(track.videos) if track.videos else 0,
            linked_videos=[
                {"id": v.id, "artist": v.artist, "title": v.title,
                 "resolution_label": v.resolution_label, "version_type": v.version_type}
                for v in (track.videos or []) if v.id != item.id
            ],
            created_at=track.created_at,
            updated_at=track.updated_at,
        )

    return response


@router.put("/{video_id}", response_model=VideoItemOut)
def update_video(video_id: int, update: VideoItemUpdate, db: Session = Depends(get_db)):
    """Manually update metadata for a video item."""
    item = db.query(VideoItem).get(video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Video not found")
    from app.services.optimistic_revision import require_expected_revision
    require_expected_revision(item, update.expected_revision, current_state=lambda: get_video(video_id, db))
    from app.tasks import _save_metadata_snapshot, _get_or_create_genre
    from app.user_identity import get_instance_user_id
    _uid = get_instance_user_id(db)
    _save_metadata_snapshot(db, item, "manual_edit", user_id=_uid)
    from app.services.provenance_events import capture_manual_values, record_manual_changes
    _prior_values = capture_manual_values(item)

    # Capture old entity IDs before changes — orphan cleanup after commit
    _old_artist_entity_id = item.artist_entity_id
    _old_album_entity_id = item.album_entity_id
    _old_track_id = item.track_id

    # Track which identity fields changed — drives entity unlinking,
    # file rename, NFO/XML rewrite decisions below.
    _artist_changed = update.artist is not None and update.artist != item.artist
    _title_changed = update.title is not None and update.title != item.title
    _album_changed = update.album is not None and update.album != item.album
    _identity_changed = _artist_changed or _title_changed or _album_changed
    _metadata_changed = False  # year, plot, genres — need NFO/XML rewrite

    if update.artist is not None:
        item.artist = update.artist
    if _artist_changed:
        # Artist changed: all entity links are invalid
        item.artist_entity_id = None
        item.album_entity_id = None
        item.track_id = None
    if update.title is not None:
        item.title = update.title
    if _title_changed and not _artist_changed:
        # Title changed: track entity is keyed by artist+title, so unlink it
        item.track_id = None
    if update.album is not None:
        item.album = update.album
    if _album_changed and not _artist_changed:
        # Album changed: album entity is keyed by artist+album, so unlink it
        item.album_entity_id = None
        # Track's album_id is also stale
        item.track_id = None
    if update.year is not None and update.year != item.year:
        item.year = update.year
        _metadata_changed = True
    elif update.year is not None:
        item.year = update.year
    if update.plot is not None and update.plot != item.plot:
        item.plot = update.plot
        _metadata_changed = True
    elif update.plot is not None:
        item.plot = update.plot
    if update.locked_fields is not None:
        item.locked_fields = update.locked_fields
    if update.version_type is not None:
        item.version_type = update.version_type
        # If manually setting version_type, clear any "version" review flag
        if item.review_status == "flagged" and item.review_category == "version":
            item.review_status = "reviewed"
            item.review_reason = None
            item.review_category = None
    if update.alternate_version_label is not None:
        item.alternate_version_label = update.alternate_version_label
    if update.original_artist is not None:
        item.original_artist = update.original_artist
    if update.original_title is not None:
        item.original_title = update.original_title
    if update.review_status is not None:
        item.review_status = update.review_status
    from datetime import datetime as _dt, timezone as _tz
    _now = _dt.now(_tz.utc)
    if update.song_rating is not None:
        item.song_rating = update.song_rating
        item.song_rating_set = True if update.song_rating_set is None else update.song_rating_set
        item.song_rating_by = _uid
        item.song_rating_at = _now
    if update.video_rating is not None:
        item.video_rating = update.video_rating
        item.video_rating_set = True if update.video_rating_set is None else update.video_rating_set
        item.video_rating_by = _uid
        item.video_rating_at = _now

    # MusicBrainz IDs
    _id_fields_changed = []
    if update.mb_artist_id is not None:
        item.mb_artist_id = update.mb_artist_id or None
        _metadata_changed = True
        _id_fields_changed.append("mb_artist_id")
    if update.mb_recording_id is not None:
        item.mb_recording_id = update.mb_recording_id or None
        _metadata_changed = True
        _id_fields_changed.append("mb_recording_id")
    if update.mb_release_id is not None:
        item.mb_release_id = update.mb_release_id or None
        _metadata_changed = True
        _id_fields_changed.append("mb_release_id")
    if update.mb_release_group_id is not None:
        item.mb_release_group_id = update.mb_release_group_id or None
        _metadata_changed = True
        _id_fields_changed.append("mb_release_group_id")
    if update.mb_track_id is not None:
        item.mb_track_id = update.mb_track_id or None
        _metadata_changed = True
        _id_fields_changed.append("mb_track_id")
    if update.artist_ids is not None:
        item.artist_ids = update.artist_ids or None
        _metadata_changed = True
        _id_fields_changed.append("artist_ids")

    # Playarr Content IDs
    if update.playarr_video_id is not None:
        item.playarr_video_id = update.playarr_video_id or None
        _metadata_changed = True
        _id_fields_changed.append("playarr_video_id")
    if update.playarr_track_id is not None:
        item.playarr_track_id = update.playarr_track_id or None
        _metadata_changed = True
        _id_fields_changed.append("playarr_track_id")

    if update.genres is not None:
        item.genres.clear()
        for g in update.genres:
            item.genres.append(_get_or_create_genre(db, g))
        _metadata_changed = True

    # Update field_provenance for any changed metadata fields
    _manually_changed = []
    if _artist_changed:
        _manually_changed.append("artist")
    if _title_changed:
        _manually_changed.append("title")
    if _album_changed:
        _manually_changed.append("album")
    if update.year is not None:
        _manually_changed.append("year")
    if update.plot is not None:
        _manually_changed.append("plot")
    if update.genres is not None:
        _manually_changed.append("genres")
    _manually_changed.extend(_id_fields_changed)
    if _manually_changed:
        from sqlalchemy.orm.attributes import flag_modified as _fp_flag
        fp = dict(item.field_provenance or {})
        for f in _manually_changed:
            fp[f] = "manual"
        item.field_provenance = fp
        _fp_flag(item, "field_provenance")

        # Track which user set each field
        fpu = dict(item.field_provenance_users or {})
        for f in _manually_changed:
            fpu[f] = _uid
        item.field_provenance_users = fpu
        _fp_flag(item, "field_provenance_users")

        # Track when each field was last set
        fpa = dict(item.field_provenance_at or {})
        _now_iso = _now.isoformat()
        for f in _manually_changed:
            fpa[f] = _now_iso
        item.field_provenance_at = fpa
        _fp_flag(item, "field_provenance_at")

        # A manual edit supersedes any prior verification of those fields
        if item.field_verifications:
            fv = dict(item.field_verifications)
            for f in _manually_changed:
                fv.pop(f, None)
            item.field_verifications = fv
            _fp_flag(item, "field_verifications")

        item.last_edited_by = _uid
        record_manual_changes(db, item, _manually_changed, _prior_values, _uid)
    _needs_rename = (
        (_artist_changed or _title_changed or update.version_type is not None)
        and item.file_path
    )
    item.revision += 1
    from app.services.sidecar_outbox import schedule_sidecar_write
    schedule_sidecar_write(db, item)
    _rename_queued = False
    if _needs_rename:
        try:
            from app.services.rename_operations import enqueue_expected_rename
            enqueue_expected_rename(db, item, actor_id=_uid)
            _rename_queued = True
        except ValueError:
            _needs_rename = False
        except FileNotFoundError as exc:
            logger.warning("Could not queue auto-rename for video %s: %s", video_id, exc)
            _needs_rename = False
    db.commit()
    db.refresh(item)
    if _rename_queued:
        from app.services.rename_operations import notify_expected_rename
        notify_expected_rename()

    # Rewrite the Kodi NFO immediately when no filesystem rename was queued.
    # The Playarr sidecar is always materialised by the transactional outbox.
    if not _needs_rename and (_identity_changed or _metadata_changed):
        if item.folder_path and os.path.isdir(item.folder_path):
            # Rewrite NFO
            try:
                from app.services.file_organizer import write_nfo_file
                source_url = item.sources[0].original_url if item.sources else ""
                genre_names = [g.name for g in item.genres] if item.genres else []
                write_nfo_file(
                    folder_path=item.folder_path,
                    artist=item.artist,
                    title=item.title,
                    album=item.album or "",
                    year=item.year,
                    genres=genre_names,
                    plot=item.plot or "",
                    source_url=source_url,
                    resolution_label=item.resolution_label or "",
                    version_type=item.version_type or "normal",
                    alternate_version_label=item.alternate_version_label or "",
                    original_artist=item.original_artist or "",
                    original_title=item.original_title or "",
                )
            except Exception as e:
                logger.warning(f"NFO rewrite after manual edit failed for video {video_id}: {e}")

            # Playarr XML is materialised by the transactional sidecar outbox.

    # Clean up orphaned entities/folders from old entity links
    if _old_artist_entity_id and _old_artist_entity_id != item.artist_entity_id:
        cleanup_orphaned_entity(db, "artist", _old_artist_entity_id)
    if _old_album_entity_id and _old_album_entity_id != item.album_entity_id:
        cleanup_orphaned_entity(db, "album", _old_album_entity_id)
    if _old_track_id and _old_track_id != item.track_id:
        cleanup_orphaned_entity(db, "track", _old_track_id)
    db.commit()

    # Re-resolve entity links when they are missing (identity changed, or
    # were already empty before the edit).
    _needs_entity_resolve = (
        item.artist_entity_id is None or item.track_id is None
    )
    if _needs_entity_resolve and item.artist and item.title:
        try:
            from app.metadata.resolver import (
                get_or_create_artist, get_or_create_album, get_or_create_track,
            )

            with db.begin_nested():
                artist_entity = get_or_create_artist(db, item.artist, None)
                item.artist_entity_id = artist_entity.id

                album_entity = None
                if item.album:
                    album_entity = get_or_create_album(
                        db, artist_entity, item.album, None,
                    )
                    item.album_entity_id = album_entity.id
                else:
                    item.album_entity_id = None

                track_entity = get_or_create_track(
                    db, artist_entity, album_entity, item.title, None,
                )
                item.track_id = track_entity.id

            db.commit()
            db.refresh(item)
            logger.info(f"Re-resolved entities after manual edit for video {video_id}: "
                        f"artist_entity={item.artist_entity_id}, album_entity={item.album_entity_id}, "
                        f"track={item.track_id}")
        except Exception as e:
            logger.warning(f"Entity re-resolution after manual edit failed for video {video_id}: {e}")

    # Return the same rich response as GET /{video_id}
    return get_video(video_id, db)


@router.post("/{video_id}/confirm-fields")
def confirm_fields(video_id: int, body: dict = Body(default={}), db: Session = Depends(get_db)):
    """Mark fields as human-verified (confirming auto values without editing).

    Body: {"fields": ["artist", "title", ...]} — omit to confirm all core fields.
    This records the strong "human_verified" trust signal for contributions.
    """
    item = db.query(VideoItem).get(video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Video not found")

    from app.provenance import mark_fields_verified
    from app.user_identity import get_instance_user_id
    from sqlalchemy.orm.attributes import flag_modified

    fields = body.get("fields") if isinstance(body, dict) else None
    marked = mark_fields_verified(item, get_instance_user_id(db), fields=fields)
    if marked:
        flag_modified(item, "field_verifications")
        db.commit()
    return {"verified": marked}


@router.get("/{video_id}/snapshots", response_model=List[MetadataSnapshotOut])
def get_snapshots(video_id: int, db: Session = Depends(get_db)):
    """Get metadata snapshots (history) for a video item."""
    from app.models import MetadataSnapshot
    snapshots = (
        db.query(MetadataSnapshot)
        .filter(MetadataSnapshot.video_id == video_id)
        .order_by(MetadataSnapshot.created_at.desc())
        .all()
    )
    return snapshots


@router.post("/{video_id}/undo-rescan", response_model=VideoItemOut)
def undo_rescan(video_id: int, db: Session = Depends(get_db)):
    """Undo the last metadata rescan by restoring the previous snapshot."""
    from app.models import MetadataSnapshot
    item = db.query(VideoItem).get(video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Video not found")

    # Find the second-most-recent snapshot (the state before the last rescan)
    snapshots = (
        db.query(MetadataSnapshot)
        .filter(MetadataSnapshot.video_id == video_id)
        .order_by(MetadataSnapshot.created_at.desc())
        .limit(2)
        .all()
    )

    if len(snapshots) < 2:
        raise HTTPException(status_code=400, detail="No previous state to restore")

    # Restore from the second snapshot
    restore = snapshots[1].snapshot_data
    item.artist = restore.get("artist", item.artist)
    item.title = restore.get("title", item.title)
    item.album = restore.get("album", item.album)
    item.year = restore.get("year", item.year)
    item.plot = restore.get("plot", item.plot)
    item.mb_artist_id = restore.get("mb_artist_id")
    item.mb_recording_id = restore.get("mb_recording_id")
    item.mb_release_id = restore.get("mb_release_id")

    # Restore genres
    if "genres" in restore:
        from app.tasks import _get_or_create_genre
        item.genres.clear()
        for g in restore["genres"]:
            item.genres.append(_get_or_create_genre(db, g))

    db.commit()
    db.refresh(item)
    return item


def _robust_rmtree(folder_path: str):
    """
    Remove a directory tree, handling OneDrive-backed paths that resist rmdir.

    OneDrive's filesystem filter driver holds locks on directories during sync.
    Python's os.rmdir() gets ``WinError 5: Access is denied`` on these empty
    directories.  The Windows ``rd /s /q`` command bypasses this restriction.

    Strategy:
    1. shutil.rmtree — works for normal paths and removes file contents
    2. os.rmdir with retries — picks up empty dirs after OneDrive releases locks
    3. ``rd /s /q`` subprocess — the nuclear option that handles OneDrive stubs
    4. Deferred background thread — last resort for extremely stubborn dirs
    """
    if not folder_path or not os.path.isdir(folder_path):
        return

    # Phase 1: delete contents with shutil
    for attempt in range(3):
        try:
            shutil.rmtree(folder_path)
        except Exception:
            time.sleep(0.5 * (attempt + 1))
        if not os.path.isdir(folder_path):
            return

    # Phase 2: clear any remaining children manually
    if os.path.isdir(folder_path):
        try:
            for entry in os.scandir(folder_path):
                if entry.is_dir():
                    shutil.rmtree(entry.path, ignore_errors=True)
                else:
                    try:
                        os.remove(entry.path)
                    except Exception:
                        pass
        except Exception:
            pass

    # Phase 3: try os.rmdir (works if OneDrive has released the lock)
    for delay in (0.5, 1, 2):
        if not os.path.isdir(folder_path):
            return
        try:
            os.rmdir(folder_path)
            return
        except OSError:
            time.sleep(delay)

    # Phase 4: use Windows rd /s /q — bypasses OneDrive filesystem filter locks
    if os.path.isdir(folder_path) and os.name == "nt":
        try:
            import subprocess
            subprocess.run(
                ["cmd", "/c", "rd", "/s", "/q", folder_path],
                capture_output=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as e:
            logger.debug(f"rd /s /q failed for {folder_path}: {e}")

    if not os.path.isdir(folder_path):
        return

    # Phase 5: schedule deferred cleanup for truly stuck directories
    logger.warning(f"Folder still exists after all removal attempts, scheduling deferred removal: {folder_path}")
    _schedule_deferred_rmdir(folder_path)


def _schedule_deferred_rmdir(folder_path: str):
    """
    Schedule a background thread to retry removing a stubborn empty directory.

    OneDrive can hold locks for 30+ seconds during sync.  Rather than blocking
    the HTTP request, we hand off to a daemon thread that retries periodically.
    """
    import threading

    def _deferred():
        delays = [5, 10, 15, 30, 60]
        for delay in delays:
            time.sleep(delay)
            if not os.path.isdir(folder_path):
                return
            # Try rd /s /q first on Windows (handles OneDrive locks)
            if os.name == "nt":
                try:
                    import subprocess
                    subprocess.run(
                        ["cmd", "/c", "rd", "/s", "/q", folder_path],
                        capture_output=True, timeout=15,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    if not os.path.isdir(folder_path):
                        logger.info(f"Deferred removal succeeded (rd /s /q): {folder_path}")
                        return
                except Exception:
                    pass
            # Fallback: clear children then rmdir
            try:
                for entry in os.scandir(folder_path):
                    if entry.is_dir():
                        shutil.rmtree(entry.path, ignore_errors=True)
                    else:
                        try:
                            os.remove(entry.path)
                        except Exception:
                            pass
            except Exception:
                pass
            try:
                os.rmdir(folder_path)
                logger.info(f"Deferred removal succeeded: {folder_path}")
                return
            except Exception:
                pass
        if os.path.isdir(folder_path):
            logger.error(f"Deferred removal FAILED (gave up): {folder_path}")

    t = threading.Thread(target=_deferred, daemon=True, name=f"rmdir-{os.path.basename(folder_path)}")
    t.start()


def _delete_video_previews(video_id: int, file_basename: str = None):
    """Delete all preview files for a specific video from the preview cache."""
    try:
        from app.services.preview_generator import delete_video_previews
        delete_video_previews(video_id, basename=file_basename)
    except Exception as e:
        logger.warning(f"Failed to delete preview files for video {video_id}: {e}")


def _delete_video_thumbnail_dir(video_id: int):
    """Delete the thumbnail cache directory for a video from disk."""
    try:
        from app.config import get_settings
        thumb_dir = os.path.join(get_settings().asset_cache_dir, "thumbnails", str(video_id))
        if os.path.isdir(thumb_dir):
            shutil.rmtree(thumb_dir, ignore_errors=True)
            logger.info(f"Deleted thumbnail directory: {thumb_dir}")
    except Exception as e:
        logger.warning(f"Failed to delete thumbnail dir for video {video_id}: {e}")


def _delete_video_cached_assets(db: Session, video_ids: List[int]):
    """Delete CachedAsset records + files for video entities being removed."""
    try:
        from app.services.artwork_service import delete_entity_cached_assets
        for vid in video_ids:
            delete_entity_cached_assets("video", vid, db)
    except Exception as e:
        logger.warning(f"Failed to delete cached assets for videos {video_ids}: {e}")


def _clear_orphaned_duplicate_partners(db: Session, deleted_ids: List[int]):
    """After deleting videos, clear duplicate review flags on surviving partners.

    If a surviving partner has no remaining undismissed duplicates,
    its review flags are cleared entirely.  Otherwise the deleted IDs
    are simply removed from its review_reason text.
    """
    import re
    deleted_set = set(deleted_ids)
    # Find all videos still flagged as duplicates that reference any deleted ID
    candidates = (
        db.query(VideoItem)
        .filter(
            VideoItem.review_category == "duplicate",
            VideoItem.review_status == "needs_human_review",
        )
        .all()
    )
    for vi in candidates:
        id_match = re.search(r'ID\(s\):\s*([\d,\s]+)', vi.review_reason or "")
        if not id_match:
            continue
        partner_ids = {
            int(x.strip())
            for x in id_match.group(1).split(",")
            if x.strip().isdigit()
        }
        if not partner_ids & deleted_set:
            continue  # this item doesn't reference any deleted video
        remaining = partner_ids - deleted_set
        # Filter to only IDs that still exist in DB
        if remaining:
            existing = {
                row[0]
                for row in db.query(VideoItem.id).filter(VideoItem.id.in_(remaining)).all()
            }
            remaining = existing
        if remaining:
            # Still has other duplicate partners — update the reason text
            vi.review_reason = f"Potential duplicate of video ID(s): {', '.join(str(i) for i in sorted(remaining))}"
        else:
            # No remaining partners — clear review flags
            vi.review_status = "none"
            vi.review_category = None
            vi.review_reason = None


def _cleanup_orphaned_child_rows(db: Session, video_ids: List[int]):
    """
    Delete orphaned rows from tables that reference video_items but lack
    SQLAlchemy-level cascade relationships.

    SQLite ignores ondelete="CASCADE" unless PRAGMA foreign_keys = ON is set
    on every connection.  Even with that enabled, we explicitly clean up here
    for defense-in-depth (the pragma might be lost on a pooled connection).
    """
    from sqlalchemy import text, bindparam

    # Tables with video_id FK to video_items that have no ORM cascade on VideoItem
    orphan_tables = [
        "ai_metadata_results",
        "ai_scene_analyses",
        "ai_thumbnails",
        "match_results",
        "match_candidates",   # FK to match_results, but also has video references
        "normalization_results",
        "user_pinned_matches",
    ]

    for table in orphan_tables:
        try:
            db.execute(
                text(f"DELETE FROM {table} WHERE video_id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": list(video_ids)},
            )
        except Exception as e:
            # Table might not exist on fresh installs — non-fatal
            logger.debug(f"Orphan cleanup for {table}: {e}")

    # processing_jobs uses ondelete="SET NULL", so rows survive but with
    # video_id=NULL.  Clean up completed/failed jobs that are now orphaned.
    try:
        db.execute(
            text(
                "DELETE FROM processing_jobs "
                "WHERE video_id IN :ids "
                "OR (video_id IS NULL AND status IN ('complete', 'failed', 'cancelled'))"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": list(video_ids)},
        )
    except Exception as e:
        logger.debug(f"Orphan cleanup for processing_jobs: {e}")

    db.flush()


def cleanup_orphaned_entity(
    db: Session,
    entity_type: str,
    entity_id: int,
):
    """
    If no remaining videos reference the given entity, remove its
    _artists/_albums folder, cached assets, export manifest, and DB row.

    entity_type: "artist", "album", or "track"
    entity_id:   The PK of the entity to check.

    Safe to call speculatively — it checks for remaining references first.
    """
    if not entity_id:
        return

    from app.services.artwork_manager import get_artists_dir, get_albums_dir, _safe_name
    from app.services.artwork_service import delete_entity_cached_assets
    from app.metadata.models import ExportManifest

    if entity_type == "artist":
        remaining = db.query(VideoItem.id).filter(VideoItem.artist_entity_id == entity_id).first()
        if remaining:
            return
        ent = db.get(ArtistEntity, entity_id)
        if not ent:
            return
        # Remove _artists folder
        folder = os.path.join(get_artists_dir(), _safe_name(ent.canonical_name))
        _robust_rmtree(folder)
        # Remove cached assets
        delete_entity_cached_assets("artist", entity_id, db)
        # Remove export manifest
        db.query(ExportManifest).filter(
            ExportManifest.entity_type == "artist",
            ExportManifest.entity_id == entity_id,
        ).delete(synchronize_session="fetch")
        db.delete(ent)
        logger.info(f"Cleaned up orphaned artist entity {entity_id} ({ent.canonical_name})")

    elif entity_type == "album":
        remaining = db.query(VideoItem.id).filter(VideoItem.album_entity_id == entity_id).first()
        if remaining:
            return
        ent = db.get(AlbumEntity, entity_id)
        if not ent:
            return
        artist_name = ent.artist.canonical_name if ent.artist else "Unknown"
        folder = os.path.join(get_albums_dir(), _safe_name(artist_name), _safe_name(ent.title))
        _robust_rmtree(folder)
        # If the artist sub-folder under _albums is now empty, remove it too
        artist_albums_dir = os.path.join(get_albums_dir(), _safe_name(artist_name))
        if os.path.isdir(artist_albums_dir) and not os.listdir(artist_albums_dir):
            _robust_rmtree(artist_albums_dir)
        delete_entity_cached_assets("album", entity_id, db)
        db.query(ExportManifest).filter(
            ExportManifest.entity_type == "album",
            ExportManifest.entity_id == entity_id,
        ).delete(synchronize_session="fetch")
        db.delete(ent)
        logger.info(f"Cleaned up orphaned album entity {entity_id} ({ent.title})")

    elif entity_type == "track":
        remaining = db.query(VideoItem.id).filter(VideoItem.track_id == entity_id).first()
        if remaining:
            return
        ent = db.get(TrackEntity, entity_id)
        if ent:
            db.delete(ent)
            logger.info(f"Cleaned up orphaned track entity {entity_id}")


def _cleanup_orphaned_entity_folders(
    db: Session,
    artist_names: set[str],
    album_keys: set[tuple[str, str]],
    artist_entity_ids: set[int],
    album_entity_ids: set[int],
    track_ids: set[int],
):
    """
    After deleting videos, remove _artists / _albums folders, entity rows,
    and cached entity assets that are no longer referenced by any remaining video.
    """
    from app.services.artwork_manager import get_artists_dir, get_albums_dir, _safe_name

    # --- Clean orphaned _artists folders (by display name, not entity) ---
    for name in artist_names:
        remaining = db.query(VideoItem.id).filter(VideoItem.artist == name).first()
        if remaining:
            continue
        artist_dir = os.path.join(get_artists_dir(), _safe_name(name))
        _robust_rmtree(artist_dir)

    # --- Clean orphaned _albums folders (by display name) ---
    for artist_name, album_name in album_keys:
        remaining = db.query(VideoItem.id).filter(
            VideoItem.artist == artist_name, VideoItem.album == album_name
        ).first()
        if remaining:
            continue
        album_dir = os.path.join(get_albums_dir(), _safe_name(artist_name), _safe_name(album_name))
        _robust_rmtree(album_dir)
        artist_albums_dir = os.path.join(get_albums_dir(), _safe_name(artist_name))
        if os.path.isdir(artist_albums_dir) and not os.listdir(artist_albums_dir):
            _robust_rmtree(artist_albums_dir)

    # --- Clean orphaned entity rows + their cached assets + export folders ---
    for eid in artist_entity_ids:
        cleanup_orphaned_entity(db, "artist", eid)
    for eid in album_entity_ids:
        cleanup_orphaned_entity(db, "album", eid)
    for tid in track_ids:
        cleanup_orphaned_entity(db, "track", tid)

    db.commit()


@router.delete("/{video_id}")
def delete_video(video_id: int, db: Session = Depends(get_db)):
    """Delete a video item and its files from disk."""

    item = db.query(VideoItem).get(video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Video not found")

    folder_path = item.folder_path
    file_basename = os.path.splitext(os.path.basename(item.file_path))[0] if item.file_path else None
    artist_names = {item.artist} if item.artist else set()
    album_keys = {(item.artist, item.album)} if item.artist and item.album else set()
    artist_entity_ids = {item.artist_entity_id} if item.artist_entity_id else set()
    album_entity_ids = {item.album_entity_id} if item.album_entity_id else set()
    track_ids = {item.track_id} if item.track_id else set()

    # Also collect entity canonical names — folders under _artists/_albums are
    # created from canonical_name, which may differ from item.artist.
    if item.artist_entity_id:
        ent = db.get(ArtistEntity, item.artist_entity_id)
        if ent and ent.canonical_name:
            artist_names.add(ent.canonical_name)
    if item.album_entity_id and item.artist_entity_id:
        album_ent = db.get(AlbumEntity, item.album_entity_id)
        artist_ent = db.get(ArtistEntity, item.artist_entity_id)
        if album_ent and artist_ent:
            album_keys.add((artist_ent.canonical_name, album_ent.title))

    # Clean orphaned child rows BEFORE deleting the parent (defense-in-depth)
    _cleanup_orphaned_child_rows(db, [video_id])

    # Delete video-level cached assets (poster/thumb in PlayarrCache)
    _delete_video_cached_assets(db, [video_id])

    db.delete(item)
    db.commit()

    # Remove the video folder from disk (recycle bin)
    from app.safe_delete import safe_delete, NetworkDeleteError
    try:
        safe_delete(folder_path)
    except NetworkDeleteError:
        # Network path — permanent delete (user already confirmed via UI delete button)
        safe_delete(folder_path, force_permanent=True)
    except Exception:
        logger.warning(f"Could not remove folder: {folder_path}")

    # Remove thumbnail cache directory for this video
    _delete_video_thumbnail_dir(video_id)

    # Remove preview files for this video
    _delete_video_previews(video_id, file_basename)

    # Remove orphaned _artists/_albums folders and entity rows
    _cleanup_orphaned_entity_folders(
        db, artist_names, album_keys, artist_entity_ids, album_entity_ids, track_ids,
    )

    return {"detail": "Deleted", "id": video_id}


class BatchDeleteRequest(BaseModel):
    video_ids: List[int]


@router.post("/batch-delete")
def batch_delete_videos(req: BatchDeleteRequest, db: Session = Depends(get_db)):
    """Delete multiple video items and their files from disk."""
    items = db.query(VideoItem).filter(VideoItem.id.in_(req.video_ids)).all()
    if not items:
        raise HTTPException(status_code=404, detail="No matching videos found")

    # Collect info for orphan cleanup before deleting
    artist_names: set[str] = set()
    album_keys: set[tuple[str, str]] = set()
    artist_entity_ids: set[int] = set()
    album_entity_ids: set[int] = set()
    track_ids: set[int] = set()
    folder_paths: list[str] = []
    all_video_ids: list[int] = []

    for item in items:
        all_video_ids.append(item.id)
        if item.artist:
            artist_names.add(item.artist)
        if item.artist and item.album:
            album_keys.add((item.artist, item.album))
        if item.artist_entity_id:
            artist_entity_ids.add(item.artist_entity_id)
            ent = db.get(ArtistEntity, item.artist_entity_id)
            if ent and ent.canonical_name:
                artist_names.add(ent.canonical_name)
        if item.album_entity_id:
            album_entity_ids.add(item.album_entity_id)
            if item.artist_entity_id:
                album_ent = db.get(AlbumEntity, item.album_entity_id)
                artist_ent = db.get(ArtistEntity, item.artist_entity_id)
                if album_ent and artist_ent:
                    album_keys.add((artist_ent.canonical_name, album_ent.title))
        if item.track_id:
            track_ids.add(item.track_id)
        if item.folder_path:
            folder_paths.append(item.folder_path)

    # Clean orphaned child rows BEFORE deleting parents (defense-in-depth)
    _cleanup_orphaned_child_rows(db, all_video_ids)

    # Delete video-level cached assets (poster/thumb in PlayarrCache)
    _delete_video_cached_assets(db, all_video_ids)

    # Delete all VideoItems in one batch — the SQLAlchemy cascade handles
    # sources, quality_signatures, snapshots, assets, genres, etc.
    deleted_ids = []
    errors = []
    for item in items:
        vid = item.id
        try:
            db.delete(item)
            db.flush()
            deleted_ids.append(vid)
        except Exception as e:
            logger.warning(f"Failed to delete video {vid}: {e}")
            db.rollback()  # Reset session state so subsequent deletes can proceed
            errors.append(vid)

    # Clear duplicate review flags on surviving partner videos
    if deleted_ids:
        _clear_orphaned_duplicate_partners(db, deleted_ids)
        db.commit()

    # Remove video folders, thumbnails, and preview files from disk
    from app.safe_delete import safe_delete, NetworkDeleteError
    deleted_set = set(deleted_ids)
    for item in items:
        if item.id in deleted_set:
            if item.folder_path:
                try:
                    safe_delete(item.folder_path)
                except NetworkDeleteError:
                    safe_delete(item.folder_path, force_permanent=True)
                except Exception:
                    logger.warning(f"Could not remove folder: {item.folder_path}")
            _delete_video_thumbnail_dir(item.id)
            basename = os.path.splitext(os.path.basename(item.file_path))[0] if item.file_path else None
            _delete_video_previews(item.id, basename)

    # Remove orphaned _artists/_albums folders and entity rows
    _cleanup_orphaned_entity_folders(
        db, artist_names, album_keys, artist_entity_ids, album_entity_ids, track_ids,
    )

    return {"deleted": deleted_ids, "errors": errors, "count": len(deleted_ids)}


class ScrapeRequest(BaseModel):
    ai_auto_analyse: bool = False
    ai_only: bool = False
    scrape_wikipedia: bool = False
    wikipedia_url: Optional[str] = None
    scrape_musicbrainz: bool = False
    musicbrainz_url: Optional[str] = None
    scrape_tmvdb: bool = False
    is_cover: bool = False
    is_live: bool = False
    is_alternate: bool = False
    is_uncensored: bool = False
    alternate_version_label: Optional[str] = None
    find_source_video: bool = False
    normalize_audio: bool = False


@router.post("/{video_id}/scrape")
def scrape_video(video_id: int, body: ScrapeRequest = ScrapeRequest(), db: Session = Depends(get_db)):
    """Trigger metadata scraping for a video item."""
    from app.models import ProcessingJob, JobStatus
    from app.tasks import scrape_metadata_task
    from app.routers.jobs import _scrape_action_label
    from app.worker import dispatch_task

    item = db.query(VideoItem).get(video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Video not found")

    _scrape_label = _scrape_action_label(
            ai_auto_analyse=body.ai_auto_analyse, ai_only=body.ai_only,
            scrape_wikipedia=body.scrape_wikipedia,
            scrape_musicbrainz=body.scrape_musicbrainz,
            scrape_tmvdb=body.scrape_tmvdb,
        )
    job = ProcessingJob(
        job_type="metadata_scrape",
        status=JobStatus.queued,
        video_id=video_id,
        display_name=f"{item.artist} \u2013 {item.title} \u203a {_scrape_label}" if item.artist and item.title else None,
        action_label=_scrape_label,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    dispatch_task(scrape_metadata_task, job_id=job.id, video_id=video_id,
                  ai_auto_analyse=body.ai_auto_analyse,
                  ai_only=body.ai_only,
                  scrape_wikipedia=body.scrape_wikipedia,
                  wikipedia_url=body.wikipedia_url,
                  scrape_musicbrainz=body.scrape_musicbrainz,
                  musicbrainz_url=body.musicbrainz_url,
                  scrape_tmvdb=body.scrape_tmvdb,
                  hint_cover=body.is_cover,
                  hint_live=body.is_live,
                  hint_alternate=body.is_alternate,
                  hint_uncensored=body.is_uncensored,
                  hint_alternate_label=body.alternate_version_label or "",
                  find_source_video=body.find_source_video,
                  normalize_audio=body.normalize_audio)
    return {"job_id": job.id, "message": "Metadata scrape queued"}


# ─── Open folder in OS file manager ──────────────────────

@router.post("/{video_id}/open-folder")
def open_folder(video_id: int, db: Session = Depends(get_db)):
    """Open the video's containing folder in the OS file manager."""
    import subprocess, sys

    video = db.query(VideoItem).get(video_id)
    if not video or not video.file_path:
        raise HTTPException(404, "Video not found or has no file path")

    folder = os.path.dirname(video.file_path)
    if not os.path.isdir(folder):
        raise HTTPException(404, f"Folder does not exist: {folder}")

    if sys.platform == "win32":
        os.startfile(folder)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", folder])
    else:
        subprocess.Popen(["xdg-open", folder])

    return {"ok": True, "folder": folder}


# ─── Rename files to expected pattern ─────────────────────

@router.post("/{video_id}/rename", status_code=410)
def rename_to_expected(video_id: int, db: Session = Depends(get_db)):
    """
    Rename the video's folder, video file, NFO, and all associated files
    to match the expected naming pattern (Artist - Title [Resolution]).
    Updates all DB path references.
    """
    raise HTTPException(status_code=410, detail={
        "code": "preview_required",
        "message": "Use /api/library/{video_id}/rename-preview then rename-commit",
        "video_id": video_id,
    })


# ---------------------------------------------------------------------------
# Source CRUD
# ---------------------------------------------------------------------------

@router.post("/{video_id}/sources", response_model=SourceOut, status_code=201)
def create_source(video_id: int, body: SourceCreate, db: Session = Depends(get_db)):
    """Add a new source to a video."""
    from app.models import Source, SourceProvider
    video = db.query(VideoItem).filter(VideoItem.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    try:
        provider = SourceProvider(body.provider)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid provider: {body.provider}")
    source = Source(
        video_id=video_id,
        provider=provider,
        source_video_id=body.source_video_id,
        original_url=body.original_url,
        canonical_url=body.canonical_url,
        source_type=body.source_type,
        provenance="manual",
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.put("/{video_id}/sources/{source_id}", response_model=SourceOut)
def update_source(video_id: int, source_id: int, body: SourceUpdate, db: Session = Depends(get_db)):
    """Update an existing source."""
    from app.models import Source, SourceProvider
    source = db.query(Source).filter(Source.id == source_id, Source.video_id == video_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    if body.provider is not None:
        try:
            source.provider = SourceProvider(body.provider)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid provider: {body.provider}")
    if body.source_video_id is not None:
        source.source_video_id = body.source_video_id
    if body.original_url is not None:
        source.original_url = body.original_url
    if body.canonical_url is not None:
        source.canonical_url = body.canonical_url
    # If URL changed, clear cached platform metadata so next scrape re-fetches
    if body.original_url is not None or body.canonical_url is not None:
        source.platform_title = None
        source.platform_description = None
        source.platform_tags = None
        source.channel_name = None
        source.upload_date = None
    if body.source_type is not None:
        source.source_type = body.source_type
    db.commit()
    db.refresh(source)
    return source


@router.delete("/{video_id}/sources/{source_id}", status_code=204)
def delete_source(video_id: int, source_id: int, db: Session = Depends(get_db)):
    """Delete a source."""
    from app.models import Source
    source = db.query(Source).filter(Source.id == source_id, Source.video_id == video_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    db.delete(source)
    db.commit()


# ---------------------------------------------------------------------------
# Bulk rename — preview and execute
# ---------------------------------------------------------------------------

class BulkRenamePreviewItem(BaseModel):
    video_id: int
    artist: str
    title: str
    current_path: str
    expected_path: str
    needs_rename: bool


class BulkRenameResponse(BaseModel):
    total: int
    needs_rename: int
    already_correct: int
    items: List[BulkRenamePreviewItem]


class BulkRenameExecResponse(BaseModel):
    renamed: int
    failed: int
    errors: List[str]


@router.post("/bulk-rename/preview", response_model=BulkRenameResponse)
def bulk_rename_preview(db: Session = Depends(get_db)):
    """
    Preview which videos would be renamed/moved to match the current
    naming convention settings. Does not modify any files.
    """
    from app.services.file_organizer import compute_expected_paths
    from app.config import get_settings

    settings = get_settings()
    videos = db.query(VideoItem).filter(
        VideoItem.folder_path.isnot(None),
        VideoItem.file_path.isnot(None),
    ).all()

    items = []
    needs_rename = 0
    already_correct = 0

    for v in videos:
        if not v.folder_path or not v.file_path:
            continue

        file_ext = os.path.splitext(v.file_path)[1] or ".mkv"
        expected = compute_expected_paths(
            settings.library_dir,
            v.artist, v.title, v.resolution_label or "1080p",
            album=v.album or "",
            version_type=v.version_type or "normal",
            alternate_version_label=v.alternate_version_label or "",
            file_ext=file_ext,
        )

        current_rel = os.path.relpath(v.folder_path, settings.library_dir) if v.folder_path.startswith(settings.library_dir) else os.path.basename(v.folder_path)
        expected_rel = expected["subpath"]

        # Compare normalised paths
        current_norm = os.path.normpath(v.folder_path).lower()
        expected_norm = os.path.normpath(expected["folder_path"]).lower()
        rename_needed = current_norm != expected_norm

        # Also check if file base name matches
        current_fname = os.path.basename(v.file_path)
        expected_fname = f"{expected['file_base_name']}{file_ext}"
        if current_fname.lower() != expected_fname.lower():
            rename_needed = True

        if rename_needed:
            needs_rename += 1
        else:
            already_correct += 1

        items.append(BulkRenamePreviewItem(
            video_id=v.id,
            artist=v.artist,
            title=v.title,
            current_path=f"{current_rel}/{current_fname}",
            expected_path=f"{expected_rel}/{expected_fname}",
            needs_rename=rename_needed,
        ))

    return BulkRenameResponse(
        total=len(items),
        needs_rename=needs_rename,
        already_correct=already_correct,
        items=items,
    )


@router.post("/bulk-rename/execute", response_model=BulkRenameExecResponse)
def bulk_rename_execute(db: Session = Depends(get_db)):
    """
    Queue durable rename plans for every video that does not match the current
    naming convention.  The mutation worker performs the filesystem changes,
    companion-file renames and database path update as one journalled unit.
    """
    from app.services.rename_operations import (
        enqueue_expected_rename,
        notify_expected_rename,
    )
    from app.user_identity import get_instance_user_id

    videos = db.query(VideoItem).filter(
        VideoItem.folder_path.isnot(None),
        VideoItem.file_path.isnot(None),
    ).all()
    queued = 0
    failed = 0
    errors: list[str] = []
    actor_id = get_instance_user_id(db)

    for video in videos:
        try:
            # A savepoint ensures a collision or missing folder for one item
            # cannot leave a partial operation/command behind or abort the
            # rest of the batch.
            with db.begin_nested():
                operation, _command, created = enqueue_expected_rename(
                    db,
                    video,
                    actor_id=actor_id,
                )
                collisions = operation.plan_json.get("collisions") or []
                if collisions:
                    raise ValueError(
                        f"destination has {len(collisions)} collision(s)"
                    )
                if created:
                    queued += 1
        except ValueError as exc:
            # An already-correct item is not a failure.  Other validation
            # errors remain visible to the caller.
            if "already matches" not in str(exc):
                failed += 1
                errors.append(f"[{video.artist} - {video.title}] {exc}")
        except (FileNotFoundError, OSError) as exc:
            failed += 1
            errors.append(f"[{video.artist} - {video.title}] {exc}")

    db.commit()
    if queued:
        notify_expected_rename()
    # Keep the existing `renamed` response field for API compatibility.  It
    # now reports accepted durable operations; completion is observable via
    # the operations API rather than being performed inside this request.
    return BulkRenameExecResponse(
        renamed=queued,
        failed=failed,
        errors=errors[:50],
    )



def _cleanup_empty_parents(folder_path: str, stop_at: str):
    """Remove empty parent directories up to (but not including) stop_at."""
    parent = os.path.dirname(folder_path)
    stop_norm = os.path.normpath(stop_at).lower()
    while parent and os.path.normpath(parent).lower() != stop_norm:
        try:
            if os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
                parent = os.path.dirname(parent)
            else:
                break
        except OSError:
            break


# ─── Repair artwork paths broken by bulk rename ──────────

@router.post("/repair-artwork-paths")
def repair_artwork_paths(db: Session = Depends(get_db)):
    """
    Fix artist_thumb and album_thumb MediaAsset records whose file_path
    was incorrectly rewritten to point into the video folder (instead of
    _artists/ or _albums/ directories) by the bulk rename operation.
    """
    import re
    from app.services.artwork_manager import (
        get_artists_dir, get_albums_dir, _safe_name,
    )

    artists_dir = get_artists_dir()
    albums_dir = get_albums_dir()
    repaired = 0
    not_found = 0

    # Fix artist_thumb assets
    artist_assets = db.query(MediaAsset).filter(
        MediaAsset.asset_type == "artist_thumb",
    ).all()

    for asset in artist_assets:
        if not asset.file_path:
            continue
        # If path already points into _artists/ and file exists, skip
        if os.path.normpath(asset.file_path).lower().startswith(
            os.path.normpath(artists_dir).lower()
        ) and os.path.isfile(asset.file_path):
            continue

        # Reconstruct correct path from the video's artist name
        video = db.query(VideoItem).get(asset.video_id)
        if not video or not video.artist:
            continue

        correct_path = os.path.join(
            artists_dir, _safe_name(video.artist), "poster.jpg"
        )
        if os.path.isfile(correct_path):
            asset.file_path = correct_path
            asset.status = "valid"
            repaired += 1
        else:
            not_found += 1
            logger.warning(
                f"Artist art not found for '{video.artist}': {correct_path}"
            )

    # Fix album_thumb assets
    album_assets = db.query(MediaAsset).filter(
        MediaAsset.asset_type == "album_thumb",
    ).all()

    for asset in album_assets:
        if not asset.file_path:
            continue
        # If path already points into _albums/ and file exists, skip
        if os.path.normpath(asset.file_path).lower().startswith(
            os.path.normpath(albums_dir).lower()
        ) and os.path.isfile(asset.file_path):
            continue

        video = db.query(VideoItem).get(asset.video_id)
        if not video or not video.artist or not video.album:
            continue

        correct_path = os.path.join(
            albums_dir, _safe_name(video.artist),
            _safe_name(video.album), "poster.jpg"
        )
        if os.path.isfile(correct_path):
            asset.file_path = correct_path
            asset.status = "valid"
            repaired += 1
        else:
            not_found += 1
            logger.warning(
                f"Album art not found for '{video.artist} - {video.album}': {correct_path}"
            )

    db.commit()
    return {
        "repaired": repaired,
        "not_found": not_found,
        "total_checked": len(artist_assets) + len(album_assets),
    }


@router.post("/export-playarr-xml")
def export_all_playarr_xml(db: Session = Depends(get_db)):
    """Write .playarr.xml sidecar files for every video in the library."""
    from app.services.playarr_xml import write_playarr_xml

    videos = db.query(VideoItem).filter(VideoItem.folder_path.isnot(None)).all()
    written = 0
    errors = 0
    for video in videos:
        try:
            write_playarr_xml(video, db)
            written += 1
        except Exception as e:
            errors += 1
            logger.warning(f"Playarr XML export failed for video {video.id}: {e}")
    return {"written": written, "errors": errors, "total": len(videos)}


# ═══════════════════════════════════════════════════════════════════════════
# Canonical Track Operations
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/{video_id}/canonical-scan")
def scan_canonical_matches(video_id: int, db: Session = Depends(get_db)):
    """Scan library for canonical track candidates for a video."""
    from app.services.canonical_track import scan_library_for_canonical_matches

    item = db.query(VideoItem).get(video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Video not found")

    candidates = scan_library_for_canonical_matches(db, item)
    return {
        "video_id": video_id,
        "current_track_id": item.track_id,
        "candidates": candidates,
    }


@router.post("/{video_id}/canonical-link")
def link_to_canonical(video_id: int, track_id: int, db: Session = Depends(get_db)):
    """Link a video to an existing canonical track."""
    from app.services.canonical_track import link_video_to_canonical_track

    item = db.query(VideoItem).get(video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Video not found")

    track = db.query(TrackEntity).get(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Canonical track not found")

    link_video_to_canonical_track(db, item, track)
    item.canonical_provenance = "user"
    item.canonical_confidence = 1.0

    # Clear any canonical review flags
    if item.review_category in ("canonical_missing", "canonical_conflict", "canonical_low_confidence"):
        item.review_status = "reviewed"
        item.review_reason = None
        item.review_category = None

    db.commit()
    return get_video(video_id, db)


@router.post("/{video_id}/canonical-unlink")
def unlink_canonical(video_id: int, db: Session = Depends(get_db)):
    """Remove a video's canonical track link."""
    item = db.query(VideoItem).get(video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Video not found")

    item.track_id = None
    item.canonical_confidence = None
    item.canonical_provenance = None
    db.commit()
    return get_video(video_id, db)


@router.post("/{video_id}/canonical-create")
def create_canonical_for_video(
    video_id: int,
    body: CanonicalTrackCreate,
    db: Session = Depends(get_db),
):
    """Create a new canonical track and link the video to it."""
    from app.services.canonical_track import (
        create_canonical_track_manual, link_video_to_canonical_track,
    )
    item = db.query(VideoItem).get(video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Video not found")

    track = create_canonical_track_manual(
        db,
        title=body.title,
        artist_name=body.artist_name,
        album_name=body.album_name,
        year=body.year,
        is_cover=body.is_cover,
        original_artist=body.original_artist,
        original_title=body.original_title,
        genres=body.genres,
    )
    link_video_to_canonical_track(db, item, track)
    item.canonical_provenance = "user"
    item.canonical_confidence = 1.0

    # Clear any canonical review flags
    if item.review_category in ("canonical_missing", "canonical_conflict", "canonical_low_confidence"):
        item.review_status = "reviewed"
        item.review_reason = None
        item.review_category = None

    db.commit()
    return get_video(video_id, db)


@router.put("/{video_id}/canonical-track")
def edit_canonical_track(
    video_id: int,
    body: CanonicalTrackUpdate,
    db: Session = Depends(get_db),
):
    """Edit the canonical track linked to a video."""
    from app.services.canonical_track import update_canonical_track
    item = db.query(VideoItem).get(video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Video not found")
    if not item.track_id:
        raise HTTPException(status_code=400, detail="Video has no canonical track to edit")

    track = db.query(TrackEntity).get(item.track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Canonical track not found")

    update_canonical_track(
        db, track,
        title=body.title,
        artist_name=body.artist_name,
        album_name=body.album_name,
        year=body.year,
        is_cover=body.is_cover,
        original_artist=body.original_artist,
        original_title=body.original_title,
        genres=body.genres,
    )

    # Update provenance on the video
    item.canonical_provenance = "user"
    item.canonical_confidence = 1.0

    db.commit()
    return get_video(video_id, db)


@router.post("/{video_id}/parent-video")
def set_parent(
    video_id: int,
    body: SetParentVideoRequest,
    db: Session = Depends(get_db),
):
    """Set or clear a video's parent video (hierarchical version chain)."""
    from app.services.canonical_track import set_parent_video
    item = db.query(VideoItem).get(video_id)
    if not item:
        raise HTTPException(status_code=404, detail="Video not found")

    try:
        set_parent_video(db, item, body.parent_video_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.commit()
    return get_video(video_id, db)


@router.post("/scan-canonical-issues")
def scan_canonical_issues(db: Session = Depends(get_db)):
    """Scan the library for canonical track issues and flag for review."""
    from app.services.canonical_track import scan_library_canonical_issues

    counts = scan_library_canonical_issues(db)
    total = sum(counts.values())
    return {"status": "ok", "flagged": total, "breakdown": counts}
