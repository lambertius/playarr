"""
Metadata Resolver — Scrape and resolve metadata from multiple sources.

Sources:
1. yt-dlp extracted metadata (artist, title, description)
2. MusicBrainz API (canonical artist/recording/release info, genres)
3. Wikipedia scraping (album, year, genre, plot/music video description)

Inspired by the Music Video Scraper and Renamer script behavior.
"""
import logging
import os
import re
import time
import unicodedata
from typing import Optional, Dict, Any, List, Tuple

import httpx
import musicbrainzngs
from bs4 import BeautifulSoup

from app.config import get_settings

logger = logging.getLogger(__name__)

# Wikipedia requires a descriptive User-Agent for API access.
# Browser-spoofing UAs (e.g. fake_useragent) get 403-blocked.
# See https://meta.wikimedia.org/wiki/User-Agent_policy
_WIKI_USER_AGENT = "Playarr/1.0 (https://github.com/playarr; playarr@users.noreply.github.com) Python-httpx"

# Initialize MusicBrainz client
_mb_initialized = False


def _init_musicbrainz():
    global _mb_initialized
    if not _mb_initialized:
        settings = get_settings()
        musicbrainzngs.set_useragent(
            settings.musicbrainz_app,
            settings.musicbrainz_version,
            settings.musicbrainz_contact,
        )
        _mb_initialized = True


# ---------------------------------------------------------------------------
# Undesired terms for filename cleaning (from reference script)
# ---------------------------------------------------------------------------
UNDESIRED_TERMS = sorted([
    "Official Music Video",
    "Official HD Video",
    "Official HD",
    "Official Video",
    "Official Audio",
    "Official Lyric Video",
    "Official Visualizer",
    "Official Lyrics",
    "HD UPGRADE",
    "HD Upload",
    "HQ Upload",
    "4K UPGRADE",
    "4K Upgrade",
    "4K Video",
    "Music Video",
    "Lyric Video",
    "Lyrics Video",
    "Audio Only",
    "4K",
    "DASH_A",
    "DASH_V",
    "remuxed",
    "Remastered",
    "Remaster",
    "Official",
    "Video",
    "VEVO",
    "[1080p]",
    "[480p]",
    "[240p]",
    "[720p]",
    "[360p]",
    "[2160p]",
], key=len, reverse=True)

# Keywords that flag bracketed content for removal
_BRACKET_KEYWORDS = (
    r"official|video|hd|4k|lyric|lyrics|audio|upload|remaster|remastered"
    r"|hq|explicit|clean|vevo|visualizer|visualiser|directed|version"
    r"|music\s*video|mv|pv|clip|short\s*film|premiere|original"
    r"|1080p|720p|480p|360p|240p|2160p|uhd"
)


def clean_title(title: str) -> str:
    """Remove undesired terms from a title string.

    Strips bracketed descriptors like (Official Video), [HD Upload],
    standalone terms from UNDESIRED_TERMS, promotional suffixes,
    session/label tags, and normalises whitespace so only the real
    'Artist - Title' content remains.
    """
    cleaned = title

    # 1. Remove parenthesised/bracketed content with known keywords
    cleaned = re.sub(
        rf"\([^)]*(?:{_BRACKET_KEYWORDS})[^)]*\)",
        "", cleaned, flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        rf"\[[^\]]*(?:{_BRACKET_KEYWORDS})[^\]]*\]",
        "", cleaned, flags=re.IGNORECASE,
    )

    # 2. Strip literal undesired terms (longest first)
    for term in UNDESIRED_TERMS:
        # Case-insensitive word-boundary removal
        cleaned = re.sub(re.escape(term), "", cleaned, flags=re.IGNORECASE)

    # 3. Remove empty bracket pairs left behind: (), []
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"\[\s*\]", "", cleaned)

    # 4. Strip promotional / session / label suffixes
    # "(from "Album" -- ALBUM OUT NOW)" and similar promo parentheticals
    cleaned = re.sub(
        r'\s*\(from\s+"[^"]*"[^)]*\)', "", cleaned, flags=re.IGNORECASE,
    )
    # "(as featured in Movie)" movie tie-in tags
    cleaned = re.sub(
        r'\s*\(as featured in[^)]*\)', "", cleaned, flags=re.IGNORECASE,
    )
    # "[Monstercat Release]", "[Label Records]" etc.
    cleaned = re.sub(
        r'\s*\[[^\]]*(?:release|records|music)\]', "", cleaned, flags=re.IGNORECASE,
    )
    # "| OurVinyl Sessions", "| Live Session" etc.
    cleaned = re.sub(r'\s*\|.*$', "", cleaned)
    # Wrapping quotes around entire title: "Title" → Title
    cleaned = re.sub(r'^"(.+)"$', r'\1', cleaned.strip())

    # 5. Clean up stray trailing/leading punctuation (|, -, ~) after stripping
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"[\s|~]+$", "", cleaned).strip()
    cleaned = re.sub(r"^[\s|~]+", "", cleaned).strip()
    # Remove trailing dash/en-dash/em-dash left after stripping
    cleaned = re.sub(r"\s*[-–—]\s*$", "", cleaned).strip()

    return cleaned


# ---------------------------------------------------------------------------
# Genre capitalisation helpers
# ---------------------------------------------------------------------------

# Acronyms / special tokens that should be fully uppercased
_GENRE_UPPER = {"r&b", "edm", "dj", "uk", "us", "idm", "ebm", "dnb", "mpb", "nyc"}


def capitalize_genre(name: str) -> str:
    """Capitalise a genre name correctly.

    - Title-cases each word ("indie rock" → "Indie Rock")
    - Preserves acronyms like R&B, EDM, IDM, DJ
    - Handles hyphenated genres ("lo-fi" → "Lo-Fi", "post-punk" → "Post-Punk")
    """
    if not name:
        return name

    # Check if the whole lowered name is a known acronym
    if name.lower() in _GENRE_UPPER:
        return name.upper()

    parts = name.split()
    result = []
    for part in parts:
        lower = part.lower()
        if lower in _GENRE_UPPER:
            result.append(lower.upper())
        elif "-" in part:
            # Capitalise each side of a hyphen
            sub = []
            for seg in part.split("-"):
                if seg.lower() in _GENRE_UPPER:
                    sub.append(seg.upper())
                else:
                    sub.append(seg.capitalize())
            result.append("-".join(sub))
        elif "&" in part:
            # e.g. "r&b" handled above; other cases title-case each side
            sub = [seg.capitalize() for seg in part.split("&")]
            result.append("&".join(sub))
        else:
            result.append(part.capitalize())
    return " ".join(result)


def _find_separator_outside_brackets(text: str, separator: str) -> int:
    """Find the first occurrence of *separator* that is NOT inside ()[] pairs.

    Returns the index into *text* where the separator starts, or -1 if every
    occurrence lives inside brackets/parentheses.
    """
    depth = 0
    for i, ch in enumerate(text):
        if ch in ("(", "["):
            depth += 1
        elif ch in (")", "]"):
            depth = max(depth - 1, 0)
        elif depth == 0 and text[i:i + len(separator)] == separator:
            return i
    return -1


def extract_artist_title(raw_title: str) -> Tuple[str, str]:
    """
    Extract artist and title from a video title string.
    Common patterns:
      "Artist - Title"
      "Artist — Title"
      "Artist: Title"

    Separators inside parentheses/brackets are ignored so that e.g.
    ``'Foo (FULL UNCENSORED - NSFW)'`` is not mis-split on the inner dash.
    """
    cleaned = clean_title(raw_title)

    # Try dash-separated — only match separators outside brackets
    for separator in [" - ", " — ", " – ", " : "]:
        idx = _find_separator_outside_brackets(cleaned, separator)
        if idx >= 0:
            return cleaned[:idx].strip(), cleaned[idx + len(separator):].strip()

    # Try "by" pattern
    by_match = re.match(r"(.+?)\s+by\s+(.+)", cleaned, re.IGNORECASE)
    if by_match:
        return by_match.group(2).strip(), by_match.group(1).strip()

    # Fallback: entire string is title, artist unknown
    return "", cleaned


def extract_featuring_credit(text: str) -> Tuple[str, str]:
    """Extract featuring credits from a title string.

    Returns ``(clean_text, featuring_artists)`` where *featuring_artists*
    is the raw string after ``ft.``/``feat.``/``featuring``, or empty.
    """
    m = re.search(
        r"\s+(?:ft\.?|feat\.?|featuring)\s+(.+)$",
        text, flags=re.IGNORECASE,
    )
    if m:
        return text[: m.start()].strip(), m.group(1).strip()
    return text, ""


def _detect_artist_title_swap(
    parsed_artist: str, parsed_title: str,
    uploader: str, channel: str,
) -> Tuple[str, str]:
    """Detect and fix swapped artist/title using uploader/channel as reference.

    YouTube titles may be in ``Title - Artist`` format instead of the expected
    ``Artist - Title``.  If the uploader/channel name matches what we parsed
    as the **title** rather than the artist, the values are swapped.
    """
    if not parsed_artist or not parsed_title:
        return parsed_artist, parsed_title

    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    n_artist = _norm(parsed_artist)
    n_title = _norm(parsed_title)

    for ref in (uploader, channel):
        if not ref:
            continue
        n_ref = _norm(ref)
        if len(n_ref) < 3:
            continue
        title_match = n_ref in n_title or n_title in n_ref
        artist_match = n_ref in n_artist or n_artist in n_ref
        if title_match and not artist_match:
            return parsed_title, parsed_artist

    return parsed_artist, parsed_title


def _clean_ytdlp_artist(
    yt_artist: str, uploader: str, channel: str,
) -> str:
    """Validate yt-dlp artist field, rejecting channel names.

    Returns the cleaned artist string, or an empty string if the value
    appears to be a channel/uploader name rather than a real artist.
    """
    if not yt_artist:
        return ""

    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    n_yt = _norm(yt_artist)
    n_up = _norm(uploader) if uploader else ""
    n_ch = _norm(channel) if channel else ""

    # Reject if artist field is just the channel/uploader name
    if n_yt and (n_yt == n_up or n_yt == n_ch):
        return ""

    # Strip common channel suffixes
    cleaned = re.sub(
        r"\s+(?:Official|Music|VEVO|Records)\s*$",
        "", yt_artist, flags=re.IGNORECASE,
    ).strip()
    return cleaned


# ---------------------------------------------------------------------------
# MusicBrainz lookups
# ---------------------------------------------------------------------------

# Release-type priority for music videos: single > album > compilation > other
_RELEASE_TYPE_PRIORITY = {
    "single": 0,
    "album": 1,
    "ep": 2,
    "": 3,         # unknown type
    "compilation": 4,
    "live": 5,
    "broadcast": 6,
    "other": 7,
}


def _pick_best_release(
    releases: list,
    allowed_types: Optional[set] = None,
) -> Optional[dict]:
    """Pick the best release from a MusicBrainz release list.

    Priority: Single > Album > EP > Compilation > Live > Other.
    Within the same type, prefer earlier release dates.

    If *allowed_types* is provided (e.g. ``{"single", "ep"}``), only
    releases whose release-group primary-type is in that set are
    considered.  Returns ``None`` when no release passes the filter.
    """
    if not releases:
        return None

    candidates = releases
    if allowed_types:
        candidates = []
        for rel in releases:
            rg = rel.get("release-group", {})
            rtype = (rg.get("primary-type") or rg.get("type") or "").lower()
            if rtype in allowed_types:
                candidates.append(rel)
        if not candidates:
            return None

    def _sort_key(rel):
        rg = rel.get("release-group", {})
        rtype = (rg.get("primary-type") or rg.get("type") or "").lower()
        priority = _RELEASE_TYPE_PRIORITY.get(rtype, 99)
        date = rel.get("date", "") or ""
        return (priority, date)

    return sorted(candidates, key=_sort_key)[0]


def _confirm_single_via_artist(
    mb_artist_id: str,
    title: str,
    similarity_threshold: float = 0.6,
) -> Optional[Dict[str, Any]]:
    """Browse an artist's Single release groups to confirm a single exists.

    Returns the matching release-group dict if a single with a similar title
    is found, or None if the artist has no such single on MusicBrainz.
    """
    if not mb_artist_id or not title:
        return None
    from difflib import SequenceMatcher
    import unicodedata
    def _norm(s: str) -> str:
        return unicodedata.normalize("NFKD", s).replace("\u2019", "'").replace("\u2018", "'").lower().strip()
    title_norm = _norm(title)
    try:
        browse = musicbrainzngs.browse_release_groups(
            artist=mb_artist_id, release_type=["single"], limit=100,
        )
        time.sleep(1.1)
    except Exception as e:
        logger.warning(f"browse_release_groups for artist {mb_artist_id} failed: {e}")
        return None
    best_rg = None
    best_sim = 0.0
    import re as _re_cs
    for rg in browse.get("release-group-list", []):
        rg_title = _norm(rg.get("title", ""))
        sim = SequenceMatcher(None, title_norm, rg_title).ratio()
        # Handle parenthetical subtitle mismatches — boost similarity when
        # base titles match exactly, but keep actual similarity if higher
        # (e.g. full title match should beat a base-only match).
        if sim < similarity_threshold:
            _bq = _re_cs.sub(r"\s*\(.*?\)", "", title_norm).strip()
            _bc = _re_cs.sub(r"\s*\(.*?\)", "", rg_title).strip()
            if _bq and _bc and _bq == _bc:
                sim = max(sim, 0.70)
        if sim >= similarity_threshold and sim > best_sim:
            best_sim = sim
            best_rg = rg
    if best_rg:
        logger.info(
            f"Single confirmed via artist browse: '{best_rg.get('title')}' "
            f"matches '{title}' (sim={best_sim:.2f}, rg={best_rg['id']})"
        )
        return best_rg
    logger.info(
        f"No matching single found in artist's releases for '{title}' "
        f"(artist={mb_artist_id}, checked {len(browse.get('release-group-list', []))} singles)"
    )
    return None


_EXCLUDED_SECONDARY_TYPES = frozenset({
    "compilation", "dj-mix", "soundtrack", "spokenword",
    "interview", "audiobook", "audio drama", "live", "remix",
})


def _find_parent_album(recording_id: str) -> Optional[Dict[str, Any]]:
    """Find the parent Album release group for a recording.

    Given a recording ID (from a single), browse all releases that contain
    the recording and look for a release group with primary-type "Album"
    and no excluded secondary types (compilation, DJ-mix, soundtrack, etc.).
    Returns the first clean Album found with its release/release-group IDs,
    or None if no album is found.
    """
    if not recording_id:
        return None
    try:
        releases = musicbrainzngs.browse_releases(
            recording=recording_id, limit=25,
            includes=["release-groups", "artist-credits"],
        )
        time.sleep(1.1)
    except Exception as e:
        logger.warning(f"browse_releases for recording {recording_id} failed: {e}")
        return None

    _VA_MBID = "89ad4ac3-39f7-470e-963a-56509c546377"

    def _extract_album(rel_list, exclude_types=_EXCLUDED_SECONDARY_TYPES):
        for rel in rel_list:
            rg = rel.get("release-group", {})
            rg_type = (rg.get("type") or rg.get("primary-type") or "").lower()
            if rg_type != "album":
                continue
            secondary = {s.lower() for s in rg.get("secondary-type-list", [])}
            if secondary & exclude_types:
                continue
            # Skip Various Artists releases not tagged as compilations
            ac = rel.get("artist-credit", [])
            if ac and isinstance(ac[0], dict):
                artist_id = ac[0].get("artist", {}).get("id", "")
                if artist_id == _VA_MBID:
                    logger.info(
                        f"Skipping Various Artists release: "
                        f"'{rg.get('title')}' (rg={rg.get('id')})"
                    )
                    continue
            album_title = rg.get("title") or rel.get("title")
            album_release_id = rel.get("id")
            album_rg_id = rg.get("id")

            # Scan remaining releases in same release group for one with cover art
            for rel2 in rel_list:
                rg2 = rel2.get("release-group", {})
                if rg2.get("id") == album_rg_id:
                    caa = rel2.get("cover-art-archive", {})
                    if caa.get("front") in (True, "true"):
                        album_release_id = rel2.get("id")
                        break

            logger.info(
                f"Parent album found: '{album_title}' "
                f"(rg={album_rg_id}, release={album_release_id})"
            )
            return {
                "album": album_title,
                "mb_album_release_id": album_release_id,
                "mb_album_release_group_id": album_rg_id,
            }
        return None

    rel_list = releases.get("release-list", [])
    # Pass 1: prefer clean artist albums (no soundtracks, compilations, etc.)
    result = _extract_album(rel_list)
    if result:
        return result
    # Pass 2: allow soundtracks as fallback for tracks only released on a soundtrack
    return _extract_album(rel_list, exclude_types=_EXCLUDED_SECONDARY_TYPES - {"soundtrack"})


def _find_album_by_artist_browse(
    mb_artist_id: str,
    title: str,
) -> Optional[Dict[str, Any]]:
    """Fallback album lookup: browse artist's Album release groups for a matching track.

    When ``_find_parent_album`` fails (single's recording ID not on any album),
    this function searches the artist's album release groups for one containing
    a recording whose title is similar to *title*.

    Only the earliest-dated release within each release group is checked, so
    that bonus tracks added on reissues/deluxe editions do not cause a wrong
    album to be selected (e.g. a track originally from album B would not match
    album A just because a later reissue of A added it as a bonus).
    """
    if not mb_artist_id or not title:
        return None

    from difflib import SequenceMatcher

    try:
        browse = musicbrainzngs.browse_release_groups(
            artist=mb_artist_id, release_type=["album"], limit=50,
        )
        time.sleep(1.1)
    except Exception as e:
        logger.warning(f"browse_release_groups for artist {mb_artist_id} failed: {e}")
        return None

    title_lower = title.lower().strip()

    # Track best soundtrack match as fallback for tracks only on soundtracks
    _soundtrack_fallback: Optional[Dict[str, Any]] = None

    for rg in browse.get("release-group-list", []):
        rg_type = (rg.get("type") or rg.get("primary-type") or "").lower()
        if rg_type != "album":
            continue
        # Skip compilations, DJ-mixes, etc. — but track soundtracks for fallback
        secondary = {s.lower() for s in rg.get("secondary-type-list", [])}
        _is_soundtrack = "soundtrack" in secondary
        if secondary & (_EXCLUDED_SECONDARY_TYPES - {"soundtrack"}):
            continue
        if _is_soundtrack and _soundtrack_fallback is not None:
            # Already have a soundtrack fallback, skip additional ones
            continue
        rg_id = rg.get("id")
        if not rg_id:
            continue

        # Get releases in this release group to check their recordings
        try:
            releases = musicbrainzngs.browse_releases(
                release_group=rg_id, limit=5,
                includes=["recordings"],
            )
            time.sleep(1.1)
        except Exception:
            continue

        rel_list = releases.get("release-list", [])
        if not rel_list:
            continue

        # Only check the earliest release to avoid matching bonus tracks
        # on reissues/deluxe editions.
        rel_list.sort(key=lambda r: r.get("date", "9999"))
        rel = rel_list[0]

        for medium in rel.get("medium-list", []):
            for track in medium.get("track-list", []):
                rec = track.get("recording", {})
                rec_title = rec.get("title", "")
                sim = SequenceMatcher(None, title_lower, rec_title.lower()).ratio()
                # Handle parenthetical subtitle mismatches
                # (e.g. "Run (Beautiful Things)" vs "Run")
                if sim < 0.80:
                    import re as _re_ab
                    _bq = _re_ab.sub(r"\s*\(.*?\)", "", title_lower).strip()
                    _bc = _re_ab.sub(r"\s*\(.*?\)", "", rec_title.lower()).strip()
                    if _bq and _bc and (_bq == _bc or SequenceMatcher(None, _bq, _bc).ratio() >= 0.85):
                        sim = max(sim, 0.80)
                # Handle slash-separated tracks (medleys, hidden tracks)
                # e.g. "After All These Years / [untitled]", "Song A / Song B"
                if sim < 0.80 and " / " in rec_title.lower():
                    for _slash_part in rec_title.lower().split(" / "):
                        _sp = _re_ab.sub(r"\s*[\[\(].*?[\]\)]\s*$", "", _slash_part).strip()
                        if not _sp:
                            continue
                        _sp_sim = SequenceMatcher(None, title_lower, _sp).ratio()
                        if _sp_sim >= 0.80:
                            sim = _sp_sim
                            break
                if sim >= 0.80:
                    album_title = rg.get("title") or rel.get("title")
                    album_release_id = rel.get("id")
                    if _is_soundtrack:
                        # Save as fallback, keep looking for a clean album
                        if _soundtrack_fallback is None:
                            _soundtrack_fallback = {
                                "album": album_title,
                                "mb_album_release_id": album_release_id,
                                "mb_album_release_group_id": rg_id,
                            }
                        break
                    logger.info(
                        f"Album found via artist browse: '{album_title}' contains "
                        f"'{rec_title}' (sim={sim:.2f} for '{title}', "
                        f"rg={rg_id}, release={album_release_id})"
                    )
                    return {
                        "album": album_title,
                        "mb_album_release_id": album_release_id,
                        "mb_album_release_group_id": rg_id,
                    }

    # Fallback to soundtrack if no clean album found
    if _soundtrack_fallback:
        logger.info(
            f"Album found via artist browse (soundtrack fallback): "
            f"'{_soundtrack_fallback['album']}' for '{title}' (artist={mb_artist_id})"
        )
        return _soundtrack_fallback

    logger.info(
        f"No album found via artist browse for '{title}' (artist={mb_artist_id})"
    )
    return None


def _search_single_release_group(
    artist: str,
    title: str,
) -> Optional[Dict[str, Any]]:
    """Search MusicBrainz for a Single (or EP) release group matching artist + title.

    Uses ``search_release_groups`` with ``primarytype:single`` which
    directly returns singles — unlike ``search_recordings`` whose
    truncated release-lists often omit single releases entirely.
    When no single is found, a second search with ``primarytype:ep``
    is attempted as a fallback.

    Returns a dict with mb_artist_id, mb_recording_id, mb_release_id,
    mb_release_group_id, artist, title, album, year, genres — or None
    if no single/EP was found.
    """
    from app.services.source_validation import parse_multi_artist
    primary_artist, _ = parse_multi_artist(artist)

    # Score each candidate: prefer exact title + artist matches, penalise variants
    from difflib import SequenceMatcher
    import unicodedata
    def _norm(s: str) -> str:
        """Normalize unicode quotes/apostrophes for comparison."""
        return unicodedata.normalize("NFKD", s).replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    title_lower = _norm(title.lower().strip())
    primary_lower = _norm(primary_artist.lower().strip())

    # Search for singles first; if no good match, try EPs as fallback
    scored: list = []
    for search_type in ("single", "ep"):
        kwargs = {"releasegroup": title, "primarytype": search_type, "limit": 10}
        if primary_artist:
            kwargs["artist"] = primary_artist

        try:
            rg_result = musicbrainzngs.search_release_groups(**kwargs)
            time.sleep(1.1)
        except Exception as e:
            logger.warning(f"MusicBrainz release-group search failed: {e}")
            continue

        rg_list = rg_result.get("release-group-list", [])
        if not rg_list:
            continue

        for rg in rg_list:
            rtype = (rg.get("primary-type") or rg.get("type") or "").lower()
            if rtype not in ("single", "ep"):
                continue
            secondary = [s.lower() for s in rg.get("secondary-type-list", [])]
            rg_title_lower = _norm((rg.get("title") or "").lower().strip())
            score = 0

            # Prefer singles over EPs — EPs are an acceptable fallback
            if rtype == "ep":
                score -= 20

            # Artist match is critical — extract candidate artist name
            ac_list_cand = rg.get("artist-credit", [])
            cand_artist = ""
            if ac_list_cand and isinstance(ac_list_cand[0], dict) and "artist" in ac_list_cand[0]:
                cand_artist = _norm(ac_list_cand[0]["artist"].get("name", "").lower().strip())
            # Also build the full joined artist-credit string
            # e.g. [{artist: "Ida Corr"}, " vs. ", {artist: "Fedde Le Grand"}]
            # -> "Ida Corr vs. Fedde Le Grand"
            _full_credit = ""
            for _ac_part in ac_list_cand:
                if isinstance(_ac_part, dict) and "artist" in _ac_part:
                    _full_credit += _ac_part["artist"].get("name", "")
                elif isinstance(_ac_part, str):
                    _full_credit += _ac_part
            _full_credit_lower = _norm(_full_credit.lower().strip())
            if cand_artist == primary_lower or _full_credit_lower == primary_lower:
                score += 50
            elif cand_artist:
                _primary_sim = SequenceMatcher(None, primary_lower, cand_artist).ratio()
                artist_sim = _primary_sim
                # Compare against full credit too, but only when the
                # primary credit has *some* similarity (>= 0.3).  This
                # prevents split/collaboration singles like
                # "Iron & Wine / American Football" from scoring as a
                # strong match when querying "American Football" — the
                # query artist is a secondary credit, not primary.
                if _full_credit_lower and _primary_sim >= 0.3:
                    artist_sim = max(artist_sim, SequenceMatcher(None, primary_lower, _full_credit_lower).ratio())
                # Guard against false similarity from shared stopwords
                # e.g. "stuck in the sound" vs "mountains in the sea" = 0.53
                # due to shared "in the s", but zero content words overlap.
                # Also catches partial word overlap like "the sound ninja" (Jaccard 0.33).
                if 0.4 <= artist_sim < 0.7:
                    _STOP = {"the","a","an","in","on","at","of","and","or","vs","vs.","&","de","le","la","les","el","y","et"}
                    _q_words = set(primary_lower.split()) - _STOP
                    _c_words = set(cand_artist.split()) - _STOP
                    _union = _q_words | _c_words
                    if _union:
                        _jaccard = len(_q_words & _c_words) / len(_union)
                        if _jaccard < 0.4:
                            artist_sim = 0.0
                if artist_sim >= 0.7:
                    score += 30
                elif artist_sim >= 0.4:
                    score += 10
                else:
                    score -= 50  # Wrong artist entirely

            # Exact title match is strongly preferred
            if rg_title_lower == title_lower:
                score += 100
            else:
                _raw_sim = SequenceMatcher(None, title_lower, rg_title_lower).ratio()
                # When one title has a parenthetical subtitle the other lacks
                # (e.g. "I Ran (So Far Away)" vs "I Ran"), compare base titles.
                import re as _re_sc
                _base_q = _re_sc.sub(r"\s*\(.*?\)", "", title_lower).strip()
                _base_c = _re_sc.sub(r"\s*\(.*?\)", "", rg_title_lower).strip()
                if _base_q and _base_c and _base_q == _base_c:
                    _raw_sim = max(_raw_sim, 0.90)
                score += int(_raw_sim * 50)
            # Penalise remixes / variants
            if "remix" in secondary:
                score -= 20
            # Penalise titles with extra qualifiers (sped up, slowed, remix, etc.)
            if any(q in rg_title_lower for q in ["sped up", "slowed", "remix", "edit", "version", "acoustic", "live"]):
                if any(q in rg_title_lower for q in ["sped up", "slowed", "remix"]):
                    score -= 30
                else:
                    score -= 10
            # Prefer older releases (originals) via first-release-date
            frd = rg.get("first-release-date", "")
            if frd and len(frd) >= 4:
                try:
                    yr = int(frd[:4])
                    # Slight preference for older releases
                    score += max(0, 2030 - yr)
                except ValueError:
                    pass
            scored.append((score, rg))

        if scored:
            break  # Good match found with this type, skip fallback search

    # Fallback: if splitting the artist name produced a different primary and
    # the search found nothing, retry with the full original artist string.
    # Handles bands like "Amanda Palmer & The Grand Theft Orchestra" where
    # the full name is the MB artist entry.
    if not scored and primary_artist.lower() != artist.lower():
        logger.info(
            f"MusicBrainz single search: no results for primary "
            f"'{primary_artist}', retrying with full artist '{artist}'"
        )
        primary_artist = artist
        primary_lower = _norm(artist.lower().strip())
        for search_type in ("single", "ep"):
            kwargs = {"releasegroup": title, "primarytype": search_type, "limit": 10}
            kwargs["artist"] = artist
            try:
                rg_result = musicbrainzngs.search_release_groups(**kwargs)
                time.sleep(1.1)
            except Exception as e:
                logger.warning(f"MusicBrainz release-group search (full artist) failed: {e}")
                continue
            rg_list = rg_result.get("release-group-list", [])
            if not rg_list:
                continue
            for rg in rg_list:
                rtype = (rg.get("primary-type") or rg.get("type") or "").lower()
                if rtype not in ("single", "ep"):
                    continue
                secondary = [s.lower() for s in rg.get("secondary-type-list", [])]
                rg_title_lower = _norm((rg.get("title") or "").lower().strip())
                score = 0
                if rtype == "ep":
                    score -= 20
                ac_list_cand = rg.get("artist-credit", [])
                cand_artist = ""
                if ac_list_cand and isinstance(ac_list_cand[0], dict) and "artist" in ac_list_cand[0]:
                    cand_artist = _norm(ac_list_cand[0]["artist"].get("name", "").lower().strip())
                if cand_artist == primary_lower:
                    score += 50
                elif cand_artist:
                    artist_sim = SequenceMatcher(None, primary_lower, cand_artist).ratio()
                    if artist_sim >= 0.7:
                        score += 30
                    elif artist_sim >= 0.4:
                        score += 10
                    else:
                        score -= 50
                if rg_title_lower == title_lower:
                    score += 100
                else:
                    import re as _re_fb
                    _raw_sim = SequenceMatcher(None, title_lower, rg_title_lower).ratio()
                    _base_q = _re_fb.sub(r"\s*\(.*?\)", "", title_lower).strip()
                    _base_c = _re_fb.sub(r"\s*\(.*?\)", "", rg_title_lower).strip()
                    if _base_q and _base_c and _base_q == _base_c:
                        _raw_sim = max(_raw_sim, 0.90)
                    score += int(_raw_sim * 50)
                if "remix" in secondary:
                    score -= 20
                if any(q in rg_title_lower for q in ["sped up", "slowed", "remix", "edit", "version", "acoustic", "live"]):
                    if any(q in rg_title_lower for q in ["sped up", "slowed", "remix"]):
                        score -= 30
                    else:
                        score -= 10
                frd = rg.get("first-release-date", "")
                if frd and len(frd) >= 4:
                    try:
                        yr = int(frd[:4])
                        score += max(0, 2030 - yr)
                    except ValueError:
                        pass
                scored.append((score, rg))
            if scored:
                break

    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    best_rg = scored[0][1]

    # Gate: the best candidate's title must be similar enough to the query.
    # Without this, artist-only matches (e.g. same artist, different song)
    # would be returned when no matching single exists on MusicBrainz.
    best_title_lower = _norm((best_rg.get("title") or "").lower().strip())
    title_sim = SequenceMatcher(None, title_lower, best_title_lower).ratio()
    # When titles differ by a parenthetical subtitle (e.g. query "I Ran (So Far Away)"
    # vs MB result "I Ran"), also compare the base titles stripped of parentheticals.
    if title_sim < 0.6:
        import re as _re_ts
        _strip_parens = lambda s: _re_ts.sub(r"\s*\(.*?\)", "", s).strip()
        _base_query = _strip_parens(title_lower)
        _base_cand = _strip_parens(best_title_lower)
        if _base_query and _base_cand:
            _base_sim = SequenceMatcher(None, _base_query, _base_cand).ratio()
            # One must be a substring/prefix of the other, or high base similarity
            if _base_sim >= 0.85 or _base_query == _base_cand:
                title_sim = max(title_sim, _base_sim)
    if title_sim < 0.6:
        logger.info(
            f"MusicBrainz Single search: best candidate "
            f"'{best_rg.get('title')}' doesn't match '{title}' "
            f"(title_sim={title_sim:.2f}), discarding"
        )
        return None

    rg_id = best_rg["id"]
    rg_title = best_rg.get("title", title)

    # Extract artist from release group
    mb_artist_id = None
    artist_name = artist
    ac_list = best_rg.get("artist-credit", [])
    if ac_list:
        ac = ac_list[0] if isinstance(ac_list, list) else ac_list
        if isinstance(ac, dict) and "artist" in ac:
            artist_name = ac["artist"].get("name", artist)
            mb_artist_id = ac["artist"].get("id")

    # Build full joined artist-credit for comparison
    _full_artist_credit = ""
    for _ac_part in ac_list:
        if isinstance(_ac_part, dict) and "artist" in _ac_part:
            _full_artist_credit += _ac_part["artist"].get("name", "")
        elif isinstance(_ac_part, str):
            _full_artist_credit += _ac_part

    # Validate: result artist must be similar to query artist.
    # Use primary artist (strips "feat." credits) for comparison so that
    # "AronChupa featuring Little Sis Nora" still matches "AronChupa".
    # Also compare against full artist-credit string for multi-artist
    # releases (e.g. "Ida Corr vs. Fedde Le Grand").
    if artist and artist_name:
        from difflib import SequenceMatcher
        _sim_full = SequenceMatcher(None, artist.lower(), artist_name.lower()).ratio()
        _sim_primary = SequenceMatcher(None, primary_artist.lower(), artist_name.lower()).ratio()
        _sim = max(_sim_full, _sim_primary)
        if _full_artist_credit and _sim >= 0.3:
            _sim = max(_sim, SequenceMatcher(None, artist.lower(), _full_artist_credit.lower()).ratio())
            _sim = max(_sim, SequenceMatcher(None, primary_artist.lower(), _full_artist_credit.lower()).ratio())
        if _sim < 0.5:
            logger.info(
                f"MusicBrainz Single search: '{artist_name}' doesn't match "
                f"'{artist}' (similarity={_sim:.2f}), discarding"
            )
            return None
        # Extra guard: marginal similarity may be inflated by shared
        # stopwords (e.g. "stuck in the sound" vs "mountains in the sea")
        # or partial word overlap (e.g. "the sound ninja").
        # Require sufficient content-word Jaccard similarity.
        if _sim < 0.7:
            _STOP = {"the","a","an","in","on","at","of","and","or","vs","vs.","&","de","le","la","les","el","y","et"}
            _q_words = set(primary_artist.lower().split()) - _STOP
            _c_words = set(artist_name.lower().split()) - _STOP
            _union = _q_words | _c_words
            if _union and len(_q_words & _c_words) / len(_union) < 0.4:
                logger.info(
                    f"MusicBrainz Single search: '{artist_name}' has low word "
                    f"overlap with '{artist}' (sim={_sim:.2f}), discarding"
                )
                return None

    # Year from first-release-date
    year = None
    frd = best_rg.get("first-release-date", "")
    if frd and len(frd) >= 4:
        try:
            year = int(frd[:4])
        except ValueError:
            pass

    # Genres from release group tags
    rg_tags = best_rg.get("tag-list", [])
    genres = [capitalize_genre(t["name"]) for t in rg_tags
              if "name" in t and int(t.get("count", 0)) >= 1]

    # Pick the best release inside this release group.
    # Prefer one with front cover art.
    rg_releases = best_rg.get("release-list", [])
    mb_release_id = None
    if rg_releases:
        # Prefer release with cover art
        for rel in rg_releases:
            caa = rel.get("cover-art-archive", {})
            if caa.get("front") in (True, "true"):
                mb_release_id = rel.get("id")
                break
        if not mb_release_id:
            mb_release_id = rg_releases[0].get("id")

    # Browse releases to get the recording ID from the tracklist
    mb_recording_id = None
    if rg_id:
        try:
            browse = musicbrainzngs.browse_releases(
                release_group=rg_id, includes=["recordings"]
            )
            time.sleep(1.1)
            for rel in browse.get("release-list", []):
                # Also update mb_release_id if we find one with cover art
                caa = rel.get("cover-art-archive", {})
                if caa.get("front") in (True, "true") and not mb_release_id:
                    mb_release_id = rel.get("id")
                # Get recording from first track of first medium
                for medium in rel.get("medium-list", []):
                    for track in medium.get("track-list", []):
                        rec = track.get("recording", {})
                        rec_title = (rec.get("title") or "").lower()
                        # Match the main song, not remixes
                        if rec_title == title.lower() or track.get("position") == "1" or track.get("number") == "1":
                            mb_recording_id = rec.get("id")
                            break
                    if mb_recording_id:
                        break
                if mb_recording_id:
                    break
        except Exception as e:
            logger.warning(f"MusicBrainz browse_releases failed for rg={rg_id}: {e}")

    # If we still don't have a release_id, try to get it from browse results
    if not mb_release_id and rg_releases:
        mb_release_id = rg_releases[0].get("id")

    logger.info(
        f"MusicBrainz Single found: rg={rg_id}, "
        f"release={mb_release_id}, recording={mb_recording_id}, "
        f"'{artist_name} - {rg_title}'"
    )

    # Find the parent album that this single's recording appears on.
    # e.g. "Cosmic Love" single → "Lungs" album
    parent_album = _find_parent_album(mb_recording_id)

    # When _find_parent_album returns an EP whose name matches the track
    # title (e.g. EP "Alright" for track "Alright"), the EP is effectively
    # the single itself — not a parent album.  Discard and try the artist-
    # browse fallback so the real album can be discovered (e.g. "I Should
    # Coco").
    if parent_album and rg_title:
        _pa_name = (parent_album.get("album") or "").lower().strip()
        _rg_lower = rg_title.lower().strip()
        if _pa_name and _pa_name == _rg_lower:
            logger.info(
                f"Parent album '{parent_album['album']}' matches title "
                f"'{rg_title}' — discarding EP-as-album, trying artist browse"
            )
            parent_album = None

    # Fallback: when _find_parent_album returns None (the single's recording
    # doesn't appear on an album release), browse the artist's album release
    # groups and look for one containing a recording with a similar title.
    # This handles the common case where MusicBrainz treats the single and
    # album versions as separate recording entities.
    if not parent_album and mb_artist_id:
        parent_album = _find_album_by_artist_browse(mb_artist_id, rg_title)

    album_name = None  # Only set if a real parent album is found
    mb_album_release_id = None
    mb_album_release_group_id = None
    if parent_album:
        album_name = parent_album["album"]
        mb_album_release_id = parent_album.get("mb_album_release_id")
        mb_album_release_group_id = parent_album.get("mb_album_release_group_id")
        logger.info(f"Single '{rg_title}' is from album '{album_name}'")

    return {
        "mb_artist_id": mb_artist_id,
        "mb_recording_id": mb_recording_id,
        "mb_release_id": mb_release_id,
        "mb_release_group_id": rg_id,
        "artist": artist_name,
        "title": rg_title,
        "album": album_name,
        "mb_album_release_id": mb_album_release_id,
        "mb_album_release_group_id": mb_album_release_group_id,
        "year": year,
        "genres": genres,
    }


def search_musicbrainz(
    artist: str,
    title: str,
) -> Dict[str, Any]:
    """
    Search MusicBrainz for a recording matching artist + title.

    Strategy:
      1. Search release groups with primarytype:single (finds singles directly)
      2. Fall back to search_recordings only if no single is found

    Returns dict with keys:
        mb_artist_id, mb_recording_id, mb_release_id,
        mb_release_group_id, artist, title, album, year, genres
    """
    _init_musicbrainz()
    result = {
        "mb_artist_id": None,
        "mb_recording_id": None,
        "mb_release_id": None,
        "mb_release_group_id": None,
        "artist": artist,
        "title": title,
        "album": None,
        "year": None,
        "genres": [],
    }

    if not artist and not title:
        return result

    try:
        # --- Strategy 1: Search for Single release groups ---
        single_result = _search_single_release_group(artist, title)
        if single_result:
            result.update(single_result)
            # If genres are sparse from the release group, supplement from artist
            if len(result["genres"]) < 2 and result["mb_artist_id"]:
                try:
                    artist_info = musicbrainzngs.get_artist_by_id(
                        result["mb_artist_id"], includes=["tags"]
                    )
                    time.sleep(1.1)
                    artist_tags = artist_info.get("artist", {}).get("tag-list", [])
                    extra = [capitalize_genre(t["name"]) for t in artist_tags
                             if "name" in t and int(t.get("count", 0)) >= 2][:5]
                    # Merge without duplicates
                    existing = {g.lower() for g in result["genres"]}
                    for g in extra:
                        if g.lower() not in existing:
                            result["genres"].append(g)
                            existing.add(g.lower())
                except Exception:
                    pass
            logger.info(f"MusicBrainz match (single): {result['artist']} - {result['title']}")
            return result

        # --- Strategy 2: Fall back to recording search ---
        logger.info(f"No Single release group found for '{title}', falling back to recording search")
        # Use keyword args for better search precision (Lucene query format
        # can mishandle stopwords and special characters in titles).
        _rec_kwargs: Dict[str, Any] = {"recording": title, "limit": 10}
        if artist:
            from app.services.source_validation import parse_multi_artist as _pma_rec
            _primary_rec, _ = _pma_rec(artist)
            _rec_kwargs["artist"] = _primary_rec or artist

        mb_result = musicbrainzngs.search_recordings(**_rec_kwargs)
        time.sleep(1.1)
        recordings = mb_result.get("recording-list", [])

        if not recordings:
            logger.info(f"No MusicBrainz results for: {artist} - {title}")
            return result

        # Filter to recordings whose title is similar enough to the query
        # and whose artist matches.
        from difflib import SequenceMatcher as _SM
        import unicodedata as _ud
        def _rnorm(s: str) -> str:
            return _ud.normalize("NFKD", s).replace("\u2019", "'").replace("\u2018", "'").lower().strip()
        _title_norm = _rnorm(title)
        _artist_norm = _rnorm(_primary_rec or artist) if artist else ""
        _filtered = []
        for rec_candidate in recordings:
            rec_title = _rnorm(rec_candidate.get("title", ""))
            tsim = _SM(None, _title_norm, rec_title).ratio()
            # Handle parenthetical subtitle mismatches (e.g. "I Ran (So Far Away)" vs "I Ran")
            if tsim < 0.6:
                import re as _re_rf
                _bq = _re_rf.sub(r"\s*\(.*?\)", "", _title_norm).strip()
                _bc = _re_rf.sub(r"\s*\(.*?\)", "", rec_title).strip()
                if _bq and _bc and (_bq == _bc or _SM(None, _bq, _bc).ratio() >= 0.85):
                    tsim = max(tsim, 0.6)
            if tsim < 0.6:
                continue
            # Validate artist
            if _artist_norm:
                ac = rec_candidate.get("artist-credit", [])
                cand_artist = ""
                if ac and isinstance(ac[0], dict) and "artist" in ac[0]:
                    cand_artist = _rnorm(ac[0]["artist"].get("name", ""))
                asim = _SM(None, _artist_norm, cand_artist).ratio()
                # Also check full joined artist-credit string
                _fc = ""
                for _acp in ac:
                    if isinstance(_acp, dict) and "artist" in _acp:
                        _fc += _acp["artist"].get("name", "")
                    elif isinstance(_acp, str):
                        _fc += _acp
                if _fc:
                    asim = max(asim, _SM(None, _artist_norm, _rnorm(_fc)).ratio())
                if asim < 0.5:
                    continue
                # Guard against false similarity from shared stopwords
                # (e.g. "stuck in the sound" vs "the sound ninja" = 0.55).
                if asim < 0.7:
                    _STOP_R = {"the","a","an","in","on","at","of","and","or","vs","vs.","&","de","le","la","les","el","y","et"}
                    _qw = set(_artist_norm.split()) - _STOP_R
                    _cw = set(cand_artist.split()) - _STOP_R
                    _uw = _qw | _cw
                    if _uw and len(_qw & _cw) / len(_uw) < 0.4:
                        continue
            _filtered.append(rec_candidate)
        if not _filtered:
            logger.info(f"MusicBrainz recording search: no validated results for '{artist} - {title}'")
            return result
        recordings = _filtered

        # Pick best recording with best release-type priority
        # First pass: prefer single/EP releases to avoid picking album releases.
        best_rec = None
        best_rel = None
        best_priority = 999
        for rec_candidate in recordings:
            releases = rec_candidate.get("release-list", [])
            # Prefer single/EP releases; fall back to any type only if needed
            rel = _pick_best_release(releases, allowed_types={"single", "ep"})
            if not rel:
                rel = _pick_best_release(releases)
            if rel:
                rg = rel.get("release-group", {})
                rtype = (rg.get("primary-type") or rg.get("type") or "").lower()
                priority = _RELEASE_TYPE_PRIORITY.get(rtype, 99)
                if priority < best_priority:
                    best_priority = priority
                    best_rec = rec_candidate
                    best_rel = rel
            elif best_rec is None:
                best_rec = rec_candidate

        if best_rec is None:
            best_rec = recordings[0]

        rec = best_rec
        result["mb_recording_id"] = rec.get("id")
        result["title"] = rec.get("title", title)

        # Artist
        artist_credits = rec.get("artist-credit", [])
        if artist_credits:
            ac = artist_credits[0]
            if isinstance(ac, dict) and "artist" in ac:
                result["artist"] = ac["artist"].get("name", artist)
                result["mb_artist_id"] = ac["artist"].get("id")

        # Release
        if best_rel is None:
            releases = rec.get("release-list", [])
            best_rel = _pick_best_release(releases, allowed_types={"single", "ep"})
            if not best_rel:
                best_rel = _pick_best_release(releases)

        if best_rel:
            rg = best_rel.get("release-group", {})
            rel_type = (rg.get("primary-type") or rg.get("type") or "").lower()

            # Secondary confirmation: when the recording's release is NOT a
            # single or EP (e.g. it's an album track), browse the artist's singles
            # to confirm whether a single actually exists for this title.
            # If no single exists, don't assign the album release as the
            # single's mb_release_id — the track is just an album track.
            if rel_type not in ("single", "ep") and result.get("mb_artist_id"):
                confirmed_single = _confirm_single_via_artist(
                    result["mb_artist_id"], title
                )
                if confirmed_single:
                    # A single exists — use the confirmed single's
                    # release group instead of the album release.
                    result["mb_release_group_id"] = confirmed_single["id"]
                    # Browse the single's release group to get a proper
                    # single release ID (with cover art preference)
                    # instead of using the album release.
                    _single_release_id = None
                    try:
                        _single_browse = musicbrainzngs.browse_releases(
                            release_group=confirmed_single["id"],
                            includes=["recordings"],
                        )
                        time.sleep(1.1)
                        for _srel in _single_browse.get("release-list", []):
                            _caa = _srel.get("cover-art-archive", {})
                            if _caa.get("front") in (True, "true"):
                                _single_release_id = _srel.get("id")
                                break
                        if not _single_release_id:
                            _srel_list = _single_browse.get("release-list", [])
                            if _srel_list:
                                _single_release_id = _srel_list[0].get("id")
                    except Exception as e:
                        logger.warning(
                            f"browse_releases for single rg={confirmed_single['id']} "
                            f"failed: {e}"
                        )
                    result["mb_release_id"] = _single_release_id or best_rel.get("id")
                    logger.info(
                        f"Recording found on '{best_rel.get('title')}', "
                        f"but single confirmed via artist browse "
                        f"(rg={confirmed_single['id']}, "
                        f"release={result['mb_release_id']})"
                    )
                    # Find the actual parent album via recording browse
                    # (same as Strategy 1). The non-single release title
                    # (e.g. a compilation) is NOT a valid album.
                    _parent = _find_parent_album(result["mb_recording_id"])
                    if not _parent:
                        _parent = _find_album_by_artist_browse(
                            result["mb_artist_id"], title
                        )
                    if _parent:
                        result["album"] = _parent["album"]
                        result["mb_album_release_id"] = _parent.get("mb_album_release_id")
                        result["mb_album_release_group_id"] = _parent.get("mb_album_release_group_id")
                        logger.info(f"Parent album resolved: '{_parent['album']}'")
                else:
                    # No matching single for this artist — this is just an
                    # album track. Store the recording/artist IDs but do NOT
                    # set album or mb_release_id from the album release, as
                    # that would falsely imply this is a single.
                    logger.info(
                        f"Recording found on '{best_rel.get('title')}' but no "
                        f"matching single exists for this artist — skipping "
                        f"album/release assignment"
                    )
                    # Still resolve the parent album so the MB album link
                    # and _has_parent_album guard work correctly downstream.
                    _parent = _find_parent_album(result["mb_recording_id"])
                    if not _parent and result.get("mb_artist_id"):
                        _parent = _find_album_by_artist_browse(
                            result["mb_artist_id"], title
                        )
                    if _parent:
                        result["album"] = _parent["album"]
                        result["mb_album_release_id"] = _parent.get("mb_album_release_id")
                        result["mb_album_release_group_id"] = _parent.get("mb_album_release_group_id")
                        logger.info(f"Parent album resolved for album track: '{_parent['album']}'")
                    # Still extract year from the release date
                    date = best_rel.get("date", "")
                    if date and len(date) >= 4:
                        try:
                            result["year"] = int(date[:4])
                        except ValueError:
                            pass
                    # Skip setting release_group_id and further date extraction
                    # below — we intentionally leave those unset for album-only
                    # tracks with no confirmed single.
            else:
                # B-side detection: when the recording appears on a single
                # but the single's title doesn't match the recording title,
                # the recording is a B-side/bonus track on someone else's
                # single (e.g. "L.G. FUAD" live on the "Broken Heart" single).
                # Don't use that single's release group — look for the correct
                # single or fall back to album-only.
                _rg_title_lower = (rg.get("title") or "").lower().strip()
                _rec_title_lower = (rec.get("title") or title).lower().strip()
                _rg_title_sim = _SM(None, _rec_title_lower, _rg_title_lower).ratio()
                import re as _re_bside
                if _rg_title_sim < 0.6:
                    _bq = _re_bside.sub(r"\s*\(.*?\)", "", _rec_title_lower).strip()
                    _bc = _re_bside.sub(r"\s*\(.*?\)", "", _rg_title_lower).strip()
                    if _bq and _bc and _bq == _bc:
                        _rg_title_sim = max(_rg_title_sim, 0.70)

                if _rg_title_sim < 0.6:
                    # Recording is a B-side on a different single.
                    logger.info(
                        f"Recording '{rec.get('title')}' is a B-side on single "
                        f"'{rg.get('title')}' (sim={_rg_title_sim:.2f}) — "
                        f"checking for correct single via artist browse"
                    )
                    _correct_single = None
                    if result.get("mb_artist_id"):
                        _correct_single = _confirm_single_via_artist(
                            result["mb_artist_id"], title
                        )
                    if _correct_single:
                        result["mb_release_group_id"] = _correct_single["id"]
                        _single_release_id = None
                        try:
                            _single_browse = musicbrainzngs.browse_releases(
                                release_group=_correct_single["id"],
                                includes=["recordings"],
                            )
                            time.sleep(1.1)
                            for _srel in _single_browse.get("release-list", []):
                                _caa = _srel.get("cover-art-archive", {})
                                if _caa.get("front") in (True, "true"):
                                    _single_release_id = _srel.get("id")
                                    break
                            if not _single_release_id:
                                _srel_list = _single_browse.get("release-list", [])
                                if _srel_list:
                                    _single_release_id = _srel_list[0].get("id")
                        except Exception as e:
                            logger.warning(
                                f"browse_releases for correct single "
                                f"rg={_correct_single['id']} failed: {e}"
                            )
                        result["mb_release_id"] = _single_release_id or best_rel.get("id")
                        logger.info(
                            f"Recording found as B-side on '{rg.get('title')}', "
                            f"correct single found via artist browse "
                            f"(rg={_correct_single['id']}, "
                            f"release={result['mb_release_id']})"
                        )
                    else:
                        # No matching single for this title — treat as
                        # album-only track (don't use wrong single's IDs).
                        logger.info(
                            f"Recording found as B-side on '{rg.get('title')}' "
                            f"but no matching single exists — skipping "
                            f"single release assignment"
                        )
                    # In both cases, find the parent album.
                    _parent = _find_parent_album(result["mb_recording_id"])
                    if not _parent and result.get("mb_artist_id"):
                        _parent = _find_album_by_artist_browse(
                            result["mb_artist_id"], title
                        )
                    if _parent:
                        result["album"] = _parent["album"]
                        result["mb_album_release_id"] = _parent.get("mb_album_release_id")
                        result["mb_album_release_group_id"] = _parent.get("mb_album_release_group_id")
                        logger.info(f"Parent album resolved: '{_parent['album']}'")
                else:
                    # Single title matches recording — this IS the correct single.
                    result["mb_release_id"] = best_rel.get("id")
                    if rg.get("id") and not result.get("mb_release_group_id"):
                        result["mb_release_group_id"] = rg["id"]
                    # Find parent album via recording browse (same as
                    # Strategy 1). When the best release IS a single, its
                    # title is just the song name — not a useful album.
                    _parent = _find_parent_album(result["mb_recording_id"])
                    if not _parent and result.get("mb_artist_id"):
                        _parent = _find_album_by_artist_browse(
                            result["mb_artist_id"], title
                        )
                    if _parent:
                        result["album"] = _parent["album"]
                        result["mb_album_release_id"] = _parent.get("mb_album_release_id")
                        result["mb_album_release_group_id"] = _parent.get("mb_album_release_group_id")
                        logger.info(f"Parent album resolved: '{_parent['album']}'")
                    else:
                        result["album"] = best_rel.get("title")

            date = best_rel.get("date", "")
            if date and len(date) >= 4 and not result.get("year"):
                try:
                    result["year"] = int(date[:4])
                except ValueError:
                    pass

        # Tags/genres
        tags = rec.get("tag-list", [])
        result["genres"] = [capitalize_genre(t["name"]) for t in tags
                            if "name" in t and int(t.get("count", 0)) >= 2]

        if not result["genres"] and result["mb_artist_id"]:
            try:
                artist_info = musicbrainzngs.get_artist_by_id(
                    result["mb_artist_id"], includes=["tags"]
                )
                time.sleep(1.1)
                artist_tags = artist_info.get("artist", {}).get("tag-list", [])
                result["genres"] = [capitalize_genre(t["name"]) for t in artist_tags
                                    if "name" in t and int(t.get("count", 0)) >= 2][:5]
            except Exception:
                pass

        logger.info(f"MusicBrainz match (fallback): {result['artist']} - {result['title']}")

    except musicbrainzngs.WebServiceError as e:
        logger.warning(f"MusicBrainz API error: {e}")
    except Exception as e:
        logger.error(f"MusicBrainz search error: {e}")

    return result


# ---------------------------------------------------------------------------
# Wikipedia scraping (inspired by reference script)
# ---------------------------------------------------------------------------


def _build_wikipedia_url(page_title: str) -> str:
    """Build a properly-encoded Wikipedia URL from a page title.

    Replaces spaces with underscores (Wikipedia convention) and percent-encodes
    special characters like '?' that would otherwise break the URL.
    """
    from urllib.parse import quote
    slug = page_title.replace(' ', '_')
    # quote() with safe='/:_(),' preserves standard Wikipedia URL chars
    # but encodes ?#& etc. that break URL parsing
    return f"https://en.wikipedia.org/wiki/{quote(slug, safe='/:_(),-')}"


def _normalize_title_for_match(s: str) -> str:
    """Normalize common word/number substitutions in song titles.

    E.g. 'Put Your Hands Up for Detroit' -> 'put your hands up 4 detroit'
    so it can match 'Put Your Hands Up 4 Detroit' from Wikipedia.
    Normalises to the shorter (numeral/symbol) form.
    """
    if not s:
        return s
    s = s.lower().strip()
    _WORD_TO_NUM = {
        r'\bfor\b': '4', r'\btwo\b': '2', r'\bto\b': '2',
        r'\btoo\b': '2', r'\bone\b': '1', r'\bwon\b': '1',
        r'\bfour\b': '4', r'\bate\b': '8', r'\beight\b': '8',
        r'\byou\b': 'u', r'\bare\b': 'r', r'\band\b': '&',
    }
    for pat, repl in _WORD_TO_NUM.items():
        s = re.sub(pat, repl, s)
    return s


def search_wikipedia(title: str, artist: str) -> Optional[str]:
    """
    Search Wikipedia for a music single/song page.
    Returns the best matching Wikipedia URL or None.

    Strategy:
    1. Search with progressively less-specific queries.
    2. For each query, get up to 5 results.
    3. Score each result against the known artist+title.
    4. Return the best-scoring page that looks like a song article.
    """
    # Strip featuring suffixes — "(feat. X)", "(ft. X)", "(featuring X)"
    # so that titles like "Big Enough (feat. Alex Cameron, ...)" don't
    # poison search queries and the similarity gate.
    title = re.sub(r'\s*\((?:feat\.?|ft\.?|featuring)\s+.*?\)\s*$', '', title, flags=re.IGNORECASE).strip()

    # Strip remix/version suffixes — "(Fatboy Slim Remix)", "(Acoustic Version)"
    # so that "I See You Baby (Fatboy Slim Remix)" matches the Wikipedia page
    # "I See You Baby".
    title = re.sub(r'\s*\([^)]*(?:remix|version|mix|edit|remaster(?:ed)?)\)\s*$', '', title, flags=re.IGNORECASE).strip()

    # Build a normalized title variant for search queries so that
    # "Put Your Hands Up for Detroit" also finds "... 4 Detroit".
    _title_norm = _normalize_title_for_match(title)
    _title_variant = None
    if _title_norm != title.lower().strip():
        _title_variant = _title_norm

    search_terms = [
        f"{title} ({artist} song)",
        f"{title} (song)",
        f"{artist} {title} single",
        f"{artist} {title}",
        f"{title} {artist} song",
        f'"{title}" {artist}',
        title,
    ]
    # Add variant search terms when normalization produced a different form
    if _title_variant:
        search_terms.insert(2, f"{_title_variant} (song)")
        search_terms.append(_title_variant)

    # Normalize Unicode hyphens (U+2010, U+2011, U+2013, U+2014, U+2212) → ASCII
    _UNICODE_HYPHENS = re.compile(r'[\u2010\u2011\u2013\u2014\u2212]')
    artist = _UNICODE_HYPHENS.sub('-', artist) if artist else artist
    title = _UNICODE_HYPHENS.sub('-', title) if title else title
    artist_lower = artist.lower().strip() if artist else ""
    title_lower = title.lower().strip() if title else ""
    # Build normalized variants of artist name for fuzzy matching
    # (e.g. "Florence + the Machine" ↔ "Florence and the Machine")
    _artist_variants = {artist_lower}
    if "+" in artist_lower:
        _artist_variants.add(artist_lower.replace("+", "and"))
    if " and " in artist_lower:
        _artist_variants.add(artist_lower.replace(" and ", " + "))
    # For hyphenated names (e.g. "a-ha"), also match without hyphens
    if "-" in artist_lower:
        _artist_variants.add(artist_lower.replace("-", ""))
        _artist_variants.add(artist_lower.replace("-", " "))

    # Collect unique candidate URLs in priority order
    seen_titles: set = set()
    candidates: List[Dict[str, Any]] = []

    for search_term in search_terms:
        results = _wikipedia_search_api(search_term, limit=5)
        for r in results:
            page_title = r["title"]
            if page_title in seen_titles:
                continue
            seen_titles.add(page_title)

            # Score the result
            pt_lower = page_title.lower()
            # Normalized form for word/number variant matching
            _pt_norm = _normalize_title_for_match(pt_lower)
            # Strip HTML tags and entities from snippets for accurate matching
            _raw_snippet = r.get("snippet", "")
            _clean_snippet = re.sub(r"<[^>]+>", "", _raw_snippet)
            _clean_snippet = _clean_snippet.replace("&quot;", '"').replace("&amp;", "&").replace("&#039;", "'")
            snippet_lower = _clean_snippet.lower()
            score = 0

            # Page title contains the song title (or normalized variant)
            if title_lower and (title_lower in pt_lower or _title_norm in _pt_norm):
                score += 3
            # Exact title match (after stripping disambiguation tag) is the
            # strongest signal that this page is about the song we want.
            _pt_base = re.sub(r"\s*\(.*?\)\s*$", "", pt_lower).strip()
            _pt_base_norm = _normalize_title_for_match(_pt_base)
            if title_lower and (_pt_base == title_lower or _pt_base_norm == _title_norm):
                score += 2
            # When the song title itself contains parentheses (e.g.
            # "Dude (Looks Like a Lady)"), the strip above removes the
            # essential part.  Award the bonus for an exact full match too.
            if title_lower and "(" in title_lower and pt_lower == title_lower:
                score += 2
            # Page title contains the artist
            if artist_lower and any(av in pt_lower for av in _artist_variants):
                score += 2
            # Snippet mentions artist
            if artist_lower and any(av in snippet_lower for av in _artist_variants):
                score += 2
            # Snippet mentions song / single / music video
            if any(w in snippet_lower for w in ["song", "single", "music video", "track"]):
                score += 2
            # Page title uses "(song)" or "(artist song)" disambiguation
            # Use regex to also match "(Artist song)" forms
            _song_disambig = re.search(r"\(([^)]*)\b(?:song|single)\)$", pt_lower)
            if _song_disambig:
                score += 3
                # If the disambiguation names a DIFFERENT artist, heavily
                # penalise — e.g. "Here Without You (The Byrds song)" when
                # searching for 3 Doors Down.
                _disambig_text = _song_disambig.group(1).strip()
                if _disambig_text and artist_lower:
                    _disambig_has_our_artist = any(
                        av in _disambig_text for av in _artist_variants
                    )
                    if not _disambig_has_our_artist:
                        score -= 6  # Wrong artist's song
            if artist_lower and any(f"({av}" in pt_lower for av in _artist_variants):
                score += 2
            # De-score artist / discography / band pages (this is a SONG search)
            _artist_page_tags = [
                "(band)", "(musician)", "(singer)", "(rapper)",
                "(group)", "discography",
            ]
            if any(tag in pt_lower for tag in _artist_page_tags):
                score -= 3
            # De-score album pages — this is a SONG search, not an album search.
            # Catches "(album)", "(Awolnation album)", "(studio album)", etc.
            if re.search(r'\((?:[^)]*\b)?album\b(?:[^)]*)?\)$', pt_lower):
                score -= 5
            # Penalize pages that match the artist name but NOT the song
            # title — these are likely artist/bio pages, not song pages.
            if (artist_lower and title_lower
                    and any(av in pt_lower for av in _artist_variants)
                    and title_lower not in pt_lower
                    and _title_norm not in _pt_norm):
                score -= 2
            # De-score disambiguation / list pages
            if "(disambiguation)" in pt_lower or pt_lower.startswith("list of"):
                score -= 5
            # De-score disambiguation pages detected by snippet content
            # (catches pages like "Oh Lord" that ARE disambiguation but lack
            # the "(disambiguation)" suffix in their title)
            _disambig_snippet_kw = [
                "may refer to", "can refer to", "may also refer to",
                "commonly refers to", "most commonly refers to",
            ]
            if any(w in snippet_lower for w in _disambig_snippet_kw):
                score -= 10
            # Secondary disambig detection: when the search API returns a
            # snippet starting mid-page (highlighting a specific artist
            # entry), "may refer to" is truncated away.  Multiple
            # "a song by" occurrences in a single snippet strongly
            # indicate a disambiguation list, not a real article.
            elif len(re.findall(r"a song by", snippet_lower)) >= 2:
                score -= 10
            # De-score TV show / film / series / game pages
            _non_music = [
                "(tv series)", "(tv show)", "(film)", "(movie)",
                "(television)", "(video game)", "(game)", "(novel)",
                "(book)", "(play)", "(musical)",
            ]
            if any(tag in pt_lower for tag in _non_music):
                score -= 5
            # Also check snippet for TV/film indicators
            _non_music_snippet = [
                "television series", "tv series", "reality show",
                "television show", "american film", "british film",
                "video game", "board game",
            ]
            if any(w in snippet_lower for w in _non_music_snippet):
                score -= 3

            candidates.append({
                "title": page_title,
                "score": score,
            })

    if not candidates:
        return None

    # Sort by score descending
    candidates.sort(key=lambda c: c["score"], reverse=True)

    # Minimum score threshold — a score below 6 means neither the title
    # nor the artist matched convincingly.  Returning a low-quality match
    # produces irrelevant Wikipedia data (wrong artist bios, wrong genres)
    # and contaminates entity resolution downstream.
    MIN_SCORE = 6

    from difflib import SequenceMatcher

    # ── Multi-phase verification ──
    # Phase 1: Score + title similarity gate (search API only, fast)
    # Phase 2: Scrape article and verify infobox artist matches expected
    #
    # This prevents false positives where a different artist's song with
    # the same title (e.g. "Keith" by Toby Keith vs "Keith" by Playlunch)
    # passes title matching but belongs to the wrong artist.
    _MAX_VERIFY = 3  # Max candidates to scrape-verify
    _verified = 0

    for candidate in candidates:
        if candidate["score"] < MIN_SCORE:
            break  # Remaining candidates score even lower

        # Title similarity gate
        _clean_page = re.sub(r"\s*\(.*?\)\s*$", "", candidate["title"]).strip()
        _clean_title = re.sub(r"\s*\(.*?\)\s*$", "", title).strip()
        _clean_title_lower = _clean_title.lower() if _clean_title else title_lower
        _sim_full = SequenceMatcher(None, title_lower, candidate["title"].lower()).ratio()
        _sim = SequenceMatcher(None, _clean_title_lower, _clean_page.lower()).ratio()
        _sim_norm = SequenceMatcher(None, _title_norm, _normalize_title_for_match(_clean_page.lower())).ratio()
        _best_sim = max(_sim_full, _sim, _sim_norm)
        if _best_sim < 0.6:
            logger.info(f"Wikipedia: candidate '{candidate['title']}' title similarity "
                         f"{_best_sim:.2f} < 0.6 for '{title}', skipping")
            continue

        # Phase 2: Scrape article and verify infobox artist
        _url = _build_wikipedia_url(candidate["title"])
        try:
            _wiki = scrape_wikipedia_page(_url)
        except Exception as e:
            logger.warning(f"Wikipedia: scrape failed for '{candidate['title']}': {e}")
            _verified += 1
            if _verified >= _MAX_VERIFY:
                break
            continue

        _scraped_artist = _wiki.get("artist") or ""
        _scraped_primary = _wiki.get("primary_artist") or ""

        # Reject album / artist pages — search_wikipedia is for SONG pages
        _page_type = _wiki.get("page_type") or ""
        if _page_type in ("album", "artist"):
            logger.info(
                f"Wikipedia: candidate '{candidate['title']}' (score={candidate['score']}) "
                f"is a {_page_type} page, not a song page — skipping"
            )
            _verified += 1
            if _verified >= _MAX_VERIFY:
                break
            continue

        # If infobox has an artist, verify it matches expected
        if _scraped_artist and artist_lower:
            _check_artist = _scraped_primary or _scraped_artist
            _norm_scraped = _normalize_for_compare(_check_artist)
            _norm_expected = _normalize_for_compare(artist)
            _artist_ok = (
                _norm_scraped == _norm_expected
                or _norm_scraped in _norm_expected
                or _norm_expected in _norm_scraped
                or _tokens_overlap(_check_artist, artist, 0.5)
            )
            if not _artist_ok:
                logger.info(
                    f"Wikipedia: candidate '{candidate['title']}' (score={candidate['score']}) "
                    f"has artist '{_scraped_artist}' — doesn't match expected '{artist}', "
                    f"trying next candidate"
                )
                _verified += 1
                if _verified >= _MAX_VERIFY:
                    break
                continue

        # If no infobox artist, check first paragraph for expected artist
        if not _scraped_artist and artist_lower:
            _plot = (_wiki.get("plot") or "").lower()[:500]
            if _plot and not any(av in _plot for av in _artist_variants):
                logger.info(
                    f"Wikipedia: candidate '{candidate['title']}' (score={candidate['score']}) "
                    f"— no infobox artist and '{artist}' not in first paragraph, "
                    f"trying next candidate"
                )
                _verified += 1
                if _verified >= _MAX_VERIFY:
                    break
                continue

        # Passed all gates
        logger.info(f"Wikipedia best match: '{candidate['title']}' (score={candidate['score']}) "
                    f"from {len(candidates)} candidates [artist verified]")
        return _url

    # No candidate passed verification
    logger.info(f"Wikipedia: no candidate passed artist verification for "
                f"'{artist} - {title}' ({len(candidates)} candidates checked)")
    return None


def resolve_artist_wikipedia_via_mb(mb_artist_id: str) -> Optional[str]:
    """Resolve an artist's Wikipedia URL via MusicBrainz URL relations → Wikidata.

    When text-based Wikipedia search fails (common for artists with generic
    names like "Trucks", "America", "Berlin"), this function uses the
    authoritative MusicBrainz → Wikidata → Wikipedia sitelink chain.

    Returns the full English Wikipedia URL or None.
    """
    if not mb_artist_id:
        return None
    try:
        _init_musicbrainz()
        artist_info = musicbrainzngs.get_artist_by_id(
            mb_artist_id, includes=["url-rels"]
        )
        time.sleep(1.1)
        wikidata_url = None
        for rel in artist_info.get("artist", {}).get("url-relation-list", []):
            if rel.get("type") == "wikidata":
                wikidata_url = rel.get("target", "")
                break
        if not wikidata_url:
            return None
        qid = wikidata_url.rsplit("/", 1)[-1]
        if not qid.startswith("Q"):
            return None
        headers = {"User-Agent": _WIKI_USER_AGENT, "Api-User-Agent": _WIKI_USER_AGENT}
        resp = httpx.get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbgetentities",
                "ids": qid,
                "props": "sitelinks",
                "sitefilter": "enwiki",
                "format": "json",
            },
            headers=headers,
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        title = (data.get("entities", {})
                 .get(qid, {})
                 .get("sitelinks", {})
                 .get("enwiki", {})
                 .get("title"))
        if not title:
            return None
        url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
        logger.info(f"Wikipedia artist via MB→Wikidata: {url}")
        return url
    except Exception as e:
        logger.debug(f"MB→Wikidata artist Wikipedia resolution failed: {e}")
        return None


def search_wikipedia_artist(artist: str) -> Optional[str]:
    """Search Wikipedia for an artist page. Returns URL or None."""
    if not artist:
        return None

    # Normalize Unicode hyphens (U+2010, U+2011, U+2013, U+2014, U+2212) → ASCII
    _UNICODE_HYPHENS = re.compile(r'[\u2010\u2011\u2013\u2014\u2212]')
    artist = _UNICODE_HYPHENS.sub('-', artist)
    artist_lower = artist.lower().strip()
    _artist_variants = {artist_lower}
    if "+" in artist_lower:
        _artist_variants.add(artist_lower.replace("+", "and"))
    if " and " in artist_lower:
        _artist_variants.add(artist_lower.replace(" and ", " + "))
    # For hyphenated names (e.g. "a-ha"), also match without hyphens ("aha")
    # and with spaces ("a ha") so page title matching is more robust.
    if "-" in artist_lower:
        _artist_variants.add(artist_lower.replace("-", ""))
        _artist_variants.add(artist_lower.replace("-", " "))

    # Extract primary artist from featuring credits so "AronChupa featuring
    # Little Sis Nora" also matches the Wikipedia page "AronChupa".
    from app.services.source_validation import parse_multi_artist as _pma_wiki
    _primary, _ = _pma_wiki(artist)
    if _primary and _primary.lower().strip() != artist_lower:
        _artist_variants.add(_primary.lower().strip())

    # Use primary artist for search terms (better Wikipedia results)
    _search_name = _primary or artist

    search_terms = [
        f"{_search_name} (band)", f"{_search_name} (musician)",
        f"{_search_name} (singer)", f"{_search_name} (rapper)", _search_name,
    ]
    # For short or hyphenated names (e.g. "a-ha"), add a quoted variant so the
    # Wikipedia search API doesn't split on hyphens or return irrelevant results.
    if "-" in _search_name or len(_search_name) <= 4:
        search_terms.insert(0, f'"{_search_name}" band')
    # Also include full name if different
    if _search_name.lower() != artist_lower:
        search_terms.append(artist)

    seen: set = set()
    candidates: List[Dict[str, Any]] = []

    for term in search_terms:
        for r in _wikipedia_search_api(term, limit=5):
            title = r["title"]
            if title in seen:
                continue
            seen.add(title)
            pt_lower = title.lower()
            _raw_snippet = r.get("snippet", "")
            _clean_snippet = re.sub(r"<[^>]+>", "", _raw_snippet)
            _clean_snippet = _clean_snippet.replace("&quot;", '"').replace("&amp;", "&").replace("&#039;", "'")
            snippet_lower = _clean_snippet.lower()
            score = 0

            # Artist name MUST appear in page title — otherwise it's a
            # completely unrelated person (e.g. "Omi (singer)" for AronChupa).
            # Strip periods for comparison so "Kirin J Callinan" matches
            # Wikipedia's "Kirin J. Callinan" (periods break substring match).
            _pt_no_dots = pt_lower.replace(".", "")
            if any(av in pt_lower or av.replace(".", "") in _pt_no_dots for av in _artist_variants):
                score += 4
            else:
                score -= 10  # Heavy penalty: wrong artist entirely

            # Penalize when the page title (sans disambiguation suffix)
            # contains extra words beyond the artist name — likely a
            # different entity (e.g. "Blackshape Prime" for "Blackshape").
            _stripped_title = re.sub(r"\s*\(.*?\)\s*$", "", pt_lower).strip()
            from difflib import SequenceMatcher as _SM_title
            _best_title_sim = max(
                _SM_title(None, av, _stripped_title).ratio()
                for av in _artist_variants
            )
            if _best_title_sim < 0.85:
                score -= 3  # e.g. "Blackshape Prime" ≠ "Blackshape"

            if any(kw in pt_lower for kw in ["(band)", "(musician)", "(singer)", "(rapper)", "(group)"]):
                score += 3
            if any(kw in snippet_lower for kw in ["band", "musician", "singer", "rapper", "songwriter", "artist"]):
                score += 2
            if "(disambiguation)" in pt_lower or pt_lower.startswith("list of"):
                score -= 5
            # Catch song/album/single/ep disambiguation tags even when
            # the artist name appears inside the parenthetical, e.g.
            # "YOLO (The Lonely Island song)" — literal "(song)" won't match.
            if re.search(r"\([^)]*\b(?:song|album|single|ep)\)$", pt_lower):
                score -= 3
            _non_music_tags = [
                "(tv series)", "(tv show)", "(film)", "(movie)",
                "(television)", "(video game)",
            ]
            if any(tag in pt_lower for tag in _non_music_tags):
                score -= 5
            # Penalise pages whose snippet identifies them as a
            # song / single / album / EP — not an artist or band.
            _work_phrases = [
                "is a song", "is a single", "is an album", "is an ep",
                "is the debut album", "is the debut single",
                "is a studio album", "is a compilation album",
                "is a live album", "is a greatest hits",
                "is the second album", "is the third album",
                "is the fourth album", "is the fifth album",
            ]
            if any(phrase in snippet_lower for phrase in _work_phrases):
                score -= 5

            candidates.append({"title": title, "score": score})

    if not candidates:
        return None
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]
    if best["score"] < 4:
        logger.info(f"Wikipedia artist: best match '{best['title']}' scored {best['score']} "
                     f"(< 4), discarding for '{artist}'")
        return None

    # Final guard: verify the Wikidata short description doesn't identify
    # the page as a song, single, album, or EP.
    _wd_desc = _get_wiki_short_description(best["title"])
    if _wd_desc:
        _wd_lower = _wd_desc.lower()
        _work_kw = ["single by", "song by", "album by", "ep by",
                     "compilation album", "soundtrack album",
                     "live album", "greatest hits"]
        if any(kw in _wd_lower for kw in _work_kw):
            logger.info(f"Wikipedia artist: '{best['title']}' rejected — "
                        f"Wikidata desc '{_wd_desc}' indicates a musical work")
            return None

    logger.info(f"Wikipedia artist match: '{best['title']}' (score={best['score']})")
    return _build_wikipedia_url(best['title'])


def search_wikipedia_album(artist: str, album: str) -> Optional[str]:
    """Search Wikipedia for an album page. Returns URL or None."""
    if not album:
        return None

    # Normalize Unicode hyphens (U+2010, U+2011, U+2013, U+2014, U+2212) → ASCII
    _UNICODE_HYPHENS = re.compile(r'[\u2010\u2011\u2013\u2014\u2212]')
    artist = _UNICODE_HYPHENS.sub('-', artist) if artist else artist
    album = _UNICODE_HYPHENS.sub('-', album)
    album_lower = album.lower().strip()
    search_terms = [
        f"{album} ({artist} album)",
        f"{album} (album)",
        f"{artist} {album} album",
        f"{album} {artist}",
        album,
    ]

    seen: set = set()
    candidates: List[Dict[str, Any]] = []

    for term in search_terms:
        for r in _wikipedia_search_api(term, limit=5):
            title = r["title"]
            if title in seen:
                continue
            seen.add(title)
            pt_lower = title.lower()
            _raw_snippet = r.get("snippet", "")
            _clean_snippet = re.sub(r"<[^>]+>", "", _raw_snippet)
            _clean_snippet = _clean_snippet.replace("&quot;", '"').replace("&amp;", "&").replace("&#039;", "'")
            snippet_lower = _clean_snippet.lower()
            score = 0

            if album_lower in pt_lower:
                score += 3
            # Match any disambiguation containing "album" or "ep",
            # e.g. "(album)", "(5 Seconds of Summer album)"
            _album_disambig = re.search(r"\(([^)]*)\b(?:album|ep)\)$", pt_lower)
            if _album_disambig:
                score += 3
                # Bonus if our artist appears inside the disambiguation
                _ad_text = _album_disambig.group(1).strip()
                if artist and _ad_text and artist.lower() in _ad_text:
                    score += 3
            if any(kw in snippet_lower for kw in ["album", "studio album", "ep", "released"]):
                score += 2
            if artist and artist.lower() in snippet_lower:
                score += 2
            if "(disambiguation)" in pt_lower or pt_lower.startswith("list of"):
                score -= 5
            if any(tag in pt_lower for tag in ["(song)", "(single)", "(band)", "(musician)"]):
                score -= 3
            _non_music_tags = [
                "(tv series)", "(tv show)", "(film)", "(movie)",
                "(television)", "(video game)", "(aircraft)",
                "(vehicle)", "(company)", "(software)", "(ship)",
            ]
            if any(tag in pt_lower for tag in _non_music_tags):
                score -= 5
            # De-score snippets that indicate non-music content
            _non_music_album_snippet = [
                "aircraft", "airplane", "airline", "helicopter",
                "automobile", "vehicle", "locomotive", "warship",
                "footballer", "basketball player", "cricketer",
                "politician", "prime minister",
            ]
            if any(w in snippet_lower for w in _non_music_album_snippet):
                score -= 3

            candidates.append({"title": title, "score": score})

    if not candidates:
        return None
    candidates.sort(key=lambda c: c["score"], reverse=True)

    from difflib import SequenceMatcher
    _artist_lower = (artist or "").lower().strip()
    for cand in candidates:
        if cand["score"] < 4:
            break
        clean_title = re.sub(r"\s*\([^)]*\)\s*$", "", cand["title"]).strip()
        sim = SequenceMatcher(None, album.lower(), clean_title.lower()).ratio()
        # Self-titled album fallback: when the stripped title matches the
        # artist name, the real album name lives in the disambiguation
        # text (e.g. "Weezer (Teal Album)" → compare against "Teal Album").
        if sim < 0.7 and _artist_lower and clean_title.lower().strip() == _artist_lower:
            _dm = re.search(r"\(([^)]+)\)$", cand["title"])
            if _dm:
                _dtext = _dm.group(1).strip()
                if _dtext:
                    sim = SequenceMatcher(
                        None, album.lower(), _dtext.lower()
                    ).ratio()
        if sim < 0.7:
            logger.info(f"Wikipedia album: '{cand['title']}' title similarity "
                         f"{sim:.2f} < 0.7 for '{album}', skipping")
            continue
        logger.info(f"Wikipedia album match: '{cand['title']}' (score={cand['score']}, sim={sim:.2f})")
        return _build_wikipedia_url(cand['title'])

    logger.info(f"Wikipedia album: no candidate passed both score and similarity gates for '{album}'")
    return None


def _get_wiki_short_description(title: str) -> Optional[str]:
    """Fetch the Wikidata short description for a Wikipedia page title."""
    try:
        resp = httpx.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "titles": title,
                "prop": "pageprops",
                "ppprop": "wikibase-shortdesc",
                "format": "json",
            },
            headers={"User-Agent": _WIKI_USER_AGENT, "Api-User-Agent": _WIKI_USER_AGENT},
            timeout=10,
        )
        pages = resp.json().get("query", {}).get("pages", {})
        for page in pages.values():
            return page.get("pageprops", {}).get("wikibase-shortdesc")
    except Exception:
        pass
    return None


def _wikipedia_search_api(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Query Wikipedia search API and return up to *limit* results.

    Each result dict has at least 'title' and 'snippet' keys.
    """
    try:
        headers = {"User-Agent": _WIKI_USER_AGENT, "Api-User-Agent": _WIKI_USER_AGENT}

        params = {
            "action": "query",
            "list": "search",
            "format": "json",
            "srlimit": limit,
            "srsearch": query,
        }

        resp = httpx.get(
            "https://en.wikipedia.org/w/api.php",
            params=params,
            headers=headers,
            timeout=15,
        )
        data = resp.json()
        return data.get("query", {}).get("search", [])

    except Exception as e:
        logger.warning(f"Wikipedia search error for '{query}': {e}")

    return []


def scrape_wikipedia_page(url: str, expected_artist: Optional[str] = None) -> Dict[str, Any]:
    """
    Scrape a Wikipedia page for music single/song metadata.

    If *expected_artist* is given, the extracted year is cleared when the
    infobox artist doesn't match — this avoids returning the original
    artist's release year for a cover-song article.

    Returns dict with keys:
        title, artist, album, year, genres, plot, image_url, imdb_url,
        page_type, primary_artist, featured_artists
    """
    result = {
        "title": None,
        "artist": None,
        "album": None,
        "year": None,
        "genres": [],
        "plot": None,
        "image_url": None,
        "imdb_url": None,
        "page_type": None,           # "artist", "single", "album", "unrelated"
        "primary_artist": None,      # First credited artist
        "featured_artists": [],      # Additional/featured artists
    }

    try:
        headers = {"User-Agent": _WIKI_USER_AGENT, "Api-User-Agent": _WIKI_USER_AGENT}
        resp = httpx.get(url, headers=headers, timeout=15)

        if resp.status_code != 200:
            logger.warning(f"Wikipedia page fetch failed: {resp.status_code}")
            return result

        soup = BeautifulSoup(resp.text, "html.parser")

        # Detect disambiguation pages — they have no useful music metadata
        _body = soup.find("body")
        if _body and "mw-disambig" in (_body.get("class") or []):
            result["page_type"] = "disambiguation"
            logger.info(f"Wikipedia: disambiguation page detected — {url}")
            return result

        infobox = soup.find("table", {"class": "infobox"})

        if infobox:
            result["title"] = _extract_infobox_title(infobox)
            result["artist"] = _extract_infobox_artist(infobox)
            result["album"] = _extract_infobox_album(infobox)
            result["year"] = _extract_infobox_year(infobox)
            result["genres"] = _extract_infobox_genres(infobox)
            result["image_url"] = _extract_infobox_image(infobox, url)

            # Cover-song guard: if the infobox artist doesn't match the
            # expected artist, the year likely belongs to the original
            # version — clear it to avoid returning a wrong year.
            if (
                expected_artist
                and result.get("year")
                and result.get("artist")
            ):
                def _norm(s: str) -> str:
                    return re.sub(r"[^a-z0-9]", "", s.lower())
                n_expected = _norm(expected_artist)
                n_actual = _norm(result["artist"])
                if (
                    n_expected
                    and n_actual
                    and n_expected not in n_actual
                    and n_actual not in n_expected
                ):
                    logger.info(
                        f"Wikipedia cover-song guard: artist mismatch "
                        f"(expected={expected_artist!r}, got={result['artist']!r}) "
                        f"— clearing year {result['year']}"
                    )
                    result["year"] = None

        # Extract plot / music video description
        result["plot"] = _extract_plot_and_mv_info(soup)

        # Classify page type (artist / single / album / unrelated)
        from app.services.source_validation import classify_wikipedia_page
        _infobox_text = infobox.get_text(separator=" ") if infobox else ""
        _first_para = result.get("plot") or ""
        _page_title = ""
        h1 = soup.find("h1")
        if h1:
            _page_title = h1.get_text(strip=True)
        result["page_type"] = classify_wikipedia_page(
            infobox_text=_infobox_text,
            first_paragraph=_first_para,
            page_title=_page_title,
        )

        # Parse multi-artist for primary artist extraction
        if result.get("artist"):
            from app.services.source_validation import parse_multi_artist
            primary, featured = parse_multi_artist(result["artist"])
            result["primary_artist"] = primary
            result["featured_artists"] = featured

        # Sanitize album (strip "Title - Single" patterns)
        if result.get("album"):
            from app.services.source_validation import sanitize_album
            result["album"] = sanitize_album(
                result["album"],
                title=result.get("title") or "",
            )

        # Extract IMDB link from external links or infobox
        result["imdb_url"] = _extract_imdb_link(soup)

    except Exception as e:
        logger.error(f"Wikipedia scraping error: {e}")

    return result


def _extract_imdb_link(soup) -> Optional[str]:
    """Extract an IMDB link from a Wikipedia page (external links, infobox, or body)."""
    try:
        # Search all links on the page for IMDB URLs
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "imdb.com/title/" in href or "imdb.com/name/" in href:
                # Normalize to full URL
                if href.startswith("//"):
                    href = "https:" + href
                elif href.startswith("/"):
                    continue  # Wikipedia internal link, skip
                # Extract clean IMDB URL (strip tracking params)
                m = re.search(r"(https?://(?:www\.)?imdb\.com/(?:title|name)/[a-z]{2}\d+)", href)
                if m:
                    return m.group(1) + "/"
                return href
    except Exception as e:
        logger.debug(f"IMDB link extraction error: {e}")
    return None


def search_imdb_music_video(artist: str, title: str) -> Optional[str]:
    """Search IMDB suggestion API for a music video entry.

    Returns the IMDB URL (e.g. https://www.imdb.com/title/tt12345678/) or None.
    """
    if not artist or not title:
        return None

    from urllib.parse import quote

    query = f"{artist} {title}".strip()
    first_char = query[0].lower() if query else "a"
    encoded = quote(query.lower())

    url = f"https://v3.sg.media-imdb.com/suggestion/{first_char}/{encoded}.json"

    try:
        resp = httpx.get(
            url, timeout=10,
            headers={"User-Agent": _WIKI_USER_AGENT},
        )
        if resp.status_code != 200:
            logger.debug(f"IMDB suggestion API returned {resp.status_code}")
            return None

        data = resp.json()
        results = data.get("d", [])

        # Priority 1: explicit music video entries
        for r in results:
            qid = (r.get("qid") or "").lower()
            q_label = (r.get("q") or "").lower()
            if qid == "musicvideo" or "music video" in q_label:
                imdb_id = r.get("id", "")
                if imdb_id.startswith("tt"):
                    logger.info(f"IMDB music video match: {r.get('l')} ({imdb_id})")
                    return f"https://www.imdb.com/title/{imdb_id}/"

        # Priority 2: short/video entries whose stars contain the artist
        artist_lower = artist.lower()
        title_lower = title.lower()
        for r in results:
            qid = (r.get("qid") or "").lower()
            q_label = (r.get("q") or "").lower()
            stars = (r.get("s") or "").lower()
            entry_title = (r.get("l") or "").lower()
            imdb_id = r.get("id", "")
            if not imdb_id.startswith("tt"):
                continue
            # Match: stars contain artist AND title is similar
            if artist_lower in stars and title_lower in entry_title:
                logger.info(f"IMDB title+artist match: {r.get('l')} ({imdb_id})")
                return f"https://www.imdb.com/title/{imdb_id}/"

    except Exception as e:
        logger.debug(f"IMDB suggestion search failed: {e}")

    return None


# ---------------------------------------------------------------------------
# Article mismatch detection (no AI required)
# ---------------------------------------------------------------------------

def _normalize_for_compare(s: str) -> str:
    """Normalize a string for fuzzy comparison.

    Lowercases, strips accents/diacritics, removes punctuation, and
    collapses whitespace.
    """
    if not s:
        return ""
    # Lowercase
    s = s.lower().strip()
    # Decompose unicode and strip combining marks (accents)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    # Remove common parenthetical disambiguators
    s = re.sub(r"\(.*?\)", "", s)
    # Remove punctuation
    s = re.sub(r"[^\w\s]", "", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _tokens_overlap(a: str, b: str, min_ratio: float = 0.5) -> bool:
    """Check if two normalized strings share enough word tokens.

    Returns True if at least ``min_ratio`` of the shorter string's tokens
    appear in the longer string (case-insensitive, accent-stripped).
    """
    a_tokens = set(_normalize_for_compare(a).split())
    b_tokens = set(_normalize_for_compare(b).split())
    if not a_tokens or not b_tokens:
        return False
    shorter = a_tokens if len(a_tokens) <= len(b_tokens) else b_tokens
    longer = a_tokens if len(a_tokens) > len(b_tokens) else b_tokens
    overlap = shorter & longer
    return len(overlap) / len(shorter) >= min_ratio


def detect_article_mismatch(
    scraped: Dict[str, Any],
    expected_artist: str,
    expected_title: str,
) -> Optional[str]:
    """
    Detect whether a scraped Wikipedia article is about the wrong song/artist.

    Compares the infobox-extracted artist and title against the expected values
    using normalized token overlap.  Returns a human-readable mismatch reason
    string if a mismatch is detected, or None if the article looks correct.

    Examples of mismatches this catches:
    - Searching for "Kishi Bashi - I Am the Antichrist to You" but finding the
      Marilyn Manson "Antichrist Superstar" article
    - Searching for "Artist A - Song" but finding "Artist B - Song (different)"
    """
    scraped_artist = scraped.get("artist") or ""
    scraped_title = scraped.get("title") or ""

    # Reject disambiguation pages and pages with no identifiable content
    page_type = scraped.get("page_type") or ""
    if page_type == "disambiguation":
        return "disambiguation page — not a music article"
    if page_type == "unrelated" and not scraped_artist and not scraped_title:
        return "unrelated page with no identifiable artist/title"
    if page_type == "album":
        return f"album page detected (page_type='{page_type}') — not a song article"
    if page_type == "artist":
        return f"artist page detected (page_type='{page_type}') — not a song article"

    reasons: List[str] = []

    # If the scraped page has an identifiable artist, check it
    if scraped_artist and expected_artist:
        norm_scraped = _normalize_for_compare(scraped_artist)
        norm_expected = _normalize_for_compare(expected_artist)
        # Exact normalized match or containment check first
        if norm_scraped == norm_expected:
            pass  # Perfect match
        elif norm_scraped in norm_expected or norm_expected in norm_scraped:
            pass  # Substring match (e.g. "The Chats" in "The Chats")
        elif _tokens_overlap(scraped_artist, expected_artist, 0.5):
            pass  # Enough token overlap
        else:
            # Cover song check: the infobox may list the original artist
            # (e.g. Keith Whitley) while the expected artist (e.g. Ronan Keating)
            # performed a cover.  If the expected artist is mentioned in the
            # article body, accept it — the article covers this version too.
            _exp_lower = expected_artist.lower().strip()
            _body_text = (scraped.get("plot") or "").lower()
            _artist_variants = {_exp_lower}
            if "+" in _exp_lower:
                _artist_variants.add(_exp_lower.replace("+", "and"))
            if " and " in _exp_lower:
                _artist_variants.add(_exp_lower.replace(" and ", " + "))
            if "-" in _exp_lower:
                _artist_variants.add(_exp_lower.replace("-", ""))
                _artist_variants.add(_exp_lower.replace("-", " "))
            if any(av in _body_text for av in _artist_variants):
                logger.info(
                    f"Artist mismatch (infobox '{scraped_artist}' vs expected "
                    f"'{expected_artist}') but expected artist found in article "
                    f"body — treating as cover song, accepting article"
                )
            else:
                reasons.append(
                    f"artist mismatch: scraped '{scraped_artist}' vs expected '{expected_artist}'"
                )

    # When the infobox has no artist, fall back to checking the first
    # paragraph.  Song articles almost always mention the artist in the
    # opening sentence ("X is a song by Y").  If it doesn't appear at
    # all, the page is likely about a different artist's song.
    if not scraped_artist and expected_artist:
        _exp_lower = expected_artist.lower().strip()
        _artist_variants = {_exp_lower}
        if "+" in _exp_lower:
            _artist_variants.add(_exp_lower.replace("+", "and"))
        if " and " in _exp_lower:
            _artist_variants.add(_exp_lower.replace(" and ", " + "))
        if "-" in _exp_lower:
            _artist_variants.add(_exp_lower.replace("-", ""))
            _artist_variants.add(_exp_lower.replace("-", " "))
        scraped_plot_lower = (scraped.get("plot") or "").lower()[:800]
        if scraped_plot_lower and not any(av in scraped_plot_lower for av in _artist_variants):
            reasons.append(
                f"artist not found in article: expected '{expected_artist}' "
                f"not mentioned in first paragraph"
            )

    # If the scraped page has an identifiable title, check it
    if scraped_title and expected_title:
        norm_scraped = _normalize_for_compare(scraped_title)
        norm_expected = _normalize_for_compare(expected_title)
        if norm_scraped == norm_expected:
            pass
        elif norm_scraped in norm_expected or norm_expected in norm_scraped:
            pass
        elif _tokens_overlap(scraped_title, expected_title, 0.5):
            pass
        else:
            reasons.append(
                f"title mismatch: scraped '{scraped_title}' vs expected '{expected_title}'"
            )

    # Detect non-music articles (TV show, film, etc.) by checking the plot/description.
    # A song page's plot describes the music; a TV show page's plot describes a show.
    scraped_plot = (scraped.get("plot") or "").lower()[:500]
    if scraped_plot:
        _non_music_phrases = [
            "television series", "tv series", "reality series",
            "television show", "reality show", "television program",
            "premiered on", "seasons and", "was renewed",
            "streaming service",
        ]
        if any(phrase in scraped_plot for phrase in _non_music_phrases):
            reasons.append(
                f"non-music article detected (plot mentions TV/film content)"
            )

    if reasons:
        return "; ".join(reasons)
    return None


def _extract_infobox_title(infobox) -> Optional[str]:
    """Extract song title from infobox."""
    try:
        title_elem = infobox.find("th", {"class": "infobox-above"})
        if title_elem:
            # Title is often in quotes or italic
            i_tag = title_elem.find("i")
            if i_tag:
                return i_tag.get_text(strip=True).strip('"').strip("'")
            return title_elem.get_text(strip=True).strip('"').strip("'")
    except Exception as e:
        logger.debug(f"Title extraction error: {e}")
    return None


def _extract_infobox_artist(infobox) -> Optional[str]:
    """Extract artist from infobox.

    Handles two common layouts:
    1. <td class="infobox-subheader">Single by <a>Foo Fighters</a></td>
    2. <th class="infobox-header description">... by <a>Artist</a></th>
    """
    try:
        # Method 1: infobox-subheader — "Single by Artist" / "Song by Artist"
        for elem in infobox.find_all(["td", "th"], {"class": "infobox-subheader"}):
            text = elem.get_text(strip=True)
            if "by" in text.lower():
                # Prefer linked artist names after "by"
                full_text = str(elem)
                by_idx = full_text.lower().find(" by ")
                if by_idx >= 0:
                    after_by = BeautifulSoup(full_text[by_idx + 4:], "html.parser")
                    links = after_by.find_all("a")
                    if links:
                        return " ".join(a.get_text(strip=True) for a in links)
                # Fallback: text split
                parts = re.split(r"\bby\b", text, flags=re.IGNORECASE)
                if len(parts) >= 2:
                    return parts[-1].strip()

        # Method 2: description headers — "... by Artist"
        for elem in infobox.find_all(["th", "td"], {"class": "infobox-header"}):
            text = elem.get_text(strip=True)
            if "by" in text.lower():
                full_text = str(elem)
                by_idx = full_text.lower().find(" by ")
                if by_idx >= 0:
                    after_by = BeautifulSoup(full_text[by_idx + 4:], "html.parser")
                    links = after_by.find_all("a")
                    if links:
                        return " ".join(a.get_text(strip=True) for a in links)
                parts = re.split(r"\bby\b", text, flags=re.IGNORECASE)
                if len(parts) >= 2:
                    return parts[-1].strip()
    except Exception as e:
        logger.debug(f"Artist extraction error: {e}")
    return None


def _extract_infobox_album(infobox) -> Optional[str]:
    """Extract album from infobox.

    Handles multiple Wikipedia infobox layouts:
    1. Description header: 'from the album <i>Album Name</i>'
    2. Regular row: th='Album' / td='<i>Album Name</i>'
    3. Description cell (td instead of th)
    """
    def _clean_album_text(text: str) -> str:
        """Strip citation references and split concatenated album names."""
        text = re.sub(r"\[\d+\]", "", text).strip()
        # Split concatenated albums: "Album OneandAlbum Two" → "Album One"
        if re.search(r"[a-z]and[A-Z]", text):
            text = re.split(r"(?<=[a-z])and(?=[A-Z])", text)[0].strip()
        return text

    try:
        # Method 1: Description header elements (most common for singles)
        # e.g. <th class="infobox-header ...">from the album <i>Name</i></th>
        for elem in infobox.find_all(["th", "td"], {"class": "infobox-header"}):
            text = elem.get_text(strip=True).lower()
            if "album" in text or " ep " in text or text.endswith(" ep"):
                i_tag = elem.find("i")
                if i_tag:
                    return _clean_album_text(i_tag.get_text(strip=True))
                # Fallback: linked album name after "album" keyword
                full_text = elem.get_text(strip=True)
                m = re.search(r"(?:album|ep)\s+(.+)", full_text, re.IGNORECASE)
                if m:
                    return _clean_album_text(m.group(1).strip())

        # Method 2: Regular infobox rows with "Album" label
        for th in infobox.find_all("th"):
            label = th.get_text(strip=True).lower()
            if label in ("album", "from the album", "from"):
                td = th.find_next_sibling("td")
                if td:
                    i_tag = td.find("i")
                    if i_tag:
                        return _clean_album_text(i_tag.get_text(strip=True))
                    a_tag = td.find("a")
                    if a_tag:
                        return _clean_album_text(a_tag.get_text(strip=True))
                    text = td.get_text(strip=True)
                    text = _clean_album_text(text)
                    if text:
                        return text
    except Exception as e:
        logger.debug(f"Album extraction error: {e}")
    return None


def extract_album_wiki_url_from_single(single_wiki_url: str) -> Optional[str]:
    """Extract the album Wikipedia URL from a single's infobox.

    Most single/song Wikipedia pages contain a "from the album <AlbumName>"
    header in the infobox, where the album name is a link to the album's
    Wikipedia page.  This function fetches the single page and returns that
    album URL if found.

    Returns the full Wikipedia URL or None.
    """
    if not single_wiki_url:
        return None
    try:
        headers = {"User-Agent": _WIKI_USER_AGENT, "Api-User-Agent": _WIKI_USER_AGENT}
        resp = httpx.get(single_wiki_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        infobox = soup.find("table", {"class": "infobox"})
        if not infobox:
            return None

        # Method 1: infobox-header elements with "from the album/EP ..."
        for elem in infobox.find_all(["th", "td"], {"class": "infobox-header"}):
            text = elem.get_text(strip=True).lower()
            # Require "from" to distinguish "from the album X" (single page)
            # from "Studio album by Artist" (album page).  Without this,
            # album pages return the generic /wiki/Album link.
            if "from" in text and ("album" in text or "ep " in text or text.endswith(" ep") or " ep" in text):
                a_tag = elem.find("a", href=True)
                if a_tag:
                    href = a_tag["href"]
                    if href.startswith("/wiki/"):
                        url = f"https://en.wikipedia.org{href}"
                        logger.info(f"Album/EP wiki URL extracted from single infobox: {url}")
                        return url

        # Method 2: Regular row with "Album" / "EP" / "from the album" label
        for th in infobox.find_all("th"):
            label = th.get_text(strip=True).lower()
            if label in ("album", "ep", "from the album", "from the ep", "from"):
                td = th.find_next_sibling("td")
                if td:
                    a_tag = td.find("a", href=True)
                    if a_tag:
                        href = a_tag["href"]
                        if href.startswith("/wiki/"):
                            url = f"https://en.wikipedia.org{href}"
                            logger.info(f"Album/EP wiki URL extracted from single infobox: {url}")
                            return url
    except Exception as e:
        logger.debug(f"Album URL extraction from single page failed: {e}")
    return None


def extract_single_wiki_url_from_album(album_wiki_url: str, track_title: str) -> Optional[str]:
    """Extract a single/song Wikipedia URL from an album page's track listing.

    Scrapes the album Wikipedia page for ``<table class="tracklist">`` tables
    and checks each track row for a title matching *track_title*.  If a matching
    track has a ``/wiki/`` link, return the full Wikipedia URL for that song.

    Returns the full Wikipedia URL string or ``None``.
    """
    if not album_wiki_url or not track_title:
        return None
    try:
        headers = {"User-Agent": _WIKI_USER_AGENT, "Api-User-Agent": _WIKI_USER_AGENT}
        resp = httpx.get(album_wiki_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        _track_lower = track_title.lower().strip()
        _track_norm = re.sub(r"[^a-z0-9 ]", "", _track_lower)

        def _title_matches(cell_norm: str, cell_lower: str) -> bool:
            """Check if the cell text matches the track title.

            Uses prefix matching to handle parenthetical annotations
            common in Wikipedia tracklists (e.g. version notes, remix
            info, featured artists).
            """
            return (cell_norm == _track_norm
                    or cell_norm.startswith(_track_norm + " ")
                    or cell_lower == _track_lower
                    or cell_lower.startswith(_track_lower + " "))

        def _extract_wiki_href(td) -> Optional[str]:
            """Return full Wikipedia URL from a <td>'s /wiki/ link matching track title."""
            for a in td.find_all("a", href=True):
                href = a.get("href", "")
                if not href.startswith("/wiki/") or ":" in href[6:]:
                    continue
                # Validate link text matches track title — avoids
                # returning remixer/producer links from variant rows
                link_text = a.get_text(strip=True).strip('"\u201c\u201d\u2018\u2019')
                link_norm = re.sub(r"[^a-z0-9 ]", "", link_text.lower())
                if link_norm == _track_norm:
                    return f"https://en.wikipedia.org{href}"
            return None

        _found_exact_no_link = False

        for table in soup.find_all("table", {"class": "tracklist"}):
            for row in table.find_all("tr"):
                tds = row.find_all("td")
                if not tds:
                    continue
                title_td = tds[0]
                cell_text = title_td.get_text(strip=True).strip('"\u201c\u201d\u2018\u2019')
                cell_lower = cell_text.lower().strip()
                cell_norm = re.sub(r"[^a-z0-9 ]", "", cell_lower)

                if not _title_matches(cell_norm, cell_lower):
                    # Fallback: check the <a> tag text directly (often the
                    # clean title without parenthetical annotations)
                    for a_tag in title_td.find_all("a", href=True):
                        link_text = a_tag.get_text(strip=True).strip('"\u201c\u201d\u2018\u2019')
                        link_norm = re.sub(r"[^a-z0-9 ]", "", link_text.lower())
                        if link_norm == _track_norm:
                            href = a_tag.get("href", "")
                            if href.startswith("/wiki/") and ":" not in href[6:]:
                                url = f"https://en.wikipedia.org{href}"
                                logger.info(
                                    f"Single wiki URL extracted from album tracklist (link text match): {url}"
                                )
                                return url
                    continue

                # Found matching track — look for a wiki link
                url = _extract_wiki_href(title_td)
                if url:
                    logger.info(
                        f"Single wiki URL extracted from album tracklist: {url}"
                    )
                    return url
                # Exact match without link → track listed but no wiki page
                if cell_norm == _track_norm or cell_lower == _track_lower:
                    _found_exact_no_link = True

        if _found_exact_no_link:
            logger.debug(
                f"Track '{track_title}' found in tracklist (exact) "
                f"but has no wiki link — skipping variant matches"
            )
            return None

        # Fallback: many Wikipedia album pages use <ol> ordered lists
        # instead of <table class="tracklist"> for track listings.
        # Search for <ol> elements within or after the "Track listing" section.
        content = soup.find("div", {"class": "mw-parser-output"})
        if content:
            tl_heading = None
            for heading in content.find_all(["h2", "h3"]):
                heading_text = heading.get_text(strip=True).lower()
                heading_text = re.sub(r"\[edit\]", "", heading_text).strip()
                if "track listing" in heading_text or "track list" in heading_text:
                    tl_heading = heading
                    break
            if tl_heading:
                walk_from = tl_heading
                if tl_heading.parent and "mw-heading" in (
                    tl_heading.parent.get("class") or []
                ):
                    walk_from = tl_heading.parent
                sibling = walk_from.find_next_sibling()
                while sibling:
                    if sibling.name in ["h2"]:
                        break
                    if (
                        sibling.name == "div"
                        and "mw-heading" in (sibling.get("class") or [])
                        and sibling.find("h2")
                    ):
                        break
                    if sibling.name == "ol":
                        for li in sibling.find_all("li", recursive=False):
                            for a_tag in li.find_all("a", href=True):
                                link_text = a_tag.get_text(strip=True).strip(
                                    '"\u201c\u201d\u2018\u2019'
                                )
                                link_norm = re.sub(
                                    r"[^a-z0-9 ]", "", link_text.lower()
                                )
                                if _title_matches(link_norm, link_text.lower().strip()):
                                    href = a_tag.get("href", "")
                                    if (
                                        href.startswith("/wiki/")
                                        and ":" not in href[6:]
                                    ):
                                        url = f"https://en.wikipedia.org{href}"
                                        logger.info(
                                            f"Single wiki URL extracted from album "
                                            f"tracklist (<ol> fallback): {url}"
                                        )
                                        return url
                    sibling = sibling.find_next_sibling()
    except Exception as e:
        logger.debug(f"Single URL extraction from album tracklist failed: {e}")
    return None


def extract_artist_wiki_url_from_page(page_wiki_url: str) -> Optional[str]:
    """Extract the artist's Wikipedia URL from a single or album page's infobox.

    Single/song pages have "Single by <a>Artist</a>" in the infobox subheader.
    Album pages have "Studio album by <a>Artist</a>" similarly.
    This function fetches the page and extracts that artist link.

    Returns the full Wikipedia URL or None.
    """
    if not page_wiki_url:
        return None
    try:
        headers = {"User-Agent": _WIKI_USER_AGENT, "Api-User-Agent": _WIKI_USER_AGENT}
        resp = httpx.get(page_wiki_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        infobox = soup.find("table", {"class": "infobox"})
        if not infobox:
            return None

        # Look for "by <Artist>" in infobox subheader/header elements
        for elem in infobox.find_all(["td", "th"], {"class": ["infobox-subheader", "infobox-header"]}):
            full_html = str(elem)
            by_idx = full_html.lower().find(" by ")
            if by_idx < 0:
                continue
            after_by = BeautifulSoup(full_html[by_idx + 4:], "html.parser")
            links = after_by.find_all("a", href=True)
            for a_tag in links:
                href = a_tag.get("href", "")
                if href.startswith("/wiki/") and ":" not in href[6:]:
                    url = f"https://en.wikipedia.org{href}"
                    logger.info(f"Artist wiki URL extracted from page infobox: {url}")
                    return url
    except Exception as e:
        logger.debug(f"Artist URL extraction from page failed: {e}")
    return None


def _extract_infobox_year(infobox) -> Optional[int]:
    """Extract release year from infobox."""
    try:
        # Look for "Released" row
        for th in infobox.find_all("th"):
            if "released" in th.get_text(strip=True).lower():
                td = th.find_next_sibling("td")
                if td:
                    text = td.get_text(strip=True)
                    # Find 4-digit year
                    year_match = re.search(r"\b(19|20)\d{2}\b", text)
                    if year_match:
                        return int(year_match.group())
    except Exception as e:
        logger.debug(f"Year extraction error: {e}")
    return None


def _extract_infobox_genres(infobox) -> List[str]:
    """Extract genres from infobox."""
    genres = []
    try:
        for th in infobox.find_all("th"):
            if th.get_text(strip=True).lower() == "genre":
                td = th.find_next_sibling("td")
                if td:
                    # Check for list items
                    lis = td.find_all("li")
                    if lis:
                        for li in lis:
                            genre_text = li.get_text(strip=True)
                            # Clean footnote references
                            genre_text = re.sub(r"\[\d+\]", "", genre_text).strip()
                            if genre_text:
                                genres.append(capitalize_genre(genre_text))
                    else:
                        # Single genre or comma-separated
                        text = td.get_text(strip=True)
                        text = re.sub(r"\[\d+\]", "", text)
                        genres = [capitalize_genre(g.strip()) for g in re.split(r"[,/]", text) if g.strip()]
    except Exception as e:
        logger.debug(f"Genre extraction error: {e}")
    return genres


def _extract_infobox_image(infobox, page_url: str) -> Optional[str]:
    """Extract the cover/image URL from infobox, returning the original file URL.

    Skips SVG images (logos, signatures, wordmarks) — only raster
    images (JPEG, PNG, WebP) are suitable as artwork.
    """
    try:
        for img in infobox.find_all("img"):
            src = img.get("src")
            if not src:
                continue
            # Skip SVG images — they are logos, signatures, or wordmarks
            if ".svg" in src.lower():
                continue
            if src.startswith("//"):
                src = "https:" + src
            elif src.startswith("/"):
                src = "https://en.wikipedia.org" + src
            # Get the original file URL — thumbnail resizing fails for
            # non-free content on wikipedia/en/ when the requested size
            # exceeds the original dimensions.
            original = re.sub(r"/thumb/(.+)/\d+px-[^/]+$", r"/\1", src)
            return original
    except Exception as e:
        logger.debug(f"Image extraction error: {e}")
    return None


def _extract_plot_and_mv_info(soup) -> Optional[str]:
    """
    Extract the first paragraph and music video section from a Wikipedia page.

    Uses get_text() for heading matching because Wikipedia headings have nested
    <span> elements (mw-headline + mw-editsection) which cause element.string
    to return None, breaking string= regex matching.
    """
    parts = []

    # Use mw-parser-output (article body) → mw-content-container → fallback
    content = soup.find("div", {"class": "mw-parser-output"})
    if not content:
        content = soup.find("div", {"class": "mw-content-container"})
    if not content:
        content = soup

    # First non-empty paragraph (article summary / lead)
    for p in content.find_all("p", recursive=True):
        text = p.get_text(separator=" ")
        text = re.sub(r"\s+", " ", text).strip()
        if text and len(text) > 50:
            parts.append(text)
            break

    # Music video section — iterate headings and check get_text()
    mv_heading = None

    # Pass 1: Look for "Music video" (most specific)
    for heading in content.find_all(["h2", "h3"]):
        heading_text = heading.get_text(strip=True).lower()
        heading_text = re.sub(r"\[edit\]", "", heading_text).strip()
        if re.search(r"music\s*video", heading_text):
            mv_heading = heading
            break

    # Pass 2: Look for exact "Video" heading (less specific, but common)
    if not mv_heading:
        for heading in content.find_all(["h2", "h3"]):
            heading_text = heading.get_text(strip=True).lower()
            heading_text = re.sub(r"\[edit\]", "", heading_text).strip()
            if heading_text == "video":
                mv_heading = heading
                break

    if mv_heading:
        # Modern Wikipedia wraps headings in <div class="mw-heading">.
        # Paragraphs are siblings of that wrapper, not of the <h2> itself.
        walk_from = mv_heading
        if mv_heading.parent and "mw-heading" in (mv_heading.parent.get("class") or []):
            walk_from = mv_heading.parent

        sibling = walk_from.find_next_sibling()
        mv_parts = []
        while sibling:
            # Stop at the next heading (h2/h3 or a mw-heading wrapper div)
            if sibling.name in ["h2", "h3"]:
                break
            if sibling.name == "div" and "mw-heading" in (sibling.get("class") or []):
                break
            if sibling.name == "p":
                text = sibling.get_text(separator=" ")
                text = re.sub(r"\s+", " ", text).strip()
                if text:
                    mv_parts.append(text)
            sibling = sibling.find_next_sibling()
        if mv_parts:
            parts.append(" ".join(mv_parts))

    if parts:
        combined = " ".join(parts)
        # Clean citation references like [3] [4] or [ 3 ]
        combined = re.sub(r"\[\s*\d+\s*\]", "", combined)
        # Normalize whitespace
        combined = re.sub(r"\s+", " ", combined).strip()
        # Fix spacing before punctuation introduced by tag boundaries
        combined = re.sub(r'\s+([.,;:!?)])', r'\1', combined)
        return combined

    return None


# ---------------------------------------------------------------------------
# Combined metadata resolution
# ---------------------------------------------------------------------------

def resolve_metadata(
    artist: str,
    title: str,
    ytdlp_metadata: Optional[Dict[str, Any]] = None,
    skip_wikipedia: bool = False,
) -> Dict[str, Any]:
    """
    Resolve full metadata from all sources, merging results.

    Priority:
    1. MusicBrainz (canonical names, IDs)
    2. Wikipedia (album, year, genres, plot, image)
    3. yt-dlp metadata (fallback)

    Returns comprehensive metadata dict.
    """
    logger.info(f"Resolving metadata for: {artist} - {title}")

    metadata = {
        "artist": artist,
        "title": title,
        "album": None,
        "year": None,
        "genres": [],
        "plot": None,
        "mb_artist_id": None,
        "mb_recording_id": None,
        "mb_release_id": None,
        "image_url": None,
        "source_url": None,
    }

    # Source: yt-dlp (baseline)
    if ytdlp_metadata:
        metadata["album"] = ytdlp_metadata.get("album")
        metadata["year"] = ytdlp_metadata.get("release_year")
        metadata["source_url"] = ytdlp_metadata.get("webpage_url", "")
        if not artist:
            metadata["artist"] = ytdlp_metadata.get("artist", "")
        if not title:
            metadata["title"] = ytdlp_metadata.get("track", "")
        # NOTE: Do NOT set image_url from yt-dlp here — only scrapers should
        # set it so tasks.py can distinguish real art from YouTube thumbnails.

    # Source: MusicBrainz
    try:
        mb = search_musicbrainz(metadata["artist"], metadata["title"])
        if mb.get("mb_recording_id"):
            metadata["mb_artist_id"] = mb["mb_artist_id"]
            metadata["mb_recording_id"] = mb["mb_recording_id"]
            metadata["mb_release_id"] = mb["mb_release_id"]
            # Prefer MB canonical names — but validate they're not wildly
            # different from the input (protects against MB returning a
            # different recording for a common title).
            if mb.get("artist"):
                mb_artist_norm = _normalize_for_compare(mb["artist"])
                input_artist_norm = _normalize_for_compare(metadata["artist"])
                if (mb_artist_norm == input_artist_norm
                        or _tokens_overlap(mb["artist"], metadata["artist"], 0.4)):
                    metadata["artist"] = mb["artist"]
                else:
                    logger.warning(
                        f"MusicBrainz artist '{mb['artist']}' diverges from "
                        f"input '{metadata['artist']}' — keeping input artist, "
                        f"discarding MB IDs"
                    )
                    metadata["mb_artist_id"] = None
                    metadata["mb_recording_id"] = None
                    metadata["mb_release_id"] = None
                    mb = {}  # discard this match entirely
            if mb.get("title"):
                metadata["title"] = mb["title"]
            if mb.get("album") and not metadata["album"]:
                metadata["album"] = mb["album"]
            if mb.get("year") and not metadata["year"]:
                metadata["year"] = mb["year"]
            if mb.get("genres"):
                metadata["genres"] = mb["genres"]
    except Exception as e:
        logger.warning(f"MusicBrainz resolution failed: {e}")

    # Source: Wikipedia
    if not skip_wikipedia:
        try:
            wiki_url = search_wikipedia(metadata["title"], metadata["artist"])
            if wiki_url:
                wiki = scrape_wikipedia_page(wiki_url)

                # Validate scraped article matches expected artist/title
                mismatch = detect_article_mismatch(
                    wiki, metadata["artist"], metadata["title"]
                )
                if mismatch:
                    logger.warning(
                        f"Wikipedia article mismatch — discarding: {mismatch} "
                        f"(url={wiki_url})"
                    )
                    wiki = {}  # Discard mismatched article entirely

                if wiki.get("album") and not metadata["album"]:
                    metadata["album"] = wiki["album"]
                if wiki.get("year") and not metadata["year"]:
                    metadata["year"] = wiki["year"]
                if wiki.get("genres") and not metadata["genres"]:
                    metadata["genres"] = wiki["genres"]
                if wiki.get("plot"):
                    metadata["plot"] = wiki["plot"]
                if wiki.get("image_url"):
                    metadata["image_url"] = wiki["image_url"]
        except Exception as e:
            logger.warning(f"Wikipedia resolution failed: {e}")

    return metadata


def download_image(url: str, save_path: str) -> bool:
    """
    Download an image from URL, validate it, and save to disk.

    **DEPRECATED** — Callers should use ``artwork_service.download_and_validate()``
    or the higher-level facade functions directly.  This wrapper exists for
    backward compatibility during the transition.

    Delegates to the unified artwork_service for proper validation
    (Content-Type, magic bytes, PIL verify).  Never persists non-image
    content as artwork.
    """
    import warnings
    warnings.warn(
        "download_image() is deprecated — use artwork_service.download_and_validate() directly",
        DeprecationWarning,
        stacklevel=2,
    )
    from app.services.artwork_service import download_and_validate

    result = download_and_validate(url, save_path, overwrite=True)
    if not result.success:
        logger.error(f"Image download rejected for {url}: {result.error}")
    return result.success
