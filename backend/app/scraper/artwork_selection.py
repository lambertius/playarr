"""Shared artwork candidate selection logic.

This module is the **single source of truth** for:
 - CoverArtArchive poster/album art endpoint selection (singles vs albums)
 - Priority-based candidate selection for poster, album, artist, and fanart

Both the scraper tester and the import pipelines (URL + library) call these
functions so their behaviour is identical.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from app.metadata.providers.coverartarchive import (
    _fetch_front_cover,
    _fetch_front_cover_by_release_group,
)


# ── CoverArtArchive poster / album art resolution ──────────────────────

def is_single_release(
    mb_release_group_id: Optional[str],
    mb_album_release_group_id: Optional[str],
) -> bool:
    """True when the track's release-group differs from the album's."""
    if mb_release_group_id and mb_album_release_group_id:
        return mb_release_group_id != mb_album_release_group_id
    if mb_release_group_id and not mb_album_release_group_id:
        return True  # has single RG but no album RG → standalone single
    return False


def fetch_caa_artwork(
    mb_release_id: Optional[str],
    mb_release_group_id: Optional[str],
    mb_album_release_group_id: Optional[str],
    mb_album_release_id: Optional[str] = None,
) -> Tuple[Optional[str], str, str]:
    """Determine the correct CAA artwork URL for a video.

    Uses the same logic as the scraper tester:
    * When the track is a **single** (its release-group differs from the
      album's release-group, or there's no album RG at all), try the
      release-group CAA endpoint first (curated canonical cover), then
      fall back to the specific matched release → ``art_type="poster"``.
    * Otherwise use the release-level CAA endpoint → ``art_type="album"``.
    * When neither single nor album release is available but an album
      release ID exists, try the album release → ``art_type="album"``.

    Returns:
        ``(url, source, art_type)``  or  ``(None, "", "")`` when no cover
        was found.
    """
    _is_single = is_single_release(mb_release_group_id, mb_album_release_group_id)

    if _is_single:
        # Prefer the release-group endpoint — it returns the curated "best"
        # front cover across all releases in the group, which is typically
        # the canonical single cover.  The specific release endpoint can
        # return art from an obscure pressing that differs from the
        # well-known single artwork.
        if mb_release_group_id:
            url = _fetch_front_cover_by_release_group(mb_release_group_id)
            if url:
                return url, "musicbrainz_coverart", "poster"
        # Fallback: specific release (when no release-group art exists)
        if mb_release_id:
            url = _fetch_front_cover(mb_release_id)
            if url:
                return url, "musicbrainz_coverart", "poster"
    elif mb_release_id:
        url = _fetch_front_cover(mb_release_id)
        if url:
            return url, "musicbrainz_coverart", "album"

    # Fallback: when single release info is absent but the parent album
    # release is known (title-track case), try album cover art.
    if mb_album_release_id and mb_album_release_id != mb_release_id:
        url = _fetch_front_cover(mb_album_release_id)
        if url:
            return url, "musicbrainz_coverart", "album"
    if mb_album_release_group_id and mb_album_release_group_id != mb_release_group_id:
        url = _fetch_front_cover_by_release_group(mb_album_release_group_id)
        if url:
            return url, "musicbrainz_coverart", "album"

    return None, "", ""


# ── Priority-based candidate selection ──────────────────────────────────

# Priority lists: lower index = higher priority.
POSTER_PRIORITY = ["musicbrainz_coverart", "wikipedia", "yt-dlp"]
# For singles, Wikipedia infobox art is more reliably the single's own
# cover; CAA for single releases often carries the parent album artwork.
POSTER_PRIORITY_SINGLE = ["wikipedia", "musicbrainz_coverart", "yt-dlp"]
ALBUM_PRIORITY = ["album_scraper", "album_scraper_wiki", "wikipedia_album", "musicbrainz_coverart"]


def _priority_key(source: str, priority_list: List[str]) -> int:
    try:
        return priority_list.index(source)
    except ValueError:
        return 999


def apply_candidate_priorities(candidates: list, *, is_single: bool = False) -> None:
    """Mutate *candidates* in-place, setting ``applied=True`` on the best
    candidate in each art-type category.

    Each element must expose ``.art_type``, ``.source``, and ``.applied``
    attributes (works with both Pydantic models and plain objects).

    Priority logic (identical to scraper tester):
    * **album** — ``album_scraper > album_scraper_wiki > musicbrainz_coverart``
    * **poster** — ``musicbrainz_coverart > wikipedia > yt-dlp``
      (for singles: ``wikipedia > musicbrainz_coverart > yt-dlp``)
      (always re-evaluates; may flip an already-applied flag)
    * **artist** — first candidate wins
    * **fanart** — first candidate wins
    """
    # ── Album art ──
    album_cands = [c for c in candidates if c.art_type == "album"]
    if album_cands and not any(c.applied for c in album_cands):
        album_cands.sort(key=lambda c: _priority_key(c.source, ALBUM_PRIORITY))
        album_cands[0].applied = True

    # ── Poster ──
    poster_cands = [c for c in candidates if c.art_type == "poster"]
    if poster_cands:
        _poster_prio = POSTER_PRIORITY_SINGLE if is_single else POSTER_PRIORITY
        poster_cands.sort(key=lambda c: _priority_key(c.source, _poster_prio))
        best = poster_cands[0]
        if not best.applied:
            for c in poster_cands:
                if c.applied:
                    c.applied = False
            best.applied = True

    # ── Artist art ──
    artist_cands = [c for c in candidates if c.art_type == "artist"]
    if artist_cands and not any(c.applied for c in artist_cands):
        artist_cands[0].applied = True

    # ── Fanart ──
    fanart_cands = [c for c in candidates if c.art_type == "fanart"]
    if fanart_cands and not any(c.applied for c in fanart_cands):
        fanart_cands[0].applied = True
