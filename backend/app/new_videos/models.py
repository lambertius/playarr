"""
New Videos — SQLAlchemy models for the discovery/recommendation system.

Tables:
  - suggested_videos: Candidate music videos discovered by the recommendation engine.
  - suggested_video_dismissals: User dismissals (temporary or permanent).
  - suggested_video_cart_items: Videos the user has queued for batch import.
  - recommendation_snapshots: Cached category-level feed snapshots.
  - recommendation_feedback: User interaction events for future ranking improvement.
"""
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text, DateTime, ForeignKey, Index,
    JSON, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from app.database import Base


class SuggestedVideo(Base):
    """A candidate music video surfaced by the recommendation engine."""
    __tablename__ = "suggested_videos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(50), nullable=False, default="youtube")          # youtube | vimeo
    provider_video_id = Column(String(200), nullable=False)                   # e.g. YouTube video ID
    url = Column(String(2000), nullable=False)
    title = Column(String(1000), nullable=False)
    artist = Column(String(500), nullable=True)
    album = Column(String(500), nullable=True)
    channel = Column(String(500), nullable=True)                              # uploader / channel name
    thumbnail_url = Column(String(2000), nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    release_date = Column(String(20), nullable=True)                          # ISO date string or "YYYY"
    view_count = Column(Integer, nullable=True)

    # Recommendation classification
    category = Column(String(50), nullable=False, index=True)                 # new|popular|rising|by_artist|taste|famous
    source_type = Column(String(100), nullable=True)                          # e.g. vevo, official_channel, label, curated

    # Scoring
    trust_score = Column(Float, default=0.0)                                  # 0.0–1.0
    popularity_score = Column(Float, default=0.0)
    trend_score = Column(Float, default=0.0)
    recommendation_score = Column(Float, default=0.0)                         # final composite score

    # Explainability
    recommendation_reason_json = Column(JSON, nullable=True)                  # list of reason strings
    trust_reasons_json = Column(JSON, nullable=True)                          # trust scoring breakdown

    # Raw metadata blob for future use
    metadata_json = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    dismissals = relationship("SuggestedVideoDismissal", back_populates="suggested_video",
                              cascade="all, delete-orphan")
    cart_items = relationship("SuggestedVideoCartItem", back_populates="suggested_video",
                              cascade="all, delete-orphan")
    feedback = relationship("RecommendationFeedback", back_populates="suggested_video",
                            cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("provider", "provider_video_id", "category",
                         name="uq_suggested_provider_video_category"),
        Index("ix_suggested_category_score", "category", "recommendation_score"),
    )

    def __repr__(self):
        return f"<SuggestedVideo id={self.id} artist={self.artist!r} title={self.title!r} cat={self.category}>"


class SuggestedVideoDismissal(Base):
    """Tracks when a user dismisses a suggestion (temporarily or permanently)."""
    __tablename__ = "suggested_video_dismissals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    suggested_video_id = Column(Integer, ForeignKey("suggested_videos.id", ondelete="CASCADE"), nullable=False, index=True)
    dismissal_type = Column(String(20), nullable=False, default="temporary")  # temporary | permanent
    dismissed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    reason = Column(String(500), nullable=True)

    # Also store provider_video_id so permanent dismissals survive feed refreshes
    provider = Column(String(50), nullable=True)
    provider_video_id = Column(String(200), nullable=True, index=True)

    suggested_video = relationship("SuggestedVideo", back_populates="dismissals")

    def __repr__(self):
        return f"<Dismissal id={self.id} type={self.dismissal_type} video={self.suggested_video_id}>"


class SuggestedVideoCartItem(Base):
    """A suggestion added to the user's import cart for batch import later."""
    __tablename__ = "suggested_video_cart_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    suggested_video_id = Column(Integer, ForeignKey("suggested_videos.id", ondelete="CASCADE"),
                                nullable=False, unique=True)
    url = Column(String(2000), nullable=False)
    title = Column(String(1000), nullable=True)
    artist = Column(String(500), nullable=True)
    provider = Column(String(50), nullable=True)
    provider_video_id = Column(String(200), nullable=True)
    added_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    suggested_video = relationship("SuggestedVideo", back_populates="cart_items")

    def __repr__(self):
        return f"<CartItem id={self.id} url={self.url!r}>"


class RecommendationSnapshot(Base):
    """Cached feed snapshot for a recommendation category.

    Feed generation is expensive (API calls, DB scans, scoring). This table
    stores the fully-ranked results per category so page loads are fast.
    The engine regenerates snapshots on refresh or when they expire.
    """
    __tablename__ = "recommendation_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(50), nullable=False, unique=True, index=True)
    generated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=True)
    payload_json = Column(JSON, nullable=True)                                # list of suggested_video IDs
    generator_version = Column(String(50), default="v1")
    source_summary_json = Column(JSON, nullable=True)                         # debug: what sources contributed

    def __repr__(self):
        return f"<Snapshot cat={self.category} generated={self.generated_at}>"


class RecommendationFeedback(Base):
    """User interaction events — foundation for future ranking improvement.

    Stored signals:
      viewed, opened_source, added, added_to_cart, dismissed,
      permanently_dismissed, imported, import_failed, removed_from_cart
    """
    __tablename__ = "recommendation_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    suggested_video_id = Column(Integer, ForeignKey("suggested_videos.id", ondelete="SET NULL"),
                                nullable=True, index=True)
    feedback_type = Column(String(50), nullable=False, index=True)
    provider = Column(String(50), nullable=True)
    provider_video_id = Column(String(200), nullable=True)
    artist = Column(String(500), nullable=True)
    category = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    context_json = Column(JSON, nullable=True)                                # arbitrary context

    suggested_video = relationship("SuggestedVideo", back_populates="feedback")

    __table_args__ = (
        Index("ix_feedback_type_created", "feedback_type", "created_at"),
    )

    def __repr__(self):
        return f"<Feedback id={self.id} type={self.feedback_type}>"
