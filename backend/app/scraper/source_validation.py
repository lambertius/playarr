# SHARED SCRAPER MODULE - source of truth for all scraping pathways.
# Used by: scraper tester, URL import, rescan, scrape metadata, (future) library import.
"""
Source Validation & Metadata Rules
===================================
Centralized rules for:
- Source type validation (what provider + source_type combos are valid)
- Multi-artist parsing (primary vs featured artists)
- Album sanitization ("Title - Single" stripping)
- Wikipedia page type classification

All four scraping modes (import, manual scrape, manual analyze, AI-assisted)
must call these functions to enforce consistent behavior.
"""
import logging
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source type validation
# ---------------------------------------------------------------------------

# Providers that are allowed to use source_type="video"
_VIDEO_ALLOWED_PROVIDERS = {"youtube", "vimeo", "imdb"}

# Valid source_type values
VALID_SOURCE_TYPES = {"video", "artist", "album", "single", "recording"}


def validate_source_type(provider: str, source_type: str, url: str = "") -> str:
    """Validate and correct a source_type assignment.

    Rules:
    - Only youtube/vimeo/imdb may use "video"
    - Wikipedia and MusicBrainz links cannot be "video"
    - If source_type is invalid, infer from URL or default to "single"

    Returns the corrected source_type.
    """
    # Reject "video" for non-platform providers
    if source_type == "video" and provider not in _VIDEO_ALLOWED_PROVIDERS:
        inferred = infer_source_type_from_url(url, provider)
        logger.warning(
            f"source_type='video' invalid for provider='{provider}' "
            f"(url={url}). Coerced to '{inferred}'."
        )
        return inferred

    # Validate known types
    if source_type not in VALID_SOURCE_TYPES:
        inferred = infer_source_type_from_url(url, provider)
        logger.warning(
            f"Unknown source_type='{source_type}' for provider='{provider}'. "
            f"Coerced to '{inferred}'."
        )
        return inferred

    return source_type


def infer_source_type_from_url(url: str, provider: str = "") -> str:
    """Infer the correct source_type from a URL.

    Uses URL patterns to classify:
    - MusicBrainz artist URLs â†’ "artist"
    - MusicBrainz release-group URLs â†’ "single"
    - MusicBrainz recording URLs â†’ "recording"
    - MusicBrainz release URLs â†’ "album" (unless looks like single)
    - Wikipedia artist pages â†’ "artist"
    - Wikipedia album pages â†’ "album"
    - Wikipedia song/single pages â†’ "single"
    """
    url_lower = url.lower()

    if "musicbrainz.org" in url_lower:
        if "/artist/" in url_lower:
            return "artist"
        if "/release-group/" in url_lower:
            return "single"
        if "/recording/" in url_lower:
            return "recording"
        if "/release/" in url_lower:
            return "album"
        return "single"  # default for MusicBrainz

    if "wikipedia.org" in url_lower:
        # Can't easily classify from URL alone; default to "single"
        return "single"

    if "imdb.com" in url_lower:
        return "video"

    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "video"

    if "vimeo.com" in url_lower:
        return "video"

    return "single"


# ---------------------------------------------------------------------------
# Multi-artist parsing
# ---------------------------------------------------------------------------

# Patterns that separate primary artist from featured/additional artists
_FEAT_PATTERNS = [
    r'\s*\(feat\.?\s+',   # "(feat. X)" â€” parenthesized
    r'\s*\(featuring\s+',  # "(featuring X)" â€” parenthesized
    r'\s*\(ft\.?\s+',     # "(ft. X)" â€” parenthesized
    r'\s+feat\.?\s+',
    r'\s+featuring\s+',
    r'\s+ft\.?\s+',
    r'\s+with\s+',
    r'\s+x\s+',          # "Artist X Artist" collab style
    r'\s+vs\.?\s+',        # "Artist vs. Artist" collab style
]

# Separators for multiple artists (applied after feat split)
_ARTIST_SEPARATORS = [
    r'\s*&\s*',
    r'\s+and\s+',
    r'\s*,\s+',
]


def parse_multi_artist(artist_string: str) -> Tuple[str, List[str]]:
    """Parse an artist string into primary artist and featured/additional artists.

    The first credited artist is always the primary artist.

    Examples:
        "AronChupa feat. Little Sis Nora" â†’ ("AronChupa", ["Little Sis Nora"])
        "AronChupa and Little Sis Nora" â†’ ("AronChupa", ["Little Sis Nora"])
        "AronChupa & Little Sis Nora" â†’ ("AronChupa", ["Little Sis Nora"])
        "DJ Snake & Lil Jon" â†’ ("DJ Snake", ["Lil Jon"])
        "Florence + the Machine" â†’ ("Florence + the Machine", [])
        "Mike Diva & Sick System" â†’ ("Mike Diva", ["Sick System"])

    Special handling:
    - "Florence + the Machine" is preserved as a single artist name
      (the "+" is part of the band name, not a separator)
    - Names with "the" after + are treated as one artist

    Returns:
        (primary_artist, list_of_featured_artists)
    """
    if not artist_string:
        return ("", [])

    artist_string = artist_string.strip()

    # Step 1: Split on featuring patterns
    primary_part = artist_string
    featured: List[str] = []

    for pattern in _FEAT_PATTERNS:
        match = re.split(pattern, primary_part, maxsplit=1, flags=re.IGNORECASE)
        if len(match) == 2:
            primary_part = match[0].strip()
            featured_part = match[1].strip()
            # Strip trailing ) left from parenthesized feat patterns like "(feat. X)"
            if featured_part.endswith(')'):
                featured_part = featured_part[:-1].strip()
            # The featured part may itself contain separators
            featured.extend(_split_artists(featured_part))
            break

    # Step 2: Split the primary part on separators to find the true primary
    # But protect known band names with "+" or "&" in them
    primary_artists = _split_artists(primary_part)

    if primary_artists:
        primary = primary_artists[0]
        additional = primary_artists[1:] + featured
    else:
        primary = artist_string
        additional = []

    return (primary, additional)


def _split_artists(artist_str: str) -> List[str]:
    """Split a string of artists on separators (& , and) while protecting band names.

    Protected patterns:
    - "X + the Y" â€” band name (Florence + the Machine)
    - "X + Y" where the result is clearly a single band
    """
    if not artist_str:
        return []

    # Protect "X + the Y" patterns (band names like "Florence + the Machine")
    if re.search(r'\+\s+the\s+', artist_str, re.IGNORECASE):
        return [artist_str.strip()]

    # Protect "X & The Y" patterns (band names like "Amanda Palmer & The Grand Theft Orchestra",
    # "Tom Petty & The Heartbreakers"). "& The" almost always indicates a
    # single band/project rather than a collaboration between separate artists.
    if re.search(r'&\s+the\s+', artist_str, re.IGNORECASE):
        return [artist_str.strip()]

    # Try splitting on "and" first (most natural separator)
    # But only if "and" appears as a standalone word, not inside a name
    for pattern in _ARTIST_SEPARATORS:
        parts = re.split(pattern, artist_str, flags=re.IGNORECASE)
        if len(parts) > 1:
            # Validate all parts look like artist names (not empty, > 1 char)
            parts = [p.strip() for p in parts if p.strip() and len(p.strip()) > 1]
            if len(parts) > 1:
                return parts

    return [artist_str.strip()]


def extract_primary_artist_from_description(description: str) -> Optional[str]:
    """Extract primary artist from descriptions like "Single by AronChupa and Little Sis Nora".

    Returns the first credited artist name, or None if parsing fails.
    """
    # Match patterns like "single by X and Y", "song by X featuring Y"
    m = re.search(
        r'(?:single|song|track)\s+by\s+(.+)',
        description, re.IGNORECASE,
    )
    if not m:
        return None

    artists_str = m.group(1).strip()
    primary, _ = parse_multi_artist(artists_str)
    return primary or None


# ---------------------------------------------------------------------------
# Album sanitization
# ---------------------------------------------------------------------------

# Patterns that indicate a storefront-style "single" label, not a real album
_SINGLE_LABEL_PATTERNS = [
    re.compile(r'^(.+?)\s*-\s*Single$', re.IGNORECASE),
    re.compile(r'^(.+?)\s*\(Single\)$', re.IGNORECASE),
    re.compile(r'^(.+?)\s*\[Single\]$', re.IGNORECASE),
]


def sanitize_album(album: Optional[str], title: str = "") -> Optional[str]:
    """Remove storefront-style "Title - Single" album labels.

    Rules:
    - If album matches "Title - Single", "Title (Single)", "Title [Single]",
      strip the single suffix and compare to the track title.
    - If the remaining text matches the track title, return None (no real album).
    - If the remaining text differs from the title, it might be a genuine
      album name that happens to include "- Single", so keep it.

    Returns the sanitized album name, or None to clear it.
    """
    if not album:
        return album

    album = album.strip()

    # Strip enclosing quote characters that AI models sometimes wrap
    # values in â€” e.g. '"I\'m an Albatraoz - Single"'. These prevent
    # regex patterns from matching the actual content.
    if len(album) >= 2 and album[0] == album[-1] and album[0] in ('"', "'", '\u201c', '\u2018'):
        album = album[1:-1].strip()
    # Also handle mismatched smart quotes: "value\u201d or \u201cvalue"
    if len(album) >= 2:
        if (album[0] == '\u201c' and album[-1] == '\u201d') or (album[0] == '\u2018' and album[-1] == '\u2019'):
            album = album[1:-1].strip()

    if not album:
        return None

    # Strip sentinel / placeholder values that indicate "no album".
    # AI models and scrapers sometimes output these instead of leaving
    # the field blank â€” and there are real albums called "Unknown" etc.
    # which would create false-positive matches downstream.
    _SENTINEL_VALUES = {
        "unknown", "unknown album", "n/a", "na", "none", "null",
        "nil", "no album", "untitled", "tbd", "not available",
        "not applicable", "[not set]", "-", "--", "â€”", "?",
    }
    if album.lower().strip() in _SENTINEL_VALUES:
        return None

    for pattern in _SINGLE_LABEL_PATTERNS:
        m = pattern.match(album)
        if m:
            core = m.group(1).strip()
            # If the core matches the track title, this is a fake album
            if _fuzzy_title_match(core, title):
                logger.info(
                    f"Album '{album}' is storefront single label "
                    f"(matches title '{title}'). Clearing to null."
                )
                return None
            # Otherwise keep the core without the "- Single" suffix,
            # but still set to null since we prefer explicit albums
            logger.info(
                f"Album '{album}' has single suffix. Clearing to null "
                f"(no strong album evidence)."
            )
            return None

    return album


def _fuzzy_title_match(a: str, b: str) -> bool:
    """Check if two strings are essentially the same title."""
    if not a or not b:
        return False
    a_norm = re.sub(r'[^\w\s]', '', a.lower()).strip()
    b_norm = re.sub(r'[^\w\s]', '', b.lower()).strip()
    return a_norm == b_norm


# ---------------------------------------------------------------------------
# Wikipedia page type classification
# ---------------------------------------------------------------------------

class WikiPageType:
    ARTIST = "artist"
    SINGLE = "single"
    ALBUM = "album"
    UNRELATED = "unrelated"
    DISAMBIGUATION = "disambiguation"


def classify_wikipedia_page(
    infobox_text: str = "",
    first_paragraph: str = "",
    page_title: str = "",
    infobox_type: str = "",
) -> str:
    """Classify a Wikipedia page as artist, single, album, or unrelated.

    Uses infobox content, first paragraph, and page title to determine
    what the article is about.

    Returns one of WikiPageType values.
    """
    # Normalize whitespace — HTML get_text(separator=" ") can produce
    # multi-space gaps between elements (e.g. "Single  by  Artist"),
    # which breaks indicator matching like "single by".
    text_lower = re.sub(r"\s+", " ", infobox_text.lower())
    para_lower = re.sub(r"\s+", " ", first_paragraph.lower())
    title_lower = page_title.lower()

    # Check infobox subheader patterns
    single_indicators = [
        "single by", "song by", "track by",
        "single from", "song from",
        "debut single", "lead single", "promotional single",
    ]
    album_indicators = [
        "studio album by", "album by", "compilation album",
        "live album by", "debut album", "soundtrack album",
        "ep by", "mixtape by",
    ]
    artist_indicators = [
        "born ", "is a ", "is an ", "was a ", "was an ",
        "musical group", "musical duo", "musical act",
        "band", "singer", "rapper", "songwriter",
        "musician", "dj ", "disc jockey",
        "record producer",
    ]

    # Priority: check infobox text first (most reliable)
    for indicator in single_indicators:
        if indicator in text_lower:
            return WikiPageType.SINGLE

    for indicator in album_indicators:
        if indicator in text_lower:
            return WikiPageType.ALBUM

    for indicator in artist_indicators:
        if indicator in text_lower:
            return WikiPageType.ARTIST

    # Check first paragraph
    for indicator in single_indicators:
        if indicator in para_lower:
            return WikiPageType.SINGLE

    for indicator in album_indicators:
        if indicator in para_lower:
            return WikiPageType.ALBUM

    for indicator in artist_indicators:
        if indicator in para_lower:
            return WikiPageType.ARTIST

    # Check page title patterns
    if "(song)" in title_lower or "(single)" in title_lower:
        return WikiPageType.SINGLE
    if "(album)" in title_lower or "(ep)" in title_lower:
        return WikiPageType.ALBUM
    if any(p in title_lower for p in ["(band)", "(musician)", "(singer)", "(rapper)"]):
        return WikiPageType.ARTIST

    return WikiPageType.UNRELATED
