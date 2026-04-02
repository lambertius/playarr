"""
Artist & Album Scraper — Retrieve artwork and metadata for Kodi's
Artist and Album browsing views.

Sources (in priority order):
1. MusicBrainz + Cover Art Archive — MBID-based artwork (highest quality)
2. Wikipedia — artist pages, album pages (infobox images, fallback)

The module is intentionally stateless — it returns image URLs and
metadata dicts.  Downloading/caching is handled by artwork_manager.py.
"""
import logging
import re
import time
from typing import Optional, Dict, Any, List

import httpx
import musicbrainzngs
from bs4 import BeautifulSoup

from app.services.metadata_resolver import (
    _WIKI_USER_AGENT,
    _init_musicbrainz,
    _wikipedia_search_api,
    capitalize_genre,
)

logger = logging.getLogger(__name__)


def _resolve_commons_url(url: str) -> Optional[str]:
    """Resolve a Wikimedia Commons file page URL to a direct image URL.

    MusicBrainz image relations often point to Commons file pages
    (e.g. https://commons.wikimedia.org/wiki/File:Foo.jpg) instead of
    direct image URLs.  This function fetches the actual file URL via
    the MediaWiki API.
    """
    if not url or "commons.wikimedia.org/wiki/File:" not in url:
        return url  # Not a Commons file page — return as-is

    try:
        # Extract the filename from the URL
        filename = url.split("File:")[-1]
        if not filename:
            return url

        # Use the MediaWiki API to get the direct image URL
        resp = httpx.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "titles": f"File:{filename}",
                "prop": "imageinfo",
                "iiprop": "url",
                "format": "json",
            },
            headers={"User-Agent": "Playarr/1.0 (https://github.com/playarr) Python-httpx"},
            timeout=15,
        )
        data = resp.json()
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            imageinfo = page.get("imageinfo", [])
            if imageinfo:
                direct_url = imageinfo[0].get("url")
                if direct_url:
                    logger.info(f"Resolved Commons URL: {url} -> {direct_url}")
                    return direct_url
    except Exception as e:
        logger.warning(f"Failed to resolve Commons URL {url}: {e}")

    return url  # Fallback: return original URL

# Cover Art Archive base URL
_CAA_BASE = "https://coverartarchive.org"


# ---------------------------------------------------------------------------
# Wikipedia helpers
# ---------------------------------------------------------------------------

def _wiki_get(url: str) -> Optional[BeautifulSoup]:
    """Fetch a Wikipedia page and return parsed soup, or None on failure."""
    try:
        headers = {"User-Agent": _WIKI_USER_AGENT, "Api-User-Agent": _WIKI_USER_AGENT}
        resp = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
        if resp.status_code == 200:
            return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.warning(f"Wikipedia fetch failed for {url}: {e}")
    return None


def _extract_high_res_image(infobox) -> Optional[str]:
    """Extract the original (full-resolution) infobox image URL.

    Skips SVG images (logos, signatures, wordmarks) — only raster
    images (JPEG, PNG, WebP) are suitable as artist/album artwork.
    """
    if not infobox:
        return None
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
    return None


# ---------------------------------------------------------------------------
# Artist scraping
# ---------------------------------------------------------------------------

def search_artist_wikipedia(artist: str) -> Optional[str]:
    """Search Wikipedia for an artist/band page.  Returns URL or None.

    Delegates to the canonical implementation in metadata_resolver which
    has robust scoring (regex-based song-tag penalty, snippet keywords,
    similarity gate, etc.).  Keeping this thin wrapper so call-sites in
    this module don't need to change their imports.
    """
    from app.services.metadata_resolver import search_wikipedia_artist
    return search_wikipedia_artist(artist)


def scrape_artist_artwork(artist: str) -> Dict[str, Any]:
    """
    Scrape Wikipedia for an artist image and basic info.

    Returns dict:
        image_url: str | None   — URL of artist photo/logo
        fanart_url: str | None  — secondary image if available
        bio: str | None         — first paragraph of biography
        genres: list[str]
    """
    result: Dict[str, Any] = {
        "image_url": None,
        "fanart_url": None,
        "bio": None,
        "genres": [],
    }

    url = search_artist_wikipedia(artist)
    if not url:
        return result

    soup = _wiki_get(url)
    if not soup:
        return result

    infobox = soup.find("table", {"class": "infobox"})
    result["image_url"] = _extract_high_res_image(infobox)

    # Extract genres from infobox
    if infobox:
        for th in infobox.find_all("th"):
            label = th.get_text(strip=True).lower()
            if label in ("genres", "genre"):
                td = th.find_next_sibling("td")
                if td:
                    lis = td.find_all("li")
                    if lis:
                        result["genres"] = [
                            capitalize_genre(re.sub(r"\[\d+\]", "", li.get_text(strip=True)).strip())
                            for li in lis if li.get_text(strip=True)
                        ]
                    else:
                        text = re.sub(r"\[\d+\]", "", td.get_text(strip=True))
                        result["genres"] = [
                            capitalize_genre(g.strip())
                            for g in re.split(r"[,/]", text) if g.strip()
                        ]

    # First paragraph as bio
    content = soup.find("div", {"class": "mw-parser-output"})
    if content:
        for p in content.find_all("p", recursive=True):
            text = re.sub(r"\s+", " ", p.get_text(separator=" ")).strip()
            text = re.sub(r"\[\s*\d+\s*\]", "", text)
            if text and len(text) > 50:
                result["bio"] = text
                break

    # Try to find a second image for fanart (gallery or additional infobox images)
    if infobox:
        images = infobox.find_all("img")
        for img in images[1:]:  # Skip the first (already used as poster)
            src = img.get("src", "")
            if src and ("logo" in src.lower() or "banner" in src.lower()):
                continue
            if src.startswith("//"):
                src = "https:" + src
            elif src.startswith("/"):
                src = "https://en.wikipedia.org" + src
            fanart = re.sub(r"/(\d+)px-", "/1920px-", src)
            result["fanart_url"] = fanart
            break

    return result


# ---------------------------------------------------------------------------
# Album scraping
# ---------------------------------------------------------------------------

def search_album_wikipedia(album: str, artist: str) -> Optional[str]:
    """Search Wikipedia for an album page.  Returns URL or None."""
    search_terms = [
        f"{album} ({artist} album)",
        f"{album} (album)",
        f"{artist} {album} album",
        f"{album} {artist}",
        album,
    ]

    album_lower = album.lower().strip()
    artist_lower = artist.lower().strip()
    seen: set = set()
    candidates: List[Dict[str, Any]] = []

    for term in search_terms:
        for r in _wikipedia_search_api(term, limit=5):
            title = r["title"]
            if title in seen:
                continue
            seen.add(title)
            pt_lower = title.lower()
            snippet_lower = r.get("snippet", "").lower()
            score = 0
            if album_lower in pt_lower:
                score += 6
            if artist_lower and artist_lower in pt_lower:
                score += 2
            if artist_lower and artist_lower in snippet_lower:
                score += 1
            if "(album)" in pt_lower or "(ep)" in pt_lower:
                score += 3
            # Hard-penalise pages whose title doesn't contain the album name.
            # Without this, any "(album)" page with snippet keywords can pass
            # the threshold even when completely unrelated (e.g. searching for
            # "The Awesome Piano" returning "September Morn (album)").
            if album_lower not in pt_lower:
                score -= 10
            if any(kw in snippet_lower for kw in ["album", "studio album", "ep", "release"]):
                score += 2
            if "(disambiguation)" in pt_lower or pt_lower.startswith("list of"):
                score -= 5
            # De-score song/single pages
            if "(song)" in pt_lower or "(single)" in pt_lower:
                score -= 3
            candidates.append({"title": title, "score": score})

    if not candidates:
        return None
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]
    if best["score"] < 1:
        return None
    logger.info(f"Album Wikipedia match: '{best['title']}' (score={best['score']})")
    from app.services.metadata_resolver import _build_wikipedia_url
    return _build_wikipedia_url(best['title'])


def scrape_album_artwork(album: str, artist: str, *, wiki_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Scrape Wikipedia for album cover art and metadata.

    Returns dict:
        image_url: str | None   — album cover URL
        year: int | None
        genres: list[str]
    """
    result: Dict[str, Any] = {
        "image_url": None,
        "year": None,
        "genres": [],
    }
    if not album:
        return result

    url = wiki_url or search_album_wikipedia(album, artist)
    if not url:
        return result

    soup = _wiki_get(url)
    if not soup:
        return result

    infobox = soup.find("table", {"class": "infobox"})
    result["image_url"] = _extract_high_res_image(infobox)

    if infobox:
        # Year
        for th in infobox.find_all("th"):
            if "released" in th.get_text(strip=True).lower():
                td = th.find_next_sibling("td")
                if td:
                    year_match = re.search(r"\b(19|20)\d{2}\b", td.get_text(strip=True))
                    if year_match:
                        result["year"] = int(year_match.group())
        # Genres
        for th in infobox.find_all("th"):
            label = th.get_text(strip=True).lower()
            if label in ("genres", "genre"):
                td = th.find_next_sibling("td")
                if td:
                    lis = td.find_all("li")
                    if lis:
                        result["genres"] = [
                            capitalize_genre(re.sub(r"\[\d+\]", "", li.get_text(strip=True)).strip())
                            for li in lis if li.get_text(strip=True)
                        ]
                    else:
                        text = re.sub(r"\[\d+\]", "", td.get_text(strip=True))
                        result["genres"] = [
                            capitalize_genre(g.strip())
                            for g in re.split(r"[,/]", text) if g.strip()
                        ]

    return result


# ---------------------------------------------------------------------------
# MusicBrainz / Cover Art Archive fallbacks
# ---------------------------------------------------------------------------

def search_artist_musicbrainz(artist: str, mb_artist_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Search MusicBrainz for an artist and retrieve image from CAA if possible.

    When *mb_artist_id* is provided, skip the name search entirely and
    look up the artist directly.  This avoids false positives where a
    name-based search returns the wrong artist (e.g. "The Rions" → "The
    Beatles").

    Returns dict:
        mb_artist_id: str | None
        image_url: str | None
        mb_name: str | None  — canonical artist name from MusicBrainz
    """
    _init_musicbrainz()
    result: Dict[str, Any] = {"mb_artist_id": None, "image_url": None, "mb_name": None}

    try:
        # --- Direct lookup when MBID is known ---
        if mb_artist_id:
            result["mb_artist_id"] = mb_artist_id
            try:
                details = musicbrainzngs.get_artist_by_id(
                    mb_artist_id, includes=["url-rels"]
                )
                artist_data = details.get("artist", {})
                result["mb_name"] = artist_data.get("name")
                rels = artist_data.get("url-relation-list", [])
                for rel in rels:
                    if rel.get("type") == "image":
                        _img_url = _resolve_commons_url(rel["target"])
                        if _img_url and _img_url.lower().endswith(".svg"):
                            continue
                        result["image_url"] = _img_url
                        break
            except Exception:
                pass
            time.sleep(1.1)
            return result

        # --- Name-based search ---
        mb = musicbrainzngs.search_artists(artist=artist, limit=5)
        artists = mb.get("artist-list", [])
        if not artists:
            return result

        # Iterate all results and pick the best name match above the gate.
        import re as _re_mb
        from difflib import SequenceMatcher
        # Normalize Unicode hyphens so "a‐ha" (U+2010) matches "a-ha" (ASCII)
        _UNICODE_HYPHENS = _re_mb.compile(r'[\u2010\u2011\u2013\u2014\u2212]')
        _artist_norm = _UNICODE_HYPHENS.sub('-', artist.lower())
        best_match = None
        best_sim = 0.0
        for candidate in artists:
            cand_name = candidate.get("name", "")
            _cand_norm = _UNICODE_HYPHENS.sub('-', cand_name.lower())
            _sim = SequenceMatcher(None, _artist_norm, _cand_norm).ratio()
            if _sim > best_sim:
                best_sim = _sim
                best_match = candidate

        if best_sim < 0.60:
            logger.info(
                f"MusicBrainz artist search: best match '{best_match.get('name', '')}' "
                f"doesn't meet threshold for '{artist}' (similarity={best_sim:.2f} < 0.60), discarding"
            )
            return result

        result["mb_artist_id"] = best_match.get("id")

        # MusicBrainz doesn't directly serve artist images — try URL relations
        if result["mb_artist_id"]:
            try:
                details = musicbrainzngs.get_artist_by_id(
                    result["mb_artist_id"], includes=["url-rels"]
                )
                rels = details.get("artist", {}).get("url-relation-list", [])
                for rel in rels:
                    if rel.get("type") == "image":
                        _img_url = _resolve_commons_url(rel["target"])
                        if _img_url and _img_url.lower().endswith(".svg"):
                            continue
                        result["image_url"] = _img_url
                        break
            except Exception:
                pass

        time.sleep(1.1)  # MusicBrainz rate limit

    except Exception as e:
        logger.warning(f"MusicBrainz artist search failed: {e}")

    return result


# Release-group type preference for album artwork searches.
# Lower index = higher priority.  We want actual Album releases, not
# singles or EPs that happen to share the same title.
_RG_TYPE_PRIORITY = {"album": 0, "compilation": 1, "ep": 2, "single": 3}


def search_album_musicbrainz(album: str, artist: str) -> Dict[str, Any]:
    """
    Search MusicBrainz for a release and retrieve cover from Cover Art Archive.

    Prefers Album-type releases over Singles/EPs when multiple releases share
    the same title (e.g. "Because I Got High" exists as both a single and an
    album by Afroman).

    Returns dict:
        mb_release_id: str | None
        image_url: str | None
    """
    _init_musicbrainz()
    result: Dict[str, Any] = {"mb_release_id": None, "image_url": None}

    if not album:
        return result

    try:
        query = f'release:"{album}"'
        if artist:
            query += f' AND artist:"{artist}"'

        mb = musicbrainzngs.search_releases(query=query, limit=10)
        releases = mb.get("release-list", [])
        if not releases:
            return result

        # Sort releases by release-group type: Album > Compilation > EP > Single.
        # Within the same type, preserve MB's original relevance ordering.
        def _rg_sort_key(rel):
            rg = rel.get("release-group", {})
            rg_type = (rg.get("type") or rg.get("primary-type") or "").lower()
            return _RG_TYPE_PRIORITY.get(rg_type, 2)  # default between EP and Single

        releases.sort(key=_rg_sort_key)

        best = releases[0]
        result["mb_release_id"] = best.get("id")

        # Prefer release-group CAA endpoint (curated canonical cover)
        # over the specific release (which varies by pressing/edition).
        rg_id = best.get("release-group", {}).get("id")
        if rg_id:
            result["image_url"] = _get_cover_art_url_by_release_group(rg_id)
        # Fallback: specific release if release-group has no art
        if not result["image_url"] and result["mb_release_id"]:
            result["image_url"] = _get_cover_art_url(result["mb_release_id"])

        time.sleep(1.1)  # MusicBrainz rate limit

    except Exception as e:
        logger.warning(f"MusicBrainz album search failed: {e}")

    return result


def _get_cover_art_url(release_mbid: str) -> Optional[str]:
    """
    Query Cover Art Archive for the front cover of a release.
    Returns the image URL or None.
    """
    try:
        url = f"{_CAA_BASE}/release/{release_mbid}"
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            return None
        data = resp.json()
        images = data.get("images", [])
        for img in images:
            if img.get("front", False):
                # Prefer the full-size "image" URL
                return img.get("image") or img.get("thumbnails", {}).get("large")
        # Fallback to first image
        if images:
            return images[0].get("image")
    except Exception as e:
        logger.debug(f"Cover Art Archive query failed for {release_mbid}: {e}")
    return None


def _get_cover_art_url_by_release_group(rg_mbid: str) -> Optional[str]:
    """
    Query Cover Art Archive for the curated front cover of a release group.
    The release-group endpoint returns the canonical "best" cover across
    all releases in the group, avoiding pressing-specific art variations.
    """
    try:
        url = f"{_CAA_BASE}/release-group/{rg_mbid}"
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            return None
        data = resp.json()
        images = data.get("images", [])
        for img in images:
            if img.get("front", False):
                return img.get("image") or img.get("thumbnails", {}).get("large")
        if images:
            return images[0].get("image")
    except Exception as e:
        logger.debug(f"Cover Art Archive RG query failed for {rg_mbid}: {e}")
    return None


# ---------------------------------------------------------------------------
# Combined lookup (MusicBrainz first → Wikipedia fallback)
# ---------------------------------------------------------------------------

# Regex to strip common edition/remaster/deluxe suffixes from album names
_EDITION_RE = re.compile(
    r"\s*[\(\[](deluxe|remaster(ed)?|expanded|anniversary|bonus|special|"
    r"\d+th\s+anniversary|limited|collector'?s?|japan(ese)?|"
    r"super\s+deluxe|standard|clean|explicit|international"
    r")(\s+edition|\s+version|\s+release)?[\)\]]",
    re.IGNORECASE,
)


def _strip_edition_suffix(album: str) -> Optional[str]:
    """Strip common edition/remaster/deluxe parenthetical from album name.

    Returns the cleaned name if it changed, else None.
    """
    cleaned = _EDITION_RE.sub("", album).strip()
    return cleaned if cleaned and cleaned.lower() != album.lower() else None


def get_artist_artwork_wikipedia(artist: str, mb_artist_id: Optional[str] = None) -> Dict[str, Any]:
    """Get artist artwork from Wikipedia only (no MusicBrainz)."""
    result: Dict[str, Any] = {
        "image_url": None, "fanart_url": None, "bio": None,
        "genres": [], "mb_artist_id": None,
    }
    if not artist or artist == "Unknown Artist":
        return result
    # Extract primary artist for multi-artist strings
    from app.services.source_validation import parse_multi_artist
    primary, _ = parse_multi_artist(artist)
    if primary != artist:
        artist = primary
    wiki = scrape_artist_artwork(artist)
    result["image_url"] = wiki.get("image_url")
    result["fanart_url"] = wiki.get("fanart_url")
    result["bio"] = wiki.get("bio")
    result["genres"] = wiki.get("genres", [])
    return result


def get_artist_artwork_musicbrainz(artist: str, mb_artist_id: Optional[str] = None) -> Dict[str, Any]:
    """Get artist metadata from MusicBrainz only (no Wikipedia).

    MusicBrainz does not host artist images natively — its URL relations
    point to Wikimedia Commons.  To avoid overlap with the Wikipedia
    scraper, we only return the MB artist ID here (no image_url).
    """
    result: Dict[str, Any] = {
        "image_url": None, "fanart_url": None, "bio": None,
        "genres": [], "mb_artist_id": None,
    }
    if not artist or artist == "Unknown Artist":
        return result
    # Extract primary artist for multi-artist strings
    from app.services.source_validation import parse_multi_artist
    primary, _ = parse_multi_artist(artist)
    if primary != artist:
        artist = primary
    mb = search_artist_musicbrainz(artist, mb_artist_id=mb_artist_id)
    result["mb_artist_id"] = mb.get("mb_artist_id")
    # Intentionally skip mb["image_url"] — it's a Wikimedia Commons link,
    # not a MusicBrainz-hosted asset.
    return result


def get_artist_artwork(artist: str, mb_artist_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Get artist artwork and metadata using MusicBrainz first, Wikipedia as fallback.

    When *mb_artist_id* is provided it is forwarded to the MusicBrainz
    lookup so the pipeline uses the already-resolved MBID instead of
    re-searching by name (which can return the wrong artist).

    Returns:
        image_url, fanart_url, bio, genres, mb_artist_id
    """
    result: Dict[str, Any] = {
        "image_url": None,
        "fanart_url": None,
        "bio": None,
        "genres": [],
        "mb_artist_id": None,
    }

    if not artist or artist == "Unknown Artist":
        return result

    # Extract primary artist for multi-artist strings (e.g. "AronChupa & Little Sis Nora" → "AronChupa")
    from app.services.source_validation import parse_multi_artist
    primary, _ = parse_multi_artist(artist)
    if primary != artist:
        logger.info(f"Multi-artist detected: {artist!r} → using primary: {primary!r}")
        artist = primary

    # MusicBrainz first (canonical IDs + image URL relations)
    mb = search_artist_musicbrainz(artist, mb_artist_id=mb_artist_id)
    result["mb_artist_id"] = mb.get("mb_artist_id")
    if mb.get("image_url"):
        result["image_url"] = mb["image_url"]
        logger.info(f"Using MusicBrainz image for artist: {artist}")

    # Wikipedia (bio, genres, and image fallback)
    wiki = scrape_artist_artwork(artist)
    # If no result and MB returned a different canonical name, retry with that
    mb_canonical = mb.get("mb_name")
    if not wiki.get("image_url") and mb_canonical and mb_canonical.lower() != artist.lower():
        logger.info(f"Wikipedia retry with MB canonical name: {artist!r} → {mb_canonical!r}")
        wiki = scrape_artist_artwork(mb_canonical)
    # If still no result and name contains "& The" / "and The" (band suffix),
    # try just the lead artist name (e.g. "Amanda Palmer & The Grand Theft Orchestra" → "Amanda Palmer")
    if not wiki.get("image_url"):
        import re as _re_art
        _lead = _re_art.split(r'\s+(?:&|and)\s+[Tt]he\s', artist, maxsplit=1)
        if len(_lead) == 2 and _lead[0].strip():
            lead_name = _lead[0].strip()
            logger.info(f"Wikipedia retry with lead artist: {artist!r} → {lead_name!r}")
            wiki = scrape_artist_artwork(lead_name)
    if not result["image_url"] and wiki.get("image_url"):
        result["image_url"] = wiki["image_url"]
        logger.info(f"Using Wikipedia image for artist: {artist}")
    elif result["image_url"] and wiki.get("image_url"):
        # Store Wikipedia URL as fallback in case the MusicBrainz URL is dead
        result["fallback_image_url"] = wiki["image_url"]
    result["fanart_url"] = wiki.get("fanart_url")
    result["bio"] = wiki.get("bio")
    result["genres"] = wiki.get("genres", [])

    return result


def get_album_artwork_wikipedia(album: str, artist: str, *, wiki_url: Optional[str] = None) -> Dict[str, Any]:
    """Get album artwork from Wikipedia only (no MusicBrainz/CAA)."""
    result: Dict[str, Any] = {
        "image_url": None, "year": None, "genres": [], "mb_release_id": None,
    }
    if not album:
        return result

    wiki = scrape_album_artwork(album, artist, wiki_url=wiki_url)
    result["image_url"] = wiki.get("image_url")
    result["year"] = wiki.get("year")
    result["genres"] = wiki.get("genres", [])

    if not result["image_url"] and not result["year"]:
        base_name = _strip_edition_suffix(album)
        if base_name:
            logger.info(f"Album '{album}' not found on Wikipedia — retrying as '{base_name}'")
            wiki = scrape_album_artwork(base_name, artist)
            result["image_url"] = wiki.get("image_url")
            result["year"] = wiki.get("year")
            result["genres"] = wiki.get("genres", [])

    return result


def get_album_artwork_musicbrainz(album: str, artist: str) -> Dict[str, Any]:
    """Get album artwork from MusicBrainz/CAA only (no Wikipedia)."""
    result: Dict[str, Any] = {
        "image_url": None, "year": None, "genres": [], "mb_release_id": None,
    }
    if not album:
        return result

    mb = search_album_musicbrainz(album, artist)
    result["mb_release_id"] = mb.get("mb_release_id")
    result["image_url"] = mb.get("image_url")

    if not result["image_url"]:
        base_name = _strip_edition_suffix(album)
        if base_name:
            logger.info(f"Album '{album}' not found on CAA — retrying as '{base_name}'")
            mb = search_album_musicbrainz(base_name, artist)
            if not result["mb_release_id"] and mb.get("mb_release_id"):
                result["mb_release_id"] = mb["mb_release_id"]
            if mb.get("image_url"):
                result["image_url"] = mb["image_url"]

    return result


def get_album_artwork(album: str, artist: str, *, wiki_url: str | None = None) -> Dict[str, Any]:
    """
    Get album artwork using MusicBrainz/CAA first, Wikipedia as fallback.

    If the album name contains an edition suffix (e.g. "(10th Anniversary Edition)")
    and the initial search finds no artwork, retries with the base album name.

    Returns:
        image_url, year, genres, mb_release_id
    """
    result: Dict[str, Any] = {
        "image_url": None,
        "year": None,
        "genres": [],
        "mb_release_id": None,
    }

    if not album:
        return result

    def _search(name: str) -> None:
        """Search MB + Wikipedia for a given album name, merging into result."""
        # MusicBrainz/CAA first (high-quality cover art)
        mb = search_album_musicbrainz(name, artist)
        if not result["mb_release_id"] and mb.get("mb_release_id"):
            result["mb_release_id"] = mb["mb_release_id"]
        if not result["image_url"] and mb.get("image_url"):
            result["image_url"] = mb["image_url"]
            logger.info(f"Using Cover Art Archive image for album: {name} by {artist}")

        # Wikipedia (year, genres, and image fallback)
        wiki = scrape_album_artwork(name, artist, wiki_url=wiki_url)
        if not result["image_url"] and wiki.get("image_url"):
            result["image_url"] = wiki["image_url"]
            logger.info(f"Using Wikipedia image for album: {name} by {artist}")
        if not result["year"] and wiki.get("year"):
            result["year"] = wiki["year"]
        if not result["genres"] and wiki.get("genres"):
            result["genres"] = wiki["genres"]

    # First pass: full album name
    _search(album)

    # If nothing found, retry with edition suffix stripped
    if not result["image_url"] and not result["year"]:
        base_name = _strip_edition_suffix(album)
        if base_name:
            logger.info(f"Album '{album}' not found — retrying as '{base_name}'")
            _search(base_name)

    return result
