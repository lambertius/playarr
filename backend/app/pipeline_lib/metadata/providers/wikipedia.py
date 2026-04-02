# AUTO-SEPARATED from metadata/providers/wikipedia.py for pipeline_lib pipeline
# This file is independent — changes here do NOT affect the other pipeline.
"""
Wikipedia Provider — Scrapes Wikipedia for artist/album/track metadata.

Primary source for:
- Narrative biography / synopsis / plot
- Infobox images (artist photos, album covers)
- Genre lists from infoboxes

Opportunistic — not all entities have Wikipedia pages.
"""
import logging
import re
from typing import Dict, Any, List, Optional

import httpx
from bs4 import BeautifulSoup

from app.metadata.providers.base import (
    MetadataProvider, ProviderResult, AssetCandidate,
)
from app.scraper.metadata_resolver import (
    _WIKI_USER_AGENT,
    _wikipedia_search_api,
    capitalize_genre,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wiki_get(url: str) -> Optional[BeautifulSoup]:
    """Fetch and parse a Wikipedia page."""
    try:
        headers = {"User-Agent": _WIKI_USER_AGENT, "Api-User-Agent": _WIKI_USER_AGENT}
        resp = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
        if resp.status_code == 200:
            return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.warning(f"Wikipedia fetch failed for {url}: {e}")
    return None


def _infobox_image_url(infobox) -> Optional[str]:
    """Extract the highest-resolution infobox image."""
    if not infobox:
        return None
    img = infobox.find("img")
    if not img or not img.get("src"):
        return None
    src = img["src"]
    if src.startswith("//"):
        src = "https:" + src
    elif src.startswith("/"):
        src = "https://en.wikipedia.org" + src
    return re.sub(r"/(\d+)px-", "/1000px-", src)


def _infobox_field(infobox, *labels) -> Optional[str]:
    """Extract a text value from an infobox row matching one of *labels*."""
    if not infobox:
        return None
    for th in infobox.find_all("th"):
        text = th.get_text(strip=True).lower()
        if text in labels:
            td = th.find_next_sibling("td")
            if td:
                return re.sub(r"\[\d+\]", "", td.get_text(separator=" ", strip=True))
    return None


def _infobox_genres(infobox) -> List[str]:
    """Extract genres from an infobox."""
    if not infobox:
        return []
    for th in infobox.find_all("th"):
        if th.get_text(strip=True).lower() in ("genres", "genre"):
            td = th.find_next_sibling("td")
            if not td:
                continue
            lis = td.find_all("li")
            if lis:
                return [capitalize_genre(re.sub(r"\[\d+\]", "", li.get_text(strip=True)).strip())
                        for li in lis if li.get_text(strip=True)]
            text = re.sub(r"\[\d+\]", "", td.get_text(strip=True))
            return [capitalize_genre(g.strip()) for g in re.split(r"[,/]", text) if g.strip()]
    return []


def _first_paragraph(soup: BeautifulSoup) -> Optional[str]:
    """Extract the first substantial paragraph from a Wikipedia article."""
    content = soup.find("div", {"class": "mw-parser-output"})
    if not content:
        return None
    for p in content.find_all("p", recursive=True):
        text = re.sub(r"\s+", " ", p.get_text(separator=" ")).strip()
        text = re.sub(r"\[\s*\d+\s*\]", "", text)
        if text and len(text) > 50:
            return text
    return None


def _scored_wiki_search(terms: List[str], positive_keywords: List[str],
                        negative_keywords: List[str], name_lower: str) -> Optional[str]:
    """Run multiple Wikipedia searches, score & rank candidates, return best URL or None."""
    seen: set = set()
    candidates: List[Dict[str, Any]] = []

    for term in terms:
        for r in _wikipedia_search_api(term, limit=5):
            title = r["title"]
            if title in seen:
                continue
            seen.add(title)
            pt_lower = title.lower()
            # Strip HTML tags and entities from snippets for accurate matching
            _raw_snippet = r.get("snippet", "")
            _clean_snippet = re.sub(r"<[^>]+>", "", _raw_snippet)
            _clean_snippet = _clean_snippet.replace("&quot;", '"').replace("&amp;", "&").replace("&#039;", "'")
            snippet_lower = _clean_snippet.lower()
            score = 0
            if name_lower in pt_lower:
                score += 4
            else:
                score -= 10  # Heavy penalty: search target name not in page title

            # Penalize when the page title (sans disambiguation suffix)
            # contains extra words beyond the search name — likely a
            # different entity (e.g. "Blackshape Prime" for "Blackshape").
            _stripped = re.sub(r"\s*\(.*?\)\s*$", "", pt_lower).strip()
            from difflib import SequenceMatcher as _SM_ws
            _title_sim = _SM_ws(None, name_lower, _stripped).ratio()
            if _title_sim < 0.85:
                score -= 3

            if any(kw in snippet_lower for kw in positive_keywords):
                score += 2
            if any(kw in pt_lower for kw in positive_keywords):
                score += 3
            if "(disambiguation)" in pt_lower or pt_lower.startswith("list of"):
                score -= 5
            # Detect disambiguation pages by snippet content
            _disambig_snippet_kw = [
                "may refer to", "can refer to", "may also refer to",
                "commonly refers to", "most commonly refers to",
            ]
            if any(w in snippet_lower for w in _disambig_snippet_kw):
                score -= 10
            for nk in negative_keywords:
                if nk in pt_lower:
                    score -= 3
            # De-score non-music pages (TV show, film, novel, etc.)
            _non_music_tags = [
                "(tv series)", "(tv show)", "(film)", "(movie)",
                "(television)", "(video game)", "(game)", "(novel)",
            ]
            if any(tag in pt_lower for tag in _non_music_tags):
                score -= 5
            _non_music_snippet = [
                "television series", "tv series", "reality show",
                "television show", "video game",
            ]
            if any(w in snippet_lower for w in _non_music_snippet):
                score -= 3
            candidates.append({"title": title, "score": score})

    if not candidates:
        return None
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]
    if best["score"] < 4:
        return None
    from app.scraper.metadata_resolver import _build_wikipedia_url
    return _build_wikipedia_url(best['title'])


# ---------------------------------------------------------------------------
# WikipediaProvider
# ---------------------------------------------------------------------------

class WikipediaProvider(MetadataProvider):
    """Wikipedia metadata provider — opportunistic scraping."""

    name = "wikipedia"

    # ---- Artist ----------------------------------------------------------

    def search_artist(self, name: str) -> List[ProviderResult]:
        terms = [
            f"{name} (band)", f"{name} (musician)", f"{name} (singer)",
            f"{name} (rapper)", name,
        ]
        url = _scored_wiki_search(
            terms,
            positive_keywords=["(band)", "(musician)", "(singer)", "(rapper)", "(group)",
                               "band", "musician", "singer", "rapper", "group",
                               "songwriter", "artist", "rock", "pop", "hip hop"],
            negative_keywords=["(song)", "(album)", "(single)", "(ep)"],
            name_lower=name.lower().strip(),
        )
        if not url:
            return []

        soup = _wiki_get(url)
        if not soup:
            return []

        # Validate: page title must be related to the artist we searched for.
        # Wikipedia can redirect "Kazoo Kid" → "Macaulay Culkin" etc.
        page_title = soup.find("h1")
        if page_title:
            page_name = page_title.get_text().strip()
            import re as _re_wp
            page_name_clean = _re_wp.sub(r"\s*\(.*?\)\s*$", "", page_name)
            from difflib import SequenceMatcher
            _sim = SequenceMatcher(None, name.lower(), page_name_clean.lower()).ratio()
            if _sim < 0.85:
                logger.info(
                    f"WikipediaProvider.search_artist: page '{page_name}' doesn't "
                    f"match '{name}' (similarity={_sim:.2f}), discarding {url}"
                )
                return []

        infobox = soup.find("table", {"class": "infobox"})

        # Safety net: if the page has an infobox, verify it contains
        # music-related fields.  Non-music pages (aircraft, companies,
        # schools) will have infoboxes with completely different fields.
        if infobox:
            _music_indicators = {
                "genres", "genre", "labels", "label", "associated acts",
                "years active", "discography", "instruments", "instrument",
                "occupation", "occupations",
            }
            _has_music_field = False
            for _th in infobox.find_all("th"):
                if _th.get_text(strip=True).lower() in _music_indicators:
                    _has_music_field = True
                    break
            if not _has_music_field:
                _occ_td = _infobox_field(infobox, "occupation", "occupations")
                if _occ_td and any(w in _occ_td.lower() for w in (
                    "musician", "singer", "rapper", "songwriter", "composer",
                    "guitarist", "drummer", "bassist", "keyboardist", "dj",
                    "producer", "vocalist",
                )):
                    _has_music_field = True
            if not _has_music_field:
                logger.info(
                    f"WikipediaProvider.search_artist: page '{url}' has "
                    f"infobox but no music-related fields, discarding"
                )
                return []

        bio = _first_paragraph(soup)

        fields: Dict[str, Any] = {
            "canonical_name": name,
            "biography": bio,
            "genres": _infobox_genres(infobox),
            "wikipedia_url": url,
        }

        # Country from infobox origin
        origin = _infobox_field(infobox, "origin", "born")
        if origin:
            fields["country"] = origin

        return [ProviderResult(fields=fields, confidence=0.7, provenance=self.name)]

    def get_artist(self, key: str) -> Optional[ProviderResult]:
        # Wikipedia doesn't have stable IDs — re-search
        results = self.search_artist(key)
        return results[0] if results else None

    # ---- Album -----------------------------------------------------------

    def search_album(self, artist: str, title: str) -> List[ProviderResult]:
        terms = [
            f"{title} ({artist} album)", f"{title} (album)",
            f"{artist} {title} album", f"{title} {artist}", title,
        ]
        url = _scored_wiki_search(
            terms,
            positive_keywords=["(album)", "(ep)", "album", "studio album", "ep", "release"],
            negative_keywords=["(song)", "(single)", "(band)", "(musician)"],
            name_lower=title.lower().strip(),
        )
        if not url:
            return []

        soup = _wiki_get(url)
        if not soup:
            return []

        infobox = soup.find("table", {"class": "infobox"})
        released = _infobox_field(infobox, "released")
        year = None
        if released:
            m = re.search(r"\b(19|20)\d{2}\b", released)
            if m:
                year = int(m.group())

        fields: Dict[str, Any] = {
            "title": title,
            "artist": artist,
            "year": year,
            "genres": _infobox_genres(infobox),
            "wikipedia_url": url,
        }
        return [ProviderResult(fields=fields, confidence=0.7, provenance=self.name)]

    def get_album(self, key: str) -> Optional[ProviderResult]:
        return None  # no stable key

    # ---- Track -----------------------------------------------------------

    def search_track(self, artist: str, title: str) -> List[ProviderResult]:
        terms = [f"{title} ({artist} song)", f'"{title}" {artist}', f"{title} (song)", title]
        url = _scored_wiki_search(
            terms,
            positive_keywords=["(song)", "(single)", "single", "track", "music video"],
            negative_keywords=["(album)", "(band)", "(musician)"],
            name_lower=title.lower().strip(),
        )
        if not url:
            return []

        soup = _wiki_get(url)
        if not soup:
            return []

        infobox = soup.find("table", {"class": "infobox"})

        # Reject articles that have no music infobox — they are likely
        # about a generic concept that shares the song's name (e.g. the
        # Wikipedia article for "thirst trap" the social media concept).
        if not infobox:
            logger.info(
                f"WikipediaProvider.search_track: no infobox found — "
                f"article is likely not about a song, discarding {url}"
            )
            return []

        # Check for a music-specific infobox by looking for typical fields
        _music_fields = ("released", "recorded", "genre", "genres", "label",
                         "from the album", "length", "songwriter", "producer")
        _has_music_field = any(
            _infobox_field(infobox, f) is not None for f in _music_fields
        )
        if not _has_music_field:
            logger.info(
                f"WikipediaProvider.search_track: infobox has no music-related "
                f"fields — article is likely not about a song, discarding {url}"
            )
            return []

        released = _infobox_field(infobox, "released")
        year = None
        if released:
            m = re.search(r"\b(19|20)\d{2}\b", released)
            if m:
                year = int(m.group())

        album = _infobox_field(infobox, "from the album")
        plot = _first_paragraph(soup)

        # Validate: check if infobox artist matches expected artist
        # to prevent cross-contamination from wrong Wikipedia articles
        from app.scraper.metadata_resolver import (
            _extract_infobox_artist, _extract_infobox_title,
            detect_article_mismatch,
        )
        scraped_data = {
            "artist": _extract_infobox_artist(infobox) if infobox else None,
            "title": _extract_infobox_title(infobox) if infobox else None,
            "plot": plot,
        }
        mismatch = detect_article_mismatch(scraped_data, artist, title)
        if mismatch:
            logger.warning(
                f"WikipediaProvider.search_track: article mismatch — "
                f"discarding {url}: {mismatch}"
            )
            return []

        fields: Dict[str, Any] = {
            "title": title,
            "artist": artist,
            "album": album,
            "year": year,
            "genres": _infobox_genres(infobox),
            "plot": plot,
            "wikipedia_url": url,
        }
        return [ProviderResult(fields=fields, confidence=0.6, provenance=self.name)]

    def get_track(self, key: str) -> Optional[ProviderResult]:
        return None

    # ---- Assets ----------------------------------------------------------

    def get_artist_assets(self, artist_name: str, mbid: Optional[str] = None) -> List[AssetCandidate]:
        terms = [
            f"{artist_name} (band)", f"{artist_name} (musician)",
            f"{artist_name} (singer)", f"{artist_name} (rapper)", artist_name,
        ]
        url = _scored_wiki_search(
            terms,
            positive_keywords=["(band)", "(musician)", "(singer)", "(rapper)", "(group)",
                               "band", "musician", "singer", "rapper", "group",
                               "trio", "duo", "songwriter", "artist", "hip hop"],
            negative_keywords=["(song)", "(album)", "(single)", "(ep)"],
            name_lower=artist_name.lower().strip(),
        )
        if not url:
            return []

        soup = _wiki_get(url)
        if not soup:
            return []

        # Validate: page title must be related to the artist we searched for
        # (mirrors the similarity gate in search_artist).
        page_title_el = soup.find("h1")
        if page_title_el:
            page_name = page_title_el.get_text().strip()
            page_name_clean = re.sub(r"\s*\(.*?\)\s*$", "", page_name)
            from difflib import SequenceMatcher
            _sim = SequenceMatcher(None, artist_name.lower(), page_name_clean.lower()).ratio()
            if _sim < 0.85:
                logger.info(
                    f"WikipediaProvider.get_artist_assets: page '{page_name}' doesn't "
                    f"match '{artist_name}' (similarity={_sim:.2f}), discarding {url}"
                )
                return []

        infobox = soup.find("table", {"class": "infobox"})

        # Music-content safety net (same as search_artist)
        if infobox:
            _music_fields_a = {
                "genres", "genre", "labels", "label", "associated acts",
                "years active", "discography", "instruments", "instrument",
                "occupation", "occupations",
            }
            _has_music_a = False
            for _th_a in infobox.find_all("th"):
                if _th_a.get_text(strip=True).lower() in _music_fields_a:
                    _has_music_a = True
                    break
            if not _has_music_a:
                _occ_a = _infobox_field(infobox, "occupation", "occupations")
                if _occ_a and any(w in _occ_a.lower() for w in (
                    "musician", "singer", "rapper", "songwriter", "composer",
                    "guitarist", "drummer", "bassist", "keyboardist", "dj",
                    "producer", "vocalist",
                )):
                    _has_music_a = True
            if not _has_music_a:
                logger.info(
                    f"WikipediaProvider.get_artist_assets: page '{url}' has "
                    f"infobox but no music-related fields, discarding"
                )
                return []

        assets: List[AssetCandidate] = []

        poster_url = _infobox_image_url(infobox)
        if poster_url:
            assets.append(AssetCandidate(
                url=poster_url, kind="poster", provenance=self.name, confidence=0.6,
            ))

        # Second infobox image → fanart
        if infobox:
            imgs = infobox.find_all("img")
            for img in imgs[1:]:
                src = img.get("src", "")
                if not src or "logo" in src.lower():
                    continue
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = "https://en.wikipedia.org" + src
                fanart = re.sub(r"/(\d+)px-", "/1920px-", src)
                assets.append(AssetCandidate(
                    url=fanart, kind="fanart", provenance=self.name, confidence=0.4,
                ))
                break

        return assets

    def get_album_assets(self, artist_name: str, album_title: str,
                         mbid: Optional[str] = None) -> List[AssetCandidate]:
        terms = [
            f"{album_title} ({artist_name} album)", f"{album_title} (album)",
            f"{artist_name} {album_title}", album_title,
        ]
        url = _scored_wiki_search(
            terms,
            positive_keywords=["(album)", "(ep)", "album"],
            negative_keywords=["(song)", "(single)", "(band)"],
            name_lower=album_title.lower().strip(),
        )
        if not url:
            return []

        soup = _wiki_get(url)
        if not soup:
            return []

        infobox = soup.find("table", {"class": "infobox"})
        poster_url = _infobox_image_url(infobox)
        if poster_url:
            return [AssetCandidate(
                url=poster_url, kind="poster", provenance=self.name, confidence=0.6,
            )]
        return []

    def get_track_assets(self, artist_name: str, track_title: str,
                         mbid: Optional[str] = None) -> List[AssetCandidate]:
        terms = [
            f"{track_title} ({artist_name} song)",
            f'"{track_title}" {artist_name}',
            f"{track_title} (song)", track_title,
        ]
        url = _scored_wiki_search(
            terms,
            positive_keywords=["(song)", "(single)", "single", "track", "music video"],
            negative_keywords=["(album)", "(band)", "(musician)"],
            name_lower=track_title.lower().strip(),
        )
        if not url:
            return []

        soup = _wiki_get(url)
        if not soup:
            return []

        infobox = soup.find("table", {"class": "infobox"})
        if not infobox:
            return []

        poster_url = _infobox_image_url(infobox)
        if poster_url:
            return [AssetCandidate(
                url=poster_url, kind="poster", provenance=self.name, confidence=0.55,
            )]
        return []
