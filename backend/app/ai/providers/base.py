"""
AI Provider Base — Abstract interface for all AI/LLM providers.

Every provider must implement:
- enrich_metadata() — verify/correct/enrich scraped metadata
- generate_plot()   — write a Kodi-suitable plot description
- analyze_scenes()  — (optional) describe scenes from frame descriptions

Providers are synchronous (run in background threads).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response containers
# ---------------------------------------------------------------------------

@dataclass
class AIMetadataResponse:
    """Structured AI metadata result."""
    artist: Optional[str] = None
    title: Optional[str] = None
    album: Optional[str] = None
    year: Optional[int] = None
    plot: Optional[str] = None
    genres: Optional[List[str]] = None

    # Additional enriched fields
    director: Optional[str] = None
    studio: Optional[str] = None
    tags: Optional[List[str]] = None

    # Per-field confidence (0.0–1.0)
    field_scores: Dict[str, float] = field(default_factory=dict)
    overall_confidence: float = 0.0

    # Identity verification from AI
    identity: Dict[str, Any] = field(default_factory=dict)
    # e.g. {"candidate_artist": "...", "candidate_title": "...",
    #        "evidence": {"filename_match": true, ...}}

    # AI-detected mismatch info
    mismatch_info: Dict[str, Any] = field(default_factory=dict)
    # e.g. {"is_mismatch": false, "severity": "none", "reasons": []}

    # Human-readable change summary from AI
    change_summary: str = ""

    # Debugging / provenance
    raw_response: str = ""
    prompt_used: str = ""
    tokens_used: int = 0
    model_name: str = ""


@dataclass
class AISceneDescription:
    """Description of a single scene for thumbnail selection."""
    timestamp_sec: float = 0.0
    description: str = ""
    visual_quality_hint: float = 0.5  # 0–1 how visually interesting the AI thinks it is


@dataclass
class ThumbnailRanking:
    """AI vision ranking result for a single thumbnail candidate."""
    index: int = 0           # Original index in the candidates list
    score: float = 0.5       # AI quality score 0–1
    has_artist: bool = False  # Whether the performer is visually prominent
    has_text: bool = False    # Whether text/logos/watermarks dominate the frame
    is_blur: bool = False     # Whether the frame is a transition/blur artifact
    description: str = ""     # Brief AI description of the frame


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------

class AIProviderBase(ABC):
    """
    Abstract AI provider interface.

    Subclasses must set ``name`` and implement all abstract methods.
    """

    name: str = "base"

    @abstractmethod
    def enrich_metadata(
        self,
        scraped: Dict[str, Any],
        video_filename: Optional[str] = None,
        source_url: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        channel_name: Optional[str] = None,
        upload_date: Optional[str] = None,
        mismatch_signals: Optional[List[Dict[str, Any]]] = None,
        fingerprint_hint: Optional[Dict[str, Any]] = None,
        review_description_only: bool = False,
        platform_description: Optional[str] = None,
        platform_tags: Optional[List[str]] = None,
        custom_system_prompt: Optional[str] = None,
        custom_enrichment_template: Optional[str] = None,
        custom_review_template: Optional[str] = None,
    ) -> AIMetadataResponse:
        """
        Verify and enrich scraped metadata using AI.

        Args:
            scraped: Existing metadata dict with keys:
                     artist, title, album, year, plot, genres
            video_filename: Original filename for context
            source_url: Video source URL
            duration_seconds: Video duration
            channel_name: Platform channel name if available
            mismatch_signals: Pre-AI heuristic mismatch signals
            fingerprint_hint: Top audio fingerprint match

        Returns:
            AIMetadataResponse with corrected/enriched fields
        """
        ...

    @abstractmethod
    def generate_plot(
        self,
        artist: str,
        title: str,
        existing_plot: Optional[str] = None,
        source_url: Optional[str] = None,
        max_length: int = 300,
    ) -> Optional[str]:
        """
        Generate a Kodi-suitable plot/description for a music video.

        Returns:
            Generated plot text, or None on failure.
        """
        ...

    def is_configured(self) -> bool:
        """Check whether this provider has valid configuration (API key, etc)."""
        return False

    def get_model_name(self) -> str:
        """Return the model identifier being used."""
        return self.name

    def rank_thumbnails(
        self,
        image_paths: List[str],
        artist: str = "",
        title: str = "",
    ) -> List[ThumbnailRanking]:
        """
        Rank thumbnail candidates using AI vision.

        Subclasses with vision support should override this method
        to send images to the vision API and return rankings.

        Args:
            image_paths: Absolute paths to JPEG thumbnail images.
            artist: Artist name for context.
            title: Track title for context.

        Returns:
            List of ThumbnailRanking, one per image, sorted best-first.
            Empty list if vision is not supported or fails.
        """
        return []


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

# Legacy enrichment prompt — DEPRECATED, kept for reference only.
# New code should use app.ai.prompt_builder.build_metadata_enrichment_prompt()
METADATA_ENRICHMENT_PROMPT = ""  # see prompt_builder.py

PLOT_GENERATION_PROMPT = """Write a concise, engaging description for the following music video, suitable for a media library (Kodi).

Artist: {artist}
Title: {title}
{existing_context}

The description should be factual, mention the artist and song, and if possible describe the music video's visual content or themes. Maximum {max_length} characters. Write only the description text, no labels or prefixes."""


THUMBNAIL_RANKING_PROMPT = """You are evaluating thumbnail candidates for the music video "{title}" by {artist}.

For each image, provide a JSON object with:
- "index": the image number (0-based, in the order shown)
- "score": quality score from 0.0 to 1.0 (higher = better thumbnail)
- "has_artist": true if the performer/band is clearly visible and recognizable
- "has_text": true if text, logos, or watermarks dominate the frame
- "is_blur": true if the frame is blurry, a scene transition, or mostly black/white
- "description": one-sentence description of what the frame shows

Score highly: clear shots of the performer(s), good composition, vibrant colors, dynamic poses.
Score low: black/blank frames, blurry transitions, title cards, static backgrounds without people.

Return a JSON object with a single key "rankings" containing an array of objects, one per image.
Example: {{"rankings": [{{"index": 0, "score": 0.8, "has_artist": true, "has_text": false, "is_blur": false, "description": "Singer performing on stage"}}]}}"""
