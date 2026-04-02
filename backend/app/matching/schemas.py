"""
Pydantic Schemas for matching API request/response.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Response schemas ──────────────────────────────────────────────────────

class ScoreBreakdownOut(BaseModel):
    features: Dict[str, float] = {}
    weighted_contributions: Dict[str, float] = {}
    category_scores: Dict[str, float] = {}
    overall_score: float = 0.0
    status: str = "unmatched"


class CandidateOut(BaseModel):
    entity_type: str
    mbid: Optional[str] = None
    canonical_name: str
    provider: str = ""
    score: float = 0.0
    breakdown: Optional[Dict[str, Any]] = None
    is_selected: bool = False


class ResolveResultOut(BaseModel):
    video_id: int
    resolved_artist: str = ""
    artist_mbid: Optional[str] = None
    resolved_recording: str = ""
    recording_mbid: Optional[str] = None
    resolved_release: Optional[str] = None
    release_mbid: Optional[str] = None
    confidence_overall: float = 0.0
    confidence_breakdown: Optional[Dict[str, Any]] = None
    status: str = "unmatched"
    candidate_list: List[CandidateOut] = []
    normalization_notes: Optional[Dict[str, Any]] = None
    changed: bool = False
    is_user_pinned: bool = False


class NormalizationResultOut(BaseModel):
    raw_artist: str
    raw_title: str
    raw_album: Optional[str] = None
    artist_display: str
    artist_key: str
    primary_artist: str
    featured_artists: Optional[List[str]] = None
    title_display: str
    title_key: str
    title_base: str
    qualifiers: Optional[List[str]] = None
    album_display: Optional[str] = None
    album_key: Optional[str] = None
    normalization_notes: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


# ── Request schemas ───────────────────────────────────────────────────────

class PinRequest(BaseModel):
    candidate_id: int = Field(..., description="MatchCandidate.id to pin")


class BatchResolveRequest(BaseModel):
    video_ids: Optional[List[int]] = Field(
        None,
        description="Specific video IDs; null = use filter",
    )
    filter: Optional[str] = Field(
        None,
        description="Filter: 'missing' | 'low_confidence' | 'needs_review' | 'all'",
    )
    force: bool = Field(False, description="Ignore hysteresis delta")


class BatchResolveOut(BaseModel):
    job_id: int
    message: str = ""
    video_count: int = 0


class UndoResultOut(BaseModel):
    video_id: int
    previous_artist: Optional[str] = None
    previous_recording: Optional[str] = None
    message: str = ""


# ── Review queue schemas ──────────────────────────────────────────────────

class DuplicateVideoSummary(BaseModel):
    """Compact summary of a video for duplicate comparison."""
    video_id: int
    artist: str = ""
    title: str = ""
    version_type: str = "normal"
    thumbnail_url: Optional[str] = None
    resolution_label: Optional[str] = None
    file_size_bytes: Optional[int] = None
    duration_seconds: Optional[float] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    video_bitrate: Optional[int] = None
    audio_bitrate: Optional[int] = None
    fps: Optional[float] = None
    hdr: bool = False
    container: Optional[str] = None
    import_method: Optional[str] = None
    quality_score: int = 0


class ReviewItemOut(BaseModel):
    """A single item in the review queue (video + match summary)."""
    video_id: int
    artist: str = ""
    title: str = ""
    filename: Optional[str] = None
    thumbnail_url: Optional[str] = None
    review_status: str = "none"
    review_category: Optional[str] = None
    resolved_artist: str = ""
    resolved_recording: str = ""
    confidence_overall: float = 0.0
    status: str = "unmatched"
    is_user_pinned: bool = False
    top_candidate: Optional[CandidateOut] = None
    candidate_count: int = 0
    version_type: str = "normal"
    review_reason: Optional[str] = None
    updated_at: Optional[datetime] = None
    resolution_label: Optional[str] = None
    file_size_bytes: Optional[int] = None
    import_method: Optional[str] = None
    related_versions: Optional[list] = None
    # Quality fields
    duration_seconds: Optional[float] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    video_bitrate: Optional[int] = None
    audio_bitrate: Optional[int] = None
    fps: Optional[float] = None
    hdr: bool = False
    container: Optional[str] = None
    quality_score: int = 0
    # Duplicate comparison
    duplicate_of: Optional[DuplicateVideoSummary] = None
    # Rename info
    expected_path: Optional[str] = None


class ReviewListOut(BaseModel):
    """Paginated review queue response."""
    items: List[ReviewItemOut] = []
    total: int = 0
    page: int = 1
    page_size: int = 25
    category_counts: dict = {}


# ── Apply (without pin) ──────────────────────────────────────────────────

class ApplyRequest(BaseModel):
    candidate_id: int = Field(..., description="MatchCandidate.id to apply without pinning")


# ── Manual search schemas ─────────────────────────────────────────────────

class ManualSearchResultOut(BaseModel):
    """A single result from a manual MusicBrainz search."""
    mbid: str
    name: str
    disambiguation: Optional[str] = None
    score: int = 0  # MB search score 0-100
    extra: Optional[Dict[str, Any]] = None  # type-specific fields


class ManualSearchResponse(BaseModel):
    query: str
    entity_type: str
    results: List[ManualSearchResultOut] = []


# ── Export schemas ────────────────────────────────────────────────────────

class ExportKodiRequest(BaseModel):
    video_ids: Optional[List[int]] = Field(None, description="Specific video IDs; null = all matched")
    overwrite_existing: bool = Field(False, description="Overwrite existing NFO files")


class ExportKodiResponse(BaseModel):
    exported: int = 0
    skipped: int = 0
    errors: int = 0
    message: str = ""
