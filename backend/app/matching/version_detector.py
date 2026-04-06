"""
Version Detection Engine — Classify videos as normal / cover / live / alternate / remix / acoustic.

Runs heuristic analysis on all available signals (filename, platform title,
uploader, description, fingerprint, scraped metadata) to produce a provisional
version classification and review-routing recommendation.

Classification states
---------------------
* ``normal``    — standard studio music video
* ``cover``     — another artist performing a song originally by someone else
* ``live``      — live performance / concert / TV appearance
* ``alternate`` — alternate official version (uncensored, director's cut, etc.)
* ``remix``     — remix by another artist/producer
* ``acoustic``  — acoustic / stripped-back rendition by the original artist

If the detection is uncertain, escalation is recommended:
* AI review  (if AI is enabled)
* Human review  (if AI is disabled)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from app.matching.normalization import make_comparison_key

logger = logging.getLogger(__name__)

__all__ = [
    "VersionClassification",
    "detect_version_type",
]

# ---------------------------------------------------------------------------
# Keyword patterns (compiled once)
# ---------------------------------------------------------------------------

# Cover indicators
_COVER_KEYWORDS = re.compile(
    r"\b(?:cover|covered\s+by|rendition|tribute|reimagined|karaoke|piano\s+cover"
    r"|acoustic\s+cover|metal\s+cover|punk\s+cover)\b",
    re.IGNORECASE,
)
_COVER_PATTERN_BY = re.compile(
    r"(?:^|\s)(?P<title>.+?)\s+(?:by|performed\s+by)\s+(?P<performer>.+)",
    re.IGNORECASE,
)

# Live indicators — used for titles and filenames where a bare "live" is meaningful
_LIVE_KEYWORDS = re.compile(
    r"\b(?:live|live\s+at|live\s+from|live\s+in|live\s+on|concert|unplugged"
    r"|mtv\s+unplugged|acoustic\s+live|session|sessions|tiny\s+desk"
    r"|late\s+show|tonight\s+show|jimmy\s+(?:fallon|kimmel)|conan"
    r"|jools\s+holland|later\s*\.\.\.\s*with|glastonbury|coachella|lollapalooza"
    r"|bonnaroo|sxsw|festival|on\s+stage|in\s+concert|live\s+performance"
    r"|live\s+session|bbc\s+(?:live|session|radio)|kexp|audiotree"
    r"|npr\s+(?:music|tiny)|colors?\s+(?:show|studio)|mahogany\s+sessions?)\b",
    re.IGNORECASE,
)

# Stricter live indicators for descriptions — excludes bare "live", "session",
# and "festival" which appear routinely in YouTube promo boilerplate
# (e.g. "Live Performances" playlist links, "watch them live at festivals").
_LIVE_KEYWORDS_DESC = re.compile(
    r"\b(?:live\s+at|live\s+from|live\s+in|live\s+on|recorded\s+live"
    r"|concert|unplugged|mtv\s+unplugged|acoustic\s+live|tiny\s+desk"
    r"|late\s+show|tonight\s+show|jimmy\s+(?:fallon|kimmel)|conan"
    r"|jools\s+holland|later\s*\.\.\.\s*with|glastonbury|coachella|lollapalooza"
    r"|bonnaroo|sxsw|on\s+stage|in\s+concert|live\s+session"
    r"|bbc\s+(?:live|session|radio)|kexp|audiotree"
    r"|npr\s+(?:music|tiny)|colors?\s+(?:show|studio)|mahogany\s+sessions?)\b",
    re.IGNORECASE,
)

# Alternate version indicators
_ALTERNATE_KEYWORDS = re.compile(
    r"\b(?:alternate\s+version|alt\s+version|version\s+[2-9]|v[2-9]|uncensored"
    r"|censored|clean\s+version|explicit\s+version|director'?s?\s+cut"
    r"|alternate\s+cut|alternate\s+edit|official\s+alternate"
    r"|alternate\s+(?:music\s+)?video|original\s+version|extended\s+version"
    r"|short\s+version|radio\s+edit|album\s+version|single\s+version"
    r"|official\s+video\s+(?:#|no?\.?\s*)[2-9]"
    r"|video\s+(?:version|edit)\s+[2-9ab]|version\s+[ab])\b",
    re.IGNORECASE,
)

# Remix indicators — distinct from alternate
_REMIX_KEYWORDS = re.compile(
    r"\b(?:remix|remixed\s+by|(?:re)?mix(?:ed)?)\b",
    re.IGNORECASE,
)

# Acoustic indicators — stripped-back rendition by the original artist
_ACOUSTIC_KEYWORDS = re.compile(
    r"\b(?:acoustic(?:\s+version)?|acoustic\s+session|stripped(?:\s+back)?|unplugged"
    r"|acoustic\s+performance|acoustic\s+rendition)\b",
    re.IGNORECASE,
)

# Labels extractable from title for alternate versions
_ALT_LABEL_PATTERNS = [
    (re.compile(r"\b(uncensored)\b", re.I), "Uncensored"),
    (re.compile(r"\b(censored|clean\s+version)\b", re.I), "Clean"),
    (re.compile(r"\b(explicit(?:\s+version)?)\b", re.I), "Explicit"),
    (re.compile(r"\b(director'?s?\s+cut)\b", re.I), "Director's Cut"),
    (re.compile(r"\b(alternate\s+cut)\b", re.I), "Alternate Cut"),
    (re.compile(r"\b(alternate\s+edit)\b", re.I), "Alternate Edit"),
    (re.compile(r"\b(extended\s+version)\b", re.I), "Extended Version"),
    (re.compile(r"\b(short\s+version)\b", re.I), "Short Version"),
    (re.compile(r"\b(radio\s+edit)\b", re.I), "Radio Edit"),
    (re.compile(r"\bversion\s+([2-9ab])\b", re.I), "Version {0}"),
    (re.compile(r"\bv([2-9])\b", re.I), "Version {0}"),
]

# Confidence thresholds
CONFIDENCE_HIGH = 0.80     # Auto-classify
CONFIDENCE_MEDIUM = 0.50   # Likely but needs review
CONFIDENCE_LOW = 0.30      # Uncertain — escalate


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class VersionSignal:
    """A single detection signal contributing to the classification."""
    source: str          # e.g. "title_keywords", "fingerprint_mismatch"
    classification: str  # "cover", "live", "alternate", "normal"
    confidence: float    # 0.0–1.0
    details: str = ""


@dataclass
class VersionClassification:
    """Complete version detection result."""
    version_type: str = "normal"          # final classification
    confidence: float = 0.0                # confidence in that classification
    alternate_version_label: str = ""      # e.g. "Uncensored"
    original_artist: str = ""              # for covers: the original artist
    original_title: str = ""               # for covers: the original song title
    performing_artist: str = ""            # for covers: the artist performing the cover
    detected_title: str = ""               # song title extracted from cover parsing
    needs_review: bool = False
    review_reason: str = ""
    signals: List[VersionSignal] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version_type": self.version_type,
            "confidence": round(self.confidence, 3),
            "alternate_version_label": self.alternate_version_label,
            "original_artist": self.original_artist,
            "original_title": self.original_title,
            "performing_artist": self.performing_artist,
            "detected_title": self.detected_title,
            "needs_review": self.needs_review,
            "review_reason": self.review_reason,
            "signals": [
                {"source": s.source, "classification": s.classification,
                 "confidence": round(s.confidence, 3), "details": s.details}
                for s in self.signals
            ],
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_version_type(
    *,
    filename: str = "",
    source_title: str = "",
    uploader: str = "",
    description: str = "",
    parsed_artist: str = "",
    parsed_title: str = "",
    fingerprint_artist: str = "",
    fingerprint_title: str = "",
    scraped_artist: str = "",
    scraped_title: str = "",
    duration_seconds: Optional[float] = None,
    known_duration: Optional[float] = None,
    existing_library_items: Optional[List[Dict[str, Any]]] = None,
    # User hints from import
    hint_cover: bool = False,
    hint_live: bool = False,
    hint_alternate: bool = False,
    hint_alternate_label: str = "",
) -> VersionClassification:
    """
    Analyse all available signals to classify the video version type.

    Args:
        filename:               Video filename on disk
        source_title:           Platform title (YouTube/Vimeo)
        uploader:               Platform uploader / channel name
        description:            Platform video description
        parsed_artist:          Artist parsed from filename/title
        parsed_title:           Song title parsed from filename/title
        fingerprint_artist:     Artist from audio fingerprint (AcoustID)
        fingerprint_title:      Title from audio fingerprint
        scraped_artist:         Artist from metadata scraping (Wikipedia/MB)
        scraped_title:          Title from metadata scraping
        duration_seconds:       Actual video duration
        known_duration:         Known studio track duration (e.g. from MusicBrainz)
        existing_library_items: Other items in library with same title (for alternate detection)
        hint_cover:             User indicated this is a cover
        hint_live:              User indicated this is a live recording
        hint_alternate:         User indicated this is an alternate version
        hint_alternate_label:   User-provided alternate version label

    Returns:
        VersionClassification with type, confidence, and review recommendations.
    """
    signals: List[VersionSignal] = []

    # Combine all text for keyword scanning
    all_text = " ".join(filter(None, [filename, source_title, description]))

    # --- User hints (strong signals) ---
    if hint_cover:
        signals.append(VersionSignal("user_hint", "cover", 0.90, "User indicated cover version"))
    if hint_live:
        signals.append(VersionSignal("user_hint", "live", 0.90, "User indicated live version"))
    if hint_alternate:
        signals.append(VersionSignal("user_hint", "alternate", 0.90,
                                     f"User indicated alternate version: {hint_alternate_label}"))

    # --- Title keyword analysis ---
    _check_title_keywords(signals, source_title, filename)

    # --- Description keyword analysis ---
    _check_description_keywords(signals, description)

    # --- Performer vs original artist mismatch (cover detection) ---
    _check_performer_mismatch(signals, parsed_artist, fingerprint_artist,
                              scraped_artist, uploader)

    # --- Duration analysis (live detection) ---
    _check_duration_signals(signals, duration_seconds, known_duration)

    # --- Existing library duplicate check (alternate detection) ---
    _check_library_duplicates(signals, parsed_artist, parsed_title,
                              existing_library_items)

    # --- Uploader / channel analysis ---
    _check_uploader_signals(signals, uploader, parsed_artist)

    # --- Aggregate and decide ---
    return _aggregate_signals(signals, hint_alternate_label, source_title, filename)


# ---------------------------------------------------------------------------
# Signal generators
# ---------------------------------------------------------------------------

def _check_title_keywords(signals: List[VersionSignal], source_title: str, filename: str):
    """Scan title and filename for version-type keywords."""
    for text, label in [(source_title, "source_title"), (filename, "filename")]:
        if not text:
            continue

        if _COVER_KEYWORDS.search(text):
            signals.append(VersionSignal(
                f"{label}_keywords", "cover", 0.75,
                f"Cover keyword found in {label}: {text}",
            ))

        if _LIVE_KEYWORDS.search(text):
            signals.append(VersionSignal(
                f"{label}_keywords", "live", 0.75,
                f"Live keyword found in {label}: {text}",
            ))

        if _REMIX_KEYWORDS.search(text):
            signals.append(VersionSignal(
                f"{label}_keywords", "remix", 0.80,
                f"Remix keyword found in {label}: {text}",
            ))

        if _ACOUSTIC_KEYWORDS.search(text):
            signals.append(VersionSignal(
                f"{label}_keywords", "acoustic", 0.75,
                f"Acoustic keyword found in {label}: {text}",
            ))

        if _ALTERNATE_KEYWORDS.search(text):
            # Try to extract a label
            label_str = _extract_alt_label(text)
            signals.append(VersionSignal(
                f"{label}_keywords", "alternate", 0.70,
                f"Alternate version keyword in {label}: {label_str or text}",
            ))


def _check_description_keywords(signals: List[VersionSignal], description: str):
    """Scan description for version-type indicators."""
    if not description:
        return

    # Only scan first 1000 chars to avoid noise in long descriptions
    desc = description[:1000]

    if _COVER_KEYWORDS.search(desc):
        signals.append(VersionSignal(
            "description_keywords", "cover", 0.50,
            "Cover keyword found in description",
        ))

    # Use the stricter description regex — bare "live", "session", "festival"
    # appear routinely in YouTube promo boilerplate and cause false positives.
    if _LIVE_KEYWORDS_DESC.search(desc):
        signals.append(VersionSignal(
            "description_keywords", "live", 0.50,
            "Live keyword found in description",
        ))

    if _REMIX_KEYWORDS.search(desc):
        signals.append(VersionSignal(
            "description_keywords", "remix", 0.55,
            "Remix keyword found in description",
        ))

    if _ACOUSTIC_KEYWORDS.search(desc):
        signals.append(VersionSignal(
            "description_keywords", "acoustic", 0.50,
            "Acoustic keyword found in description",
        ))

    if _ALTERNATE_KEYWORDS.search(desc):
        signals.append(VersionSignal(
            "description_keywords", "alternate", 0.45,
            "Alternate version keyword found in description",
        ))


def _check_performer_mismatch(
    signals: List[VersionSignal],
    parsed_artist: str,
    fingerprint_artist: str,
    scraped_artist: str,
    uploader: str,
):
    """
    Detect performer vs original artist mismatch (strong cover signal).

    If the fingerprint says "Nirvana" but the performer/uploader is
    "Magic Joe", this is likely a cover.
    """
    if not parsed_artist:
        return

    parsed_key = make_comparison_key(parsed_artist)

    # Fingerprint vs parsed artist
    if fingerprint_artist:
        fp_key = make_comparison_key(fingerprint_artist)
        if fp_key and parsed_key and fp_key != parsed_key:
            # Check if they're reasonably different (not just rearranged)
            overlap = set(fp_key.split()) & set(parsed_key.split())
            total = set(fp_key.split()) | set(parsed_key.split())
            if total and len(overlap) / len(total) < 0.5:
                signals.append(VersionSignal(
                    "fingerprint_artist_mismatch", "cover", 0.80,
                    f"Fingerprint artist '{fingerprint_artist}' differs from "
                    f"parsed artist '{parsed_artist}'",
                ))

    # Scraped artist vs parsed artist
    if scraped_artist:
        sc_key = make_comparison_key(scraped_artist)
        if sc_key and parsed_key and sc_key != parsed_key:
            overlap = set(sc_key.split()) & set(parsed_key.split())
            total = set(sc_key.split()) | set(parsed_key.split())
            if total and len(overlap) / len(total) < 0.5:
                signals.append(VersionSignal(
                    "scraped_artist_mismatch", "cover", 0.65,
                    f"Scraped artist '{scraped_artist}' differs from "
                    f"parsed artist '{parsed_artist}'",
                ))


def _check_duration_signals(
    signals: List[VersionSignal],
    duration_seconds: Optional[float],
    known_duration: Optional[float],
):
    """
    Duration differences can indicate live versions (often longer).

    Live recordings typically run 20%+ longer than studio versions.
    """
    if duration_seconds is None or known_duration is None:
        return
    if known_duration <= 0:
        return

    diff = duration_seconds - known_duration
    ratio = diff / known_duration

    if ratio > 0.30:
        signals.append(VersionSignal(
            "duration_longer", "live", 0.45,
            f"Video is {ratio:.0%} longer than studio version "
            f"({duration_seconds:.0f}s vs {known_duration:.0f}s)",
        ))
    elif ratio < -0.30:
        signals.append(VersionSignal(
            "duration_shorter", "alternate", 0.30,
            f"Video is {abs(ratio):.0%} shorter than studio version "
            f"({duration_seconds:.0f}s vs {known_duration:.0f}s)",
        ))


def _check_library_duplicates(
    signals: List[VersionSignal],
    parsed_artist: str,
    parsed_title: str,
    existing_items: Optional[List[Dict[str, Any]]],
):
    """
    If library already has the same artist+title, this might be an alternate version.
    """
    if not existing_items or not parsed_artist or not parsed_title:
        return

    parsed_artist_key = make_comparison_key(parsed_artist)
    parsed_title_key = make_comparison_key(parsed_title)

    for item in existing_items:
        item_artist_key = make_comparison_key(item.get("artist", ""))
        item_title_key = make_comparison_key(item.get("title", ""))

        if item_artist_key == parsed_artist_key and item_title_key == parsed_title_key:
            signals.append(VersionSignal(
                "library_duplicate", "alternate", 0.55,
                f"Library already contains '{item.get('artist')} - {item.get('title')}' "
                f"(id={item.get('id')}, version_type={item.get('version_type', 'normal')})",
            ))
            break  # One signal is enough


def _check_uploader_signals(
    signals: List[VersionSignal],
    uploader: str,
    parsed_artist: str,
):
    """
    If the uploader doesn't match the artist at all, it could indicate a cover
    (fan uploading their own version) or a live performance by a festival channel.
    """
    if not uploader or not parsed_artist:
        return

    up_key = make_comparison_key(uploader)
    art_key = make_comparison_key(parsed_artist)

    # If uploader contains festival/venue keywords, boost live signal
    if re.search(r"\b(?:festival|venue|theater|theatre|stadium|arena|live|tv|television"
                 r"|bbc|npr|kexp|colors|vevo)\b", uploader, re.I):
        if not (art_key in up_key or up_key in art_key):
            signals.append(VersionSignal(
                "uploader_venue", "live", 0.35,
                f"Uploader '{uploader}' appears to be venue/broadcaster "
                f"(not the artist '{parsed_artist}')",
            ))


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _aggregate_signals(
    signals: List[VersionSignal],
    hint_alternate_label: str,
    source_title: str,
    filename: str,
) -> VersionClassification:
    """
    Aggregate individual signals into a final classification.

    Strategy: score each type by summing confidences (with diminishing returns),
    then pick the strongest. If the strongest is below a threshold, recommend review.
    """
    if not signals:
        return VersionClassification(version_type="normal", confidence=1.0)

    # Accumulate weighted scores per type
    type_scores: Dict[str, float] = {
        "cover": 0.0, "live": 0.0, "alternate": 0.0,
        "remix": 0.0, "acoustic": 0.0, "normal": 0.0,
    }
    for sig in signals:
        # Diminishing returns: each additional signal adds less
        current = type_scores.get(sig.classification, 0.0)
        # Soft-cap: new_score = old + conf * (1 - old)
        type_scores[sig.classification] = current + sig.confidence * (1.0 - current)

    # Find best non-normal classification
    best_type = "normal"
    best_score = 0.0
    for vt in ("cover", "live", "alternate", "remix", "acoustic"):
        if type_scores[vt] > best_score:
            best_type = vt
            best_score = type_scores[vt]

    # Extract alternate label
    alt_label = hint_alternate_label or ""
    original_artist = ""
    original_title = ""
    performing_artist = ""
    detected_title = ""

    if best_type == "alternate" and not alt_label:
        alt_label = _extract_alt_label(source_title or filename or "")

    if best_type == "cover":
        # Try to extract performing artist, original artist, and song from title
        cover_parse = _parse_cover_from_title(source_title)
        if not cover_parse:
            cover_parse = _parse_cover_from_title(filename)

        if cover_parse:
            original_artist = cover_parse.get("original", "") or original_artist
            original_title = cover_parse.get("song", "") or original_title
            performing_artist = cover_parse.get("performer", "")
            detected_title = cover_parse.get("song", "")
        else:
            # Fallback: try to identify the original artist from fingerprint signals
            for sig in signals:
                if "fingerprint_artist" in sig.source and "differs" in sig.details:
                    parts = sig.details.split("'")
                    if len(parts) >= 2:
                        original_artist = parts[1]
                        break

    # Determine review needs
    needs_review = False
    review_reason = ""

    if best_score >= CONFIDENCE_HIGH:
        needs_review = False
    elif best_score >= CONFIDENCE_MEDIUM:
        needs_review = True
        review_reason = f"Possible {best_type} version (confidence: {best_score:.0%})"
    elif best_score >= CONFIDENCE_LOW:
        needs_review = True
        review_reason = f"Uncertain classification: possible {best_type} (confidence: {best_score:.0%})"
        # If confidence is low, default to normal but flag for review
        if best_score < CONFIDENCE_MEDIUM:
            review_reason = f"Ambiguous signals detected — possible {best_type} version"
    else:
        # Very low confidence — classify as normal
        best_type = "normal"
        best_score = 1.0 - max(type_scores.values())

    # Build conflicting evidence summary for review
    if needs_review:
        conflicting = [s for s in signals if s.classification != best_type and s.confidence >= 0.3]
        if conflicting:
            conflicts = ", ".join(f"{s.classification} ({s.source})" for s in conflicting)
            review_reason += f" | Conflicting: {conflicts}"

    return VersionClassification(
        version_type=best_type,
        confidence=best_score,
        alternate_version_label=alt_label,
        original_artist=original_artist,
        original_title=original_title,
        performing_artist=performing_artist,
        detected_title=detected_title,
        needs_review=needs_review,
        review_reason=review_reason,
        signals=signals,
    )


def _extract_alt_label(text: str) -> str:
    """Try to extract a specific alternate version label from text."""
    for pattern, template in _ALT_LABEL_PATTERNS:
        m = pattern.search(text)
        if m:
            if "{0}" in template and m.lastindex and m.lastindex >= 1:
                return template.format(m.group(1))
            return template
    return ""


# ---------------------------------------------------------------------------
# Cover title parsing
# ---------------------------------------------------------------------------

# Patterns for extracting performer, original artist, and song from cover titles.
# These are tried in order — first match wins.
_COVER_TITLE_PATTERNS = [
    # "Artist cover OtherArtist 'Song' for Show"  (Like A Version format)
    # "Gang of Youths cover The Middle East 'Blood' for Like A Version"
    re.compile(
        r"^(?P<performer>.+?)\s+covers?\s+"
        r"(?P<original>.+?)\s+"
        r"['\'\u2018\u2019\u201c\u201d\"]+(?P<song>[^'\'\u2018\u2019\u201c\u201d\"]+)['\'\u2018\u2019\u201c\u201d\"]+"
        r"(?:\s+.*)?$",
        re.IGNORECASE,
    ),
    # "Artist covers 'Song' by OtherArtist"
    re.compile(
        r"^(?P<performer>.+?)\s+covers?\s+"
        r"['\'\u2018\u2019\u201c\u201d\"]+(?P<song>[^'\'\u2018\u2019\u201c\u201d\"]+)['\'\u2018\u2019\u201c\u201d\"]+"
        r"\s+(?:by|originally\s+by)\s+(?P<original>.+?)$",
        re.IGNORECASE,
    ),
    # "Artist cover OtherArtist - Song" (dash-separated song)
    re.compile(
        r"^(?P<performer>.+?)\s+covers?\s+"
        r"(?P<original>.+?)\s*[-\u2013\u2014]\s*(?P<song>.+)$",
        re.IGNORECASE,
    ),
    # "Artist covers 'Song'" (no original artist given)
    re.compile(
        r"^(?P<performer>.+?)\s+covers?\s+"
        r"['\'\u2018\u2019\u201c\u201d\"]+(?P<song>[^'\'\u2018\u2019\u201c\u201d\"]+)['\'\u2018\u2019\u201c\u201d\"]+",
        re.IGNORECASE,
    ),
    # "Artist - Song (Cover)" or "Artist - Song [Cover]" — standard format with cover tag
    re.compile(
        r"^(?P<performer>.+?)\s*[-\u2013\u2014]\s*(?P<song>.+?)\s*[\(\[]cover[\)\]]",
        re.IGNORECASE,
    ),
]


def _parse_cover_from_title(text: str) -> Optional[Dict[str, str]]:
    """
    Try to extract performing artist, original artist, and song title from
    a cover-style video title.

    Returns dict with 'performer', 'original' (may be empty), 'song' or None.
    """
    if not text:
        return None
    # Strip common suffixes before matching
    cleaned = re.sub(
        r"\s*[\(\[](?:official|video|audio|hd|4k|lyric|lyrics|music\s*video|mv)[^\)\]]*[\)\]]",
        "", text, flags=re.IGNORECASE
    ).strip()

    for pattern in _COVER_TITLE_PATTERNS:
        m = pattern.match(cleaned)
        if m:
            groups = m.groupdict()
            performer = groups.get("performer", "").strip()
            original = groups.get("original", "").strip()
            song = groups.get("song", "").strip()
            # Clean trailing "for/on" fragments from original if pattern was greedy
            original = re.sub(r"\s+(?:for|on|at)\s+.*$", "", original, flags=re.I).strip()
            if performer and song:
                return {
                    "performer": performer,
                    "original": original,
                    "song": song,
                }
    return None
