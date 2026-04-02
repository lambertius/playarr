"""
AI Subsystem — Database models for AI metadata results and scene analysis.

Tables:
- ai_metadata_results: AI-generated/verified metadata per video
- ai_scene_analyses:   Scene detection results and thumbnail candidates
- ai_thumbnails:       Scored thumbnail frames extracted from video
"""
import enum
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text, DateTime,
    ForeignKey, Enum, JSON, Index, UniqueConstraint,
)
from sqlalchemy.orm import relationship, Mapped, mapped_column

from app.database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AIResultStatus(str, enum.Enum):
    """Processing status for an AI metadata result."""
    pending = "pending"
    processing = "processing"
    complete = "complete"
    failed = "failed"
    accepted = "accepted"      # user accepted AI suggestions
    rejected = "rejected"      # user rejected AI suggestions
    partial = "partial"        # some fields accepted


class AIProvider(str, enum.Enum):
    """Supported AI providers."""
    openai = "openai"
    gemini = "gemini"
    claude = "claude"
    local = "local"
    none = "none"


class SceneAnalysisStatus(str, enum.Enum):
    """Processing status for scene analysis."""
    pending = "pending"
    processing = "processing"
    complete = "complete"
    failed = "failed"


# ---------------------------------------------------------------------------
# AIMetadataResult — AI-generated metadata for a video
# ---------------------------------------------------------------------------

class AIMetadataResult(Base):
    """
    Stores AI-generated metadata alongside the original scraped data
    for comparison and selective merge.  One row per AI run per video.
    """
    __tablename__ = "ai_metadata_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        ForeignKey("video_items.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    # Which provider produced this result
    provider: Mapped[AIProvider] = mapped_column(Enum(AIProvider), nullable=False)
    model_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Status
    status: Mapped[AIResultStatus] = mapped_column(
        Enum(AIResultStatus), default=AIResultStatus.pending,
    )

    # AI-generated fields (nullable — only populated fields mean the AI had an opinion)
    ai_artist: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ai_title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ai_album: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ai_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ai_plot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ai_genres: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # list of str

    # Additional AI-generated fields
    ai_director: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ai_studio: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ai_actors: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # [{name, role}]
    ai_tags: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # list of str

    # Verification result
    verification_status: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # True = AI verified metadata is correct, False = AI found issues

    # Overall and per-field confidence (0.0–1.0)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    field_scores: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # e.g. {"artist": 0.95, "title": 0.9, "album": 0.7, "year": 0.85, "genres": 0.6}

    # Copy of the scraped/existing data BEFORE AI ran (for diff/comparison)
    original_scraped: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Which fields the user accepted (null = no action yet)
    accepted_fields: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # list of str

    # Which fields were requested for enrichment (null = all)
    requested_fields: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # list of str

    # Pre-AI mismatch detection
    mismatch_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mismatch_signals: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Audio fingerprint result
    fingerprint_result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Model routing info
    model_task: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Raw AI response for debugging
    raw_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    prompt_used: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tokens_used: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Proposed source links (pending user approval)
    proposed_sources: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # [{"provider": "wikipedia", "source_type": "artist", "original_url": "...", "provenance": "scraped"}]

    # Change summary from AI
    change_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Error
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    dismissed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    video_item = relationship("VideoItem", foreign_keys=[video_id])

    __table_args__ = (
        Index("ix_ai_meta_video_provider", "video_id", "provider"),
    )

    def __repr__(self):
        return f"<AIMetadataResult {self.id}: video={self.video_id} provider={self.provider.value}>"


# ---------------------------------------------------------------------------
# AISceneAnalysis — scene detection and thumbnail candidates
# ---------------------------------------------------------------------------

class AISceneAnalysis(Base):
    """
    Stores scene detection results for a video.
    Links to individual thumbnail candidates for ranked selection.
    """
    __tablename__ = "ai_scene_analyses"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        ForeignKey("video_items.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    status: Mapped[SceneAnalysisStatus] = mapped_column(
        Enum(SceneAnalysisStatus), default=SceneAnalysisStatus.pending,
    )

    # Scene boundaries (list of {"start": float, "end": float, "description": str})
    scenes: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Config used for this analysis
    config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # e.g. {"threshold": 0.3, "min_scene_len": 1.0, "max_thumbnails": 12}

    # Stats
    total_scenes: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    video_item = relationship("VideoItem", foreign_keys=[video_id])
    thumbnails: Mapped[List["AIThumbnail"]] = relationship(
        back_populates="scene_analysis", cascade="all, delete-orphan",
        order_by="AIThumbnail.score_overall.desc()",
    )

    def __repr__(self):
        return f"<AISceneAnalysis {self.id}: video={self.video_id} scenes={self.total_scenes}>"


# ---------------------------------------------------------------------------
# AIThumbnail — individual scored thumbnail frame
# ---------------------------------------------------------------------------

class AIThumbnail(Base):
    """
    A single extracted frame scored by multiple quality heuristics.
    The highest-scoring frame is selected as the video thumbnail.
    """
    __tablename__ = "ai_thumbnails"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        ForeignKey("video_items.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    scene_analysis_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ai_scene_analyses.id", ondelete="SET NULL"), nullable=True,
    )

    # Frame info
    timestamp_sec: Mapped[float] = mapped_column(Float, nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)

    # Quality scores (0.0–1.0 each)
    score_sharpness: Mapped[float] = mapped_column(Float, default=0.0)
    score_contrast: Mapped[float] = mapped_column(Float, default=0.0)
    score_color_variance: Mapped[float] = mapped_column(Float, default=0.0)
    score_composition: Mapped[float] = mapped_column(Float, default=0.0)  # rule-of-thirds / face detection
    score_overall: Mapped[float] = mapped_column(Float, default=0.0)

    # Selection
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)
    provenance: Mapped[str] = mapped_column(String(100), default="ai_scene_analysis")
    # ai_scene_analysis | manual_selection | import_default

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    video_item = relationship("VideoItem", foreign_keys=[video_id])
    scene_analysis = relationship("AISceneAnalysis", back_populates="thumbnails")

    __table_args__ = (
        Index("ix_ai_thumb_video_selected", "video_id", "is_selected"),
    )

    def __repr__(self):
        return f"<AIThumbnail {self.id}: video={self.video_id} t={self.timestamp_sec:.1f}s score={self.score_overall:.2f}>"
