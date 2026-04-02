"""
TMVDB Provider — Interface to The Music Video DB (themusicvideodb.org).

Supports bidirectional data exchange:
- Pull: retrieve metadata by audio fingerprint, MBID, or artist+title lookup
- Push: submit local metadata to improve the community database

This is a placeholder implementation — the actual API endpoints will be
wired in when themusicvideodb.org goes live.  All methods return empty
results until a valid API key and base URL are configured.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.metadata.providers.base import AssetCandidate, MetadataProvider, ProviderResult

logger = logging.getLogger(__name__)

# Default base URL — will be configurable via settings
TMVDB_BASE_URL = "https://api.themusicvideodb.org/v1"


class TMVDBProvider(MetadataProvider):
    """
    Metadata provider backed by The Music Video DB.

    When enabled, acts as the highest-trust structured data source
    (community-curated, similar to MusicBrainz for audio).
    """

    name = "tmvdb"

    def __init__(self, api_key: str = "", base_url: str = TMVDB_BASE_URL):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._enabled = bool(api_key)

    # ── Internal helpers ─────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "Playarr/1.0",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """GET request to TMVDB API.  Returns None if disabled or on error."""
        if not self._enabled:
            return None
        import httpx
        try:
            url = f"{self.base_url}{path}"
            resp = httpx.get(url, headers=self._headers(), params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                logger.warning("TMVDB rate limited — backing off")
            elif resp.status_code != 404:
                logger.warning(f"TMVDB {path}: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"TMVDB request failed: {e}")
        return None

    def _post(self, path: str, payload: Dict) -> Optional[Dict]:
        """POST request to TMVDB API.  Returns None if disabled or on error."""
        if not self._enabled:
            return None
        import httpx
        try:
            url = f"{self.base_url}{path}"
            resp = httpx.post(url, headers=self._headers(), json=payload, timeout=15)
            if resp.status_code in (200, 201):
                return resp.json()
            logger.warning(f"TMVDB POST {path}: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"TMVDB POST failed: {e}")
        return None

    # ── MetadataProvider interface ───────────────────────────────

    def search_artist(self, name: str) -> List[ProviderResult]:
        data = self._get("/artists/search", {"q": name})
        if not data or "results" not in data:
            return []
        results = []
        for item in data["results"]:
            fields = {
                "canonical_name": item.get("name", name),
                "mb_artist_id": item.get("mb_artist_id"),
                "country": item.get("country"),
                "biography": item.get("biography"),
                "tmvdb_artist_id": item.get("id"),
            }
            fp = {k: "tmvdb" for k, v in fields.items() if v}
            results.append(ProviderResult(
                fields=fields,
                confidence=item.get("confidence", 0.8),
                provenance="tmvdb",
                field_provenance=fp,
            ))
        return results

    def get_artist(self, key: str) -> Optional[ProviderResult]:
        data = self._get(f"/artists/{key}")
        if not data:
            return None
        fields = {
            "canonical_name": data.get("name"),
            "mb_artist_id": data.get("mb_artist_id"),
            "country": data.get("country"),
            "biography": data.get("biography"),
            "disambiguation": data.get("disambiguation"),
            "tmvdb_artist_id": data.get("id"),
        }
        fp = {k: "tmvdb" for k, v in fields.items() if v}
        return ProviderResult(
            fields={k: v for k, v in fields.items() if v},
            confidence=0.9,
            provenance="tmvdb",
            field_provenance=fp,
        )

    def search_album(self, artist: str, title: str) -> List[ProviderResult]:
        data = self._get("/albums/search", {"artist": artist, "title": title})
        if not data or "results" not in data:
            return []
        results = []
        for item in data["results"]:
            fields = {
                "title": item.get("title", title),
                "year": item.get("year"),
                "album_type": item.get("type"),
                "mb_release_id": item.get("mb_release_id"),
                "mb_release_group_id": item.get("mb_release_group_id"),
                "tmvdb_album_id": item.get("id"),
            }
            fp = {k: "tmvdb" for k, v in fields.items() if v}
            results.append(ProviderResult(
                fields=fields,
                confidence=item.get("confidence", 0.8),
                provenance="tmvdb",
                field_provenance=fp,
            ))
        return results

    def get_album(self, key: str) -> Optional[ProviderResult]:
        data = self._get(f"/albums/{key}")
        if not data:
            return None
        fields = {
            "title": data.get("title"),
            "year": data.get("year"),
            "album_type": data.get("type"),
            "mb_release_id": data.get("mb_release_id"),
            "mb_release_group_id": data.get("mb_release_group_id"),
            "tmvdb_album_id": data.get("id"),
        }
        fp = {k: "tmvdb" for k, v in fields.items() if v}
        return ProviderResult(
            fields={k: v for k, v in fields.items() if v},
            confidence=0.9,
            provenance="tmvdb",
            field_provenance=fp,
        )

    def search_track(self, artist: str, title: str) -> List[ProviderResult]:
        data = self._get("/tracks/search", {"artist": artist, "title": title})
        if not data or "results" not in data:
            return []
        results = []
        for item in data["results"]:
            fields = {
                "title": item.get("title", title),
                "artist": item.get("artist", artist),
                "album": item.get("album"),
                "year": item.get("year"),
                "plot": item.get("plot"),
                "mb_recording_id": item.get("mb_recording_id"),
                "mb_release_id": item.get("mb_release_id"),
                "genres": item.get("genres", []),
                "tmvdb_track_id": item.get("id"),
                "version_type": item.get("version_type"),
                "source_urls": item.get("source_urls", []),
            }
            fp = {k: "tmvdb" for k, v in fields.items() if v}
            results.append(ProviderResult(
                fields=fields,
                confidence=item.get("confidence", 0.8),
                provenance="tmvdb",
                field_provenance=fp,
            ))
        return results

    def get_track(self, key: str) -> Optional[ProviderResult]:
        data = self._get(f"/tracks/{key}")
        if not data:
            return None
        fields = {
            "title": data.get("title"),
            "artist": data.get("artist"),
            "album": data.get("album"),
            "year": data.get("year"),
            "plot": data.get("plot"),
            "mb_recording_id": data.get("mb_recording_id"),
            "genres": data.get("genres", []),
            "tmvdb_track_id": data.get("id"),
        }
        fp = {k: "tmvdb" for k, v in fields.items() if v}
        return ProviderResult(
            fields={k: v for k, v in fields.items() if v},
            confidence=0.9,
            provenance="tmvdb",
            field_provenance=fp,
        )

    def get_artist_assets(self, artist_name: str,
                          mbid: Optional[str] = None) -> List[AssetCandidate]:
        params: Dict[str, str] = {"name": artist_name}
        if mbid:
            params["mbid"] = mbid
        data = self._get("/artists/assets", params)
        if not data or "assets" not in data:
            return []
        return [
            AssetCandidate(
                url=a["url"],
                kind=a.get("kind", "poster"),
                width=a.get("width", 0),
                height=a.get("height", 0),
                provenance="tmvdb",
                confidence=a.get("confidence", 0.8),
            )
            for a in data["assets"]
        ]

    def get_album_assets(self, artist_name: str, album_title: str,
                         mbid: Optional[str] = None) -> List[AssetCandidate]:
        params: Dict[str, str] = {"artist": artist_name, "album": album_title}
        if mbid:
            params["mbid"] = mbid
        data = self._get("/albums/assets", params)
        if not data or "assets" not in data:
            return []
        return [
            AssetCandidate(
                url=a["url"],
                kind=a.get("kind", "poster"),
                width=a.get("width", 0),
                height=a.get("height", 0),
                provenance="tmvdb",
                confidence=a.get("confidence", 0.8),
            )
            for a in data["assets"]
        ]

    # ── Fingerprint-based lookup (unique to TMVDB) ───────────────

    def lookup_by_fingerprint(self, fingerprint: str,
                              duration: Optional[float] = None) -> Optional[ProviderResult]:
        """
        Look up a track by its Chromaprint audio fingerprint.

        Returns full metadata if the fingerprint is known to TMVDB.
        """
        params: Dict[str, Any] = {"fingerprint": fingerprint}
        if duration:
            params["duration"] = duration
        data = self._get("/lookup/fingerprint", params)
        if not data:
            return None
        fields = {
            "title": data.get("title"),
            "artist": data.get("artist"),
            "album": data.get("album"),
            "year": data.get("year"),
            "plot": data.get("plot"),
            "genres": data.get("genres", []),
            "mb_recording_id": data.get("mb_recording_id"),
            "mb_artist_id": data.get("mb_artist_id"),
            "tmvdb_track_id": data.get("id"),
            "source_urls": data.get("source_urls", []),
        }
        fp = {k: "tmvdb" for k, v in fields.items() if v}
        return ProviderResult(
            fields={k: v for k, v in fields.items() if v},
            confidence=data.get("confidence", 0.9),
            provenance="tmvdb",
            field_provenance=fp,
        )

    # ── Push operations (contribute to the community DB) ─────────

    def push_track(self, track_data: Dict[str, Any]) -> Optional[Dict]:
        """
        Submit track metadata to TMVDB.

        track_data should include: artist, title, album, year, plot,
        genres, mb_recording_id, source_urls, fingerprint, etc.
        """
        return self._post("/tracks", track_data)

    def push_artist(self, artist_data: Dict[str, Any]) -> Optional[Dict]:
        """Submit artist metadata to TMVDB."""
        return self._post("/artists", artist_data)

    def push_album(self, album_data: Dict[str, Any]) -> Optional[Dict]:
        """Submit album metadata to TMVDB."""
        return self._post("/albums", album_data)
