"""
Candidate Data Structures & Provider Adapter
=============================================

Dataclasses representing candidates returned by provider searches,
plus an adapter that wraps the existing MusicBrainz (and optionally
Wikipedia) providers into the unified candidate interface.

Each candidate carries:
* canonical name / title
* MBID (nullable)
* aliases (artist) / disambiguation / country / type
* release date / year (release)
* track duration (recording)
* provider score hint
* raw ``ProviderResult`` reference for traceability
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "ArtistCandidate",
    "RecordingCandidate",
    "ReleaseCandidate",
    "fetch_artist_candidates",
    "fetch_recording_candidates",
    "fetch_release_candidates",
]


# ── Dataclasses ───────────────────────────────────────────────────────────

@dataclass
class ArtistCandidate:
    """A single artist result from a provider search."""
    canonical_name: str
    mbid: Optional[str] = None
    sort_name: Optional[str] = None
    aliases: List[str] = field(default_factory=list)
    disambiguation: Optional[str] = None
    country: Optional[str] = None
    artist_type: Optional[str] = None  # Person | Group | Orchestra | …
    provider_score: float = 0.0        # raw score hint from provider (0–1)
    provider: str = ""
    raw: Optional[Dict[str, Any]] = None  # original ProviderResult fields


@dataclass
class RecordingCandidate:
    """A single recording / track result."""
    title: str
    mbid: Optional[str] = None
    artist_name: Optional[str] = None
    artist_mbid: Optional[str] = None
    album_title: Optional[str] = None
    album_mbid: Optional[str] = None
    year: Optional[int] = None
    duration_seconds: Optional[float] = None
    genres: List[str] = field(default_factory=list)
    is_video: bool = False
    provider_score: float = 0.0
    provider: str = ""
    raw: Optional[Dict[str, Any]] = None


@dataclass
class ReleaseCandidate:
    """A single album / release result."""
    title: str
    mbid: Optional[str] = None
    artist_name: Optional[str] = None
    artist_mbid: Optional[str] = None
    year: Optional[int] = None
    release_date: Optional[str] = None
    album_type: Optional[str] = None  # album | ep | single | compilation
    genres: List[str] = field(default_factory=list)
    provider_score: float = 0.0
    provider: str = ""
    raw: Optional[Dict[str, Any]] = None


# ── Provider adapter ─────────────────────────────────────────────────────

def _get_mb_provider():
    """Lazy import to avoid circular deps and enable mocking."""
    from app.metadata.providers.musicbrainz import MusicBrainzProvider
    return MusicBrainzProvider()


def _get_wiki_provider():
    from app.metadata.providers.wikipedia import WikipediaProvider
    return WikipediaProvider()


def fetch_artist_candidates(
    name: str,
    mb_artist_id: Optional[str] = None,
    *,
    limit: int = 5,
    include_wikipedia: bool = False,
) -> List[ArtistCandidate]:
    """
    Fetch artist candidates from all enabled providers.

    Returns up to *limit* candidates from MusicBrainz, optionally
    augmented with Wikipedia data.
    """
    candidates: List[ArtistCandidate] = []
    mb = _get_mb_provider()

    # Direct MBID lookup
    if mb_artist_id:
        r = mb.get_artist(mb_artist_id)
        if r:
            candidates.append(_artist_from_provider_result(r))

    # Search by name
    for pr in mb.search_artist(name):
        candidates.append(_artist_from_provider_result(pr))
        if len(candidates) >= limit:
            break

    # Optional Wikipedia signal
    if include_wikipedia:
        try:
            wp = _get_wiki_provider()
            for pr in wp.search_artist(name):
                candidates.append(_artist_from_provider_result(pr))
        except Exception as e:
            logger.debug(f"Wikipedia artist search skipped: {e}")

    return candidates[:limit]


def fetch_recording_candidates(
    artist: str,
    title: str,
    mb_recording_id: Optional[str] = None,
    *,
    limit: int = 8,
    include_wikipedia: bool = False,
) -> List[RecordingCandidate]:
    """Fetch recording candidates from all enabled providers."""
    candidates: List[RecordingCandidate] = []
    mb = _get_mb_provider()

    # Direct MBID lookup
    if mb_recording_id:
        r = mb.get_track(mb_recording_id)
        if r:
            candidates.append(_recording_from_provider_result(r))

    # Search
    for pr in mb.search_track(artist, title):
        candidates.append(_recording_from_provider_result(pr))
        if len(candidates) >= limit:
            break

    if include_wikipedia:
        try:
            wp = _get_wiki_provider()
            for pr in wp.search_track(artist, title):
                candidates.append(_recording_from_provider_result(pr))
        except Exception as e:
            logger.debug(f"Wikipedia track search skipped: {e}")

    return candidates[:limit]


def fetch_release_candidates(
    artist: str,
    album: str,
    mb_release_id: Optional[str] = None,
    *,
    limit: int = 5,
) -> List[ReleaseCandidate]:
    """Fetch release (album) candidates from MusicBrainz."""
    candidates: List[ReleaseCandidate] = []
    mb = _get_mb_provider()

    if mb_release_id:
        r = mb.get_album(mb_release_id)
        if r:
            candidates.append(_release_from_provider_result(r))

    for pr in mb.search_album(artist, album):
        candidates.append(_release_from_provider_result(pr))
        if len(candidates) >= limit:
            break

    return candidates[:limit]


# ── Converters ────────────────────────────────────────────────────────────

def _artist_from_provider_result(pr) -> ArtistCandidate:
    f = pr.fields
    return ArtistCandidate(
        canonical_name=f.get("canonical_name", ""),
        mbid=f.get("mb_artist_id"),
        sort_name=f.get("sort_name"),
        aliases=f.get("aliases") or [],
        disambiguation=f.get("disambiguation"),
        country=f.get("country"),
        artist_type=f.get("artist_type"),
        provider_score=pr.confidence,
        provider=pr.provenance,
        raw=f,
    )


def _recording_from_provider_result(pr) -> RecordingCandidate:
    f = pr.fields
    return RecordingCandidate(
        title=f.get("title", ""),
        mbid=f.get("mb_recording_id"),
        artist_name=f.get("artist"),
        artist_mbid=f.get("mb_artist_id"),
        album_title=f.get("album"),
        album_mbid=f.get("mb_release_id"),
        year=f.get("year"),
        duration_seconds=f.get("duration_seconds"),
        genres=f.get("genres") or [],
        provider_score=pr.confidence,
        provider=pr.provenance,
        raw=f,
    )


def _release_from_provider_result(pr) -> ReleaseCandidate:
    f = pr.fields
    return ReleaseCandidate(
        title=f.get("title", ""),
        mbid=f.get("mb_release_id"),
        artist_name=f.get("artist"),
        artist_mbid=f.get("mb_artist_id"),
        year=f.get("year"),
        release_date=f.get("release_date"),
        album_type=f.get("album_type"),
        genres=f.get("genres") or [],
        provider_score=pr.confidence,
        provider=pr.provenance,
        raw=f,
    )
