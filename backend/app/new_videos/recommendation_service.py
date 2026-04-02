"""
Recommendation Service — The orchestration layer for the New Videos feature.

Responsibilities:
  - Fetch candidate videos from modular source strategies
  - Score with trust_scoring + recommendation_ranker
  - Filter against library contents (already imported/queued/dismissed)
  - Cache results in recommendation_snapshots
  - Serve category feeds to the API layer

Source strategies are pluggable: each category has a function that returns
a list of RecommendationCandidate objects. The engine scores, filters,
deduplicates, and persists them as SuggestedVideo rows.

Display strategy:
  Thumbnail-based cards (no embedded players). Users click "Open Source"
  to watch externally or "Add" to import. This minimizes API load, avoids
  autoplay/iframe issues, and lets users scan many suggestions quickly.
"""
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.new_videos.models import (
    SuggestedVideo, SuggestedVideoDismissal, SuggestedVideoCartItem,
    RecommendationSnapshot,
)
from app.new_videos.trust_scoring import score_trust
from app.new_videos.recommendation_ranker import (
    RecommendationCandidate, RecommendationRanker, FeedbackAdjuster,
)
from app.new_videos import feedback_service

logger = logging.getLogger(__name__)

# ── Category definitions ──────────────────────────────────────────────────────
CATEGORIES = ["new", "popular", "rising", "by_artist", "taste", "famous"]

# ── Famous music videos seed list ─────────────────────────────────────────────
# Curated list of historically significant / iconic music videos.
# Each entry: (artist, title, youtube_video_id_if_known)
FAMOUS_SEEDS = [
    ("A-ha", "Take On Me", "djV11Xbc914"),
    ("Michael Jackson", "Thriller", "sOnqjkJTMaA"),
    ("Peter Gabriel", "Sledgehammer", "OJWJE0x7T4Q"),
    ("Nirvana", "Smells Like Teen Spirit", "hTWKbfoikeg"),
    ("OK Go", "Here It Goes Again", "dTAAsCNK7RA"),
    ("Fatboy Slim", "Weapon of Choice", "wCDIYvFmgW8"),
    ("Radiohead", "Karma Police", "1uYWYWPc9HU"),
    ("Beastie Boys", "Sabotage", "z5rRZdiu1UE"),
    ("Jamiroquai", "Virtual Insanity", "4JkIs37a2JE"),
    ("Daft Punk", "Around the World", "LKYPYj2XX80"),
    ("Gorillaz", "Clint Eastwood", "1V_xRb0x9aw"),
    ("The White Stripes", "Seven Nation Army", "0J2QdDbelmY"),
    ("Missy Elliott", "Get Ur Freak On", "FPoKiGQzbSQ"),
    ("Childish Gambino", "This Is America", "VYOjWnS4cMY"),
    ("Björk", "All Is Full of Love", "AjI2J2SQ528"),
    ("Nine Inch Nails", "Closer", "PTFwQP86BRs"),
    ("Weezer", "Buddy Holly", "kemivUKb4f4"),
    ("OutKast", "Hey Ya!", "PWgvGjAhvIw"),
    ("Beyoncé", "Single Ladies", "4m1EFMoRFvY"),
    ("Foo Fighters", "Everlong", "eBG7P-K-r1Y"),
    ("Red Hot Chili Peppers", "Californication", "YlUKcNNmywk"),
    ("Johnny Cash", "Hurt", "8AHCfZTRGiI"),
    ("Queen", "Bohemian Rhapsody", "fJ9rUzIMcZQ"),
    ("Guns N' Roses", "November Rain", "8SbUC-UaAxE"),
    ("Pearl Jam", "Jeremy", "MS91knuzoOA"),
    ("R.E.M.", "Losing My Religion", "xwtdhWltSIg"),
    ("Eminem", "Without Me", "YVkUvmDQ3HY"),
    ("Talking Heads", "Once in a Lifetime", "5IsSpAOD6K8"),
    ("The Prodigy", "Firestarter", "wmin5WkOuPw"),
    ("Sia", "Chandelier", "2vjPBrBU-TM"),
    ("Kendrick Lamar", "HUMBLE.", "tvTRZJ-4EyI"),
    ("Tool", "Sober", "nspxAG12Cpc"),
    ("Massive Attack", "Teardrop", "u7K72X4eo_s"),
    ("LCD Soundsystem", "Drunk Girls", "qdRaf3-OEh4"),
    ("Smashing Pumpkins", "Tonight Tonight", "NOG3eus4ZSo"),
    ("Lauryn Hill", "Doo Wop (That Thing)", "T6QKqFPRZSA"),
    ("Arcade Fire", "The Suburbs", "5Euj9f3gdyM"),
    ("David Bowie", "Space Oddity", "iYYRH4apXDo"),
    ("Madonna", "Like a Prayer", "79fzeNUqQbQ"),
    ("Prince", "When Doves Cry", "UG3VcCAlUgE"),
]

# ── Popular seeds (well-known, high-view-count official videos) ───────────────
POPULAR_SEEDS = [
    ("Luis Fonsi ft. Daddy Yankee", "Despacito", "kJQP7kiw5Fk"),
    ("Ed Sheeran", "Shape of You", "JGwWNGJdvx8"),
    ("Wiz Khalifa ft. Charlie Puth", "See You Again", "RgKAFK5djSk"),
    ("Mark Ronson ft. Bruno Mars", "Uptown Funk", "OPf0YbXqDm0"),
    ("PSY", "Gangnam Style", "9bZkp7q19f0"),
    ("Maroon 5", "Sugar", "09R8_2nJtjg"),
    ("Katy Perry", "Roar", "CevxZvSJLk8"),
    ("Taylor Swift", "Shake It Off", "nfWlot6h_JM"),
    ("Adele", "Hello", "YQHsXMglC9A"),
    ("The Weeknd", "Blinding Lights", "4NRXx6U8ABQ"),
    ("Imagine Dragons", "Believer", "7wtfhZwyrcc"),
    ("Billie Eilish", "Bad Guy", "DyDfgMOUjCI"),
    ("Dua Lipa", "Levitating", "TUVcZfQe-Kw"),
    ("Harry Styles", "Watermelon Sugar", "E07s5ZYadZs"),
    ("Post Malone", "Circles", "wXhTHyIgQ_U"),
    ("Lizzo", "Juice", "XaCrQL_8eMY"),
    ("Doja Cat", "Say So", "pok8H_KF1FA"),
    ("Glass Animals", "Heat Waves", "mRD0-GxKHPk"),
    ("The Cranberries", "Zombie", "6Ejga4kJUts"),
    ("Linkin Park", "In the End", "eVTXPUF4Oz4"),
]


def _get_setting(db: Session, key: str, default: str, value_type: str = "string"):
    """Read a setting from app_settings, returning typed default if missing."""
    from app.models import AppSetting
    row = db.query(AppSetting).filter(
        AppSetting.key == key, AppSetting.user_id.is_(None)
    ).first()
    val = row.value if row else default
    if value_type == "bool":
        return val.lower() in ("true", "1", "yes")
    if value_type == "int":
        try:
            return int(val)
        except (ValueError, TypeError):
            return int(default)
    if value_type == "float":
        try:
            return float(val)
        except (ValueError, TypeError):
            return float(default)
    return val


# ── Library awareness helpers ─────────────────────────────────────────────────

def _get_library_source_urls(db: Session) -> set[str]:
    """Return set of all source URLs already in the Playarr library."""
    from app.models import Source
    rows = db.query(Source.original_url).all()
    urls = set()
    for (url,) in rows:
        if url:
            urls.add(url.strip())
            # Also add canonical form
            m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
            if m:
                urls.add(f"https://www.youtube.com/watch?v={m.group(1)}")
    return urls


def _get_library_video_ids(db: Session) -> set[str]:
    """Return set of YouTube video IDs already in the library."""
    from app.models import Source
    ids = set()
    for (url,) in db.query(Source.original_url).all():
        if url:
            m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
            if m:
                ids.add(m.group(1))
    return ids


def _get_library_artists(db: Session) -> list[dict]:
    """Return list of {artist, count, has_5star} for artists in the library."""
    from app.models import VideoItem
    from sqlalchemy import case

    rows = db.query(
        VideoItem.artist,
        func.count(VideoItem.id),
        func.max(case(
            (VideoItem.song_rating == 5, 1),
            else_=0,
        )),
    ).filter(
        VideoItem.artist.isnot(None),
        VideoItem.artist != "",
    ).group_by(VideoItem.artist).all()

    return [
        {"artist": r[0], "count": r[1], "has_5star": bool(r[2])}
        for r in rows
    ]


def _get_dismissed_provider_ids(db: Session) -> set[str]:
    """Return set of provider_video_ids that are permanently dismissed."""
    rows = db.query(SuggestedVideoDismissal.provider_video_id).filter(
        SuggestedVideoDismissal.dismissal_type == "permanent",
        SuggestedVideoDismissal.provider_video_id.isnot(None),
    ).all()
    return {r[0] for r in rows}


def _get_cart_provider_ids(db: Session) -> set[str]:
    """Return set of provider_video_ids already in the cart."""
    rows = db.query(SuggestedVideoCartItem.provider_video_id).filter(
        SuggestedVideoCartItem.provider_video_id.isnot(None),
    ).all()
    return {r[0] for r in rows}


# ── Source strategies ─────────────────────────────────────────────────────────
# Each returns a list of RecommendationCandidate objects.
# These are the pluggable candidate generators.

def _generate_famous_candidates(db: Session, limit: int = 20) -> list[RecommendationCandidate]:
    """Generate candidates from the curated famous videos seed list."""
    library_ids = _get_library_video_ids(db)
    dismissed_ids = _get_dismissed_provider_ids(db)
    candidates = []

    for artist, title, vid_id in FAMOUS_SEEDS:
        if vid_id in library_ids or vid_id in dismissed_ids:
            continue
        candidates.append(RecommendationCandidate(
            provider="youtube",
            provider_video_id=vid_id,
            url=f"https://www.youtube.com/watch?v={vid_id}",
            title=f"{artist} - {title} (Official Video)",
            artist=artist,
            channel=f"{artist}VEVO" if "VEVO" not in artist else artist,
            thumbnail_url=f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg",
            category="famous",
            popularity_score=0.90,
            freshness_score=0.30,
            reasons=[f"Iconic music video — '{title}' by {artist}"],
        ))
        if len(candidates) >= limit:
            break

    return candidates


def _generate_popular_candidates(db: Session, limit: int = 20) -> list[RecommendationCandidate]:
    """Generate candidates from the popular videos seed list."""
    library_ids = _get_library_video_ids(db)
    dismissed_ids = _get_dismissed_provider_ids(db)
    candidates = []

    for artist, title, vid_id in POPULAR_SEEDS:
        if vid_id in library_ids or vid_id in dismissed_ids:
            continue
        candidates.append(RecommendationCandidate(
            provider="youtube",
            provider_video_id=vid_id,
            url=f"https://www.youtube.com/watch?v={vid_id}",
            title=f"{artist} - {title} (Official Video)",
            artist=artist,
            channel=f"{artist}VEVO",
            thumbnail_url=f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg",
            category="popular",
            popularity_score=0.95,
            freshness_score=0.40,
            reasons=["Extremely popular official music video"],
        ))
        if len(candidates) >= limit:
            break

    return candidates


def _generate_by_artist_candidates(db: Session, limit: int = 20) -> list[RecommendationCandidate]:
    """Suggest notable videos by artists already in the user's library.

    Strategy: for each artist with >= min_videos, we surface a placeholder
    recommendation. The actual YouTube search is deferred to a background
    enrichment pass (or uses yt-dlp search if available).
    For now, we generate candidates from the famous/popular seeds that match
    library artists and aren't yet imported.
    """
    min_owned = _get_setting(db, "nv_min_owned_for_artist_rec", "2", "int")
    max_per_artist = _get_setting(db, "nv_max_recs_per_artist", "5", "int")
    library_artists = _get_library_artists(db)
    library_ids = _get_library_video_ids(db)
    dismissed_ids = _get_dismissed_provider_ids(db)
    candidates = []

    # Build artist→seeds lookup
    all_seeds = FAMOUS_SEEDS + POPULAR_SEEDS
    artist_seeds: dict[str, list] = {}
    for a, t, vid in all_seeds:
        artist_seeds.setdefault(a.lower(), []).append((a, t, vid))

    for info in library_artists:
        if info["count"] < min_owned:
            continue
        artist_lower = info["artist"].lower()
        seeds = artist_seeds.get(artist_lower, [])
        added = 0
        for artist, title, vid_id in seeds:
            if vid_id in library_ids or vid_id in dismissed_ids:
                continue
            candidates.append(RecommendationCandidate(
                provider="youtube",
                provider_video_id=vid_id,
                url=f"https://www.youtube.com/watch?v={vid_id}",
                title=f"{artist} - {title} (Official Video)",
                artist=artist,
                channel=f"{artist}VEVO",
                thumbnail_url=f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg",
                category="by_artist",
                popularity_score=0.80,
                freshness_score=0.30,
                reasons=[f"You already have {info['count']} {artist} videos — this one is missing"],
            ))
            added += 1
            if added >= max_per_artist:
                break
        if len(candidates) >= limit:
            break

    return candidates[:limit]


def _generate_taste_candidates(db: Session, limit: int = 10) -> list[RecommendationCandidate]:
    """Generate taste-based recommendations from 5-star rated artists.

    Strategy: Find artists the user has rated 5 stars and suggest famous/popular
    videos by similar or same artists that aren't in their library.
    """
    from app.models import VideoItem
    use_ratings = _get_setting(db, "nv_use_ratings", "true", "bool")
    if not use_ratings:
        return []

    # Get artists with 5-star song ratings
    fav_artists = db.query(VideoItem.artist).filter(
        VideoItem.song_rating == 5,
        VideoItem.song_rating_set == True,  # noqa: E712
        VideoItem.artist.isnot(None),
    ).distinct().all()
    fav_artist_names = {r[0].lower() for r in fav_artists if r[0]}

    if not fav_artist_names:
        return []

    library_ids = _get_library_video_ids(db)
    dismissed_ids = _get_dismissed_provider_ids(db)
    candidates = []

    all_seeds = FAMOUS_SEEDS + POPULAR_SEEDS
    for artist, title, vid_id in all_seeds:
        if artist.lower() in fav_artist_names:
            if vid_id in library_ids or vid_id in dismissed_ids:
                continue
            candidates.append(RecommendationCandidate(
                provider="youtube",
                provider_video_id=vid_id,
                url=f"https://www.youtube.com/watch?v={vid_id}",
                title=f"{artist} - {title} (Official Video)",
                artist=artist,
                channel=f"{artist}VEVO",
                thumbnail_url=f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg",
                category="taste",
                popularity_score=0.80,
                freshness_score=0.35,
                reasons=[f"Based on your 5-star rated {artist} videos"],
            ))
            if len(candidates) >= limit:
                break

    return candidates


def _generate_new_candidates(db: Session, limit: int = 10) -> list[RecommendationCandidate]:
    """Placeholder for new/recent music video discovery.

    In production, this would query YouTube Data API or RSS feeds for recent
    uploads from known VEVO/official channels. For now returns empty — the
    refresh-via-yt-dlp enrichment can populate these.
    """
    return []


def _generate_rising_candidates(db: Session, limit: int = 10) -> list[RecommendationCandidate]:
    """Placeholder for rising/trending music videos.

    Would use YouTube trending API or external trend data. Returns empty
    until a provider API key is configured.
    """
    return []


# Category → generator mapping
CATEGORY_GENERATORS = {
    "new": _generate_new_candidates,
    "popular": _generate_popular_candidates,
    "rising": _generate_rising_candidates,
    "by_artist": _generate_by_artist_candidates,
    "taste": _generate_taste_candidates,
    "famous": _generate_famous_candidates,
}


# ── Main service functions ────────────────────────────────────────────────────

def _build_feedback_adjuster(db: Session) -> FeedbackAdjuster:
    """Build a FeedbackAdjuster from stored feedback data."""
    artist_adds, artist_dismisses = feedback_service.get_artist_feedback_counts(db)
    cat_adds, cat_dismisses = feedback_service.get_category_feedback_counts(db)
    trusted = feedback_service.get_trusted_channels(db)
    return FeedbackAdjuster(
        artist_add_counts=artist_adds,
        artist_dismiss_counts=artist_dismisses,
        category_add_counts=cat_adds,
        category_dismiss_counts=cat_dismisses,
        trusted_channels=trusted,
    )


def _persist_candidate(db: Session, candidate: RecommendationCandidate,
                       score: float) -> SuggestedVideo:
    """Upsert a SuggestedVideo row from a scored candidate."""
    existing = db.query(SuggestedVideo).filter(
        SuggestedVideo.provider == candidate.provider,
        SuggestedVideo.provider_video_id == candidate.provider_video_id,
        SuggestedVideo.category == candidate.category,
    ).first()

    trust = candidate.trust_result
    data = {
        "url": candidate.url,
        "title": candidate.title,
        "artist": candidate.artist,
        "album": candidate.album,
        "channel": candidate.channel,
        "thumbnail_url": candidate.thumbnail_url,
        "duration_seconds": candidate.duration_seconds,
        "release_date": candidate.release_date,
        "view_count": candidate.view_count,
        "source_type": trust.source_type if trust else "unknown",
        "trust_score": trust.score if trust else 0.5,
        "popularity_score": candidate.popularity_score,
        "trend_score": candidate.trend_score,
        "recommendation_score": score,
        "recommendation_reason_json": candidate.reasons or [],
        "trust_reasons_json": (trust.reasons + trust.penalties) if trust else [],
        "metadata_json": candidate.metadata,
        "updated_at": datetime.now(timezone.utc),
    }

    if existing:
        for k, v in data.items():
            setattr(existing, k, v)
        return existing
    else:
        sv = SuggestedVideo(
            provider=candidate.provider,
            provider_video_id=candidate.provider_video_id,
            category=candidate.category,
            **data,
        )
        db.add(sv)
        db.flush()
        return sv


def refresh_category(db: Session, category: str, force: bool = False) -> int:
    """Regenerate suggestions for a single category.

    Returns the number of suggestions generated.
    """
    if category not in CATEGORY_GENERATORS:
        logger.warning(f"Unknown category: {category}")
        return 0

    # Check cache freshness
    refresh_minutes = _get_setting(db, "nv_refresh_interval_minutes", "360", "int")
    snapshot = db.query(RecommendationSnapshot).filter(
        RecommendationSnapshot.category == category
    ).first()

    if snapshot and not force:
        if snapshot.expires_at and datetime.now(timezone.utc) < snapshot.expires_at:
            logger.info(f"Category '{category}' cache still fresh, skipping")
            return 0

    limit = _get_setting(db, "nv_videos_per_category", "12", "int")
    min_trust = _get_setting(db, "nv_min_trust_threshold", "0.3", "float")

    # Generate candidates
    generator = CATEGORY_GENERATORS[category]
    candidates = generator(db, limit=limit * 2)  # over-generate, then filter

    if not candidates:
        logger.info(f"Category '{category}': no candidates generated")
        _update_snapshot(db, category, [])
        return 0

    # Score trust
    for c in candidates:
        c.trust_result = score_trust(
            title=c.title,
            channel=c.channel,
            artist=c.artist,
            view_count=c.view_count,
            duration_seconds=c.duration_seconds,
        )

    # Filter by minimum trust
    trusted_filter = _get_setting(db, "nv_enable_trusted_source_filtering", "true", "bool")
    if trusted_filter:
        candidates = [c for c in candidates if c.trust_result.score >= min_trust]

    # Apply feedback adjustments
    adjuster = _build_feedback_adjuster(db)
    for c in candidates:
        c.feedback_adjustment = adjuster.adjust(c)

    # Score with ranker
    ranker = RecommendationRanker()
    scored = []
    for c in candidates:
        score = ranker.score(c)
        scored.append((c, score))

    # Sort by score descending
    scored.sort(key=lambda x: x[1], reverse=True)

    # Deduplicate by provider_video_id (keep highest-scored)
    seen_ids: set[str] = set()
    deduped = []
    for c, s in scored:
        if c.provider_video_id not in seen_ids:
            seen_ids.add(c.provider_video_id)
            deduped.append((c, s))

    # Persist ALL candidates (extras serve as backfill pool when
    # snapshot videos are dismissed), but only put top N in snapshot.
    sv_ids = []
    for candidate, score in deduped:
        sv = _persist_candidate(db, candidate, score)
        sv_ids.append(sv.id)

    snapshot_ids = sv_ids[:limit]
    _update_snapshot(db, category, snapshot_ids)
    db.commit()

    logger.info(f"Category '{category}': generated {len(deduped)} suggestions "
                f"({len(snapshot_ids)} in snapshot, {len(sv_ids) - len(snapshot_ids)} backfill pool)")
    return len(deduped)


def _update_snapshot(db: Session, category: str, video_ids: list[int]):
    """Upsert the snapshot for a category."""
    refresh_minutes = _get_setting(db, "nv_refresh_interval_minutes", "360", "int")
    now = datetime.now(timezone.utc)

    snapshot = db.query(RecommendationSnapshot).filter(
        RecommendationSnapshot.category == category
    ).first()

    if snapshot:
        snapshot.generated_at = now
        snapshot.expires_at = now + timedelta(minutes=refresh_minutes)
        snapshot.payload_json = video_ids
        snapshot.generator_version = "v1"
    else:
        snapshot = RecommendationSnapshot(
            category=category,
            generated_at=now,
            expires_at=now + timedelta(minutes=refresh_minutes),
            payload_json=video_ids,
            generator_version="v1",
        )
        db.add(snapshot)
    db.flush()


def refresh_all_categories(db: Session, force: bool = False) -> dict[str, int]:
    """Refresh all categories. Returns {category: count} dict."""
    results = {}
    for cat in CATEGORIES:
        results[cat] = refresh_category(db, cat, force=force)
    return results


def get_feed(db: Session) -> dict:
    """Return the full discovery feed grouped by category.

    Reads from cached SuggestedVideo rows. If a category has no snapshot
    or the snapshot is empty, returns an empty list for that category.

    Returns:
        {
            "categories": {
                "famous": { "videos": [...], "generated_at": "...", "expires_at": "..." },
                ...
            },
            "cart_count": 5,
        }
    """
    # Get all active dismissed provider IDs (temporary + permanent)
    dismissed_temp_ids = set()
    dismissed_perm_ids = set()
    for d in db.query(SuggestedVideoDismissal).all():
        if d.dismissal_type == "permanent" and d.provider_video_id:
            dismissed_perm_ids.add(d.provider_video_id)
        elif d.dismissal_type == "temporary" and d.suggested_video_id:
            dismissed_temp_ids.add(d.suggested_video_id)

    cart_count = db.query(SuggestedVideoCartItem).count()
    cart_video_ids = {r[0] for r in db.query(SuggestedVideoCartItem.suggested_video_id).all()}

    limit = _get_setting(db, "nv_videos_per_category", "12", "int")

    result: dict = {"categories": {}, "cart_count": cart_count}

    for cat in CATEGORIES:
        snapshot = db.query(RecommendationSnapshot).filter(
            RecommendationSnapshot.category == cat
        ).first()

        if not snapshot or not snapshot.payload_json:
            result["categories"][cat] = {
                "videos": [],
                "generated_at": None,
                "expires_at": None,
            }
            continue

        # Fetch videos in snapshot order
        video_ids = snapshot.payload_json
        videos = db.query(SuggestedVideo).filter(
            SuggestedVideo.id.in_(video_ids)
        ).all()
        video_map = {v.id: v for v in videos}

        ordered = []
        snapshot_ids_used = set()
        for vid in video_ids:
            v = video_map.get(vid)
            if not v:
                continue
            snapshot_ids_used.add(vid)
            # Skip dismissed
            if v.provider_video_id in dismissed_perm_ids:
                continue
            if v.id in dismissed_temp_ids:
                continue
            ordered.append(_serialize_video(v, in_cart=v.id in cart_video_ids))

        # Backfill from other SuggestedVideo rows for this category if
        # dismissals caused the count to drop below the target.
        if len(ordered) < limit:
            used_provider_ids = {s["provider_video_id"] for s in ordered}
            backfill_q = (
                db.query(SuggestedVideo)
                .filter(
                    SuggestedVideo.category == cat,
                    SuggestedVideo.id.notin_(snapshot_ids_used),
                )
                .order_by(SuggestedVideo.recommendation_score.desc())
                .limit((limit - len(ordered)) * 2)
                .all()
            )
            for v in backfill_q:
                if len(ordered) >= limit:
                    break
                if v.provider_video_id in dismissed_perm_ids:
                    continue
                if v.id in dismissed_temp_ids:
                    continue
                if v.provider_video_id in used_provider_ids:
                    continue
                used_provider_ids.add(v.provider_video_id)
                ordered.append(_serialize_video(v, in_cart=v.id in cart_video_ids))

        result["categories"][cat] = {
            "videos": ordered,
            "generated_at": snapshot.generated_at.isoformat() if snapshot.generated_at else None,
            "expires_at": snapshot.expires_at.isoformat() if snapshot.expires_at else None,
        }

    return result


def _serialize_video(v: SuggestedVideo, in_cart: bool = False) -> dict:
    """Serialize a SuggestedVideo to API response dict."""
    return {
        "id": v.id,
        "provider": v.provider,
        "provider_video_id": v.provider_video_id,
        "url": v.url,
        "title": v.title,
        "artist": v.artist,
        "album": v.album,
        "channel": v.channel,
        "thumbnail_url": v.thumbnail_url,
        "duration_seconds": v.duration_seconds,
        "release_date": v.release_date,
        "view_count": v.view_count,
        "category": v.category,
        "source_type": v.source_type,
        "trust_score": v.trust_score,
        "popularity_score": v.popularity_score,
        "trend_score": v.trend_score,
        "recommendation_score": v.recommendation_score,
        "reasons": v.recommendation_reason_json or [],
        "trust_reasons": v.trust_reasons_json or [],
        "in_cart": in_cart,
    }
