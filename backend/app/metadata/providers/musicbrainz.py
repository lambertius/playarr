"""
MusicBrainz Provider — Structured metadata from MusicBrainz.

Primary source for:
- Canonical names & MBIDs (artist, release, recording)
- Structured data (year, release date, genres, country)

Rate-limit: ≤1 req/sec — enforced via ``time.sleep(1.1)`` between calls.
"""
import logging
import time
from typing import Any, Dict, List, Optional

import musicbrainzngs

from app.metadata.providers.base import (
    MetadataProvider, ProviderResult, AssetCandidate,
)
from app.services.metadata_resolver import (
    _init_musicbrainz, capitalize_genre, _pick_best_release,
    _search_single_release_group,
)
from app.services.artist_album_scraper import _resolve_commons_url

logger = logging.getLogger(__name__)

_RATE_LIMIT_SEC = 1.1

# Minimum vote count for MusicBrainz tags to be considered valid genres.
# Tags with very low counts (1-2) are often vandalism or noise.
_MIN_TAG_COUNT = 2


def _throttle():
    time.sleep(_RATE_LIMIT_SEC)


def _filter_tags(tags: list, min_count: int = _MIN_TAG_COUNT) -> List[str]:
    """Filter MusicBrainz tags by minimum vote count and return genre names."""
    return [
        capitalize_genre(t["name"])
        for t in tags
        if t.get("name") and int(t.get("count", 0)) >= min_count
    ]


# ---------------------------------------------------------------------------
# MusicBrainzProvider
# ---------------------------------------------------------------------------

class MusicBrainzProvider(MetadataProvider):
    """MusicBrainz metadata provider — canonical structured data."""

    name = "musicbrainz"

    def __init__(self):
        _init_musicbrainz()

    # ---- Artist ----------------------------------------------------------

    def search_artist(self, name: str) -> List[ProviderResult]:
        try:
            mb = musicbrainzngs.search_artists(artist=name, limit=5)
            _throttle()
        except Exception as e:
            logger.warning(f"MusicBrainz artist search failed: {e}")
            return []

        results: List[ProviderResult] = []
        for a in mb.get("artist-list", []):
            score = int(a.get("ext:score", 0)) / 100.0
            result_name = a.get("name", "")

            # Validate: result name must be reasonably similar to query.
            # MusicBrainz fuzzy search can return "Zippy Kid" for "Kazoo Kid"
            # or "The Beatles" for "The Chats" (sim=0.70 exactly).
            from difflib import SequenceMatcher
            _sim = SequenceMatcher(None, name.lower(), result_name.lower()).ratio()
            if _sim < 0.75:
                logger.debug(
                    f"MusicBrainz artist search: discarding '{result_name}' "
                    f"(similarity={_sim:.2f} to '{name}')"
                )
                continue

            aliases = [al.get("alias", "") for al in a.get("alias-list", [])]
            fields: Dict[str, Any] = {
                "canonical_name": a.get("name", name),
                "sort_name": a.get("sort-name"),
                "mb_artist_id": a.get("id"),
                "country": a.get("country"),
                "disambiguation": a.get("disambiguation"),
                "aliases": aliases,
                "artist_type": a.get("type"),  # Person, Group, etc.
            }
            # Extract genres/tags
            tags = a.get("tag-list", [])
            if tags:
                fields["genres"] = _filter_tags(tags)

            results.append(ProviderResult(
                fields=fields, confidence=score, provenance=self.name,
            ))
        return results

    def get_artist(self, key: str) -> Optional[ProviderResult]:
        """Get artist by MBID with URL relations."""
        try:
            data = musicbrainzngs.get_artist_by_id(key, includes=["url-rels", "tags"])
            _throttle()
        except Exception as e:
            logger.warning(f"MusicBrainz get_artist failed for {key}: {e}")
            return None

        a = data.get("artist", {})
        aliases = [al.get("alias", "") for al in a.get("alias-list", [])]
        tags = a.get("tag-list", [])

        fields: Dict[str, Any] = {
            "canonical_name": a.get("name"),
            "sort_name": a.get("sort-name"),
            "mb_artist_id": a.get("id"),
            "country": a.get("country"),
            "disambiguation": a.get("disambiguation"),
            "aliases": aliases,
            "artist_type": a.get("type"),
        }
        if tags:
            fields["genres"] = _filter_tags(tags)

        # Image URL from URL relations
        for rel in a.get("url-relation-list", []):
            if rel.get("type") == "image":
                fields["image_url"] = _resolve_commons_url(rel["target"])
                break

        # MBID direct lookup is authoritative — use confidence 1.0 so search
        # results (which can return wrong artists with high ext:score) cannot
        # override the canonical name from a known-good MBID.
        return ProviderResult(fields=fields, confidence=1.0, provenance=self.name)

    # ---- Album -----------------------------------------------------------

    def search_album(self, artist: str, title: str) -> List[ProviderResult]:
        try:
            query = f'release:"{title}"'
            if artist:
                query += f' AND artist:"{artist}"'
            mb = musicbrainzngs.search_releases(query=query, limit=5)
            _throttle()
        except Exception as e:
            logger.warning(f"MusicBrainz album search failed: {e}")
            return []

        results: List[ProviderResult] = []
        for r in mb.get("release-list", []):
            score = int(r.get("ext:score", 0)) / 100.0
            year = None
            release_date = r.get("date", "")
            if release_date:
                parts = release_date.split("-")
                if parts[0].isdigit():
                    year = int(parts[0])

            rg = r.get("release-group", {})
            fields: Dict[str, Any] = {
                "title": r.get("title", title),
                "mb_release_id": r.get("id"),
                "year": year,
                "release_date": release_date,
                "album_type": rg.get("primary-type", "").lower() if rg else None,
                "mb_release_group_id": rg.get("id") if rg else None,
            }
            # Artist credit
            credit = r.get("artist-credit", [])
            if credit:
                ac = credit[0] if isinstance(credit, list) else credit
                if isinstance(ac, dict) and "artist" in ac:
                    fields["artist"] = ac["artist"].get("name")
                    fields["mb_artist_id"] = ac["artist"].get("id")

            results.append(ProviderResult(
                fields=fields, confidence=score, provenance=self.name,
            ))
        return results

    def get_album(self, key: str) -> Optional[ProviderResult]:
        try:
            data = musicbrainzngs.get_release_by_id(key, includes=["tags", "artists"])
            _throttle()
        except Exception as e:
            logger.warning(f"MusicBrainz get_album failed for {key}: {e}")
            return None

        r = data.get("release", {})
        year = None
        release_date = r.get("date", "")
        if release_date:
            parts = release_date.split("-")
            if parts[0].isdigit():
                year = int(parts[0])

        rg = r.get("release-group", {})
        fields: Dict[str, Any] = {
            "title": r.get("title"),
            "mb_release_id": r.get("id"),
            "year": year,
            "release_date": release_date,
            "album_type": rg.get("primary-type", "").lower() if rg else None,
        }
        tags = r.get("tag-list", [])
        if tags:
            fields["genres"] = _filter_tags(tags)

        return ProviderResult(fields=fields, confidence=0.95, provenance=self.name)

    # ---- Track -----------------------------------------------------------

    def search_track(self, artist: str, title: str) -> List[ProviderResult]:
        # --- Strategy 1: search_release_groups with primarytype:single ---
        single = _search_single_release_group(artist, title)
        if single:
            fields: Dict[str, Any] = {
                "title": single.get("title", title),
                "mb_recording_id": single.get("mb_recording_id"),
                "mb_release_id": single.get("mb_release_id"),
                "mb_release_group_id": single.get("mb_release_group_id"),
                "album": single.get("album"),
                "year": single.get("year"),
                "artist": single.get("artist"),
                "mb_artist_id": single.get("mb_artist_id"),
            }
            if single.get("genres"):
                fields["genres"] = single["genres"]
            return [ProviderResult(
                fields=fields, confidence=0.98, provenance=self.name,
            )]

        # --- Strategy 2: fall back to search_recordings ---
        try:
            query = f'recording:"{title}"'
            if artist:
                query += f' AND artist:"{artist}"'
            mb = musicbrainzngs.search_recordings(query=query, limit=10)
            _throttle()
        except Exception as e:
            logger.warning(f"MusicBrainz track search failed: {e}")
            return []

        results: List[ProviderResult] = []
        for r in mb.get("recording-list", []):
            score = int(r.get("ext:score", 0)) / 100.0
            fields: Dict[str, Any] = {
                "title": r.get("title", title),
                "mb_recording_id": r.get("id"),
                "duration_seconds": int(r.get("length", 0)) / 1000.0 if r.get("length") else None,
            }
            # Releases — pick the best single/EP release only
            releases = r.get("release-list", [])
            best_rel = _pick_best_release(releases, allowed_types={"single", "ep"})
            if best_rel:
                rg = best_rel.get("release-group", {})
                fields["album"] = best_rel.get("title")
                fields["mb_release_id"] = best_rel.get("id")
                if rg.get("id"):
                    fields["mb_release_group_id"] = rg["id"]
                rd = best_rel.get("date", "")
                if rd:
                    parts = rd.split("-")
                    if parts[0].isdigit():
                        fields["year"] = int(parts[0])

            # Artist credit
            credit = r.get("artist-credit", [])
            result_artist = ""
            if credit:
                ac = credit[0] if isinstance(credit, list) else credit
                if isinstance(ac, dict) and "artist" in ac:
                    result_artist = ac["artist"].get("name", "")
                    fields["artist"] = result_artist
                    fields["mb_artist_id"] = ac["artist"].get("id")

            # Validate: artist in result must be similar to query artist.
            if artist and result_artist:
                from difflib import SequenceMatcher
                _sim = SequenceMatcher(None, artist.lower(), result_artist.lower()).ratio()
                if _sim < 0.5:
                    logger.debug(
                        f"MusicBrainz track search: discarding recording by "
                        f"'{result_artist}' (similarity={_sim:.2f} to '{artist}')"
                    )
                    continue

            # Tags/genres
            tags = r.get("tag-list", [])
            if tags:
                fields["genres"] = _filter_tags(tags)

            results.append(ProviderResult(
                fields=fields, confidence=score, provenance=self.name,
            ))
        return results

    def get_track(self, key: str) -> Optional[ProviderResult]:
        try:
            data = musicbrainzngs.get_recording_by_id(key, includes=["tags", "artists", "releases"])
            _throttle()
        except Exception as e:
            logger.warning(f"MusicBrainz get_track failed for {key}: {e}")
            return None

        r = data.get("recording", {})
        fields: Dict[str, Any] = {
            "title": r.get("title"),
            "mb_recording_id": r.get("id"),
            "duration_seconds": int(r.get("length", 0)) / 1000.0 if r.get("length") else None,
        }
        releases = r.get("release-list", [])
        best_rel = _pick_best_release(releases, allowed_types={"single", "ep"})
        if best_rel:
            fields["album"] = best_rel.get("title")
            fields["mb_release_id"] = best_rel.get("id")

        tags = r.get("tag-list", [])
        if tags:
            fields["genres"] = _filter_tags(tags)

        return ProviderResult(fields=fields, confidence=0.95, provenance=self.name)

    # ---- Assets (MusicBrainz has no direct image hosting) ----------------

    def get_artist_assets(self, artist_name: str, mbid: Optional[str] = None) -> List[AssetCandidate]:
        """MusicBrainz can provide artist images via URL relations."""
        if not mbid:
            results = self.search_artist(artist_name)
            if results and results[0].fields.get("mb_artist_id"):
                mbid = results[0].fields["mb_artist_id"]
        if not mbid:
            return []

        result = self.get_artist(mbid)
        if result and result.fields.get("image_url"):
            return [AssetCandidate(
                url=result.fields["image_url"],
                kind="poster", provenance=self.name, confidence=0.8,
            )]
        return []

    def get_album_assets(self, artist_name: str, album_title: str,
                         mbid: Optional[str] = None) -> List[AssetCandidate]:
        """Delegate album artwork to CoverArtArchiveProvider."""
        return []  # handled by coverartarchive provider
