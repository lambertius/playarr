"""
Trust Scoring — Evaluates how trustworthy a candidate video source is.

Design decisions:
  - VEVO channels get highest trust (0.95+).
  - Official artist/label channels get high trust (0.85+).
  - Title keywords like "Official Video" / "Official Music Video" boost trust.
  - Reuploads, fan channels, lyric-only, compilations get penalized.
  - Mismatch between uploader and artist is a strong negative signal.
  - Final trust_score is 0.0–1.0.

The scorer is stateless and operates on a single candidate dict. This makes
it easy to test, extend, and eventually replace with an ML-based scorer.
"""
import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Patterns ──────────────────────────────────────────────────────────────────
_OFFICIAL_PATTERNS = [
    re.compile(r"\bofficial\s+(music\s+)?video\b", re.I),
    re.compile(r"\bofficial\s+hd\b", re.I),
    re.compile(r"\bofficial\s+mv\b", re.I),
]

_NEGATIVE_TITLE_PATTERNS = [
    (re.compile(r"\blyric(s)?\s+video\b", re.I), "lyric_video"),
    (re.compile(r"\baudio\s+only\b", re.I), "audio_only"),
    (re.compile(r"\bcover\b", re.I), "cover"),
    (re.compile(r"\bremix\b", re.I), "remix"),
    (re.compile(r"\breaction\b", re.I), "reaction"),
    (re.compile(r"\bparody\b", re.I), "parody"),
    (re.compile(r"\bfan[\s-]?made\b", re.I), "fan_made"),
    (re.compile(r"\bunofficial\b", re.I), "unofficial"),
    (re.compile(r"\bbootleg\b", re.I), "bootleg"),
    (re.compile(r"\blive\s+at\b", re.I), "live_recording"),
    (re.compile(r"\bconcert\b", re.I), "concert"),
    (re.compile(r"\bcompilation\b", re.I), "compilation"),
    (re.compile(r"\bmix\s+20\d{2}\b", re.I), "mix"),
    (re.compile(r"\btop\s+\d+\b", re.I), "countdown_list"),
    (re.compile(r"\b\d+\s+hours?\s+of\b", re.I), "multi_hour_compilation"),
    (re.compile(r"\bfull\s+album\b", re.I), "full_album"),
    (re.compile(r"\bnonstop\b", re.I), "nonstop_mix"),
    (re.compile(r"\bmegamix\b", re.I), "megamix"),
]

_VEVO_PATTERN = re.compile(r"vevo", re.I)
_TOPIC_CHANNEL = re.compile(r"^\s*.+\s*-\s*Topic\s*$", re.I)


@dataclass
class TrustResult:
    """Output of trust scoring for a single candidate."""
    score: float = 0.5
    reasons: list = field(default_factory=list)
    penalties: list = field(default_factory=list)
    source_type: str = "unknown"


def _normalize_name(name: Optional[str]) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    if not name:
        return ""
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()


def _name_overlap(a: str, b: str) -> float:
    """Simple token-overlap ratio between two names."""
    a_tokens = set(_normalize_name(a).split())
    b_tokens = set(_normalize_name(b).split())
    if not a_tokens or not b_tokens:
        return 0.0
    intersection = a_tokens & b_tokens
    return len(intersection) / min(len(a_tokens), len(b_tokens))


def score_trust(
    title: str,
    channel: Optional[str] = None,
    artist: Optional[str] = None,
    view_count: Optional[int] = None,
    duration_seconds: Optional[int] = None,
    description: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> TrustResult:
    """Compute a trust score for a candidate video.

    Args:
        title: Video title as returned by the provider.
        channel: Uploader / channel name.
        artist: Expected artist name (from recommendation context).
        view_count: Number of views (if available).
        duration_seconds: Video length in seconds.
        description: Video description text.
        metadata: Raw provider metadata dict (for future extensibility).

    Returns:
        TrustResult with score, reasons, penalties, and inferred source_type.
    """
    result = TrustResult()
    channel_norm = _normalize_name(channel)
    title_str = title or ""

    # ── VEVO detection ────────────────────────────────────────────────────────
    is_vevo = bool(channel and _VEVO_PATTERN.search(channel))
    if is_vevo:
        result.score = 0.95
        result.source_type = "vevo"
        result.reasons.append("VEVO channel — highest trust source")

    # ── Official artist/label channel ─────────────────────────────────────────
    elif artist and channel:
        overlap = _name_overlap(artist, channel)
        if overlap >= 0.7:
            result.score = 0.88
            result.source_type = "official_channel"
            result.reasons.append(f"Channel name matches artist ({overlap:.0%} overlap)")
        elif overlap >= 0.4:
            result.score = 0.70
            result.source_type = "possible_official"
            result.reasons.append(f"Partial channel/artist match ({overlap:.0%} overlap)")

    # ── Topic channels (YouTube auto-generated) ──────────────────────────────
    if channel and _TOPIC_CHANNEL.match(channel):
        result.score = max(result.score, 0.60)
        if result.source_type == "unknown":
            result.source_type = "topic_channel"
        result.reasons.append("YouTube Topic (auto-generated) channel")

    # ── Title official signals ────────────────────────────────────────────────
    for pat in _OFFICIAL_PATTERNS:
        if pat.search(title_str):
            result.score = min(result.score + 0.10, 1.0)
            result.reasons.append("Title contains official video marker")
            if result.source_type == "unknown":
                result.source_type = "official_title"
            break

    # ── Negative title signals ────────────────────────────────────────────────
    _HARD_BLOCK_LABELS = {"multi_hour_compilation", "full_album", "nonstop_mix", "megamix"}
    for pat, label in _NEGATIVE_TITLE_PATTERNS:
        if pat.search(title_str):
            if label in _HARD_BLOCK_LABELS:
                result.score = 0.0
                result.penalties.append(f"Blocked: title indicates {label}")
            elif label in ("reaction", "parody", "fan_made", "bootleg", "unofficial", "compilation"):
                penalty = 0.20
                result.score = max(result.score - penalty, 0.0)
                result.penalties.append(f"Title indicates {label} (-{penalty:.2f})")
            else:
                penalty = 0.10
                result.score = max(result.score - penalty, 0.0)
                result.penalties.append(f"Title indicates {label} (-{penalty:.2f})")

    # ── View count signal ─────────────────────────────────────────────────────
    if view_count is not None:
        if view_count >= 100_000_000:
            result.score = min(result.score + 0.05, 1.0)
            result.reasons.append("100M+ views — very high engagement")
        elif view_count >= 10_000_000:
            result.score = min(result.score + 0.03, 1.0)
            result.reasons.append("10M+ views — high engagement")
        elif view_count < 10_000:
            result.score = max(result.score - 0.05, 0.0)
            result.penalties.append("Under 10K views — low engagement")

    # ── Duration sanity ───────────────────────────────────────────────────────
    if duration_seconds is not None:
        if duration_seconds < 60:
            result.score = max(result.score - 0.15, 0.0)
            result.penalties.append("Very short (<60s) — likely clip or preview")
        elif duration_seconds > 900:
            # Hard-block anything over 15 minutes — compilations, concerts, etc.
            result.score = 0.0
            result.penalties.append("Blocked: over 15 min — compilation/concert/non-music-video")
        elif duration_seconds > 480:
            result.score = max(result.score - 0.25, 0.0)
            result.penalties.append("Long (>8min) — possibly extended/concert cut")

    # ── Uploader/artist mismatch ──────────────────────────────────────────────
    if artist and channel and not is_vevo:
        overlap = _name_overlap(artist, channel)
        if overlap == 0.0 and result.source_type == "unknown":
            result.score = max(result.score - 0.15, 0.0)
            result.penalties.append("Channel name has no overlap with artist")

    # ── Floor / ceiling ───────────────────────────────────────────────────────
    result.score = max(0.0, min(1.0, round(result.score, 3)))

    return result
