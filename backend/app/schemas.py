"""
Playarr API Schemas — Pydantic models for request/response validation.
"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, HttpUrl, model_validator


# ---------------------------------------------------------------------------
# VideoItem
# ---------------------------------------------------------------------------

class VideoItemBase(BaseModel):
    artist: str
    title: str
    album: Optional[str] = None
    year: Optional[int] = None
    plot: Optional[str] = None


class VideoItemCreate(BaseModel):
    """Used when importing by URL."""
    url: str = Field(..., description="YouTube or Vimeo URL to import")
    artist: Optional[str] = Field(None, description="Override artist (auto-detected if blank)")
    title: Optional[str] = Field(None, description="Override title (auto-detected if blank)")
    normalize: bool = Field(True, description="Apply audio normalization after import")
    scrape: bool = Field(True, description="Scrape Wikipedia for metadata (album, plot, image)")
    scrape_musicbrainz: bool = Field(True, description="Scrape MusicBrainz for metadata")
    scrape_tmvdb: bool = Field(False, description="Retrieve metadata from The Music Video DB")
    # Version hints — user can indicate the type at import time
    is_cover: bool = Field(False, description="Hint: this is a cover version")
    is_live: bool = Field(False, description="Hint: this is a live performance")
    is_alternate: bool = Field(False, description="Hint: this is an alternate official version")
    is_uncensored: bool = Field(False, description="Hint: this is an uncensored version")
    alternate_version_label: Optional[str] = Field(None, description="Label for alternate version (e.g. Director's Cut)")
    ai_auto_analyse: bool = Field(False, description="Run full AI enrichment during import")
    ai_auto_fallback: bool = Field(False, description="Run AI enrichment only (no external scrapers)")


class VideoItemUpdate(BaseModel):
    """Manual metadata edit."""
    artist: Optional[str] = None
    title: Optional[str] = None
    album: Optional[str] = None
    year: Optional[int] = None
    plot: Optional[str] = None
    genres: Optional[List[str]] = None
    locked_fields: Optional[List[str]] = None
    version_type: Optional[str] = None
    alternate_version_label: Optional[str] = None
    original_artist: Optional[str] = None
    original_title: Optional[str] = None
    review_status: Optional[str] = None
    song_rating: Optional[int] = None
    video_rating: Optional[int] = None
    song_rating_set: Optional[bool] = None
    video_rating_set: Optional[bool] = None


class QualitySignatureOut(BaseModel):
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    video_codec: Optional[str] = None
    video_bitrate: Optional[int] = None
    hdr: bool = False
    audio_codec: Optional[str] = None
    audio_bitrate: Optional[int] = None
    audio_sample_rate: Optional[int] = None
    audio_channels: Optional[int] = None
    container: Optional[str] = None
    duration_seconds: Optional[float] = None
    loudness_lufs: Optional[float] = None

    class Config:
        from_attributes = True


# Providers that must never appear as source_type="video"
_METADATA_PROVIDERS = frozenset({"wikipedia", "musicbrainz"})


class SourceOut(BaseModel):
    id: int
    provider: str
    source_video_id: str
    original_url: str
    canonical_url: str
    source_type: Optional[str] = None
    provenance: Optional[str] = None

    @model_validator(mode="after")
    def _normalize_source_type(self) -> "SourceOut":
        """Strict enforcement: wikipedia/musicbrainz can never be 'video'."""
        if self.provider in _METADATA_PROVIDERS and self.source_type in (None, "video"):
            self.source_type = "single"
        return self

    class Config:
        from_attributes = True


class SourceCreate(BaseModel):
    provider: str
    source_video_id: str
    original_url: str
    canonical_url: str
    source_type: Optional[str] = None


class SourceUpdate(BaseModel):
    provider: Optional[str] = None
    source_video_id: Optional[str] = None
    original_url: Optional[str] = None
    canonical_url: Optional[str] = None
    source_type: Optional[str] = None


class MediaAssetOut(BaseModel):
    id: int
    asset_type: str
    file_path: str
    source_url: Optional[str] = None
    provenance: Optional[str] = None
    status: Optional[str] = "valid"

    class Config:
        from_attributes = True


class GenreOut(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True


class VideoItemOut(BaseModel):
    id: int
    artist: str
    title: str
    album: Optional[str] = None
    album_entity_id: Optional[int] = None
    year: Optional[int] = None
    plot: Optional[str] = None
    mb_artist_id: Optional[str] = None
    mb_recording_id: Optional[str] = None
    mb_release_id: Optional[str] = None
    folder_path: Optional[str] = None
    file_path: Optional[str] = None
    file_size_bytes: Optional[int] = None
    resolution_label: Optional[str] = None
    song_rating: Optional[int] = None
    video_rating: Optional[int] = None
    song_rating_set: bool = False
    video_rating_set: bool = False
    locked_fields: Optional[List[str]] = None
    version_type: str = "normal"
    alternate_version_label: Optional[str] = None
    original_artist: Optional[str] = None
    original_title: Optional[str] = None
    related_versions: Optional[list] = None
    parent_video_id: Optional[int] = None
    canonical_confidence: Optional[float] = None
    canonical_provenance: Optional[str] = None
    review_status: str = "none"
    review_reason: Optional[str] = None
    processing_state: Optional[dict] = None
    canonical_track_id: Optional[int] = None
    has_archive: bool = False
    exclude_from_editor_scan: bool = False
    created_at: datetime
    updated_at: datetime

    sources: List[SourceOut] = []
    quality_signature: Optional[QualitySignatureOut] = None
    genres: List[GenreOut] = []
    media_assets: List[MediaAssetOut] = []
    canonical_track: Optional["CanonicalTrackOut"] = None

    class Config:
        from_attributes = True


class VideoItemSummary(BaseModel):
    """Lightweight version for list views."""
    id: int
    artist: str
    title: str
    album: Optional[str] = None
    album_entity_id: Optional[int] = None
    year: Optional[int] = None
    resolution_label: Optional[str] = None
    has_poster: bool = False
    version_type: str = "normal"
    review_status: str = "none"
    enrichment_status: str = "pending"
    import_method: Optional[str] = None
    duration_seconds: Optional[float] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Processing Jobs
# ---------------------------------------------------------------------------

class JobOut(BaseModel):
    id: int
    video_id: Optional[int] = None
    celery_task_id: Optional[str] = None
    job_type: str
    status: str
    display_name: Optional[str] = None
    action_label: Optional[str] = None
    input_url: Optional[str] = None
    progress_percent: int = 0
    current_step: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    pipeline_steps: Optional[list] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class JobLogOut(BaseModel):
    id: int
    log_text: Optional[str] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Metadata Snapshot
# ---------------------------------------------------------------------------

class MetadataSnapshotOut(BaseModel):
    id: int
    video_id: int
    snapshot_data: dict
    reason: str
    created_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

class NormalizeRequest(BaseModel):
    target_lufs: Optional[float] = None  # None => use default from settings
    video_ids: Optional[List[int]] = None  # None => entire library


class NormalizationHistoryOut(BaseModel):
    id: int
    video_id: int
    target_lufs: float
    measured_lufs_before: Optional[float] = None
    measured_lufs_after: Optional[float] = None
    gain_applied_db: Optional[float] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class SettingOut(BaseModel):
    key: str
    value: str
    value_type: str = "string"

    class Config:
        from_attributes = True


class SettingUpdate(BaseModel):
    key: str
    value: str
    value_type: str = "string"


# ---------------------------------------------------------------------------
# Library scanning
# ---------------------------------------------------------------------------

class LibraryScanRequest(BaseModel):
    import_new: bool = Field(True, description="Import files not yet in the database")


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

class PaginatedResponse(BaseModel):
    items: List[VideoItemSummary]
    total: int
    page: int
    page_size: int
    total_pages: int


# ---------------------------------------------------------------------------
# Batch operations
# ---------------------------------------------------------------------------

class BatchRescanRequest(BaseModel):
    video_ids: Optional[List[int]] = None  # None => entire library
    # Pipeline options
    scrape_wikipedia: Optional[bool] = None
    scrape_musicbrainz: Optional[bool] = None
    scrape_tmvdb: Optional[bool] = None
    ai_auto: Optional[bool] = None
    ai_only: Optional[bool] = None
    hint_cover: Optional[bool] = None
    hint_live: Optional[bool] = None
    hint_alternate: Optional[bool] = None
    normalize: Optional[bool] = None
    find_source_video: Optional[bool] = None
    from_disk: Optional[bool] = None


class BatchActionResponse(BaseModel):
    job_id: int
    message: str
    locked_skipped: int = 0


# ---------------------------------------------------------------------------
# Canonical Entity Schemas
# ---------------------------------------------------------------------------

class ArtistEntityOut(BaseModel):
    id: int
    canonical_name: str
    sort_name: Optional[str] = None
    mb_artist_id: Optional[str] = None
    country: Optional[str] = None
    origin: Optional[str] = None
    disambiguation: Optional[str] = None
    biography: Optional[str] = None
    artist_image: Optional[str] = None
    genres: List[GenreOut] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LinkedVideoSummary(BaseModel):
    """Minimal info about a sibling video sharing the same canonical track."""
    id: int
    artist: str
    title: str
    resolution_label: Optional[str] = None
    version_type: str = "normal"

    class Config:
        from_attributes = True


class CanonicalTrackOut(BaseModel):
    """Canonical track metadata — shared across all linked videos."""
    id: int
    title: str
    artist_id: Optional[int] = None
    artist_name: Optional[str] = None
    album: Optional[str] = None
    album_id: Optional[int] = None
    year: Optional[int] = None
    genres: List[GenreOut] = []
    mb_recording_id: Optional[str] = None
    mb_release_id: Optional[str] = None
    mb_artist_id: Optional[str] = None
    artwork_album: Optional[str] = None
    artwork_single: Optional[str] = None
    canonical_verified: bool = False
    metadata_source: Optional[str] = None
    ai_verified: bool = False
    ai_verified_at: Optional[datetime] = None
    is_cover: bool = False
    original_artist: Optional[str] = None
    original_title: Optional[str] = None
    video_count: int = 0
    linked_videos: List["LinkedVideoSummary"] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CanonicalTrackSummary(BaseModel):
    """Lightweight canonical track for list views."""
    id: int
    title: str
    artist_name: Optional[str] = None
    album: Optional[str] = None
    year: Optional[int] = None
    ai_verified: bool = False
    video_count: int = 0

    class Config:
        from_attributes = True


class ProcessingStateOut(BaseModel):
    """Structured processing state for a video."""
    metadata_scraped: Optional[dict] = None
    metadata_ai_analyzed: Optional[dict] = None
    track_identified: Optional[dict] = None
    scenes_analyzed: Optional[dict] = None
    audio_normalized: Optional[dict] = None
    description_generated: Optional[dict] = None
    filename_checked: Optional[dict] = None
    canonical_linked: Optional[dict] = None


# ---------------------------------------------------------------------------
# Canonical Track Operations
# ---------------------------------------------------------------------------

class CanonicalTrackUpdate(BaseModel):
    """Manual canonical track edit (user override)."""
    title: Optional[str] = None
    artist_name: Optional[str] = None
    album_name: Optional[str] = None
    year: Optional[int] = None
    is_cover: Optional[bool] = None
    original_artist: Optional[str] = None
    original_title: Optional[str] = None
    genres: Optional[List[str]] = None


class CanonicalTrackCreate(BaseModel):
    """Create a new canonical track manually."""
    title: str
    artist_name: str
    album_name: Optional[str] = None
    year: Optional[int] = None
    is_cover: bool = False
    original_artist: Optional[str] = None
    original_title: Optional[str] = None
    genres: Optional[List[str]] = None


class SetParentVideoRequest(BaseModel):
    """Assign a parent video for hierarchical version chains."""
    parent_video_id: Optional[int] = None  # None to unlink


class LinkCanonicalRequest(BaseModel):
    """Link a video to an existing canonical track."""
    track_id: int


class CanonicalMatchCandidate(BaseModel):
    """A candidate match found by library scan."""
    track_id: int
    title: str
    artist_name: Optional[str] = None
    year: Optional[int] = None
    match_source: str  # musicbrainz / fingerprint / fuzzy
    confidence: float  # 0.0–1.0
    video_count: int = 0

    class Config:
        from_attributes = True


class CanonicalScanResult(BaseModel):
    """Result of scanning library for canonical matches."""
    video_id: int
    current_track_id: Optional[int] = None
    candidates: List[CanonicalMatchCandidate] = []
