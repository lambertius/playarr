# AUTO-SEPARATED from matching/normalization.py for pipeline_url pipeline
# This file is independent — changes here do NOT affect the other pipeline.
"""
Normalization & Parsing — Clean raw artist / title / album strings.

Public API
----------
    normalize_artist_name(s) -> str
    extract_featured_artists(s) -> tuple[str, list[str]]
    normalize_title(s) -> str
    extract_title_qualifiers(s) -> dict
    normalize_album(s) -> str
    make_comparison_key(s) -> str

Design choices
--------------
* **Preserve meaningful punctuation** for display (AC/DC, P!nk, MØ)
  but also produce an aggressive "comparison key" with punctuation stripped.
* Featured-artist separators are normalised to ", " in display form and
  split into a primary + list of featured artists.
* Title qualifiers (Live, Acoustic, Remix, …) are extracted but NOT
  removed from the normalised title; instead they are returned alongside
  a ``title_base`` that has braces/brackets stripped.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional, Set, Tuple

__all__ = [
    "normalize_artist_name",
    "extract_featured_artists",
    "normalize_title",
    "extract_title_qualifiers",
    "normalize_album",
    "make_comparison_key",
]

# ── Constants ─────────────────────────────────────────────────────────────

# Separators that indicate featured artists  (order matters — longest first)
_FEAT_SEPARATORS = re.compile(
    r"""
    \s+featuring\s+  |
    \s+feat\.?\s+    |
    \s+ft\.?\s+      |
    \s+with\s+       |
    \s+x\s+          |
    \s*[&,]\s*
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Patterns to detect the *start* of a featured-artist clause
_FEAT_CLAUSE = re.compile(
    r"""
    \s*\(?\s*(?:feat(?:uring)?\.?|ft\.?)\s+
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Bracket content that should be STRIPPED from titles (noise descriptors)
_NOISE_BRACKET = re.compile(
    r"""
    [\(\[\{]\s*(?:
        official\s*(?:music\s*)?(?:video|hd(?:\s*video)?|audio|lyric\s*video|visuali[sz]er|lyrics)?
        | music\s*video | lyric\s*video | lyrics\s*video | lyrics
        | video | audio\s*only | audio
        | hd\s*(?:upgrade|upload|remaster)?
        | hq\s*(?:upgrade|upload)?
        | 4k\s*(?:upgrade|video|remaster)?
        | uhd | 1080p | 720p | 480p | 360p | 240p | 2160p
        | vevo | explicit | clean
        | directed\s+by\s+[^)\]]*
        | mv | pv | clip | short\s*film | premiere
        | remastered\s*(?:version)?
    )\s*[\)\]\}]
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Standalone noise terms (outside brackets)
_NOISE_STANDALONE = re.compile(
    r"""
    \b(?:
        official\s+music\s+video | official\s+video | music\s+video
        | official\s+hd\s+video | official\s+audio
        | lyric\s+video | lyrics\s+video
        | hd\s+upgrade | hq\s+upload | 4k\s+upgrade | 4k\s+video
        | VEVO | explicit | DASH_[AV] | remuxed
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Meaningful version qualifiers we want to KEEP and tag
_VERSION_QUALIFIERS = {
    "live", "acoustic", "unplugged", "demo", "instrumental",
    "radio edit", "extended mix", "extended", "remix",
    "remaster", "remastered", "deluxe", "bonus track",
    "single version", "album version",
}

# Regex to detect qualifiers inside brackets
_QUALIFIER_BRACKET = re.compile(
    r"[\(\[\{]\s*([^)\]\}]+?)\s*[\)\]\}]",
)

# Regex for trailing " - Topic" on YouTube channel auto-generated names
_TOPIC_SUFFIX = re.compile(r"\s*-\s*Topic\s*$", re.IGNORECASE)

# Collapse multiple spaces / dashes
_MULTI_SPACE = re.compile(r"[ \t]+")
_MULTI_DASH = re.compile(r"\s*[-–—]\s*[-–—]+\s*")


# ── Artist normalization ──────────────────────────────────────────────────

def normalize_artist_name(s: str) -> str:
    """
    Return a display-ready artist name.

    * Collapses whitespace, trims
    * Strips " - Topic" suffix (YouTube auto-generated)
    * Preserves meaningful punctuation (AC/DC, P!nk, MØ)
    """
    s = s.strip()
    s = _TOPIC_SUFFIX.sub("", s)
    s = _MULTI_SPACE.sub(" ", s).strip()
    return s


def extract_featured_artists(s: str) -> Tuple[str, List[str]]:
    """
    Split a raw artist string into (primary_artist, [featured, …]).

    Handles: "A feat. B", "A ft B", "A featuring B & C", "A, B, C",
             "A (feat. B)", "A x B".

    Returns display-normalised names.
    """
    s = normalize_artist_name(s)

    # First try to split on an explicit feat clause
    m = _FEAT_CLAUSE.search(s)
    if m:
        primary = s[: m.start()].strip().rstrip("(").strip()
        rest = s[m.end():]
        # Remove trailing paren if the feat was inside brackets
        rest = rest.rstrip(")").strip()
        featured = [normalize_artist_name(p) for p in _FEAT_SEPARATORS.split(rest) if p.strip()]
        return normalize_artist_name(primary), featured

    # Fall back: split on separators
    parts = _FEAT_SEPARATORS.split(s)
    parts = [normalize_artist_name(p) for p in parts if p.strip()]
    if len(parts) > 1:
        return parts[0], parts[1:]
    return parts[0] if parts else s, []


# ── Title normalization ───────────────────────────────────────────────────

def normalize_title(s: str) -> str:
    """
    Return a display-ready title with YouTube noise stripped.

    * Removes bracketed noise descriptors (Official Video, HD, 4K, …)
    * Removes standalone noise terms
    * Preserves meaningful version qualifiers (Live, Acoustic, Remix, …)
    * Collapses whitespace
    """
    s = _NOISE_BRACKET.sub("", s)
    s = _NOISE_STANDALONE.sub("", s)
    s = _MULTI_DASH.sub(" ", s)
    s = _MULTI_SPACE.sub(" ", s).strip()
    # Strip trailing dash or pipe left over from noise removal
    s = re.sub(r"\s*[-–—|]\s*$", "", s)
    s = re.sub(r"^\s*[-–—|]\s*", "", s)
    return s.strip()


def extract_title_qualifiers(s: str) -> Dict[str, object]:
    """
    Extract version qualifiers from a raw title.

    Returns::

        {
            "title_base": "Song Name",           # brackets stripped
            "qualifiers": {"live", "acoustic"},   # set of matched tokens
            "raw": "Song Name (Live) (Official Video)"
        }
    """
    qualifiers: Set[str] = set()

    # Check bracketed content for qualifiers
    for m in _QUALIFIER_BRACKET.finditer(s):
        inside = m.group(1).strip().lower()
        for q in _VERSION_QUALIFIERS:
            if q in inside:
                qualifiers.add(q)

    # Also check the bare text for standalone qualifiers preceded by " - "
    parts = re.split(r"\s+[-–—]\s+", s)
    if len(parts) > 1:
        for part in parts[1:]:
            lp = part.strip().lower()
            for q in _VERSION_QUALIFIERS:
                if q in lp:
                    qualifiers.add(q)

    title_base = normalize_title(s)

    return {
        "title_base": title_base,
        "qualifiers": qualifiers,
        "raw": s,
    }


# ── Album normalization ──────────────────────────────────────────────────

def normalize_album(s: str) -> str:
    """Clean an album title — collapse whitespace, trim, no other changes."""
    return _MULTI_SPACE.sub(" ", s.strip())


# ── Comparison key ────────────────────────────────────────────────────────

# Characters to keep when building a comparison key
_STRIP_CHARS = re.compile(r"[^\w\s]", re.UNICODE)


def make_comparison_key(s: str) -> str:
    """
    Aggressive normalisation for matching — lowercase, no punctuation,
    collapsed whitespace.  "AC/DC" → "acdc", "P!nk" → "pnk".

    Unicode-aware: decomposes accented characters where possible
    (Björk → bjork) but keeps non-Latin scripts intact.
    """
    # NFC → NFD decompose, then strip combining marks
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.lower()
    s = _STRIP_CHARS.sub("", s)
    s = _MULTI_SPACE.sub(" ", s).strip()
    return s
