"""
Mismatch Detection — Pre-AI heuristic validation of scraped metadata.

Runs cheap, fast checks to detect obviously incorrect metadata BEFORE
calling the AI provider, saving API costs and providing transparent signals.

Heuristic signals:
1. Title comparison    — fuzzy match: video title vs scraped title vs DB title
2. Artist comparison   — channel name vs artist metadata
3. Duration comparison — video duration vs typical music video length (2–8 min)
4. Keyword detection   — "official music video", "lyrics", "live", "cover", etc.
5. Channel trust       — verified / VEVO / label vs unknown uploader

Each signal produces a score 0.0–1.0 (higher = more suspicious).
A weighted combination gives the overall mismatch_score.

If mismatch_score > threshold (default 0.4), the system:
- Flags the video as suspicious
- Forces AI verification before auto-apply
- Shows a warning in the UI
"""
import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Weights for each signal (must sum to ~1.0)
SIGNAL_WEIGHTS = {
    "title_mismatch": 0.25,
    "artist_mismatch": 0.30,
    "duration_mismatch": 0.15,
    "keyword_flags": 0.15,
    "channel_trust": 0.15,
}

# Default threshold to flag metadata as suspicious
DEFAULT_MISMATCH_THRESHOLD = 0.4

# Typical music video duration range (seconds)
TYPICAL_MV_MIN_SECS = 120   # 2 minutes
TYPICAL_MV_MAX_SECS = 480   # 8 minutes

# Keywords that classify video type
KEYWORDS_OFFICIAL = {"official music video", "official video", "official hd video", "music video"}
KEYWORDS_LYRICS = {"lyrics", "lyric video", "lyrics video"}
KEYWORDS_LIVE = {"live", "live performance", "concert", "live at", "live from"}
KEYWORDS_COVER = {"cover", "acoustic cover", "piano cover"}
KEYWORDS_REACTION = {"reaction", "reacts", "first time hearing"}
KEYWORDS_REMIX = {"remix", "mashup", "bootleg"}

# Channel name patterns indicating trust
VEVO_PATTERN = re.compile(r"vevo$", re.IGNORECASE)
OFFICIAL_CHANNEL_HINTS = {"official", "music", "records", "entertainment", "label"}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class MismatchSignal:
    """A single mismatch detection signal."""
    name: str
    score: float          # 0.0 = no mismatch, 1.0 = definite mismatch
    details: str = ""
    weight: float = 0.0   # Will be filled from SIGNAL_WEIGHTS


@dataclass
class MismatchReport:
    """Complete mismatch analysis report."""
    overall_score: float = 0.0
    is_suspicious: bool = False
    threshold: float = DEFAULT_MISMATCH_THRESHOLD
    signals: List[MismatchSignal] = field(default_factory=list)
    video_type: str = "unknown"       # official, lyrics, live, cover, reaction, remix, unknown
    channel_trust: str = "unknown"    # verified, vevo, label, official, unknown

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_score": round(self.overall_score, 3),
            "is_suspicious": self.is_suspicious,
            "threshold": self.threshold,
            "signals": [
                {
                    "name": s.name,
                    "score": round(s.score, 3),
                    "details": s.details,
                    "weight": s.weight,
                }
                for s in self.signals
            ],
            "video_type": self.video_type,
            "channel_trust": self.channel_trust,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_mismatches(
    scraped: Dict[str, Any],
    video_title: Optional[str] = None,
    channel_name: Optional[str] = None,
    duration_seconds: Optional[float] = None,
    known_duration: Optional[float] = None,
    threshold: float = DEFAULT_MISMATCH_THRESHOLD,
) -> MismatchReport:
    """
    Run all mismatch detection heuristics on scraped metadata.

    Args:
        scraped: Scraped metadata dict with keys: artist, title, album, year, genres
        video_title: Platform video title (from YouTube/Vimeo)
        channel_name: Platform channel/uploader name
        duration_seconds: Actual video file duration in seconds
        known_duration: Known track duration (e.g. from MusicBrainz) in seconds
        threshold: Mismatch score threshold to flag as suspicious

    Returns:
        MismatchReport with signals and overall score.
    """
    signals = []

    # 1. Title comparison
    signals.append(_check_title_mismatch(scraped, video_title))

    # 2. Artist comparison
    signals.append(_check_artist_mismatch(scraped, channel_name))

    # 3. Duration comparison
    signals.append(_check_duration_mismatch(duration_seconds, known_duration))

    # 4. Keyword classification
    kw_signal, video_type = _check_keywords(video_title, scraped.get("title"))
    signals.append(kw_signal)

    # 5. Channel trust
    trust_signal, channel_trust = _check_channel_trust(
        channel_name, scraped.get("artist", ""),
    )
    signals.append(trust_signal)

    # Calculate weighted overall score
    overall = 0.0
    for sig in signals:
        sig.weight = SIGNAL_WEIGHTS.get(sig.name, 0)
        overall += sig.score * sig.weight

    report = MismatchReport(
        overall_score=min(overall, 1.0),
        is_suspicious=(overall >= threshold),
        threshold=threshold,
        signals=signals,
        video_type=video_type,
        channel_trust=channel_trust,
    )

    if report.is_suspicious:
        logger.info(
            f"Mismatch detected (score={overall:.2f}): "
            f"artist={scraped.get('artist')}, title={scraped.get('title')}"
        )

    return report


# ---------------------------------------------------------------------------
# Individual detectors
# ---------------------------------------------------------------------------

def _check_title_mismatch(
    scraped: Dict[str, Any],
    video_title: Optional[str],
) -> MismatchSignal:
    """Compare scraped title against platform video title."""
    db_title = scraped.get("title", "") or ""

    if not video_title or not db_title:
        return MismatchSignal(
            name="title_mismatch",
            score=0.3,  # Mild concern if no title to compare
            details="Missing title for comparison",
        )

    # Normalize for comparison
    norm_db = _normalize_for_comparison(db_title)
    norm_video = _normalize_for_comparison(video_title)

    # Check if DB title appears in video title (common: "Artist - Title (Official)")
    if norm_db in norm_video or norm_video in norm_db:
        return MismatchSignal(
            name="title_mismatch",
            score=0.0,
            details=f"Title matches: '{db_title}' found in '{video_title}'",
        )

    similarity = _fuzzy_similarity(norm_db, norm_video)

    if similarity > 0.8:
        score = 0.0
    elif similarity > 0.5:
        score = 0.3
    elif similarity > 0.3:
        score = 0.6
    else:
        score = 0.9

    return MismatchSignal(
        name="title_mismatch",
        score=score,
        details=f"Title similarity: {similarity:.2f} ('{db_title}' vs '{video_title}')",
    )


def _check_artist_mismatch(
    scraped: Dict[str, Any],
    channel_name: Optional[str],
) -> MismatchSignal:
    """Compare scraped artist against platform channel name."""
    artist = scraped.get("artist", "") or ""

    if not channel_name or not artist:
        return MismatchSignal(
            name="artist_mismatch",
            score=0.2,
            details="Missing channel name or artist for comparison",
        )

    norm_artist = _normalize_for_comparison(artist)
    norm_channel = _normalize_for_comparison(channel_name)

    # Direct containment check
    if norm_artist in norm_channel or norm_channel in norm_artist:
        return MismatchSignal(
            name="artist_mismatch",
            score=0.0,
            details=f"Artist matches channel: '{artist}' ≈ '{channel_name}'",
        )

    # Strip common suffixes from channels
    clean_channel = _strip_channel_suffixes(norm_channel)
    if norm_artist in clean_channel or clean_channel in norm_artist:
        return MismatchSignal(
            name="artist_mismatch",
            score=0.05,
            details=f"Artist matches cleaned channel: '{artist}' ≈ '{clean_channel}'",
        )

    similarity = _fuzzy_similarity(norm_artist, clean_channel)

    if similarity > 0.7:
        score = 0.1
    elif similarity > 0.4:
        score = 0.5
    else:
        score = 0.85

    return MismatchSignal(
        name="artist_mismatch",
        score=score,
        details=f"Artist-channel similarity: {similarity:.2f} ('{artist}' vs '{channel_name}')",
    )


def _check_duration_mismatch(
    duration_seconds: Optional[float],
    known_duration: Optional[float],
) -> MismatchSignal:
    """
    Check duration against typical music video range and known track duration.
    """
    if duration_seconds is None:
        return MismatchSignal(
            name="duration_mismatch",
            score=0.1,
            details="No duration available",
        )

    score = 0.0
    details = []

    # Check against typical range
    if duration_seconds < TYPICAL_MV_MIN_SECS:
        score = max(score, 0.4)
        details.append(f"Short ({duration_seconds:.0f}s < {TYPICAL_MV_MIN_SECS}s)")
    elif duration_seconds > TYPICAL_MV_MAX_SECS:
        # Very long = might be compilation, concert, etc.
        score = max(score, 0.5)
        details.append(f"Long ({duration_seconds:.0f}s > {TYPICAL_MV_MAX_SECS}s)")

    # Compare with known track duration (e.g. from MusicBrainz)
    if known_duration and known_duration > 0:
        diff = abs(duration_seconds - known_duration)
        ratio = diff / known_duration

        if ratio > 0.3:
            score = max(score, 0.7)
            details.append(
                f"Duration mismatch: video={duration_seconds:.0f}s "
                f"vs known={known_duration:.0f}s (diff={ratio:.0%})"
            )
        elif ratio > 0.15:
            score = max(score, 0.3)
            details.append(f"Minor duration diff ({ratio:.0%})")
        else:
            details.append(f"Duration acceptable (diff={ratio:.0%})")

    return MismatchSignal(
        name="duration_mismatch",
        score=score,
        details="; ".join(details) if details else "Duration within normal range",
    )


def _check_keywords(
    video_title: Optional[str],
    scraped_title: Optional[str],
) -> tuple:
    """
    Classify video type based on title keywords.
    Returns (signal, video_type).
    """
    combined = f"{video_title or ''} {scraped_title or ''}".lower()

    video_type = "unknown"
    score = 0.3  # Default: mild concern for unclassifiable

    # Check patterns in priority order
    if any(kw in combined for kw in KEYWORDS_REACTION):
        video_type = "reaction"
        score = 0.8  # Reaction videos likely have wrong metadata
    elif any(kw in combined for kw in KEYWORDS_COVER):
        video_type = "cover"
        score = 0.5  # Covers may have artist mismatch
    elif any(kw in combined for kw in KEYWORDS_REMIX):
        video_type = "remix"
        score = 0.4
    elif any(kw in combined for kw in KEYWORDS_LIVE):
        video_type = "live"
        score = 0.2  # Live versions are usually by the right artist
    elif any(kw in combined for kw in KEYWORDS_LYRICS):
        video_type = "lyrics"
        score = 0.3  # Often uploaded by fan channels
    elif any(kw in combined for kw in KEYWORDS_OFFICIAL):
        video_type = "official"
        score = 0.0  # Official videos are usually correctly tagged
    else:
        video_type = "unknown"
        score = 0.3

    signal = MismatchSignal(
        name="keyword_flags",
        score=score,
        details=f"Video type: {video_type}",
    )
    return signal, video_type


def _check_channel_trust(
    channel_name: Optional[str],
    artist: str,
) -> tuple:
    """
    Score channel trustworthiness.
    Returns (signal, trust_level).
    """
    if not channel_name:
        return MismatchSignal(
            name="channel_trust",
            score=0.4,
            details="No channel info available",
        ), "unknown"

    channel_lower = channel_name.lower()
    trust_level = "unknown"
    score = 0.4

    # VEVO channels are highly trusted
    if VEVO_PATTERN.search(channel_name):
        trust_level = "vevo"
        score = 0.0

    # Channel contains the artist name → likely official
    elif artist and _normalize_for_comparison(artist) in _normalize_for_comparison(channel_name):
        if any(hint in channel_lower for hint in OFFICIAL_CHANNEL_HINTS):
            trust_level = "official"
            score = 0.05
        else:
            trust_level = "artist_match"
            score = 0.1

    # Known label/music keywords
    elif any(hint in channel_lower for hint in {"records", "music", "entertainment", "label", "warner", "sony", "universal", "atlantic", "interscope"}):
        trust_level = "label"
        score = 0.1

    # Topic channels (YouTube auto-generated)
    elif "- topic" in channel_lower:
        trust_level = "topic"
        score = 0.15

    else:
        trust_level = "unknown"
        score = 0.4

    signal = MismatchSignal(
        name="channel_trust",
        score=score,
        details=f"Channel '{channel_name}': trust={trust_level}",
    )
    return signal, trust_level


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _normalize_for_comparison(text: str) -> str:
    """Normalize a string for fuzzy comparison."""
    text = text.lower().strip()
    # Remove common noise
    text = re.sub(r"\(.*?\)", "", text)          # Remove parentheticals
    text = re.sub(r"\[.*?\]", "", text)          # Remove brackets
    text = re.sub(r"official\s*(music\s*)?video", "", text)
    text = re.sub(r"(hd|4k|1080p|720p|480p)", "", text)
    text = re.sub(r"[^\w\s]", " ", text)         # Remove punctuation
    text = re.sub(r"\s+", " ", text).strip()     # Collapse whitespace
    return text


def _fuzzy_similarity(a: str, b: str) -> float:
    """Compute fuzzy string similarity (0.0–1.0)."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _strip_channel_suffixes(channel: str) -> str:
    """Remove common channel name suffixes."""
    suffixes = ["vevo", "official", "music", "tv", "channel", "band", "records"]
    result = channel
    for suffix in suffixes:
        if result.endswith(suffix):
            result = result[:-len(suffix)].strip()
    return result
