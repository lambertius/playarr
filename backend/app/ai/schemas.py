"""
AI Subsystem — Pydantic schemas for API request/response validation.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# AI Metadata
# ---------------------------------------------------------------------------

ALL_ENRICHABLE_FIELDS = [
    "artist", "title", "album", "year", "genres", "plot",
    "director", "studio", "actors", "tags",
]

class AIEnrichRequest(BaseModel):
    """Request to run AI metadata enrichment on a video."""
    provider: Optional[str] = Field(None, description="AI provider override (openai/gemini/claude/local)")
    auto_apply: bool = Field(False, description="Auto-apply high-confidence fields")
    force: bool = Field(False, description="Re-run even if results exist")
    fields: Optional[List[str]] = Field(None, description="Fields to enrich (null = use global defaults)")
    run_fingerprint: bool = Field(False, description="Also run audio fingerprint identification")
    skip_mismatch_check: bool = Field(False, description="Skip pre-AI mismatch detection")
    review_description_only: bool = Field(False, description="Only review/edit the description to a sensible Kodi size")


class AIFieldComparison(BaseModel):
    """Single field comparison between scraped and AI values."""
    field: str
    scraped_value: Any = None
    ai_value: Any = None
    ai_confidence: float = 0.0
    changed: bool = False
    accepted: bool = False
    locked: bool = False


class MismatchSignalOut(BaseModel):
    """A single mismatch detection signal."""
    name: str
    score: float
    details: str = ""
    weight: float = 0.0


class MismatchReportOut(BaseModel):
    """Mismatch detection report."""
    overall_score: float = 0.0
    is_suspicious: bool = False
    threshold: float = 0.4
    signals: List[MismatchSignalOut] = []
    video_type: str = "unknown"
    channel_trust: str = "unknown"


class FingerprintMatchOut(BaseModel):
    """A single fingerprint match result."""
    artist: Optional[str] = None
    title: Optional[str] = None
    album: Optional[str] = None
    year: Optional[int] = None
    confidence: float = 0.0
    mb_recording_id: Optional[str] = None


class FingerprintResultOut(BaseModel):
    """Audio fingerprint analysis result."""
    match_count: int = 0
    best_match: Optional[FingerprintMatchOut] = None
    matches: List[FingerprintMatchOut] = []
    error: Optional[str] = None
    fpcalc_available: bool = True


class SourceUpdateOut(BaseModel):
    """A source link discovered during a scrape."""
    provider: str
    source_type: Optional[str] = None
    original_url: str
    provenance: Optional[str] = None
    pending: bool = False


class ArtworkUpdateOut(BaseModel):
    """A single artwork asset with current vs proposed comparison."""
    asset_type: str
    proposed_asset_id: Optional[int] = None
    proposed_source_url: Optional[str] = None
    current_asset_id: Optional[int] = None
    current_source_url: Optional[str] = None
    provenance: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    unchanged: bool = False


class AIComparisonResponse(BaseModel):
    """Full metadata comparison between scraped and AI data."""
    video_id: int
    scraped: Dict[str, Any]
    ai: Optional[Dict[str, Any]] = None
    ai_result_id: Optional[int] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    overall_confidence: Optional[float] = None
    status: Optional[str] = None
    created_at: Optional[str] = None
    fields: List[AIFieldComparison] = []
    mismatch_report: Optional[MismatchReportOut] = None
    fingerprint_result: Optional[FingerprintResultOut] = None
    change_summary: Optional[str] = None
    verification_status: Optional[bool] = None
    artwork_updates: List[ArtworkUpdateOut] = []
    source_updates: List[SourceUpdateOut] = []


class AIApplyFieldsRequest(BaseModel):
    """Request to apply specific AI fields to a video."""
    ai_result_id: int
    fields: List[str] = Field(..., description="Fields to apply: artist, title, album, year, plot, genres, director, studio, actors, tags, poster, thumb, artist_thumb, album_thumb")
    rename_files: bool = Field(False, description="Also rename folder/files based on updated metadata")


class AIMetadataResultOut(BaseModel):
    """AI metadata result output."""
    id: int
    video_id: int
    provider: str
    model_name: Optional[str] = None
    model_task: Optional[str] = None
    status: str
    ai_artist: Optional[str] = None
    ai_title: Optional[str] = None
    ai_album: Optional[str] = None
    ai_year: Optional[int] = None
    ai_plot: Optional[str] = None
    ai_genres: Optional[List[str]] = None
    ai_director: Optional[str] = None
    ai_studio: Optional[str] = None
    ai_actors: Optional[List[Dict[str, str]]] = None
    ai_tags: Optional[List[str]] = None
    verification_status: Optional[bool] = None
    confidence_score: float = 0.0
    field_scores: Optional[Dict[str, float]] = None
    accepted_fields: Optional[List[str]] = None
    requested_fields: Optional[List[str]] = None
    mismatch_score: Optional[float] = None
    mismatch_signals: Optional[Dict[str, Any]] = None
    fingerprint_result: Optional[Dict[str, Any]] = None
    change_summary: Optional[str] = None
    tokens_used: Optional[int] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    dismissed_at: Optional[str] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Scene Analysis
# ---------------------------------------------------------------------------

class SceneAnalysisRequest(BaseModel):
    """Request to run scene analysis on a video."""
    threshold: float = Field(0.3, ge=0.0, le=1.0, description="Scene detection sensitivity")
    max_thumbnails: int = Field(12, ge=1, le=50, description="Max thumbnail candidates")
    force: bool = Field(False, description="Re-run even if results exist")


class AIThumbnailOut(BaseModel):
    """Thumbnail candidate output."""
    id: int
    video_id: int
    timestamp_sec: float
    file_path: str
    score_sharpness: float = 0.0
    score_contrast: float = 0.0
    score_color_variance: float = 0.0
    score_composition: float = 0.0
    score_overall: float = 0.0
    is_selected: bool = False
    provenance: str = "ai_scene_analysis"

    class Config:
        from_attributes = True


class SceneAnalysisOut(BaseModel):
    """Scene analysis result output."""
    id: int
    video_id: int
    status: str
    total_scenes: int = 0
    duration_seconds: Optional[float] = None
    scenes: Optional[List[Dict[str, Any]]] = None
    thumbnails: List[AIThumbnailOut] = []
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None

    class Config:
        from_attributes = True


class SelectThumbnailRequest(BaseModel):
    """Request to select a specific thumbnail."""
    thumbnail_id: int
    apply_to_poster: bool = False


# ---------------------------------------------------------------------------
# AI Settings
# ---------------------------------------------------------------------------

class AISettingsOut(BaseModel):
    """Current AI configuration."""
    provider: str = "none"
    openai_api_key_set: bool = False
    gemini_api_key_set: bool = False
    claude_api_key_set: bool = False
    local_llm_base_url: str = "http://localhost:11434/v1"
    local_llm_model: str = "llama3"
    auto_enrich_on_import: bool = False
    auto_scene_analysis: bool = False
    auto_apply_threshold: float = 0.85
    # Model selection
    model_selection_mode: str = "auto"  # "auto" | "manual"
    model_default: Optional[str] = None  # manual mode: single default model
    model_fallback: Optional[str] = None  # manual mode: fallback model
    model_metadata: Optional[str] = None  # manual advanced override
    model_verification: Optional[str] = None  # manual advanced override
    model_scene: Optional[str] = None  # manual advanced override
    # Auto mode preference
    auto_tier_preference: str = "balanced"  # "cheapest" | "balanced" | "accuracy"
    # Field-level controls
    enrichable_fields: List[str] = Field(
        default_factory=lambda: ALL_ENRICHABLE_FIELDS.copy()
    )
    # File rename safety
    rename_on_metadata_update: bool = False
    # Scene analysis mode
    scene_analysis_mode: str = "heuristic"  # "heuristic" or "ai_assisted"
    # AcoustID
    acoustid_api_key_set: bool = False


class AISettingsUpdate(BaseModel):
    """Update AI configuration."""
    provider: Optional[str] = None
    openai_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    claude_api_key: Optional[str] = None
    local_llm_base_url: Optional[str] = None
    local_llm_model: Optional[str] = None
    auto_enrich_on_import: Optional[bool] = None
    auto_scene_analysis: Optional[bool] = None
    auto_apply_threshold: Optional[float] = None
    # Model selection
    model_selection_mode: Optional[str] = None
    model_default: Optional[str] = None
    model_fallback: Optional[str] = None
    model_metadata: Optional[str] = None
    model_verification: Optional[str] = None
    model_scene: Optional[str] = None
    # Auto mode preference
    auto_tier_preference: Optional[str] = None
    # Field-level controls
    enrichable_fields: Optional[List[str]] = None
    # File rename safety
    rename_on_metadata_update: Optional[bool] = None
    # Scene analysis mode
    scene_analysis_mode: Optional[str] = None
    # AcoustID
    acoustid_api_key: Optional[str] = None


class AIPromptSettingsOut(BaseModel):
    """Current AI prompt templates."""
    system_prompt: str = ""
    enrichment_prompt: str = ""
    review_prompt: str = ""
    is_default_system: bool = True
    is_default_enrichment: bool = True
    is_default_review: bool = True


class AIPromptSettingsUpdate(BaseModel):
    """Update AI prompt templates. Send null or omit to keep current value."""
    system_prompt: Optional[str] = None
    enrichment_prompt: Optional[str] = None
    review_prompt: Optional[str] = None


class AITestConnectionRequest(BaseModel):
    """Request to test AI provider connection."""
    provider: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None


class AITestConnectionResponse(BaseModel):
    """Response from AI provider connection test."""
    success: bool
    provider: str = ""
    model_name: str = ""
    message: str = ""
    tokens_used: Optional[int] = None
    response_time_ms: Optional[int] = None


class AIUndoRequest(BaseModel):
    """Request to undo an AI enrichment."""
    ai_result_id: int


# ---------------------------------------------------------------------------
# Model Catalog
# ---------------------------------------------------------------------------

class ModelInfoOut(BaseModel):
    """A single model in a provider's catalog."""
    id: str
    label: str
    tier: str  # "fast" | "standard" | "high"
    capabilities: List[str] = []
    recommended_for: List[str] = []


class ModelCatalogOut(BaseModel):
    """Model catalog response for a single provider."""
    provider: str
    models: List[ModelInfoOut] = []
    defaults: Dict[str, Any] = {}
    updated_at: str = ""


class RoutingPreviewEntry(BaseModel):
    """Single row in the routing preview table."""
    task: str
    model_id: str
    model_label: str = ""
    reason: str = ""


class RoutingPreviewOut(BaseModel):
    """Shows what model the router would pick for each task type."""
    provider: str
    mode: str  # "auto" | "manual"
    entries: List[RoutingPreviewEntry] = []


class ModelAvailabilityEntry(BaseModel):
    """Result of testing a single model's availability."""
    model_id: str
    available: bool
    error: str = ""
    response_time_ms: int = 0


class ModelAvailabilityOut(BaseModel):
    """Results from testing which models are accessible."""
    provider: str
    results: List[ModelAvailabilityEntry] = []
    cached: bool = False
    tested_at: str = ""
