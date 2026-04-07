"""
Unified Metadata Resolution Service — Single code path for both automatic
import and manual metadata operations.

This service implements the new source-guided metadata pipeline:

  1. Parse local/source identity hints
  2. AI Source Resolution (if AI enabled)
  3. Scraper fetch using resolved source links/IDs
  4. Scraper validation
  5. AI Final Review and correction
  6. Return resolved metadata

Both manual "Analyze Metadata" / "Scrape Metadata" and the automatic import
pipeline call this service. There must be NO duplicated prompt builders or
separate code paths with different context.

Pipeline order within this service:
  - Gather all available context (platform metadata, filename, duration, etc.)
  - Run AI source resolution (pre-scrape)
  - Run scrapers with AI-provided links first, falling back to search
  - Validate scraped results
  - Run AI final review (post-scrape)
  - Return combined metadata
"""
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.ai.source_resolution import (
    SourceResolutionResult, resolve_sources_with_ai,
)
from app.ai.final_review import FinalReviewResult, run_final_review
from app.services.metadata_resolver import (
    resolve_metadata as _legacy_resolve_metadata,
    search_musicbrainz,
    search_wikipedia,
    scrape_wikipedia_page,
    detect_article_mismatch,
    search_imdb_music_video,
    clean_title,
    extract_artist_title,
    capitalize_genre,
    extract_album_wiki_url_from_single,
)

logger = logging.getLogger(__name__)


def _get_setting_bool(db: Optional[Session], key: str, default: bool = False) -> bool:
    """Read a boolean global setting from the DB."""
    if not db:
        return default
    try:
        from app.models import AppSetting
        row = db.query(AppSetting).filter(
            AppSetting.key == key,
            AppSetting.user_id.is_(None),
        ).first()
        if row:
            return row.value.lower() in ("true", "1", "yes")
    except Exception:
        pass
    return default


def _get_setting_str(db: Optional[Session], key: str, default: str = "") -> str:
    """Read a global setting string from the DB."""
    if not db:
        return default
    try:
        from app.models import AppSetting
        row = db.query(AppSetting).filter(
            AppSetting.key == key,
            AppSetting.user_id.is_(None),
        ).first()
        return row.value if row else default
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Album-vs-title deduplication
# ---------------------------------------------------------------------------

def _album_is_title_duplicate(album: str, title: str) -> bool:
    """Return True if album is effectively the same as title (hallucinated).

    Also catches partial matches: album is a prefix/core of the title
    (e.g. album="Kazoo Kid" vs title="Kazoo Kid - Trap Remix"), and
    the reverse (e.g. album="I'm an Albatraoz - Single" vs title="I'm an Albatraoz").
    """
    if not album or not title:
        return False
    # Normalise: lowercase, strip parenthetical suffixes and common noise
    _strip = re.compile(r"\s*[\(\[].+?[\)\]]\s*$")
    a = _strip.sub("", album.strip()).lower().strip(" -")
    t = _strip.sub("", title.strip()).lower().strip(" -")
    if a == t:
        return True
    # Check if album is a prefix of title (before a separator like " - ")
    if t.startswith(a) and len(t) > len(a) and t[len(a):].lstrip().startswith("-"):
        return True
    # Check if title is a prefix of album (before a separator like " - ")
    if a.startswith(t) and len(a) > len(t) and a[len(t):].lstrip().startswith("-"):
        return True
    return False


# ---------------------------------------------------------------------------
# Source-guided scraping helpers
# ---------------------------------------------------------------------------

def _scrape_with_ai_links(
    artist: str,
    title: str,
    ai_result: Optional[SourceResolutionResult],
    ytdlp_metadata: Optional[Dict[str, Any]] = None,
    skip_wikipedia: bool = False,
    skip_musicbrainz: bool = False,
    log_callback=None,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Run scrapers using AI-provided links first, falling back to search.

    Returns (metadata_dict, scraper_log_lines).

    When AI has provided direct links/IDs, use those first.
    Only fall back to blind search if AI-provided links are missing or invalid.
    """
    logs: List[str] = []

    metadata: Dict[str, Any] = {
        "artist": artist,
        "title": title,
        "album": None,
        "year": None,
        "genres": [],
        "plot": None,
        "mb_artist_id": None,
        "mb_recording_id": None,
        "mb_release_id": None,
        "mb_release_group_id": None,
        "mb_album_release_id": None,
        "mb_album_release_group_id": None,
        "image_url": None,
        "source_url": None,
        "scraper_sources_used": [],  # Track which sources were actually used
    }

    # Baseline from yt-dlp
    if ytdlp_metadata:
        metadata["album"] = ytdlp_metadata.get("album")
        metadata["year"] = ytdlp_metadata.get("release_year")
        metadata["source_url"] = ytdlp_metadata.get("webpage_url", "")
        # NOTE: Do NOT set metadata["image_url"] from yt-dlp thumbnail here.
        # image_url must only be set by real scrapers (Wikipedia, IMDB, etc.)
        # so that tasks.py can distinguish scraper art from YouTube thumbnails.
        # The yt-dlp thumbnail fallback in tasks.py handles the YouTube case.

    has_ai = ai_result is not None

    # ── MusicBrainz resolution ──
    mb_used_ai_ids = False
    _ai_mb_id = (ai_result.sources.musicbrainz_recording_id if has_ai else None) or None
    logs.append(f"MusicBrainz: entering resolution (skip={skip_musicbrainz}, has_ai={has_ai}, ai_mb_id={bool(_ai_mb_id)})")

    if skip_musicbrainz:
        logs.append("MusicBrainz: skipped (disabled)")
    elif has_ai and _ai_mb_id:
        # Step A: Try AI-provided MusicBrainz IDs directly
        logs.append(f"MusicBrainz: using AI-provided recording ID: {_ai_mb_id}")
        try:
            import musicbrainzngs
            from app.services.metadata_resolver import _init_musicbrainz, _pick_best_release
            import time
            _init_musicbrainz()

            rec = musicbrainzngs.get_recording_by_id(
                _ai_mb_id,
                includes=["artists", "releases", "tags"],
            )
            recording = rec.get("recording", {})
            if recording:
                # Release — pick best single/EP only
                releases = recording.get("release-list", [])
                best_rel = _pick_best_release(releases, allowed_types={"single", "ep"})

                # Music videos must be from singles/EPs. If the AI-provided
                # recording has no single or EP release, reject it and fall
                # back to search so we can find the correct recording.
                from app.services.metadata_resolver import _RELEASE_TYPE_PRIORITY
                _rel_type = ""
                if best_rel:
                    _rg = best_rel.get("release-group", {})
                    _rel_type = (_rg.get("primary-type") or _rg.get("type") or "").lower()
                if _rel_type not in ("single", "ep"):
                    logs.append(
                        f"MusicBrainz: AI-provided recording rejected — "
                        f"best release type is '{_rel_type or 'unknown'}', not 'single'/'ep'. "
                        f"Falling back to search."
                    )
                    raise ValueError("Recording is not from a single or EP")

                metadata["mb_recording_id"] = recording.get("id")

                # Artist — validate that the recording's artist matches what
                # we expect.  AI sometimes provides a recording by a
                # different artist with the same song title.
                artist_credits = recording.get("artist-credit", [])
                if artist_credits:
                    # Filter to actual credit dicts (skip joinphrase strings)
                    _credit_dicts = [c for c in artist_credits if isinstance(c, dict) and "artist" in c]
                    ac = _credit_dicts[0] if _credit_dicts else None
                    if ac:
                        _mb_artist_name = ac["artist"].get("name", "")
                        if _mb_artist_name and artist:
                            from difflib import SequenceMatcher as _SM_art
                            from app.services.metadata_resolver import (
                                _normalize_for_compare, _tokens_overlap,
                            )
                            # Compare against primary artist (strip feat. credits)
                            from app.services.source_validation import parse_multi_artist as _pma_validate
                            _input_primary, _ = _pma_validate(artist)
                            _art_sim = _SM_art(
                                None,
                                _normalize_for_compare(_input_primary),
                                _normalize_for_compare(_mb_artist_name),
                            ).ratio()
                            _art_tok = _tokens_overlap(_mb_artist_name, _input_primary, 0.4)
                            if _art_sim < 0.6 and not _art_tok:
                                logs.append(
                                    f"MusicBrainz: AI-provided recording rejected — "
                                    f"artist '{_mb_artist_name}' doesn't match "
                                    f"'{artist}' (sim={_art_sim:.2f}). "
                                    f"Falling back to search."
                                )
                                raise ValueError("Recording artist mismatch")

                        # Build semicolon-separated artist name from all credits
                        _all_credit_names = [c["artist"].get("name", "") for c in _credit_dicts if c["artist"].get("name")]
                        if len(_all_credit_names) > 1:
                            metadata["artist"] = "; ".join(_all_credit_names)
                        else:
                            metadata["artist"] = _mb_artist_name or artist
                        metadata["mb_artist_id"] = ac["artist"].get("id")
                        # Store raw credits for building artist_ids downstream
                        metadata["mb_artist_credits"] = _credit_dicts

                if best_rel:
                    _rel_album = best_rel.get("title")
                    _rel_accepted = False
                    if not metadata.get("album") and _rel_album:
                        if not _album_is_title_duplicate(_rel_album, metadata.get("title", "")):
                            metadata["album"] = _rel_album
                            _rel_accepted = True
                    if _rel_accepted or metadata.get("album"):
                        metadata["mb_release_id"] = best_rel.get("id")
                        _rg = best_rel.get("release-group", {})
                        if _rg.get("id"):
                            metadata["mb_release_group_id"] = _rg["id"]
                    date = best_rel.get("date", "")
                    if date and len(date) >= 4:
                        try:
                            metadata["year"] = metadata.get("year") or int(date[:4])
                        except ValueError:
                            pass

                # Tags/genres
                tags = recording.get("tag-list", [])
                if tags:
                    metadata["genres"] = [capitalize_genre(t["name"]) for t in tags if "name" in t and int(t.get("count", 0)) >= 2]

                mb_used_ai_ids = True
                metadata["scraper_sources_used"].append("musicbrainz:ai_id")
                logs.append(f"MusicBrainz: resolved via AI ID — {metadata['artist']} - {metadata.get('album', '?')}")

            time.sleep(1.1)  # Rate limit
        except Exception as e:
            logs.append(f"MusicBrainz: AI-provided ID lookup failed: {e}")
            mb_used_ai_ids = False

    # Find parent album release group for AI-resolved recording
    # (search path already sets this via search_musicbrainz, but AI path skips it)
    if mb_used_ai_ids and metadata.get("mb_recording_id") and not metadata.get("mb_album_release_group_id"):
        from app.services.metadata_resolver import _find_parent_album
        try:
            _parent = _find_parent_album(metadata["mb_recording_id"])
            if _parent:
                metadata["mb_album_release_id"] = _parent.get("mb_album_release_id")
                metadata["mb_album_release_group_id"] = _parent.get("mb_album_release_group_id")
                if _parent.get("album") and not metadata.get("album"):
                    metadata["album"] = _parent["album"]
                logs.append(f"MusicBrainz: parent album '{_parent.get('album')}' "
                            f"(rg={_parent.get('mb_album_release_group_id')})")
        except Exception as e:
            logs.append(f"MusicBrainz: parent album lookup failed: {e}")

    # Step A.5: AI artist cross-reference fallback.
    _ai_artist_id = (ai_result.sources.musicbrainz_artist_id if has_ai else None) or None
    if not skip_musicbrainz and not mb_used_ai_ids and _ai_artist_id:
        logs.append(f"MusicBrainz: AI provided artist ID {_ai_artist_id} — cross-referencing singles")
        try:
            from app.services.metadata_resolver import (
                _confirm_single_via_artist, _find_parent_album,
                _find_album_by_artist_browse, _init_musicbrainz,
                _pick_best_release,
            )
            import musicbrainzngs
            import time as _time_a5
            _init_musicbrainz()
            _search_title = title
            if has_ai and ai_result.identity.title:
                _search_title = ai_result.identity.title
            confirmed = _confirm_single_via_artist(_ai_artist_id, _search_title)
            if confirmed:
                metadata["mb_artist_id"] = _ai_artist_id
                metadata["mb_release_group_id"] = confirmed["id"]
                _single_rel_id = None
                _single_rec_id = None
                try:
                    _browse = musicbrainzngs.browse_releases(
                        release_group=confirmed["id"],
                        includes=["recordings"],
                    )
                    _time_a5.sleep(1.1)
                    for _rel in _browse.get("release-list", []):
                        _caa = _rel.get("cover-art-archive", {})
                        if _caa.get("front") in (True, "true") and not _single_rel_id:
                            _single_rel_id = _rel.get("id")
                        if not _single_rec_id:
                            for _med in _rel.get("medium-list", []):
                                for _trk in _med.get("track-list", []):
                                    _rec = _trk.get("recording", {})
                                    if _rec.get("id"):
                                        _single_rec_id = _rec["id"]
                                        break
                                if _single_rec_id:
                                    break
                    if not _single_rel_id:
                        _rlist = _browse.get("release-list", [])
                        if _rlist:
                            _single_rel_id = _rlist[0].get("id")
                except Exception as e:
                    logs.append(f"MusicBrainz: browse single RG failed: {e}")
                if _single_rel_id:
                    metadata["mb_release_id"] = _single_rel_id
                if _single_rec_id:
                    metadata["mb_recording_id"] = _single_rec_id
                    metadata["_source_urls"]["musicbrainz"] = f"https://musicbrainz.org/recording/{_single_rec_id}"
                metadata["title"] = confirmed.get("title") or metadata["title"]
                _parent = None
                if _single_rec_id:
                    _parent = _find_parent_album(_single_rec_id)
                if not _parent:
                    _parent = _find_album_by_artist_browse(_ai_artist_id, _search_title)
                if _parent:
                    metadata["album"] = _parent["album"]
                    metadata["mb_album_release_id"] = _parent.get("mb_album_release_id")
                    metadata["mb_album_release_group_id"] = _parent.get("mb_album_release_group_id")
                _frd = confirmed.get("first-release-date", "")
                if _frd and len(_frd) >= 4 and not metadata.get("year"):
                    try:
                        metadata["year"] = int(_frd[:4])
                    except ValueError:
                        pass
                mb_used_ai_ids = True
                metadata["scraper_sources_used"].append("musicbrainz:ai_artist_xref")
                logs.append(
                    f"MusicBrainz: AI artist cross-ref found single "
                    f"'{confirmed.get('title')}' (rg={confirmed['id']}, "
                    f"release={_single_rel_id})"
                )
            else:
                logs.append(f"MusicBrainz: AI artist cross-ref — no matching single found")
        except Exception as e:
            logs.append(f"MusicBrainz: AI artist cross-ref failed: {e}")

    if not skip_musicbrainz and not mb_used_ai_ids:
        # Step B: Fall back to search-based MusicBrainz resolution
        try:
            search_artist = artist
            search_title = title
            # If AI resolved identity, use that for better search
            if has_ai and ai_result.identity.artist:
                search_artist = ai_result.identity.artist
            if has_ai and ai_result.identity.title:
                search_title = ai_result.identity.title

            mb = search_musicbrainz(search_artist, search_title)
            if mb.get("mb_recording_id"):
                # Validate the MB result against the expected identity.
                # Use primary artist (without featured credits) so that
                # "AronChupa featuring Little Sis Nora" still matches "AronChupa".
                from app.services.metadata_resolver import _normalize_for_compare, _tokens_overlap
                from app.services.source_validation import parse_multi_artist as _pma_mb
                _primary_for_match, _ = _pma_mb(search_artist)
                if mb.get("artist"):
                    mb_norm = _normalize_for_compare(mb["artist"])
                    exp_norm = _normalize_for_compare(search_artist)
                    primary_norm = _normalize_for_compare(_primary_for_match) if _primary_for_match else ""
                    if (mb_norm == exp_norm
                            or mb_norm == primary_norm
                            or _tokens_overlap(mb["artist"], search_artist, 0.4)
                            or (_primary_for_match and _tokens_overlap(mb["artist"], _primary_for_match, 0.4))):
                        metadata["mb_artist_id"] = mb["mb_artist_id"]
                        metadata["mb_recording_id"] = mb["mb_recording_id"]
                        if mb.get("mb_release_group_id"):
                            metadata["mb_release_group_id"] = mb["mb_release_group_id"]
                        if mb.get("mb_album_release_id"):
                            metadata["mb_album_release_id"] = mb["mb_album_release_id"]
                        if mb.get("mb_album_release_group_id"):
                            metadata["mb_album_release_group_id"] = mb["mb_album_release_group_id"]
                        metadata["artist"] = mb.get("artist") or metadata["artist"]
                        metadata["title"] = mb.get("title") or metadata["title"]
                        _mb_album_accepted = False
                        if mb.get("album"):
                            # When MusicBrainz found a parent album (e.g. single
                            # "Cosmic Love" → album "Lungs"), always prefer
                            # that over yt-dlp's album which is often just the
                            # single name.
                            # Guard: reject if album is just the song title
                            _mb_album = mb["album"]
                            _resolved_title = metadata.get("title", "")
                            _is_dup = _album_is_title_duplicate(_mb_album, _resolved_title)
                            # Even if names match, accept when MB found a
                            # genuinely different release group for the album
                            # (e.g. single "Unwritten" → album "Unwritten"
                            # with different RG IDs).
                            _has_distinct_rg = (
                                mb.get("mb_album_release_group_id")
                                and (
                                    # Track has no single RG → it only exists
                                    # on this album, so the album IS the
                                    # genuine parent even when names match.
                                    not mb.get("mb_release_group_id")
                                    or mb["mb_album_release_group_id"] != mb["mb_release_group_id"]
                                )
                            )
                            if _is_dup and not _has_distinct_rg:
                                logs.append(f"MusicBrainz: album '{_mb_album}' matches title — discarded")
                            else:
                                if _is_dup and _has_distinct_rg:
                                    logs.append(
                                        f"MusicBrainz: album '{_mb_album}' matches title but has "
                                        f"distinct release group — accepted as genuine parent album"
                                    )
                                metadata["album"] = _mb_album
                                _mb_album_accepted = True
                        # Always store mb_release_id — it points to the
                        # single's release and is needed for CoverArtArchive
                        # poster lookup regardless of album status.
                        metadata["mb_release_id"] = mb["mb_release_id"]
                        if mb.get("year") and not metadata.get("year"):
                            metadata["year"] = mb["year"]
                        if mb.get("genres"):
                            metadata["genres"] = mb["genres"]
                        metadata["scraper_sources_used"].append("musicbrainz:search")
                        logs.append(f"MusicBrainz: search match — {mb['artist']} - {mb.get('album', '?')}")
                    else:
                        logs.append(
                            f"MusicBrainz: search result '{mb['artist']}' doesn't match "
                            f"expected '{search_artist}' — discarded"
                        )
                else:
                    logs.append("MusicBrainz: no results from search")
        except Exception as e:
            logs.append(f"MusicBrainz: search failed: {e}")

    _mb_resolved = bool(metadata.get("mb_recording_id"))
    logs.append(f"MusicBrainz: resolution complete (resolved={_mb_resolved})")

    # ── Wikipedia resolution ──
    if not skip_wikipedia:
        wiki_used_ai_url = False

        if has_ai and ai_result.sources.wikipedia_url:
            # Step A: Try AI-provided Wikipedia URL directly
            logs.append(f"Wikipedia: using AI-provided URL: {ai_result.sources.wikipedia_url}")
            try:
                wiki = scrape_wikipedia_page(ai_result.sources.wikipedia_url)
                # Check if scrape returned meaningful data (not all-None from 404)
                _wiki_has_data = wiki and any(
                    wiki.get(k) for k in ("title", "artist", "plot", "genres")
                )
                if not _wiki_has_data:
                    logs.append(
                        "Wikipedia: AI-provided URL returned no usable data (page may not exist). "
                        "Falling back to search."
                    )
                elif wiki:
                    # Validate the scraped article matches
                    mismatch = detect_article_mismatch(
                        wiki,
                        ai_result.identity.artist or artist,
                        ai_result.identity.title or title,
                    )
                    if mismatch:
                        logs.append(
                            f"Wikipedia: AI-provided URL mismatch — {mismatch}. Rejecting."
                        )
                    else:
                        if wiki.get("album") and not metadata.get("album"):
                            metadata["album"] = wiki["album"]
                        if wiki.get("year") and not metadata.get("year"):
                            metadata["year"] = wiki["year"]
                        if wiki.get("genres") and not metadata.get("genres"):
                            metadata["genres"] = wiki["genres"]
                        if wiki.get("plot"):
                            metadata["plot"] = wiki["plot"]
                        if wiki.get("image_url"):
                            metadata["image_url"] = wiki["image_url"]
                        if wiki.get("page_type"):
                            metadata["wiki_page_type"] = wiki["page_type"]
                        metadata["source_url"] = ai_result.sources.wikipedia_url
                        wiki_used_ai_url = True
                        metadata["scraper_sources_used"].append("wikipedia:ai_url")
                        logs.append("Wikipedia: scraped via AI-provided URL successfully")
            except Exception as e:
                logs.append(f"Wikipedia: AI-provided URL scrape failed: {e}")

        if not wiki_used_ai_url:
            # Step B: Fall back to search-based Wikipedia resolution
            try:
                search_artist = artist
                search_title = title
                if has_ai and ai_result.identity.artist:
                    search_artist = ai_result.identity.artist
                # Use primary artist for Wikipedia search (strip featured credits)
                from app.services.source_validation import parse_multi_artist as _pma
                _wiki_primary, _ = _pma(search_artist)
                if _wiki_primary:
                    search_artist = _wiki_primary
                if has_ai and ai_result.identity.title:
                    search_title = ai_result.identity.title

                wiki_url = search_wikipedia(search_title, search_artist)
                if wiki_url:
                    wiki = scrape_wikipedia_page(wiki_url)
                    mismatch = detect_article_mismatch(
                        wiki, search_artist, search_title,
                    )
                    if mismatch:
                        logs.append(f"Wikipedia: search result mismatch — {mismatch}. Discarding.")
                        metadata["_wiki_single_rejected"] = True
                    else:
                        if wiki.get("album") and not metadata.get("album"):
                            metadata["album"] = wiki["album"]
                        if wiki.get("year") and not metadata.get("year"):
                            metadata["year"] = wiki["year"]
                        if wiki.get("genres") and not metadata.get("genres"):
                            metadata["genres"] = wiki["genres"]
                        if wiki.get("plot"):
                            metadata["plot"] = wiki["plot"]
                        if wiki.get("image_url"):
                            metadata["image_url"] = wiki["image_url"]
                        if wiki.get("page_type"):
                            metadata["wiki_page_type"] = wiki["page_type"]
                        metadata["source_url"] = wiki_url
                        metadata["scraper_sources_used"].append("wikipedia:search")
                        logs.append(f"Wikipedia: search match — {wiki_url}")
                else:
                    logs.append("Wikipedia: no confident search match found")
            except Exception as e:
                logs.append(f"Wikipedia: search failed: {e}")

    # ── Wikipedia album-link fallback ──
    # If we have a Wikipedia single/song page but no album was resolved,
    # follow the "from the album" infobox link to discover the album name.
    _wiki_source = metadata.get("source_url", "")
    if (
        not metadata.get("album")
        and _wiki_source
        and "wikipedia.org" in _wiki_source
    ):
        try:
            album_wiki_url = extract_album_wiki_url_from_single(_wiki_source)
            if album_wiki_url:
                logs.append(f"Wikipedia: following album link from single infobox: {album_wiki_url}")
                album_wiki = scrape_wikipedia_page(album_wiki_url)
                if album_wiki and album_wiki.get("title"):
                    _album_name = album_wiki["title"]
                    _resolved_title = metadata.get("title") or title
                    if _album_is_title_duplicate(_album_name, _resolved_title):
                        # Check if MB gave us a distinct release group
                        _has_distinct_rg = (
                            metadata.get("mb_album_release_group_id")
                            and (
                                not metadata.get("mb_release_group_id")
                                or metadata["mb_album_release_group_id"] != metadata["mb_release_group_id"]
                            )
                        )
                        if _has_distinct_rg:
                            metadata["album"] = _album_name
                            logs.append(
                                f"Wikipedia: album '{_album_name}' from infobox link matches title "
                                f"but has distinct release group — accepted"
                            )
                        else:
                            logs.append(
                                f"Wikipedia: album '{_album_name}' from infobox link matches title — discarded"
                            )
                    else:
                        metadata["album"] = _album_name
                        logs.append(f"Wikipedia: album '{_album_name}' resolved from single infobox link")
                else:
                    logs.append("Wikipedia: album page from infobox link had no usable title")
            else:
                logs.append("Wikipedia: no album link found in single page infobox")
        except Exception as e:
            logs.append(f"Wikipedia: album-link fallback failed: {e}")

    # ── IMDB resolution ──
    imdb_used_ai_url = False
    if has_ai and ai_result.sources.imdb_url:
        logs.append(f"IMDB: using AI-provided URL: {ai_result.sources.imdb_url}")
        metadata["imdb_url"] = ai_result.sources.imdb_url
        imdb_used_ai_url = True
        metadata["scraper_sources_used"].append("imdb:ai_url")

    if not imdb_used_ai_url and not (skip_wikipedia and skip_musicbrainz):
        try:
            search_artist = artist
            search_title = title
            if has_ai and ai_result.identity.artist:
                search_artist = ai_result.identity.artist
            if has_ai and ai_result.identity.title:
                search_title = ai_result.identity.title

            imdb_url = search_imdb_music_video(search_artist, search_title)
            if imdb_url:
                metadata["imdb_url"] = imdb_url
                metadata["scraper_sources_used"].append("imdb:search")
                logs.append(f"IMDB: search match — {imdb_url}")
        except Exception as e:
            logs.append(f"IMDB: search failed: {e}")

    # Use AI-resolved identity if scraper didn't find better data
    if has_ai and ai_result.confidence.identity >= 0.7:
        if ai_result.identity.artist and not metadata.get("artist"):
            metadata["artist"] = ai_result.identity.artist
        if ai_result.identity.title and not metadata.get("title"):
            metadata["title"] = ai_result.identity.title
        if ai_result.identity.album and not metadata.get("album"):
            _resolved_title = metadata.get("title") or ai_result.identity.title or ""
            if _album_is_title_duplicate(ai_result.identity.album, _resolved_title):
                logs.append(f"AI album '{ai_result.identity.album}' matches title — discarded")
            else:
                metadata["album"] = ai_result.identity.album

    # ── Album sanitization: strip "Title - Single" patterns ──
    from app.services.source_validation import sanitize_album
    if metadata.get("album"):
        _orig_album = metadata["album"]
        metadata["album"] = sanitize_album(
            metadata["album"],
            title=metadata.get("title") or "",
        )
        if metadata["album"] != _orig_album:
            logs.append(f"Album sanitized: '{_orig_album}' → {metadata['album'] or 'null'}")

    # ── Multi-artist parsing & normalization ──
    from app.services.source_validation import parse_multi_artist, normalize_feat_to_semicolons, build_artist_ids
    if metadata.get("artist"):
        primary, featured = parse_multi_artist(metadata["artist"])
        metadata["primary_artist"] = primary
        metadata["featured_artists"] = featured
        # Normalize "feat."/"ft."/"featuring" to semicolon-separated format
        _orig_artist = metadata["artist"]
        metadata["artist"] = normalize_feat_to_semicolons(metadata["artist"])
        if metadata["artist"] != _orig_artist:
            logs.append(f"Artist normalized: '{_orig_artist}' → '{metadata['artist']}'")
        # Build artist_ids list from MB credits or parsed artists
        metadata["artist_ids"] = build_artist_ids(
            metadata["artist"],
            mb_artist_credits=metadata.get("mb_artist_credits"),
            primary_mb_artist_id=metadata.get("mb_artist_id"),
        )
    else:
        metadata["primary_artist"] = metadata.get("artist", "")
        metadata["featured_artists"] = []

    # ── Wikipedia page type classification ──
    # Propagate page_type from scraper if available
    # (used by tasks.py to assign correct source_type)

    return metadata, logs


# ---------------------------------------------------------------------------
# Unified metadata resolution — the ONE entry point
# ---------------------------------------------------------------------------

def resolve_metadata_unified(
    *,
    artist: str,
    title: str,
    db: Optional[Session] = None,
    # Platform context
    source_url: str = "",
    platform_title: str = "",
    channel_name: str = "",
    platform_description: str = "",
    platform_tags: Optional[List[str]] = None,
    upload_date: str = "",
    # File context
    filename: str = "",
    folder_name: str = "",
    duration_seconds: Optional[float] = None,
    # Fingerprint
    fingerprint_artist: str = "",
    fingerprint_title: str = "",
    fingerprint_confidence: float = 0.0,
    # yt-dlp metadata
    ytdlp_metadata: Optional[Dict[str, Any]] = None,
    # Options
    skip_wikipedia: bool = False,
    skip_musicbrainz: bool = False,
    skip_ai: bool = False,
    log_callback=None,
) -> Dict[str, Any]:
    """
    Unified metadata resolution using the source-guided pipeline.

    This function is the SINGLE entry point for ALL metadata resolution:
    - Automatic import pipeline
    - Manual "Analyze Metadata"
    - Manual "Scrape Metadata"
    - Any AI-assisted metadata tool

    Pipeline:
    1. AI Source Resolution (if enabled) — determine identity and source links
    2. Scraper fetch — use AI-provided links first, fall back to search
    3. Scraper validation — verify results match expected identity
    4. AI Final Review (if enabled) — verify and correct scraped metadata
    5. Return combined metadata

    Args:
        artist: Parsed artist name
        title: Parsed title
        db: SQLAlchemy session (for reading settings)
        source_url: YouTube/Vimeo source URL
        platform_title: Original platform video title
        channel_name: Channel/uploader name
        platform_description: Video description from platform
        platform_tags: Tags from platform
        upload_date: Upload date (YYYYMMDD)
        filename: Video filename
        folder_name: Folder name
        duration_seconds: Video duration
        fingerprint_artist: Artist from audio fingerprint
        fingerprint_title: Title from audio fingerprint
        fingerprint_confidence: Fingerprint confidence
        ytdlp_metadata: Raw yt-dlp metadata dict
        skip_wikipedia: Skip Wikipedia scraping
        skip_musicbrainz: Skip MusicBrainz scraping
        skip_ai: Force skip AI stages (e.g. when AI disabled)
        log_callback: Optional fn(message: str) for progress logs

    Returns:
        Comprehensive metadata dict with keys:
        artist, title, album, year, genres, plot, mb_artist_id,
        mb_recording_id, mb_release_id, image_url, source_url,
        imdb_url, ai_source_resolution, ai_final_review,
        scraper_sources_used, pipeline_log
    """

    def _log(msg: str):
        logger.info(msg)
        if log_callback:
            log_callback(msg)

    # Check AI settings
    ai_enabled = not skip_ai
    ai_source_resolution_enabled = False
    ai_final_review_enabled = False

    if ai_enabled and db:
        ai_provider = _get_setting_str(db, "ai_provider", "none")
        if ai_provider == "none":
            ai_enabled = False
        else:
            ai_source_resolution_enabled = True
            ai_final_review_enabled = True

    pipeline_log: List[str] = []
    pipeline_failures: List[Dict[str, str]] = []
    ai_source_result: Optional[SourceResolutionResult] = None

    # ── Stage 1: AI Source Resolution ──
    if ai_enabled and ai_source_resolution_enabled:
        _log("Running AI source resolution...")
        pipeline_log.append("stage:ai_source_resolution:started")

        ai_source_result = resolve_sources_with_ai(
            source_url=source_url,
            platform_title=platform_title,
            channel_name=channel_name,
            platform_description=platform_description,
            filename=filename,
            folder_name=folder_name,
            duration_seconds=duration_seconds,
            parsed_artist=artist,
            parsed_title=title,
            fingerprint_artist=fingerprint_artist,
            fingerprint_title=fingerprint_title,
            fingerprint_confidence=fingerprint_confidence,
            db=db,
        )

        if ai_source_result and not ai_source_result.error:
            pipeline_log.append(
                f"stage:ai_source_resolution:complete "
                f"identity_conf={ai_source_result.confidence.identity:.2f} "
                f"sources_conf={ai_source_result.confidence.sources:.2f}"
            )
            _log(
                f"AI Source Resolution: {ai_source_result.identity.artist} - "
                f"{ai_source_result.identity.title} "
                f"(identity={ai_source_result.confidence.identity:.2f}, "
                f"sources={ai_source_result.confidence.sources:.2f})"
            )

            # If AI identified the track with high confidence, update artist/title
            # for better scraper results
            if ai_source_result.confidence.identity >= 0.7:
                if ai_source_result.identity.artist:
                    _log(f"Using AI-resolved artist: {ai_source_result.identity.artist}")
                    artist = ai_source_result.identity.artist
                if ai_source_result.identity.title:
                    _log(f"Using AI-resolved title: {ai_source_result.identity.title}")
                    title = ai_source_result.identity.title

            # Log AI-provided source links
            if ai_source_result.sources.wikipedia_url:
                _log(f"AI suggests Wikipedia URL: {ai_source_result.sources.wikipedia_url}")
            if ai_source_result.sources.musicbrainz_recording_id:
                _log(f"AI suggests MB recording: {ai_source_result.sources.musicbrainz_recording_id}")
            if ai_source_result.sources.imdb_url:
                _log(f"AI suggests IMDB: {ai_source_result.sources.imdb_url}")
        elif ai_source_result and ai_source_result.error:
            pipeline_log.append(f"stage:ai_source_resolution:failed:{ai_source_result.error[:80]}")
            _log(f"AI source resolution: failed \u2014 {ai_source_result.error}")
            pipeline_failures.append({
                "code": "AI_SOURCE_FAILED",
                "description": f"AI source resolution failed \u2014 {ai_source_result.error}",
            })
        else:
            pipeline_log.append("stage:ai_source_resolution:skipped_or_failed")
            _log("AI source resolution: no result (provider unavailable or failed)")
            pipeline_failures.append({
                "code": "AI_SOURCE_FAILED",
                "description": "AI source resolution failed — track identity may be incorrect",
            })
    else:
        pipeline_log.append("stage:ai_source_resolution:disabled")
        if not ai_enabled:
            _log("AI source resolution: disabled (AI not enabled)")

    # ── Stage 2: Scraper Fetch (using AI-provided links first) ──
    _log("Running source-guided scraper fetch...")
    pipeline_log.append("stage:scraper_fetch:started")

    metadata, scraper_logs = _scrape_with_ai_links(
        artist=artist,
        title=title,
        ai_result=ai_source_result,
        ytdlp_metadata=ytdlp_metadata,
        skip_wikipedia=skip_wikipedia,
        skip_musicbrainz=skip_musicbrainz,
        log_callback=log_callback,
    )

    for sl in scraper_logs:
        _log(f"  Scraper: {sl}")
        pipeline_log.append(f"scraper:{sl}")

    pipeline_log.append("stage:scraper_fetch:complete")

    # Track scraper failures — if a source was attempted but produced no data
    _sources_used = metadata.get("scraper_sources_used", [])
    if not skip_wikipedia and not any(s.startswith("wikipedia:") for s in _sources_used):
        pipeline_failures.append({
            "code": "WIKI_SCRAPE_FAILED",
            "description": "Wikipedia scraping failed — plot and genre data unavailable",
        })
    if not skip_musicbrainz and not any(s.startswith("musicbrainz:") for s in _sources_used):
        pipeline_failures.append({
            "code": "MB_LOOKUP_FAILED",
            "description": "MusicBrainz lookup failed — album, year, and MB IDs unavailable",
        })

    # ── Stage 3: Scraper Validation ──
    # Scrapers already validate via detect_article_mismatch inside
    # _scrape_with_ai_links. Additional validation happens here.
    pipeline_log.append("stage:validation:complete")

    # ── Stage 4: AI Final Review ──
    ai_review_result: Optional[FinalReviewResult] = None

    if ai_enabled and ai_final_review_enabled:
        _log("Running AI final review...")
        pipeline_log.append("stage:ai_final_review:started")

        # Build scraper sources description for the review prompt
        sources_desc = "\n".join(
            f"- {s}" for s in metadata.get("scraper_sources_used", [])
        ) or "No scraper sources were successfully used"

        ai_review_result = run_final_review(
            source_url=source_url,
            platform_title=platform_title,
            channel_name=channel_name,
            platform_description=platform_description,
            filename=filename,
            duration_seconds=duration_seconds,
            scraped_artist=metadata.get("artist", artist),
            scraped_title=metadata.get("title", title),
            scraped_album=metadata.get("album", ""),
            scraped_year=metadata.get("year"),
            scraped_genres=metadata.get("genres"),
            scraped_plot=metadata.get("plot", ""),
            resolved_artist=ai_source_result.identity.artist if (ai_source_result and not ai_source_result.error) else artist,
            resolved_title=ai_source_result.identity.title if (ai_source_result and not ai_source_result.error) else title,
            resolved_album=ai_source_result.identity.album if (ai_source_result and not ai_source_result.error) else "",
            version_type=(ai_source_result.identity.version_type if (ai_source_result and not ai_source_result.error) else "normal"),
            scraper_sources=sources_desc,
            db=db,
        )

        if ai_review_result and not ai_review_result.error:
            pipeline_log.append(
                f"stage:ai_final_review:complete "
                f"confidence={ai_review_result.overall_confidence:.2f} "
                f"changes={len(ai_review_result.changes)}"
            )

            # Apply corrections from final review (only high-confidence changes)
            MIN_FIELD_CONFIDENCE = 0.7
            # Lower threshold for AI-generated plot when no existing plot —
            # any generated plot is better than nothing.
            MIN_PLOT_GENERATE_CONFIDENCE = 0.4
            changes_applied = []

            for field_name in ("artist", "title", "album", "year", "genres", "plot"):
                proposed = getattr(ai_review_result, field_name, None)
                if proposed is None:
                    continue
                field_conf = ai_review_result.field_scores.get(field_name, 0.0)

                # Use lower confidence threshold for AI-generated plots
                # when no existing plot exists.
                _effective_min_conf = MIN_FIELD_CONFIDENCE
                if field_name == "plot" and not metadata.get("plot"):
                    _effective_min_conf = MIN_PLOT_GENERATE_CONFIDENCE
                if field_conf < _effective_min_conf:
                    continue

                # MusicBrainz parent-album resolution is authoritative;
                # don't let AI revert "Lungs" back to "Cosmic Love" etc.
                if field_name == "album" and metadata.get("mb_album_release_group_id"):
                    _log(f"AI Final Review: skipping album override "
                         f"(MB release-group is authoritative): "
                         f"'{proposed}' rejected, keeping '{metadata['album']}'")
                    continue

                # Reject AI album when it's just the song title (hallucination)
                if field_name == "album" and _album_is_title_duplicate(
                    str(proposed), str(metadata.get("title", ""))
                ):
                    # Allow self-titled correction when AI replaces a different (wrong) album
                    _current_album = metadata.get("album")
                    if _current_album and not _album_is_title_duplicate(
                        str(_current_album), str(metadata.get("title", ""))
                    ):
                        _log(f"AI Final Review: allowing self-titled album '{proposed}' "
                             f"(replacing different album '{_current_album}')")
                    else:
                        _log(f"AI Final Review: album '{proposed}' matches title — discarded")
                        continue

                # Reject AI album when it's a sentinel / placeholder value
                if field_name == "album":
                    _sentinel_check = str(proposed).strip().lower()
                    _ALBUM_SENTINELS = {
                        "unknown", "unknown album", "n/a", "na", "none",
                        "null", "nil", "no album", "untitled", "tbd",
                        "not available", "not applicable", "[not set]",
                        "-", "--", "\u2014", "?",
                    }
                    if _sentinel_check in _ALBUM_SENTINELS:
                        _log(f"AI Final Review: album '{proposed}' is a sentinel — discarded")
                        continue

                # Run full album sanitization on AI-proposed album — catches
                # storefront "Title - Single" labels the AI hallucinated
                if field_name == "album":
                    from app.services.source_validation import sanitize_album as _sanitize_ai_album
                    _sanitized_proposed = _sanitize_ai_album(
                        str(proposed), title=str(metadata.get("title", ""))
                    )
                    if _sanitized_proposed is None:
                        _log(f"AI Final Review: album '{proposed}' sanitized to null "
                             f"(storefront single label) — discarded")
                        continue
                    proposed = _sanitized_proposed

                # Reject AI plot if it looks like raw YouTube description
                # (contains URLs, social media links, or lyrics dumps)
                if field_name == "plot" and proposed:
                    import re as _re_plot
                    _url_count = len(_re_plot.findall(r'https?://', str(proposed)))
                    _has_social = bool(_re_plot.search(
                        r'instagram\.com|facebook\.com|twitter\.com|tiktok\.com'
                        r'|smarturl\.it|lnk\.to|linktr\.ee',
                        str(proposed), _re_plot.IGNORECASE,
                    ))
                    if _url_count >= 3 or _has_social:
                        _log(f"AI Final Review: plot rejected — contains {_url_count} URLs"
                             f"{' and social media links' if _has_social else ''}"
                             f" (looks like YouTube description)")
                        continue

                current = metadata.get(field_name)
                if str(proposed) != str(current):
                    metadata[field_name] = proposed
                    changes_applied.append(
                        f"{field_name}: '{current}' → '{proposed}' (conf={field_conf:.2f})"
                    )

            if changes_applied:
                _log(f"AI Final Review applied {len(changes_applied)} correction(s):")
                for c in changes_applied:
                    _log(f"  {c}")
                    pipeline_log.append(f"ai_review_change:{c}")

            # ── Process proposed removals ──
            # The AI can recommend removing specific fields when the scraped
            # value is incorrect and no correct replacement was found.
            # This handles the case where a previous scrape found a wrong
            # value that should be cleared rather than kept.
            _REMOVABLE_FIELDS = {"album", "year", "genres", "plot"}
            for _rm_field, _rm_reason in (ai_review_result.proposed_removals or {}).items():
                if _rm_field not in _REMOVABLE_FIELDS:
                    _log(f"AI Final Review: ignoring removal of non-removable "
                         f"field '{_rm_field}'")
                    continue
                _rm_current = metadata.get(_rm_field)
                if not _rm_current:
                    continue  # Already empty, nothing to remove

                # MB release-group is authoritative for album — don't remove
                if _rm_field == "album" and metadata.get("mb_album_release_group_id"):
                    _log(f"AI Final Review: skipping removal of album "
                         f"(MB release-group is authoritative): "
                         f"keeping '{_rm_current}'")
                    continue

                metadata[_rm_field] = None
                _log(f"AI Final Review: removed {_rm_field} "
                     f"(was '{_rm_current}'): {_rm_reason}")
                pipeline_log.append(
                    f"ai_review_removal:{_rm_field}:{_rm_reason}"
                )
                changes_applied.append(
                    f"{_rm_field}: '{_rm_current}' → removed ({_rm_reason})"
                )

            # Handle artwork rejection — only clear image_url when the AI
            # actually reviewed artwork and rejected it.  When the rejection
            # reason says "no artwork provided" the AI never saw an image, so
            # the scraper-sourced image_url (e.g. Wikipedia single cover)
            # should be preserved.
            if not ai_review_result.artwork_approved:
                _reason = ai_review_result.artwork_rejection_reason or ""
                _no_art_phrases = ("no artwork provided", "no artwork")
                _ai_had_no_artwork = any(p in _reason.lower() for p in _no_art_phrases)
                if _ai_had_no_artwork and metadata.get("image_url"):
                    _log(f"AI Final Review: no artwork sent to AI — preserving scraper image_url")
                else:
                    _log(f"AI Final Review rejected artwork: {_reason}")
                    metadata["image_url"] = None  # Clear to use placeholder
                pipeline_log.append(
                    f"ai_review_artwork_rejected:{_reason}"
                )
        else:
            if ai_review_result and ai_review_result.error:
                pipeline_log.append(f"stage:ai_final_review:failed:{ai_review_result.error[:80]}")
                _log(f"AI final review: failed \u2014 {ai_review_result.error}")
            else:
                pipeline_log.append("stage:ai_final_review:skipped_or_failed")
                _log("AI final review: no result (provider unavailable or failed)")
            metadata["ai_final_review_failed"] = True
            pipeline_failures.append({
                "code": "AI_REVIEW_FAILED",
                "description": f"AI final review failed \u2014 {ai_review_result.error if ai_review_result and ai_review_result.error else 'provider unavailable'}",
            })
    else:
        pipeline_log.append("stage:ai_final_review:disabled")

    # ── Re-sync primary_artist after AI Final Review ──
    # primary_artist is set inside _scrape_with_ai_links() BEFORE the review
    # runs, so if the review changed metadata["artist"] the primary_artist
    # field is stale.  Downstream consumers (artwork pipeline, Wikipedia
    # artist source recording) rely on primary_artist — a stale value causes
    # false positives (e.g. fetching artwork for the sampled artist instead
    # of the actual artist).
    from app.services.source_validation import parse_multi_artist as _pma_resync
    if metadata.get("artist"):
        _resynced_primary, _resynced_featured = _pma_resync(metadata["artist"])
        if metadata.get("primary_artist") != _resynced_primary:
            _log(f"Re-synced primary_artist: '{metadata.get('primary_artist')}' → '{_resynced_primary}'")
            metadata["primary_artist"] = _resynced_primary
            metadata["featured_artists"] = _resynced_featured

    # ── Invalidate album when AI changed the artist identity ──
    # When the AI Final Review changed the artist (e.g. from "4 Non Blondes"
    # to "slackcircus"), any album that was resolved under the OLD artist
    # identity is almost certainly wrong.  Only clear it when there's no
    # authoritative MusicBrainz confirmation — MB-confirmed albums survive.
    # We detect artist change by checking if the review proposed a different
    # artist than the pre-review value.
    #
    # IMPORTANT: Normalise featuring/collaboration separators before comparing.
    # "A Great Big World featuring Christina Aguilera" → "A Great Big World & Christina Aguilera"
    # is NOT an identity change — it's a formatting change.  Only trigger when the
    # underlying set of artists actually differs.
    _artist_changed_by_review = False
    if (ai_review_result
            and not ai_review_result.error
            and ai_review_result.artist is not None
            and ai_source_result is not None
            and not ai_source_result.error
            and ai_source_result.identity.artist):
        _pre_review_artist = ai_source_result.identity.artist
        _post_review_artist = ai_review_result.artist
        if _pre_review_artist.lower() != _post_review_artist.lower():
            # Check whether only the collaboration separator changed
            # (feat. / featuring / & / and / with / ,) while the actual
            # artists are the same set of people.
            _pma_id = _pma_resync  # already imported above
            _pre_primary, _pre_feat = _pma_id(_pre_review_artist)
            _post_primary, _post_feat = _pma_id(_post_review_artist)
            _pre_set = {_pre_primary.lower()} | {f.lower() for f in _pre_feat}
            _post_set = {_post_primary.lower()} | {f.lower() for f in _post_feat}
            if _pre_set != _post_set:
                _artist_changed_by_review = True
                _log(f"Artist identity changed by review: '{_pre_review_artist}' → '{_post_review_artist}'")
            else:
                _log(f"Artist formatting changed by review (not an identity change): "
                     f"'{_pre_review_artist}' → '{_post_review_artist}'")

    if _artist_changed_by_review:
        # ── Clear album if not MB-confirmed ──
        if metadata.get("album") and not metadata.get("mb_album_release_group_id"):
            _stale_album = metadata["album"]
            metadata["album"] = None
            _log(f"Album '{_stale_album}' cleared — artist identity changed by AI review "
                 f"and no MusicBrainz album confirmation exists")
            pipeline_log.append(f"album_cleared_after_artist_change:{_stale_album}")

        # ── Clear IMDB URL found via search under the old identity ──
        _imdb_from_search = metadata.get("imdb_url") and any(
            s == "imdb:search" for s in metadata.get("scraper_sources_used", [])
        )
        if _imdb_from_search:
            _stale_imdb = metadata["imdb_url"]
            metadata["imdb_url"] = None
            _log(f"IMDB URL '{_stale_imdb}' cleared — found via search under old artist identity")
            pipeline_log.append(f"imdb_cleared_after_artist_change:{_stale_imdb}")

        # ── Clear MusicBrainz IDs resolved under the wrong identity ──
        # These were looked up for the pre-review artist (e.g. "4 Non Blondes")
        # and would contaminate sources/entities for the actual artist.
        _mb_fields = ["mb_recording_id", "mb_release_id", "mb_artist_id",
                      "mb_release_group_id", "mb_album_release_group_id",
                      "mb_album_release_id"]
        _cleared_mb = []
        for _mbf in _mb_fields:
            if metadata.get(_mbf):
                _cleared_mb.append(f"{_mbf}={metadata[_mbf]}")
                metadata[_mbf] = None
        if _cleared_mb:
            _log(f"MusicBrainz IDs cleared after identity change: {', '.join(_cleared_mb)}")
            pipeline_log.append(f"mb_cleared_after_artist_change:{','.join(_cleared_mb)}")

        # ── Clear Wikipedia source URL resolved under wrong identity ──
        if metadata.get("source_url") and "wikipedia.org" in metadata.get("source_url", ""):
            _stale_wiki = metadata["source_url"]
            metadata["source_url"] = None
            _log(f"Wikipedia source URL '{_stale_wiki}' cleared — resolved under old identity")
            pipeline_log.append(f"wiki_source_cleared_after_artist_change:{_stale_wiki}")

            # Re-resolve the Wikipedia single/song URL under the corrected identity
            try:
                from app.services.metadata_resolver import search_wikipedia as _search_wiki_song
                _new_wiki = _search_wiki_song(
                    metadata.get("title") or "",
                    _post_review_artist,
                )
                if _new_wiki:
                    metadata["source_url"] = _new_wiki
                    _log(f"Wikipedia source URL re-resolved under new identity: {_new_wiki}")
                    pipeline_log.append(f"wiki_source_reresolved:{_new_wiki}")
            except Exception as _wiki_err:
                _log(f"Wikipedia re-resolution failed (non-fatal): {_wiki_err}")

    # ── Final album sanitization (catches AI-introduced sentinels like "Unknown") ──
    from app.services.source_validation import sanitize_album as _sanitize_album_final
    if metadata.get("album"):
        _pre_final = metadata["album"]
        metadata["album"] = _sanitize_album_final(
            metadata["album"],
            title=metadata.get("title") or "",
        )
        if metadata["album"] != _pre_final:
            _log(f"Post-review album sanitized: '{_pre_final}' → {metadata['album'] or 'null'}")

    # ── Attach pipeline metadata ──
    metadata["ai_source_resolution"] = ai_source_result.to_dict() if ai_source_result else None
    metadata["ai_final_review"] = {
        "corrections": ai_review_result.changes if ai_review_result else [],
        "overrides": ai_review_result.scraper_overrides if ai_review_result else [],
        "proposed_removals": ai_review_result.proposed_removals if ai_review_result else {},
        "artwork_approved": ai_review_result.artwork_approved if ai_review_result else True,
        "confidence": ai_review_result.overall_confidence if ai_review_result else None,
        "error": (ai_review_result.error or None) if ai_review_result else None,
    } if ai_review_result else None
    metadata["pipeline_log"] = pipeline_log
    metadata["pipeline_failures"] = pipeline_failures

    return metadata
