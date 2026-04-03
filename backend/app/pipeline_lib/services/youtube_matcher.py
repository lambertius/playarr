# AUTO-SEPARATED from services/youtube_matcher.py for pipeline_lib pipeline
# This file is independent — changes here do NOT affect the other pipeline.
"""
YouTube Source Matcher — Search YouTube for source videos matching imported content.

Uses yt-dlp search to find candidate YouTube videos, then scores them
against known metadata (artist, title, duration) to find the best match.

Scoring signals:
  - Title similarity (SequenceMatcher)
  - Official title keywords ("official video", "official music video", etc.)
  - Trusted channel patterns (Vevo, topic channels, etc.)
  - Artist-name channel matching (channel name resembles artist)
  - Negative penalties for fan/unofficial markers ("fan edit", "cover", etc.)
  - Duration matching (when available)
"""
import json
import logging
import os
import re
import subprocess

from app.subprocess_utils import HIDE_WINDOW
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import List, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

# Title keywords that indicate an official upload — boost title_score
_OFFICIAL_TITLE_PATTERNS: List[tuple[re.Pattern, float]] = [
    (re.compile(r"\bofficial\s+music\s+video\b", re.I), 0.15),
    (re.compile(r"\bofficial\s+video\b", re.I), 0.12),
    (re.compile(r"\bofficial\s+hd\s+video\b", re.I), 0.12),
    (re.compile(r"\bofficial\s+audio\b", re.I), 0.08),
    (re.compile(r"\bofficial\s+visuali[sz]er\b", re.I), 0.06),
    (re.compile(r"\bofficial\s+lyric\s+video\b", re.I), 0.05),
    (re.compile(r"\(official\)", re.I), 0.10),
    (re.compile(r"\[official\]", re.I), 0.10),
]

# Trusted channel name suffixes/patterns — boost title_score
_TRUSTED_CHANNEL_PATTERNS: List[tuple[re.Pattern, float]] = [
    (re.compile(r"VEVO$", re.I), 0.15),
    (re.compile(r"\s*-\s*Topic$", re.I), 0.08),         # YouTube auto-gen topic channels
    (re.compile(r"Official$", re.I), 0.10),              # "ArtistOfficial"
    (re.compile(r"Official\s*Channel$", re.I), 0.10),
    (re.compile(r"Official\s*Music$", re.I), 0.08),
    (re.compile(r"^Warner\s*Music", re.I), 0.06),
    (re.compile(r"^Universal\s*Music", re.I), 0.06),
    (re.compile(r"^Sony\s*Music", re.I), 0.06),
    (re.compile(r"^Atlantic\s*Records", re.I), 0.06),
    (re.compile(r"^Interscope\s*Records", re.I), 0.06),
    (re.compile(r"^Republic\s*Records", re.I), 0.06),
    (re.compile(r"^Capitol\s*Records", re.I), 0.06),
    (re.compile(r"^Def\s*Jam", re.I), 0.06),
    (re.compile(r"^Island\s*Records", re.I), 0.06),
    (re.compile(r"^RCA\s*Records", re.I), 0.06),
    (re.compile(r"^Columbia\s*Records", re.I), 0.06),
    (re.compile(r"^Epic\s*Records", re.I), 0.06),
    (re.compile(r"^Polydor", re.I), 0.06),
    (re.compile(r"^Parlophone", re.I), 0.06),
    (re.compile(r"^XL\s*Recordings", re.I), 0.06),
    (re.compile(r"^Sub\s*Pop", re.I), 0.06),
    (re.compile(r"^Domino\s*Recording", re.I), 0.06),
    (re.compile(r"^4AD", re.I), 0.06),
    (re.compile(r"^Warp\s*Records", re.I), 0.06),
    (re.compile(r"^Ninja\s*Tune", re.I), 0.06),
    (re.compile(r"^Ed\s*Banger", re.I), 0.06),
    (re.compile(r"^Spinnin['']?\s*Records", re.I), 0.06),
    (re.compile(r"^Monstercat", re.I), 0.06),
    (re.compile(r"^Armada\s*Music", re.I), 0.06),
    (re.compile(r"^Ultra\s*Records", re.I), 0.06),
    (re.compile(r"^Mad\s*Decent", re.I), 0.06),
    (re.compile(r"^OWSLA", re.I), 0.06),
]

# Negative markers in the video title — penalise title_score
_NEGATIVE_TITLE_PATTERNS: List[tuple[re.Pattern, float]] = [
    (re.compile(r"\bfan\s*edit\b", re.I), -0.20),
    (re.compile(r"\bfan\s*made\b", re.I), -0.20),
    (re.compile(r"\bfan\s*video\b", re.I), -0.15),
    (re.compile(r"\bfan\s*version\b", re.I), -0.15),
    (re.compile(r"\bunofficial\b", re.I), -0.15),
    (re.compile(r"\bbootleg\b", re.I), -0.15),
    (re.compile(r"\bcover\b", re.I), -0.12),
    (re.compile(r"\btribute\b", re.I), -0.12),
    (re.compile(r"\bremake\b", re.I), -0.10),
    (re.compile(r"\bparody\b", re.I), -0.20),
    (re.compile(r"\breaction\b", re.I), -0.20),
    (re.compile(r"\blyric\s*video\b", re.I), -0.08),
    (re.compile(r"\blyrics\b", re.I), -0.05),
    (re.compile(r"\baudio\s*only\b", re.I), -0.05),
    (re.compile(r"\bslowed\b", re.I), -0.12),
    (re.compile(r"\breverb\b", re.I), -0.10),
    (re.compile(r"\bsped\s*up\b", re.I), -0.12),
    (re.compile(r"\bnightcore\b", re.I), -0.15),
    (re.compile(r"\b8\s*-?\s*bit\b", re.I), -0.15),
    (re.compile(r"\bremix\b", re.I), -0.08),
    (re.compile(r"\bmeme\b", re.I), -0.10),
    (re.compile(r"\btutorial\b", re.I), -0.20),
    (re.compile(r"\bdrum\s*cover\b", re.I), -0.15),
    (re.compile(r"\bguitar\s*cover\b", re.I), -0.15),
    (re.compile(r"\bpiano\s*cover\b", re.I), -0.15),
    (re.compile(r"\bbass\s*cover\b", re.I), -0.15),
    (re.compile(r"\binstrumental\b", re.I), -0.08),
    (re.compile(r"\bkaraoke\b", re.I), -0.15),
    (re.compile(r"\blivetstream\b|\blive\s*stream\b", re.I), -0.10),
]


@dataclass
class YouTubeCandidate:
    """A YouTube search result candidate."""
    video_id: str
    url: str
    title: str
    channel: str
    duration_seconds: Optional[int] = None
    view_count: Optional[int] = None
    upload_date: Optional[str] = None
    # Matching scores
    title_score: float = 0.0
    channel_score: float = 0.0
    duration_score: float = 0.0
    overall_score: float = 0.0


def _normalise_for_compare(text: str) -> str:
    """Lowercase, strip non-alphanumeric (keep spaces) for loose comparison."""
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


# Regex to strip common tag/marker suffixes from titles before SequenceMatcher.
# This prevents "(Official Audio)" etc. from inflating the character count and
# lowering the similarity ratio when compared against a clean search target.
_TITLE_STRIP_RE = re.compile(
    r"""
      \s*[\(\[\|–—-]\s*        # opener: ( [ | – — -
      (?:official\s+)?         # optional "official"
      (?:music\s+video|video|audio|hd\s+video|visuali[sz]er|lyric\s+video
        |lyrics?|remaster(?:ed)?|full\s+(?:song|album)|hd|hq|4k|mv)
      \s*[\)\]]?               # optional closer: ) ]
    """,
    re.I | re.X,
)


def _strip_title_tags(title: str) -> str:
    """Remove official/quality/format markers from a video title for comparison."""
    stripped = _TITLE_STRIP_RE.sub("", title)
    # Also strip year tags like (2005), [1999]
    stripped = re.sub(r"\s*[\(\[]\d{4}[\)\]]", "", stripped)
    return stripped.strip()


def _artist_channel_score(artist: str, channel: str) -> float:
    """
    Score how well a channel name matches the artist.

    Checks: exact containment, stripped-punctuation containment,
    and common variations like 'ArtistVEVO', 'ArtistOfficial',
    'ArtistMusic'.
    """
    if not artist or not channel:
        return 0.0

    artist_lower = artist.lower()
    channel_lower = channel.lower()

    # Exact containment
    if artist_lower in channel_lower:
        return 0.15

    # Strip non-alphanumeric for fuzzy containment:
    # "Foo Fighters" matches "foofighters", "FooFightersVEVO"
    artist_clean = re.sub(r"[^a-z0-9]", "", artist_lower)
    channel_clean = re.sub(r"[^a-z0-9]", "", channel_lower)

    if len(artist_clean) >= 3 and artist_clean in channel_clean:
        return 0.12

    # SequenceMatcher on cleaned forms for partial matches
    sim = SequenceMatcher(None, artist_clean, channel_clean).ratio()
    if sim >= 0.8:
        return 0.10

    return 0.0


def search_youtube(
    artist: str,
    title: str,
    duration_seconds: Optional[int] = None,
    max_results: int = 5,
    duration_tolerance_pct: float = 0.15,
) -> List[YouTubeCandidate]:
    """
    Search YouTube for a music video matching the given artist/title.

    Args:
        artist: Artist name.
        title: Song/video title.
        duration_seconds: Known video duration for matching (from file or NFO).
        max_results: Maximum search results to return.
        duration_tolerance_pct: Allowed duration difference as a fraction (0.15 = 15%).

    Returns:
        List of candidates sorted by overall_score (descending).
    """
    query = f"{artist} - {title} music video"
    search_url = f"ytsearch{max_results}:{query}"

    settings = get_settings()
    ytdlp = settings.resolved_ytdlp

    cmd = [
        ytdlp,
        "--dump-json",
        "--flat-playlist",
        "--no-download",
        "--no-warnings",
        search_url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=os.path.dirname(ytdlp) if os.path.dirname(ytdlp) else None,
            **HIDE_WINDOW,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"YouTube search timed out for: {query}")
        return []
    except Exception as e:
        logger.warning(f"YouTube search failed for: {query}: {e}")
        return []

    if result.returncode != 0:
        logger.warning(f"yt-dlp search returned code {result.returncode}: {result.stderr[:200]}")
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

        candidate = YouTubeCandidate(
            video_id=vid_id,
            url=f"https://www.youtube.com/watch?v={vid_id}",
            title=info.get("title", ""),
            channel=info.get("channel", "") or info.get("uploader", ""),
            duration_seconds=info.get("duration"),
            view_count=info.get("view_count"),
            upload_date=info.get("upload_date"),
        )

        # --- Title similarity (base score) ---
        # Strip official/tag markers so that "(Official Audio)" etc. don't
        # inflate character count and lower SequenceMatcher ratio compared to
        # clean re-uploads.
        search_target = _strip_title_tags(f"{artist} - {title}").lower()
        candidate_title_stripped = _strip_title_tags(candidate.title).lower()
        candidate.title_score = SequenceMatcher(
            None, search_target, candidate_title_stripped
        ).ratio()

        # --- Official title keyword boost ---
        # Only take the best matching pattern (no stacking)
        best_official_boost = 0.0
        for pat, boost in _OFFICIAL_TITLE_PATTERNS:
            if pat.search(candidate.title):
                best_official_boost = max(best_official_boost, boost)
        candidate.title_score = min(1.0, candidate.title_score + best_official_boost)

        # --- Channel authority scoring ---
        # Trusted channel patterns (Vevo, labels, etc.)
        best_channel_boost = 0.0
        for pat, boost in _TRUSTED_CHANNEL_PATTERNS:
            if pat.search(candidate.channel or ""):
                best_channel_boost = max(best_channel_boost, boost)

        # Artist-name channel matching
        artist_ch_boost = _artist_channel_score(artist, candidate.channel or "")
        # Take the better of trusted-pattern or artist-match (no stacking)
        candidate.channel_score = max(best_channel_boost, artist_ch_boost)
        candidate.title_score = min(1.0, candidate.title_score + candidate.channel_score)

        # --- Negative markers penalty ---
        for pat, penalty in _NEGATIVE_TITLE_PATTERNS:
            if pat.search(candidate.title):
                candidate.title_score = max(0.0, candidate.title_score + penalty)

        # --- Duration matching ---
        if duration_seconds and candidate.duration_seconds:
            diff = abs(duration_seconds - candidate.duration_seconds)
            tolerance = duration_seconds * duration_tolerance_pct
            if diff <= tolerance:
                # Linear scale: perfect match = 1.0, at tolerance = 0.5
                candidate.duration_score = 1.0 - (diff / (tolerance * 2))
            elif diff <= tolerance * 2:
                candidate.duration_score = 0.25
            else:
                candidate.duration_score = 0.0
        else:
            # No duration to compare — neutral score
            candidate.duration_score = 0.5

        # --- View count boost (small tiebreaker) ---
        view_boost = 0.0
        if candidate.view_count and candidate.view_count > 0:
            import math
            # log10 scale: 1M views = 6, 100M = 8, 1B = 9
            log_views = math.log10(candidate.view_count)
            # Small boost: 0 at ≤10k, up to 0.05 at 100M+
            if log_views > 4:  # > 10,000 views
                view_boost = min(0.05, (log_views - 4) * 0.0125)

        # --- Overall score: weighted combination ---
        candidate.overall_score = (
            candidate.title_score * 0.55 +
            candidate.duration_score * 0.40 +
            view_boost * 1.0  # Already scaled 0–0.05
        )

        candidates.append(candidate)

    # Sort by overall score descending
    candidates.sort(key=lambda c: c.overall_score, reverse=True)

    # Debug logging: show all candidates and their scores
    if candidates:
        logger.info(f"YouTube search for '{artist} - {title}': {len(candidates)} candidates")
        for i, c in enumerate(candidates):
            logger.info(
                f"  #{i+1} [{c.video_id}] \"{c.title}\" | ch=\"{c.channel}\" | "
                f"title={c.title_score:.3f} ch={c.channel_score:.3f} "
                f"dur={c.duration_score:.3f} views={c.view_count or 0} "
                f"overall={c.overall_score:.3f}"
            )
    return candidates


def find_best_youtube_match(
    artist: str,
    title: str,
    duration_seconds: Optional[int] = None,
    min_confidence: float = 0.6,
) -> Optional[YouTubeCandidate]:
    """
    Search YouTube and return the best match above the confidence threshold.

    Returns None if no match meets the threshold.
    """
    candidates = search_youtube(
        artist=artist,
        title=title,
        duration_seconds=duration_seconds,
        max_results=5,
    )

    if not candidates:
        return None

    best = candidates[0]
    if best.overall_score >= min_confidence:
        logger.info(f"Best match for '{artist} - {title}': {best.url} (score={best.overall_score:.3f})")
        return best

    logger.info(f"No match above threshold {min_confidence} for '{artist} - {title}' (best={best.overall_score:.3f})")
    return None
