"""
Cover Art Archive Provider — Album artwork by MusicBrainz release MBID.

Uses the Cover Art Archive (coverartarchive.org) to fetch front-cover
images for releases identified via MusicBrainz.

Rate-limit: CAA has no strict limit, but we keep requests modest.
"""
import logging
from typing import List, Optional

import httpx

from app.metadata.providers.base import (
    MetadataProvider, ProviderResult, AssetCandidate,
)
from app.services.metadata_resolver import _init_musicbrainz

logger = logging.getLogger(__name__)

_CAA_BASE = "https://coverartarchive.org"


def _fetch_front_cover(release_mbid: str) -> Optional[str]:
    """Query CAA for the front cover URL of a release."""
    return _fetch_front_cover_from_url(f"{_CAA_BASE}/release/{release_mbid}")


def _fetch_front_cover_by_release_group(rg_mbid: str) -> Optional[str]:
    """Query CAA for the front cover URL of a release group.

    The ``/release-group/{id}`` endpoint returns the canonical cover
    chosen for an entire release-group, which is more reliable than
    picking an individual release.
    """
    return _fetch_front_cover_from_url(f"{_CAA_BASE}/release-group/{rg_mbid}")


def _fetch_front_cover_from_url(url: str) -> Optional[str]:
    """Shared logic for querying a CAA listing endpoint."""
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            return None
        data = resp.json()
        for img in data.get("images", []):
            if img.get("front", False):
                return img.get("image") or img.get("thumbnails", {}).get("large")
        # Fallback: first image
        images = data.get("images", [])
        if images:
            return images[0].get("image")
    except Exception as e:
        logger.debug(f"CAA query failed for {url}: {e}")
    return None


class CoverArtArchiveProvider(MetadataProvider):
    """Cover Art Archive provider — album artwork via release MBIDs."""

    name = "coverartarchive"

    def __init__(self):
        _init_musicbrainz()

    # Metadata searches are delegated to MusicBrainzProvider;
    # this provider only supplies assets.

    def search_artist(self, name: str) -> List[ProviderResult]:
        return []

    def get_artist(self, key: str) -> Optional[ProviderResult]:
        return None

    def search_album(self, artist: str, title: str) -> List[ProviderResult]:
        return []

    def get_album(self, key: str) -> Optional[ProviderResult]:
        return None

    def search_track(self, artist: str, title: str) -> List[ProviderResult]:
        return []

    def get_track(self, key: str) -> Optional[ProviderResult]:
        return None

    def get_artist_assets(self, artist_name: str, mbid: Optional[str] = None) -> List[AssetCandidate]:
        return []  # CAA has no artist images

    def get_album_assets(self, artist_name: str, album_title: str,
                         mbid: Optional[str] = None) -> List[AssetCandidate]:
        """Return front-cover artwork from Cover Art Archive."""
        if not mbid:
            # Attempt to find release MBID via MusicBrainz search
            import musicbrainzngs, time
            try:
                kwargs = {"release": album_title, "limit": 3}
                if artist_name:
                    kwargs["artist"] = artist_name
                mb = musicbrainzngs.search_releases(**kwargs)
                time.sleep(1.1)
                for r in mb.get("release-list", []):
                    mbid = r.get("id")
                    if mbid:
                        break
            except Exception as e:
                logger.warning(f"CAA MBID lookup failed: {e}")
                return []

        if not mbid:
            return []

        cover_url = _fetch_front_cover(mbid)
        if cover_url:
            return [AssetCandidate(
                url=cover_url, kind="poster", provenance=self.name, confidence=0.9,
            )]
        return []
