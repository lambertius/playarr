"""
Provider Interface — Abstract base class for metadata providers.

Every provider adapter must implement this interface.  The resolver
uses these methods to gather metadata candidates, score them, and
merge the best results into the canonical entity graph.

Design notes
-------------
* ``search_*`` methods return **lists of candidates**, each annotated
  with a confidence score (0.0–1.0) and provenance string.
* ``get_*`` methods return a single canonical result by ID
  (e.g. MusicBrainz MBID).
* ``get_assets`` returns downloadable artwork candidates.
* All methods are synchronous (background-thread execution); providers
  must implement their own rate-limit / throttle logic internally.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class ProviderResult:
    """
    A single metadata result returned by a provider.

    Attributes:
        fields      — Dict of metadata key→value pairs.
        confidence  — 0.0–1.0 score expressing match certainty.
        provenance  — Provider name (e.g. "musicbrainz", "wikipedia").
    """
    fields: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    provenance: str = ""

    # Convenience: field-level provenance tracking
    field_provenance: Dict[str, str] = field(default_factory=dict)


@dataclass
class AssetCandidate:
    """
    A downloadable artwork candidate.

    Attributes:
        url         — Remote URL to download.
        kind        — One of poster|thumb|fanart|logo|banner.
        width       — Pixel width  (0 if unknown).
        height      — Pixel height (0 if unknown).
        provenance  — Provider name.
        confidence  — How likely this is the *correct* image.
    """
    url: str
    kind: str = "poster"
    width: int = 0
    height: int = 0
    provenance: str = ""
    confidence: float = 0.5


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------

class MetadataProvider(ABC):
    """
    Abstract metadata provider.

    Subclasses must set ``name`` and implement all abstract methods.
    Methods that are not applicable for a given provider may return
    empty lists / ``None``.
    """

    name: str = "base"

    # ---- Artist ----------------------------------------------------------

    @abstractmethod
    def search_artist(self, name: str) -> List[ProviderResult]:
        """Return candidate artist results ranked by confidence."""
        ...

    @abstractmethod
    def get_artist(self, key: str) -> Optional[ProviderResult]:
        """Return artist metadata by provider-specific key (e.g. MBID)."""
        ...

    # ---- Album -----------------------------------------------------------

    @abstractmethod
    def search_album(self, artist: str, title: str) -> List[ProviderResult]:
        """Return candidate album results."""
        ...

    @abstractmethod
    def get_album(self, key: str) -> Optional[ProviderResult]:
        """Return album metadata by key."""
        ...

    # ---- Track -----------------------------------------------------------

    @abstractmethod
    def search_track(self, artist: str, title: str) -> List[ProviderResult]:
        """Return candidate track / recording results."""
        ...

    @abstractmethod
    def get_track(self, key: str) -> Optional[ProviderResult]:
        """Return track metadata by key."""
        ...

    # ---- Assets ----------------------------------------------------------

    @abstractmethod
    def get_artist_assets(self, artist_name: str, mbid: Optional[str] = None) -> List[AssetCandidate]:
        """Return artwork candidates for an artist."""
        ...

    @abstractmethod
    def get_album_assets(self, artist_name: str, album_title: str,
                         mbid: Optional[str] = None) -> List[AssetCandidate]:
        """Return artwork candidates for an album."""
        ...

    def get_track_assets(self, artist_name: str, track_title: str,
                         mbid: Optional[str] = None) -> List[AssetCandidate]:
        """Return artwork candidates for a track / single."""
        return []
