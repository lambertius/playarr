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
import random
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
CATEGORIES = ["famous", "popular", "new", "rising", "by_artist", "taste"]

# ── Famous music videos seed list ─────────────────────────────────────────────
# Curated list of historically significant / iconic music videos.
# Each entry: (artist, title, youtube_video_id_if_known)
FAMOUS_SEEDS = [
    ("A-ha", "Take On Me", "djV11Xbc914"),
    ("Michael Jackson", "Thriller", "sOnqjkJTMaA"),
    ("Michael Jackson", "Billie Jean", "Zi_XLOBDo_Y"),
    ("Michael Jackson", "Beat It", "oRdxUFDoQe0"),
    ("Peter Gabriel", "Sledgehammer", "OJWJE0x7T4Q"),
    ("Nirvana", "Smells Like Teen Spirit", "hTWKbfoikeg"),
    ("Nirvana", "Heart-Shaped Box", "n6P0SitGwy8"),
    ("OK Go", "Here It Goes Again", "dTAAsCNK7RA"),
    ("Fatboy Slim", "Weapon of Choice", "wCDIYvFmgW8"),
    ("Radiohead", "Karma Police", "1uYWYWPc9HU"),
    ("Radiohead", "No Surprises", "u5CVsCnxyXg"),
    ("Beastie Boys", "Sabotage", "z5rRZdiu1UE"),
    ("Jamiroquai", "Virtual Insanity", "4JkIs37a2JE"),
    ("Daft Punk", "Around the World", "LKYPYj2XX80"),
    ("Gorillaz", "Clint Eastwood", "1V_xRb0x9aw"),
    ("Gorillaz", "Feel Good Inc.", "HyHNuVaZJ-k"),
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
    ("Red Hot Chili Peppers", "Under the Bridge", "lwlogyj7nFE"),
    ("Johnny Cash", "Hurt", "8AHCfZTRGiI"),
    ("Queen", "Bohemian Rhapsody", "fJ9rUzIMcZQ"),
    ("Guns N' Roses", "November Rain", "8SbUC-UaAxE"),
    ("Guns N' Roses", "Sweet Child O' Mine", "1w7OgIMMRc4"),
    ("Pearl Jam", "Jeremy", "MS91knuzoOA"),
    ("R.E.M.", "Losing My Religion", "xwtdhWltSIg"),
    ("Eminem", "Without Me", "YVkUvmDQ3HY"),
    ("Eminem", "The Real Slim Shady", "eJO5HU_7_1w"),
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
    ("David Bowie", "Heroes", "lXgkuM2NhYI"),
    ("Madonna", "Like a Prayer", "79fzeNUqQbQ"),
    ("Prince", "When Doves Cry", "UG3VcCAlUgE"),
    ("The Verve", "Bitter Sweet Symphony", "1lyu1KKwC74"),
    ("TLC", "Waterfalls", "8WEtxJ4-sh4"),
    ("Soundgarden", "Black Hole Sun", "3mbBbFH9fAg"),
    ("Green Day", "Basket Case", "NUTGr5t3MoY"),
    ("Alanis Morissette", "Ironic", "Jne9t8sHpUc"),
    ("No Doubt", "Don't Speak", "TR3Vdo5etCQ"),
    ("Oasis", "Wonderwall", "bx1Bh8ZvH84"),
    ("Oasis", "Don't Look Back in Anger", "cmpRLQZkTb8"),
    ("Blur", "Song 2", "SSbBvKaM6sk"),
    ("The Cranberries", "Zombie", "6Ejga4kJUts"),
    ("Backstreet Boys", "I Want It That Way", "4fndeDfaWCg"),
    ("The Killers", "Mr. Brightside", "gGdGFtwCNBE"),
    ("Muse", "Supermassive Black Hole", "Xsp3_a-PMTw"),
    ("Franz Ferdinand", "Take Me Out", "GhCXAiNT9_A"),
    ("Arctic Monkeys", "Do I Wanna Know?", "bpOSxM0rNPM"),
    ("Tame Impala", "The Less I Know the Better", "2SUwOgmvzK4"),
    ("Joy Division", "Love Will Tear Us Apart", "zuuObGsB0No"),
    ("Depeche Mode", "Enjoy the Silence", "aGSKrC7dGcY"),
    ("New Order", "Blue Monday", "FYH8DsU2WCk"),
    ("The Cure", "Friday I'm in Love", "mGgMZpGYiy8"),
    ("The Smiths", "How Soon Is Now?", "hnpILIIo9ek"),
    ("Duran Duran", "Hungry Like the Wolf", "oJL-lCzEXgI"),
    ("Tears for Fears", "Everybody Wants to Rule the World", "aGCdLKXNF3w"),
    ("Cyndi Lauper", "Girls Just Want to Have Fun", "PIb6AZdTr-A"),
    ("Eurythmics", "Sweet Dreams", "qeMFqkcPYcg"),
    ("Softcell", "Tainted Love", "XZVpR3Pk-r8"),
    ("The Police", "Every Breath You Take", "OMOGaugKpzs"),
    ("Blondie", "Heart of Glass", "WGU_4-5RaxU"),
    ("Fleetwood Mac", "Dreams", "mrZRURcb1cM"),
    ("Eagles", "Hotel California", "BciS5krYL80"),
    ("Led Zeppelin", "Stairway to Heaven", "QkF3oxziUI4"),
    ("Pink Floyd", "Comfortably Numb", "x-xTttimcNk"),
    ("AC/DC", "Back in Black", "pAgnJDJN4VA"),
    ("Metallica", "Enter Sandman", "CD-E-LDc384"),
    ("Black Sabbath", "Paranoid", "0qanF-91aJo"),
    ("Iron Maiden", "The Trooper", "X4bgXH3sJ2Q"),
    ("Mötley Crüe", "Girls, Girls, Girls", "d2XdmyBtCRQ"),
    ("Def Leppard", "Pour Some Sugar on Me", "0UIB9Y4OFPs"),
    ("Aerosmith", "I Don't Want to Miss a Thing", "JkK8g6FMEXE"),
    ("Bon Jovi", "Livin' on a Prayer", "lDK9QqIzhwk"),
    ("Van Halen", "Jump", "SwYN7mTi6HM"),
    ("U2", "One", "ftjEcrrf7r0"),
    ("Coldplay", "The Scientist", "RB-RcX5DS5A"),
    ("Coldplay", "Yellow", "yKNxeF4KMsY"),
]

# ── Popular seeds (well-known, high-view-count official videos) ───────────────
POPULAR_SEEDS = [
    ("Luis Fonsi ft. Daddy Yankee", "Despacito", "kJQP7kiw5Fk"),
    ("Ed Sheeran", "Shape of You", "JGwWNGJdvx8"),
    ("Ed Sheeran", "Perfect", "2Vv-BfVoq4g"),
    ("Wiz Khalifa ft. Charlie Puth", "See You Again", "RgKAFK5djSk"),
    ("Mark Ronson ft. Bruno Mars", "Uptown Funk", "OPf0YbXqDm0"),
    ("Bruno Mars", "24K Magic", "UqyT8IEBkvY"),
    ("PSY", "Gangnam Style", "9bZkp7q19f0"),
    ("Maroon 5", "Sugar", "09R8_2nJtjg"),
    ("Katy Perry", "Roar", "CevxZvSJLk8"),
    ("Katy Perry", "Firework", "QGJuMBdaqIw"),
    ("Taylor Swift", "Shake It Off", "nfWlot6h_JM"),
    ("Taylor Swift", "Blank Space", "e-ORhEE9VVg"),
    ("Adele", "Hello", "YQHsXMglC9A"),
    ("Adele", "Rolling in the Deep", "rYEDA3JcQqw"),
    ("The Weeknd", "Blinding Lights", "4NRXx6U8ABQ"),
    ("The Weeknd", "Starboy", "34Na4j8AVgA"),
    ("Imagine Dragons", "Believer", "7wtfhZwyrcc"),
    ("Imagine Dragons", "Radioactive", "ktvTqknDobU"),
    ("Billie Eilish", "Bad Guy", "DyDfgMOUjCI"),
    ("Billie Eilish", "Lovely", "V1Pl8CzNzCw"),
    ("Dua Lipa", "Levitating", "TUVcZfQe-Kw"),
    ("Dua Lipa", "Don't Start Now", "oygrmJFKYZY"),
    ("Harry Styles", "Watermelon Sugar", "E07s5ZYadZs"),
    ("Harry Styles", "As It Was", "H5v3kku4y6Q"),
    ("Post Malone", "Circles", "wXhTHyIgQ_U"),
    ("Post Malone", "Sunflower", "ApXoWvfEYVU"),
    ("Lizzo", "Juice", "XaCrQL_8eMY"),
    ("Doja Cat", "Say So", "pok8H_KF1FA"),
    ("Glass Animals", "Heat Waves", "mRD0-GxKHPk"),
    ("Linkin Park", "In the End", "eVTXPUF4Oz4"),
    ("Linkin Park", "Numb", "kXYiU_JCYtU"),
    ("LMFAO", "Party Rock Anthem", "KQ6zr6kCPj8"),
    ("Gotye", "Somebody That I Used to Know", "8UVNT4wvIGY"),
    ("Carly Rae Jepsen", "Call Me Maybe", "fWNaR-rxAic"),
    ("Pharrell Williams", "Happy", "ZbZSe6N_BXs"),
    ("Macklemore & Ryan Lewis", "Thrift Shop", "QK8mJJJvaes"),
    ("Lorde", "Royals", "nlcIKh6sBtc"),
    ("Hozier", "Take Me to Church", "PVjiKRfKpPI"),
    ("Passenger", "Let Her Go", "RBumgq5yVrA"),
    ("John Legend", "All of Me", "450p7goxZqg"),
    ("Sam Smith", "Stay with Me", "pB-5XG-DbAA"),
    ("Avicii", "Wake Me Up", "IcrbM1l_BoI"),
    ("Clean Bandit", "Rather Be", "m-M1AtrxztU"),
    ("Major Lazer & DJ Snake", "Lean On", "YqeW9_5kURI"),
    ("Shawn Mendes", "Stitches", "VbfpW0pbvaU"),
    ("Camila Cabello", "Havana", "BQ0mxQXmLsk"),
    ("Lady Gaga", "Bad Romance", "qrO4YZeyl0I"),
    ("Lady Gaga", "Poker Face", "bESGLojNYSo"),
    ("Rihanna", "Umbrella", "CvBfHwUxHIk"),
    ("Rihanna", "We Found Love", "tg00YEETFzg"),
    ("Drake", "Hotline Bling", "uxpDa-c-4Mc"),
    ("Travis Scott", "SICKO MODE", "6ONRf7h3Mdk"),
    ("Cardi B", "Bodak Yellow", "PEGccV-NOm8"),
    ("SZA", "Kill Bill", "hIiPErz2cms"),
    ("Olivia Rodrigo", "Drivers License", "ZmDBbnmKFnI"),
    ("Olivia Rodrigo", "Good 4 U", "gNi_6U5Pm_o"),
    ("Miley Cyrus", "Flowers", "G7KNmW9a75Y"),
    ("Miley Cyrus", "Wrecking Ball", "My2FRPA3Gf8"),
    ("Justin Bieber", "Sorry", "fRh_vgS2dFE"),
    ("Ariana Grande", "Thank U, Next", "gl1aHhXnN1k"),
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
    """Return list of {artist, count, has_5star, avg_rating, play_count} for artists in the library."""
    from app.models import VideoItem, PlaybackHistory
    from sqlalchemy import case

    rows = db.query(
        VideoItem.artist,
        func.count(VideoItem.id),
        func.max(case(
            (VideoItem.song_rating == 5, 1),
            else_=0,
        )),
        func.avg(case(
            (VideoItem.song_rating_set == True, VideoItem.song_rating),  # noqa: E712
            else_=None,
        )),
    ).filter(
        VideoItem.artist.isnot(None),
        VideoItem.artist != "",
    ).group_by(VideoItem.artist).all()

    # Get play counts per artist from PlaybackHistory
    play_counts: dict[str, int] = {}
    ph_rows = (
        db.query(VideoItem.artist, func.count(PlaybackHistory.id))
        .join(PlaybackHistory, PlaybackHistory.video_id == VideoItem.id)
        .filter(VideoItem.artist.isnot(None), VideoItem.artist != "")
        .group_by(VideoItem.artist)
        .all()
    )
    for artist_name, count in ph_rows:
        play_counts[artist_name] = count

    return [
        {
            "artist": r[0],
            "count": r[1],
            "has_5star": bool(r[2]),
            "avg_rating": round(r[3], 2) if r[3] is not None else None,
            "play_count": play_counts.get(r[0], 0),
        }
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


def _normalize_title(s: str) -> str:
    """Normalize a string for fuzzy title comparison."""
    s = s.lower().strip()
    # Remove common suffixes/noise
    for noise in ("(official video)", "(official music video)",
                  "(official hd video)", "(music video)", "[official video]",
                  "(lyric video)", "(lyrics)", "(audio)", "(visualizer)",
                  "(remastered)", "[remastered]", "(hd remaster)"):
        s = s.replace(noise, "")
    # Strip punctuation except apostrophes inside words
    s = re.sub(r"[^\w\s']", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _get_library_titles(db: Session) -> set[tuple[str, str]]:
    """Return set of normalized (artist, title) tuples from the library.

    Used to detect when a suggested video already exists in the library
    even if the YouTube video ID differs (e.g. remastered upload).
    """
    from app.models import VideoItem
    rows = db.query(VideoItem.artist, VideoItem.title).filter(
        VideoItem.artist.isnot(None),
        VideoItem.title.isnot(None),
    ).all()
    return {
        (_normalize_title(r[0]), _normalize_title(r[1]))
        for r in rows
        if r[0] and r[1]
    }


# ── yt-dlp dynamic search ────────────────────────────────────────────────────

def _ytdlp_search(query: str, max_results: int = 10,
                  category: str = "popular") -> list[RecommendationCandidate]:
    """Run a yt-dlp search and return RecommendationCandidate objects.

    Uses --flat-playlist for speed (metadata-only, no download).
    Gracefully returns [] if yt-dlp is not available or the search fails.
    """
    import subprocess
    import math
    from app.subprocess_utils import HIDE_WINDOW

    try:
        from app.config import get_settings
        settings = get_settings()
        ytdlp = settings.resolved_ytdlp
    except Exception:
        return []

    search_url = f"ytsearch{max_results}:{query}"
    cmd = [ytdlp, "--dump-json", "--flat-playlist", "--no-download",
           "--no-warnings", search_url]

    try:
        import os
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            cwd=os.path.dirname(ytdlp) if os.path.dirname(ytdlp) else None,
            **HIDE_WINDOW,
        )
    except Exception as e:
        logger.debug(f"yt-dlp search failed for '{query}': {e}")
        return []

    if result.returncode != 0:
        return []

    candidates = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        try:
            info = json.loads(line)
        except json.JSONDecodeError:
            continue

        vid_id = info.get("id", "")
        if not vid_id:
            continue

        title = info.get("title", "")
        channel = info.get("channel", "") or info.get("uploader", "")
        view_count = info.get("view_count")
        duration = info.get("duration")

        # Compute a popularity score from view count
        pop = 0.5
        if view_count and view_count > 0:
            log_views = math.log10(max(view_count, 1))
            pop = min(1.0, max(0.1, (log_views - 3) / 7))  # 1K→0.1, 10B→1.0

        # Parse artist from title (common format: "Artist - Title")
        artist_parsed = ""
        if " - " in title:
            artist_parsed = title.split(" - ", 1)[0].strip()

        candidates.append(RecommendationCandidate(
            provider="youtube",
            provider_video_id=vid_id,
            url=f"https://www.youtube.com/watch?v={vid_id}",
            title=title,
            artist=artist_parsed,
            channel=channel,
            thumbnail_url=f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg",
            category=category,
            duration_seconds=duration,
            view_count=view_count,
            popularity_score=pop,
            freshness_score=0.5,
            reasons=[f"Found via YouTube search: {query}"],
        ))

    return candidates


def _search_artist_videos(artist: str, category: str,
                          exclude_ids: set, exclude_titles: set,
                          max_results: int = 5) -> list[RecommendationCandidate]:
    """Search YouTube for official music videos by a specific artist.

    Filters out videos already in the exclude sets (library/dismissed).
    """
    query = f"{artist} official music video"
    raw = _ytdlp_search(query, max_results=max_results, category=category)

    filtered = []
    for c in raw:
        if c.provider_video_id in exclude_ids:
            continue
        # Override parsed artist with the known artist name
        c.artist = artist
        # Title-based dedup
        raw_title = c.title
        if " - " in raw_title:
            raw_title = raw_title.split(" - ", 1)[1]
        raw_title = re.sub(r"\s*\(.*?\)\s*$", "", raw_title).strip()
        if (_normalize_title(artist), _normalize_title(raw_title)) in exclude_titles:
            continue
        filtered.append(c)

    return filtered


# ── Source strategies ─────────────────────────────────────────────────────────
# Each returns a list of RecommendationCandidate objects.
# These are the pluggable candidate generators.

def _generate_famous_candidates(db: Session, limit: int = 20) -> list[RecommendationCandidate]:
    """Generate candidates from the curated famous videos seed list, then yt-dlp search."""
    library_ids = _get_library_video_ids(db)
    library_titles = _get_library_titles(db)
    dismissed_ids = _get_dismissed_provider_ids(db)
    exclude_ids = library_ids | dismissed_ids
    candidates = []

    for artist, title, vid_id in FAMOUS_SEEDS:
        if vid_id in exclude_ids:
            continue
        if (_normalize_title(artist), _normalize_title(title)) in library_titles:
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

    # If seeds didn't fill the limit, search for classic music videos
    if len(candidates) < limit:
        seen_ids = {c.provider_video_id for c in candidates}
        search_queries = [
            "greatest music videos of all time official",
            "iconic music videos official",
            "best music videos ever made official",
            "classic music video official",
        ]
        random.shuffle(search_queries)
        for query in search_queries[:2]:
            if len(candidates) >= limit:
                break
            results = _ytdlp_search(query, max_results=10, category="famous")
            for c in results:
                if c.provider_video_id in exclude_ids or c.provider_video_id in seen_ids:
                    continue
                if c.artist and c.title:
                    raw_title = c.title.split(" - ", 1)[1] if " - " in c.title else c.title
                    raw_title = re.sub(r"\s*\(.*?\)\s*$", "", raw_title).strip()
                    if (_normalize_title(c.artist), _normalize_title(raw_title)) in library_titles:
                        continue
                seen_ids.add(c.provider_video_id)
                c.reasons = ["Iconic/classic music video — discovered via search"]
                candidates.append(c)
                if len(candidates) >= limit:
                    break

    return candidates


def _generate_popular_candidates(db: Session, limit: int = 20) -> list[RecommendationCandidate]:
    """Generate candidates from the popular videos seed list, then yt-dlp search."""
    library_ids = _get_library_video_ids(db)
    library_titles = _get_library_titles(db)
    dismissed_ids = _get_dismissed_provider_ids(db)
    exclude_ids = library_ids | dismissed_ids
    candidates = []

    for artist, title, vid_id in POPULAR_SEEDS:
        if vid_id in exclude_ids:
            continue
        if (_normalize_title(artist), _normalize_title(title)) in library_titles:
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

    # If seeds didn't fill the limit, search YouTube dynamically
    if len(candidates) < limit:
        seen_ids = {c.provider_video_id for c in candidates}
        search_queries = [
            "most popular music videos official",
            "top music videos official video",
            "best official music videos",
            "hit music video official",
        ]
        random.shuffle(search_queries)
        for query in search_queries[:2]:
            if len(candidates) >= limit:
                break
            results = _ytdlp_search(query, max_results=10, category="popular")
            for c in results:
                if c.provider_video_id in exclude_ids or c.provider_video_id in seen_ids:
                    continue
                if c.artist and c.title:
                    raw_title = c.title.split(" - ", 1)[1] if " - " in c.title else c.title
                    raw_title = re.sub(r"\s*\(.*?\)\s*$", "", raw_title).strip()
                    if (_normalize_title(c.artist), _normalize_title(raw_title)) in library_titles:
                        continue
                seen_ids.add(c.provider_video_id)
                candidates.append(c)
                if len(candidates) >= limit:
                    break

    return candidates


def _generate_by_artist_candidates(db: Session, limit: int = 20) -> list[RecommendationCandidate]:
    """Suggest notable videos by artists already in the user's library.

    Strategy: first check seeds matching library artists, then use yt-dlp
    search to dynamically discover videos by library artists.
    """
    min_owned = _get_setting(db, "nv_min_owned_for_artist_rec", "1", "int")
    max_per_artist = _get_setting(db, "nv_max_recs_per_artist", "5", "int")
    library_artists = _get_library_artists(db)
    library_ids = _get_library_video_ids(db)
    library_titles = _get_library_titles(db)
    dismissed_ids = _get_dismissed_provider_ids(db)
    exclude_ids = library_ids | dismissed_ids
    candidates = []

    # Build artist→seeds lookup
    all_seeds = FAMOUS_SEEDS + POPULAR_SEEDS
    artist_seeds: dict[str, list] = {}
    for a, t, vid in all_seeds:
        artist_seeds.setdefault(a.lower(), []).append((a, t, vid))

    # Phase 1: seed-based matches (fast, no network)
    for info in library_artists:
        if info["count"] < min_owned:
            continue
        artist_lower = info["artist"].lower()
        seeds = artist_seeds.get(artist_lower, [])
        added = 0
        for artist, title, vid_id in seeds:
            if vid_id in exclude_ids:
                continue
            if (_normalize_title(artist), _normalize_title(title)) in library_titles:
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

    # Phase 2: yt-dlp search for library artists (dynamic discovery)
    if len(candidates) < limit:
        seen_ids = {c.provider_video_id for c in candidates}
        # Eligible artists: have enough library videos, shuffle for variety
        eligible_artists = [
            info for info in library_artists
            if info["count"] >= min_owned
        ]
        random.shuffle(eligible_artists)
        # Limit searches to keep refresh fast (max 8 artist searches)
        max_searches = min(8, len(eligible_artists))
        for info in eligible_artists[:max_searches]:
            if len(candidates) >= limit:
                break
            artist_name = info["artist"]
            results = _search_artist_videos(
                artist_name, "by_artist",
                exclude_ids | seen_ids, library_titles,
                max_results=max_per_artist,
            )
            for c in results:
                if c.provider_video_id in seen_ids:
                    continue
                c.reasons = [f"You have {info['count']} {artist_name} videos — discovered via search"]
                seen_ids.add(c.provider_video_id)
                candidates.append(c)
                if len(candidates) >= limit:
                    break

    return candidates[:limit]


def _generate_taste_candidates(db: Session, limit: int = 20) -> list[RecommendationCandidate]:
    """Generate taste-based recommendations from user preferences.

    Strategy (multi-signal):
      1. Artists with 5-star ratings → highest priority
      2. Artists rated 3-4 stars → medium priority
      3. Most-played artists (by PlaybackHistory count) → engagement signal
      4. Genre affinity: find top genres, search for videos in those genres
    """
    from app.models import VideoItem, PlaybackHistory, Genre, video_genres
    use_ratings = _get_setting(db, "nv_use_ratings", "true", "bool")

    library_ids = _get_library_video_ids(db)
    library_titles = _get_library_titles(db)
    dismissed_ids = _get_dismissed_provider_ids(db)
    exclude_ids = library_ids | dismissed_ids
    candidates = []

    # ── Build preference-ranked artist list ──────────────────────────────────
    # Score each artist: rating weight + play count weight
    artist_scores: dict[str, float] = {}
    artist_reasons: dict[str, str] = {}

    if use_ratings:
        # Tier 1: 5-star artists
        for (name,) in db.query(VideoItem.artist).filter(
            VideoItem.song_rating == 5,
            VideoItem.song_rating_set == True,  # noqa: E712
            VideoItem.artist.isnot(None),
        ).distinct().all():
            if name:
                artist_scores[name] = artist_scores.get(name, 0) + 1.0
                artist_reasons[name] = "5-star rated"

        # Tier 2: 4-star artists
        for (name,) in db.query(VideoItem.artist).filter(
            VideoItem.song_rating == 4,
            VideoItem.song_rating_set == True,  # noqa: E712
            VideoItem.artist.isnot(None),
        ).distinct().all():
            if name and name not in artist_scores:
                artist_scores[name] = artist_scores.get(name, 0) + 0.6
                artist_reasons.setdefault(name, "4-star rated")

        # Tier 3: 3-star artists (only if we still need more)
        if len(artist_scores) < 8:
            for (name,) in db.query(VideoItem.artist).filter(
                VideoItem.song_rating == 3,
                VideoItem.song_rating_set == True,  # noqa: E712
                VideoItem.artist.isnot(None),
            ).distinct().all():
                if name and name not in artist_scores:
                    artist_scores[name] = artist_scores.get(name, 0) + 0.3
                    artist_reasons.setdefault(name, "3-star rated")

    # Engagement signal: most-played artists
    play_rows = (
        db.query(VideoItem.artist, func.count(PlaybackHistory.id))
        .join(PlaybackHistory, PlaybackHistory.video_id == VideoItem.id)
        .filter(VideoItem.artist.isnot(None), VideoItem.artist != "")
        .group_by(VideoItem.artist)
        .order_by(func.count(PlaybackHistory.id).desc())
        .limit(20)
        .all()
    )
    for artist_name, play_count in play_rows:
        if play_count >= 3:
            bonus = min(0.5, play_count * 0.05)
            artist_scores[artist_name] = artist_scores.get(artist_name, 0) + bonus
            if artist_name not in artist_reasons:
                artist_reasons[artist_name] = f"played {play_count} times"

    if not artist_scores:
        return []

    # Sort by preference score descending
    ranked_artists = sorted(artist_scores.items(), key=lambda x: x[1], reverse=True)
    fav_artist_lower = {name.lower() for name, _ in ranked_artists}

    # Phase 1: seed-based matches for preference-ranked artists
    all_seeds = FAMOUS_SEEDS + POPULAR_SEEDS
    for artist, title, vid_id in all_seeds:
        if artist.lower() in fav_artist_lower:
            if vid_id in exclude_ids:
                continue
            if (_normalize_title(artist), _normalize_title(title)) in library_titles:
                continue
            reason_tag = artist_reasons.get(artist, "your preferences")
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
                reasons=[f"Based on your {reason_tag} {artist} videos"],
            ))
            if len(candidates) >= limit:
                break

    # Phase 2: yt-dlp search for preference-ranked artists (top 6, not just 3)
    if len(candidates) < limit:
        seen_ids = {c.provider_video_id for c in candidates}
        max_artist_searches = min(6, len(ranked_artists))
        for artist_name, _ in ranked_artists[:max_artist_searches]:
            if len(candidates) >= limit:
                break
            reason_tag = artist_reasons.get(artist_name, "your preferences")
            results = _search_artist_videos(
                artist_name, "taste",
                exclude_ids | seen_ids, library_titles,
                max_results=5,
            )
            for c in results:
                if c.provider_video_id in seen_ids:
                    continue
                c.reasons = [f"Based on your {reason_tag} {artist_name} videos"]
                seen_ids.add(c.provider_video_id)
                candidates.append(c)
                if len(candidates) >= limit:
                    break

    # Phase 3: genre affinity — find top genres and search for videos in them
    if len(candidates) < limit:
        seen_ids = {c.provider_video_id for c in candidates}
        # Get top genres from the user's highest-rated / most-played videos
        top_genres = (
            db.query(Genre.name, func.count(video_genres.c.video_id))
            .join(video_genres, video_genres.c.genre_id == Genre.id)
            .join(VideoItem, VideoItem.id == video_genres.c.video_id)
            .filter(
                or_(
                    VideoItem.song_rating >= 3,
                    VideoItem.id.in_(
                        db.query(PlaybackHistory.video_id).distinct()
                    ),
                )
            )
            .group_by(Genre.name)
            .order_by(func.count(video_genres.c.video_id).desc())
            .limit(3)
            .all()
        )
        for genre_name, _ in top_genres:
            if len(candidates) >= limit:
                break
            query = f"{genre_name} official music video"
            results = _ytdlp_search(query, max_results=5, category="taste")
            for c in results:
                if c.provider_video_id in exclude_ids or c.provider_video_id in seen_ids:
                    continue
                c.reasons = [f"Matches your favourite genre: {genre_name}"]
                seen_ids.add(c.provider_video_id)
                candidates.append(c)
                if len(candidates) >= limit:
                    break

    return candidates


def _generate_new_candidates(db: Session, limit: int = 10) -> list[RecommendationCandidate]:
    """Discover recent/new official music video releases via yt-dlp search."""
    library_ids = _get_library_video_ids(db)
    library_titles = _get_library_titles(db)
    dismissed_ids = _get_dismissed_provider_ids(db)
    exclude_ids = library_ids | dismissed_ids
    candidates = []
    seen_ids: set[str] = set()

    search_queries = [
        "new official music video 2025",
        "new music video premiere",
        "latest official music video",
        "official music video new release",
    ]
    random.shuffle(search_queries)

    for query in search_queries[:2]:
        if len(candidates) >= limit:
            break
        results = _ytdlp_search(query, max_results=10, category="new")
        for c in results:
            if c.provider_video_id in exclude_ids or c.provider_video_id in seen_ids:
                continue
            if c.artist and c.title:
                raw_title = c.title.split(" - ", 1)[1] if " - " in c.title else c.title
                raw_title = re.sub(r"\s*\(.*?\)\s*$", "", raw_title).strip()
                if (_normalize_title(c.artist), _normalize_title(raw_title)) in library_titles:
                    continue
            seen_ids.add(c.provider_video_id)
            c.freshness_score = 0.80
            candidates.append(c)
            if len(candidates) >= limit:
                break

    return candidates


def _generate_rising_candidates(db: Session, limit: int = 10) -> list[RecommendationCandidate]:
    """Discover trending/rising music videos via yt-dlp search."""
    library_ids = _get_library_video_ids(db)
    library_titles = _get_library_titles(db)
    dismissed_ids = _get_dismissed_provider_ids(db)
    exclude_ids = library_ids | dismissed_ids
    candidates = []
    seen_ids: set[str] = set()

    search_queries = [
        "trending official music video",
        "viral music video official",
        "music video trending now",
    ]
    random.shuffle(search_queries)

    for query in search_queries[:2]:
        if len(candidates) >= limit:
            break
        results = _ytdlp_search(query, max_results=10, category="rising")
        for c in results:
            if c.provider_video_id in exclude_ids or c.provider_video_id in seen_ids:
                continue
            if c.artist and c.title:
                raw_title = c.title.split(" - ", 1)[1] if " - " in c.title else c.title
                raw_title = re.sub(r"\s*\(.*?\)\s*$", "", raw_title).strip()
                if (_normalize_title(c.artist), _normalize_title(raw_title)) in library_titles:
                    continue
            seen_ids.add(c.provider_video_id)
            c.trend_score = 0.80
            candidates.append(c)
            if len(candidates) >= limit:
                break

    return candidates


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

    # Per-category count overrides: nv_famous_count, nv_popular_count, etc.
    CATEGORY_COUNT_KEYS = {
        "famous": "nv_famous_count",
        "popular": "nv_popular_count",
        "rising": "nv_rising_count",
        "new": "nv_new_count",
    }
    default_limit = _get_setting(db, "nv_videos_per_category", "15", "int")
    count_key = CATEGORY_COUNT_KEYS.get(category)
    if count_key:
        limit = _get_setting(db, count_key, str(default_limit), "int")
    else:
        limit = default_limit
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

    # Also filter by library contents (video ID + title match)
    library_ids = _get_library_video_ids(db)
    library_titles = _get_library_titles(db)

    cart_count = db.query(SuggestedVideoCartItem).count()
    cart_video_ids = {r[0] for r in db.query(SuggestedVideoCartItem.suggested_video_id).all()}

    default_limit = _get_setting(db, "nv_videos_per_category", "15", "int")
    CATEGORY_COUNT_KEYS = {
        "famous": "nv_famous_count",
        "popular": "nv_popular_count",
        "rising": "nv_rising_count",
        "new": "nv_new_count",
    }

    result: dict = {"categories": {}, "cart_count": cart_count}

    for cat in CATEGORIES:
        count_key = CATEGORY_COUNT_KEYS.get(cat)
        limit = _get_setting(db, count_key, str(default_limit), "int") if count_key else default_limit

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
            # Skip if already in library (video ID or title match)
            if v.provider_video_id in library_ids:
                continue
            if v.artist and v.title:
                raw_title = v.title
                # Strip "Artist - " prefix if present for matching
                if " - " in raw_title:
                    raw_title = raw_title.split(" - ", 1)[1]
                # Remove "(Official Video)" etc.
                raw_title = re.sub(r"\s*\(.*?\)\s*$", "", raw_title).strip()
                if (_normalize_title(v.artist), _normalize_title(raw_title)) in library_titles:
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
                if v.provider_video_id in library_ids:
                    continue
                if v.artist and v.title:
                    raw_title = v.title
                    if " - " in raw_title:
                        raw_title = raw_title.split(" - ", 1)[1]
                    raw_title = re.sub(r"\s*\(.*?\)\s*$", "", raw_title).strip()
                    if (_normalize_title(v.artist), _normalize_title(raw_title)) in library_titles:
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
