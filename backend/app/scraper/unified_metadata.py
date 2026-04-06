# SHARED SCRAPER MODULE - source of truth for all scraping pathways.
# Used by: scraper tester, URL import, rescan, scrape metadata, (future) library import.
"""
Unified Metadata Resolution Service Ã¢â‚¬â€ Single code path for both automatic
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
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote as _url_unquote

from sqlalchemy.orm import Session

from app.scraper.ai.source_resolution import (
    SourceResolutionResult, resolve_sources_with_ai,
)
from app.scraper.ai.final_review import FinalReviewResult, run_final_review
from app.scraper.metadata_resolver import (
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
# AI identity validation (P2 — hallucination guard)
# ---------------------------------------------------------------------------

def _validate_ai_identity(
    parsed_artist: str,
    parsed_title: str,
    ai_artist: str,
    ai_title: str,
) -> tuple:
    """Compare AI-resolved identity against original parsed values.

    Returns (is_valid, confidence_multiplier, reason).
    - is_valid=False means reject AI identity entirely
    - confidence_multiplier < 1.0 means halve/reduce confidence
    """
    sim_artist = SequenceMatcher(
        None, parsed_artist.lower().strip(), ai_artist.lower().strip()
    ).ratio()
    sim_title = SequenceMatcher(
        None, parsed_title.lower().strip(), ai_title.lower().strip()
    ).ratio()

    # Total hallucination — both artist and title completely different
    if sim_artist < 0.3 and sim_title < 0.3:
        return (
            False,
            0.0,
            f"Identity hallucination detected: "
            f"parsed='{parsed_artist} - {parsed_title}' vs "
            f"AI='{ai_artist} - {ai_title}' "
            f"(sim_artist={sim_artist:.2f}, sim_title={sim_title:.2f})",
        )

    # Partial mismatch — reduce confidence to prevent auto-apply
    if sim_artist < 0.4 or sim_title < 0.4:
        return (
            True,
            0.5,
            f"Low identity similarity: "
            f"parsed='{parsed_artist} - {parsed_title}' vs "
            f"AI='{ai_artist} - {ai_title}' "
            f"(sim_artist={sim_artist:.2f}, sim_title={sim_title:.2f}) "
            f"— confidence halved",
        )

    # Reasonable match — trust AI
    return (True, 1.0, "")


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
    wikipedia_url: Optional[str] = None,
    musicbrainz_url: Optional[str] = None,
    log_callback=None,
    parsed_title: Optional[str] = None,
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
        "_artwork_candidates": [],   # All artwork URLs from different sources
        "_source_urls": {},          # Source URLs by type (wikipedia, imdb, etc.)
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

    # Ã¢â€â‚¬Ã¢â€â‚¬ MusicBrainz resolution Ã¢â€â‚¬Ã¢â€â‚¬
    mb_used_ai_ids = False
    # User-provided MusicBrainz URL takes highest priority
    _user_mb_rec_id = None
    _user_mb_rg_id = None
    if musicbrainz_url and musicbrainz_url.strip():
        import re as _re_mb
        _mb_match = _re_mb.search(r"musicbrainz\.org/recording/([a-f0-9-]+)", musicbrainz_url.strip())
        if _mb_match:
            _user_mb_rec_id = _mb_match.group(1)
        else:
            _mb_rg_match = _re_mb.search(r"musicbrainz\.org/release-group/([a-f0-9-]+)", musicbrainz_url.strip())
            if _mb_rg_match:
                _user_mb_rg_id = _mb_rg_match.group(1)
    _ai_mb_id = (ai_result.sources.musicbrainz_recording_id if has_ai else None) or None
    _effective_mb_id = _user_mb_rec_id or _ai_mb_id
    logs.append(f"MusicBrainz: entering resolution (skip={skip_musicbrainz}, has_ai={has_ai}, ai_mb_id={bool(_ai_mb_id)}, user_url={bool(_user_mb_rec_id)}, user_rg={bool(_user_mb_rg_id)})")

    if skip_musicbrainz:
        logs.append("MusicBrainz: skipped (disabled)")
    elif _effective_mb_id:
        # Step A: Try user-provided or AI-provided MusicBrainz IDs directly
        _mb_id_source = "user-provided URL" if _user_mb_rec_id else "AI-provided"
        logs.append(f"MusicBrainz: using {_mb_id_source} recording ID: {_effective_mb_id}")
        try:
            import musicbrainzngs
            from app.scraper.metadata_resolver import _init_musicbrainz, _pick_best_release
            import time
            _init_musicbrainz()

            rec = musicbrainzngs.get_recording_by_id(
                _effective_mb_id,
                includes=["artists", "releases", "tags"],
            )
            recording = rec.get("recording", {})
            if recording:
                # Validate that the recording title is similar to the expected
                # title.  AI sometimes provides the wrong MusicBrainz ID
                # (e.g. "Intro" instead of "Something" from the same EP).
                _rec_title = recording.get("title", "")
                if _rec_title and title:
                    from difflib import SequenceMatcher as _SM_ai
                    _ai_tsim = _SM_ai(None, title.lower().strip(), _rec_title.lower().strip()).ratio()
                    if _ai_tsim < 0.5:
                        logs.append(
                            f"MusicBrainz: AI-provided recording rejected Ã¢â‚¬â€ "
                            f"title '{_rec_title}' doesn't match '{title}' "
                            f"(sim={_ai_tsim:.2f}). Falling back to search."
                        )
                        raise ValueError("Recording title mismatch")

                # Release Ã¢â‚¬â€ pick best single/EP only
                releases = recording.get("release-list", [])
                best_rel = _pick_best_release(releases, allowed_types={"single", "ep"})

                # Music videos must be from singles/EPs. If the AI-provided
                # recording has no single or EP release, reject it and fall
                # back to search so we can find the correct recording.
                from app.scraper.metadata_resolver import _RELEASE_TYPE_PRIORITY
                _rel_type = ""
                if best_rel:
                    _rg = best_rel.get("release-group", {})
                    _rel_type = (_rg.get("primary-type") or _rg.get("type") or "").lower()
                if _rel_type not in ("single", "ep"):
                    logs.append(
                        f"MusicBrainz: AI-provided recording rejected - "
                        f"best release type is '{_rel_type or 'unknown'}', "
                        f"not 'single'/'ep'. Falling back to search."
                    )
                    raise ValueError("Recording is not from a single or EP")

                metadata["mb_recording_id"] = recording.get("id")

                # Artist Ã¢â‚¬â€ validate that the recording's artist matches what
                # we expect.  AI sometimes provides a recording by a
                # different artist with the same song title.
                artist_credits = recording.get("artist-credit", [])
                if artist_credits:
                    ac = artist_credits[0]
                    if isinstance(ac, dict) and "artist" in ac:
                        _mb_artist_name = ac["artist"].get("name", "")
                        if _mb_artist_name and artist:
                            from difflib import SequenceMatcher as _SM_art
                            from app.scraper.metadata_resolver import (
                                _normalize_for_compare, _tokens_overlap,
                            )
                            _art_sim = _SM_art(
                                None,
                                _normalize_for_compare(artist),
                                _normalize_for_compare(_mb_artist_name),
                            ).ratio()
                            _art_tok = _tokens_overlap(_mb_artist_name, artist, 0.4)
                            if _art_sim < 0.6 and not _art_tok:
                                logs.append(
                                    f"MusicBrainz: AI-provided recording rejected Ã¢â‚¬â€ "
                                    f"artist '{_mb_artist_name}' doesn't match "
                                    f"'{artist}' (sim={_art_sim:.2f}). "
                                    f"Falling back to search."
                                )
                                raise ValueError("Recording artist mismatch")
                        metadata["artist"] = _mb_artist_name or artist
                        metadata["mb_artist_id"] = ac["artist"].get("id")

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
                if metadata.get("mb_recording_id"):
                    metadata["_source_urls"]["musicbrainz"] = f"https://musicbrainz.org/recording/{metadata['mb_recording_id']}"
                logs.append(f"MusicBrainz: resolved via AI ID Ã¢â‚¬â€ {metadata['artist']} - {metadata.get('album', '?')}")

            time.sleep(1.1)  # Rate limit
        except Exception as e:
            logs.append(f"MusicBrainz: AI-provided ID lookup failed: {e}")
            mb_used_ai_ids = False

    # Find parent album release group for AI-resolved recording
    # (search path already sets this via search_musicbrainz, but AI path skips it)
    if mb_used_ai_ids and metadata.get("mb_recording_id") and not metadata.get("mb_album_release_group_id"):
        from app.scraper.metadata_resolver import _find_parent_album
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

    # Step A-RG: User-provided release-group URL resolution.
    # When the user supplies a release-group URL instead of a recording URL,
    # look up the release-group directly and browse for releases/recordings.
    if not skip_musicbrainz and not mb_used_ai_ids and _user_mb_rg_id:
        logs.append(f"MusicBrainz: using user-provided release-group ID: {_user_mb_rg_id}")
        try:
            import musicbrainzngs
            from app.scraper.metadata_resolver import (
                _init_musicbrainz, _find_parent_album,
                _find_album_by_artist_browse,
            )
            import time as _time_rg
            _init_musicbrainz()

            rg_data = musicbrainzngs.get_release_group_by_id(
                _user_mb_rg_id,
                includes=["artists", "tags"],
            )
            _time_rg.sleep(1.1)
            rg = rg_data.get("release-group", {})
            if rg:
                _rg_type = (rg.get("primary-type") or rg.get("type") or "").lower()
                if _rg_type not in ("single", "ep"):
                    logs.append(
                        f"MusicBrainz: user-provided release-group type is "
                        f"'{_rg_type or 'unknown'}', not 'single'/'ep' - skipping"
                    )
                    raise ValueError("Release-group is not a single or EP")

                metadata["mb_release_group_id"] = _user_mb_rg_id

                # Extract artist
                _rg_ac = rg.get("artist-credit", [])
                if _rg_ac:
                    ac0 = _rg_ac[0]
                    if isinstance(ac0, dict) and "artist" in ac0:
                        metadata["artist"] = ac0["artist"].get("name", "") or metadata.get("artist", artist)
                        metadata["mb_artist_id"] = ac0["artist"].get("id")

                # Title from release-group
                if rg.get("title"):
                    metadata["title"] = rg["title"]

                # Year from first-release-date
                _frd = rg.get("first-release-date", "")
                if _frd and len(_frd) >= 4 and not metadata.get("year"):
                    try:
                        metadata["year"] = int(_frd[:4])
                    except ValueError:
                        pass

                # Tags/genres
                tags = rg.get("tag-list", [])
                if tags:
                    metadata["genres"] = [
                        capitalize_genre(t["name"])
                        for t in tags
                        if "name" in t and int(t.get("count", 0)) >= 2
                    ]

                # Browse releases for recording ID and cover art
                _rg_rel_id = None
                _rg_rec_id = None
                try:
                    _browse = musicbrainzngs.browse_releases(
                        release_group=_user_mb_rg_id,
                        includes=["recordings"],
                    )
                    _time_rg.sleep(1.1)
                    for _rel in _browse.get("release-list", []):
                        _caa = _rel.get("cover-art-archive", {})
                        if _caa.get("front") in (True, "true") and not _rg_rel_id:
                            _rg_rel_id = _rel.get("id")
                        if not _rg_rec_id:
                            for _med in _rel.get("medium-list", []):
                                for _trk in _med.get("track-list", []):
                                    _rec = _trk.get("recording", {})
                                    if _rec.get("id"):
                                        _rg_rec_id = _rec["id"]
                                        break
                                if _rg_rec_id:
                                    break
                    if not _rg_rel_id:
                        _rlist = _browse.get("release-list", [])
                        if _rlist:
                            _rg_rel_id = _rlist[0].get("id")
                except Exception as e:
                    logs.append(f"MusicBrainz: browse user RG releases failed: {e}")

                if _rg_rel_id:
                    metadata["mb_release_id"] = _rg_rel_id
                if _rg_rec_id:
                    metadata["mb_recording_id"] = _rg_rec_id
                    metadata["_source_urls"]["musicbrainz"] = f"https://musicbrainz.org/recording/{_rg_rec_id}"

                # Find parent album
                _rg_artist_id = metadata.get("mb_artist_id")
                _parent = None
                if _rg_rec_id:
                    _parent = _find_parent_album(_rg_rec_id)
                if not _parent and _rg_artist_id:
                    _search_title = metadata.get("title") or title
                    _parent = _find_album_by_artist_browse(_rg_artist_id, _search_title)
                if _parent:
                    metadata["album"] = _parent["album"]
                    metadata["mb_album_release_id"] = _parent.get("mb_album_release_id")
                    metadata["mb_album_release_group_id"] = _parent.get("mb_album_release_group_id")

                mb_used_ai_ids = True
                metadata["scraper_sources_used"].append("musicbrainz:user_rg")
                logs.append(
                    f"MusicBrainz: resolved via user release-group - "
                    f"{metadata.get('artist', '?')} - {metadata.get('title', '?')} "
                    f"(rg={_user_mb_rg_id}, release={_rg_rel_id})"
                )
        except Exception as e:
            logs.append(f"MusicBrainz: user-provided release-group lookup failed: {e}")

    # Step A.5: AI artist cross-reference fallback.
    # When AI provides musicbrainz_artist_id but no recording/release IDs
    # (or the recording was rejected in Step A), browse the artist's
    # Singles/EPs to find the track directly.
    _ai_artist_id = (ai_result.sources.musicbrainz_artist_id if has_ai else None) or None
    if not skip_musicbrainz and not mb_used_ai_ids and _ai_artist_id and not _user_mb_rec_id:
        logs.append(f"MusicBrainz: AI provided artist ID {_ai_artist_id} Ã¢â‚¬â€ cross-referencing singles")
        try:
            from app.scraper.metadata_resolver import (
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
                # Browse the single's release group for a release with cover art
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
                # Find parent album
                _parent = None
                if _single_rec_id:
                    _parent = _find_parent_album(_single_rec_id)
                if not _parent:
                    _parent = _find_album_by_artist_browse(_ai_artist_id, _search_title)
                if _parent:
                    metadata["album"] = _parent["album"]
                    metadata["mb_album_release_id"] = _parent.get("mb_album_release_id")
                    metadata["mb_album_release_group_id"] = _parent.get("mb_album_release_group_id")
                # Year from first-release-date
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
                logs.append(f"MusicBrainz: AI artist cross-ref Ã¢â‚¬â€ no matching single found")
        except Exception as e:
            logs.append(f"MusicBrainz: AI artist cross-ref failed: {e}")

    if not skip_musicbrainz and not mb_used_ai_ids and not _user_mb_rec_id:
        # Step B: Fall back to search-based MusicBrainz resolution
        try:
            search_artist = artist
            search_title = title
            # When the AI provided a recording ID that was rejected in
            # Step A, the AI's proposed title may be wrong (e.g. an EP
            # name instead of the track name).  Fall back to the
            # original parsed title from the filename/platform so the
            # search doesn't find the wrong release.
            _ai_recording_rejected = (
                has_ai
                and (ai_result.sources.musicbrainz_recording_id or None)
                and not mb_used_ai_ids
            )
            if _ai_recording_rejected and parsed_title:
                search_title = parsed_title
                logs.append(
                    f"MusicBrainz: AI recording was rejected Ã¢â‚¬â€ "
                    f"using parsed title '{parsed_title}' for search "
                    f"instead of AI title '{ai_result.identity.title}'"
                )
            elif has_ai and ai_result.identity.title:
                search_title = ai_result.identity.title
                # When AI simplified the title by dropping a
                # parenthetical subtitle (e.g. "Ya Mama" vs
                # "Ya Mama (Push The Tempo)"), the parsed title
                # provides better disambiguation for MusicBrainz.
                # But first strip any artist-name prefix so that
                # "Paramore: Decode" isn't mistaken for having more
                # disambiguation than "Decode".
                if (parsed_title
                        and parsed_title.lower() != search_title.lower()
                        and search_title.lower() in parsed_title.lower()):
                    _clean_parsed = parsed_title
                    _art_prefix = (ai_result.identity.artist or search_artist or "").strip()
                    if _art_prefix and _clean_parsed.lower().startswith(_art_prefix.lower()):
                        _clean_parsed = _clean_parsed[len(_art_prefix):].lstrip(" :-\u2013\u2014\t").strip()
                    # Strip enclosing quotes (e.g. '"L.G. FUAD"' → 'L.G. FUAD')
                    # so literal quote chars from YouTube titles don't corrupt the MB search.
                    if _clean_parsed and len(_clean_parsed) >= 3:
                        if (_clean_parsed[0] in '"\u201c\u201d' and _clean_parsed[-1] in '"\u201c\u201d'):
                            _clean_parsed = _clean_parsed[1:-1].strip()
                    # Strip featuring credits ("ft. X", "feat. X", "featuring X")
                    # — MusicBrainz stores featured artists in the artist credit,
                    # not the recording title; including them causes lookup failures.
                    if _clean_parsed:
                        _clean_parsed = re.sub(
                            r'\s*\(?\s*(?:feat(?:uring)?\.?|ft\.?)\s+.*$',
                            '', _clean_parsed, flags=re.IGNORECASE,
                        ).strip()
                    if (_clean_parsed
                            and _clean_parsed.lower() != search_title.lower()
                            and search_title.lower() in _clean_parsed.lower()):
                        logs.append(
                            f"MusicBrainz: AI title \"{search_title}\" is shorter "
                            f"than parsed title \"{parsed_title}\" - using cleaned "
                            f"parsed title \"{_clean_parsed}\" for better disambiguation"
                        )
                        search_title = _clean_parsed
                    else:
                        logs.append(
                            f"MusicBrainz: parsed title \"{parsed_title}\" only "
                            f"differs by artist prefix / featuring credit "
                            f"— keeping AI title \"{search_title}\""
                        )
            if has_ai and ai_result.identity.artist:
                search_artist = ai_result.identity.artist

            mb = search_musicbrainz(search_artist, search_title)
            if mb.get("mb_recording_id"):
                # Validate the MB result against the expected identity.
                # Use primary artist (without featured credits) so that
                # "AronChupa featuring Little Sis Nora" still matches "AronChupa".
                from app.scraper.metadata_resolver import _normalize_for_compare, _tokens_overlap
                from app.scraper.source_validation import parse_multi_artist as _pma_mb
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
                            _mb_album = mb["album"]
                            _resolved_title = metadata.get("title", "")
                            _is_dup = _album_is_title_duplicate(_mb_album, _resolved_title)
                            # Even if names match, accept when MB found a
                            # genuinely different release group for the album
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
                                logs.append(f"MusicBrainz: album '{_mb_album}' matches title \u2014 discarded")
                                # Clear stale album IDs so they don't block
                                # later Wikipedia or AI album resolution.
                                metadata["mb_album_release_id"] = None
                                metadata["mb_album_release_group_id"] = None
                            else:
                                if _is_dup and _has_distinct_rg:
                                    logs.append(
                                        f"MusicBrainz: album '{_mb_album}' matches title but has "
                                        f"distinct release group \u2014 accepted as genuine parent album"
                                    )
                                metadata["album"] = _mb_album
                                _mb_album_accepted = True
                        # Always store mb_release_id Ã¢â‚¬â€ it points to the
                        # single's release and is needed for CoverArtArchive
                        # poster lookup regardless of album status.
                        metadata["mb_release_id"] = mb["mb_release_id"]
                        if mb.get("year") and not metadata.get("year"):
                            metadata["year"] = mb["year"]
                        if mb.get("genres"):
                            metadata["genres"] = mb["genres"]
                        metadata["scraper_sources_used"].append("musicbrainz:search")
                        if mb.get("mb_recording_id"):
                            metadata["_source_urls"]["musicbrainz"] = f"https://musicbrainz.org/recording/{mb['mb_recording_id']}"
                        logs.append(f"MusicBrainz: search match Ã¢â‚¬â€ {mb['artist']} - {mb.get('album', '?')}")
                    else:
                        logs.append(
                            f"MusicBrainz: search result '{mb['artist']}' doesn't match "
                            f"expected '{search_artist}' Ã¢â‚¬â€ discarded"
                        )
                else:
                    logs.append("MusicBrainz: no results from search")
        except Exception as e:
            logs.append(f"MusicBrainz: search failed: {e}")

    _mb_resolved = bool(metadata.get("mb_recording_id"))
    logs.append(f"MusicBrainz: resolution complete (resolved={_mb_resolved})")

    # Ã¢â€â‚¬Ã¢â€â‚¬ Wikipedia resolution Ã¢â€â‚¬Ã¢â€â‚¬
    if not skip_wikipedia:
        wiki_used_ai_url = False
        # User-provided Wikipedia URL takes highest priority
        _user_wiki_url = (wikipedia_url.strip() if wikipedia_url and wikipedia_url.strip() else None)

        if _user_wiki_url:
            # Step 0: User-provided Wikipedia URL Ã¢â‚¬â€ use directly, skip validation
            logs.append(f"Wikipedia: using user-provided URL: {_user_wiki_url}")
            try:
                wiki = scrape_wikipedia_page(_user_wiki_url)
                _wiki_has_data = wiki and any(
                    wiki.get(k) for k in ("title", "artist", "plot", "genres")
                )
                if not _wiki_has_data:
                    logs.append("Wikipedia: user-provided URL returned no usable data.")
                elif wiki:
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
                        metadata["_artwork_candidates"].append({"url": wiki["image_url"], "source": "wikipedia", "applied": True})
                    if wiki.get("page_type"):
                        metadata["wiki_page_type"] = wiki["page_type"]
                    metadata["source_url"] = _user_wiki_url
                    metadata["_source_urls"]["wikipedia"] = _user_wiki_url
                    wiki_used_ai_url = True
                    metadata["scraper_sources_used"].append("wikipedia:user_url")
                    logs.append("Wikipedia: scraped via user-provided URL successfully")
            except Exception as e:
                logs.append(f"Wikipedia: user-provided URL scrape failed: {e}")

        elif has_ai and ai_result.sources.wikipedia_url:
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
                            f"Wikipedia: AI-provided URL mismatch Ã¢â‚¬â€ {mismatch}. Rejecting."
                        )
                    else:
                        # Cover song protection: when the infobox artist
                        # differs from the expected artist, the article is
                        # about a song originally by another artist.  The
                        # infobox album/year belong to the original release,
                        # so skip merging those fields.
                        _wiki_artist_raw = (wiki.get("artist") or "").lower().strip()
                        _expected_art_lower = (ai_result.identity.artist or artist or "").lower().strip()
                        _is_cover_article = (
                            _wiki_artist_raw
                            and _expected_art_lower
                            and _wiki_artist_raw != _expected_art_lower
                            and _expected_art_lower not in _wiki_artist_raw
                            and _wiki_artist_raw not in _expected_art_lower
                        )
                        if _is_cover_article:
                            logs.append(
                                f"Wikipedia: cover song detected (infobox artist "
                                f"'{wiki.get('artist')}' vs expected "
                                f"'{ai_result.identity.artist or artist}') "
                                f"\u2014 skipping infobox album/year merge"
                            )
                        if not _is_cover_article:
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
                            metadata["_artwork_candidates"].append({"url": wiki["image_url"], "source": "wikipedia", "applied": True})
                        if wiki.get("page_type"):
                            metadata["wiki_page_type"] = wiki["page_type"]
                        metadata["source_url"] = ai_result.sources.wikipedia_url
                        metadata["_source_urls"]["wikipedia"] = ai_result.sources.wikipedia_url
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
                from app.scraper.source_validation import parse_multi_artist as _pma
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
                        logs.append(f"Wikipedia: search result mismatch Ã¢â‚¬â€ {mismatch}. Discarding.")
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
                            metadata["_artwork_candidates"].append({"url": wiki["image_url"], "source": "wikipedia", "applied": True})
                        if wiki.get("page_type"):
                            metadata["wiki_page_type"] = wiki["page_type"]
                        metadata["source_url"] = wiki_url
                        metadata["_source_urls"]["wikipedia"] = wiki_url
                        metadata["scraper_sources_used"].append("wikipedia:search")
                        logs.append(f"Wikipedia: search match Ã¢â‚¬â€ {wiki_url}")
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
                        _has_distinct_rg = (
                            metadata.get("mb_album_release_group_id")
                            and metadata.get("mb_release_group_id")
                            and metadata["mb_album_release_group_id"] != metadata["mb_release_group_id"]
                        )
                        if _has_distinct_rg:
                            metadata["album"] = _album_name
                            logs.append(
                                f"Wikipedia: album \'{_album_name}\' from infobox link matches title "
                                f"but has distinct release group \u2014 accepted"
                            )
                        else:
                            logs.append(
                                f"Wikipedia: album \'{_album_name}\' from infobox link matches title \u2014 discarded"
                            )
                    else:
                        metadata["album"] = _album_name
                        logs.append(f"Wikipedia: album \'{_album_name}\' resolved from single infobox link")
                else:
                    logs.append("Wikipedia: album page from infobox link had no usable title")
            else:
                logs.append("Wikipedia: no album link found in single page infobox")
        except Exception as e:
            logs.append(f"Wikipedia: album-link fallback failed: {e}")

    # Ã¢â€â‚¬Ã¢â€â‚¬ Cross-fallback: MB Ã¢â€ â€ Wikipedia Ã¢â€â‚¬Ã¢â€â‚¬
    # After both scrapers have run independently, use each other's results
    # to fill gaps Ã¢â‚¬â€ finding album/artist/single pages the other missed.
    _wiki_single_url = metadata.get("_source_urls", {}).get("wikipedia")
    _wiki_has_single = bool(_wiki_single_url)

    if not skip_wikipedia:
        # --- MBÃ¢â€ â€™Wiki: Use MB album to discover Wikipedia album page ---
        # If MB found the album name, search Wikipedia for the album page.
        # Then from the album page, follow tracklist links back to the
        # single page (if Wikipedia search for the single failed).
        _resolved_album = metadata.get("album")
        _resolved_artist = metadata.get("artist") or artist

        if _resolved_album and not _wiki_has_single:
            # MB found an album but Wiki didn't find the single Ã¢â‚¬â€ search
            # Wikipedia for the album page, then extract single from tracklist
            logs.append(f"Cross-fallback: MB found album '{_resolved_album}' Ã¢â‚¬â€ "
                        f"searching Wikipedia for album to discover single link")
            try:
                from app.scraper.metadata_resolver import (
                    search_wikipedia_album, extract_single_wiki_url_from_album,
                )
                _xref_album_url = search_wikipedia_album(_resolved_artist, _resolved_album)

                # AI album fallback: when the album search returned the
                # artist page (common for self-titled albums), try the AI's
                # album name instead.
                _artist_wiki_url = metadata.get("_source_urls", {}).get("wikipedia_artist")
                if (
                    has_ai
                    and ai_result.identity.album
                    and (
                        not _xref_album_url
                        or (_artist_wiki_url and _xref_album_url == _artist_wiki_url)
                    )
                ):
                    _ai_album = ai_result.identity.album
                    if _ai_album.lower().strip() != _resolved_album.lower().strip():
                        logs.append(
                            f"Cross-fallback: AI album '{_ai_album}' differs from "
                            f"MB album '{_resolved_album}' — trying AI album"
                        )
                        _ai_album_url = search_wikipedia_album(_resolved_artist, _ai_album)
                        if _ai_album_url and _ai_album_url != _artist_wiki_url:
                            _xref_album_url = _ai_album_url

                if _xref_album_url:
                    metadata["_source_urls"]["wikipedia_album"] = _xref_album_url
                    logs.append(f"Cross-fallback: found album page Ã¢â€ â€™ {_xref_album_url}")

                    # Try to find the single from the album's tracklist
                    _resolved_title = metadata.get("title") or title
                    _xref_single_url = extract_single_wiki_url_from_album(
                        _xref_album_url, _resolved_title,
                    )
                    if _xref_single_url:
                        metadata["_source_urls"]["wikipedia"] = _xref_single_url
                        metadata["source_url"] = _xref_single_url
                        _wiki_has_single = True
                        logs.append(f"Cross-fallback: single found in album tracklist \u2192 {_xref_single_url}")

                        # Cover song detection: when the tracklist URL
                        # contains a #fragment (e.g. Smooth_Criminal#Alien_Ant_Farm_version),
                        # the page belongs to the original artist.  Detect
                        # this so we can link to the article without
                        # contaminating metadata with the original's details.
                        _is_xref_cover = False
                        if "#" in _xref_single_url:
                            _fragment = _xref_single_url.rsplit("#", 1)[1].replace("_", " ").lower()
                            _ra_lower = _resolved_artist.lower().strip()
                            if _ra_lower in _fragment or _fragment in _ra_lower:
                                _is_xref_cover = True
                                logs.append(
                                    f"Cross-fallback: cover song detected \u2014 URL "
                                    f"fragment '{_fragment}' references artist "
                                    f"'{_resolved_artist}'"
                                )

                        # Scrape the single page for metadata (plot, genres, image)
                        try:
                            _xref_wiki = scrape_wikipedia_page(
                                _xref_single_url,
                                expected_artist=_resolved_artist,
                            )
                            if _xref_wiki:
                                # Secondary cover detection via infobox artist
                                if not _is_xref_cover:
                                    _xref_infobox_artist = (_xref_wiki.get("artist") or "").lower().strip()
                                    _ra_lower2 = _resolved_artist.lower().strip()
                                    if (_xref_infobox_artist
                                            and _ra_lower2
                                            and _xref_infobox_artist != _ra_lower2
                                            and _ra_lower2 not in _xref_infobox_artist
                                            and _xref_infobox_artist not in _ra_lower2):
                                        _is_xref_cover = True
                                        logs.append(
                                            f"Cross-fallback: cover song detected \u2014 "
                                            f"infobox artist '{_xref_wiki.get('artist')}' "
                                            f"doesn't match '{_resolved_artist}'"
                                        )

                                if _is_xref_cover:
                                    # Cover song: skip ALL metadata (plot,
                                    # year, genres, image_url) to prevent
                                    # contamination from the original artist.
                                    # The URL is still stored so the wiki
                                    # article can be linked for reference.
                                    metadata["scraper_sources_used"].append("wikipedia:mb_xref_cover")
                                    if not metadata.get("source_urls"):
                                        metadata["source_urls"] = {}
                                    metadata["source_urls"]["wikipedia_cover_ref"] = _xref_single_url
                                    logs.append(
                                        "Cross-fallback: cover song \u2014 skipped all metadata "
                                        "to prevent contamination, stored URL for reference"
                                    )
                                else:
                                    if _xref_wiki.get("plot") and not metadata.get("plot"):
                                        metadata["plot"] = _xref_wiki["plot"]
                                    if _xref_wiki.get("genres") and not metadata.get("genres"):
                                        metadata["genres"] = _xref_wiki["genres"]
                                    if _xref_wiki.get("year") and not metadata.get("year"):
                                        metadata["year"] = _xref_wiki["year"]
                                    if _xref_wiki.get("image_url"):
                                        metadata["image_url"] = _xref_wiki["image_url"]
                                        metadata["_artwork_candidates"].append({
                                            "url": _xref_wiki["image_url"],
                                            "source": "wikipedia",
                                            "applied": True,
                                        })
                                    if _xref_wiki.get("page_type"):
                                        metadata["wiki_page_type"] = _xref_wiki["page_type"]
                                    metadata["scraper_sources_used"].append("wikipedia:mb_xref")
                                    logs.append("Cross-fallback: scraped single page via album tracklist")
                        except Exception as e:
                            logs.append(f"Cross-fallback: single page scrape failed: {e}")
                    else:
                        logs.append("Cross-fallback: single not found in album tracklist")
                else:
                    logs.append("Cross-fallback: album Wikipedia page not found")
            except Exception as e:
                logs.append(f"Cross-fallback: MBÃ¢â€ â€™Wiki album search failed: {e}")

        # --- WikiÃ¢â€ â€™MB: Use Wiki data to re-search MusicBrainz ---
        # If Wikipedia confirmed the artist/title but MB search found nothing,
        # retry MB with the wiki-confirmed identity (handles cases where the
        # initial parsed artist/title was too different from the real one).
        if not skip_musicbrainz and not metadata.get("mb_recording_id"):
            _wiki_artist = metadata.get("artist") or artist
            _wiki_title = metadata.get("title") or title
            # Only retry if Wikipedia actually provided useful identity data
            _wiki_contributed = any(
                s.startswith("wikipedia:") for s in metadata.get("scraper_sources_used", [])
            )
            if _wiki_contributed and (_wiki_artist != artist or _wiki_title != title):
                logs.append(f"Cross-fallback: Wiki confirmed identity '{_wiki_artist} - {_wiki_title}' Ã¢â‚¬â€ "
                            f"retrying MusicBrainz search")
                try:
                    _xref_mb = search_musicbrainz(_wiki_artist, _wiki_title)
                    if _xref_mb and _xref_mb.get("mb_recording_id"):
                        from app.scraper.metadata_resolver import (
                            _normalize_for_compare, _tokens_overlap,
                        )
                        from app.scraper.source_validation import parse_multi_artist as _pma_xref
                        _xref_primary, _ = _pma_xref(_wiki_artist)
                        _xref_mb_artist = _xref_mb.get("artist", "")
                        _xref_ok = (
                            _normalize_for_compare(_xref_mb_artist) == _normalize_for_compare(_wiki_artist)
                            or _tokens_overlap(_xref_mb_artist, _wiki_artist, 0.4)
                            or (_xref_primary and _tokens_overlap(_xref_mb_artist, _xref_primary, 0.4))
                        )
                        if _xref_ok:
                            for _k in ("mb_artist_id", "mb_recording_id", "mb_release_id",
                                       "mb_release_group_id", "mb_album_release_id",
                                       "mb_album_release_group_id"):
                                if _xref_mb.get(_k):
                                    metadata[_k] = _xref_mb[_k]
                            if _xref_mb.get("album") and not metadata.get("album"):
                                if not _album_is_title_duplicate(_xref_mb["album"], _wiki_title):
                                    metadata["album"] = _xref_mb["album"]
                                    _resolved_album = _xref_mb["album"]
                            if _xref_mb.get("year") and not metadata.get("year"):
                                metadata["year"] = _xref_mb["year"]
                            if _xref_mb.get("genres") and not metadata.get("genres"):
                                metadata["genres"] = _xref_mb["genres"]
                            if _xref_mb.get("mb_recording_id"):
                                metadata["_source_urls"]["musicbrainz"] = (
                                    f"https://musicbrainz.org/recording/{_xref_mb['mb_recording_id']}"
                                )
                            metadata["scraper_sources_used"].append("musicbrainz:wiki_xref")
                            logs.append(f"Cross-fallback: MB re-search found "
                                        f"'{_xref_mb_artist} - {_xref_mb.get('album', '?')}'")
                        else:
                            logs.append(f"Cross-fallback: MB re-search result "
                                        f"'{_xref_mb_artist}' doesn't match "
                                        f"'{_wiki_artist}' Ã¢â‚¬â€ discarded")
                except Exception as e:
                    logs.append(f"Cross-fallback: WikiÃ¢â€ â€™MB search failed: {e}")

        # --- Wikipedia album + artist page discovery ---
        # After all fallbacks, discover Wikipedia album and artist pages
        # using cross-links from the single page and independent search.
        _resolved_artist = metadata.get("artist") or artist
        _resolved_album = metadata.get("album")
        _resolved_title = metadata.get("title") or title
        _wiki_single_url = metadata.get("_source_urls", {}).get("wikipedia")

        # Step 1: Extract cross-links from the single page's infobox
        if _wiki_single_url and "wikipedia_album" not in metadata.get("_source_urls", {}):
            try:
                from app.scraper.metadata_resolver import extract_wiki_infobox_links
                _xlinks = extract_wiki_infobox_links(_wiki_single_url)

                # Validate artist cross-link against resolved artist.
                # Cover song protection: the first infobox on a song page
                # may link to the original artist (e.g. Keith Whitley)
                # instead of the covering artist (e.g. Ronan Keating).
                # When the link target doesn't match, discard BOTH
                # artist and album cross-links (the album also belongs
                # to the original artist) and fall through to search.
                _xlink_ok = True
                if _xlinks.get("artist_url") and _resolved_artist:
                    _link_page = _url_unquote(
                        _xlinks["artist_url"].rsplit("/wiki/", 1)[-1]
                    ).replace("_", " ").strip()
                    _lp = _link_page.lower()
                    _ra = _resolved_artist.lower().strip()
                    if not (_lp == _ra or _lp in _ra or _ra in _lp):
                        # Tier 1: alpha-only prefix check for band renames
                        # e.g. "The Jackson 5" / "The Jacksons" → "thejackson" / "thejacksons"
                        _lp_alpha = re.sub(r'[^a-z]', '', _lp)
                        _ra_alpha = re.sub(r'[^a-z]', '', _ra)
                        _shorter = min(len(_lp_alpha), len(_ra_alpha))
                        _longer = max(len(_lp_alpha), len(_ra_alpha))
                        if (_shorter >= 6
                                and _longer > 0
                                and _shorter / _longer >= 0.8
                                and (_lp_alpha.startswith(_ra_alpha)
                                     or _ra_alpha.startswith(_lp_alpha))):
                            logs.append(
                                f"Wikipedia cross-link: name prefix match "
                                f"accepted ('{_link_page}' ≈ '{_resolved_artist}')")
                        else:
                            # Tier 2: check MusicBrainz aliases (authoritative)
                            _mb_aid = metadata.get("mb_artist_id")
                            _alias_matched = False
                            if _mb_aid:
                                try:
                                    import musicbrainzngs
                                    import time as _time_xlink
                                    _ai = musicbrainzngs.get_artist_by_id(
                                        _mb_aid, includes=["aliases"]
                                    )
                                    _time_xlink.sleep(1.1)
                                    _known = {_ai["artist"]["name"].lower().strip()}
                                    for _al in _ai["artist"].get("alias-list", []):
                                        if _al.get("alias"):
                                            _known.add(_al["alias"].lower().strip())
                                    if _lp in _known:
                                        _alias_matched = True
                                        logs.append(
                                            f"Wikipedia cross-link: '{_link_page}' "
                                            f"verified as MB alias of '{_resolved_artist}'")
                                except Exception:
                                    pass
                            if not _alias_matched:
                                _xlink_ok = False
                                logs.append(
                                    f"Wikipedia cross-link: infobox artist "
                                    f"'{ _link_page}' doesn't match resolved "
                                    f"'{ _resolved_artist}' (cover song?) "
                                    f"\u2014 discarding infobox cross-links"
                                )

                if _xlink_ok:
                    if _xlinks.get("album_url"):
                        metadata["_source_urls"]["wikipedia_album"] = _xlinks["album_url"]
                        logs.append(f"Wikipedia cross-link: album -> {_xlinks['album_url']}")
                    if _xlinks.get("artist_url"):
                        metadata["_source_urls"]["wikipedia_artist"] = _xlinks["artist_url"]
                        logs.append(f"Wikipedia cross-link: artist -> {_xlinks['artist_url']}")
            except Exception as e:
                logs.append(f"Wikipedia cross-link extraction failed: {e}")

        # Step 2: If no album page found via cross-link, search Wikipedia
        _album_search_term = _resolved_album
        # Title-track fallback: when album name was discarded as a
        # title-duplicate but MB confirmed an album release group exists,
        # the song is likely a title track.  Use the song title as the
        # album search term so the Wikipedia album page is still found.
        if not _album_search_term and metadata.get("mb_album_release_group_id"):
            _album_search_term = _resolved_title
            logs.append(
                f"Wikipedia album search: album is None but MB album RG "
                f"exists — using title '{_resolved_title}' as album search term "
                f"(title-track)"
            )
        if _album_search_term and "wikipedia_album" not in metadata.get("_source_urls", {}):
            try:
                from app.scraper.metadata_resolver import search_wikipedia_album
                _album_url = search_wikipedia_album(_resolved_artist, _album_search_term)
                if _album_url:
                    metadata["_source_urls"]["wikipedia_album"] = _album_url
                    logs.append(f"Wikipedia album search: {_album_url}")
            except Exception as e:
                logs.append(f"Wikipedia album search failed: {e}")

        # Step 3: If no artist page found via cross-link, search Wikipedia
        if "wikipedia_artist" not in metadata.get("_source_urls", {}):
            try:
                from app.scraper.metadata_resolver import search_wikipedia_artist
                _artist_url = search_wikipedia_artist(_resolved_artist)
                if _artist_url:
                    metadata["_source_urls"]["wikipedia_artist"] = _artist_url
                    logs.append(f"Wikipedia artist search: {_artist_url}")
            except Exception as e:
                logs.append(f"Wikipedia artist search failed: {e}")

        # Step 3b: MB→Wikidata→Wikipedia fallback for artist page
        if "wikipedia_artist" not in metadata.get("_source_urls", {}) and metadata.get("mb_artist_id"):
            try:
                from app.scraper.metadata_resolver import resolve_artist_wikipedia_via_mb
                _artist_url = resolve_artist_wikipedia_via_mb(metadata["mb_artist_id"])
                if _artist_url:
                    metadata["_source_urls"]["wikipedia_artist"] = _artist_url
                    logs.append(f"Wikipedia artist via MB→Wikidata: {_artist_url}")
            except Exception as e:
                logs.append(f"Wikipedia artist MB→Wikidata fallback failed: {e}")

        # Step 4: Scrape album page for cover artwork
        _wiki_album_url = metadata.get("_source_urls", {}).get("wikipedia_album")
        if _wiki_album_url:
            try:
                _album_wiki = scrape_wikipedia_page(_wiki_album_url)
                if _album_wiki and _album_wiki.get("image_url"):
                    _existing = {c["url"] for c in metadata.get("_artwork_candidates", [])}
                    if _album_wiki["image_url"] not in _existing:
                        metadata["_artwork_candidates"].append({
                            "url": _album_wiki["image_url"],
                            "source": "wikipedia_album",
                            "art_type": "album",
                            "applied": False,
                        })
                        logs.append(f"Wikipedia album art: {_album_wiki['image_url']}")
            except Exception as e:
                logs.append(f"Wikipedia album art scrape failed: {e}")

        # Step 5: Scrape artist page for portrait artwork
        _wiki_artist_url = metadata.get("_source_urls", {}).get("wikipedia_artist")
        if _wiki_artist_url:
            try:
                _artist_wiki = scrape_wikipedia_page(_wiki_artist_url)
                if _artist_wiki and _artist_wiki.get("image_url"):
                    _existing = {c["url"] for c in metadata.get("_artwork_candidates", [])}
                    if _artist_wiki["image_url"] not in _existing:
                        metadata["_artwork_candidates"].append({
                            "url": _artist_wiki["image_url"],
                            "source": "wikipedia_artist",
                            "art_type": "artist",
                            "applied": False,
                        })
                        logs.append(f"Wikipedia artist art: {_artist_wiki['image_url']}")
            except Exception as e:
                logs.append(f"Wikipedia artist art scrape failed: {e}")

    # Ã¢â€â‚¬Ã¢â€â‚¬ IMDB resolution Ã¢â€â‚¬Ã¢â€â‚¬
    imdb_used_ai_url = False
    if has_ai and ai_result.sources.imdb_url:
        logs.append(f"IMDB: using AI-provided URL: {ai_result.sources.imdb_url}")
        metadata["imdb_url"] = ai_result.sources.imdb_url
        metadata["_source_urls"]["imdb"] = ai_result.sources.imdb_url
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
                metadata["_source_urls"]["imdb"] = imdb_url
                metadata["scraper_sources_used"].append("imdb:search")
                logs.append(f"IMDB: search match Ã¢â‚¬â€ {imdb_url}")
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
                logs.append(f"AI album '{ai_result.identity.album}' matches title Ã¢â‚¬â€ discarded")
            else:
                metadata["album"] = ai_result.identity.album


    # --- Album RG name-match fallback ---
    # When the album name is known (from MB, Wikipedia, or AI) but
    # mb_album_release_group_id was not resolved (common when the track
    # lives on an EP and _find_parent_album only checks Album-type
    # releases), search the artist's release groups by name.
    if (not skip_musicbrainz
            and metadata.get("album")
            and metadata.get("mb_artist_id")
            and not metadata.get("mb_album_release_group_id")):
        _album_for_rg = metadata["album"]
        logs.append(
            f"Album RG fallback: album '{_album_for_rg}' has no "
            f"mb_album_release_group_id \u2014 searching by name"
        )
        try:
            from app.scraper.metadata_resolver import _find_release_group_by_name
            _rg_result = _find_release_group_by_name(
                metadata["mb_artist_id"], _album_for_rg
            )
            if _rg_result:
                metadata["mb_album_release_group_id"] = _rg_result["mb_album_release_group_id"]
                if _rg_result.get("mb_album_release_id"):
                    metadata["mb_album_release_id"] = _rg_result["mb_album_release_id"]
                if _rg_result.get("album"):
                    metadata["album"] = _rg_result["album"]
                logs.append(
                    f"Album RG fallback: found '{_rg_result['album']}' "
                    f"(rg={_rg_result['mb_album_release_group_id']})"
                )
            else:
                logs.append("Album RG fallback: no matching release group found")
        except Exception as e:
            logs.append(f"Album RG fallback failed: {e}")

    # Late-stage Wikipedia album search: if album was resolved late
    # (AI identity fallback or Album RG name-match) and wikipedia_album
    # is still missing, search now.
    if (not skip_wikipedia
            and metadata.get("album")
            and "wikipedia_album" not in metadata.get("_source_urls", {})):
        try:
            from app.scraper.metadata_resolver import search_wikipedia_album
            _late_album_url = search_wikipedia_album(
                metadata.get("artist") or artist,
                metadata["album"],
            )
            if _late_album_url:
                metadata["_source_urls"]["wikipedia_album"] = _late_album_url
                logs.append(f"Wikipedia album search (late): {_late_album_url}")
                try:
                    _album_wiki = scrape_wikipedia_page(_late_album_url)
                    if _album_wiki and _album_wiki.get("image_url"):
                        _existing = {c["url"] for c in metadata.get("_artwork_candidates", [])}
                        if _album_wiki["image_url"] not in _existing:
                            metadata["_artwork_candidates"].append({
                                "url": _album_wiki["image_url"],
                                "source": "wikipedia_album",
                                "art_type": "album",
                                "applied": False,
                            })
                            logs.append(f"Wikipedia album art: {_album_wiki['image_url']}")
                except Exception as e:
                    logs.append(f"Wikipedia album art scrape failed: {e}")
        except Exception as e:
            logs.append(f"Wikipedia album search (late) failed: {e}")
    # Ã¢â€â‚¬Ã¢â€â‚¬ Album sanitization: strip "Title - Single" patterns Ã¢â€â‚¬Ã¢â€â‚¬
    from app.scraper.source_validation import sanitize_album
    if metadata.get("album"):
        _orig_album = metadata["album"]
        metadata["album"] = sanitize_album(
            metadata["album"],
            title=metadata.get("title") or "",
        )
        if metadata["album"] != _orig_album:
            logs.append(f"Album sanitized: '{_orig_album}' Ã¢â€ â€™ {metadata['album'] or 'null'}")

    # Ã¢â€â‚¬Ã¢â€â‚¬ Multi-artist parsing Ã¢â€â‚¬Ã¢â€â‚¬
    from app.scraper.source_validation import parse_multi_artist
    if metadata.get("artist"):
        primary, featured = parse_multi_artist(metadata["artist"])
        metadata["primary_artist"] = primary
        metadata["featured_artists"] = featured
    else:
        metadata["primary_artist"] = metadata.get("artist", "")
        metadata["featured_artists"] = []

    # Ã¢â€â‚¬Ã¢â€â‚¬ Wikipedia page type classification Ã¢â€â‚¬Ã¢â€â‚¬
    # Propagate page_type from scraper if available
    # (used by tasks.py to assign correct source_type)

    return metadata, logs


# ---------------------------------------------------------------------------
# Unified metadata resolution Ã¢â‚¬â€ the ONE entry point
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
    wikipedia_url: Optional[str] = None,
    musicbrainz_url: Optional[str] = None,
    log_callback=None,
    _test_mode: bool = False,
) -> Dict[str, Any]:
    """
    Unified metadata resolution using the source-guided pipeline.

    This function is the SINGLE entry point for ALL metadata resolution:
    - Automatic import pipeline
    - Manual "Analyze Metadata"
    - Manual "Scrape Metadata"
    - Any AI-assisted metadata tool

    Pipeline:
    1. AI Source Resolution (if enabled) Ã¢â‚¬â€ determine identity and source links
    2. Scraper fetch Ã¢â‚¬â€ use AI-provided links first, fall back to search
    3. Scraper validation Ã¢â‚¬â€ verify results match expected identity
    4. AI Final Review (if enabled) Ã¢â‚¬â€ verify and correct scraped metadata
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

    pipeline_log: List[str] = []  # Tracks pipeline stage tags for tester UI
    pipeline_failures: List[Dict[str, str]] = []

    def _log(msg: str):
        logger.info(msg)
        pipeline_log.append(msg)
        if log_callback:
            log_callback(msg)

    # Check AI settings
    ai_enabled = not skip_ai
    ai_source_resolution_enabled = False
    ai_final_review_enabled = False
    _ai_no_provider = False  # Track if AI was requested but no provider configured

    if ai_enabled and db:
        ai_provider = _get_setting_str(db, "ai_provider", "none")
        if ai_provider == "none":
            _ai_no_provider = True
            ai_enabled = False
        else:
            ai_source_resolution_enabled = True
            ai_final_review_enabled = True
    elif not ai_enabled:
        _log(f"AI: skipped (mode does not use AI, skip_ai={skip_ai})")
    elif not db:
        _log("AI: skipped (no database session)")
    ai_source_result: Optional[SourceResolutionResult] = None
    _original_parsed_artist = artist  # Preserve before AI override
    _original_parsed_title = title  # Preserve before AI override

    # Ã¢â€â‚¬Ã¢â€â‚¬ Stage 1: AI Source Resolution Ã¢â€â‚¬Ã¢â€â‚¬
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
                # P2: Validate AI identity against parsed values
                _ai_valid, _ai_conf_mult, _ai_reason = _validate_ai_identity(
                    _original_parsed_artist, _original_parsed_title,
                    ai_source_result.identity.artist,
                    ai_source_result.identity.title,
                )
                if not _ai_valid:
                    # Total hallucination — reject AI identity, keep parsed values
                    _log(f"AI Source Resolution REJECTED: {_ai_reason}")
                    pipeline_log.append(f"ai_identity_rejected:{_ai_reason}")
                    ai_source_result.confidence.identity = 0.0
                elif _ai_conf_mult < 1.0:
                    # Partial mismatch — halve confidence
                    _log(f"AI Source Resolution confidence reduced: {_ai_reason}")
                    pipeline_log.append(f"ai_identity_reduced:{_ai_reason}")
                    ai_source_result.confidence.identity *= _ai_conf_mult
                    if ai_source_result.identity.artist:
                        _log(f"Using AI-resolved artist (reduced conf): {ai_source_result.identity.artist}")
                        artist = ai_source_result.identity.artist
                    if ai_source_result.identity.title:
                        _log(f"Using AI-resolved title (reduced conf): {ai_source_result.identity.title}")
                        title = ai_source_result.identity.title
                else:
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
            _log(f"AI source resolution: failed Ã¢â‚¬â€ {ai_source_result.error}")
            pipeline_failures.append({
                "code": "AI_SOURCE_FAILED",
                "description": f"AI source resolution failed Ã¢â‚¬â€ {ai_source_result.error}",
            })
    else:
        if _ai_no_provider:
            pipeline_log.append("stage:ai_source_resolution:disabled:no_provider")
            _log("AI source resolution: SKIPPED Ã¢â‚¬â€ no AI provider configured in Settings. Set ai_provider to gemini/openai/claude/local to enable AI.")
            pipeline_failures.append({
                "code": "AI_NO_PROVIDER",
                "description": "AI was requested but no AI provider is configured. Go to Settings Ã¢â€ â€™ AI Provider and select gemini, openai, claude, or local.",
            })
        elif skip_ai:
            pipeline_log.append("stage:ai_source_resolution:disabled")
            _log("AI source resolution: disabled (mode does not use AI)")
        else:
            pipeline_log.append("stage:ai_source_resolution:disabled")
            _log("AI source resolution: disabled")

    # Ã¢â€â‚¬Ã¢â€â‚¬ Stage 2: Scraper Fetch (using AI-provided links first) Ã¢â€â‚¬Ã¢â€â‚¬
    _log("Running source-guided scraper fetch...")
    pipeline_log.append("stage:scraper_fetch:started")

    # Preserve the original parsed title before AI override so that
    # _scrape_with_ai_links can fall back to it when the AI recording
    # is rejected (the AI's proposed title may be wrong).
    _parsed_title = _original_parsed_title

    metadata, scraper_logs = _scrape_with_ai_links(
        artist=artist,
        title=title,
        ai_result=ai_source_result,
        ytdlp_metadata=ytdlp_metadata,
        skip_wikipedia=skip_wikipedia,
        skip_musicbrainz=skip_musicbrainz,
        wikipedia_url=wikipedia_url,
        musicbrainz_url=musicbrainz_url,
        log_callback=log_callback,
        parsed_title=_parsed_title,
    )

    for sl in scraper_logs:
        _log(f"  Scraper: {sl}")
        pipeline_log.append(f"scraper:{sl}")

    pipeline_log.append("stage:scraper_fetch:complete")

    # Ã¢â€â‚¬Ã¢â€â‚¬ Test mode: capture pre-AI-review snapshot Ã¢â€â‚¬Ã¢â€â‚¬
    _SNAPSHOT_FIELDS = ("artist", "title", "album", "year", "genres", "plot", "image_url")
    if _test_mode:
        import copy
        metadata["_pre_ai_snapshot"] = {k: copy.deepcopy(metadata.get(k)) for k in _SNAPSHOT_FIELDS}

    # Track scraper failures Ã¢â‚¬â€ if a source was attempted but produced no data
    _sources_used = metadata.get("scraper_sources_used", [])
    if not skip_wikipedia and not any(s.startswith("wikipedia:") for s in _sources_used):
        pipeline_failures.append({
            "code": "WIKI_SCRAPE_FAILED",
            "description": "Wikipedia scraping failed Ã¢â‚¬â€ plot and genre data unavailable",
        })
    if not skip_musicbrainz and not any(s.startswith("musicbrainz:") for s in _sources_used):
        pipeline_failures.append({
            "code": "MB_LOOKUP_FAILED",
            "description": "MusicBrainz lookup failed Ã¢â‚¬â€ album, year, and MB IDs unavailable",
        })

    # Ã¢â€â‚¬Ã¢â€â‚¬ Stage 3: Scraper Validation Ã¢â€â‚¬Ã¢â€â‚¬
    # Scrapers already validate via detect_article_mismatch inside
    # _scrape_with_ai_links. Additional validation happens here.
    pipeline_log.append("stage:validation:complete")

    # Ã¢â€â‚¬Ã¢â€â‚¬ Stage 4: AI Final Review Ã¢â€â‚¬Ã¢â€â‚¬
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

                # P3: Prevent Final Review from overriding high-confidence
                # Source Resolution identity fields
                if field_name in ("artist", "title") and ai_source_result and not ai_source_result.error:
                    sr_conf = ai_source_result.confidence.identity
                    if sr_conf >= 0.85:
                        sr_val = (
                            ai_source_result.identity.artist
                            if field_name == "artist"
                            else ai_source_result.identity.title
                        )
                        if sr_val and str(proposed) != str(sr_val):
                            sim = SequenceMatcher(
                                None,
                                str(sr_val).lower().strip(),
                                str(proposed).lower().strip(),
                            ).ratio()
                            has_mb = metadata.get("mb_artist_id") or metadata.get("mb_recording_id")
                            if sim < 0.5 and not has_mb:
                                _log(
                                    f"AI Final Review: blocked {field_name} override "
                                    f"'{sr_val}' → '{proposed}' "
                                    f"(Source Resolution conf={sr_conf:.2f}, sim={sim:.2f})"
                                )
                                pipeline_log.append(
                                    f"ai_review_blocked:{field_name}:{sr_val}->{proposed}"
                                )
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
                        _log(f"AI Final Review: album '{proposed}' matches title \u2014 discarded")
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
                        _log(f"AI Final Review: album '{proposed}' is a sentinel Ã¢â‚¬â€ discarded")
                        continue

                # Run full album sanitization on AI-proposed album Ã¢â‚¬â€ catches
                # storefront "Title - Single" labels the AI hallucinated
                if field_name == "album":
                    from app.scraper.source_validation import sanitize_album as _sanitize_ai_album
                    _sanitized_proposed = _sanitize_ai_album(
                        str(proposed), title=str(metadata.get("title", ""))
                    )
                    if _sanitized_proposed is None:
                        _log(f"AI Final Review: album '{proposed}' sanitized to null "
                             f"(storefront single label) Ã¢â‚¬â€ discarded")
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
                        _log(f"AI Final Review: plot rejected Ã¢â‚¬â€ contains {_url_count} URLs"
                             f"{' and social media links' if _has_social else ''}"
                             f" (looks like YouTube description)")
                        continue

                current = metadata.get(field_name)
                if str(proposed) != str(current):
                    metadata[field_name] = proposed
                    changes_applied.append(
                        f"{field_name}: '{current}' Ã¢â€ â€™ '{proposed}' (conf={field_conf:.2f})"
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
                    f"{_rm_field}: '{_rm_current}' \u2192 removed ({_rm_reason})"
                )

            # Handle artwork rejection Ã¢â‚¬â€ only clear image_url when the AI
            # actually reviewed artwork and rejected it.  When the rejection
            # reason says "no artwork provided" the AI never saw an image, so
            # the scraper-sourced image_url (e.g. Wikipedia single cover)
            # should be preserved.
            if not ai_review_result.artwork_approved:
                _reason = ai_review_result.artwork_rejection_reason or ""
                _no_art_phrases = ("no artwork provided", "no artwork")
                _ai_had_no_artwork = any(p in _reason.lower() for p in _no_art_phrases)
                if _ai_had_no_artwork and metadata.get("image_url"):
                    _log(f"AI Final Review: no artwork sent to AI Ã¢â‚¬â€ preserving scraper image_url")
                else:
                    _log(f"AI Final Review rejected artwork: {_reason}")
                    metadata["image_url"] = None  # Clear to use placeholder
                pipeline_log.append(
                    f"ai_review_artwork_rejected:{_reason}"
                )
        elif ai_review_result and ai_review_result.error:
            pipeline_log.append(f"stage:ai_final_review:failed:{ai_review_result.error[:80]}")
            _log(f"AI final review: failed Ã¢â‚¬â€ {ai_review_result.error}")
            metadata["ai_final_review_failed"] = True
            pipeline_failures.append({
                "code": "AI_REVIEW_FAILED",
                "description": f"AI final review failed Ã¢â‚¬â€ {ai_review_result.error}",
            })
        else:
            pipeline_log.append("stage:ai_final_review:skipped_or_failed")
            _log("AI final review: no result (provider unavailable or failed)")
            metadata["ai_final_review_failed"] = True
            pipeline_failures.append({
                "code": "AI_REVIEW_FAILED",
                "description": "AI final review failed Ã¢â‚¬â€ metadata not validated",
            })
    else:
        if _ai_no_provider:
            pipeline_log.append("stage:ai_final_review:disabled:no_provider")
            _log("AI final review: SKIPPED Ã¢â‚¬â€ no AI provider configured in Settings.")
        elif skip_ai:
            pipeline_log.append("stage:ai_final_review:disabled")
            _log("AI final review: disabled (mode does not use AI)")
        else:
            pipeline_log.append("stage:ai_final_review:disabled")

    # -- Normalize featuring credits (P5) --
    from app.ai.response_parser import normalize_featuring as _norm_feat
    if metadata.get("artist"):
        _normed = _norm_feat(metadata["artist"])
        if _normed != metadata["artist"]:
            _log(f"Normalized featuring: '{metadata['artist']}' -> '{_normed}'")
            metadata["artist"] = _normed


    # Ã¢â€â‚¬Ã¢â€â‚¬ Re-sync primary_artist after AI Final Review Ã¢â€â‚¬Ã¢â€â‚¬
    # primary_artist is set inside _scrape_with_ai_links() BEFORE the review
    # runs, so if the review changed metadata["artist"] the primary_artist
    # field is stale.  Downstream consumers (artwork pipeline, Wikipedia
    # artist source recording) rely on primary_artist Ã¢â‚¬â€ a stale value causes
    # false positives (e.g. fetching artwork for the sampled artist instead
    # of the actual artist).
    from app.scraper.source_validation import parse_multi_artist as _pma_resync
    if metadata.get("artist"):
        _resynced_primary, _resynced_featured = _pma_resync(metadata["artist"])
        if metadata.get("primary_artist") != _resynced_primary:
            _log(f"Re-synced primary_artist: '{metadata.get('primary_artist')}' Ã¢â€ â€™ '{_resynced_primary}'")
            metadata["primary_artist"] = _resynced_primary
            metadata["featured_artists"] = _resynced_featured

    # Ã¢â€â‚¬Ã¢â€â‚¬ Invalidate album when AI changed the artist identity Ã¢â€â‚¬Ã¢â€â‚¬
    # When the AI Final Review changed the artist (e.g. from "4 Non Blondes"
    # to "slackcircus"), any album that was resolved under the OLD artist
    # identity is almost certainly wrong.  Only clear it when there's no
    # authoritative MusicBrainz confirmation Ã¢â‚¬â€ MB-confirmed albums survive.
    # We detect artist change by checking if the review proposed a different
    # artist than the pre-review value.
    #
    # IMPORTANT: Normalise featuring/collaboration separators before comparing.
    # "A Great Big World featuring Christina Aguilera" Ã¢â€ â€™ "A Great Big World & Christina Aguilera"
    # is NOT an identity change Ã¢â‚¬â€ it's a formatting change.  Only trigger when the
    # underlying set of artists actually differs.
    _artist_changed_by_review = False
    if (ai_review_result
            and ai_review_result.artist is not None
            and ai_source_result is not None
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
                _log(f"Artist identity changed by review: '{_pre_review_artist}' Ã¢â€ â€™ '{_post_review_artist}'")
            else:
                _log(f"Artist formatting changed by review (not an identity change): "
                     f"'{_pre_review_artist}' Ã¢â€ â€™ '{_post_review_artist}'")

    if _artist_changed_by_review:
        # Ã¢â€â‚¬Ã¢â€â‚¬ Clear album if not MB-confirmed Ã¢â€â‚¬Ã¢â€â‚¬
        if metadata.get("album") and not metadata.get("mb_album_release_group_id"):
            _stale_album = metadata["album"]
            metadata["album"] = None
            _log(f"Album '{_stale_album}' cleared Ã¢â‚¬â€ artist identity changed by AI review "
                 f"and no MusicBrainz album confirmation exists")
            pipeline_log.append(f"album_cleared_after_artist_change:{_stale_album}")

        # Ã¢â€â‚¬Ã¢â€â‚¬ Clear IMDB URL found via search under the old identity Ã¢â€â‚¬Ã¢â€â‚¬
        _imdb_from_search = metadata.get("imdb_url") and any(
            s == "imdb:search" for s in metadata.get("scraper_sources_used", [])
        )
        if _imdb_from_search:
            _stale_imdb = metadata["imdb_url"]
            metadata["imdb_url"] = None
            _log(f"IMDB URL '{_stale_imdb}' cleared Ã¢â‚¬â€ found via search under old artist identity")
            pipeline_log.append(f"imdb_cleared_after_artist_change:{_stale_imdb}")

        # Ã¢â€â‚¬Ã¢â€â‚¬ Clear MusicBrainz IDs resolved under the wrong identity Ã¢â€â‚¬Ã¢â€â‚¬
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

        # Ã¢â€â‚¬Ã¢â€â‚¬ Clear Wikipedia source URL resolved under wrong identity Ã¢â€â‚¬Ã¢â€â‚¬
        if metadata.get("source_url") and "wikipedia.org" in metadata.get("source_url", ""):
            _stale_wiki = metadata["source_url"]
            metadata["source_url"] = None
            _log(f"Wikipedia source URL '{_stale_wiki}' cleared Ã¢â‚¬â€ resolved under old identity")
            pipeline_log.append(f"wiki_source_cleared_after_artist_change:{_stale_wiki}")

            # Re-resolve the Wikipedia single/song URL under the corrected identity
            try:
                from app.scraper.metadata_resolver import search_wikipedia as _search_wiki_song
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

    # Ã¢â€â‚¬Ã¢â€â‚¬ Final album sanitization (catches AI-introduced sentinels like "Unknown") Ã¢â€â‚¬Ã¢â€â‚¬
    from app.scraper.source_validation import sanitize_album as _sanitize_album_final
    if metadata.get("album"):
        _pre_final = metadata["album"]
        metadata["album"] = _sanitize_album_final(
            metadata["album"],
            title=metadata.get("title") or "",
        )
        if metadata["album"] != _pre_final:
            _log(f"Post-review album sanitized: '{_pre_final}' Ã¢â€ â€™ {metadata['album'] or 'null'}")

    # Ã¢â€â‚¬Ã¢â€â‚¬ Attach pipeline metadata Ã¢â€â‚¬Ã¢â€â‚¬
    metadata["ai_source_resolution"] = ai_source_result.to_dict() if ai_source_result else None
    metadata["ai_final_review"] = {
        "corrections": ai_review_result.changes if ai_review_result else [],
        "overrides": ai_review_result.scraper_overrides if ai_review_result else [],
        "proposed_removals": ai_review_result.proposed_removals if ai_review_result else {},
        "artwork_approved": ai_review_result.artwork_approved if ai_review_result else True,
        "confidence": ai_review_result.overall_confidence if ai_review_result else None,
        "prompt_used": ai_review_result.prompt_used if ai_review_result else None,
        "raw_response": ai_review_result.raw_response if ai_review_result else None,
        "model_name": ai_review_result.model_name if ai_review_result else None,
        "error": (ai_review_result.error or None) if ai_review_result else None,
    } if ai_review_result else None
    metadata["pipeline_log"] = pipeline_log
    metadata["pipeline_failures"] = pipeline_failures

    return metadata
