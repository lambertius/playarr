"""
SQLAlchemy Models — Persistence for match results, candidates, pins.

Tables
------
* ``match_results``         — selected match per video (artist+recording+release)
* ``match_candidates``      — top-N candidates with score breakdown
* ``normalization_results``  — stored normalized artist/title/qualifiers
* ``user_pinned_matches``   — explicit user Fix-Match selections

All models share the same ``Base`` as the rest of Playarr.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text, DateTime,
    ForeignKey, Enum, JSON, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship, Mapped, mapped_column

from app.database import Base


# ── Enums ─────────────────────────────────────────────────────────────────

class MatchStatusEnum(str, enum.Enum):
    matched_high = "matched_high"
    matched_medium = "matched_medium"
    needs_review = "needs_review"
    unmatched = "unmatched"


# ── MatchResult ───────────────────────────────────────────────────────────

class MatchResult(Base):
    """
    Per-video resolved match: selected artist / recording / release
    with confidence, status, and revision tracking.

    One row per ``video_id``.  Replaced on re-resolve; old state
    preserved via ``previous_snapshot`` JSON.
    """
    __tablename__ = "match_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        ForeignKey("video_items.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    # Resolved artist
    resolved_artist: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    artist_mbid: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    # Resolved recording
    resolved_recording: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    recording_mbid: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    # Resolved release / album
    resolved_release: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    release_mbid: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    # Scores
    confidence_overall: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_breakdown: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Status
    status: Mapped[MatchStatusEnum] = mapped_column(
        Enum(MatchStatusEnum), default=MatchStatusEnum.unmatched,
    )

    # Snapshot of previous match (for undo)
    previous_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Normalization notes
    normalization_notes: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Is this the result of a user pin?
    is_user_pinned: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    candidates: Mapped[List["MatchCandidate"]] = relationship(
        back_populates="match_result", cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("video_id", name="uq_match_result_video"),
    )


# ── MatchCandidate ────────────────────────────────────────────────────────

class MatchCandidate(Base):
    """
    Individual candidate for a video's match, with full score breakdown.

    Multiple rows per ``match_result_id`` — stores the top-N candidates
    so the "Fix Match" UI can present alternatives.
    """
    __tablename__ = "match_candidates"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    match_result_id: Mapped[int] = mapped_column(
        ForeignKey("match_results.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    # Candidate identity
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)  # artist | recording | release
    candidate_mbid: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    canonical_name: Mapped[str] = mapped_column(String(500), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)

    # Score
    score: Mapped[float] = mapped_column(Float, default=0.0)
    score_breakdown: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Rank (1 = top)
    rank: Mapped[int] = mapped_column(Integer, default=0)

    # Was this the selected candidate?
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
    )

    match_result: Mapped["MatchResult"] = relationship(back_populates="candidates")


# ── NormalizationResult ───────────────────────────────────────────────────

class NormalizationResult(Base):
    """
    Stored record of how raw strings were normalised for a video.

    Useful for debugging and auditing the parsing pipeline.
    """
    __tablename__ = "normalization_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        ForeignKey("video_items.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    # Raw inputs
    raw_artist: Mapped[str] = mapped_column(String(500), nullable=False)
    raw_title: Mapped[str] = mapped_column(String(500), nullable=False)
    raw_album: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Normalised outputs
    artist_display: Mapped[str] = mapped_column(String(500), nullable=False)
    artist_key: Mapped[str] = mapped_column(String(500), nullable=False)
    primary_artist: Mapped[str] = mapped_column(String(500), nullable=False)
    featured_artists: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    title_display: Mapped[str] = mapped_column(String(500), nullable=False)
    title_key: Mapped[str] = mapped_column(String(500), nullable=False)
    title_base: Mapped[str] = mapped_column(String(500), nullable=False)
    qualifiers: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    album_display: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    album_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Changelog
    normalization_notes: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("video_id", name="uq_normalization_result_video"),
    )


# ── UserPinnedMatch ───────────────────────────────────────────────────────

class UserPinnedMatch(Base):
    """
    Explicit user Fix-Match selection.

    When set, the resolver will NOT auto-change the match for this video
    regardless of score deltas.  Only ``unpin`` clears this.
    """
    __tablename__ = "user_pinned_matches"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        ForeignKey("video_items.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    # What was pinned
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("match_candidates.id", ondelete="SET NULL"), nullable=True,
    )
    artist_mbid: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    recording_mbid: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    release_mbid: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    pinned_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("video_id", name="uq_user_pinned_video"),
    )
